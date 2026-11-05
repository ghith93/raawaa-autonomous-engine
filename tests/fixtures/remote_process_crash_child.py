"""Trusted, local-only child used by the remote D1 crash-recovery drill."""
from __future__ import annotations

import os
import sys
import time


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    marker_path = sys.argv[1]
    with open(marker_path, "a", encoding="ascii") as marker:
        marker.write(f"{os.getpid()}\n")
        marker.flush()
        os.fsync(marker.fileno())
    # The local integration harness kills this child after killing its worker.
    # It has no network access, secrets, package installation, or task inputs.
    time.sleep(120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
