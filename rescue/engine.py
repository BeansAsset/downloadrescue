"""The rescue: finish a partial download from a new link without harming it.

The rules, in the order they're applied:

  1. The new link must be for a file of the same size.
  2. Overlap: the bytes just before where the download stopped are fetched
     again and must match what's on disk. If only the very end of the
     partial file is damaged (an interrupted write), that damaged end is
     set aside and fetched again; if nothing matches, it's a different file
     and the rescue stops.
  3. Spot checks across the rest of what's on disk must match too.
  4. Only missing parts are written. Bytes known to be good are never
     rewritten. Anything about to be overwritten inside the original file
     (a damaged end, an empty gap) is backed up first.
  5. Progress is saved as it goes (fsync, then the journal), so a rescue
     that's interrupted itself carries on where it stopped.
  6. With a checksum (given, or found next to the file on the server) the
     finished file is verified completely. If it doesn't match, the file is
     put back exactly as it was before the rescue.
  7. Only then does it get its real name, never replacing another file.
"""

import hashlib
import json
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field

from . import checksum, naming, ranges, remote
from .remote import RemoteError

OVERLAP = 1 << 20          # bytes re-fetched before the stopping point
OVERLAP_WIDE = 16 << 20    # a wider look, if the end of the file is damaged
BLOCK = 4096               # granularity for finding where damage starts
SPOT = 1 << 16             # size of each spot check
SPOTS = 24                 # number of spot checks
SAVE_EVERY = 8 << 20       # fsync and record progress this often
RETRIES = 5


class Refused(Exception):
    """The new link can't safely finish this file. Nothing was changed."""


class Failed(Exception):
    """Something went wrong part way. What was done so far is kept (or undone)."""


@dataclass
class Partial:
    path: str                    # the unfinished file (.crdownload, .part, ...)
    total: int | None = None     # expected size, if known
    good: list = field(default_factory=list)   # known-good byte ranges; empty = everything on disk
    name: str | None = None      # what it should be called when done
    etag: str | None = None
    browser: str | None = None


def data_ranges(path):
    """Where a file actually has data, skipping sparse holes (never written)."""
    size = os.path.getsize(path)
    out, pos = [], 0
    with open(path, "rb") as f:
        fd = f.fileno()
        while pos < size:
            try:
                start = os.lseek(fd, pos, os.SEEK_DATA)
            except OSError:          # no more data after pos
                break
            end = os.lseek(fd, start, os.SEEK_HOLE)
            out.append((start, min(end, size)))
            pos = end
    return ranges.merge(out)


def final_name(directory, name):
    """name, or "name (2)", "name (3)"... whichever doesn't exist yet."""
    base, ext = os.path.splitext(name)
    if base.endswith(".tar"):
        base, ext = base[:-4], ".tar" + ext
    candidate, n = name, 2
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


class Journal:
    """What the rescue has done, next to the file, so it can carry on or be undone."""

    def __init__(self, path):
        self.path = path + ".rescue"
        self.dir = path + ".rescue-backup"
        self.data = {}

    def load(self):
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        return self.data

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def remove(self):
        for p in (self.path, self.path + ".tmp"):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)


class Rescue:
    """Run with .run(); reports through on_event(kind, **details):

        step(name, state, text)   state is "doing", "ok", "note" or "bad"
        progress(done, total, what)
    """

    def __init__(self, partial, url, sha256=None, on_event=None):
        self.partial = partial
        self.url = url.strip()
        self.sha256 = (sha256 or "").strip().lower() or None
        self.on_event = on_event or (lambda kind, **kw: None)
        self.journal = Journal(partial.path)
        self.cancelled = False

    def step(self, name, state, text):
        self.on_event("step", name=name, state=state, text=text)

    def progress(self, done, total, what):
        self.on_event("progress", done=done, total=total, what=what)

    # -- reading what's on disk -------------------------------------------------------

    def _local(self, f, start, end):
        f.seek(start)
        return f.read(end - start)

    # -- the steps --------------------------------------------------------------------

    def run(self):
        """Returns (final path, how it was verified)."""
        p = self.partial
        state = self.journal.load()
        resuming = state.get("url") is not None

        self.step("source", "doing", "Asking the new link about the file")
        try:
            src = remote.probe(self.url)
        except RemoteError as e:
            raise Refused(f"The new link didn't work: {e}.") from None
        self.src = src

        size_now = os.path.getsize(p.path)
        if resuming:
            original_size = state["original_size"]
            total = state["total"]
        else:
            original_size = size_now
            total = p.total or src.total
        if total is None:
            raise Refused("Neither your download nor the new link says how big the file is, "
                          "so there's no safe way to line them up.")
        if src.total is not None and src.total != total:
            raise Refused(f"The new link is for a different file: it's {src.total:,} bytes, "
                          f"and yours should be {total:,}.")
        if original_size > total:
            raise Refused("Your partial file is bigger than the whole file should be.")
        same = " The server's version tag matches yours too." if p.etag and src.etag == p.etag else ""
        if p.total or resuming:
            self.step("source", "ok", f"Same size: {total:,} bytes.{same}")
        else:
            self.step("source", "ok", f"The new link's file is {total:,} bytes.")

        if resuming:
            good = [tuple(r) for r in state["good"]]
        else:
            good = ranges.merge(p.good) if p.good else data_ranges(p.path)
            good = ranges.clip(good, original_size)
            self.journal.data = {"url": self.url, "total": total, "original_size": original_size,
                                 "good": good, "backups": [], "written": []}
            self.journal.save()

        expected = self.sha256 or self._find_checksum(src)

        with open(p.path, "r+b") as f:
            if not resuming:
                if not src.ranges:
                    return self._whole_file(f, src, good, total, expected)
                try:
                    good = self._check_overlap(f, src, good)
                    self._spot_checks(f, src, good)
                except Refused:
                    self._undo()          # a refusal leaves no trace
                    raise
                except RemoteError as e:
                    self._undo()
                    raise Failed(f"Couldn't finish checking the new link ({e}). "
                                 "Your download was left as it was.") from None
                self.journal.data["good"] = good
                self.journal.save()
            else:
                self.step("checks", "ok", "Checks were done when this rescue started.")
            self._fill(f, src, good, total)

        return self._finish(total, expected)

    def _find_checksum(self, src):
        self.step("checksum", "doing", "Looking for a published checksum")
        found = checksum.find(src)
        if found:
            digest, where = found
            self.step("checksum", "ok", f"Found a SHA-256 checksum in {where}.")
            return digest
        self.step("checksum", "note", "No checksum published next to the file. Add one if the site lists it.")
        return None

    def _check_overlap(self, f, src, good):
        """Re-fetch the bytes before each place the download stopped; they must match.
        A damaged end (matching first, then not) is set aside to be fetched again."""
        self.step("overlap", "doing", "Comparing the end of your download with the new link")
        checked = []
        for start, end in list(good):
            for width in (OVERLAP, OVERLAP_WIDE):
                lo = max(start, end - width)
                theirs = remote.fetch(src, lo, end) if end > lo else b""
                mine = self._local(f, lo, end)
                bad = next((i for i in range(0, len(mine), BLOCK)
                            if mine[i:i + BLOCK] != theirs[i:i + BLOCK]), None)
                if bad is None:
                    checked.append((start, end, None))
                    break
                if bad > 0:                  # good up to a point, damaged after it
                    checked.append((start, end, lo + bad))
                    break
                if lo == start:              # nothing at all matches, even from the start of this part
                    raise Refused("The new link's file doesn't match what you already have. "
                                  "It's a different file, or a different version of it.")
            else:
                raise Refused("The new link's file doesn't match the end of what you already have. "
                              "It's a different file, or a different version of it.")
        new_good, damaged = [], 0
        for start, end, cut in checked:
            if cut is None:
                new_good.append((start, end))
            else:
                self._backup(f, cut, end)
                new_good.append((start, cut))
                damaged += end - cut
        if damaged:
            self.step("overlap", "ok", f"Matches. The last {damaged:,} bytes on disk were damaged; "
                                       "they'll be fetched again.")
        else:
            self.step("overlap", "ok", "The end of your download matches the new link exactly.")
        return ranges.merge(new_good)

    def _spot_checks(self, f, src, good):
        """Compare small samples spread across everything already on disk."""
        spots = []
        rng = random.Random(len(good) * 7919 + ranges.size(good))
        for start, end in good:
            if end - start <= SPOT:
                spots.append((start, end))
                continue
            spots.append((start, start + SPOT))
        candidates = [(s, e) for s, e in good if e - s > 2 * SPOT]
        weight = ranges.size(candidates)
        for s, e in candidates:
            n = max(1, round(SPOTS * (e - s) / weight)) if weight else 0
            for _ in range(n):
                at = rng.randrange(s, e - SPOT)
                spots.append((at, at + SPOT))
        spots = sorted(set(spots))[:SPOTS + len(good)]
        for k, (s, e) in enumerate(spots, 1):
            self.step("spots", "doing", f"Spot checks: {k} of {len(spots)}")
            if remote.fetch(src, s, e) != self._local(f, s, e):
                raise Refused(f"The new link's file differs from yours at byte {s:,}. "
                              "It's a different version of the file.")
        self.step("spots", "ok", f"All {len(spots)} spot checks across your download match.")

    def _backup(self, f, start, end):
        """Keep the original bytes of [start, end) before writing there. A gap
        that was never written (a sparse hole) is only noted, not copied."""
        if not any(s < end and e > start for s, e in data_ranges(self.partial.path)):
            self.journal.data["backups"].append([start, end, "empty"])
            self.journal.save()
            return
        os.makedirs(self.journal.dir, exist_ok=True)
        name = os.path.join(self.journal.dir, f"{start}-{end}")
        if not os.path.exists(name):
            with open(name, "wb") as out:
                f.seek(start)
                left = end - start
                while left:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            self.journal.data["backups"].append([start, end])
            self.journal.save()

    def _fill(self, f, src, good, total):
        """Fetch every missing part straight into its place in the file."""
        gaps = ranges.missing(good, total)
        need = ranges.size(gaps)
        original_size = self.journal.data["original_size"]
        if need == 0:
            self.step("download", "ok", "Nothing was missing.")
            return
        self.step("download", "doing", f"Fetching the missing {need:,} bytes")
        # Empty gaps inside the original file are backed up (cheaply: they're
        # usually sparse) so the file can be put back exactly.
        for s, e in gaps:
            if s < original_size:
                self._backup(f, s, min(e, original_size))
        done = 0
        started = time.monotonic()
        for gap_start, gap_end in gaps:
            pos = gap_start
            tries = 0
            while pos < gap_end:
                if self.cancelled:
                    raise Failed("Stopped. What's done so far is kept; start again to carry on.")
                try:
                    unsaved = 0
                    for chunk in remote.stream(src, pos, gap_end):
                        os.pwrite(f.fileno(), chunk, pos)
                        pos += len(chunk)
                        done += len(chunk)
                        unsaved += len(chunk)
                        if unsaved >= SAVE_EVERY:
                            self._record(f, good, gap_start, pos)
                            unsaved = 0
                        self.progress(done, need, "download")
                        if self.cancelled:
                            break
                    self._record(f, good, gap_start, pos)
                    tries = 0
                except RemoteError as e:
                    self._record(f, good, gap_start, pos)
                    tries += 1
                    if tries > RETRIES:
                        raise Failed(f"The download kept failing ({e}). What's done so far is kept; "
                                     "start again to carry on.") from None
                    self.step("download", "note", f"Connection trouble ({e}); trying again.")
                    time.sleep(min(30, 2 ** tries))
        secs = max(0.001, time.monotonic() - started)
        self.step("download", "ok", f"Fetched {need:,} bytes ({need / secs / 1e6:.1f} MB/s).")

    def _record(self, f, good, start, upto):
        """Make [start, upto) permanent on disk, then note it in the journal.
        The journal never claims more than has been fsynced."""
        os.fsync(f.fileno())
        good[:] = ranges.merge(good + [(start, upto)])
        self.journal.data["good"] = good
        self.journal.save()

    def _whole_file(self, f, src, good, total, expected):
        """For servers that only send the whole file: read it from the start,
        compare everything we already have, and write only what's missing."""
        self.step("overlap", "note", "This server can't send parts, so the whole file is read again; "
                                     "everything you have is compared on the way.")
        pos = 0
        for s, e in ranges.missing(good, total):
            if s < self.journal.data["original_size"]:
                self._backup(f, s, min(e, self.journal.data["original_size"]))
        try:
            for chunk in remote.stream_whole(src):
                pos = self._whole_chunk(f, chunk, pos, good, total)
        except RemoteError as e:
            self._undo()
            raise Failed(f"The download broke off ({e}). Your download was left as it was.") from None
        if pos != total:
            self._undo()
            raise Failed(f"The connection ended {total - pos:,} bytes early. Your download was left as it was.")
        os.fsync(f.fileno())
        self.step("download", "ok", "Read the whole file: everything you had matches.")
        return self._finish(total, expected)

    def _whole_chunk(self, f, chunk, pos, good, total):
        """Compare one piece of the whole-file stream with what's on disk, and
        write the parts that are missing. Returns the new position."""
        if self.cancelled:
            self._undo()
            raise Failed("Stopped. Your download was left as it was.")
        end = pos + len(chunk)
        if end > total:
            self._undo()
            raise Failed("The server sent more than the file's size. Your download was left as it was.")
        have = ranges.merge([(max(a, pos), min(b, end)) for a, b in good if a < end and b > pos])
        # Compare the parts we have...
        for s, e in have:
            if self._local(f, s, e) != chunk[s - pos:e - pos]:
                self._undo()
                raise Refused(f"The new link's file differs from yours at byte {s:,}. "
                              "Your download was left as it was.")
        # ...and write the parts we don't.
        for s, e in ranges.missing(have, end):
            s = max(s, pos)
            if e > s:
                os.pwrite(f.fileno(), chunk[s - pos:e - pos], s)
        self.progress(end, total, "download")
        return end

    def _finish(self, total, expected):
        p = self.partial
        if os.path.getsize(p.path) != total:
            raise Failed("The finished file isn't the right size.")
        how = "overlap and spot checks"
        if expected:
            self.step("verify", "doing", "Checking the whole file against the checksum")
            digest = hashlib.sha256()
            done = 0
            with open(p.path, "rb") as f:
                while True:
                    chunk = f.read(4 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    done += len(chunk)
                    self.progress(done, total, "verify")
            if digest.hexdigest() != expected:
                self._undo()
                raise Failed("The finished file doesn't match the checksum, so it was put back "
                             "exactly as it was before. The new link may serve a different version.")
            self.step("verify", "ok", "The whole file matches its SHA-256 checksum.")
            how = "SHA-256 checksum"
        directory = os.path.dirname(p.path)
        name = final_name(directory, p.name or self._plain_name())
        final = os.path.join(directory, name)
        os.rename(p.path, final)
        self.journal.remove()
        self.step("finish", "ok", f"Saved as {name}.")
        return final, how

    def _plain_name(self):
        """No name from the browser: see naming.py."""
        src = getattr(self, "src", None)
        with open(self.partial.path, "rb") as f:
            head = f.read(40000)
        fallback = os.path.basename(self.partial.path)
        if re.fullmatch(r"Unconfirmed \d+(\.crdownload)?", fallback):
            fallback = "download"
        if src is None:
            return naming.name_for(None, "", None, head, fallback)
        return naming.name_for(src.filename, src.final_url, src.content_type, head, fallback)

    def _undo(self):
        """Put the partial file back exactly as it was before the rescue."""
        data = self.journal.load()
        if not data:
            return
        with open(self.partial.path, "r+b") as f:
            for start, end, *kind in data.get("backups", []):
                if kind == ["empty"]:
                    zero = bytes(1 << 20)
                    for at in range(start, end, len(zero)):
                        os.pwrite(f.fileno(), zero[:min(len(zero), end - at)], at)
                    continue
                with open(os.path.join(self.journal.dir, f"{start}-{end}"), "rb") as b:
                    at = start
                    while chunk := b.read(1 << 20):
                        os.pwrite(f.fileno(), chunk, at)
                        at += len(chunk)
            f.truncate(data["original_size"])
            os.fsync(f.fileno())
        self.journal.remove()

    def undo(self):
        self._undo()
