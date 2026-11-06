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

# Instagram feed/exports. 4:5 is the tallest classical feed image, 3:4 matches
# Instagram's 2025+ profile grid, 1:1 survives legacy placements, and 9:16 is
# the story/reel cover.
TARGETS = {
    "feed_4x5": (1080, 1350),
    "grid_3x4": (1080, 1440),
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


def _composite_over_backdrop(
    src: Path,
    backdrop: Path,
    dest: Path,
    size: tuple[int, int],
    *,
    bottom_pad: int = 0,
) -> Result:
    """Cover-crop `backdrop` to `size`, then composite `src` fitted inside it.

    Earlier code resized the backdrop and discarded `src` altogether, producing
    blank backgrounds with no product. This keeps 12% breathing room around the
    subject and honours `bottom_pad` on stories.
    """
    width, height = size
    usable_h = height - bottom_pad
    fg_w = max(64, int(width * 0.78))
    fg_h = max(64, int(usable_h * 0.78))
    y_offset = -bottom_pad // 2 if bottom_pad else 0
    offset = f"+0{y_offset:+d}" if y_offset else "+0+0"
    ok, err = _run([
        "convert",
        "(", str(backdrop), "-auto-orient", "-resize", f"{width}x{height}^",
        "-gravity", "center", "-extent", f"{width}x{height}", ")",
        "(", str(src), "-auto-orient", "-resize", f"{fg_w}x{fg_h}", ")",
        "-gravity", "center",
        "-geometry", offset,
        "-composite",
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

    notes = []
    backdrop_path = Path(backdrop) if backdrop and Path(backdrop).exists() else None
    if backdrop_path is not None:
        notes.append("backdrop_composited")

    results = []
    for name, size in TARGETS.items():
        dest = out / f"{src_path.stem}__{name}.jpg"
        bottom = SAFE_BOTTOM_PX if name == "story_9x16" else 0
        if backdrop_path is not None:
            res = _composite_over_backdrop(src_path, backdrop_path, dest, size, bottom_pad=bottom)
        else:
            res = _fit_to_canvas(src_path, dest, size, bottom_pad=bottom)
        results.append(res)

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
