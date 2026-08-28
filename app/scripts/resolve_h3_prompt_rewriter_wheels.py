"""Plan or explicitly execute the isolated H3 rewriter wheel resolution."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.h3_prompt_rewriter_wheel_resolver import (
    DEFAULT_BYTE_CAP,
    DEFAULT_DEADLINE_SECONDS,
    MAX_REPORT_BYTES,
    H3PromptRewriterWheelResolverError,
    apply_h3_prompt_rewriter_parent_limits,
    build_h3_prompt_rewriter_wheel_resolution_plan,
    execute_h3_prompt_rewriter_wheel_resolution,
)


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan the isolated H3 prompt-rewriter wheel closure"
    )
    parser.add_argument("--byte-cap", type=int, default=DEFAULT_BYTE_CAP)
    parser.add_argument(
        "--deadline-seconds", type=int, default=DEFAULT_DEADLINE_SECONDS
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--resolution-report")
    parser.add_argument("--expected-resolution-report-sha256")
    parser.add_argument("--private-feature-root")
    parser.add_argument("--staging-root")
    executables = parser.add_mutually_exclusive_group()
    executables.add_argument("--python-executable")
    executables.add_argument("--pip-executable")
    return parser


def _read_private_report_unwrapped(value: str) -> bytes:
    path = Path(value)
    if not path.is_absolute() or Path(os.path.normpath(os.fspath(path))) != path:
        raise H3PromptRewriterWheelResolverError(
            "resolution report path must be canonical and absolute"
        )
    try:
        if path.resolve(strict=True) != path:
            raise H3PromptRewriterWheelResolverError(
                "resolution report path must not traverse links"
            )
        before = path.lstat()
    except OSError as error:
        raise H3PromptRewriterWheelResolverError(
            "resolution report is unavailable"
        ) from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= MAX_REPORT_BYTES
    ):
        raise H3PromptRewriterWheelResolverError(
            "resolution report private-file boundary failed"
        )
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise H3PromptRewriterWheelResolverError(
                "resolution report identity changed"
            )
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise H3PromptRewriterWheelResolverError(
                    "resolution report read was incomplete"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise H3PromptRewriterWheelResolverError(
                "resolution report grew during read"
            )
    finally:
        os.close(descriptor)
    after = path.lstat()
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
    )
    if identity(before) != identity(after):
        raise H3PromptRewriterWheelResolverError(
            "resolution report changed during read"
        )
    return b"".join(chunks)


def _read_private_report(value: str) -> bytes:
    try:
        return _read_private_report_unwrapped(value)
    except H3PromptRewriterWheelResolverError:
        raise
    except OSError as error:
        raise H3PromptRewriterWheelResolverError(
            "resolution report private-file read failed"
        ) from error


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        plan = build_h3_prompt_rewriter_wheel_resolution_plan(
            byte_cap=options.byte_cap,
            deadline_seconds=options.deadline_seconds,
        )
        if not options.execute:
            if any(
                value is not None
                for value in (
                    options.expected_plan_sha256,
                    options.resolution_report,
                    options.expected_resolution_report_sha256,
                    options.private_feature_root,
                    options.staging_root,
                    options.python_executable,
                    options.pip_executable,
                )
            ):
                raise H3PromptRewriterWheelResolverError(
                    "execution arguments require --execute"
                )
            _print({"plan": plan.to_mapping(), "plan_sha256": plan.sha256})
            return 0
        if (
            options.expected_plan_sha256 is None
            or options.resolution_report is None
            or options.expected_resolution_report_sha256 is None
            or options.private_feature_root is None
            or options.staging_root is None
            or (options.python_executable is None) == (options.pip_executable is None)
        ):
            raise H3PromptRewriterWheelResolverError(
                "--execute requires exact plan/report bindings, private roots, and one executable"
            )
        try:
            apply_h3_prompt_rewriter_parent_limits()
        except (OSError, H3PromptRewriterWheelResolverError) as error:
            raise H3PromptRewriterWheelResolverError(
                "reviewed parent resource boundary could not be applied"
            ) from error
        report_payload = _read_private_report(options.resolution_report)
        manifest = execute_h3_prompt_rewriter_wheel_resolution(
            plan,
            expected_plan_sha256=options.expected_plan_sha256,
            resolution_report_payload=report_payload,
            expected_resolution_report_sha256=(
                options.expected_resolution_report_sha256
            ),
            private_feature_root=options.private_feature_root,
            staging_root=options.staging_root,
            python_executable=options.python_executable,
            pip_executable=options.pip_executable,
            apply_parent_limits=lambda: None,
        )
        _print(
            {
                "manifest_sha256": __import__("hashlib")
                .sha256(
                    json.dumps(
                        manifest,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ).encode("ascii")
                    + b"\n"
                )
                .hexdigest(),
                "plan_sha256": plan.sha256,
                "wheel_count": manifest["wheel_count"],
                "total_size_bytes": manifest["total_size_bytes"],
            }
        )
        return 0
    except H3PromptRewriterWheelResolverError:
        _print({"error": "H3PromptRewriterWheelResolverError"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
