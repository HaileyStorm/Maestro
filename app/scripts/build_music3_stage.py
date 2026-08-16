"""Read-only CLI for the reviewed MiniMax Music 3 stage-builder contract."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.music3_runtime import Music3RuntimeError
from services.music3_stage_builder import (
    Music3StageBuilderError,
    build_music3_stage_plan,
    load_music3_resume_record,
    music3_stage_recovery_status,
    verify_music3_download_cache,
)


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _add_reviewed_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pinokio-root", required=True)
    parser.add_argument("--reviewed-manifest", required=True)
    parser.add_argument("--expected-reviewed-manifest-sha256", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan and verify the pinned MiniMax Music 3 offline stage"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--pinokio-root", required=True)
    plan.add_argument("--reviewed-manifest")
    plan.add_argument("--expected-reviewed-manifest-sha256")

    verify_cache = commands.add_parser("verify-cache")
    _add_reviewed_input(verify_cache)

    resume_status = commands.add_parser("resume-status")
    _add_reviewed_input(resume_status)
    resume_status.add_argument("--resume-record", required=True)
    return parser


def _plan(options: argparse.Namespace):
    return build_music3_stage_plan(
        options.pinokio_root,
        reviewed_manifest_path=options.reviewed_manifest,
        expected_reviewed_manifest_sha256=options.expected_reviewed_manifest_sha256,
    )


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        plan = _plan(options)
        if options.command == "plan":
            _print({"plan": plan.to_mapping(), "plan_sha256": plan.sha256})
            return 0
        if options.command == "verify-cache":
            _print(verify_music3_download_cache(plan))
            return 0
        if options.command == "resume-status":
            record = load_music3_resume_record(plan, options.resume_record)
            _print(music3_stage_recovery_status(plan, record))
            return 0
    except (Music3StageBuilderError, Music3RuntimeError) as error:
        _print({"error": type(error).__name__, "message": str(error)})
        return 2
    raise AssertionError("unreachable Music 3 stage-builder command")


if __name__ == "__main__":
    raise SystemExit(main())
