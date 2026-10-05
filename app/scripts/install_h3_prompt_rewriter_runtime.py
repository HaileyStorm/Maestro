"""Install a reviewed H3 wheel closure into a fresh private generation.

The controller supplies the pinned uv executable, a hash-bound CPython 3.12.14
interpreter, the reviewed report, and the resolver's completed wheel manifest.
Installation is offline and never changes a production environment. Qualification
uses distribution metadata and uv consistency checks; it imports no models or
torch and makes no native-library, inference, GPU, or human-acceptance claim.
Failed generations remain available for diagnosis. Explicit promotion and
rollback select a qualified generation without moving or modifying its venv.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services import h3_prompt_rewriter_dependency_closure as closure
from services import h3_prompt_rewriter_uv_resolution_report as producer
from services import h3_prompt_rewriter_wheel_resolver as resolver

RECEIPT_SCHEMA = "maestro.h3-prompt-rewriter.runtime-installation.v1"
POINTER_SCHEMA = "maestro.h3-prompt-rewriter.runtime-selection.v1"
RECEIPT_NAME = "runtime-receipt.json"
POINTER_NAME = "current-runtime.json"
MAX_OUTPUT_BYTES = 1024 * 1024
_GENERATION = re.compile(r"[0-9a-f]{32}")
_QUALIFICATION = {"consistency_checked": True, "model_free": True, "cuda_masked": True}
_PROBE = r'''
import importlib.metadata as metadata, json, platform, re, sys
inventory = {}
for distribution in metadata.distributions():
    name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
    if name in inventory:
        raise RuntimeError("duplicate distribution")
    inventory[name] = distribution.version
print(json.dumps({"implementation": platform.python_implementation(),
    "python_version": platform.python_version(), "system": platform.system(),
    "machine": platform.machine(), "libc": list(platform.libc_ver()), "prefix": sys.prefix,
    "base_prefix": sys.base_prefix, "runtime_inventory": inventory}, sort_keys=True))
'''


class H3PromptRewriterInstallationError(RuntimeError):
    """Installation or selection failed; existing runtimes are preserved."""


def _canonical(value: object) -> bytes:
    return resolver._canonical_json(value) + b"\n"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise H3PromptRewriterInstallationError("expected SHA-256 is invalid")
    return value


def _read(path: Path, expected: str | None = None) -> bytes:
    resolver._private_file(path, resolver.MAX_REPORT_BYTES * 8)
    payload = path.read_bytes()
    if expected is not None and _sha(payload) != _digest(expected):
        raise H3PromptRewriterInstallationError("private input hash changed")
    return payload


def _document(path: Path, expected: str | None = None) -> dict:
    payload = _read(path, expected)
    try:
        value = json.loads(payload)
        resolver._bounded_plain_json(value)
    except (ValueError, RecursionError) as error:
        raise H3PromptRewriterInstallationError("private input JSON is invalid") from error
    if type(value) is not dict or _canonical(value) != payload:
        raise H3PromptRewriterInstallationError("private input is not canonical")
    return value


def _directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise H3PromptRewriterInstallationError("private directory traverses links")
    resolver._private_directory(path)


def _layout(feature_root: Path) -> tuple[Path, Path]:
    _directory(feature_root)
    if feature_root.name != "h3-prompt-rewriter":
        raise H3PromptRewriterInstallationError("feature root is not dedicated")
    for production in APP_ROOT.glob("env*"):
        resolved = production.resolve()
        if feature_root == resolved or feature_root.is_relative_to(resolved):
            raise H3PromptRewriterInstallationError("feature root overlaps production")
    for name in ("generations", "state"):
        resolver._mkdir(feature_root / name)
        _directory(feature_root / name)
    return feature_root / "generations", feature_root / "state"


@contextmanager
def _lock(state: Path):
    path = state / "installation.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise H3PromptRewriterInstallationError("installation lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _atomic(path: Path, document: dict) -> None:
    resolver._atomic_write(path, _canonical(document))


def _environment(generation: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "HOME": str(generation / "home"),
        "TMPDIR": str(generation / "tmp"), "UV_CACHE_DIR": str(generation / "cache"),
        "XDG_CACHE_HOME": str(generation / "cache"), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null",
        "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "",
        "ROCR_VISIBLE_DEVICES": "", "NVIDIA_VISIBLE_DEVICES": "none",
        "OMP_NUM_THREADS": "2", "UV_CONCURRENT_INSTALLS": "1",
    }


def _limits() -> None:
    os.umask(0o077)
    os.nice(15)
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**3, 8 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    allowed = sorted(os.sched_getaffinity(0))[:2]
    os.sched_setaffinity(0, allowed)


def _run(command: list[str], *, generation: Path, timeout: int = 600) -> bytes:
    with tempfile.TemporaryFile(dir=generation / "tmp") as output, tempfile.TemporaryFile(
            dir=generation / "tmp") as errors:
        process = subprocess.Popen(command, cwd=generation, env=_environment(generation),
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors, shell=False,
            close_fds=True, start_new_session=True, preexec_fn=_limits)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if output.tell() > MAX_OUTPUT_BYTES or errors.tell() > MAX_OUTPUT_BYTES:
                    raise H3PromptRewriterInstallationError("installation output exceeds bound")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise H3PromptRewriterInstallationError("installation command reached deadline")
                try:
                    code = process.wait(timeout=min(remaining, 0.1))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            producer._cleanup_process_group(process)
            raise
        producer._cleanup_process_group(process)
        if code or output.tell() > MAX_OUTPUT_BYTES or errors.tell() > MAX_OUTPUT_BYTES:
            raise H3PromptRewriterInstallationError("bounded installation command failed")
        output.seek(0)
        return output.read(MAX_OUTPUT_BYTES + 1)


def _verify_inputs(report_path: Path, report_sha256: str, manifest_path: Path,
                   manifest_sha256: str) -> tuple[object, Path]:
    report = resolver._load_report(_read(report_path, report_sha256), report_sha256)
    manifest = _document(manifest_path, manifest_sha256)
    plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan(
        byte_cap=manifest.get("byte_cap"), deadline_seconds=manifest.get("deadline_seconds"))
    rows = manifest.get("wheels")
    if type(rows) is not list or len(rows) != len(report.packages):
        raise H3PromptRewriterInstallationError("wheel manifest inventory differs")
    verified = {}
    for package, row in zip(report.packages, rows):
        expected = {"name": package["name"], "version": package["version"],
            **package["wheel"], "provenance": "sha_bound_reviewed_resolution_report"}
        if row != expected:
            raise H3PromptRewriterInstallationError("wheel manifest contradicts report")
        verified[package["name"]] = package["wheel"]
    if manifest != resolver._manifest(plan, report, verified):
        raise H3PromptRewriterInstallationError("wheel manifest evidence differs")
    wheels = manifest_path.parent / "wheels"
    _directory(wheels)
    filenames = {package["wheel"]["filename"] for package in report.packages}
    if {path.name for path in wheels.iterdir()} != filenames:
        raise H3PromptRewriterInstallationError("wheel directory contains foreign entries")
    versions = {package["name"]: package["version"] for package in report.packages}
    for package in report.packages:
        resolver._wheel_bytes(wheels / package["wheel"]["filename"], package, versions)
    return report, wheels


def _interpreter(path: Path, expected_sha256: str) -> dict:
    inspected = producer._inspect_python(path.resolve(strict=True))
    if inspected.version != closure.RUNTIME_TARGET["python_version"]:
        raise H3PromptRewriterInstallationError("interpreter must be CPython 3.12.14")
    if inspected.sha256 != _digest(expected_sha256):
        raise H3PromptRewriterInstallationError("interpreter hash changed")
    return {"size_bytes": inspected.size_bytes, "sha256": inspected.sha256}


def _qualify(generation: Path, uv: Path, inventory: dict) -> dict:
    for name in ("home", "tmp", "cache", "venv", "venv/bin"):
        _directory(generation / name)
    python = generation / "venv/bin/python"
    result = json.loads(_run([str(python), "-I", "-c", _PROBE], generation=generation))
    if (result.get("implementation") != "CPython" or result.get("python_version") != "3.12.14"
            or result.get("system") != "Linux" or result.get("machine") != "x86_64"
            or type(result.get("libc")) is not list or len(result["libc"]) != 2
            or result["libc"][0] != "glibc"
            or re.fullmatch(r"[0-9]+\.[0-9]+", result["libc"][1]) is None
            or tuple(map(int, result["libc"][1].split("."))) < (2, 35)
            or result.get("prefix") != str(generation / "venv")
            or result.get("prefix") == result.get("base_prefix")
            or result.get("runtime_inventory") != inventory):
        raise H3PromptRewriterInstallationError("isolated runtime inventory differs")
    if any(inventory.get(name) != version for name, version in closure.ROOT_PACKAGE_PINS):
        raise H3PromptRewriterInstallationError("isolated runtime roots differ")
    _run([str(uv), "--no-config", "--offline", "pip", "check", "--python", str(python)],
         generation=generation)
    return dict(_QUALIFICATION)


def _current(state: Path, expected_sha256: str | None) -> None:
    pointer = state / POINTER_NAME
    if expected_sha256 is None:
        if pointer.exists() or pointer.is_symlink():
            raise H3PromptRewriterInstallationError("existing selection requires its exact hash")
    else:
        _document(pointer, expected_sha256)


def _promote(state: Path, generation: str, receipt_sha256: str,
             expected_current_sha256: str | None) -> dict:
    _current(state, expected_current_sha256)
    pointer = {"schema": POINTER_SCHEMA, "generation": generation,
               "receipt_sha256": receipt_sha256}
    _atomic(state / POINTER_NAME, pointer)
    return pointer


def install_runtime(*, feature_root: Path, generation: str, uv_executable: Path,
                    python_executable: Path, expected_python_sha256: str,
                    report_path: Path, expected_report_sha256: str, manifest_path: Path,
                    expected_manifest_sha256: str, promote: bool = False,
                    expected_current_sha256: str | None = None) -> dict:
    """Execute an offline install; callers serialize actual installation authority."""
    if _GENERATION.fullmatch(generation) is None:
        raise H3PromptRewriterInstallationError("generation must be 32 lowercase hex digits")
    if not python_executable.is_absolute() or python_executable.resolve(strict=True) != python_executable:
        raise H3PromptRewriterInstallationError("bootstrap interpreter must be resolved and absolute")
    uv = producer._inspect_uv(uv_executable).path
    bootstrap_seal = _interpreter(python_executable, expected_python_sha256)
    report, wheels = _verify_inputs(report_path, expected_report_sha256,
                                   manifest_path, expected_manifest_sha256)
    if expected_current_sha256 is not None and not promote:
        raise H3PromptRewriterInstallationError("selection binding requires promotion")
    generations, state = _layout(feature_root)
    with _lock(state):
        if promote:
            _current(state, expected_current_sha256)
        destination = generations / generation
        destination.mkdir(mode=0o700, exist_ok=False)
        for name in ("home", "tmp", "cache"):
            resolver._mkdir(destination / name)
        requirements = "".join(f"{package['name']} @ "
            f"{(wheels / package['wheel']['filename']).as_uri()} "
            f"--hash=sha256:{package['wheel']['sha256']}\n" for package in report.packages)
        requirements_path = destination / "requirements.txt"
        descriptor = os.open(requirements_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(requirements)
        _run([str(uv), "--no-config", "--offline", "venv", "--no-python-downloads",
              "--python", str(python_executable), str(destination / "venv")],
             generation=destination)
        python = destination / "venv/bin/python"
        _interpreter(python, bootstrap_seal["sha256"])
        _run([str(uv), "--no-config", "--offline", "pip", "install", "--python", str(python),
              "--no-index", "--require-hashes", "--only-binary=:all:", "--link-mode", "copy",
              "--requirements", str(requirements_path)], generation=destination)
        inventory = {package["name"]: package["version"] for package in report.packages}
        qualification = _qualify(destination, uv, inventory)
        seal = _interpreter(python, bootstrap_seal["sha256"])
        receipt = {"schema": RECEIPT_SCHEMA, "generation": generation,
            "python_executable": str(python), "interpreter_seal": seal,
            "python_version": "3.12.14", "runtime_inventory": inventory,
            "resolution_report_sha256": expected_report_sha256,
            "wheel_manifest_sha256": expected_manifest_sha256,
            "source_sha256": _sha(Path(__file__).read_bytes()),
            "worker_sha256": _sha((APP_ROOT / "services/h3_prompt_rewriter_worker.py").read_bytes()),
            "uv_sha256": producer.PINNED_UV_SHA256, "qualification": qualification,
            "model_execution_accepted": False, "gpu_execution_accepted": False}
        _atomic(destination / RECEIPT_NAME, receipt)
        receipt_sha = _sha(_canonical(receipt))
        if promote:
            _promote(state, generation, receipt_sha, expected_current_sha256)
        return {"generation": generation, "receipt_sha256": receipt_sha,
                "package_count": len(inventory), "promoted": promote}


def activate_generation(*, feature_root: Path, generation: str, uv_executable: Path,
                        expected_receipt_sha256: str,
                        expected_current_sha256: str | None = None) -> dict:
    """Select or roll back to an exact qualified generation, preserving both venvs."""
    if _GENERATION.fullmatch(generation) is None:
        raise H3PromptRewriterInstallationError("generation must be 32 lowercase hex digits")
    uv = producer._inspect_uv(uv_executable).path
    generations, state = _layout(feature_root)
    with _lock(state):
        destination = generations / generation
        _directory(destination)
        receipt = _document(destination / RECEIPT_NAME, expected_receipt_sha256)
        if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("generation") != generation
                or receipt.get("python_executable") != str(destination / "venv/bin/python")
                or receipt.get("python_version") != "3.12.14"
                or receipt.get("qualification") != _QUALIFICATION
                or receipt.get("source_sha256") != _sha(Path(__file__).read_bytes())
                or receipt.get("worker_sha256") != _sha((APP_ROOT / "services/h3_prompt_rewriter_worker.py").read_bytes())
                or receipt.get("uv_sha256") != producer.PINNED_UV_SHA256
                or receipt.get("model_execution_accepted") is not False
                or receipt.get("gpu_execution_accepted") is not False):
            raise H3PromptRewriterInstallationError("generation receipt is incompatible")
        seal = _interpreter(destination / "venv/bin/python", receipt["interpreter_seal"]["sha256"])
        if seal != receipt["interpreter_seal"]:
            raise H3PromptRewriterInstallationError("generation interpreter seal differs")
        _qualify(destination, uv, receipt["runtime_inventory"])
        _promote(state, generation, expected_receipt_sha256, expected_current_sha256)
        return {"generation": generation, "receipt_sha256": expected_receipt_sha256, "promoted": True}


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    install = commands.add_parser("install")
    activate = commands.add_parser("activate", help="promote or roll back an existing generation")
    for command in (install, activate):
        command.add_argument("--execute", action="store_true", required=True)
        command.add_argument("--feature-root", type=Path, required=True)
        command.add_argument("--generation", required=True)
        command.add_argument("--uv-executable", type=Path, required=True)
        command.add_argument("--expected-current-sha256")
    install.add_argument("--python-executable", type=Path, required=True)
    install.add_argument("--expected-python-sha256", required=True)
    install.add_argument("--report", type=Path, required=True)
    install.add_argument("--expected-report-sha256", required=True)
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument("--expected-manifest-sha256", required=True)
    install.add_argument("--promote", action="store_true")
    activate.add_argument("--expected-receipt-sha256", required=True)
    options = parser.parse_args(arguments)
    shared = {"feature_root": options.feature_root, "generation": options.generation,
        "uv_executable": options.uv_executable, "expected_current_sha256": options.expected_current_sha256}
    try:
        if options.operation == "install":
            result = install_runtime(**shared, python_executable=options.python_executable,
                expected_python_sha256=options.expected_python_sha256, report_path=options.report,
                expected_report_sha256=options.expected_report_sha256, manifest_path=options.manifest,
                expected_manifest_sha256=options.expected_manifest_sha256, promote=options.promote)
        else:
            result = activate_generation(**shared, expected_receipt_sha256=options.expected_receipt_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (H3PromptRewriterInstallationError, resolver.H3PromptRewriterWheelResolverError,
            producer.H3PromptRewriterUvResolutionError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError):
        print(json.dumps({"error": "H3PromptRewriterInstallationError"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
