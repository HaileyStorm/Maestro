"""Print the owner-supplied storage-tier plan inspection as JSON."""

from __future__ import annotations

import json
from pathlib import Path
import sys


# The documented command runs this file from app/.  Python otherwise puts only
# app/scripts on sys.path, so add the adjacent application root explicitly.
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.storage_tier_plan import inspect_storage_tier_plan


def main() -> int:
    report = inspect_storage_tier_plan()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"not_configured", "unbound", "ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
