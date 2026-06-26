#!/usr/bin/env python3
"""ppm2png.py — convert a binary PPM (P6) framebuffer dump to PNG. Stdlib only (zlib).

The app dumps the virtual framebuffer as raw P6 PPM (trivial, no deps under qemu); the host
turns it into the PNG artifact the bead/CI wants. No libpng, no Pillow.
  ppm2png.py in.ppm out.png
"""
import struct
import sys
import zlib


def read_ppm(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"P6":
        raise ValueError(f"{path}: not a P6 PPM")
    i, vals = 2, []
    while len(vals) < 3:
        while i < len(data) and data[i] in b" \t\n\r":
            i += 1
        if data[i:i + 1] == b"#":
            while data[i] not in b"\n":
                i += 1
            continue
        s = i
        while data[i] not in b" \t\n\r":
            i += 1
        vals.append(int(data[s:i]))
    w, h, _maxv = vals
    i += 1  # single whitespace after maxval
    return w, h, data[i:i + w * h * 3]


def write_png(path, w, h, rgb):
    def chunk(typ, b):
        return struct.pack(">I", len(b)) + typ + b + struct.pack(">I", zlib.crc32(typ + b) & 0xffffffff)
    raw = bytearray()
    for y in range(h):
        raw.append(0)                        # filter: none
        raw += rgb[y * w * 3:(y + 1) * w * 3]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit RGB
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: ppm2png.py in.ppm out.png")
    w, h, rgb = read_ppm(argv[0])
    write_png(argv[1], w, h, rgb)
    print(f"{argv[1]} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
