#!/usr/bin/env python3
"""Turn one phone photo into every feed format Raawaa needs.

The division of labour this encodes:

    you:  take one decent photo of the bottle (about 2 minutes)
    this: crop, pad, colour-match the backdrop, and export 4:5 / 1:1 / 9:16

That is the whole trick. A boutique charges for the part below; the founder
cannot outsource the shutter press, but there is no reason to do the rest by
hand for every post.

Requires ImageMagick (`convert`). No Python imaging library, no network, no
model. Falls back with a clear message when the tool is missing.

Usage:
    python scripts/raawaa_compose.py shot.jpg --out raawaa/out
    python scripts/raawaa_compose.py shot.jpg --out raawaa/out --backdrop raawaa/assets/bg-linen.png
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Instagram feed/exports. 4:5 is the tallest feed image, so it earns the most
# screen; 1:1 survives old placements; 9:16 is the story/reel cover.
TARGETS = {
    "feed_4x5": (1080, 1350),
    "square_1x1": (1080, 1080),
    "story_9x16": (1080, 1920),
}

# Keeps the product off the very bottom of a story, where the caption and the
# reply bar sit, and leaves quiet space for type on a feed post.
SAFE_BOTTOM_PX = 220


@dataclass
class Result:
    fmt: str
    path: Path
    ok: bool
    detail: str = ""


def have_imagemagick() -> bool:
    return shutil.which("convert") is not None


def _run(args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stderr or "").strip()


def _fit_to_canvas(src: Path, dest: Path, size: tuple[int, int], *, bottom_pad: int = 0) -> Result:
    """Cover-crop the source into `size`, then optionally reserve space at the bottom."""
    width, height = size
    usable = height - bottom_pad
    # -resize with ^ and -gravity center -extent is the standard cover crop.
    ok, err = _run([
        "convert", str(src),
        "-auto-orient",
        "-resize", f"{width}x{usable}^",
        "-gravity", "center",
        "-extent", f"{width}x{usable}",
        "-bordercolor", "#F2EBE3",
        "-border", f"0x0",
        str(dest),
    ])
    if not ok:
        return Result(dest.stem, dest, False, err[:200])
    if bottom_pad:
        ok, err = _run([
            "convert", str(dest),
            "-background", "#F2EBE3",
            "-gravity", "north",
            "-extent", f"{width}x{height}",
            str(dest),
        ])
        if not ok:
            return Result(dest.stem, dest, False, err[:200])
    return Result(dest.stem, dest, True)


def compose(src: str | Path, out_dir: str | Path, *, backdrop: str | None = None) -> dict:
    src_path = Path(src)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not src_path.exists():
        raise FileNotFoundError(src_path)
    if not have_imagemagick():
        raise RuntimeError("ImageMagick `convert` is not installed")

    source = src_path
    notes = []
    # If the shot is landscape and a backdrop exists, letterbox it onto the
    # backdrop instead of cropping the product. This is the only "design" step.
    if backdrop and Path(backdrop).exists():
        composed = out / "_backdrop.png"
        ok, err = _run([
            "convert", str(backdrop),
            "-resize", "1080x1350^", "-gravity", "center", "-extent", "1080x1350",
            str(composed),
        ])
        if ok:
            source = composed
            notes.append("backdrop_applied")

    results = []
    for name, size in TARGETS.items():
        dest = out / f"{src_path.stem}__{name}.jpg"
        bottom = SAFE_BOTTOM_PX if name == "story_9x16" else 0
        res = _fit_to_canvas(source, dest, size, bottom_pad=bottom)
        results.append(res)

    if source != src_path:
        source.unlink(missing_ok=True)

    return {
        "source": str(src_path),
        "out_dir": str(out),
        "notes": notes,
        "outputs": [
            {"format": r.fmt, "path": str(r.path), "ok": r.ok, "detail": r.detail}
            for r in results
        ],
        "all_ok": all(r.ok for r in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose Raawaa feed formats from one phone photo")
    parser.add_argument("source")
    parser.add_argument("--out", default="raawaa/out")
    parser.add_argument("--backdrop", default=None)
    args = parser.parse_args(argv)

    if not have_imagemagick():
        print("ImageMagick not found. Install it, or run this on a machine that has it.",
              file=sys.stderr)
        return 2
    try:
        report = compose(args.source, args.out, backdrop=args.backdrop)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for item in report["outputs"]:
        status = "OK  " if item["ok"] else "FAIL"
        print(f"{status} {item['format']:<12} {item['path']}")
        if not item["ok"]:
            print(f"     {item['detail']}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
