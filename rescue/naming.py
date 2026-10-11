"""What to call the finished file, when the browser didn't know yet.

In order: the name the server gives (Content-Disposition), the file name in
the link, and if the link is something like download.php?id=123, a plain
name with the extension the file's own contents call for.
"""

import mimetypes
import os
import re
import urllib.parse

# Link endings that are a web page or script, not the file's name.
NOT_A_FILE = {"php", "asp", "aspx", "cgi", "jsp", "do", "action", "html", "htm", "pl", "py"}

# How common file types begin: (offset, bytes, extension).
SIGNATURES = [
    (0, b"%PDF-", ".pdf"),
    (0, b"PK\x03\x04", ".zip"),
    (0, b"7z\xbc\xaf\x27\x1c", ".7z"),
    (0, b"Rar!\x1a\x07", ".rar"),
    (0, b"\x1f\x8b", ".gz"),
    (0, b"\xfd7zXZ\x00", ".xz"),
    (0, b"(\xb5/\xfd", ".zst"),
    (0, b"BZh", ".bz2"),
    (0, b"!<arch>\ndebian-binary", ".deb"),
    (0, b"\xed\xab\xee\xdb", ".rpm"),
    (0, b"MZ", ".exe"),
    (0, b"\x89PNG\r\n\x1a\n", ".png"),
    (0, b"\xff\xd8\xff", ".jpg"),
    (0, b"GIF8", ".gif"),
    (0, b"OggS", ".ogg"),
    (0, b"fLaC", ".flac"),
    (0, b"ID3", ".mp3"),
    (0, b"\x1aE\xdf\xa3", ".mkv"),
    (4, b"ftyp", ".mp4"),
    (32769, b"CD001", ".iso"),
    (257, b"ustar", ".tar"),
]


def sniff(head):
    """The extension the first bytes of a file call for, or None."""
    for offset, magic, ext in SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            if ext == ".exe":
                continue                    # too broad to name a file by
            if ext == ".zip" and b"META-INF/" in head[:200]:
                return ".jar"
            return ext
    if head[:4] == b"\x7fELF":
        return ".AppImage" if b"AI\x02" in head[8:11] else None
    return None


def _usable(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\x00" not in name


def name_for(filename, final_url, content_type, head, fallback):
    """The best name for the finished file."""
    if _usable(filename):
        return filename
    from_link = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(final_url).path))
    stem, ext = os.path.splitext(from_link)
    if _usable(from_link) and ext and ext[1:].lower() not in NOT_A_FILE:
        return from_link
    ext = sniff(head)
    if not ext and content_type and content_type not in ("application/octet-stream", "binary/octet-stream"):
        ext = mimetypes.guess_extension(content_type)
    base = stem if _usable(stem) and stem.lower() not in ("download", "file", "get", "index") else None
    base = base or re.sub(r"\.(crdownload|part|partial|download)$", "", fallback) or "download"
    return base + (ext or "")
