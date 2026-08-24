#!/usr/bin/env python3
"""Run a serial, local-only, synthetic MiniMax H3 benchmark matrix.

The runner intentionally has no access to Maestro's job list, gallery, logs, or
existing media.  It creates one procedural reference image, submits known-safe
synthetic prompts, polls only the job IDs it created, and retains only
content-free configuration/timing/validity records.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)
import uuid
import zlib


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
MANAGED_H3_MODELS = frozenset({
    "minimax_h3",
    "minimax_h3_w4a8_fl2va",
    "minimax_h3_pinkcherry_fl2va",
    "minimax_h3_ref2va",
})
NATIVE_RESOLUTIONS = frozenset({
    "1344x768", "768x1344", "1024x768", "768x1024", "768x768",
    "1152x640", "640x1152", "960x544", "544x960", "864x480",
    "480x864", "640x640", "608x352", "352x608",
})
CONFIGURABLE_FIELDS = frozenset({
    "enabled", "steps", "resolution", "attention_engine", "sol_dense_steps",
    "sol_dense_blocks", "export_frames", "expected_frames",
})
SAFE_FAILURE_STAGES = frozenset({
    "denoise", "vae_decode", "segment_checkpoint", "concat", "audio_mux",
    "postprocess", "flashvsr", "delivery", "publication", "generation",
})
SAFE_FAILURE_CODES = frozenset({
    "cuda_oom",
    "denoise_failed",
    "vae_decode_failed",
    "segment_identity_invalid",
    "segment_checkpoint_failed",
    "segment_checkpoint_invalid",
    "segment_encode_failed",
    "concat_input_missing",
    "concat_input_invalid",
    "concat_input_incomplete",
    "concat_overlap_invalid",
    "concat_process_failed",
    "concat_output_invalid",
    "concat_timeout",
    "concat_exception",
    "concat_cancelled",
    "concat_failed",
    "audio_mux_overlap_invalid",
    "audio_mux_process_failed",
    "audio_mux_output_invalid",
    "audio_mux_timeout",
    "audio_mux_exception",
    "audio_mux_failed",
    "postprocess_failed",
    "flashvsr_failed",
    "delivery_fit_failed",
    "delivery_native_protection_failed",
    "delivery_recovery_failed",
    "delivery_failed",
    "publication_failed",
    "generation_failed",
    "h3_boundary_encode_failed",
    "h3_keyframe_encode_failed",
    "h3_reference_encode_failed",
    "h3_prompt_encode_failed",
    "h3_schedule_failed",
    "h3_transformer_warmup_failed",
    "h3_denoise_failed",
    "h3_video_decode_failed",
    "h3_audio_decode_failed",
})
SYNTHETIC_PROMPT = (
    "A clean procedural animation test on a pale blue studio background. "
    "A red circular robot with a yellow triangular chest emblem walks from "
    "left to right while the camera makes a slow, steady lateral move. "
    "Preserve simple geometry, saturated colors, smooth coherent motion, and "
    "stable object identity. Soft electronic footsteps are audible."
)
SYNTHETIC_REF_PROMPT = (
    "Use Picture 1 as the exact visual identity reference for the geometric "
    "robot. The red circular robot with the yellow triangular chest emblem "
    "walks from left to right across a pale blue studio while the camera makes "
    "a slow, steady lateral move. Preserve its shape, colors, emblem, and "
    "identity throughout with smooth coherent motion."
)
BENCHMARK_HEADER_NAME = "X-Maestro-H3-Benchmark"
BENCHMARK_HEADER_VALUE = "synthetic-v1"
LIVE_4K_ACCEPTANCE_CASE = "base_4k_delivery"
ALLOCATION_PROBE_CASES = {
    "base_high_allocation_p4": ("minimax_h3", 4),
    "base_high_allocation_p5": ("minimax_h3", 5),
    "w4a8_high_allocation_p4": ("minimax_h3_w4a8_fl2va", 4),
    "w4a8_high_allocation_p5": ("minimax_h3_w4a8_fl2va", 5),
    "pinkcherry_high_allocation_p4": ("minimax_h3_pinkcherry_fl2va", 4),
    "pinkcherry_high_allocation_p5": ("minimax_h3_pinkcherry_fl2va", 5),
    "ref2va_high_allocation_p3": ("minimax_h3_ref2va", 3),
    "ref2va_high_allocation_p4": ("minimax_h3_ref2va", 4),
    "ref2va_high_allocation_p5": ("minimax_h3_ref2va", 5),
}


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    model_type: str
    steps: int
    resolution: str = "608x352"
    attention_engine: str = "sdpa"
    turbo: bool = False
    semantic_reference: bool = False
    explicit_output: bool = False
    sol_dense_steps: int = 50
    sol_dense_blocks: int = 51
    spatial_upsampling: str = ""
    delivery_resolution: str = ""
    delivery_fit: str = ""
    export_frames: bool = False
    native_boundary_conditioning: bool | None = None
    boundary_mode: str = ""
    procedural_edge: bool = False
    expected_frames: int = 124
    multirate_profile: str = ""
    video_evaluations: int = 0
    audio_evaluations: int = 0
    benchmark_dry_run_only: bool = False
    allocation_probe: bool = False
    offload_profile: int = -1
    enabled: bool = True

    def public_config(self) -> dict[str, Any]:
        result = {
            "case_id": self.case_id,
            "model_type": self.model_type,
            "steps": self.steps,
            "resolution": self.resolution,
            "attention_engine": self.attention_engine,
            "turbo": self.turbo,
            "semantic_reference": self.semantic_reference,
            "explicit_output": self.explicit_output,
        }
        if self.boundary_mode:
            # The non-native semantic path uses FL2VA for the first segment
            # and Ref2VA for the remaining semantic run, so its supplied
            # procedural first-frame edge is still effective.  Native mode
            # carries the same edge through the opt-in mixed contract.
            edge_effective = bool(self.procedural_edge)
            result.update({
                "native_boundary_conditioning": self.native_boundary_conditioning,
                "boundary_mode": self.boundary_mode,
                "procedural_edge": self.procedural_edge,
                "procedural_edge_effective": edge_effective,
                "expected_frames": self.expected_frames,
            })
        if self.attention_engine == "sol_attn":
            result["sol_dense_steps"] = self.sol_dense_steps
            result["sol_dense_blocks"] = self.sol_dense_blocks
        if self.spatial_upsampling:
            result.update({
                "spatial_upsampling": self.spatial_upsampling,
                "delivery_resolution": self.delivery_resolution,
                "delivery_fit": self.delivery_fit,
                "delivery_kind": "learned_upscale",
                "native_resolution": self.resolution,
                "native_delivery_resolution": False,
            })
        if self.multirate_profile:
            result.update({
                "multirate_profile": self.multirate_profile,
                "video_evaluations": self.video_evaluations,
                "audio_evaluations": self.audio_evaluations,
                "benchmark_dry_run_only": self.benchmark_dry_run_only,
            })
        if self.allocation_probe:
            result.update({
                "allocation_probe": True,
                "expected_frames": self.expected_frames,
                "offload_profile": self.offload_profile,
            })
        return result


DEFAULT_CASES = (
    BenchmarkCase("base_native_sdpa", "minimax_h3", 20),
    BenchmarkCase("base_turbo_4_sdpa", "minimax_h3", 4, turbo=True),
    BenchmarkCase("base_turbo_8_sdpa", "minimax_h3", 8, turbo=True),
    BenchmarkCase(
        "base_exact_dense_sol", "minimax_h3", 20,
        attention_engine="sol_attn", sol_dense_steps=50,
    ),
    BenchmarkCase(
        "w4a8_turbo_8_sdpa", "minimax_h3_w4a8_fl2va", 8, turbo=True,
    ),
    BenchmarkCase(
        "ref2va_native_sdpa", "minimax_h3_ref2va", 20,
        semantic_reference=True, export_frames=True,
    ),
    # Opt-in local allocation probe for frame-ceiling calibration. Two steps
    # execute two complete denoising evaluations plus decode, exercising the
    # frame/resolution/model/profile allocation shape without paying for a
    # full 20-step sample. A separate full-step confirmation is still required
    # before promoting a measured ceiling into recovery policy.
    *tuple(
        BenchmarkCase(
            case_id=case_id,
            model_type=model_type,
            steps=2,
            resolution="1344x768",
            attention_engine="sol_attn",
            semantic_reference=model_type == "minimax_h3_ref2va",
            sol_dense_steps=10,
            sol_dense_blocks=2,
            expected_frames=243,
            allocation_probe=True,
            offload_profile=offload_profile,
            enabled=False,
        )
        for case_id, (model_type, offload_profile)
        in ALLOCATION_PROBE_CASES.items()
    ),
    BenchmarkCase(
        "ref2va_turbo_4_sdpa", "minimax_h3_ref2va", 4, turbo=True,
        semantic_reference=True, export_frames=True,
    ),
    BenchmarkCase(
        "ref2va_turbo_8_sdpa", "minimax_h3_ref2va", 8, turbo=True,
        semantic_reference=True, export_frames=True,
    ),
    # Opt-in only: these are the unprivileged reproducibility lanes for the
    # release-bound pinned SageAttention2++ Base native/Turbo evidence. Keep
    # them out of the ordinary matrix so costly validation runs stay explicit.
    BenchmarkCase(
        "base_native_sage2", "minimax_h3", 20,
        attention_engine="sage2", enabled=False,
    ),
    BenchmarkCase(
        "base_turbo_4_sage2", "minimax_h3", 4,
        attention_engine="sage2", turbo=True, enabled=False,
    ),
    BenchmarkCase(
        "base_turbo_8_sage2", "minimax_h3", 8,
        attention_engine="sage2", turbo=True, enabled=False,
    ),
    # Exact opt-in Fast geometry pair. Keep both cases together and use one
    # explicit seed so the only engine-level difference is SDPA versus Sage2.
    BenchmarkCase(
        "base_fast_864_turbo_8_sdpa", "minimax_h3", 8,
        resolution="864x480", turbo=True, export_frames=True, enabled=False,
    ),
    BenchmarkCase(
        "base_fast_864_turbo_8_sage2", "minimax_h3", 8,
        resolution="864x480", attention_engine="sage2", turbo=True,
        export_frames=True, enabled=False,
    ),
    # Fresh/default High and delivery profiles are opt-in because their
    # maximum native canvas and learned upscale stages are intentionally
    # expensive. They use the exact shipped profile settings.
    BenchmarkCase(
        "base_high_native_sol", "minimax_h3", 20,
        resolution="1344x768", attention_engine="sol_attn",
        sol_dense_steps=10, sol_dense_blocks=2, enabled=False,
    ),
    BenchmarkCase(
        "base_1080p_delivery", "minimax_h3", 20,
        resolution="1344x768", attention_engine="sol_attn",
        sol_dense_steps=10, sol_dense_blocks=2,
        spatial_upsampling="flashvsr1.5",
        delivery_resolution="1920x1080", delivery_fit="center_crop",
        enabled=False,
    ),
    BenchmarkCase(
        "base_ultra_delivery", "minimax_h3", 30,
        resolution="1344x768", attention_engine="sdpa",
        spatial_upsampling="flashvsr2pass2",
        delivery_resolution="2688x1536", delivery_fit="upscale_exact",
        enabled=False,
    ),
    BenchmarkCase(
        "base_4k_delivery", "minimax_h3", 30,
        resolution="1344x768", attention_engine="sdpa",
        spatial_upsampling="flashvsr3",
        delivery_resolution="3840x2160", delivery_fit="center_crop",
        enabled=False,
    ),
    # T8Mars publicly describes a four-video/eight-audio dual-clock lane.
    # Maestro records only this content-free evidence identity: no generation
    # code path exists until a live synchronized quality matrix is accepted.
    BenchmarkCase(
        "base_t8_multirate_4v8a_evidence", "minimax_h3", 8,
        attention_engine="sdpa",
        multirate_profile="t8_4v8a_evidence_v1",
        video_evaluations=4,
        audio_evaluations=8,
        benchmark_dry_run_only=True,
        enabled=False,
    ),
    # Wan2GP 12.44 native-boundary evidence lane. These fixed-seed cases are
    # deliberately disabled unless named with --case: two exact 175-frame
    # segments, SDPA, 20 steps, one procedural first-frame edge, with the
    # boundary flag/boundary semantics/reference shape fully crossed.
    *tuple(
        BenchmarkCase(
            case_id=(
                f"boundary_{'on' if native else 'off'}_{boundary}_"
                f"{'semantic' if semantic else 'edge_only'}"
            ),
            model_type=("minimax_h3_ref2va" if semantic else "minimax_h3"),
            steps=20,
            semantic_reference=semantic,
            native_boundary_conditioning=native,
            boundary_mode=boundary,
            procedural_edge=True,
            expected_frames=350,
            export_frames=True,
            enabled=False,
        )
        for native in (False, True)
        for boundary in ("continuous", "cut")
        for semantic in (False, True)
    ),
)


class BenchmarkError(RuntimeError):
    """A deliberately content-free runner failure."""

    def __init__(self, category: str, *, status_code: int | None = None):
        super().__init__(category)
        self.category = category
        self.status_code = status_code


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def validate_local_base_url(value: str) -> str:
    parsed = urlparse(str(value).strip())
    host = (parsed.hostname or "").lower()
    is_loopback = _is_loopback_host(host)
    if parsed.scheme not in {"http", "https"} or not is_loopback:
        raise argparse.ArgumentTypeError(
            "--base-url must target localhost or an IP loopback address"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError(
            "--base-url cannot contain credentials, a query, or a fragment"
        )
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _validate_case(case: BenchmarkCase) -> BenchmarkCase:
    if case.model_type not in MANAGED_H3_MODELS:
        raise ValueError(f"Unsupported managed H3 model for {case.case_id}")
    if isinstance(case.steps, bool) or not isinstance(case.steps, int):
        raise ValueError(f"steps for {case.case_id} must be an integer")
    if not 2 <= case.steps <= 50:
        raise ValueError(f"steps for {case.case_id} must be between 2 and 50")
    if case.resolution not in NATIVE_RESOLUTIONS:
        raise ValueError(f"resolution for {case.case_id} is not H3-native")
    if case.attention_engine not in {"sdpa", "sol_attn", "sage2"}:
        raise ValueError(f"attention_engine for {case.case_id} is invalid")
    if case.turbo and not 4 <= int(case.steps) <= 8:
        raise ValueError(f"Turbo steps for {case.case_id} must be 4 through 8")
    if case.turbo and case.model_type == "minimax_h3_ref2va" and (
        case.steps not in {4, 8} or case.resolution != "608x352"
    ):
        raise ValueError(
            f"Ref2VA Turbo case {case.case_id} must use the fixed 4/8-step "
            "608x352 visual-validation lane"
        )
    if case.semantic_reference != (case.model_type == "minimax_h3_ref2va"):
        raise ValueError(f"conditioning mode does not match {case.case_id}")
    if case.boundary_mode:
        if case.boundary_mode not in {"continuous", "cut"}:
            raise ValueError(f"boundary mode for {case.case_id} is invalid")
        if case.native_boundary_conditioning not in {False, True}:
            raise ValueError(f"native boundary flag for {case.case_id} is invalid")
        if (
            case.steps != 20
            or case.resolution != "608x352"
            or case.attention_engine != "sdpa"
            or not case.procedural_edge
            or case.expected_frames != 350
        ):
            raise ValueError(
                f"native-boundary evidence case {case.case_id} changed its fixed geometry"
            )
    elif not case.allocation_probe and (
        case.native_boundary_conditioning is not None
        or case.procedural_edge
        or case.expected_frames != 124
    ):
        raise ValueError(f"boundary-only settings leaked into {case.case_id}")
    if case.allocation_probe:
        expected_probe = ALLOCATION_PROBE_CASES.get(case.case_id)
        minimum_frames = (
            107 if case.model_type == "minimax_h3_ref2va" else 124
        )
        if (
            expected_probe != (case.model_type, case.offload_profile)
            or case.resolution != "1344x768"
            or case.attention_engine != "sol_attn"
            or case.sol_dense_steps != 10
            or case.sol_dense_blocks != 2
            or case.steps not in {2, 20}
            or case.expected_frames < minimum_frames
            or case.expected_frames > 345
            or (case.expected_frames - 5) % 17 != 0
            or case.turbo
            or case.boundary_mode
            or case.spatial_upsampling
        ):
            raise ValueError(
                f"allocation probe {case.case_id} changed its bounded H3 contract"
            )
    elif case.offload_profile != -1:
        raise ValueError(f"allocation-only offload profile leaked into {case.case_id}")
    if (
        isinstance(case.sol_dense_steps, bool)
        or not isinstance(case.sol_dense_steps, int)
        or case.sol_dense_steps < 0
        or case.sol_dense_steps > 50
    ):
        raise ValueError(f"sol_dense_steps for {case.case_id} is invalid")
    if (
        isinstance(case.sol_dense_blocks, bool)
        or not isinstance(case.sol_dense_blocks, int)
        or case.sol_dense_blocks < 0
        or case.sol_dense_blocks > 51
    ):
        raise ValueError(f"sol_dense_blocks for {case.case_id} is invalid")
    delivery = (
        case.spatial_upsampling, case.delivery_resolution, case.delivery_fit,
    )
    allowed_delivery = {
        ("", "", ""),
        ("flashvsr1.5", "1920x1080", "center_crop"),
        ("flashvsr2pass2", "2688x1536", "upscale_exact"),
        ("flashvsr3", "3840x2160", "center_crop"),
    }
    if delivery not in allowed_delivery:
        raise ValueError(f"delivery settings for {case.case_id} are invalid")
    if case.multirate_profile:
        if (
            case.multirate_profile != "t8_4v8a_evidence_v1"
            or case.model_type != "minimax_h3"
            or case.steps != 8
            or case.attention_engine != "sdpa"
            or case.turbo
            or case.semantic_reference
            or case.video_evaluations != 4
            or case.audio_evaluations != 8
            or not case.benchmark_dry_run_only
            or case.enabled
        ):
            raise ValueError(
                f"multirate evidence case {case.case_id} changed its disabled 4v/8a contract"
            )
    elif (
        case.video_evaluations
        or case.audio_evaluations
        or case.benchmark_dry_run_only
    ):
        raise ValueError(f"multirate-only settings leaked into {case.case_id}")
    for field in (
        "enabled", "export_frames", "procedural_edge", "benchmark_dry_run_only",
        "allocation_probe",
    ):
        if not isinstance(getattr(case, field), bool):
            raise ValueError(f"{field} for {case.case_id} must be boolean")
    return case


def load_matrix_overrides(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("matrix config must be an object keyed by case ID")
    result: dict[str, dict[str, Any]] = {}
    for case_id, values in raw.items():
        if not isinstance(case_id, str) or not isinstance(values, dict):
            raise ValueError("matrix config entries must be objects")
        unknown = set(values) - CONFIGURABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported matrix override fields for {case_id}: {sorted(unknown)}"
            )
        result[case_id] = dict(values)
    return result


def build_matrix(
    selected: Iterable[str] | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[BenchmarkCase]:
    by_id = {case.case_id: case for case in DEFAULT_CASES}
    overrides = dict(overrides or {})
    unknown_overrides = set(overrides) - set(by_id)
    if unknown_overrides:
        raise ValueError(f"unknown benchmark cases: {sorted(unknown_overrides)}")
    wanted = list(selected or [])
    explicitly_selected = bool(wanted)
    unknown_selected = set(wanted) - set(by_id)
    if unknown_selected:
        raise ValueError(f"unknown selected cases: {sorted(unknown_selected)}")
    if not wanted:
        wanted = [case.case_id for case in DEFAULT_CASES]
    result: list[BenchmarkCase] = []
    for case_id in wanted:
        case = by_id[case_id]
        values = dict(overrides.get(case_id) or {})
        if values:
            case = replace(case, **values)
        case = _validate_case(case)
        if case.enabled or (explicitly_selected and "enabled" not in values):
            result.append(case)
    return result


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def procedural_reference_png(width: int = 608, height: int = 352) -> bytes:
    """Return a deterministic RGB PNG containing only geometric shapes."""
    rows = bytearray()
    cx, cy = width * 2 // 5, height // 2
    radius = max(18, min(width, height) // 5)
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = (190, 225, 245)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                color = (214, 45, 55)
            # High-contrast triangular identity emblem.
            rel_y = y - cy + radius // 4
            if 0 <= rel_y <= radius and abs(x - cx) <= rel_y * 2 // 3:
                color = (252, 210, 45)
            # Dark feet establish orientation and make motion easy to inspect.
            if cy + radius - 4 <= y <= cy + radius + 18:
                if cx - radius // 2 <= x <= cx - radius // 6:
                    color = (32, 38, 50)
                if cx + radius // 6 <= x <= cx + radius // 2:
                    color = (32, 38, 50)
            rows.extend(color)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


def classify_failure(value: Any, status_code: int | None = None) -> str:
    text = str(value or "").lower()
    if status_code in {401, 403, 423}:
        return "authorization"
    if "out of memory" in text or "cuda oom" in text:
        return "out_of_memory"
    if "visual gate" in text:
        return "visual_gate"
    if "terms" in text or "license" in text:
        return "terms"
    if "unsupported" in text or "incompatible" in text or "compatib" in text:
        return "compatibility"
    if "download" in text or "not found" in text:
        return "asset_unavailable"
    if status_code is not None and 400 <= status_code < 500:
        return "request_rejected"
    if status_code is not None and status_code >= 500:
        return "server_runtime"
    return "runtime"


def sanitize_failure_details(value: Any) -> dict[str, Any]:
    """Retain only path-free structured failure facts needed by the matrix."""
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    code = value.get("code")
    if isinstance(code, str) and code in SAFE_FAILURE_CODES:
        result["code"] = code
    stage = value.get("stage")
    if isinstance(stage, str) and stage in SAFE_FAILURE_STAGES:
        result["stage"] = stage
    if isinstance(value.get("is_oom"), bool):
        result["is_oom"] = value["is_oom"]
    return result


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never follow redirects away from the already-validated loopback URL."""

    def redirect_request(self, _request, _file, _code, _message, _headers, _newurl):
        return None


class MaestroClient:
    def __init__(self, base_url: str, timeout_seconds: float):
        self.base_url = validate_local_base_url(base_url)
        self.timeout_seconds = float(timeout_seconds)
        jar = http.cookiejar.CookieJar()
        self._proxy_handler = ProxyHandler({})
        self._opener = build_opener(
            self._proxy_handler,
            _NoRedirectHandler(),
            HTTPCookieProcessor(jar),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        target = self.base_url + path
        parsed = urlparse(target)
        if (
            parsed.scheme not in {"http", "https"}
            or not _is_loopback_host(parsed.hostname or "")
            or parsed.username
            or parsed.password
        ):
            raise BenchmarkError("non_loopback_request")
        request_headers = dict(headers or {})
        # Maestro requires an Origin/Referer on mutations while remote
        # sharing is enabled. The runner is loopback-only, so the validated
        # local origin is the correct CSRF origin for every request.
        request_headers.setdefault("Origin", self.base_url)
        request = Request(
            target,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with self._opener.open(
                request, timeout=timeout or self.timeout_seconds,
            ) as response:
                return response.read()
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8", errors="replace"))
                detail = payload.get("detail") if isinstance(payload, dict) else ""
            except (OSError, ValueError, TypeError):
                detail = ""
            raise BenchmarkError(
                classify_failure(detail, error.code), status_code=error.code,
            ) from None
        except (URLError, TimeoutError, OSError) as error:
            raise BenchmarkError(classify_failure(error)) from None

    def json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        raw = self._request(method, path, data=data, headers=request_headers)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BenchmarkError("invalid_server_response") from None
        if not isinstance(parsed, dict):
            raise BenchmarkError("invalid_server_response")
        return parsed

    def establish_project_access(self, project: str, password: str) -> None:
        self.json("GET", "/api/v1/workspaces")
        if password:
            self.json(
                "POST", f"/api/v1/workspaces/{quote(project, safe='')}/unlock",
                {"password": password},
            )

    def upload_procedural_reference(self, png_bytes: bytes) -> str:
        boundary = "----maestro-h3-benchmark-" + uuid.uuid4().hex
        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            b'Content-Disposition: form-data; name="file"; '
            b'filename="procedural-h3-reference.png"\r\n'
        )
        body.extend(b"Content-Type: image/png\r\n\r\n")
        body.extend(png_bytes)
        body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
        raw = self._request(
            "POST", "/api/v1/upload?private=true", data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BenchmarkError("invalid_server_response") from None
        path = payload.get("path") if isinstance(payload, dict) else None
        if not isinstance(path, str) or not path:
            raise BenchmarkError("invalid_server_response")
        return path

    def submit(self, payload: Mapping[str, Any]) -> str:
        custom = payload.get("custom_settings")
        requests_ref2va_turbo_probe = (
            payload.get("model_type") == "minimax_h3_ref2va"
            and isinstance(custom, Mapping)
            and custom.get("h3_turbo_profile") == "h3_turbo_v4"
            and payload.get("num_inference_steps") in (4, 8)
        )
        response = self.json(
            "POST",
            "/api/v1/generate",
            payload,
            headers=(
                {BENCHMARK_HEADER_NAME: BENCHMARK_HEADER_VALUE}
                if requests_ref2va_turbo_probe else None
            ),
        )
        job_id = response.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise BenchmarkError("invalid_server_response")
        return job_id

    def cancel(self, job_id: str) -> bool:
        response = self.json(
            "POST", f"/api/v1/cancel/{quote(job_id, safe='')}",
        )
        return str(response.get("status") or "") in {
            "cancelling", "cancelled", "failed",
        }

    def poll(
        self, job_id: str, *, poll_interval: float, deadline: float,
    ) -> dict[str, Any]:
        while True:
            if time.monotonic() >= deadline:
                raise BenchmarkError("timeout")
            status = self.json("GET", f"/api/v1/status/{quote(job_id, safe='')}")
            if status.get("status") in TERMINAL_STATUSES:
                return status
            time.sleep(max(0.1, float(poll_interval)))

    def download_output(self, filename: str, project: str, destination: Path) -> None:
        if not isinstance(filename, str) or not filename or Path(filename).name != filename:
            raise BenchmarkError("invalid_output_reference")
        query = urlencode({"workspace": project})
        data = self._request(
            "GET", f"/api/v1/file/{quote(filename, safe='')}?{query}",
            timeout=max(60.0, self.timeout_seconds),
        )
        try:
            destination.write_bytes(data)
        except OSError:
            raise BenchmarkError("local_io") from None

    def h3_benchmark_report(self) -> dict[str, Any]:
        return self.json("GET", "/api/v1/h3/benchmark")


def benchmark_sample_counts(report: Any) -> dict[str, int] | None:
    records = report.get("records") if isinstance(report, Mapping) else None
    if not isinstance(records, list):
        return None
    result: dict[str, int] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        spec = record.get("spec")
        spec = spec if isinstance(spec, Mapping) else {}
        cache_key = str(record.get("cache_key") or spec.get("cache_key") or "")
        if not cache_key:
            continue
        try:
            result[cache_key] = max(0, int(record.get("sample_count") or 0))
        except (TypeError, ValueError):
            continue
    return result


def boundary_vram_evidence(
    report: Any,
    *,
    prior_sample_counts: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Select content-free records matching the fixed 175-frame evidence lane."""

    records = report.get("records") if isinstance(report, Mapping) else None
    result: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, Mapping):
            continue
        spec = record.get("spec")
        spec = spec if isinstance(spec, Mapping) else {}
        task = spec.get("task")
        task = task if isinstance(task, Mapping) else {}
        engine = spec.get("engine")
        engine = engine if isinstance(engine, Mapping) else {}
        try:
            fixed_lane = (
                int(task.get("width") or 0) == 608
                and int(task.get("height") or 0) == 352
                and int(task.get("frame_count") or 0) == 175
                and int(task.get("sampling_steps") or 0) == 20
                and str(engine.get("id") or "") == "sdpa"
            )
            sample_count = max(0, int(record.get("sample_count") or 0))
        except (TypeError, ValueError):
            continue
        if not fixed_lane:
            continue
        cache_key = str(record.get("cache_key") or spec.get("cache_key") or "")
        prior_count = int((prior_sample_counts or {}).get(cache_key, 0) or 0)
        if prior_sample_counts is not None and sample_count <= prior_count:
            continue
        model = spec.get("model")
        model = model if isinstance(model, Mapping) else {}
        result.append({
            "model_type": str(model.get("id") or ""),
            "peak_gpu_memory_bytes": record.get("peak_gpu_memory_bytes"),
            "generation_wall_time_seconds": record.get(
                "generation_wall_time_seconds"
            ),
            "sample_count": record.get("sample_count"),
            "sample_count_delta": sample_count - prior_count,
            "source": "local_content_free_h3_cache_delta",
        })
    return result


def allocation_vram_evidence(
    report: Any,
    case: BenchmarkCase,
    *,
    prior_sample_counts: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Select only fresh content-free evidence for one allocation probe."""

    if not case.allocation_probe or prior_sample_counts is None:
        return []
    records = report.get("records") if isinstance(report, Mapping) else None
    result: list[dict[str, Any]] = []
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, Mapping):
            continue
        spec = record.get("spec")
        spec = spec if isinstance(spec, Mapping) else {}
        task = spec.get("task")
        task = task if isinstance(task, Mapping) else {}
        engine = spec.get("engine")
        engine = engine if isinstance(engine, Mapping) else {}
        model = spec.get("model")
        model = model if isinstance(model, Mapping) else {}
        try:
            exact = (
                str(model.get("id") or "") == case.model_type
                and int(task.get("width") or 0) == 1344
                and int(task.get("height") or 0) == 768
                and int(task.get("frame_count") or 0) == case.expected_frames
                and int(task.get("sampling_steps") or 0) == case.steps
                and int(task.get("offload_profile") or 0)
                == case.offload_profile
                and str(engine.get("id") or "") == "sol_attn"
                and int(engine.get("dense_steps") or -1) == 10
                and int(engine.get("dense_blocks") or -1) == 2
            )
            sample_count = max(0, int(record.get("sample_count") or 0))
        except (TypeError, ValueError):
            continue
        if not exact:
            continue
        cache_key = str(record.get("cache_key") or spec.get("cache_key") or "")
        prior_count = int((prior_sample_counts or {}).get(cache_key, 0) or 0)
        if prior_sample_counts is not None and sample_count <= prior_count:
            continue
        peak_gpu_memory_bytes = record.get("peak_gpu_memory_bytes")
        generation_wall_time_seconds = record.get(
            "generation_wall_time_seconds"
        )
        try:
            peak_value = float(peak_gpu_memory_bytes)
            wall_value = float(generation_wall_time_seconds)
        except (OverflowError, TypeError, ValueError):
            continue
        if (
            isinstance(peak_gpu_memory_bytes, bool)
            or not isinstance(peak_gpu_memory_bytes, (int, float))
            or not math.isfinite(peak_value)
            or peak_value < 0
            or isinstance(generation_wall_time_seconds, bool)
            or not isinstance(generation_wall_time_seconds, (int, float))
            or not math.isfinite(wall_value)
            or wall_value < 0
        ):
            continue
        result.append({
            "model_type": case.model_type,
            "offload_profile": case.offload_profile,
            "frame_count": case.expected_frames,
            "sampling_steps": case.steps,
            "peak_gpu_memory_bytes": peak_value,
            "generation_wall_time_seconds": wall_value,
            "sample_count_delta": sample_count - prior_count,
            "source": "local_content_free_h3_allocation_delta",
        })
    return result


def build_generation_payload(
    case: BenchmarkCase,
    *,
    project: str,
    seed: int,
    reference_path: str | None,
) -> dict[str, Any]:
    custom: dict[str, Any] = {
        "h3_attention_engine": case.attention_engine,
        "h3_benchmark_capture": True,
    }
    if case.attention_engine == "sol_attn":
        custom.update({
            "h3_sol_tau": 1.0,
            "h3_sol_dense_steps": case.sol_dense_steps,
            "h3_sol_dense_blocks": case.sol_dense_blocks,
            "h3_sol_min_tokens": 4096,
        })
    if case.turbo:
        custom["h3_turbo_profile"] = "h3_turbo_v4"
    if case.multirate_profile:
        custom["h3_multirate_profile"] = case.multirate_profile
    payload: dict[str, Any] = {
        "workspace": project,
        "model_type": case.model_type,
        "prompt": SYNTHETIC_REF_PROMPT if case.semantic_reference else SYNTHETIC_PROMPT,
        "generation_mode": "video",
        "image_mode": 0,
        "video_length": case.expected_frames,
        # H3 Studio treats this as an explicit physical-segment ceiling.  An
        # allocation probe must stay one exact native clip at the requested
        # legal frame count; inheriting the ordinary 124-frame benchmark
        # window silently turns a 226/243-frame probe into two 124-frame
        # segments and measures the wrong allocation shape.
        "sliding_window_size": (
            case.expected_frames
            if case.allocation_probe
            else 175 if case.boundary_mode else 124
        ),
        "sliding_window_overlap": 0,
        "resolution": case.resolution,
        "num_inference_steps": case.steps,
        "guidance_scale": 1.0,
        "seed": int(seed),
        "repeat_generation": 1,
        "image_prompt_type": "",
        "video_prompt_type": "I" if case.semantic_reference else "",
        "audio_prompt_type": "",
        "activated_loras": [],
        "loras_multipliers": "",
        "tea_cache": 0,
        "private_output": True,
        "explicit_output": case.explicit_output,
        "custom_settings": custom,
    }
    if case.allocation_probe:
        payload["override_profile"] = case.offload_profile
    if case.semantic_reference:
        if not reference_path:
            raise ValueError("semantic reference case requires procedural media")
        payload.update({
            "image_refs": [reference_path],
            "image_refs_relative_size": 100,
            "remove_background_images_ref": 0,
            "h3_ref2va_terms_accepted": True,
            "h3_adaptive_conditioning": False,
        })
    elif case.allocation_probe:
        if not reference_path:
            raise ValueError("allocation probe requires procedural media")
        payload.update({
            "image_start": reference_path,
            "image_prompt_type": "S",
            "h3_adaptive_conditioning": False,
        })
    if case.boundary_mode:
        if not reference_path:
            raise ValueError("boundary evidence case requires procedural media")
        payload.update({
            "image_start": reference_path,
            "image_prompt_type": "S",
            "h3_adaptive_conditioning": True,
            "h3_native_boundary_conditioning": (
                case.native_boundary_conditioning is True
            ),
            "h3_boundary_overrides": [{"type": case.boundary_mode}],
        })
    if case.spatial_upsampling:
        payload.update({
            "spatial_upsampling": case.spatial_upsampling,
            "delivery_resolution": case.delivery_resolution,
            "delivery_fit": case.delivery_fit,
        })
    return payload


def summarize_postprocess_phase_times(events: Any) -> dict[str, float]:
    """Reduce exact job timestamps to content-free stage durations."""
    if not isinstance(events, list):
        return {}
    ordered = [event for event in events if isinstance(event, Mapping)]
    result = {"upscale": 0.0, "delivery_fit": 0.0}
    for current, following in zip(ordered, ordered[1:]):
        phase = str(current.get("phase") or "").strip().lower()
        key = (
            "upscale" if phase == "upscaling"
            else "delivery_fit" if phase == "delivery fit"
            else None
        )
        if key is None:
            continue
        try:
            elapsed = max(0.0, float(following.get("at")) - float(current.get("at")))
        except (TypeError, ValueError):
            continue
        result[key] += elapsed
    return {
        key: round(value, 3) for key, value in result.items() if value > 0
    }


def probe_video(
    path: Path,
    *,
    expected_resolution: str,
    expected_frames: int = 124,
    expected_fps: float = 24.0,
    sample_video_signal: bool = False,
    require_audible_audio: bool = False,
) -> dict[str, Any]:
    """Return path-free structural and optional sampled signal evidence."""

    try:
        artifact_size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        artifact_sha256 = f"sha256:{digest.hexdigest()}"
    except OSError:
        artifact_size = 0
        artifact_sha256 = ""
    artifact_valid = artifact_size > 0 and bool(artifact_sha256)
    artifact_evidence = {
        "artifact_size_bytes": artifact_size,
        "artifact_sha256": artifact_sha256,
    }

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "validation": "unverified" if artifact_valid else "invalid",
            "probe_available": False,
            **artifact_evidence,
            "checks": {"artifact_nonempty": artifact_valid},
        }
    command = [
        ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=60,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {
            "validation": "invalid", "probe_available": True,
            **artifact_evidence,
        }
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video_streams = [
        item for item in streams
        if isinstance(item, Mapping) and item.get("codec_type") == "video"
    ]
    audio_streams = [
        item for item in streams
        if isinstance(item, Mapping) and item.get("codec_type") == "audio"
    ]
    video = video_streams[0] if video_streams else {}
    audio_stream = audio_streams[0] if audio_streams else {}
    audio = len(audio_streams) == 1

    def media_rate(value: Any) -> float:
        try:
            text = str(value or "").strip()
            if "/" in text:
                numerator, denominator = text.split("/", 1)
                rate = float(numerator) / float(denominator)
            else:
                rate = float(text)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
        return rate if math.isfinite(rate) and rate > 0 else 0.0

    actual_fps = media_rate(video.get("avg_frame_rate"))
    if actual_fps <= 0:
        actual_fps = media_rate(video.get("r_frame_rate"))
    container = payload.get("format") if isinstance(payload, Mapping) else {}
    container = container if isinstance(container, Mapping) else {}
    try:
        duration = float(
            video.get("duration")
            or container.get("duration")
            or 0
        )
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        audio_sample_rate = int(audio_stream.get("sample_rate") or 0)
        audio_channels = int(audio_stream.get("channels") or 0)
        audio_duration = float(audio_stream.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
        width = height = 0
        audio_sample_rate = audio_channels = 0
        audio_duration = 0.0
    frame_count: int | None
    try:
        frame_count = int(video.get("nb_read_frames") or video.get("nb_frames"))
    except (TypeError, ValueError):
        frame_count = None
    try:
        expected_width, expected_height = (
            int(value) for value in expected_resolution.split("x", 1)
        )
    except (TypeError, ValueError):
        return {
            "validation": "invalid", "probe_available": True,
            **artifact_evidence,
        }
    try:
        expected_fps = float(expected_fps)
        if (
            isinstance(expected_frames, bool)
            or int(expected_frames) != expected_frames
            or expected_frames <= 0
            or not math.isfinite(expected_fps)
            or expected_fps <= 0
        ):
            raise ValueError
        expected_frames = int(expected_frames)
    except (TypeError, ValueError, OverflowError):
        return {
            "validation": "invalid",
            "probe_available": True,
            **artifact_evidence,
        }
    expected_duration = float(expected_frames) / expected_fps
    duration_tolerance = max(0.01, 1.0 / expected_fps)
    audio_duration_tolerance = max(
        duration_tolerance,
        1024.0 / float(audio_sample_rate or 32_000),
    )
    fps_tolerance = max(0.001, expected_fps * 0.0001)
    checks = {
        "artifact_nonempty": artifact_valid,
        "one_video_stream": len(video_streams) == 1,
        "one_audio_stream": len(audio_streams) == 1,
        "dimensions": (width, height) == (expected_width, expected_height),
        "fps": abs(actual_fps - expected_fps) <= fps_tolerance,
        "duration": abs(duration - expected_duration) <= duration_tolerance,
        "duration_frame_grid": (
            frame_count is not None
            and actual_fps > 0
            and abs(duration * actual_fps - frame_count) <= 1.0
        ),
        "frame_count": frame_count == expected_frames,
        "audio_preserved": audio,
        "audio_duration": (
            audio_duration > 0
            and abs(audio_duration - duration) <= audio_duration_tolerance
        ),
        "audio_32khz_stereo": (
            audio_sample_rate == 32_000 and audio_channels == 2
        ),
    }
    signal_evidence: dict[str, Any] = {}
    if sample_video_signal or require_audible_audio:
        ffmpeg = shutil.which("ffmpeg")
        signal_evidence["signal_probe_available"] = bool(ffmpeg)
        if sample_video_signal:
            checks["sampled_non_black"] = False
            checks["sampled_motion"] = False
        checks["decoded_audio_coverage"] = False
        if require_audible_audio:
            checks["audible_audio"] = False
        if ffmpeg and sample_video_signal:
            sample_indices = (0, expected_frames // 2, expected_frames - 1)
            selector = "+".join(f"eq(n\\,{index})" for index in sample_indices)
            frame_command = [
                ffmpeg, "-v", "error", "-i", str(path),
                "-vf", f"select='{selector}',scale=32:32,format=gray",
                "-vsync", "0", "-an", "-f", "rawvideo", "-",
            ]
            try:
                frame_result = subprocess.run(
                    frame_command, check=True, capture_output=True, timeout=60,
                )
                frame_size = 32 * 32
                if len(frame_result.stdout) != frame_size * len(sample_indices):
                    raise ValueError
                sampled_frames = [
                    frame_result.stdout[offset:offset + frame_size]
                    for offset in range(
                        0, len(frame_result.stdout), frame_size,
                    )
                ]
                mean_luma = max(
                    sum(frame) / (len(frame) * 255.0)
                    for frame in sampled_frames
                )
                motion_delta = max(
                    sum(abs(left - right) for left, right in zip(first, second))
                    / (frame_size * 255.0)
                    for first, second in zip(sampled_frames, sampled_frames[1:])
                )
                checks["sampled_non_black"] = mean_luma > (1.0 / 255.0)
                checks["sampled_motion"] = motion_delta > (1.0 / 255.0)
                signal_evidence.update({
                    "sampled_video_frames": len(sampled_frames),
                    "sampled_max_mean_luma": round(mean_luma, 6),
                    "sampled_max_motion_delta": round(motion_delta, 6),
                })
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        if ffmpeg:
            audio_decode_seconds = min(
                max(duration + audio_duration_tolerance, 0.0), 16.0,
            )
            audio_command = [
                ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0",
                "-vn", "-t", f"{audio_decode_seconds:.6f}", "-ac", "2",
                "-ar", "8000", "-f", "f32le", "-",
            ]
            try:
                audio_result = subprocess.run(
                    audio_command, check=True, capture_output=True, timeout=60,
                )
                samples = [
                    value[0]
                    for value in struct.iter_unpack("<f", audio_result.stdout)
                ]
                if not samples or len(samples) % 2:
                    raise ValueError
                channel_samples = (samples[0::2], samples[1::2])
                channel_ac_rms: list[float] = []
                for channel in channel_samples:
                    trim = min(400, len(channel) // 4)
                    analyzed = channel[trim:len(channel) - trim] or channel
                    dc = sum(analyzed) / len(analyzed)
                    channel_ac_rms.append(math.sqrt(
                        sum((value - dc) ** 2 for value in analyzed)
                        / len(analyzed)
                    ))
                decoded_audio_seconds = len(channel_samples[0]) / 8000.0
                checks["decoded_audio_coverage"] = (
                    abs(decoded_audio_seconds - duration)
                    <= audio_duration_tolerance
                )
                if require_audible_audio:
                    checks["audible_audio"] = (
                        all(math.isfinite(value) for value in channel_ac_rms)
                        and max(channel_ac_rms) > 0.001
                    )
                signal_evidence.update({
                    "sampled_audio_values": len(samples),
                    "decoded_audio_seconds": round(decoded_audio_seconds, 6),
                    "sampled_audio_left_ac_rms": round(channel_ac_rms[0], 8),
                    "sampled_audio_right_ac_rms": round(channel_ac_rms[1], 8),
                })
            except (
                OSError, subprocess.SubprocessError, struct.error, ValueError,
            ):
                pass
    valid = len(video_streams) == 1 and all(checks.values())
    return {
        "validation": "valid" if valid else "invalid",
        "probe_available": True,
        **artifact_evidence,
        "duration_seconds": round(duration, 3),
        "fps": round(actual_fps, 6),
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "has_audio": audio,
        "audio_sample_rate": audio_sample_rate,
        "audio_channels": audio_channels,
        "audio_duration_seconds": round(audio_duration, 3),
        "checks": checks,
        **signal_evidence,
    }


def assess_delivery_publication(
    status: Mapping[str, Any], outputs: Any,
) -> dict[str, Any]:
    """Validate only content-free public finality facts for one delivery job.

    A successful live run cannot exercise the private recovery path without
    fault injection.  It can, however, prove that the terminal job exposes one
    ordinary final basename and no recovery/native capability.  The private
    artifact validation and recovery mechanics remain covered by the separate
    model-free delivery-recovery suite.
    """

    published = outputs if isinstance(outputs, list) else []
    reference = published[0] if len(published) == 1 else None
    safe_reference = (
        isinstance(reference, str)
        and bool(reference)
        and Path(reference).name == reference
        and not reference.startswith(".")
        and reference.casefold().endswith(".mp4")
    )
    recovery_exposed = any(
        bool(status.get(field))
        for field in (
            "delivery_recovery",
            "delivery_recovery_actions",
            "delivery_native",
            "protected_native",
        )
    )
    checks = {
        "terminal_completed": str(status.get("status") or "") == "completed",
        "one_public_output": len(published) == 1,
        "safe_final_reference": safe_reference,
        "protected_native_not_exposed": not recovery_exposed,
    }
    return {
        "validation": "valid" if all(checks.values()) else "invalid",
        "checks": checks,
    }


def measure_boundary_evidence(
    path: Path,
    *,
    width: int,
    height: int,
    boundary_frame: int = 175,
    boundary_mode: str,
) -> dict[str, Any]:
    """Measure content-free seam, cut, identity, and audio-boundary evidence."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"validation": "unverified", "ffmpeg_available": False}
    indices = (0, boundary_frame - 1, boundary_frame, 349)
    selector = "+".join(f"eq(n\\,{index})" for index in indices)
    frame_command = [
        ffmpeg, "-v", "error", "-i", str(path),
        "-vf", f"select='{selector}'", "-vsync", "0",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    try:
        frames_result = subprocess.run(
            frame_command, check=True, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return {"validation": "invalid", "ffmpeg_available": True}
    frame_size = width * height * 3
    if len(frames_result.stdout) != frame_size * len(indices):
        return {"validation": "invalid", "ffmpeg_available": True}
    frames = [
        frames_result.stdout[offset:offset + frame_size]
        for offset in range(0, len(frames_result.stdout), frame_size)
    ]

    def normalized_l1(left: bytes, right: bytes) -> float:
        return sum(abs(a - b) for a, b in zip(left, right)) / (len(left) * 255.0)

    def identity_presence(frame: bytes) -> dict[str, float]:
        pixels = len(frame) // 3
        red = yellow = 0
        for offset in range(0, len(frame), 3):
            r, g, b = frame[offset:offset + 3]
            red += int(r >= 150 and r >= g * 1.35 and r >= b * 1.35)
            yellow += int(r >= 170 and g >= 130 and b <= 120)
        return {
            "red_fraction": round(red / pixels, 6),
            "yellow_fraction": round(yellow / pixels, 6),
        }

    boundary_change = normalized_l1(frames[1], frames[2])
    identities = [identity_presence(frame) for frame in frames]
    evidence: dict[str, Any] = {
        "validation": "measured",
        "ffmpeg_available": True,
        "sampled_frames": list(indices),
        "boundary_rgb_l1": round(boundary_change, 6),
        "continuous_seam_score": (
            round(1.0 - boundary_change, 6)
            if boundary_mode == "continuous" else None
        ),
        "hard_cut_change_score": (
            round(boundary_change, 6) if boundary_mode == "cut" else None
        ),
        "identity_presence": identities,
        "identity_min_red_fraction": min(
            item["red_fraction"] for item in identities
        ),
        "identity_min_yellow_fraction": min(
            item["yellow_fraction"] for item in identities
        ),
    }

    sample_rate = 32_000
    half_window = 0.25
    audio_command = [
        ffmpeg, "-v", "error", "-ss",
        f"{boundary_frame / 24.0 - half_window:.9f}",
        "-i", str(path), "-t", f"{half_window * 2:.9f}",
        "-map", "0:a:0", "-af", f"aresample={sample_rate}",
        "-ac", "2", "-f", "f32le", "-",
    ]
    try:
        audio_result = subprocess.run(
            audio_command, check=True, capture_output=True, timeout=120,
        )
        samples = [
            value[0] for value in struct.iter_unpack("<f", audio_result.stdout)
        ]
    except (OSError, subprocess.SubprocessError, struct.error):
        samples = []
    midpoint = len(samples) // 2
    if midpoint > 1:
        before = samples[:midpoint]
        after = samples[midpoint:]

        def rms(values: list[float]) -> float:
            return math.sqrt(sum(value * value for value in values) / len(values))

        before_rms, after_rms = rms(before), rms(after)
        evidence["audio_boundary"] = {
            "sample_rate": sample_rate,
            "channels": 2,
            "decoded_interleaved_samples": len(samples),
            "rms_before": round(before_rms, 8),
            "rms_after": round(after_rms, 8),
            "rms_delta": round(abs(after_rms - before_rms), 8),
            "sample_jump": round(abs(after[0] - before[-1]), 8),
        }
    else:
        evidence["audio_boundary"] = {"validation": "invalid"}
    return evidence


def export_representative_frames(
    video_path: Path, destination: Path, *, duration_seconds: float,
) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or duration_seconds <= 0:
        return []
    destination.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for label, fraction in (("start", 0.08), ("middle", 0.50), ("end", 0.92)):
        target = destination / f"{label}.png"
        command = [
            ffmpeg, "-v", "error", "-y", "-ss",
            f"{duration_seconds * fraction:.6f}", "-i", str(video_path),
            "-frames:v", "1",
            str(target),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if target.is_file() and target.stat().st_size > 0:
            exported.append(str(target.name))
    return exported


def run_case(
    client: MaestroClient,
    case: BenchmarkCase,
    *,
    project: str,
    seed: int,
    reference_path: str | None,
    output_dir: Path,
    poll_interval: float,
    case_timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    job_id: str | None = None
    record: dict[str, Any] = {
        "config": case.public_config(),
        "status": "failed",
        "wall_time_seconds": 0.0,
        "output_count": 0,
        "validity": {"validation": "not_produced"},
        "visual_frames": [],
    }
    if case.boundary_mode:
        record["boundary_evidence"] = {"validation": "not_produced"}
        record["vram_evidence"] = []
    if case.allocation_probe:
        record["allocation_evidence"] = []
    prior_benchmark_counts: dict[str, int] | None = None
    def cancel_owned_job() -> bool:
        if not job_id:
            return False
        try:
            return bool(client.cancel(job_id))
        except BenchmarkError:
            return False

    try:
        if case.boundary_mode or case.allocation_probe:
            try:
                prior_benchmark_counts = benchmark_sample_counts(
                    client.h3_benchmark_report()
                )
            except BenchmarkError:
                prior_benchmark_counts = None
        job_id = client.submit(build_generation_payload(
            case, project=project, seed=seed, reference_path=reference_path,
        ))
        status = client.poll(
            job_id,
            poll_interval=poll_interval,
            deadline=time.monotonic() + case_timeout,
        )
        record["status"] = str(status.get("status") or "failed")
        record["postprocess_phase_times_seconds"] = summarize_postprocess_phase_times(
            status.get("events")
        )
        outputs = status.get("output_files")
        outputs = outputs if isinstance(outputs, list) else []
        record["output_count"] = len(outputs)
        if record["status"] != "completed":
            failure_details = sanitize_failure_details(status.get("failure_details"))
            if failure_details:
                record["failure_details"] = failure_details
            category = classify_failure(status.get("error"))
            if failure_details.get("is_oom") is True:
                category = "out_of_memory"
            elif (
                failure_details.get("is_oom") is False
                and category == "out_of_memory"
            ):
                category = "runtime"
            record["failure_category"] = category
            return record
        if case.spatial_upsampling:
            record["delivery_acceptance"] = {
                "validation": "not_produced",
                "delivery_kind": "learned_upscale",
                "native_resolution": case.resolution,
                "delivery_resolution": case.delivery_resolution,
                "native_4k": False,
                "public_finality": assess_delivery_publication(status, outputs),
                "private_recovery_live_evidence": "not_exercised_success_path",
            }
            if (
                record["delivery_acceptance"]["public_finality"]["validation"]
                != "valid"
            ):
                record["status"] = "invalid"
                record["failure_category"] = "delivery_publication"
                return record
        if len(outputs) != 1:
            record["status"] = "invalid"
            record["failure_category"] = "unexpected_output_count"
            return record
        with tempfile.TemporaryDirectory(prefix="maestro-h3-benchmark-") as temporary:
            video_path = Path(temporary) / "synthetic-output.mp4"
            client.download_output(str(outputs[0]), project, video_path)
            record["validity"] = probe_video(
                video_path,
                expected_resolution=(case.delivery_resolution or case.resolution),
                expected_frames=case.expected_frames,
                expected_fps=24.0,
                sample_video_signal=True,
                require_audible_audio=not case.semantic_reference,
            )
            if case.boundary_mode:
                expected_width, expected_height = (
                    int(value) for value in case.resolution.split("x", 1)
                )
                record["boundary_evidence"] = measure_boundary_evidence(
                    video_path,
                    width=expected_width,
                    height=expected_height,
                    boundary_mode=case.boundary_mode,
                )
            if case.export_frames:
                frame_dir = output_dir / "visual" / case.case_id
                record["visual_frames"] = export_representative_frames(
                    video_path, frame_dir,
                    duration_seconds=float(
                        record["validity"].get("duration_seconds") or 0
                    ),
                )
        if case.boundary_mode:
            try:
                record["vram_evidence"] = boundary_vram_evidence(
                    client.h3_benchmark_report(),
                    prior_sample_counts=prior_benchmark_counts,
                )
            except BenchmarkError:
                record["vram_evidence"] = []
        if case.allocation_probe:
            try:
                record["allocation_evidence"] = allocation_vram_evidence(
                    client.h3_benchmark_report(),
                    case,
                    prior_sample_counts=prior_benchmark_counts,
                )
            except BenchmarkError:
                record["allocation_evidence"] = []
        frames_valid = (
            not case.export_frames
            or set(record["visual_frames"]) == {
                "start.png", "middle.png", "end.png",
            }
        )
        if record["validity"].get("validation") != "valid" or not frames_valid:
            record["status"] = "invalid"
            record["failure_category"] = "output_validation"
        elif case.spatial_upsampling:
            record["delivery_acceptance"]["validation"] = "valid"
        return record
    except KeyboardInterrupt:
        record["failure_category"] = "interrupted"
        record["cancel_requested"] = cancel_owned_job()
        record["stop_matrix"] = True
        return record
    except BenchmarkError as error:
        record["failure_category"] = error.category
        if error.status_code is not None:
            record["http_status"] = error.status_code
        if error.category == "timeout":
            record["cancel_requested"] = cancel_owned_job()
            record["stop_matrix"] = True
        return record
    finally:
        record["wall_time_seconds"] = round(time.monotonic() - started, 3)


def write_report(output_dir: Path, matrix: list[BenchmarkCase], records: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "serial": True,
        "case_count": len(matrix),
        "results": records,
    }
    destination = output_dir / "h3-benchmark-results.json"
    temporary = output_dir / ".h3-benchmark-results.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", type=validate_local_base_url, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--password-env", default="MAESTRO_PROJECT_PASSWORD",
        help="environment variable containing the project password",
    )
    parser.add_argument(
        "--case", action="append", dest="cases", default=[],
        help="case ID to run; repeat to select multiple (default: all)",
    )
    parser.add_argument(
        "--matrix-config",
        help="JSON object of safe per-case setting overrides",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("h3-benchmark-results"))
    parser.add_argument("--seed", type=int, default=314159265)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--case-timeout", type=float, default=4 * 60 * 60)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the sanitized matrix without contacting Maestro",
    )
    parser.add_argument(
        "--live-4k-acceptance",
        action="store_true",
        help=(
            "run only the exact minimum-duration 1344x768 -> FlashVSR3 -> "
            "3840x2160 learned-upscale delivery gate"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selected_cases = args.cases
        if args.live_4k_acceptance:
            if args.matrix_config:
                raise ValueError("live 4K acceptance does not allow matrix overrides")
            if selected_cases not in ([], [LIVE_4K_ACCEPTANCE_CASE]):
                raise ValueError(
                    f"live 4K acceptance runs only {LIVE_4K_ACCEPTANCE_CASE}"
                )
            selected_cases = [LIVE_4K_ACCEPTANCE_CASE]
        matrix = build_matrix(
            selected_cases, load_matrix_overrides(args.matrix_config),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"Benchmark matrix error: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({
            "synthetic_only": True,
            "serial": True,
            "cases": [case.public_config() for case in matrix],
        }, indent=2, sort_keys=True))
        return 0
    if any(case.benchmark_dry_run_only for case in matrix):
        print(
            "Benchmark matrix error: selected multirate evidence is dry-run only",
            file=sys.stderr,
        )
        return 2
    missing_media_tools = [
        command for command in ("ffmpeg", "ffprobe")
        if not shutil.which(command)
    ]
    if missing_media_tools:
        print(
            "Benchmark setup failed: media_tools_required:" + ",".join(
                missing_media_tools
            ),
            file=sys.stderr,
        )
        return 1
    password = os.environ.get(args.password_env, "")
    try:
        client = MaestroClient(args.base_url, timeout_seconds=120)
        client.establish_project_access(args.project, password)
        reference_path = None
        if any(
            case.semantic_reference or case.procedural_edge
            or case.allocation_probe
            for case in matrix
        ):
            reference_path = client.upload_procedural_reference(
                procedural_reference_png(),
            )
    except BenchmarkError as error:
        print(f"Benchmark setup failed: {error.category}", file=sys.stderr)
        return 1

    records: list[dict[str, Any]] = []
    for index, case in enumerate(matrix, 1):
        print(f"[{index}/{len(matrix)}] {case.case_id}: submitted serially")
        record = run_case(
            client,
            case,
            project=args.project,
            seed=args.seed,
            reference_path=reference_path,
            output_dir=args.output_dir,
            poll_interval=args.poll_interval,
            case_timeout=args.case_timeout,
        )
        records.append(record)
        print(f"[{index}/{len(matrix)}] {case.case_id}: {record['status']}")
        write_report(args.output_dir, matrix, records)
        if record.get("stop_matrix"):
            print(f"[{index}/{len(matrix)}] matrix stopped after {case.case_id}")
            break
    report = write_report(args.output_dir, matrix, records)
    print(f"Sanitized benchmark report: {report}")
    return 0 if all(item["status"] == "completed" for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
