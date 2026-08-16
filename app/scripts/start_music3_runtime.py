"""Narrow command-line boundary for the local MiniMax Music 3 runtime."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.music3_runtime import (
    PINNED_SGLANG_SOURCE_REVISION,
    STAGE_MANIFEST_NAME,
    Music3RuntimeError,
    apply_music3_reset_plan,
    build_music3_provision_plan,
    build_music3_reset_plan,
    music3_publication_token,
    music3_runtime_status,
    publish_music3_stage,
    retire_stopped_music3_process_marker,
    start_music3_runtime,
    stop_owned_music3_runtime,
    verify_music3_runtime,
)


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the pinned, loopback-only MiniMax Music 3 experiment"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    provision = commands.add_parser("provision")
    provision.add_argument("--pinokio-root", required=True)
    provision.add_argument(
        "--runtime-source-revision",
        default=PINNED_SGLANG_SOURCE_REVISION,
    )
    provision.add_argument("--ucx-version", required=True)
    provision.add_argument("--ucx-source-revision", required=True)
    provision.add_argument("--stage")
    provision.add_argument("--expected-stage-manifest-sha256")
    provision.add_argument("--apply-token")

    verify = commands.add_parser("verify")
    verify.add_argument("--pinokio-root", required=True)

    start = commands.add_parser("start")
    start.add_argument("--pinokio-root", required=True)
    start.add_argument("--port", required=True, type=int)

    status = commands.add_parser("status")
    status.add_argument("--pinokio-root", required=True)

    reset = commands.add_parser("reset-plan")
    reset.add_argument("--pinokio-root", required=True)
    reset.add_argument("--confirm-token")
    return parser


def _provision(options: argparse.Namespace) -> int:
    plan = build_music3_provision_plan(
        options.pinokio_root,
        runtime_source_revision=options.runtime_source_revision,
        ucx_version=options.ucx_version,
        ucx_source_revision=options.ucx_source_revision,
    )
    if (options.apply_token or options.expected_stage_manifest_sha256) and not options.stage:
        raise Music3RuntimeError("stage trust and apply token require --stage")
    if not options.stage:
        _print({
            "plan": plan.to_mapping(),
            "plan_sha256": plan.sha256,
            "stage_manifest_path": str(plan.layout.generations / "<generation>" / STAGE_MANIFEST_NAME),
            "mutation": False,
        })
        return 0
    stage = Path(options.stage)
    if not options.expected_stage_manifest_sha256:
        raise Music3RuntimeError(
            "--stage requires an independently reviewed --expected-stage-manifest-sha256"
        )
    token = music3_publication_token(
        plan,
        stage,
        expected_stage_manifest_sha256=options.expected_stage_manifest_sha256,
    )
    if not options.apply_token:
        _print({
            "plan_sha256": plan.sha256,
            "publication_token": token,
            "stage": str(stage),
            "mutation": False,
        })
        return 0
    _print(publish_music3_stage(
        plan,
        stage,
        apply_token=options.apply_token,
        expected_stage_manifest_sha256=options.expected_stage_manifest_sha256,
    ))
    return 0


def _start(options: argparse.Namespace) -> int:
    previous: dict[int, object] = {}
    process = None
    termination_requested = False
    stopping = False

    def stop_from_signal(_number, _frame) -> None:
        nonlocal termination_requested, stopping
        termination_requested = True
        if process is not None and not stopping:
            stopping = True
            try:
                stop_owned_music3_runtime(options.pinokio_root)
            except Music3RuntimeError:
                stopping = False

    for number in (signal.SIGINT, signal.SIGTERM):
        previous[number] = signal.signal(number, stop_from_signal)
    try:
        process, marker = start_music3_runtime(
            options.pinokio_root,
            port=options.port,
        )
        if termination_requested and not stopping:
            stopping = True
            stop_owned_music3_runtime(options.pinokio_root)
        _print(marker)
        return_code = int(process.wait())
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        try:
            retire_stopped_music3_process_marker(options.pinokio_root)
        except Music3RuntimeError:
            pass
    return return_code


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        if options.command == "provision":
            return _provision(options)
        if options.command == "verify":
            _print(verify_music3_runtime(options.pinokio_root))
            return 0
        if options.command == "start":
            return _start(options)
        if options.command == "status":
            _print(music3_runtime_status(options.pinokio_root))
            return 0
        if options.command == "reset-plan":
            if options.confirm_token:
                _print(apply_music3_reset_plan(
                    options.pinokio_root,
                    confirmation_token=options.confirm_token,
                ))
            else:
                _print(build_music3_reset_plan(options.pinokio_root))
            return 0
    except Music3RuntimeError as error:
        _print({"error": type(error).__name__, "message": str(error)})
        return 2
    raise AssertionError("unreachable Music 3 runtime command")


if __name__ == "__main__":
    raise SystemExit(main())
