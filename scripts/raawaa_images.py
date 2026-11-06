#!/usr/bin/env python3
"""Generate Raawaa imagery through the free provider chain.

    python scripts/raawaa_images.py --status
    python scripts/raawaa_images.py --prompt "..." --out shot.png
    python scripts/raawaa_images.py --batch raawaa/plan/prompts.json

Exit codes: 0 served, 1 every provider failed, 3 served by the offline fallback
(so a real failure is never mistaken for success in CI).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.image_providers import (  # noqa: E402
    ImageChain, ImageProviderError, ImageRequest, chain_from_config,
)


def build_chain() -> ImageChain:
    config = json.loads((ROOT / "config" / "image_providers.json").read_text(encoding="utf-8"))
    return chain_from_config(config, ledger_path=ROOT / "data" / "image-ledger.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raawaa free image generation")
    parser.add_argument("--status", action="store_true", help="show provider availability")
    parser.add_argument("--prompt")
    parser.add_argument("--out", default="raawaa/out/image.png")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--batch", help="JSON file with a list of {prompt,out,width,height}")
    args = parser.parse_args(argv)

    chain = build_chain()

    if args.status:
        print(json.dumps(chain.status(), ensure_ascii=False, indent=2))
        return 0

    jobs = []
    if args.batch:
        jobs = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    elif args.prompt:
        jobs = [{"prompt": args.prompt, "out": args.out,
                 "width": args.width, "height": args.height}]
    else:
        parser.error("one of --status, --prompt or --batch is required")

    served = 0
    fallbacks = 0
    for job in jobs:
        request = ImageRequest(
            prompt=job["prompt"],
            width=int(job.get("width", args.width)),
            height=int(job.get("height", args.height)),
            steps=int(job.get("steps", args.steps)),
        )
        try:
            result, meta = chain.generate(request)
        except ImageProviderError as exc:
            print(f"FAIL {job.get('out')}: {exc}", file=sys.stderr)
            return 1
        path = result.save(ROOT / job["out"])
        skipped = ", ".join(f"{a['provider']}:{a['outcome']}" for a in meta["attempts"])
        print(f"OK   {result.provider:<11} {path}  ({result.width}x{result.height})")
        if skipped:
            print(f"     skipped -> {skipped}")
        if result.provider == "offline":
            fallbacks += 1
        served += 1

    print(f"\nserved {served}, offline fallbacks {fallbacks}")
    return 3 if fallbacks else 0


if __name__ == "__main__":
    raise SystemExit(main())
