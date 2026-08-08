#!/usr/bin/env python3
"""Manage Maestro's public-only scheduled research cycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.research_pipeline import ResearchPipeline
from services.research_providers import CodexNousRunner, PUBLIC_DATA_DISCLOSURE
from services.research_sources import DEFAULT_CANDIDATES_PER_CYCLE, MAX_CANDIDATES_PER_CYCLE
from services.research_store import ResearchStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and analyze bounded public model/tool/LoRA updates without implementation writes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="print the sanitized research status model")
    status.add_argument("--readiness-threshold", type=int, default=3)

    enable = subparsers.add_parser("enable", help="enable the anchored adaptive schedule")
    enable.add_argument("--dry-run", action="store_true")
    enable.add_argument("--batch-size", type=int, choices=range(1, 25))

    disable = subparsers.add_parser("disable", help="disable automatic research")
    disable.add_argument("--dry-run", action="store_true")

    run = subparsers.add_parser("run", help="run one cycle if due")
    run.add_argument("--force", action="store_true", help="run even when disabled or not due")
    run.add_argument("--dry-run", action="store_true", help="perform no network, provider, or state writes")
    run.add_argument("--batch-size", type=int, choices=range(1, MAX_CANDIDATES_PER_CYCLE + 1))

    packet = subparsers.add_parser("packet", help="emit the exact reconciled review packet")
    packet.add_argument("--readiness-threshold", type=int, default=3)
    packet.add_argument("--force", action="store_true", help="emit below the readiness threshold")
    return parser


def _effective_run_batch_size(store: ResearchStore, requested: int | None) -> int:
    if requested is not None:
        return requested
    schedule = store.load_state().get("schedule") or {}
    configured = schedule.get("batch_size")
    if isinstance(configured, int) and not isinstance(configured, bool) and 1 <= configured <= MAX_CANDIDATES_PER_CYCLE:
        return configured
    return DEFAULT_CANDIDATES_PER_CYCLE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ResearchStore.default()
    if args.command == "status":
        result = store.read_model(readiness_threshold=args.readiness_threshold)
    elif args.command == "enable":
        state = store.enable(dry_run=args.dry_run, batch_size=args.batch_size)
        result = {"dry_run": args.dry_run, "schedule": state["schedule"]}
    elif args.command == "disable":
        state = store.disable(dry_run=args.dry_run)
        result = {"dry_run": args.dry_run, "schedule": state["schedule"]}
    elif args.command == "run":
        runner = CodexNousRunner(disclosure_sink=lambda message: print(message, file=sys.stderr))
        pipeline = ResearchPipeline(
            store,
            analyst=runner,
            max_candidates=_effective_run_batch_size(store, args.batch_size),
        )
        if not args.dry_run:
            print(PUBLIC_DATA_DISCLOSURE, file=sys.stderr)
            runner.disclosure_sink = None
        result = pipeline.run(force=args.force, dry_run=args.dry_run)
    elif args.command == "packet":
        result = store.build_implementation_packet(
            readiness_threshold=args.readiness_threshold,
            force=args.force,
        )
    else:  # pragma: no cover - argparse prevents this path
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
