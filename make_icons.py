#!/usr/bin/env python3
"""生成 PWA 图标（纯标准库，无需 Pillow）。运行一次即可，产物是 ui/icon-192.png / icon-512.png。"""
import os
import struct
import zlib

BLUE = (37, 99, 235)
WHITE = (255, 255, 255)


def rounded_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_triangle(px, py, a, b, c):
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = sign((px, py), a, b), sign((px, py), b, c), sign((px, py), c, a)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def render(size, ss=3):
    n = size * ss
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    fx, fy = (x * ss + sx + 0.5) / n, (y * ss + sy + 0.5) / n
                    col = (0, 0, 0, 0)
                    if rounded_rect(fx, fy, 0.03, 0.03, 0.97, 0.97, 0.22):
                        col = BLUE + (255,)
                        if rounded_rect(fx, fy, 0.20, 0.26, 0.80, 0.66, 0.08):
                            col = WHITE + (255,)
                        if in_triangle(fx, fy, (0.44, 0.36), (0.44, 0.56), (0.62, 0.46)):
                            col = BLUE + (255,)
                        if rounded_rect(fx, fy, 0.42, 0.70, 0.58, 0.76, 0.02):
                            col = WHITE + (255,)
                    acc[0] += col[0] * col[3]
                    acc[1] += col[1] * col[3]
                    acc[2] += col[2] * col[3]
                    acc[3] += col[3]
            a = acc[3] // (ss * ss)
            if a:
                px[y][x] = (acc[0] // acc[3], acc[1] // acc[3], acc[2] // acc[3], a)
    return px


def write_png(path, px):
    h, w = len(px), len(px[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *p) for p in row) for row in px)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for size, ss in ((192, 3), (512, 2)):
        out = os.path.join(here, "ui", f"icon-{size}.png")
        write_png(out, render(size, ss))
        print("wrote", out, os.path.getsize(out), "bytes")
