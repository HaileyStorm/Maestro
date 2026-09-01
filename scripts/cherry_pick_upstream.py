#!/usr/bin/env python3
"""
Cherry-pick upstream Wan2GP commits with path rewriting.

Upstream (deepbeepmeep/Wan2GP) puts `wgp.py`, `models/`, `shared/`,
`preprocessing/`, `postprocessing/`, `defaults/`, `profiles/`, and
`plugins/` at the repo root. Maestro nests all of these under `app/`.

A naive `git cherry-pick <upstream-hash>` fails because the paths don't
match. This script generates a patch via `git format-patch`, rewrites
the paths in the patch metadata lines only (never touching hunk
content), and applies the rewritten patch with `git am --3way`. It accepts
only commits contained in fetched refs from the official WanGP remote, rejects
unsafe or unmapped paths, preserves license/notice files, and records immutable
source trailers in the resulting commit message. Independently maintained H3
sources, defaults, profiles, and model registry are protected and require a
manual, symbol-level port.

Usage:
    python scripts/cherry_pick_upstream.py <hash> [<hash> ...]
    python scripts/cherry_pick_upstream.py --dry-run <hash>

Requires:
    - Remote `upstream-wgp` configured to the official repository and fetched.
      git remote add upstream-wgp https://github.com/deepbeepmeep/Wan2GP.git
      git fetch upstream-wgp
    - Python 3.8+

Behavior on conflict:
    `git am` pauses. Resolve files, `git add` them, then run
    `git am --continue`. To abort, run `git am --abort`.

Adding new path mappings:
    Edit PATH_PREFIXES (directories) or FILE_PATHS (single files) below.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Path-rewrite configuration

# Upstream top-level directories that map to app/<same>. Unknown directories
# are rejected; they must never fall through into Maestro's launcher root.
PATH_PREFIXES = (
    "defaults/",
    "docs/",
    "finetunes/",
    "icons/",
    "models/",
    "plugins/",
    "postprocessing/",
    "preprocessing/",
    "profiles/",
    "scripts/",
    "shared/",
)

# These app-owned sources intentionally diverge from upstream and require a
# manual, symbol-level port. A broad upstream commit must never replace them.
PROTECTED_DIRECTORY_PATHS = (
    "models/minimax_h3",
    "profiles/minimax_h3",
)
PROTECTED_FILE_PREFIXES = (
    "defaults/minimax_h3",
)
PROTECTED_FILES = frozenset({
    "models/_settings.json",
})

# Upstream root-level files that map to their app-owned equivalents. In
# particular, upstream README/LICENSE/requirements must not overwrite
# Maestro's launcher-level notice, dependency, or documentation files.
FILE_PATHS = {
    ".gitignore": "app/.gitignore",
    "Custom Resolutions Instructions.txt": "app/Custom Resolutions Instructions.txt",
    "Dockerfile": "app/Dockerfile",
    "LICENSE.txt": "app/LICENSE.txt",
    "README.md": "app/README.md",
    "entrypoint.sh": "app/entrypoint.sh",
    "favicon.png": "app/favicon.png",
    "plugins.json": "app/plugins.json",
    "requirements.txt": "app/requirements.txt",
    "run-docker-cuda-deb.sh": "app/run-docker-cuda-deb.sh",
    "setup.py": "app/setup.py",
    "setup_config.json": "app/setup_config.json",
    "wgp.py": "app/wgp.py",
}

REMOTE = "upstream-wgp"
UPSTREAM_REPOSITORY = "https://github.com/deepbeepmeep/Wan2GP.git"
_ALLOWED_REMOTE_URLS = frozenset({
    UPSTREAM_REPOSITORY,
    "git@github.com:deepbeepmeep/Wan2GP.git",
    "ssh://git@github.com/deepbeepmeep/Wan2GP.git",
})
_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._+@() -]+$")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_COMMITISH = re.compile(r"^[0-9a-fA-F]{7,40}$")
_PROVENANCE_NAME = re.compile(
    r"^(?:authors?|citation|copying|copyright|credits?|licen[cs]e|notices?|"
    r"patents?|third[._-]party[._-](?:licen[cs]es?|notices?))(?:[._-].*)?$",
    re.IGNORECASE,
)
_UNSAFE_GIT_MODES = frozenset({"120000", "160000"})


class UpstreamSyncError(ValueError):
    """A patch or Git source failed the selective-sync policy."""


def _validate_upstream_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or "\0" in path
    ):
        raise UpstreamSyncError(f"unsafe upstream path: {path!r}")
    components = path.split("/")
    if any(
        component in {"", ".", ".."}
        or component != component.strip()
        or component.endswith(".")
        or component.lower() == ".git"
        or _WINDOWS_DEVICE_NAME.fullmatch(component)
        or not _SAFE_PATH_COMPONENT.fullmatch(component)
        for component in components
    ):
        raise UpstreamSyncError(f"unsafe upstream path: {path!r}")
    return path


def _is_provenance_path(path: str) -> bool:
    return _PROVENANCE_NAME.fullmatch(path.rsplit("/", 1)[-1]) is not None


def _is_protected_upstream_path(path: str) -> bool:
    folded = path.casefold()
    if folded in PROTECTED_FILES:
        return True
    if any(folded.startswith(prefix) for prefix in PROTECTED_FILE_PREFIXES):
        return True
    return any(
        folded == directory or folded.startswith(directory + "/")
        for directory in PROTECTED_DIRECTORY_PATHS
    )


def rewrite_path(p: str) -> str:
    """Return one explicitly mapped Maestro app path or fail closed."""
    p = _validate_upstream_path(p)
    if _is_protected_upstream_path(p):
        raise UpstreamSyncError(
            f"protected Maestro-owned upstream path requires manual port: {p!r}"
        )
    for prefix in PATH_PREFIXES:
        if p.startswith(prefix):
            return "app/" + p
    if p in FILE_PATHS:
        return FILE_PATHS[p]
    raise UpstreamSyncError(
        f"unmapped upstream path {p!r}; add an explicit app-owned mapping"
    )


# ---------------------------------------------------------------------------
# Patch rewriting — only touches metadata lines, never hunk content.
#
# Diff metadata line formats handled:
#   diff --git a/PATH b/PATH
#   --- a/PATH
#   +++ b/PATH
#   rename from PATH
#   rename to PATH
#   copy from PATH
#   copy to PATH
#   Binary files a/PATH and b/PATH differ
#
# Special cases that stay untouched:
#   --- /dev/null            (new files)
#   +++ /dev/null            (deleted files)
#   Lines inside hunks (+, -, context, @@)
#   Commit message lines

_METADATA_PREFIXES = (
    "diff --git ", "--- ", "+++ ", "rename from ", "rename to ",
    "copy from ", "copy to ", "Binary files ",
)


def _split_shell_words(line: str, expected: int) -> list[str]:
    # Git's ordinary quoted ASCII paths are shell-compatible. Backslash-quoted
    # octal/non-ASCII paths are deliberately unsupported and therefore fail
    # closed rather than risking a lossy rewrite.
    if "\\" in line:
        raise UpstreamSyncError(
            f"unsupported escaped path metadata: {line!r}"
        )
    try:
        parts = shlex.split(line, posix=True)
    except ValueError as error:
        raise UpstreamSyncError(f"malformed patch metadata: {line!r}") from error
    if len(parts) != expected:
        raise UpstreamSyncError(f"malformed patch metadata: {line!r}")
    return parts


def _quoted_patch_path(path: str, prefix: str = "") -> str:
    value = prefix + path
    if re.fullmatch(r"[A-Za-z0-9._+@()/=-]+", value):
        return value
    return json.dumps(value, ensure_ascii=True)


def _prefixed_path(value: str, prefix: str, line: str) -> str:
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise UpstreamSyncError(f"malformed patch path: {line!r}")
    return value[len(prefix):]


def rewrite_patch(patch_text: str) -> str:
    """Rewrite only diff metadata and reject every unmapped/unsafe path."""
    out_lines = []
    in_diff = False
    in_hunk = False
    saw_diff = False
    current_paths: tuple[str, str] | None = None
    current_old_path: str | None = None
    provenance_rename_from = False
    for line in patch_text.split("\n"):
        if line.startswith("diff --git "):
            parts = _split_shell_words(line, 4)
            if parts[:2] != ["diff", "--git"]:
                raise UpstreamSyncError(f"malformed diff header: {line!r}")
            old = _prefixed_path(parts[2], "a/", line)
            new = _prefixed_path(parts[3], "b/", line)
            mapped_old, mapped_new = rewrite_path(old), rewrite_path(new)
            current_paths = (mapped_old, mapped_new)
            current_old_path = old
            provenance_rename_from = False
            in_diff = True
            in_hunk = False
            saw_diff = True
            out_lines.append(
                "diff --git "
                f"{_quoted_patch_path(mapped_old, 'a/')} "
                f"{_quoted_patch_path(mapped_new, 'b/')}"
            )
            continue

        if not in_diff:
            out_lines.append(line)
            continue
        if line.startswith("@@") or line == "GIT binary patch":
            in_hunk = True
            out_lines.append(line)
            continue
        if in_hunk:
            out_lines.append(line)
            continue

        if line.startswith(
            ("new file mode ", "old mode ", "new mode ", "deleted file mode ")
        ):
            mode_match = re.fullmatch(
                r"(?:new file|deleted file|old|new) mode ([0-7]{6})",
                line,
            )
            if mode_match is None:
                raise UpstreamSyncError(f"malformed file mode metadata: {line!r}")
            mode = mode_match.group(1)
            if mode in _UNSAFE_GIT_MODES:
                raise UpstreamSyncError(
                    f"refusing symlink or gitlink mode in upstream patch: {line!r}"
                )
            if (
                line.startswith("deleted file mode ")
                and current_old_path
                and _is_provenance_path(current_old_path)
            ):
                raise UpstreamSyncError(
                    f"refusing to delete provenance file {current_old_path!r}"
                )
            out_lines.append(line)
            continue

        if line.startswith("index "):
            parts = line.split()
            if len(parts) not in {2, 3} or ".." not in parts[1]:
                raise UpstreamSyncError(f"malformed index metadata: {line!r}")
            if len(parts) == 3 and parts[2] in _UNSAFE_GIT_MODES:
                raise UpstreamSyncError(
                    f"refusing symlink or gitlink mode in upstream patch: {line!r}"
                )
            out_lines.append(line)
            continue

        if line.startswith("--- ") or line.startswith("+++ "):
            parts = _split_shell_words(line, 2)
            marker, value = parts
            expected_marker = "---" if line.startswith("--- ") else "+++"
            expected_prefix = "a/" if marker == "---" else "b/"
            if marker != expected_marker:
                raise UpstreamSyncError(f"malformed file marker: {line!r}")
            if value == "/dev/null":
                if marker == "+++" and current_old_path and _is_provenance_path(
                    current_old_path
                ):
                    raise UpstreamSyncError(
                        f"refusing to delete provenance file {current_old_path!r}"
                    )
                out_lines.append(f"{marker} /dev/null")
                continue
            path = _prefixed_path(value, expected_prefix, line)
            mapped = rewrite_path(path)
            expected = current_paths[0 if marker == "---" else 1]
            if mapped != expected:
                raise UpstreamSyncError(
                    f"diff metadata path mismatch: {path!r}"
                )
            if marker == "---":
                current_old_path = path
            out_lines.append(
                f"{marker} {_quoted_patch_path(mapped, expected_prefix)}"
            )
            continue

        if line.startswith("rename from ") or line.startswith("rename to "):
            parts = _split_shell_words(line, 3)
            direction = parts[1]
            path = parts[2]
            mapped = rewrite_path(path)
            if direction == "from":
                provenance_rename_from = _is_provenance_path(path)
            elif direction == "to" and provenance_rename_from:
                if not _is_provenance_path(path):
                    raise UpstreamSyncError(
                        "refusing to rename a provenance file to a non-provenance path"
                    )
                provenance_rename_from = False
            out_lines.append(
                f"rename {direction} {_quoted_patch_path(mapped)}"
            )
            continue

        if line.startswith("copy from ") or line.startswith("copy to "):
            parts = _split_shell_words(line, 3)
            direction, path = parts[1], parts[2]
            out_lines.append(
                f"copy {direction} {_quoted_patch_path(rewrite_path(path))}"
            )
            continue

        if line.startswith("Binary files "):
            parts = _split_shell_words(line, 6)
            if parts[0:2] != ["Binary", "files"] or parts[3] != "and" or parts[5] != "differ":
                raise UpstreamSyncError(f"malformed binary metadata: {line!r}")
            old = _prefixed_path(parts[2], "a/", line)
            new = _prefixed_path(parts[4], "b/", line)
            out_lines.append(
                "Binary files "
                f"{_quoted_patch_path(rewrite_path(old), 'a/')} and "
                f"{_quoted_patch_path(rewrite_path(new), 'b/')} differ"
            )
            continue

        if line.startswith(_METADATA_PREFIXES):
            raise UpstreamSyncError(f"unsupported patch metadata: {line!r}")
        out_lines.append(line)
    if not saw_diff:
        raise UpstreamSyncError("upstream patch contains no file diff")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Git operations

def _decode_patch(data: bytes) -> str:
    """Expose patch bytes for metadata rewriting without losing any byte."""
    return data.decode("utf-8", errors="surrogateescape")


def _encode_patch(text: str) -> bytes:
    """Restore the exact bytes accepted by :func:`_decode_patch`."""
    return text.encode("utf-8", errors="surrogateescape")


def get_patch(commit: str) -> str:
    """Return git format-patch output for a single commit."""
    result = subprocess.run(
        [
            "git", "format-patch", "-1", "--stdout", "--full-index",
            "--binary", "--no-renames", commit,
        ],
        check=True,
        capture_output=True,
    )
    return _decode_patch(result.stdout)


def verify_remote() -> None:
    """Require the named remote to point at the official Wan2GP repository."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", REMOTE],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise UpstreamSyncError(
            f"remote {REMOTE!r} is not configured; add {UPSTREAM_REPOSITORY}"
        ) from error
    if url not in _ALLOWED_REMOTE_URLS:
        raise UpstreamSyncError(
            f"remote {REMOTE!r} must point at official {UPSTREAM_REPOSITORY}; "
            f"found {url!r}"
        )


def resolve_upstream_commit(commit: str) -> str:
    """Resolve one fetched official-remote commit to its full object ID."""
    if not _COMMITISH.fullmatch(commit):
        raise UpstreamSyncError(
            f"commit must be a 7-40 character hexadecimal object ID: {commit!r}"
        )
    try:
        full = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().lower()
    except subprocess.CalledProcessError as error:
        raise UpstreamSyncError(
            f"commit {commit!r} is not available locally; fetch {REMOTE} first"
        ) from error
    if not _FULL_COMMIT.fullmatch(full):
        raise UpstreamSyncError(f"Git returned an invalid commit ID: {full!r}")
    containing_refs = subprocess.run(
        [
            "git", "for-each-ref", f"--contains={full}",
            "--format=%(refname)", f"refs/remotes/{REMOTE}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if not containing_refs:
        raise UpstreamSyncError(
            f"commit {full} is not contained in a fetched {REMOTE} ref"
        )
    return full


def add_provenance_trailers(patch_text: str, commit: str) -> str:
    """Bind the applied commit to its exact official source in Git history."""
    if not _FULL_COMMIT.fullmatch(commit):
        raise UpstreamSyncError(f"invalid full upstream commit ID: {commit!r}")
    lines = patch_text.split("\n")
    try:
        diff_index = next(
            index for index, line in enumerate(lines)
            if line.startswith("diff --git ")
        )
        separator = max(
            index for index, line in enumerate(lines[:diff_index])
            if line == "---"
        )
    except (StopIteration, ValueError) as error:
        raise UpstreamSyncError(
            "format-patch output is missing its message/diff separator"
        ) from error
    message = lines[:separator]
    if any(
        re.match(r"^\s*upstream-(?:repository|commit)\s*:", line, re.IGNORECASE)
        for line in message
    ):
        raise UpstreamSyncError(
            "upstream message already contains reserved provenance trailers"
        )
    if message and message[-1]:
        message.append("")
    message.extend((
        f"Upstream-Repository: {UPSTREAM_REPOSITORY}",
        f"Upstream-Commit: {commit}",
    ))
    return "\n".join(message + lines[separator:])


def apply_patch(patch_text: str) -> None:
    """Apply a rewritten patch via git am.

    On conflict, git am pauses and the script exits with the failing
    return code. The user is expected to resolve, `git add`, and run
    `git am --continue` (or `git am --abort`).
    """
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".patch", delete=False
    ) as f:
        f.write(_encode_patch(patch_text))
        patch_path = f.name

    try:
        result = subprocess.run(
            ["git", "am", "--3way", patch_path],
            check=False,
        )
        if result.returncode != 0:
            print("", file=sys.stderr)
            print("!! git am failed — likely a merge conflict.", file=sys.stderr)
            print("   Resolve the conflicting files, `git add` them,", file=sys.stderr)
            print("   then run:", file=sys.stderr)
            print("       git am --continue", file=sys.stderr)
            print("   Or abort with:", file=sys.stderr)
            print("       git am --abort", file=sys.stderr)
            sys.exit(result.returncode)
    finally:
        Path(patch_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "commits",
        nargs="+",
        help="Upstream commit hash(es) to cherry-pick, in order.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the rewritten patch to stdout instead of applying.",
    )
    args = parser.parse_args()

    try:
        verify_remote()
    except (subprocess.CalledProcessError, UpstreamSyncError) as error:
        print(f"!! {error}", file=sys.stderr)
        return 1

    staged = []
    for commit in args.commits:
        print(f"==> Cherry-picking {commit}", file=sys.stderr)
        try:
            full_commit = resolve_upstream_commit(commit)
            patch = get_patch(full_commit)
            rewritten = rewrite_patch(patch)
            rewritten = add_provenance_trailers(rewritten, full_commit)
        except (subprocess.CalledProcessError, UpstreamSyncError) as error:
            print(f"!! Refusing {commit}: {error}", file=sys.stderr)
            return 1

        staged.append((full_commit, rewritten))

    for full_commit, rewritten in staged:
        if args.dry_run:
            output = _encode_patch(rewritten) + b"\n"
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(output)
            else:  # Test harnesses may replace stdout with an in-memory stream.
                sys.stdout.write(_decode_patch(output))
            continue

        apply_patch(rewritten)
        print(f"    Applied {full_commit}", file=sys.stderr)

    if not args.dry_run:
        n = len(args.commits)
        print(f"\nApplied {n} commit(s).", file=sys.stderr)
        print(f"Review with: git log -n {n} --oneline", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
