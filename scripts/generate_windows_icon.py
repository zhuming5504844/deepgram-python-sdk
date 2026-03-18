from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'build' / 'app_icon.ico'
SIZE = 256


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack('!I', len(data)) + chunk_type + data + struct.pack('!I', crc)


def _build_png(width: int, height: int, rgba: bytes) -> bytes:
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('!IIBBBBB', width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        start = row * stride
        raw.extend(rgba[start:start + stride])
    compressed = zlib.compress(bytes(raw), level=9)
    return signature + _png_chunk(b'IHDR', ihdr) + _png_chunk(b'IDAT', compressed) + _png_chunk(b'IEND', b'')


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _pixel(x: int, y: int) -> tuple[int, int, int, int]:
    t = (x + y) / ((SIZE - 1) * 2)
    bg_start = (15, 23, 42)
    bg_end = (37, 99, 235)
    r = _lerp(bg_start[0], bg_end[0], t)
    g = _lerp(bg_start[1], bg_end[1], t)
    b = _lerp(bg_start[2], bg_end[2], t)
    a = 255

    radius = 48
    if min(x, y, SIZE - 1 - x, SIZE - 1 - y) < radius:
        corners = ((radius, radius), (SIZE - 1 - radius, radius), (radius, SIZE - 1 - radius), (SIZE - 1 - radius, SIZE - 1 - radius))
        if x < radius and y < radius:
            cx, cy = corners[0]
        elif x > SIZE - 1 - radius and y < radius:
            cx, cy = corners[1]
        elif x < radius and y > SIZE - 1 - radius:
            cx, cy = corners[2]
        elif x > SIZE - 1 - radius and y > SIZE - 1 - radius:
            cx, cy = corners[3]
        else:
            cx = cy = None
        if cx is not None:
            if (x - cx) ** 2 + (y - cy) ** 2 > radius ** 2:
                return 0, 0, 0, 0

    def in_round_rect(px: int, py: int, x0: int, y0: int, w: int, h: int, rr: int) -> bool:
        x1, y1 = x0 + w - 1, y0 + h - 1
        if x0 + rr <= px <= x1 - rr or y0 + rr <= py <= y1 - rr:
            return x0 <= px <= x1 and y0 <= py <= y1
        corners2 = ((x0 + rr, y0 + rr), (x1 - rr, y0 + rr), (x0 + rr, y1 - rr), (x1 - rr, y1 - rr))
        for cx, cy in corners2:
            if (px - cx) ** 2 + (py - cy) ** 2 <= rr ** 2:
                return True
        return False

    for rect in ((52, 64, 28, 128, 14), (94, 92, 28, 72, 14), (136, 76, 28, 104, 14)):
        if in_round_rect(x, y, *rect):
            return 226, 232, 240, 255

    if in_round_rect(x, y, 50, 192, 156, 18, 9):
        return 56, 189, 248, 235

    curve_points = []
    for px in range(60, 197):
        progress = (px - 60) / (196 - 60)
        base = 176 - 40 * (1 - ((progress * 2) - 1) ** 2)
        sway = 12 * progress
        py = base - sway
        curve_points.append((px, py))
    for px, py in curve_points:
        if (x - px) ** 2 + (y - py) ** 2 <= 9 ** 2:
            mix = (x - 60) / (196 - 60)
            return _lerp(34, 232, mix), _lerp(211, 121, mix), _lerp(238, 249, mix), 255

    return r, g, b, a


def _build_rgba() -> bytes:
    pixels = bytearray()
    for y in range(SIZE):
        for x in range(SIZE):
            pixels.extend(_pixel(x, y))
    return bytes(pixels)


def write_ico(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    png_data = _build_png(SIZE, SIZE, _build_rgba())
    header = struct.pack('<HHH', 0, 1, 1)
    directory = struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(png_data), 6 + 16)
    destination.write_bytes(header + directory + png_data)
    return destination


if __name__ == '__main__':
    path = write_ico(OUTPUT)
    print(f'Generated Windows icon: {path}')
