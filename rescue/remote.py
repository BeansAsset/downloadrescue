"""Talking to the server behind the new link.

Every answer is checked before its bytes are used: a range request must come
back as 206 with a Content-Range for exactly the bytes asked for, and the
total size must be the one we expect. A server that ignores the Range header
and sends the whole file (200) is detected, never mistaken for the piece.
"""

import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) DownloadRescue/0.1"
TIMEOUT = 30
CHUNK = 1 << 16


class RemoteError(Exception):
    """The server didn't give what was asked for. The message says what happened."""


@dataclass
class Source:
    url: str              # the link as given
    final_url: str        # after redirects
    total: int | None     # file size, if the server says
    ranges: bool          # whether it serves parts of the file
    etag: str | None
    last_modified: str | None
    filename: str | None  # from Content-Disposition, if any


_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")


def _request(url, start=None, end=None):
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if start is not None:
        headers["Range"] = f"bytes={start}-" + ("" if end is None else str(end - 1))
    req = urllib.request.Request(url, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            raise RemoteError("the server says that part of the file doesn't exist") from None
        if e.code in (401, 403):
            raise RemoteError(f"the server refused (HTTP {e.code}); the link may have expired too") from None
        if e.code == 404:
            raise RemoteError("the file isn't there (HTTP 404)") from None
        raise RemoteError(f"the server answered HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RemoteError(f"couldn't reach the server ({e.reason})") from None
    except (TimeoutError, OSError) as e:
        raise RemoteError(f"couldn't reach the server ({e})") from None


def _filename(disposition):
    if not disposition:
        return None
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", disposition, re.I)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    return m.group(1).strip() if m else None


def probe(url):
    """What the server says about the file, from a request for its first byte."""
    with _request(url, 0, 1) as r:
        headers = r.headers
        status = r.status
        if status == 206:
            m = _RANGE.match(headers.get("Content-Range", ""))
            if not m or m.group(1) != "0":
                raise RemoteError("the server's answer about the file's size doesn't make sense")
            total = None if m.group(3) == "*" else int(m.group(3))
            ranges = True
        elif status == 200:
            length = headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            ranges = False
        else:
            raise RemoteError(f"the server answered HTTP {status}")
        if (headers.get("Content-Encoding") or "identity").lower() != "identity":
            raise RemoteError("the server compresses the file on the way, so its bytes can't be compared")
        return Source(url=url, final_url=r.geturl(), total=total, ranges=ranges,
                      etag=headers.get("ETag"), last_modified=headers.get("Last-Modified"),
                      filename=_filename(headers.get("Content-Disposition")))


READ_ERRORS = (http.client.HTTPException, OSError)


def fetch(source, start, end, attempts=6):
    """Exactly the bytes [start, end) of the file, or an error. A connection
    that ends early is picked up again from where it stopped."""
    data = bytearray()
    failures = 0
    while len(data) < end - start:
        at = start + len(data)
        try:
            for chunk in stream(source, at, end):
                data += chunk
        except RemoteError:
            failures += 1
            if failures >= attempts:
                raise
    return bytes(data)


def stream(source, start, end):
    """The bytes [start, end) in chunks, checked to be the part asked for."""
    with _request(source.url, start, end) as r:
        _check_part(r, source, start, end)
        got = 0
        while got < end - start:
            try:
                chunk = r.read(min(CHUNK, end - start - got))
            except READ_ERRORS as e:
                raise RemoteError(f"the connection broke ({type(e).__name__})") from None
            if not chunk:
                raise RemoteError(f"the connection ended {end - start - got:,} bytes early")
            got += len(chunk)
            yield chunk


def stream_whole(source):
    """The whole file from the start, for servers that can't send parts."""
    with _request(source.url) as r:
        if r.status != 200:
            raise RemoteError(f"the server answered HTTP {r.status}")
        while True:
            try:
                chunk = r.read(CHUNK)
            except READ_ERRORS as e:
                raise RemoteError(f"the connection broke ({type(e).__name__})") from None
            if not chunk:
                return
            yield chunk


def _check_part(response, source, start, end):
    if response.status == 200:
        raise RemoteError("the server sent the whole file instead of the part asked for")
    if response.status != 206:
        raise RemoteError(f"the server answered HTTP {response.status}")
    m = _RANGE.match(response.headers.get("Content-Range", ""))
    if not m:
        raise RemoteError("the server didn't say which part it sent")
    first, last, total = int(m.group(1)), int(m.group(2)), m.group(3)
    if first != start or last != end - 1:
        raise RemoteError(f"the server sent bytes {first}-{last} instead of {start}-{end - 1}")
    if source.total is not None and total != "*" and int(total) != source.total:
        raise RemoteError("the file's size changed on the server while rescuing")
    if (response.headers.get("Content-Encoding") or "identity").lower() != "identity":
        raise RemoteError("the server compresses the file on the way, so its bytes can't be trusted")


def small_text(url, limit=1 << 20):
    """A small text file (like a SHA256SUMS list), or None."""
    try:
        with _request(url) as r:
            if r.status != 200:
                return None
            data = r.read(limit + 1)
    except RemoteError:
        return None
    if len(data) > limit:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
