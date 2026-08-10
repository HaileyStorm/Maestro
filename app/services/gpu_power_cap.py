"""Local NVIDIA GPU power-limit discovery and bounded administration.

This module is intentionally not exposed through Maestro's HTTP surface.  A
power limit is host-global state, so changing it remains an explicit local
administrator action performed by :mod:`scripts.configure_gpu_power_cap`.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
import math
import os
import re
import shutil
import stat
import subprocess
from typing import Callable, Iterable, Sequence


_GPU_UUID = re.compile(r"^GPU-[A-Za-z0-9-]+$")
_QUERY_FIELDS = (
    "index,uuid,name,power.min_limit,power.max_limit,"
    "power.default_limit,power.limit"
)


class GpuPowerCapError(RuntimeError):
    """Raised when safe discovery, selection, validation, or application fails."""


@dataclass(frozen=True)
class GpuPowerState:
    index: int
    uuid: str
    name: str
    minimum_watts: float
    maximum_watts: float
    default_watts: float
    current_watts: float

    def public_dict(self) -> dict[str, object]:
        """Return the content-free hardware state suitable for CLI output."""

        return asdict(self)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _trusted_root_executable(path: str) -> bool:
    """Require a root-owned immutable executable and immutable ancestor chain."""

    current = path
    first = True
    while True:
        try:
            metadata = os.stat(current)
        except OSError:
            return False
        expected_type = stat.S_ISREG if first else stat.S_ISDIR
        if (
            not expected_type(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            return False
        if first and not os.access(current, os.X_OK):
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent
        first = False


def _resolve_executable(name: str, *, which: Callable[[str], str | None]) -> str:
    candidate = which(name)
    if not candidate:
        raise GpuPowerCapError(f"{name} is unavailable")
    resolved = os.path.realpath(candidate)
    if not os.path.isabs(resolved) or not _trusted_root_executable(resolved):
        raise GpuPowerCapError(f"{name} is not a trusted root-owned executable")
    return resolved


def _number(value: str, field: str) -> float:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        raise GpuPowerCapError(f"NVIDIA reported an invalid {field}") from None
    if not math.isfinite(number) or number <= 0:
        raise GpuPowerCapError(f"NVIDIA reported an invalid {field}")
    return number


def parse_power_query(output: str) -> list[GpuPowerState]:
    """Parse the fixed ``nvidia-smi`` power query without accepting partial rows."""

    states: list[GpuPowerState] = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 7:
            raise GpuPowerCapError("NVIDIA power query returned an unexpected shape")
        index_text, uuid, name, minimum, maximum, default, current = (
            value.strip() for value in row
        )
        if not index_text.isdecimal() or not _GPU_UUID.fullmatch(uuid):
            raise GpuPowerCapError("NVIDIA power query returned an invalid identity")
        state = GpuPowerState(
            index=int(index_text),
            uuid=uuid,
            name=name,
            minimum_watts=_number(minimum, "minimum power limit"),
            maximum_watts=_number(maximum, "maximum power limit"),
            default_watts=_number(default, "default power limit"),
            current_watts=_number(current, "current power limit"),
        )
        if not (
            state.minimum_watts
            <= state.default_watts
            <= state.maximum_watts
        ):
            raise GpuPowerCapError("NVIDIA reported an invalid default power range")
        if not (
            state.minimum_watts
            <= state.current_watts
            <= state.maximum_watts
        ):
            raise GpuPowerCapError("NVIDIA reported an invalid current power range")
        states.append(state)
    if not states:
        raise GpuPowerCapError("No NVIDIA GPU power controls were found")
    identities = {(state.index, state.uuid) for state in states}
    if len(identities) != len(states):
        raise GpuPowerCapError("NVIDIA power query returned duplicate identities")
    return states


def discover_gpu_power_states(
    *,
    nvidia_smi: str | None = None,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[GpuPowerState]:
    """Query all local NVIDIA devices using a fixed, content-free field set."""

    executable = os.path.realpath(nvidia_smi) if nvidia_smi else _resolve_executable(
        "nvidia-smi", which=which,
    )
    if not os.path.isabs(executable):
        raise GpuPowerCapError("nvidia-smi must be an absolute path")
    command = [
        executable,
        f"--query-gpu={_QUERY_FIELDS}",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        raise GpuPowerCapError("NVIDIA power query failed") from None
    return parse_power_query(completed.stdout)


def select_gpu(
    states: Iterable[GpuPowerState], *, gpu_uuid: str | None = None,
) -> GpuPowerState:
    """Select exactly one GPU, requiring a UUID when discovery is ambiguous."""

    available = list(states)
    requested = str(gpu_uuid or "").strip()
    if requested:
        if not _GPU_UUID.fullmatch(requested):
            raise GpuPowerCapError("GPU UUID is invalid")
        matches = [state for state in available if state.uuid == requested]
        if len(matches) != 1:
            raise GpuPowerCapError("Requested GPU UUID was not found")
        return matches[0]
    if len(available) != 1:
        raise GpuPowerCapError("Multiple NVIDIA GPUs found; select an exact UUID")
    return available[0]


def validate_target_watts(state: GpuPowerState, watts: object) -> int:
    """Return an integer target only when it is inside the live device range."""

    if isinstance(watts, bool):
        raise GpuPowerCapError("Power limit must be a whole number of watts")
    try:
        target = float(watts)
    except (TypeError, ValueError):
        raise GpuPowerCapError("Power limit must be a whole number of watts") from None
    if not math.isfinite(target) or not target.is_integer():
        raise GpuPowerCapError("Power limit must be a whole number of watts")
    integer = int(target)
    if not state.minimum_watts <= integer <= state.maximum_watts:
        raise GpuPowerCapError(
            f"Power limit must be between {state.minimum_watts:g}W "
            f"and {state.maximum_watts:g}W"
        )
    return integer


def apply_power_limit(
    *,
    gpu_uuid: str,
    watts: int,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> GpuPowerState:
    """Apply and verify a power limit through an argv-only ``pkexec`` call.

    Discovery is deliberately repeated immediately before and after the
    privileged mutation.  The exact UUID avoids index drift; no shell is used,
    and this function never handles or persists administrator credentials.
    """

    nvidia_smi = _resolve_executable("nvidia-smi", which=which)
    pkexec = _resolve_executable("pkexec", which=which)
    before = select_gpu(
        discover_gpu_power_states(nvidia_smi=nvidia_smi, runner=runner),
        gpu_uuid=gpu_uuid,
    )
    target = validate_target_watts(before, watts)
    try:
        runner(
            [pkexec, nvidia_smi, "-i", before.uuid, "-pl", str(target)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        raise GpuPowerCapError("GPU power-limit authorization or application failed") from None
    after = select_gpu(
        discover_gpu_power_states(nvidia_smi=nvidia_smi, runner=runner),
        gpu_uuid=before.uuid,
    )
    if not math.isclose(after.current_watts, float(target), abs_tol=0.1):
        raise GpuPowerCapError("GPU power limit did not verify after application")
    return after


def rollback_watts(state: GpuPowerState) -> int:
    """Return the queried hardware default as the explicit rollback target."""

    return validate_target_watts(state, state.default_watts)


__all__: Sequence[str] = (
    "GpuPowerCapError",
    "GpuPowerState",
    "apply_power_limit",
    "discover_gpu_power_states",
    "parse_power_query",
    "rollback_watts",
    "select_gpu",
    "validate_target_watts",
)
