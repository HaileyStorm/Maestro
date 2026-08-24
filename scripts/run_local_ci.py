#!/usr/bin/env python3
"""Run Maestro's trusted GPU-masked checks using existing local dependencies.

The defined commands never install packages, start generation, or contact
GitHub. Environment masking is not hardware isolation and cannot prove what
arbitrary future test code will access.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPO_ROOT / "ui"


@dataclass(frozen=True)
class GateCommand:
    label: str
    argv: tuple[str, ...]
    cwd: Path


def _app_python() -> Path:
    candidates = (
        REPO_ROOT / "app" / "env" / "bin" / "python",
        REPO_ROOT / "app" / "env" / "Scripts" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _ui_tool(name: str) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return UI_ROOT / "node_modules" / ".bin" / f"{name}{suffix}"


def commands_for_gate(gate: str, *, python: Path | None = None) -> tuple[GateCommand, ...]:
    python = python or _app_python()
    guard = (
        GateCommand(
            "tracked publication boundary",
            (str(python), "scripts/verify_clean_repo.py"),
            REPO_ROOT,
        ),
        GateCommand(
            "Python syntax",
            (
                str(python),
                "-m",
                "compileall",
                "-q",
                "-x",
                r"(^|/)(env|node_modules)/",
                "app/services",
                "app/launch.py",
                "scripts",
            ),
            REPO_ROOT,
        ),
    )
    backend = guard + (
        GateCommand(
            "Python unit tests",
            (
                str(python),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
            ),
            REPO_ROOT,
        ),
        GateCommand(
            "JSON grammar regression",
            (str(python), "tests/test_call_llm_json_grammar.py"),
            REPO_ROOT,
        ),
    )
    ui = (
        GateCommand("UI tests", ("npm", "test"), UI_ROOT),
        GateCommand("UI type-check and build", ("npm", "run", "build"), UI_ROOT),
    )
    h3 = (
        GateCommand(
            "H3 Turbo upstream observation",
            (str(python), "scripts/check_h3_turbo_upstream.py"),
            REPO_ROOT,
        ),
    )
    gates = {
        "guard": guard,
        "backend": backend,
        "ui": ui,
        "all": backend + ui,
        "h3-upstream": h3,
    }
    return gates[gate]


def cpu_only_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "",
            "HIP_VISIBLE_DEVICES": "",
            "NVIDIA_VISIBLE_DEVICES": "void",
            "ROCR_VISIBLE_DEVICES": "",
            "MAESTRO_CI_CPU_ONLY": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def validate_dependencies(commands: Iterable[GateCommand], *, python: Path) -> None:
    selected = tuple(commands)
    if any(command.argv[0] == str(python) for command in selected):
        if not python.is_file() or not os.access(python, os.X_OK):
            raise RuntimeError(
                f"Maestro app Python is unavailable at {python}. "
                "Run the normal Pinokio install/update flow; local CI will not install it."
            )
    if any(command.cwd == UI_ROOT for command in selected):
        missing = [tool for tool in ("tsc", "vite") if not _ui_tool(tool).is_file()]
        if not (UI_ROOT / "node_modules").is_dir() or missing:
            detail = ", ".join(missing) if missing else "node_modules"
            raise RuntimeError(
                f"Maestro UI dependencies are unavailable ({detail}). "
                "Run the normal Pinokio update or an explicit local npm install; "
                "local CI will not install packages."
            )
        if shutil.which("npm") is None:
            raise RuntimeError("npm is unavailable on this host")


def run_commands(commands: Iterable[GateCommand], *, dry_run: bool = False) -> int:
    selected = tuple(commands)
    python = _app_python()
    if not dry_run:
        validate_dependencies(selected, python=python)
    env = cpu_only_environment()
    if dry_run:
        for command in selected:
            print(f"==> {command.label}: {shlex.join(command.argv)}", flush=True)
        print("DRY RUN: commands listed; no gates were executed.")
        return 0

    # Keep compileall and incidental cache traffic off the repository/storage
    # tier. The directory is exact, process-owned, and removed on exit.
    with tempfile.TemporaryDirectory(prefix="maestro-local-ci-") as pycache:
        env["PYTHONPYCACHEPREFIX"] = pycache
        for command in selected:
            rendered = shlex.join(command.argv)
            print(f"==> {command.label}: {rendered}", flush=True)
            completed = subprocess.run(
                command.argv,
                cwd=command.cwd,
                env=env,
                check=False,
            )
            if completed.returncode != 0:
                print(
                    f"FAILED ({completed.returncode}): {command.label}",
                    file=sys.stderr,
                )
                return completed.returncode
    print(
        "PASS: selected local CI gates completed with GPU-related "
        "environment variables masked."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gate",
        choices=("guard", "backend", "ui", "all", "h3-upstream"),
        default="all",
        help="bounded gate set to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact commands without checking dependencies or executing them",
    )
    args = parser.parse_args(argv)
    commands = commands_for_gate(args.gate)
    try:
        return run_commands(commands, dry_run=args.dry_run)
    except RuntimeError as error:
        print(f"LOCAL CI PREFLIGHT FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
