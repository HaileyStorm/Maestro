"""Clean-repo guard for locally generated or private publication artifacts.

The guard inspects only git-tracked paths and never reads or classifies tracked
creative prose. Maestro's local-content-neutrality contract permits authored
subject matter in source, tests, and local model guidance; publication hygiene
is enforced by provenance and path boundaries instead of vocabulary scanning.

Never-publish artifacts such as generated finetune records, downloaded model
metadata, generated LoRA guides, and retired supplement-pack contents must not
be tracked.

Run it before publishing a snapshot (Phase 5), or wire it into CI / a pre-commit
hook once the public repo exists.

(Formerly verify_supplement_refactor.py — repurposed from a one-off refactor
check into the durable boundary guard.)

Usage:
    python scripts/verify_clean_repo.py

Exit codes:
    0 — clean
    1 — leaks found (printed to stdout)
    2 — could not enumerate tracked files (not a git repo / git unavailable)
"""
import os
import re
import subprocess
import sys

# Force UTF-8 output on Windows (cp1252 default chokes on em-dashes and bytes
# from token vocab files). Python 3.7+ supports reconfigure on stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# Paths that must never be git-tracked.
# Regex over the '/'-normalized repo-relative path. These are the locally-
# generated / mature artifacts the whole architecture keeps out of git.
FORBIDDEN_TRACKED_PATTERNS = [
    (re.compile(r"(^|/)_supplement_pack/"),
     "supplement pack contents (mature — must stay gitignored)"),
    (re.compile(r"(^|/)app/postprocessing/seedvc/"),
     "seed-vc component (GPL-3.0 — fetched at install from its own repo, must stay untracked)"),
    (re.compile(r"(^|/)finetunes/[^/]*\.json$"),
     "finetune def (carries per-checkpoint inline guide — must stay gitignored)"),
    (re.compile(r"\.guide\.md$"),
     "generated per-LoRA prompt guide (must stay gitignored)"),
    (re.compile(r"\.civitai\.json$"),
     "CivitAI metadata sidecar (must stay gitignored)"),
]


def _safe_print(text):
    """Print that survives narrow Windows console encodings."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def _tracked_files():
    """Return repo-relative paths of all git-tracked files, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", _REPO_ROOT, "ls-files", "-z"],
            capture_output=True, text=True, encoding="utf-8",
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.split("\0") if p]


def main() -> int:
    files = _tracked_files()
    if files is None:
        _safe_print(f"FAIL: could not list git-tracked files under {_REPO_ROOT} "
                    "(not a git repo, or git unavailable).")
        return 2

    _safe_print(f"Scanning {len(files)} git-tracked file(s) under {_REPO_ROOT}")
    _safe_print("")

    boundary = []

    for rel in files:
        norm = rel.replace("\\", "/")

        for pat, label in FORBIDDEN_TRACKED_PATTERNS:
            if pat.search(norm):
                boundary.append((norm, label))
                break

    failed = False

    if boundary:
        failed = True
        _safe_print(f"FAIL: {len(boundary)} never-publish path(s) are git-tracked "
                    "(should be gitignored):\n")
        for path, label in sorted(set(boundary)):
            _safe_print(f"  {path}")
            _safe_print(f"      -> {label}")
        _safe_print("")

    if failed:
        return 1

    _safe_print("PASS: tracked publication boundaries hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
