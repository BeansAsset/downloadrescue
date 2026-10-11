"""Finding a published SHA-256 for the file on its server.

Download sites often put one next to the file: name.iso.sha256, or a
SHA256SUMS list in the same folder. Only exact matches for the file's own
name are used, and only 64-character hex digests.
"""

import os
import re
import urllib.parse

from . import remote

SIBLINGS = ("SHA256SUMS", "SHA256SUMS.txt", "sha256sums.txt", "sha256sum.txt", "CHECKSUM", "CHECKSUMS")
SUFFIXES = (".sha256", ".sha256sum", ".sha256.txt", ".SHA256")


def digest_for(text, filename):
    """The SHA-256 for `filename` in a checksum file's text, or None.
    Understands `sha256sum` output ("digest  name" or "digest *name") and the
    BSD style "SHA256 (name) = digest". A file holding only a digest, with no
    name at all, is taken as being for the file it sits next to."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    for line in lines:
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if m and os.path.basename(m.group(2).strip()) == filename:
            return m.group(1).lower()
        m = re.match(r"^SHA256\s*\((.+)\)\s*=\s*([0-9a-fA-F]{64})$", line, re.I)
        if m and os.path.basename(m.group(1).strip()) == filename:
            return m.group(2).lower()
    # A file holding nothing but one digest (no name) is for the file it sits next to.
    if len(lines) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", lines[0]):
        return lines[0].lower()
    return None


def find(source):
    """(digest, where it was found) or None."""
    parts = urllib.parse.urlsplit(source.final_url)
    path = parts.path
    filename = source.filename or urllib.parse.unquote(os.path.basename(path))
    if not filename:
        return None
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    folder = base.rsplit("/", 1)[0] + "/"
    tries = [(base + suffix, os.path.basename(path) + suffix) for suffix in SUFFIXES]
    tries += [(folder + name, name) for name in SIBLINGS]
    for url, label in tries:
        text = remote.small_text(url)
        if text:
            digest = digest_for(text, filename)
            if digest:
                return digest, label
    return None
