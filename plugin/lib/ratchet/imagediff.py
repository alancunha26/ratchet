"""Pixel diff of two PNGs with no dependencies (pure-Python PNG decode/encode).

Supports non-interlaced 8/16-bit grayscale, RGB, palette, gray+alpha and RGBA images,
which covers browser, simulator and game-engine screenshots. Deterministic visual checks
(render, reference, diff) come first; a model's opinion is the last step, not the first.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class ImageError(Exception):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def decode(path: Path) -> tuple[int, int, list]:
    """Return (width, height, rows) with rows as lists of (r, g, b, a) tuples."""
    data = Path(path).read_bytes()
    if not data.startswith(_SIG):
        raise ImageError(f"{path}: not a PNG")
    pos, idat, palette, trns = 8, b"", None, None
    width = height = depth = ctype = interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if interlace:
        raise ImageError(f"{path}: interlaced PNGs are not supported")
    if ctype not in _CHANNELS or depth not in (8, 16) and not (ctype == 3 and depth == 8):
        raise ImageError(f"{path}: unsupported PNG (color type {ctype}, depth {depth})")
    channels = _CHANNELS[ctype]
    bpp = channels * (depth // 8)
    stride = width * bpp
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
        prev = line
        step = depth // 8
        vals = line[::step] if step == 2 else line  # 16-bit: keep the high byte
        px = []
        for x in range(width):
            v = vals[x * channels:(x + 1) * channels]
            if ctype == 0:
                px.append((v[0], v[0], v[0], 255))
            elif ctype == 2:
                px.append((v[0], v[1], v[2], 255))
            elif ctype == 3:
                r, g, bb = palette[v[0]] if palette else (0, 0, 0)
                alpha = trns[v[0]] if trns and v[0] < len(trns) else 255
                px.append((r, g, bb, alpha))
            elif ctype == 4:
                px.append((v[0], v[0], v[0], v[1]))
            else:
                px.append((v[0], v[1], v[2], v[3]))
        rows.append(px)
    return width, height, rows


def encode(path: Path, width: int, height: int, rows: list) -> None:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    Path(path).write_bytes(_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
                           chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def diff(a: Path, b: Path, out: Path = None, threshold: int = 16) -> dict:
    """Compare two PNGs; a pixel differs when any channel differs by more than `threshold` (0-255)."""
    wa, ha, ra = decode(a)
    wb, hb, rb = decode(b)
    if (wa, ha) != (wb, hb):
        return {"same_size": False, "size_a": [wa, ha], "size_b": [wb, hb], "ratio": 1.0, "differing": wa * ha}
    differing = 0
    out_rows = []
    for y in range(ha):
        row = []
        for x in range(wa):
            pa, pb = ra[y][x], rb[y][x]
            if max(abs(pa[i] - pb[i]) for i in range(4)) > threshold:
                differing += 1
                row.append((255, 0, 0, 255))
            else:
                g = (pa[0] + pa[1] + pa[2]) // 3 // 3 + 170  # faded original
                row.append((g, g, g, 255))
        out_rows.append(row)
    if out:
        encode(out, wa, ha, out_rows)
    total = wa * ha
    return {"same_size": True, "size": [wa, ha], "differing": differing, "ratio": round(differing / total, 6) if total else 0.0,
            "diff_image": str(out) if out else None}
