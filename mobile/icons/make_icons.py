#!/usr/bin/env python3
"""Generate ASTRA app icons as PNGs with no third-party dependencies.

Draws an instrument-style gauge on a deep-ink field: a gold arc, a swept needle,
and a horizon tick. Everything is composed with anti-aliased coverage from a
supersampled buffer, so it needs only zlib + struct from the standard library.

Run: python3 mobile/icons/make_icons.py
Outputs: icon-192.png, icon-512.png, icon-maskable-512.png (maskable has the
gauge pulled inside the safe zone so Android's circle crop never clips it).
"""

from __future__ import annotations

import math
import os
import struct
import zlib

INK = (0x0B, 0x0E, 0x13)
GOLD = (0xD6, 0xA5, 0x3C)
GOLD_DIM = (0x8A, 0x6A, 0x24)
PAPER = (0xEC, 0xE6, 0xD8)

SS = 4  # supersample factor


def _blend(dst, src, alpha):
    return tuple(round(d + (s - d) * alpha) for d, s in zip(dst, src))


def _png(width: int, height: int, rows: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + rows[y * width * 3:(y + 1) * width * 3]
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _coverage(px, py, fn):
    """Supersampled coverage of `fn(x, y) -> alpha` over one output pixel."""
    total = 0.0
    for sy in range(SS):
        for sx in range(SS):
            total += fn(px + (sx + 0.5) / SS, py + (sy + 0.5) / SS)
    return total / (SS * SS)


def draw(size: int, inset: float = 1.0) -> bytes:
    """Render the gauge. `inset` < 1 shrinks it into the maskable safe zone."""
    buf = [INK] * (size * size)
    cx = cy = size / 2.0
    r_outer = size * 0.36 * inset
    r_arc = size * 0.30 * inset
    arc_w = size * 0.055 * inset

    # 220 degrees of gauge sweep, opening downward.
    a_start = math.radians(160)
    a_end = math.radians(380)

    def put(x: int, y: int, color, alpha):
        if 0 <= x < size and 0 <= y < size and alpha > 0:
            i = y * size + x
            buf[i] = _blend(buf[i], color, min(1.0, alpha))

    for y in range(size):
        for x in range(size):
            def sample(fx, fy):
                dx, dy = fx - cx, fy - cy
                d = math.hypot(dx, dy)
                if d > r_outer:
                    return 0.0
                # Dial face.
                a = 1.0 if d <= r_outer else 0.0
                # Gauge arc.
                ang = math.atan2(dy, dx)
                if ang < 0:
                    ang += 2 * math.pi
                if a_start <= ang <= a_end and abs(d - r_arc) <= arc_w / 2:
                    a = 1.0
                # Needle pointing up-left, plus hub.
                na = math.radians(215)
                nx, ny = math.cos(na), math.sin(na)
                proj = dx * nx + dy * ny
                perp = abs(dx * -ny + dy * nx)
                if 0 <= proj <= r_arc * 0.92 and perp <= size * 0.012 * inset:
                    a = 1.0
                if d <= size * 0.045 * inset:
                    a = 1.0
                return a

            cov = _coverage(x, y, sample)
            if cov <= 0:
                continue
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            d = math.hypot(dx, dy)
            ang = math.atan2(dy, dx)
            if ang < 0:
                ang += 2 * math.pi
            color = PAPER
            if abs(d - r_arc) <= arc_w / 2 and a_start <= ang <= a_end:
                color = GOLD if ang >= math.radians(215) else GOLD_DIM
            elif d <= size * 0.045 * inset:
                color = GOLD
            elif d <= r_outer and d > r_outer - size * 0.02 * inset:
                color = GOLD_DIM
            put(x, y, color, cov)

    return _png(size, size, bytes(b for p in buf for b in p))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for name, size, inset in (
        ("icon-192.png", 192, 1.0),
        ("icon-512.png", 512, 1.0),
        ("icon-maskable-512.png", 512, 0.72),
    ):
        path = os.path.join(here, name)
        with open(path, "wb") as fh:
            fh.write(draw(size, inset))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
