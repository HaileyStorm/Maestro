"""Plan or explicitly produce the isolated H3 uv resolution report."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.h3_prompt_rewriter_uv_resolution_report import (
    DEFAULT_METADATA_BYTE_CAP,
    DEFAULT_METADATA_ENTRY_CAP,
    MAX_DEADLINE_SECONDS,
    H3PromptRewriterUvResolutionError,
    build_h3_prompt_rewriter_uv_resolution_plan,
    execute_h3_prompt_rewriter_uv_resolution,
)


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or explicitly produce the pinned-uv H3 wheel report"
    )
    parser.add_argument("--uv-executable", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--deadline-seconds", type=int, default=MAX_DEADLINE_SECONDS)
    parser.add_argument(
        "--metadata-byte-cap", type=int, default=DEFAULT_METADATA_BYTE_CAP
    )
    parser.add_argument(
        "--metadata-entry-cap", type=int, default=DEFAULT_METADATA_ENTRY_CAP
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--expected-uv-sha256")
    parser.add_argument("--expected-python-sha256")
    parser.add_argument("--private-feature-root")
    parser.add_argument("--state-root")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        plan = build_h3_prompt_rewriter_uv_resolution_plan(
            options.uv_executable,
            options.python_executable,
            deadline_seconds=options.deadline_seconds,
            metadata_byte_cap=options.metadata_byte_cap,
            metadata_entry_cap=options.metadata_entry_cap,
        )
        execution_values = (
            options.expected_plan_sha256,
            options.expected_input_sha256,
            options.expected_uv_sha256,
            options.expected_python_sha256,
            options.private_feature_root,
            options.state_root,
        )
        if not options.execute:
            if any(value is not None for value in execution_values):
                raise H3PromptRewriterUvResolutionError(
                    "execution arguments require --execute"
                )
            _print({"plan": plan.to_mapping(), "plan_sha256": plan.sha256})
            return 0
        if any(value is None for value in execution_values):
            raise H3PromptRewriterUvResolutionError(
                "--execute requires exact plan/input/uv/Python bindings and private roots"
            )
        result = execute_h3_prompt_rewriter_uv_resolution(
            plan,
            expected_plan_sha256=options.expected_plan_sha256,
            expected_input_sha256=options.expected_input_sha256,
            expected_uv_sha256=options.expected_uv_sha256,
            expected_python_sha256=options.expected_python_sha256,
            uv_executable=options.uv_executable,
            python_executable=options.python_executable,
            private_feature_root=options.private_feature_root,
            state_root=options.state_root,
        )
        _print(
            {
                "package_count": result["package_count"],
                "peak_rss_bytes": result["peak_rss_bytes"],
                "plan_sha256": plan.sha256,
                "provenance_sha256": result["provenance_sha256"],
                "pylock_sha256": result["pylock_sha256"],
                "report_sha256": result["report_sha256"],
                "total_candidate_bytes": result["total_candidate_bytes"],
            }
        )
        return 0
    except H3PromptRewriterUvResolutionError:
        _print({"error": "H3PromptRewriterUvResolutionError"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
