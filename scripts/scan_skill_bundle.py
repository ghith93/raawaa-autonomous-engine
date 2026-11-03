"""CLI for offline, non-executing skill bundle preflight."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.skill_static_gate import scan_skill_bundle

MAX_INPUT_BYTES = 2 * 1024 * 1024


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else Path("skill-bundle.json")
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("skill bundle input exceeds 2 MiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("skill bundle input must be a JSON object")
        result = scan_skill_bundle(payload.get("manifest"), payload.get("files"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({
            "schema_version": 1, "status": "BLOCKED",
            "error": type(exc).__name__, "network_calls": 0,
            "model_calls": 0, "code_execution": False,
        }, separators=(",", ":")))
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
