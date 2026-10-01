#!/usr/bin/env python3
"""Compare before/after PNG screenshots of key pages.

A page Passes when both images exist, share the same size, and the fraction
of pixels that differ is below the threshold. Prints JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path


class PngError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png_rgb(path: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError(f"{path} is not a PNG")

    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break

    if width is None or height is None or bit_depth is None or color_type is None:
        raise PngError(f"{path} is missing IHDR")
    if bit_depth != 8 or color_type not in (2, 6):
        raise PngError(
            f"{path} must be 8-bit RGB or RGBA PNG (got depth={bit_depth} type={color_type})"
        )

    raw = zlib.decompress(bytes(idat))
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    rows: list[bytes] = []
    i = 0
    prev = bytes(stride)
    for _ in range(height):
        filter_type = raw[i]
        i += 1
        filt = bytearray(raw[i : i + stride])
        i += stride
        recon = bytearray(stride)
        for x, byte in enumerate(filt):
            left = recon[x - channels] if x >= channels else 0
            up = prev[x]
            ul = prev[x - channels] if x >= channels else 0
            if filter_type == 0:
                recon[x] = byte
            elif filter_type == 1:
                recon[x] = (byte + left) & 255
            elif filter_type == 2:
                recon[x] = (byte + up) & 255
            elif filter_type == 3:
                recon[x] = (byte + ((left + up) // 2)) & 255
            elif filter_type == 4:
                recon[x] = (byte + _paeth(left, up, ul)) & 255
            else:
                raise PngError(f"{path} has unsupported PNG filter {filter_type}")
        rows.append(bytes(recon))
        prev = recon

    pixels: list[tuple[int, int, int]] = []
    for row in rows:
        for x in range(width):
            o = x * channels
            pixels.append((row[o], row[o + 1], row[o + 2]))
    return width, height, pixels


def write_png_rgb(path: Path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw.extend((r, g, b))
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + tag
            + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def compare_images(before: Path, after: Path, threshold: float) -> dict[str, object]:
    result: dict[str, object] = {
        "before": str(before),
        "after": str(after),
        "pass": False,
        "reason": "",
        "width": 0,
        "height": 0,
        "changed_pixels": 0,
        "total_pixels": 0,
        "changed_fraction": 1.0,
        "threshold": threshold,
    }
    if not before.is_file():
        result["reason"] = "before screenshot is missing"
        return result
    if not after.is_file():
        result["reason"] = "after screenshot is missing"
        return result
    try:
        bw, bh, bp = read_png_rgb(before)
        aw, ah, ap = read_png_rgb(after)
    except PngError as exc:
        result["reason"] = str(exc)
        return result
    if (bw, bh) != (aw, ah):
        result["reason"] = f"size mismatch ({bw}x{bh} vs {aw}x{ah})"
        result["width"] = bw
        result["height"] = bh
        return result
    total = bw * bh
    changed = sum(1 for a, b in zip(bp, ap) if a != b)
    fraction = changed / total if total else 1.0
    passed = fraction <= threshold
    result.update(
        {
            "pass": passed,
            "reason": "pass" if passed else "pages do not match",
            "width": bw,
            "height": bh,
            "changed_pixels": changed,
            "total_pixels": total,
            "changed_fraction": round(fraction, 6),
        }
    )
    return result


def compare_dirs(before_dir: Path, after_dir: Path, threshold: float) -> dict[str, object]:
    names = sorted(
        {p.stem for p in before_dir.glob("*.png")} | {p.stem for p in after_dir.glob("*.png")}
    )
    pages = [
        {"id": name, **compare_images(before_dir / f"{name}.png", after_dir / f"{name}.png", threshold)}
        for name in names
    ]
    passed = bool(pages) and all(page["pass"] for page in pages)
    return {
        "pass": passed,
        "decision": "Pass" if passed else "Rolled back",
        "page_count": len(pages),
        "failed": [page["id"] for page in pages if not page["pass"]],
        "pages": pages,
        "threshold": threshold,
    }


def self_test() -> None:
    import tempfile

    red = [(200, 40, 40)] * 16
    mixed = [(200, 40, 40)] * 15 + [(40, 200, 80)]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_png_rgb(root / "a.png", 4, 4, red)
        write_png_rgb(root / "b.png", 4, 4, red)
        write_png_rgb(root / "c.png", 4, 4, mixed)
        same = compare_images(root / "a.png", root / "b.png", 0.02)
        diff = compare_images(root / "a.png", root / "c.png", 0.02)
        if not same["pass"]:
            raise SystemExit("self-test failed: identical images should Pass")
        if diff["pass"]:
            raise SystemExit("self-test failed: different images should not Pass")
        if diff["changed_pixels"] != 1:
            raise SystemExit(f"self-test failed: expected 1 changed pixel, got {diff['changed_pixels']}")
    print("compare-pages self-test passed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare before and after page screenshots.")
    parser.add_argument("--before", type=Path, help="Folder of before PNGs")
    parser.add_argument("--after", type=Path, help="Folder of after PNGs")
    parser.add_argument("--threshold", type=float, default=0.02)
    parser.add_argument("--out", type=Path, help="Write JSON report to this path")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    if args.before is None or args.after is None:
        parser.error("--before and --after are required")
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")

    report = compare_dirs(args.before, args.after, args.threshold)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
