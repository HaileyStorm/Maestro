#!/usr/bin/env python3
"""Inspect, apply, or roll back a local NVIDIA GPU power limit.

The setting is host-global but not persistent: a driver reset or reboot may
restore the device default.  This script is deliberately local CLI-only and is
not reachable through Maestro's HTTP or project APIs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.gpu_power_cap import (  # noqa: E402
    GpuPowerCapError,
    apply_power_limit,
    discover_gpu_power_states,
    rollback_watts,
    select_gpu,
    validate_target_watts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu-uuid",
        help="exact NVIDIA GPU UUID; required when more than one GPU is present",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--watts", type=int, help="whole-watt power limit")
    target.add_argument(
        "--rollback",
        action="store_true",
        help="restore the device-reported default power limit",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="request local administrator authorization and apply the target",
    )
    return parser


def _result(state, *, action: str, target_watts: int | None = None) -> dict:
    result = {
        "action": action,
        "gpu": state.public_dict(),
        "persistent": False,
        "persistence_note": (
            "The power limit is host-global but may reset to the device default "
            "after a reboot or NVIDIA driver reset."
        ),
        "rollback_watts": rollback_watts(state),
    }
    if target_watts is not None:
        result["target_watts"] = target_watts
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        selected = select_gpu(
            discover_gpu_power_states(), gpu_uuid=args.gpu_uuid,
        )
        requested = (
            rollback_watts(selected)
            if args.rollback
            else validate_target_watts(selected, args.watts)
            if args.watts is not None
            else None
        )
        if args.apply and requested is None:
            raise GpuPowerCapError("--apply requires --watts or --rollback")
        if args.apply:
            selected = apply_power_limit(
                gpu_uuid=selected.uuid,
                watts=requested,
            )
            result = _result(
                selected, action="applied", target_watts=requested,
            )
        elif requested is not None:
            result = _result(
                selected, action="preview", target_watts=requested,
            )
        else:
            result = _result(selected, action="status")
    except GpuPowerCapError as error:
        print(json.dumps({"status": "error", "detail": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
