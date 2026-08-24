#!/usr/bin/env python3
"""Generate the PWA icons.

Pure stdlib so this stays runnable without a build chain. The mark is a
brass ring with a centre dot — a scope, drawn at 4x and downsampled for
antialiasing.

    python3 scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "icons"

BG = (0x0B, 0x0E, 0x14, 255)
BRASS = (0xC8, 0x87, 0x3A, 255)
CLEAR = (0, 0, 0, 0)

SS = 4  # supersampling factor


def write_png(path: Path, size: int, pixels: list[tuple[int, int, int, int]]) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type: none
        for x in range(size):
            raw.extend(pixels[y * size + x])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def in_rounded_rect(x: float, y: float, side: float, radius: float) -> bool:
    if radius <= 0:
        return True
    cx = min(max(x, radius), side - radius)
    cy = min(max(y, radius), side - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def render(size: int, *, rounded: bool, ring_outer: float, ring_width: float, dot: float):
    n = size * SS
    centre = n / 2
    radius_corner = 0.22 * n if rounded else 0.0

    outer = ring_outer * n
    inner = outer - ring_width * n
    dot_r = dot * n

    # Render supersampled, then box-filter down.
    hi: list[tuple[int, int, int, int]] = []
    for y in range(n):
        dy = y + 0.5 - centre
        for x in range(n):
            dx = x + 0.5 - centre
            dist_sq = dx * dx + dy * dy

            if not in_rounded_rect(x + 0.5, y + 0.5, n, radius_corner):
                hi.append(CLEAR)
            elif inner**2 <= dist_sq <= outer**2 or dist_sq <= dot_r**2:
                hi.append(BRASS)
            else:
                hi.append(BG)

    out: list[tuple[int, int, int, int]] = []
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                row = (y * SS + sy) * n
                for sx in range(SS):
                    px = hi[row + x * SS + sx]
                    # Premultiply so transparent corners do not darken edges.
                    alpha = px[3]
                    acc[0] += px[0] * alpha
                    acc[1] += px[1] * alpha
                    acc[2] += px[2] * alpha
                    acc[3] += alpha

            total_alpha = acc[3]
            if total_alpha == 0:
                out.append(CLEAR)
            else:
                out.append(
                    (
                        round(acc[0] / total_alpha),
                        round(acc[1] / total_alpha),
                        round(acc[2] / total_alpha),
                        round(total_alpha / (SS * SS)),
                    )
                )
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    standard = dict(rounded=True, ring_outer=0.30, ring_width=0.075, dot=0.075)
    # Maskable icons get cropped to a circle on some launchers, so the mark
    # shrinks into the safe zone and the background bleeds to the edges.
    masked = dict(rounded=False, ring_outer=0.24, ring_width=0.060, dot=0.060)

    for name, size, opts in [
        ("icon-192.png", 192, standard),
        ("icon-512.png", 512, standard),
        ("apple-touch-icon.png", 180, dict(standard, rounded=False)),
        ("maskable-512.png", 512, masked),
    ]:
        write_png(OUT / name, size, render(size, **opts))
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
