"""Build data/DownloadRescue.icns (the Mac icon) from the SVG icon.

Mac icons sit on a 1024 px canvas with the artwork 824 px wide, centred,
with a soft shadow below. Run on Linux (needs GdkPixbuf to draw the SVG):

    python3 tools/make_icns.py
"""

import io
import os
import struct

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "data", "io.github.beansasset.DownloadRescue.svg")
OUT = os.path.join(ROOT, "data", "DownloadRescue.icns")

# (icns type, pixel size)
TYPES = [("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512),
         ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512)]


def master():
    # In the SVG the tile is 112 of 128 units; make it 824 of 1024 px.
    size = round(824 * 128 / 112)
    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(SVG, size, size)
    ok, data = pix.save_to_bufferv("png", [], [])
    art = Image.open(io.BytesIO(data)).convert("RGBA")
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    at = ((1024 - size) // 2, (1024 - size) // 2 - 10)
    shadow = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    alpha = art.split()[3].point(lambda a: a * 0.35)
    shadow.paste((0, 0, 0, 255), (at[0], at[1] + 14), alpha)
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(14)))
    canvas.alpha_composite(art, at)
    return canvas


def main():
    big = master()
    chunks = b""
    for kind, px in TYPES:
        buf = io.BytesIO()
        big.resize((px, px), Image.LANCZOS).save(buf, "PNG")
        png = buf.getvalue()
        chunks += kind.encode() + struct.pack(">I", len(png) + 8) + png
    with open(OUT, "wb") as f:
        f.write(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
