"""Local and OpenAI-compatible LLM service backed by llama-server.

Local GGUF models use a hardware-aware llama.cpp runtime with optional linked
multimodal projectors.  Maestro can use CPU binaries or build its pinned Linux
CUDA runtime when GPU inference is requested.
"""

import os
import gc
import hashlib
import functools
import inspect
import math
import re
import time
import subprocess
import threading
import logging
import requests
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit
from typing import Callable, Optional, Sequence

from services.llm_cancellation import (
    LlmCancellationHandle,
    LlmRequestCancelled,
)
from services.llm_response_assist import (
    PrefixEchoStripper,
    RequestProgress,
    apply_local_assistant_prefill,
    normalize_response_assist,
    response_assist_refused,
    response_assist_retry_enabled,
    strip_one_prefix,
)

logger = logging.getLogger(__name__)


def _cancellation_checkpoint(
    cancel_handle: Optional[LlmCancellationHandle],
) -> None:
    if cancel_handle is not None:
        cancel_handle.checkpoint()


def _register_cancellable_response(
    cancel_handle: Optional[LlmCancellationHandle],
    response,
) -> None:
    if cancel_handle is not None:
        cancel_handle.register_response(response)
        cancel_handle.checkpoint()


def _unregister_cancellable_response(
    cancel_handle: Optional[LlmCancellationHandle],
    response,
) -> bool:
    if cancel_handle is None:
        return True
    return cancel_handle.unregister_response(response)

# Singleton state
_process: Optional[subprocess.Popen] = None
_lock = threading.RLock()
_runtime_status_lock = threading.RLock()
_model_id: str = ""
_device: str = ""
_server_port: int = 0
_vision_available: bool = False
_runtime_backend: str = ""
_runtime_build: Optional[int] = None
_runtime_devices: list[str] = []
_runtime_profile: dict = {}
_runtime_timings: dict = {}
_requested_device: str = ""
_runtime_fallback_reason: str = ""
_runtime_model_size_gb: float = 0.0
_runtime_timings_multimodal: bool = False
_runtime_speed_variant_digest: str = ""
_runtime_generation_counter: int = 0
_runtime_generation: int = 0
_runtime_attempt_counter: int = 0
_runtime_active_attempt_id: int = 0
_runtime_phase: str = "idle"
_runtime_execution: str = ""
_runtime_load_started_at: Optional[float] = None
_runtime_load_finished_at: Optional[float] = None
_runtime_request_started_at: Optional[float] = None
_runtime_request_finished_at: Optional[float] = None
_runtime_abort_requested_at: Optional[float] = None
_runtime_last_release: dict = {}
_runtime_last_aborted_attempt: tuple[int, int, bool] = (0, 0, False)
_runtime_output_token_limit: Optional[int] = None
_runtime_observed_output_tokens: Optional[int] = None
_runtime_request_pass: int = 0
_runtime_request_multimodal: Optional[bool] = None
_runtime_remaining_rate_tps: Optional[float] = None
_runtime_remaining_selection_digest: str = ""
_runtime_remaining_invalid_reason: str = "no_active_attempt"
_hardware_cache: Optional[dict] = None
_nvcc_path_cache: Optional[str] = None
_speed_observation_lock = threading.Lock()
_speed_observation_cache: Optional[dict] = None
_speed_observation_cache_identity: Optional[tuple] = None
_speed_hardware_identity_cache: dict[str, tuple[str, dict]] = {}
_SPEED_OBSERVATION_VERSION = 2
_MAX_SPEED_OBSERVATIONS = 96

# Bounded, content-free diagnostics for llama-server's combined stdout/stderr.
# The pipe is drained immediately after launch so cold-load output cannot fill
# it and deadlock the child. Raw lines are never retained or written to disk.
import collections as _collections
_server_log: "_collections.deque[str]" = _collections.deque(maxlen=200)
_log_reader: Optional[threading.Thread] = None
_log_reader_generation: int = 0
_LOG_DRAIN_EXIT_WAIT_SEC: float = 2.0

# Provider state: "local" | "remote" | "openai" | "anthropic"
_provider: str = "local"
_remote_url: str = ""       # Base URL for remote/OpenAI-compatible servers
_api_key: str = ""           # API key for OpenAI/Anthropic
_loaded_model_key: tuple = ()  # Provider + exact source used by the singleton

# Auto-unload idle timer
_idle_timer: Optional[threading.Timer] = None
_idle_timer_generation: int = 0
_idle_timeout: float = 60.0  # seconds before auto-unload

# Streaming state — accumulates tokens during generation
_stream_buffer: str = ""
_stream_done: bool = True
_stream_lock = threading.Lock()
_download_state_lock = threading.Lock()
_download_state: dict = {}
_loading_model_id: str = ""
_model_activity = threading.local()
_runtime_request = threading.local()


CPU_COEXISTENCE_MODE = "cooperative_cpu"
_CPU_COEXISTENCE_MAX_THREADS = 8
_CPU_COEXISTENCE_MAX_BATCH_THREADS = 8
_CPU_COEXISTENCE_BATCH_SIZE = 256
_CPU_COEXISTENCE_UBATCH_SIZE = 64
_PROCESS_TERMINATE_TIMEOUT_SEC = 10.0
_PROCESS_KILL_TIMEOUT_SEC = 5.0
_RUNTIME_OUTPUT_TOKEN_LIMIT_MAX = 1_000_000
_RUNTIME_REMAINING_MAX_SECONDS = 86_400.0


class LocalRuntimeAbortedError(RuntimeError):
    """An exact local CPU attempt was intentionally stopped by its owner."""

    def __init__(
        self,
        runtime_generation: int,
        attempt_id: int,
        *,
        resources_released: bool,
    ) -> None:
        super().__init__(
            "Local CPU LLM attempt was preempted after a faster execution "
            "path became available"
        )
        self.runtime_generation = int(runtime_generation)
        self.attempt_id = int(attempt_id)
        self.resources_released = bool(resources_released)


def get_cpu_coexistence_defaults() -> dict:
    """Return the public, content-free caps for opportunistic CPU inference."""

    return {
        "execution": CPU_COEXISTENCE_MODE,
        "max_threads": _CPU_COEXISTENCE_MAX_THREADS,
        "max_batch_threads": _CPU_COEXISTENCE_MAX_BATCH_THREADS,
        "batch_size": _CPU_COEXISTENCE_BATCH_SIZE,
        "ubatch_size": _CPU_COEXISTENCE_UBATCH_SIZE,
        "abort_capable": True,
        "preemptible": False,
        "preemption_requires_decision_evidence": True,
        "slots": 1,
    }


def _clear_runtime_remaining_evidence_locked(reason: str) -> None:
    """Invalidate request-bound projection evidence without reading content."""

    global _runtime_output_token_limit, _runtime_observed_output_tokens
    global _runtime_request_pass, _runtime_request_multimodal
    global _runtime_remaining_rate_tps, _runtime_remaining_selection_digest
    global _runtime_remaining_invalid_reason
    _runtime_output_token_limit = None
    _runtime_observed_output_tokens = None
    _runtime_request_pass = 0
    _runtime_request_multimodal = None
    _runtime_remaining_rate_tps = None
    _runtime_remaining_selection_digest = ""
    _runtime_remaining_invalid_reason = str(reason or "unavailable")


def _bind_runtime_request_budget(
    max_output_tokens: int,
    *,
    multimodal: bool,
    request_pass: int,
) -> bool:
    """Bind exact request-budget evidence to the current CPU attempt.

    ``max_output_tokens`` is a protocol ceiling, not a predicted terminal
    length. The resulting projection is therefore diagnostic evidence only
    and is never decision-eligible for preemption.
    """

    global _runtime_output_token_limit, _runtime_observed_output_tokens
    global _runtime_request_pass, _runtime_request_multimodal
    global _runtime_remaining_rate_tps, _runtime_remaining_selection_digest
    global _runtime_remaining_invalid_reason
    token = _current_runtime_attempt_token()
    if (
        token is None
        or isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= _RUNTIME_OUTPUT_TOKEN_LIMIT_MAX
        or isinstance(request_pass, bool)
        or not isinstance(request_pass, int)
        or request_pass < 1
    ):
        return False
    generation, attempt_id = token
    with _runtime_status_lock:
        if (
            _runtime_generation != generation
            or _runtime_active_attempt_id != attempt_id
            or _runtime_execution != CPU_COEXISTENCE_MODE
            or _runtime_phase != "requesting"
        ):
            return False
        rate = None
        if _runtime_timings_multimodal == bool(multimodal):
            rate = _valid_speed_rate(
                _runtime_timings.get("predicted_per_second")
            )
        selection_digest = _runtime_speed_variant_digest if rate else ""
        if not selection_digest:
            rate = None
        _runtime_output_token_limit = max_output_tokens
        _runtime_observed_output_tokens = 0
        _runtime_request_pass = request_pass
        _runtime_request_multimodal = bool(multimodal)
        _runtime_remaining_rate_tps = rate
        _runtime_remaining_selection_digest = selection_digest
        _runtime_remaining_invalid_reason = (
            "terminal_length_unknown"
            if rate is not None else "same_selection_rate_unavailable"
        )
    return True


def _exact_output_tokens(metrics: dict) -> Optional[int]:
    """Read an exact server token count; never estimate it from response text."""

    if not isinstance(metrics, dict):
        return None
    candidates = []
    usage = metrics.get("usage")
    if isinstance(usage, dict):
        candidates.append(usage.get("completion_tokens"))
    timings = metrics.get("timings")
    if isinstance(timings, dict):
        candidates.append(timings.get("predicted_n"))
    exact = [
        value for value in candidates
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    if not exact:
        return None
    if len(set(exact)) > 1:
        return None
    return exact[0]


def _observe_runtime_output_metrics(metrics: dict, *, request_pass: int) -> bool:
    """Publish monotonic exact token progress for the current attempt/pass."""

    global _runtime_observed_output_tokens, _runtime_remaining_invalid_reason
    observed = _exact_output_tokens(metrics)
    token = _current_runtime_attempt_token()
    if observed is None or token is None:
        return False
    generation, attempt_id = token
    with _runtime_status_lock:
        if (
            _runtime_generation != generation
            or _runtime_active_attempt_id != attempt_id
            or _runtime_request_pass != request_pass
            or _runtime_output_token_limit is None
            or observed > _runtime_output_token_limit
        ):
            return False
        current = _runtime_observed_output_tokens
        if current is not None and observed < current:
            return False
        _runtime_observed_output_tokens = observed
        if _runtime_remaining_rate_tps is not None:
            _runtime_remaining_invalid_reason = "terminal_length_unknown"
    return True


def _runtime_remaining_snapshot_locked() -> dict:
    """Return a bounded, content-free projection and its evidence grade."""

    base = {
        "state": "unavailable",
        "reason": _runtime_remaining_invalid_reason,
        "decision_eligible": False,
        "runtime_generation": _runtime_generation or None,
        "attempt_id": _runtime_active_attempt_id or None,
        "request_pass": _runtime_request_pass or None,
        "output_token_limit": _runtime_output_token_limit,
        "observed_output_tokens": _runtime_observed_output_tokens,
        "remaining_budget_tokens": None,
        "measured_tokens_per_second": _runtime_remaining_rate_tps,
        "budget_projection_seconds": None,
    }
    if (
        _runtime_active_attempt_id <= 0
        or _runtime_output_token_limit is None
        or _runtime_observed_output_tokens is None
    ):
        return base
    if (
        _runtime_remaining_rate_tps is None
        or not _runtime_remaining_selection_digest
        or _runtime_remaining_selection_digest != _runtime_speed_variant_digest
    ):
        base["reason"] = "same_selection_rate_unavailable"
        return base
    remaining = max(
        0, _runtime_output_token_limit - _runtime_observed_output_tokens,
    )
    projection = remaining / _runtime_remaining_rate_tps
    if not math.isfinite(projection):
        base["reason"] = "non_finite_projection"
        return base
    base.update({
        "state": "budget_projection",
        "reason": "terminal_length_unknown",
        "remaining_budget_tokens": remaining,
        "budget_projection_seconds": round(
            min(max(projection, 0.0), _RUNTIME_REMAINING_MAX_SECONDS), 3,
        ),
    })
    return base


def _current_runtime_attempt_token() -> Optional[tuple[int, int]]:
    token = getattr(_runtime_request, "token", None)
    if (
        isinstance(token, tuple)
        and len(token) == 2
        and all(isinstance(value, int) for value in token)
    ):
        return token
    return None


def _begin_runtime_attempt() -> Optional[tuple[int, int]]:
    """Enter one re-entrant request attempt for the current local runtime."""

    global _runtime_attempt_counter, _runtime_active_attempt_id
    global _runtime_request_started_at, _runtime_request_finished_at
    global _runtime_abort_requested_at, _runtime_phase
    depth = int(getattr(_runtime_request, "depth", 0) or 0)
    token = _current_runtime_attempt_token()
    if depth > 0 and token is not None:
        _runtime_request.depth = depth + 1
        return token
    with _runtime_status_lock:
        process = _process
        if (
            _provider != "local"
            or process is None
            or process.poll() is not None
            or _runtime_generation <= 0
        ):
            return None
        _runtime_attempt_counter += 1
        attempt_id = _runtime_attempt_counter
        token = (_runtime_generation, attempt_id)
        _runtime_active_attempt_id = attempt_id
        _runtime_request_started_at = time.time()
        _runtime_request_finished_at = None
        _runtime_abort_requested_at = None
        _runtime_phase = "requesting"
        _clear_runtime_remaining_evidence_locked("request_budget_unbound")
    _runtime_request.depth = 1
    _runtime_request.token = token
    return token


def _end_runtime_attempt(token: Optional[tuple[int, int]]) -> None:
    """Finish only the request attempt owned by this worker thread."""

    global _runtime_active_attempt_id, _runtime_request_finished_at
    global _runtime_phase
    if token is None:
        return
    depth = int(getattr(_runtime_request, "depth", 0) or 0)
    if depth > 1:
        _runtime_request.depth = depth - 1
        return
    if hasattr(_runtime_request, "depth"):
        del _runtime_request.depth
    if hasattr(_runtime_request, "token"):
        del _runtime_request.token
    generation, attempt_id = token
    with _runtime_status_lock:
        if (
            _runtime_generation == generation
            and _runtime_active_attempt_id == attempt_id
        ):
            _runtime_active_attempt_id = 0
            _runtime_request_finished_at = time.time()
            if _runtime_phase == "requesting":
                _clear_runtime_remaining_evidence_locked("request_finished")
                _runtime_phase = "ready" if is_loaded() else "idle"


def _activate_request_scope_after_load() -> None:
    """Bind a decorated cold/switching request to its post-load runtime."""

    if int(getattr(_runtime_request, "scope_depth", 0) or 0) <= 0:
        return
    current = _current_runtime_attempt_token()
    with _runtime_status_lock:
        generation = _runtime_generation
        local_ready = (
            _provider == "local"
            and generation > 0
            and _process is not None
            and _process.poll() is None
        )
    if not local_ready or (current is not None and current[0] == generation):
        return
    # A generate_chat model switch invalidates the pre-load token. The old
    # runtime state was already cleared only after its confirmed exit; replace
    # this thread's token without letting its eventual finalizer touch the new
    # generation.
    if hasattr(_runtime_request, "depth"):
        del _runtime_request.depth
    if hasattr(_runtime_request, "token"):
        del _runtime_request.token
    _begin_runtime_attempt()


@contextmanager
def local_runtime_attempt(expected_generation: int):
    """Reserve one exact cooperative-CPU request attempt.

    The context holds Maestro's existing re-entrant model lease. An external
    supervisor can still call :func:`abort_local_cpu_runtime`, which operates
    on the exact captured process rather than waiting for this lease.
    """

    with _lock:
        _begin_model_activity()
        token: Optional[tuple[int, int]] = None
        try:
            with _runtime_status_lock:
                if (
                    _runtime_generation != int(expected_generation)
                    or _provider != "local"
                    or _runtime_execution != CPU_COEXISTENCE_MODE
                    or not is_loaded()
                ):
                    raise RuntimeError(
                        "Expected cooperative CPU LLM runtime is not resident"
                    )
            token = _begin_runtime_attempt()
            if token is None or token[0] != int(expected_generation):
                raise RuntimeError(
                    "Expected cooperative CPU LLM runtime changed before request"
                )
            _cancel_idle_timer()
            yield token[1]
        finally:
            _end_runtime_attempt(token)
            _end_model_activity(_loaded_model_key)


def get_local_runtime_control() -> dict:
    """Return content-free lifecycle state for the local runtime supervisor."""

    with _runtime_status_lock:
        request_started_at = _runtime_request_started_at
        now = time.time()
        remaining = _runtime_remaining_snapshot_locked()
        abort_capable = bool(
            _runtime_execution == CPU_COEXISTENCE_MODE
            and _runtime_active_attempt_id
            and _runtime_phase == "requesting"
        )
        return {
            "generation": _runtime_generation or None,
            "attempt_id": _runtime_active_attempt_id or None,
            "phase": _runtime_phase,
            "execution": _runtime_execution or None,
            "abort_capable": abort_capable,
            "preemptible": bool(remaining["decision_eligible"]),
            "load_started_at": _runtime_load_started_at,
            "load_finished_at": _runtime_load_finished_at,
            "request_started_at": request_started_at,
            "request_finished_at": _runtime_request_finished_at,
            "request_elapsed_seconds": (
                round(max(0.0, now - request_started_at), 3)
                if request_started_at is not None
                and _runtime_active_attempt_id
                else None
            ),
            "remaining_estimate_seconds": (
                remaining["budget_projection_seconds"]
                if remaining["decision_eligible"] else None
            ),
            "remaining": remaining,
            "abort_requested_at": _runtime_abort_requested_at,
            "resources_released": bool(
                _runtime_generation == 0
                and _runtime_last_release.get("resources_released", False)
            ),
            "last_release": dict(_runtime_last_release) or None,
        }


def _begin_model_activity() -> None:
    """Enter a re-entrant activity scope on the current worker thread."""
    _model_activity.depth = getattr(_model_activity, "depth", 0) + 1


def _end_model_activity(identity: tuple) -> bool:
    """Finalize idle expiry once, when the outermost activity exits."""
    depth = getattr(_model_activity, "depth", 0)
    if depth > 1:
        _model_activity.depth = depth - 1
        return False
    if hasattr(_model_activity, "depth"):
        del _model_activity.depth
    return _finish_model_activity(identity)


def _with_model_lease(function):
    """Keep model load/unload changes out of an active inference request."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            _begin_model_activity()
            identity = _loaded_model_key
            prior_scope_depth = int(
                getattr(_runtime_request, "scope_depth", 0) or 0
            )
            _runtime_request.scope_depth = prior_scope_depth + 1
            # generate_chat owns its load/switch operation, so its exact
            # attempt must bind to the post-load generation rather than a
            # previously resident runtime. Other request entry points already
            # require a resident model and can bind immediately.
            runtime_attempt = (
                None if function.__name__ == "generate_chat"
                else _begin_runtime_attempt()
            )
            try:
                return function(*args, **kwargs)
            finally:
                # A cold/switching generate_chat receives its attempt only
                # after load_model commits the new runtime generation.
                terminal_attempt = (
                    _current_runtime_attempt_token() or runtime_attempt
                )
                _end_runtime_attempt(terminal_attempt)
                if prior_scope_depth:
                    _runtime_request.scope_depth = prior_scope_depth
                elif hasattr(_runtime_request, "scope_depth"):
                    del _runtime_request.scope_depth
                # generate_chat may cold-load or intentionally switch the
                # model inside this re-entrant lease. External switches cannot
                # race the lock, so the terminal resident identity is the
                # activity identity in that case. A crash/unload leaves it
                # empty and cannot accidentally re-arm the prior model.
                _end_model_activity(_loaded_model_key or identity)
    return wrapped


def _with_stream_done_finally(function):
    """Publish terminal state only for callers using the legacy stream."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        global _stream_done
        bound = signature.bind_partial(*args, **kwargs)
        request_scoped_progress = callable(
            bound.arguments.get("progress_callback")
        )
        try:
            return function(*args, **kwargs)
        finally:
            if not request_scoped_progress:
                with _stream_lock:
                    _stream_done = True
    return wrapped


def _with_failed_load_cleanup(function):
    """Leave either the prior exact model or a fully unloaded retry state."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        global _loading_model_id
        try:
            return function(*args, **kwargs)
        except Exception:
            with _lock:
                if _loaded_model_key and is_loaded():
                    # Artifact/runtime preparation can fail before the prior
                    # resident model is replaced. Preserve it, but clear the
                    # attempted model's transient status.
                    with _runtime_status_lock:
                        _loading_model_id = ""
                else:
                    # A newly spawned server is not a valid resident until its
                    # exact load key is committed after /health succeeds.
                    _unload_inner()
            raise
    return wrapped


@contextmanager
def loaded_model_lease(
    *,
    model_id: str = "",
    device: str = "cpu",
    force_reload: bool = False,
    provider: str = "local",
    remote_url: str = "",
    api_key: str = "",
    local_gguf_path: str = "",
    gguf_file_override: str = "",
    cpu_coexistence: bool = False,
):
    """Hold one exact loaded-model identity across caller-owned inference.

    This synchronous lease is intended for route/worker code that must keep a
    cold load and a subsequent callback on the same singleton model. Calls to
    :func:`generate` are safe inside the lease because the lock is re-entrant.
    The yielded tuple is the exact resident identity used for finalization.
    ``cpu_coexistence`` forces a bounded, externally abortable local CPU
    runtime without changing the defaults used by ordinary CPU or GPU loads.
    """
    with _lock:
        _begin_model_activity()
        identity = _loaded_model_key
        try:
            load_model(
                model_id=model_id,
                device=device,
                force_reload=force_reload,
                provider=provider,
                remote_url=remote_url,
                api_key=api_key,
                local_gguf_path=local_gguf_path,
                gguf_file_override=gguf_file_override,
                cpu_coexistence=cpu_coexistence,
            )
            identity = _loaded_model_key
            if not identity or not is_loaded():
                raise RuntimeError("LLM did not finish loading")
            _cancel_idle_timer()
            yield identity
        finally:
            _end_model_activity(_loaded_model_key or identity)

# Last call state — for pipeline dashboard capture. The user prompt is
# captured alongside the system prompt so the Director Dashboard can
# render the full LLM input (system + user) for each pass, not just
# the system prompt. Without _last_user_prompt the dashboard's "Pass 1
# System Prompt" view was misleading — users saw only the rules, not
# the actual concept the LLM was being asked to expand into a script.
_last_system_prompt: str = ""
_last_user_prompt: str = ""
_last_thinking_text: str = ""

# Heavy text-only authoring and Director planning use the strongest local
# abliterated model by default. Generic sparkle/pre-generation enhancement is
# lighter rewrite work and uses the faster Qwen 27B model, whose projector also
# makes it safe for image-bearing enhancement. Generation models with their own
# compatible ``prompt_enhancer_model`` still override the generic selection.
DEFAULT_HF_REPO = "MoonRide/gemma-4-31B-it-heretic-ara-GGUF"
DEFAULT_ENHANCE_HF_REPO = (
    "Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF"
)
DEFAULT_GGUF_FILE = "gemma-4-31B-it-heretic-ara-Q4_K_M.gguf"
DEFAULT_MMPROJ_FILE = "mmproj-F16.gguf"
RETIRED_MODEL_IDS = frozenset({
    "Abhiray/gemma-4-E4B-it-heretic-GGUF",
    "Jiunsong/supergemma4-26b-uncensored-gguf-v2",
})


def _migrate_retired_model_id(model_id: str) -> str:
    return DEFAULT_HF_REPO if model_id in RETIRED_MODEL_IDS else model_id


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(_BASE_DIR, "..", "ckpts", "llm")
DEFAULT_BIN_DIR = os.path.join(DEFAULT_CACHE_DIR, "bin")

import re as _re
# Matches all common thinking/reasoning tag patterns used by various models
_THINKING_TAG_RE = _re.compile(
    r"<(?:think|thinking|seed:think|reasoning|reflection)>[\s\S]*?"
    r"</(?:think|thinking|seed:think|reasoning|reflection)>\s*",
    _re.IGNORECASE,
)
_THINKING_TAG_UNCLOSED_RE = _re.compile(
    r"<(?:think|thinking|seed:think|reasoning|reflection)>[\s\S]*$",
    _re.IGNORECASE,
)
# Gemma 4 thinking tags: <|channel>thought\n...<channel|>
_GEMMA_THINKING_RE = _re.compile(
    r"<\|channel>thought\n[\s\S]*?<channel\|>\s*",
)
_GEMMA_THINKING_UNCLOSED_RE = _re.compile(
    r"<\|channel>thought\n[\s\S]*$",
)
# Capture-group variant — pulls the thought content WITHOUT the channel
# markers, so the dashboard can render thinking text cleanly. Used by
# the streaming finalizer; the strip-tags variants above keep their
# original non-capturing form to stay compatible with re.sub.
_GEMMA_THINKING_INNER_RE = _re.compile(
    r"<\|channel>thought\n([\s\S]*?)<channel\|>",
)

def _strip_thinking_tags(text: str) -> str:
    """Strip all known thinking/reasoning tag patterns from LLM output."""
    text = _THINKING_TAG_RE.sub("", text)
    text = _THINKING_TAG_UNCLOSED_RE.sub("", text)
    text = _GEMMA_THINKING_RE.sub("", text)
    text = _GEMMA_THINKING_UNCLOSED_RE.sub("", text)
    return text


def _public_response_text(
    raw_content: str,
    assistant_prefix: str,
    *,
    strip_prefix: bool,
) -> str:
    """Return exactly the response text eligible for publication/detection."""
    return strip_one_prefix(
        _strip_thinking_tags(raw_content),
        assistant_prefix,
        enabled=strip_prefix,
    )


def _inline_thinking_blocks(raw_content: str) -> str:
    """Extract exact inline reasoning blocks for the legacy dashboard only."""
    if not isinstance(raw_content, str) or not raw_content:
        return ""
    matches = []
    for pattern in (
        _THINKING_TAG_RE,
        _THINKING_TAG_UNCLOSED_RE,
        _GEMMA_THINKING_RE,
        _GEMMA_THINKING_UNCLOSED_RE,
    ):
        matches.extend(
            (match.start(), match.end(), match.group(0))
            for match in pattern.finditer(raw_content)
        )
    matches.sort(key=lambda item: (item[0], item[1]))
    blocks = []
    last_end = -1
    for start, end, value in matches:
        if start < last_end:
            continue
        blocks.append(value)
        last_end = end
    return "".join(blocks)

# ─── VRAM estimator ────────────────────────────────────────────────────
# Each registry entry below carries:
#   weights_gb: GB on disk for the .gguf weights (≈ VRAM when loaded)
#   mmproj_gb:  GB for the vision projector (0 if no mmproj)
#   arch:       key into LLM_ARCHITECTURES used to size the KV cache
# Plus optional extra_flags whose -c / --cache-type-{k,v} values are
# parsed for the KV cache. The size_hint string in the dropdown is built
# at module load by _build_size_hint(info) so it always reflects the
# *configured* context — change the -c flag and the hint updates with it.

# Approximate per-architecture parameters used for KV-cache sizing.
# Formula: kv_bytes = 2 * layers * kv_heads * head_dim * ctx * dtype_bytes
# `sliding_window` (if set) caps the per-token cache for "local" layers
# at that window size, while `global_layer_ratio` is the fraction of
# layers that use full context — Gemma 3/4 alternate ~5 local : 1 global.
LLM_ARCHITECTURES = {
    # (layers, kv_heads, head_dim, sliding_window or None, global_layer_ratio)
    "qwen3-2b":   {"layers": 28, "kv_heads": 4,  "head_dim": 128, "sliding_window": None, "global_layer_ratio": 1.0},
    "qwen3-4b":   {"layers": 36, "kv_heads": 8,  "head_dim": 128, "sliding_window": None, "global_layer_ratio": 1.0},
    "qwen3-9b":   {"layers": 36, "kv_heads": 8,  "head_dim": 128, "sliding_window": None, "global_layer_ratio": 1.0},
    "qwen3-27b":  {"layers": 48, "kv_heads": 8,  "head_dim": 128, "sliding_window": None, "global_layer_ratio": 1.0},
    # Gemma 3/4: 5:1 local:global attention pattern, local window 4096.
    # Most layers' KV is bounded by the window regardless of total ctx.
    "gemma4-2b":  {"layers": 26, "kv_heads": 4,  "head_dim": 256, "sliding_window": 4096, "global_layer_ratio": 1/6},
    # Gemma 4 12B "unified" — 48 layers per the model card. kv_heads/head_dim
    # mirror Gemma 3 12B as an approximation; this only feeds the VRAM size hint,
    # not functional loading, so refine if the real config differs.
    "gemma4-12b": {"layers": 48, "kv_heads": 8,  "head_dim": 256, "sliding_window": 4096, "global_layer_ratio": 1/6},
    "gemma4-27b": {"layers": 62, "kv_heads": 16, "head_dim": 128, "sliding_window": 4096, "global_layer_ratio": 1/6},
}


def _kv_dtype_bytes(t: str) -> float:
    """Bytes per KV cache element for llama.cpp cache-type strings."""
    t = (t or "").lower().strip()
    return {
        "f32":  4.0, "fp32": 4.0,
        "f16":  2.0, "fp16": 2.0, "bf16": 2.0,
        "q8_0": 1.0625,
        "q5_0": 0.6875, "q5_1": 0.6875,
        "q4_0": 0.5625, "q4_1": 0.5625,
    }.get(t, 2.0)


def _estimate_kv_gb(arch_key: str, extra_flags: list) -> float:
    """Estimate KV cache size in GB for the given arch + llama.cpp flags.

    Uses a realistic-usage context cap rather than the configured maximum.
    llama.cpp lazy-grows the KV cache as tokens are processed, so a model
    configured with `-c 262144` only allocates that full 13+ GB if a user
    actually fills 256k tokens of conversation. Empirical measurement
    (e.g. Qwen3.6 27B @ 256k → 21.8 GB total) shows real KV usage tracks
    typical session lengths much more closely than the configured max.
    Capping at 64k gives a number that buckets correctly to the GPU tier
    a user actually needs to run the model comfortably.
    """
    arch = LLM_ARCHITECTURES.get(arch_key)
    if not arch:
        return 0.0
    # Defaults match llama.cpp's: ctx=4096, fp16 cache.
    ctx = 4096
    cache_dtype = "f16"
    flags = list(extra_flags or [])
    for i, f in enumerate(flags):
        if f == "-c" and i + 1 < len(flags):
            try:
                ctx = int(flags[i + 1])
            except (TypeError, ValueError):
                pass
        elif f in ("--cache-type-k", "--cache-type-v") and i + 1 < len(flags):
            cache_dtype = flags[i + 1]  # assume k & v match (they do in our configs)
    # Cap at 64k for the estimate. Models configured with bigger contexts
    # (e.g. 256k Qwen) won't actually use that much KV in typical use,
    # and bucketing the estimate to a GPU tier becomes more accurate
    # this way. Bumping a model's -c above 64k won't change the
    # displayed VRAM tier — that's deliberate.
    TYPICAL_USAGE_CTX_CAP = 65536
    effective_ctx = min(ctx, TYPICAL_USAGE_CTX_CAP)
    bpe = _kv_dtype_bytes(cache_dtype)
    layers = arch["layers"]
    kv_heads = arch["kv_heads"]
    head_dim = arch["head_dim"]
    window = arch.get("sliding_window")
    global_ratio = arch.get("global_layer_ratio", 1.0)
    if window and effective_ctx > window and global_ratio < 1.0:
        # Mixed local/global attention (Gemma): apportion KV between
        # local layers (capped at the window) and global layers (full ctx).
        global_layers = max(1, round(layers * global_ratio))
        local_layers = layers - global_layers
        bytes_total = 2 * bpe * kv_heads * head_dim * (
            global_layers * effective_ctx + local_layers * window
        )
    else:
        bytes_total = 2 * layers * kv_heads * head_dim * effective_ctx * bpe
    return bytes_total / (1024 ** 3)


# Standard consumer/workstation GPU VRAM tiers. A model's displayed
# requirement is rounded UP to the smallest tier that comfortably runs
# it. This communicates "what GPU do I need?" much better than a precise
# decimal — a user with a 12 GB card sees "12 GB VRAM" and immediately
# knows it fits, vs. seeing "9.96 GB" and having to do mental math about
# headroom.
#
# Headroom note: the bucket is the recommended *minimum*. Maestro's
# generation pipelines also need VRAM concurrently if you're running an
# LLM during video gen — in that case, pick a card with the LLM's bucket
# size PLUS your video model's footprint, or run the LLM on a remote
# host.
GPU_VRAM_TIERS = (6, 8, 12, 16, 24, 32, 48, 80)


def _bucket_to_tier(gb: float) -> int:
    """Round a measured-or-estimated VRAM total up to the smallest GPU
    tier that fits it. Returns 6 for tiny models, 80 for very large.
    Beyond 80 GB it just rounds up to the next 16 GB step."""
    for tier in GPU_VRAM_TIERS:
        if gb <= tier:
            return tier
    last = GPU_VRAM_TIERS[-1]
    extra = gb - last
    return last + int(((extra + 15.999) // 16) * 16)


def _build_size_hint(info: dict) -> str:
    """Compose the dropdown's '~N GB VRAM' string from registry metadata.

    Bucketed to standard GPU tiers (6 / 8 / 12 / 16 / 24 / 32 / 48 / 80)
    rather than reporting a precise estimate, so users can match their
    hardware at a glance. Falls back to a manual `size_hint` field if
    the entry doesn't carry enough metadata to compute (e.g. legacy
    entries or remote models).
    """
    if "weights_gb" not in info:
        return info.get("size_hint", "")
    weights = float(info.get("weights_gb", 0))
    mmproj = float(info.get("mmproj_gb", 0))
    kv = _estimate_kv_gb(info.get("arch", ""), info.get("extra_flags", []))
    total = weights + mmproj + kv
    return f"{_bucket_to_tier(total)} GB VRAM"


# Model registry — maps HF repo IDs to their GGUF filenames.
# size_hint is built automatically from weights_gb + mmproj_gb + KV-cache
# estimate at module load (see post-loop below).
MODEL_REGISTRY = {
    "unsloth/Qwen3.5-2B-GGUF": {
        "label": "Qwen3.5 2B (Fast)",
        "gguf_file": "Qwen3.5-2B-Q4_K_S.gguf",
        "weights_gb": 1.13, "mmproj_gb": 0.0, "arch": "qwen3-2b",
    },
    "unsloth/Qwen3.5-4B-GGUF": {
        "label": "Qwen3.5 4B (Balanced)",
        "gguf_file": "Qwen3.5-4B-UD-Q4_K_XL.gguf",
        "weights_gb": 2.9, "mmproj_gb": 0.0, "arch": "qwen3-4b",
    },
    "mradermacher/Huihui-Qwen3.5-9B-Claude-4.6-Opus-abliterated-i1-GGUF": {
        "label": "Qwen3.5 9B Claude Opus Abliterated Q6_K",
        "gguf_file": "Huihui-Qwen3.5-9B-Claude-4.6-Opus-abliterated.i1-Q6_K.gguf",
        "mmproj_file": "mmproj-Q8_0.gguf",
        "weights_gb": 6.85, "mmproj_gb": 0.58, "arch": "qwen3-9b",
        "cache_dir_override": "Huihui-Qwen3.5-9B-Claude-4.6-Opus-abliterated",
        "extra_flags": [
            "-c", "65536",
            "-np", "1",
            "-fa", "on",
            "--cache-type-k", "q4_0",
            "--cache-type-v", "q4_0",
        ],
    },
    "Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF": {
        "label": "Qwen3.6 27B Abliterated Heretic (Uncensored, Vision)",
        "gguf_file": "Qwen3.6-27B-Abliterated-Heretic-Uncensored-Q4_K_M.gguf",
        # The Heretic GGUF repo doesn't ship an mmproj file, but the base
        # Qwen3.6-27B vision architecture is preserved in the abliterated
        # weights — so pull the mmproj from the upstream unsloth GGUF repo.
        "mmproj_file": "mmproj-BF16.gguf",
        "mmproj_repo": "unsloth/Qwen3.6-27B-GGUF",
        "weights_gb": 15.4, "mmproj_gb": 0.87, "arch": "qwen3-27b",
        # Qwen3.6 inherits Qwen3.5's 256k native context. Note: full 256k
        # context is the dominant VRAM cost here — that single -c flag
        # alone allocates ~15 GB of KV cache even with q4_0 quantization.
        "extra_flags": [
            "-c", "262144",
            "-np", "1",
            "-fa", "on",
            "--cache-type-k", "q4_0",
            "--cache-type-v", "q4_0",
        ],
    },
    "Nesuwka/gemma-4-E2B-it-heretic-ara-Q4_K_M-GGUF": {
        "label": "Gemma 4 2B Heretic Uncensored (Vision, Tiny)",
        "gguf_file": "model-q4_k_m.gguf",
        "mmproj_file": "mmproj-gemma-4-e2b-it-f16.gguf",
        "mmproj_repo": "ggml-org/gemma-4-E2B-it-GGUF",
        "weights_gb": 3.4, "mmproj_gb": 1.0, "arch": "gemma4-2b",
        "thinking_style": "gemma",
        # Gemma 4 was tuned at temp=1.0; running below that leaves it
        # more deterministic than its sweet spot. frequency/presence
        # penalty=0 because Gemma 4 doesn't have the Qwen 3.x repetition-
        # cascade pathology, and the OpenAI-style penalties dampen
        # reasoning-vocabulary diversity in long thinking — observed
        # empirically as shallow Pass 1 thinking compared to LM Studio
        # output (which ships with no penalty by default).
        "sampling_defaults": {
            "temperature": 1.0, "top_p": 0.95, "top_k": 64,
            "frequency_penalty": 0, "presence_penalty": 0,
        },
    },
    "SulphurAI/Sulphur-2-base": {
        # Sulphur-2's own uncensored prompt enhancer — a ~9.6B multimodal
        # (text+image) llama.cpp model the checkpoint author trained to prompt
        # the Sulphur-2 LTX-2.3 finetune. Used in raw-passthrough mode (no
        # system prompt) by any gen model that declares
        # prompt_enhancer_model: "SulphurAI/Sulphur-2-base". The GGUFs live in
        # the repo's prompt_enhancer_uncensored/ subfolder (hf_hub_download
        # handles the subfolder path). No `arch` key → the KV size hint is
        # skipped (the base arch isn't published); weights+mmproj still count.
        "label": "Sulphur-2 Uncensored Prompt Enhancer (Vision)",
        "gguf_file": "prompt_enhancer_uncensored/prompt_enhancer_uncensored-q8_0.gguf",
        "mmproj_file": "prompt_enhancer_uncensored/mmproj-prompt_enhancer_uncensored.gguf",
        "weights_gb": 9.79, "mmproj_gb": 0.92,
        "sampling_defaults": {
            "temperature": 0.7, "top_p": 0.9, "top_k": 40,
            "frequency_penalty": 0, "presence_penalty": 0,
        },
    },
    "MoonRide/gemma-4-31B-it-heretic-ara-GGUF": {
        "label": "Gemma 4 31B Heretic ARA Q4_K_M (Text)",
        "gguf_file": "gemma-4-31B-it-heretic-ara-Q4_K_M.gguf",
        "mmproj_file": None,
        "weights_gb": 18.7, "mmproj_gb": 0.0, "arch": "gemma4-27b",
        "thinking_style": "gemma",
        "sampling_defaults": {
            "temperature": 1.0, "top_p": 0.95, "top_k": 64,
            "frequency_penalty": 0, "presence_penalty": 0,
        },
        "extra_flags": [
            "-c", "65536",
            "-np", "1",
            "-fa", "on",
            "--cache-type-k", "q4_0",
            "--cache-type-v", "q4_0",
        ],
    },
    "paperscarecrow/Gemma-4-31B-it-abliterated-gguf": {
        # Optional local vision/refinement model. It is exposed under this
        # canonical ID so an installed GGUF is never reduced to an opaque
        # linked-file identity; ordinary chat still keeps the separate
        # MoonRide text model as its configured/default selection.
        "label": "Gemma 4 31B Abliterated Q4_K_M (Vision)",
        "gguf_file": "gemma-4-31b-abliterated-Q4_K_M.gguf",
        "mmproj_file": "mmproj-gemma-4-31B-it-BF16.gguf",
        "mmproj_repo": "ggml-org/gemma-4-31B-it-GGUF",
        "weights_gb": 18.7, "mmproj_gb": 1.2, "arch": "gemma4-27b",
        "thinking_style": "gemma",
        "sampling_defaults": {
            "temperature": 1.0, "top_p": 0.95, "top_k": 64,
            "frequency_penalty": 0, "presence_penalty": 0,
        },
        "extra_flags": [
            "-c", "65536",
            "-np", "1",
            "-fa", "on",
            "--cache-type-k", "q4_0",
            "--cache-type-v", "q4_0",
        ],
    },
    "mradermacher/gemma-4-12B-it-abliterated-uncensored-i1-GGUF": {
        # EXPERIMENTAL — not recommended for Director yet. It loads and runs, but
        # on the structured Director pipeline it under-writes Pass 1 (~4.5k chars
        # vs the 4B's ~10.6k on an identical 5-min prompt) and loops on Pass 2
        # JSON (~96k chars -> failed fallback) even with low temp + strong repeat
        # penalties. Root cause is model/architecture, NOT sampling (three sampling
        # passes did not fix it): gemma4_unified support in llama.cpp is brand-new
        # (PR #24118, 2026-06-04) and this is an abliterated build, which erodes
        # long-form structured coherence. Kept selectable; revisit when llama.cpp's
        # unified support matures. Prefer the registered 31B text authoring model.
        "label": "Gemma 4 12B Abliterated (Text, Experimental)",
        "gguf_file": "gemma-4-12B-it-abliterated-uncensored.i1-Q4_K_M.gguf",
        # Encoder-free "gemma4_unified" architecture: its multimodal projector
        # ships in the new `gemma4uv` format (NOT a standard mmproj-*.gguf), and
        # llama.cpp's unified vision/audio support is brand-new (PR #24118,
        # 2026-06-04). Registered TEXT-ONLY for now (no mmproj).
        # IMPORTANT: requires a llama-server build from AFTER 2026-06-04 — older
        # builds cannot load gemma4_unified at all and will fail at load time.
        "weights_gb": 7.5, "mmproj_gb": 0.0, "arch": "gemma4-12b",
        # Template (verified from the GGUF) honors enable_thinking and activates
        # with <|think|>; Maestro already sends the kwarg + launches with --jinja,
        # so "gemma" (kwarg) activation is correct here — do NOT switch to
        "thinking_style": "gemma",
        # Repeat-loop fix — COMPLEMENT the caller, don't clobber it.
        # registry sampling_defaults OVERRIDE per-call values (see
        # _apply_sampling_defaults), and the Director passes are tuned per pass:
        # Pass 2 (_call_llm_json) NEEDS low temp 0.7 + frequency_penalty 0.3 to
        # keep structured JSON from looping. An earlier version set temperature
        # 1.0 + frequency_penalty 0.1 here, which overrode that and produced a
        # 122K-char JSON repeat loop; freq/presence penalties ALSO shrink
        # Gemma's reasoning depth (shallow thinking on Pass 1). So set ONLY
        # llama-native anti-loop (repeat_penalty + min_p) that adds on top of
        # whatever each pass requests, plus Google's top_p/top_k. Deliberately
        # NO temperature / frequency_penalty / presence_penalty so each pass
        # keeps its own tuned values.
        "sampling_defaults": {
            "top_p": 0.95, "top_k": 64,
            "min_p": 0.05,
            "repeat_penalty": 1.15,
            "repeat_last_n": 256,
        },
    },
}

# Defensive removal keeps retired IDs out of every effective registry lookup.
for _retired_model_id in RETIRED_MODEL_IDS:
    MODEL_REGISTRY.pop(_retired_model_id, None)

# Build size_hint strings once at module load. Re-runs if you `import importlib;
# importlib.reload(llm_service)` after editing the registry.
for _repo_id, _info in MODEL_REGISTRY.items():
    _info["size_hint"] = _build_size_hint(_info)


# ── Curated public model catalog ────────────────────────────────────
# The local models that appear in the LLM picker, in this order. Any other
# MODEL_REGISTRY entry stays loadable by id but is HIDDEN from the dropdown:
# this covers functional-only entries (e.g. the Sulphur-2 dedicated prompt
# enhancer, referenced by gen models via prompt_enhancer_model) and
# deprecated / experimental variants. A repo id listed here that isn't
# currently in the registry is simply skipped.
_PUBLIC_MODEL_ORDER = [
    "Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF",
    "Nesuwka/gemma-4-E2B-it-heretic-ara-Q4_K_M-GGUF",
    "MoonRide/gemma-4-31B-it-heretic-ara-GGUF",                   # Director default
    "paperscarecrow/Gemma-4-31B-it-abliterated-gguf",            # Optional vision reviewer
]

CHAT_MAX_MESSAGES = 64
CHAT_MAX_MESSAGE_CHARS = 32_768
CHAT_MAX_TOTAL_CHARS = 131_072
CHAT_MAX_NEW_TOKENS = 8_192
_DISCOVERED_MODEL_PREFIX = "gguf:"
_DISCOVERY_MAX_ROOTS = 16
_DISCOVERY_MAX_DEPTH = 4
_DISCOVERY_MAX_ENTRIES = 4096
_HF_PART_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# These are IDs, not paths. The browser may request only one of these fixed
# entries; callers can never choose an arbitrary guide-loader category/name.
CHAT_GUIDES = {
    "minimax_h3_ref2va": {
        "label": "MiniMax H3 reference video prompting",
        "category": "enhance", "name": "minimax_h3_ref2va_video",
        "target_mode": "video",
        "target_model_prefixes": ("minimax_h3_ref2va",),
    },
    "minimax_h3": {
        "label": "MiniMax H3 video prompting",
        "category": "enhance", "name": "minimax_h3_video",
        "target_mode": "video",
        "target_model_prefixes": ("minimax_h3",),
    },
    "ltx2_video": {
        "label": "LTX-2 video prompting",
        "category": "enhance", "name": "ltx2_video",
        "target_mode": "video",
        "target_model_prefixes": ("ltx2", "ltxv"),
    },
    "wan_video": {
        "label": "Wan video prompting",
        "category": "enhance", "name": "wan_video",
        "target_mode": "video",
        "target_model_prefixes": (
            "t2v", "i2v", "ti2v", "animate", "wanmove", "ovi", "lucy",
            "multitalk", "phantom", "fun_inp", "alpha", "fantasy",
            "chrono", "flf2v", "hunyuan", "heartmula",
        ),
    },
    "flux_image": {
        "label": "Flux image prompting",
        "category": "enhance", "name": "flux_image",
    },
    "qwen_image": {
        "label": "Qwen image prompting",
        "category": "enhance", "name": "qwen_image_gen",
    },
    "qwen_image_edit": {
        "label": "Qwen image editing prompting",
        "category": "enhance", "name": "qwen_image_edit",
    },
}


def get_chat_guides() -> list[dict]:
    guides = []
    for guide_id, entry in CHAT_GUIDES.items():
        guide = {"id": guide_id, "label": entry["label"]}
        if entry.get("target_mode"):
            guide["target_mode"] = entry["target_mode"]
            guide["target_model_prefixes"] = list(
                entry.get("target_model_prefixes", ())
            )
        guides.append(guide)
    return guides


def load_chat_guides(guide_ids) -> tuple[list[str], str]:
    """Resolve curated chat guide IDs to one server-owned system prompt."""
    if guide_ids is None:
        return [], ""
    if not isinstance(guide_ids, list) or len(guide_ids) > 4:
        raise ValueError("guide_ids must be a list of at most 4 guide IDs")
    selected = []
    blocks = []
    from services.guide_loader import load_guide
    for raw_id in guide_ids:
        if not isinstance(raw_id, str) or raw_id not in CHAT_GUIDES:
            raise ValueError("Unknown chat prompting guide")
        if raw_id in selected:
            continue
        entry = CHAT_GUIDES[raw_id]
        content = load_guide(entry["category"], entry["name"])
        if not content:
            raise RuntimeError(f"Chat prompting guide is unavailable: {raw_id}")
        selected.append(raw_id)
        blocks.append(f"PROMPTING GUIDE: {entry['label']}\n\n{content}")
    return selected, "\n\n---\n\n".join(blocks)


def validate_chat_messages(messages) -> list[dict]:
    """Return a bounded role-preserving user/assistant conversation."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    if len(messages) > CHAT_MAX_MESSAGES:
        raise ValueError(f"messages may contain at most {CHAT_MAX_MESSAGES} entries")
    clean = []
    total = 0
    expected = "user"
    for item in messages:
        if not isinstance(item, dict) or set(item) - {"role", "content"}:
            raise ValueError("Each message may contain only role and content")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or role != expected:
            raise ValueError("Chat messages must alternate user and assistant roles")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Chat message content must be non-empty text")
        if len(content) > CHAT_MAX_MESSAGE_CHARS:
            raise ValueError("A chat message is too large")
        total += len(content)
        if total > CHAT_MAX_TOTAL_CHARS:
            raise ValueError("Chat history is too large")
        clean.append({"role": role, "content": content})
        expected = "assistant" if expected == "user" else "user"
    if clean[-1]["role"] != "user":
        raise ValueError("The final chat message must be from the user")
    return clean


def normalize_hf_model_source(value: str) -> tuple[str, Optional[str]]:
    """Normalize a local-owner HF repo ID or main-branch GGUF URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Invalid Hugging Face model source")
    filename = None
    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise ValueError("Invalid Hugging Face URL") from error
        try:
            explicit_port = parsed.port is not None
        except ValueError as error:
            raise ValueError("Invalid Hugging Face URL") from error
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "huggingface.co"
            or explicit_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in parsed.path
        ):
            raise ValueError("Only exact https://huggingface.co model URLs are allowed")
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if any(
            "\\" in part or any(ord(character) < 32 for character in part)
            for part in parts
        ):
            raise ValueError("Invalid Hugging Face URL path")
        if len(parts) < 2:
            raise ValueError("Hugging Face URL must identify a repository")
        repo_parts = parts[:2]
        if len(parts) > 2:
            if len(parts) < 5 or parts[2] not in {"blob", "resolve"} or parts[3] != "main":
                raise ValueError("Hugging Face file URLs must use the main revision")
            filename = "/".join(parts[4:])
            if not filename.lower().endswith(".gguf"):
                raise ValueError("Hugging Face file URL must identify a GGUF file")
    else:
        repo_parts = value.split("/")
        if len(repo_parts) != 2:
            raise ValueError("Hugging Face model ID must be owner/repository")
    if any(
        not _HF_PART_RE.fullmatch(part) or part in {".", ".."}
        for part in repo_parts
    ):
        raise ValueError("Invalid Hugging Face repository ID")
    if filename:
        path_parts = filename.split("/")
        if any(part in {"", ".", ".."} for part in path_parts):
            raise ValueError("Invalid Hugging Face GGUF filename")
    return "/".join(repo_parts), filename


_PROJECTOR_GENERIC_TOKENS = {
    "mmproj", "model", "projector", "vision", "f16", "bf16", "fp16",
    "q8", "q8_0", "q5", "q5_k", "q4", "q4_k", "q4_k_m", "gguf",
}


def _association_tokens(filename: str) -> set[str]:
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    parts = set(filter(None, _re.split(r"[^a-z0-9]+", stem)))
    return {
        token for token in parts
        if token not in _PROJECTOR_GENERIC_TOKENS
        and not _re.fullmatch(r"(?:q|iq|fp|f|bf)?\d+[a-z0-9]*", token)
    }


def _is_projector_gguf_filename(filename: str) -> bool:
    """Return whether a GGUF basename uses a conventional projector name."""
    basename = os.path.basename(filename).lower()
    if not basename.endswith(".gguf"):
        return False
    stem = os.path.splitext(basename)[0]
    # Preserve the common compact ``mmproj*.gguf`` convention while also
    # recognizing sidecars named after their model, such as
    # ``model-name.mmproj-f16.gguf``.  Token boundaries avoid treating an
    # unrelated model name containing the letters "mmproj" as a projector.
    return stem.startswith("mmproj") or bool(
        _re.search(r"(?:^|[^a-z0-9])(?:mmproj|projector)(?:[^a-z0-9]|$)", stem)
    )


def _find_sibling_mmproj(model_path: str) -> Optional[str]:
    """Resolve a deterministic, contained projector beside a linked GGUF.

    A single conventional ``mmproj*.gguf`` sibling is an unambiguous
    directory-level association.  If several projector quantizations or model
    families share a folder, require a unique best filename-token match.
    Symlinks are intentionally ignored so linked folders cannot escape their
    approved root through a projector sidecar.
    """
    try:
        requested_path = os.path.abspath(model_path)
        if os.path.islink(requested_path):
            return None
        model_path = os.path.realpath(requested_path)
        directory = os.path.dirname(model_path)
        if not os.path.isfile(model_path):
            return None
        candidates = []
        sibling_models = []
        for entry in os.scandir(directory):
            lower = entry.name.lower()
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            if not lower.endswith(".gguf"):
                continue
            resolved = os.path.realpath(entry.path)
            if os.path.commonpath([directory, resolved]) != directory:
                continue
            if _is_projector_gguf_filename(entry.name):
                candidates.append(resolved)
            else:
                sibling_models.append(resolved)
    except (OSError, ValueError):
        return None
    candidates.sort(key=lambda path: os.path.basename(path).casefold())
    if not candidates:
        return None
    model_tokens = _association_tokens(model_path)
    sibling_token_sets = [
        _association_tokens(path) for path in sibling_models if path != model_path
    ]

    def belongs_to_current_model(candidate: str) -> bool:
        if not sibling_token_sets:
            return True
        all_families = {frozenset(model_tokens)} | {
            frozenset(tokens) for tokens in sibling_token_sets
        }
        if len(all_families) == 1:
            # Several quantizations/shards of one model family.
            return True
        projector_tokens = _association_tokens(candidate)
        current_score = len(model_tokens & projector_tokens)
        return current_score > 0 and all(
            current_score > len(tokens & projector_tokens)
            for tokens in sibling_token_sets
        )

    if len(candidates) == 1:
        return candidates[0] if belongs_to_current_model(candidates[0]) else None
    ranked = [
        (len(model_tokens & _association_tokens(candidate)), candidate)
        for candidate in candidates
    ]
    best_score = max(score for score, _candidate in ranked)
    winners = [candidate for score, candidate in ranked if score == best_score]
    if best_score > 0 and len(winners) == 1:
        return winners[0] if belongs_to_current_model(winners[0]) else None
    winner_tokens = {frozenset(_association_tokens(candidate)) for candidate in winners}
    if len(winner_tokens) == 1:
        # Same family, different projector quantizations. Prefer the compact
        # Q8 projector for throughput, then BF16/F16, with filename order as a
        # stable final tiebreaker.
        def preference(path: str):
            name = os.path.basename(path).lower()
            if "q8_0" in name or "q8-0" in name:
                rank = 0
            elif "q8" in name:
                rank = 1
            elif "bf16" in name:
                rank = 2
            elif "f16" in name or "fp16" in name:
                rank = 3
            else:
                rank = 4
            return rank, name
        selected = min(winners, key=preference)
        return selected if belongs_to_current_model(selected) else None
    return None


def get_model_capabilities(
    model_id: str,
    *,
    local_gguf_path: str = "",
    gguf_file_override: str = "",
) -> dict:
    """Return path-free vision/runtime metadata for a catalog model."""
    entry = MODEL_REGISTRY.get(model_id, {})
    projector_path = _find_sibling_mmproj(local_gguf_path) if local_gguf_path else None
    native_vision = bool(entry.get("native_vision", False))
    declared_projector = bool(entry.get("mmproj_file"))
    projector_available = bool(projector_path)
    if declared_projector and not local_gguf_path:
        repo_basename = model_id.split("/")[-1] if "/" in model_id else model_id
        model_stem = repo_basename.replace("-GGUF", "")
        cache_dir = os.path.join(
            get_model_dir(), entry.get("cache_dir_override") or model_stem,
        )
        projector_available = os.path.isfile(
            os.path.join(cache_dir, entry["mmproj_file"])
        )
    vision_capable = native_vision or declared_projector or bool(projector_path)
    return {
        "vision_capable": vision_capable,
        "projector_available": projector_available,
        "native_vision": native_vision,
        "runtime_profile": dict(entry.get("runtime_profile") or {}),
    }


def _discovered_gguf_paths(search_roots) -> dict[str, dict]:
    """Scan explicit roots without following directory or file symlinks."""
    if not isinstance(search_roots, (list, tuple)):
        return {}
    found = {}
    entries_seen = 0
    roots_seen = set()
    for raw_root in list(search_roots)[:_DISCOVERY_MAX_ROOTS]:
        if not isinstance(raw_root, str) or not raw_root.strip():
            continue
        root = os.path.realpath(os.path.abspath(raw_root))
        root_key = os.path.normcase(root)
        if root_key in roots_seen or not os.path.isdir(root):
            continue
        roots_seen.add(root_key)
        stack = [(root, 0)]
        while stack and entries_seen < _DISCOVERY_MAX_ENTRIES:
            current, depth = stack.pop()
            try:
                children = list(os.scandir(current))
            except OSError:
                continue
            for entry in children:
                entries_seen += 1
                if entries_seen > _DISCOVERY_MAX_ENTRIES:
                    break
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < _DISCOVERY_MAX_DEPTH:
                            stack.append((entry.path, depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    lower = entry.name.lower()
                    if (
                        not lower.endswith(".gguf")
                        or _is_projector_gguf_filename(entry.name)
                    ):
                        continue
                    real_path = os.path.realpath(entry.path)
                    if os.path.commonpath([root, real_path]) != root:
                        continue
                    size = entry.stat(follow_symlinks=False).st_size
                    opaque = _DISCOVERED_MODEL_PREFIX + hashlib.sha256(
                        real_path.encode("utf-8", errors="surrogatepass")
                    ).hexdigest()[:24]
                    found[opaque] = {
                        "path": real_path,
                        "label": os.path.splitext(entry.name)[0],
                        "size_bytes": size,
                    }
                except (OSError, ValueError):
                    continue
    for entry in found.values():
        entry["mmproj_path"] = _find_sibling_mmproj(entry["path"])
    return found


def discover_gguf_models(search_roots) -> list[dict]:
    """Return safe public metadata for GGUFs in owner-linked folders."""
    models = []
    for opaque, entry in _discovered_gguf_paths(search_roots).items():
        models.append({
            "id": opaque,
            "label": entry["label"],
            "size_hint": f"{entry['size_bytes'] / 1e9:.1f} GB installed",
            "provider": "local",
            "installed": True,
            "downloaded": True,
            "source": "Linked model folder",
            "vision_capable": bool(entry.get("mmproj_path")),
            "projector_available": bool(entry.get("mmproj_path")),
            "native_vision": False,
        })
    return sorted(models, key=lambda item: item["label"].casefold())


def resolve_discovered_gguf(model_id: str, search_roots) -> Optional[str]:
    entry = _discovered_gguf_paths(search_roots).get(model_id)
    return entry["path"] if entry else None


def get_available_models(provider: str = "local", remote_url: str = "", api_key: str = "") -> list:
    """Return list of available LLM model options for the UI.

    For local provider, returns the curated built-in catalog
    (_PUBLIC_MODEL_ORDER). For remote/openai, queries the server's
    /v1/models endpoint. For anthropic, returns a curated Claude list.
    """
    local_models = [
        {
            "id": repo_id,
            "label": MODEL_REGISTRY[repo_id]["label"],
            "size_hint": MODEL_REGISTRY[repo_id]["size_hint"],
            "provider": "local",
            "source": "Maestro catalog",
            "downloaded": os.path.isfile(os.path.join(
                get_model_dir(),
                MODEL_REGISTRY[repo_id].get("cache_dir_override")
                or repo_id.split("/")[-1].replace("-GGUF", ""),
                MODEL_REGISTRY[repo_id]["gguf_file"],
            )),
            **get_model_capabilities(repo_id),
        }
        for repo_id in _PUBLIC_MODEL_ORDER
        if repo_id in MODEL_REGISTRY
    ]

    remote_models: list[dict] = []

    # Query remote OpenAI-compatible server (LM Studio, etc.)
    if provider in ("remote", "openai") and remote_url:
        try:
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            url = remote_url.rstrip("/")
            resp = requests.get(f"{url}/v1/models", headers=headers, timeout=10)
            if resp.ok:
                data = resp.json()
                for m in data.get("data", []):
                    mid = m.get("id", "")
                    if mid:
                        remote_models.append({
                            "id": mid,
                            "label": f"{mid} (Remote)" if provider == "remote" else f"{mid} (OpenAI)",
                            "size_hint": provider,
                            "provider": provider,
                        })
        except Exception as e:
            print(f"[LLM] Failed to query remote models at {remote_url}: {e}")

    # Anthropic models (curated list — no /models endpoint)
    if provider == "anthropic" and api_key:
        remote_models.extend([
            {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "size_hint": "anthropic", "provider": "anthropic"},
            {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "size_hint": "anthropic", "provider": "anthropic"},
        ])

    return local_models + remote_models


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_model_dir() -> str:
    d = os.environ.get("MAESTRO_LLM_CACHE", DEFAULT_CACHE_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _server_url() -> str:
    if _provider in ("remote", "openai") and _remote_url:
        return _remote_url.rstrip("/")
    return f"http://127.0.0.1:{_server_port}"


def _api_headers() -> dict:
    """Build headers for API calls (adds auth for remote providers)."""
    headers = {"Content-Type": "application/json"}
    if _provider in ("remote", "openai", "anthropic") and _api_key:
        if _provider == "anthropic":
            headers["x-api-key"] = _api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {_api_key}"
    return headers


def _active_registry_entry() -> dict:
    """Return the MODEL_REGISTRY entry for the currently loaded model, or {}."""
    return MODEL_REGISTRY.get(_model_id, {})


def _apply_model_defaults(temperature: float, top_p: float, payload: dict) -> tuple[float, float]:
    """Apply per-model sampling defaults from the registry.

    Registry values WIN over caller values for any field the registry
    specifies. Rationale: caller values are pass-level heuristics (e.g.
    Pass 1 sends temperature=0.8 + frequency_penalty=0.15 because that
    works well for Qwen and is the historical default) but registry
    values are model-tuned (Gemma 4 was tuned at 1.0; the freq penalty
    that protects Qwen 3.x from repetition cascades shrinks Gemma's
    reasoning-vocabulary diversity and produces shallow thinking output).

    Models with no `sampling_defaults` entry pass through unchanged —
    caller's values stay. So adding registry tuning for one model never
    affects others.

    Returns adjusted (temperature, top_p) and mutates payload with
    top_k / frequency_penalty / presence_penalty when present.
    """
    entry = _active_registry_entry()
    defaults = entry.get("sampling_defaults", {})
    if not defaults:
        return temperature, top_p
    if "temperature" in defaults:
        temperature = defaults["temperature"]
    if "top_p" in defaults:
        top_p = defaults["top_p"]
    if "top_k" in defaults:
        payload["top_k"] = defaults["top_k"]
    # Penalty overrides — `0` is a valid value (means "explicitly off")
    # so we use `in defaults` rather than truthiness checks here. Caller's
    # frequency_penalty / presence_penalty (set later in generate_streaming)
    # need to be cleared if registry says off; otherwise they'd stick.
    if "frequency_penalty" in defaults:
        fp = defaults["frequency_penalty"]
        if fp > 0:
            payload["frequency_penalty"] = fp
        else:
            payload.pop("frequency_penalty", None)
    if "presence_penalty" in defaults:
        pp = defaults["presence_penalty"]
        if pp > 0:
            payload["presence_penalty"] = pp
        else:
            payload.pop("presence_penalty", None)
    # llama.cpp-native sampling parameters (forwarded by llama-server's
    # OpenAI-compatible endpoint as request extensions). These are NOT
    # standard OpenAI fields but llama-server passes them through to the
    # sampler. Necessary for models that need llama.cpp-native repetition
    # control rather than the weaker OpenAI-style presence/frequency
    # penalties — e.g. MoE Gemma fine-tunes that LM Studio handles via
    # its default `repeat_penalty: 1.1` + `min_p: 0.05` config.
    if "repeat_penalty" in defaults:
        rp = defaults["repeat_penalty"]
        if rp and rp != 1.0:
            payload["repeat_penalty"] = rp
        else:
            payload.pop("repeat_penalty", None)
    if "repeat_last_n" in defaults:
        # Width of the recent-tokens window that repeat_penalty applies
        # to. Default in llama.cpp is 64. Wider windows protect against
        # word-spaced repetition (where the model emits "X foo X bar X
        # baz" — within 64 tokens the repeated X stays inside the window
        # and gets penalized; widen to 256+ for protection against
        # paragraph-scale repetition).
        rln = defaults["repeat_last_n"]
        if rln and rln > 0:
            payload["repeat_last_n"] = rln
        else:
            payload.pop("repeat_last_n", None)
    if "min_p" in defaults:
        mp = defaults["min_p"]
        if mp and mp > 0:
            payload["min_p"] = mp
        else:
            payload.pop("min_p", None)
    return temperature, top_p


def _apply_request_sampling(
    payload: dict,
    temperature: float,
    top_p: float,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
) -> tuple[float, float]:
    """Merge caller sampling options and model defaults in one stable order."""
    if frequency_penalty > 0:
        payload["frequency_penalty"] = frequency_penalty
    if presence_penalty > 0:
        payload["presence_penalty"] = presence_penalty
    temperature, top_p = _apply_model_defaults(temperature, top_p, payload)
    payload["temperature"] = max(temperature, 0.01)
    payload["top_p"] = top_p
    return temperature, top_p


def _build_user_content(prompt: str, image_paths) -> tuple[object, bool]:
    """Encode authorized images or fail closed before constructing a request."""
    if not image_paths:
        return prompt, False
    if (
        isinstance(image_paths, (str, bytes))
        or not isinstance(image_paths, Sequence)
        or len(image_paths) > 8
        or any(not isinstance(path, str) or not path for path in image_paths)
    ):
        raise ValueError("image_paths must be a list of at most 8 image files")
    if not _vision_available:
        raise ValueError("The selected LLM has no available vision projector")
    content_parts = []
    for image_path in image_paths:
        data_url = _image_to_data_url(image_path)
        if not data_url:
            raise ValueError("An authorized image is unavailable")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })
    content_parts.append({"type": "text", "text": prompt})
    return content_parts, True


def _prepare_thinking(system_prompt: str, enable_thinking: Optional[bool], thinking_budget: int) -> tuple[str, Optional[bool], int]:
    """Handle model-specific thinking mode activation.

    Gemma 4 (incl. Heretic / abliterated fine-tunes): activate thinking
    by passing `enable_thinking=True` via `chat_template_kwargs`. The
    chat template embedded in the Heretic GGUF emits the literal
    `<|think|>` directive at the top of the system turn when this kwarg
    is true (verified by inspecting tokenizer.chat_template — it has an
    explicit `{%- if enable_thinking -%}{{- '<|think|>' -}}{%- endif -%}`
    block). Once activated, the model emits its reasoning inline as
    `<|channel>thought\\n...<channel|>` and switches to its actual
    answer after the closing marker.

    Do not inject `<|think|>` into the system message text when the chat
    template already emits it. Manual injection is redundant and risks
    double-tokenization.

    Qwen 3.x: same `enable_thinking` kwarg path. The Qwen chat template
    inserts `<think>` automatically when the kwarg is true.

    Returns (system_prompt, enable_thinking, thinking_budget).
    """
    entry = _active_registry_entry()
    # Force-off wins over everything else (caller's explicit value AND any
    # thinking_style setting). Used for models that auto-activate thinking
    # mode regardless of chat_template_kwargs — Gemma 4 fine-tunes are the
    # known offender. The model burns the entire max_new_tokens budget on
    # internal reasoning, stuffs the reasoning into `reasoning_content`,
    # and returns empty `content`. The result is a successful HTTP 200
    # with no useful output for the pipeline. Set `disable_thinking: True`
    # in the registry entry to suppress this — propagates as
    # enable_thinking=False to the chat template kwargs, and the
    # companion stop-tokens injection in generate() / generate_streaming()
    # catches any chat templates that ignore the kwarg.
    if entry.get("disable_thinking", False):
        return system_prompt, False, 0
    style = entry.get("thinking_style", "qwen")
    if style == "gemma":
        # Honor explicit opt-out from caller.
        if enable_thinking is False:
            return system_prompt, False, 0
        if thinking_budget <= 0:
            thinking_budget = 2048
        enable_thinking = True
        # Strip any leftover literal `<|think|>` prefix from previous
        # versions of this function — the chat template will inject the
        # real special-tokenized version. Leaving the literal text in
        # would result in the prefix appearing twice (once tokenized, once
        # as plain characters), which can confuse the model.
        stripped = system_prompt.lstrip()
        if stripped.startswith("<|think|>"):
            system_prompt = stripped[len("<|think|>"):].lstrip("\n")
    return system_prompt, enable_thinking, thinking_budget


def is_loaded() -> bool:
    if _provider in ("remote", "openai", "anthropic"):
        return bool(_model_id)
    return _process is not None and _process.poll() is None


def get_status() -> dict:
    with _download_state_lock:
        download = dict(_download_state)
    if download and str(download.get("phase") or "downloading") == "downloading":
        try:
            from services import safe_download
            basename = str(download.get("filename") or "")
            tracked = safe_download.get_active_downloads()
            match = next(
                (
                    item for item in tracked
                    if basename and (
                        os.path.basename(str(item.get("filename") or "")) == basename
                    )
                ),
                None,
            )
            if match:
                download.update({
                    "downloaded_bytes": int(match.get("downloaded_bytes") or 0),
                    "total_bytes": match.get("total_bytes"),
                    "seconds_since_progress": match.get("seconds_since_progress"),
                })
        except Exception:
            pass
    with _runtime_status_lock:
        loaded = is_loaded()
        runtime_snapshot = {
            "model_id": _model_id,
            "provider": _provider,
            "backend": _runtime_backend,
            "requested_device": _requested_device,
            "timings": dict(_runtime_timings),
            "multimodal": _runtime_timings_multimodal,
        }
        status_snapshot = {
            "loaded": loaded,
            "model_id": _model_id or None,
            "device": _device if loaded else None,
            "requested_device": _requested_device or None,
            "provider": _provider,
            "vision_available": bool(loaded and _vision_available),
            "backend": _runtime_backend or None,
            "runtime_build": _runtime_build,
            "runtime_devices": list(_runtime_devices),
            "runtime_profile": dict(_runtime_profile),
            "runtime_timings": dict(_runtime_timings),
            "fallback_reason": _runtime_fallback_reason or None,
            "loading_model_id": _loading_model_id,
        }
    runtime_control = get_local_runtime_control()
    runtime_ready = bool(
        status_snapshot["loaded"]
        and isinstance(runtime_control, dict)
        and runtime_control.get("phase") == "ready"
    )
    active_loading_model_id = (
        None if runtime_ready else status_snapshot["loading_model_id"]
    )
    return {
        "loaded": status_snapshot["loaded"],
        "model_id": status_snapshot["model_id"],
        "device": status_snapshot["device"],
        "requested_device": status_snapshot["requested_device"],
        "provider": status_snapshot["provider"],
        "vision_available": status_snapshot["vision_available"],
        "backend": status_snapshot["backend"],
        "runtime": {
            "backend": status_snapshot["backend"],
            "build": status_snapshot["runtime_build"],
            "devices": status_snapshot["runtime_devices"],
            "effective_profile": status_snapshot["runtime_profile"],
            "timings": status_snapshot["runtime_timings"],
            "speed": _current_runtime_speed(runtime_snapshot),
            "fallback_reason": status_snapshot["fallback_reason"],
            "control": runtime_control,
        },
        "loading": bool(download or active_loading_model_id),
        "loading_model_id": (
            active_loading_model_id
            or (download.get("model_id") if download else None)
        ),
        "loading_phase": (
            str(download.get("phase") or "downloading")
            if download
            else ("loading model" if active_loading_model_id else None)
        ),
        "download": download or None,
    }


def vision_available() -> bool:
    """Return whether the loaded Director model will actually receive images."""
    return is_loaded() and bool(_vision_available)


def _record_response_metrics(data, *, multimodal: bool = False) -> None:
    """Store only numeric, path-free llama.cpp timing/usage information."""
    global _runtime_timings, _runtime_timings_multimodal
    if not isinstance(data, dict):
        return
    metrics = {}
    timings = data.get("timings")
    if isinstance(timings, dict):
        allowed = {
            "prompt_n", "prompt_ms", "prompt_per_second", "predicted_n",
            "predicted_ms", "predicted_per_second",
        }
        for key in allowed:
            value = timings.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[key] = value
    usage = data.get("usage")
    if isinstance(usage, dict):
        for source, target in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[target] = value
    persist_metrics = None
    persist_multimodal = bool(multimodal)
    if metrics:
        # Some compatible servers return token usage on a later/final event
        # without repeating their timing block. Retain the newest actual rates
        # instead of replacing them with a usage-only payload.
        with _runtime_status_lock:
            new_rate_keys = {
                rate_key
                for rate_key in ("prompt_per_second", "predicted_per_second")
                if rate_key in metrics
            }
            has_new_rate = bool(new_rate_keys)
            may_retain_rate = (
                not has_new_rate
                or _runtime_timings_multimodal == bool(multimodal)
            )
            for rate_key in ("prompt_per_second", "predicted_per_second"):
                if rate_key not in metrics:
                    previous_rate = _valid_speed_rate(
                        _runtime_timings.get(rate_key)
                    )
                    if may_retain_rate and previous_rate is not None:
                        metrics[rate_key] = previous_rate
            if has_new_rate:
                _runtime_timings_multimodal = bool(multimodal)
            _runtime_timings = metrics
            if _provider == "local" and new_rate_keys:
                persist_metrics = dict(metrics), set(new_rate_keys)
    if persist_metrics is not None:
        persisted_values, observed_keys = persist_metrics
        _persist_speed_observation(
            persisted_values,
            multimodal=persist_multimodal,
            observed_rate_keys=observed_keys,
        )


def _valid_speed_rate(value) -> Optional[float]:
    """Return a bounded positive throughput value, or ``None``."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or not 0.01 <= value <= 100_000:
        return None
    return value


def _speed_observation_path() -> str:
    """Private content-free calibration store; never returned by an API."""
    return os.path.join(get_model_dir(), ".runtime_speed_v2.json")


class _SpeedObservationFileLock:
    """Bounded cross-process lock for the shared calibration sidecar."""

    def __init__(self):
        self._handle = None

    def __enter__(self):
        lock_path = _speed_observation_path() + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        self._handle = open(lock_path, "a+b")
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        try:
            deadline = time.monotonic() + 30
            if os.name == "nt":
                import msvcrt

                if os.path.getsize(lock_path) == 0:
                    self._handle.write(b"\0")
                    self._handle.flush()
                self._handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(
                            self._handle.fileno(), msvcrt.LK_NBLCK, 1,
                        )
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "timed out locking LLM speed observations"
                            )
                        time.sleep(0.05)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(
                            self._handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "timed out locking LLM speed observations"
                            )
                        time.sleep(0.05)
        except Exception:
            self._handle.close()
            self._handle = None
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._handle is None:
            return False
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
        return False


def _speed_hardware_identity(backend: str) -> tuple[str, dict]:
    """Hash a coarse hardware class so persisted records expose no device name."""
    import json
    import platform

    cached = _speed_hardware_identity_cache.get(backend)
    if cached is not None:
        return cached[0], dict(cached[1])

    profile = _hardware_profile(probe_gpu=backend == "cuda")
    coarse = {
        "backend": backend,
        "physical_threads": int(profile.get("physical_threads") or 0),
        "logical_threads": int(profile.get("logical_threads") or 0),
        "gpu_vram_gb": round(float(profile.get("gpu_vram_gb") or 0), 1),
    }
    identity = dict(coarse)
    identity["machine"] = platform.machine()
    cpu_model = platform.processor()
    if os.path.isfile("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.lower().startswith("model name") and ":" in line:
                        cpu_model = line.split(":", 1)[1].strip()[:160]
                        break
        except OSError:
            pass
    identity["cpu_model"] = cpu_model
    if backend == "cuda":
        visible_devices = []
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in (result.stdout or "").splitlines():
                    parts = [part.strip() for part in line.split(",", 4)]
                    if len(parts) != 5:
                        continue
                    index, uuid, name, memory_mib, driver = parts
                    if _cuda_device_is_visible(index, uuid):
                        visible_devices.append({
                            "uuid": uuid,
                            "name": name,
                            "memory_mib": memory_mib,
                            "driver": driver,
                        })
        except (OSError, subprocess.SubprocessError):
            pass
        identity["visible_devices"] = visible_devices
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    result = hashlib.sha256(encoded.encode("utf-8")).hexdigest(), coarse
    _speed_hardware_identity_cache[backend] = result
    return result[0], dict(result[1])


def _speed_model_digest(model_id: str) -> str:
    return hashlib.sha256(str(model_id or "").encode("utf-8")).hexdigest()


def _speed_variant_digest(
    model_id: str,
    *,
    local_gguf_path: str = "",
    gguf_file_override: str = "",
    device: str = "auto",
    effective_profile_override: Optional[dict] = None,
) -> str:
    """Hash quant/projector/runtime inputs without persisting their names."""
    import json

    entry = MODEL_REGISTRY.get(model_id, {})
    model_file = (
        gguf_file_override
        or entry.get("gguf_file")
        or (os.path.basename(local_gguf_path) if local_gguf_path else "")
    )
    model_path = local_gguf_path
    if not model_path and model_file:
        repo_basename = model_id.split("/")[-1] if "/" in model_id else model_id
        model_stem = repo_basename.replace("-GGUF", "")
        cache_dir = os.path.join(
            get_model_dir(), entry.get("cache_dir_override") or model_stem,
        )
        candidate = os.path.join(cache_dir, model_file)
        if os.path.isfile(candidate):
            model_path = candidate
    projector_file = entry.get("mmproj_file")
    projector_path = None
    if model_path:
        projector_path = _find_sibling_mmproj(model_path)
        if projector_path:
            projector_file = os.path.basename(projector_path)
    if not projector_path and projector_file and model_path:
        candidate = os.path.join(os.path.dirname(model_path), projector_file)
        if os.path.isfile(candidate):
            projector_path = candidate
    if not projector_path and model_id not in MODEL_REGISTRY:
        projector_file = DEFAULT_MMPROJ_FILE
    runtime_bin = os.environ.get("MAESTRO_LLAMA_BIN", DEFAULT_BIN_DIR)
    server_path = os.path.join(
        runtime_bin, "llama-server.exe" if os.name == "nt" else "llama-server",
    )
    effective_profile = dict(effective_profile_override or {})
    normalized_device = "cuda" if str(device).lower() == "cuda" else "cpu"
    if model_path and not effective_profile:
        try:
            effective_profile = _runtime_profile_for(
                model_path,
                projector_path,
                normalized_device,
                {"backend": normalized_device},
                entry,
            )
        except (OSError, TypeError, ValueError):
            effective_profile = {}
    material = {
        "model_file": model_file,
        "model_identity": _safe_file_identity(model_path),
        "projector_file": projector_file,
        "projector_identity": _safe_file_identity(projector_path),
        "server_identity": _safe_file_identity(server_path),
        "extra_flags": list(entry.get("extra_flags") or []),
        "runtime_profile": dict(entry.get("runtime_profile") or {}),
        "effective_profile": effective_profile,
        "disable_jinja": bool(entry.get("disable_jinja", False)),
        "runtime_version": globals().get("LLAMA_SERVER_VERSION", ""),
        "device": str(device or "auto").lower(),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _speed_observation_key(
    model_id: str,
    backend: str,
    hardware_digest: str,
    variant_digest: str,
    multimodal: bool,
) -> str:
    material = "\0".join((
        _speed_model_digest(model_id), backend, hardware_digest, variant_digest,
        "vision" if multimodal else "text",
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _load_speed_observations_locked() -> dict:
    """Load and validate the bounded observation map while holding its lock."""
    import json

    global _speed_observation_cache, _speed_observation_cache_identity
    path = _speed_observation_path()
    file_identity = _safe_file_identity(path)
    if (
        _speed_observation_cache is not None
        and (
            _speed_observation_cache_identity is None
            or _speed_observation_cache_identity == file_identity
        )
    ):
        return _speed_observation_cache
    observations = {}
    try:
        if os.path.getsize(path) > 128 * 1024:
            raise ValueError("speed observation file exceeds its size bound")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("invalid speed observation root")
        if payload.get("version") != _SPEED_OBSERVATION_VERSION:
            raise ValueError("unsupported speed observation version")
        rows = payload.get("observations")
        if not isinstance(rows, list):
            raise ValueError("invalid speed observations")
        for row in rows[:_MAX_SPEED_OBSERVATIONS]:
            if not isinstance(row, dict):
                continue
            key = row.get("key")
            backend = row.get("backend")
            if (
                not isinstance(key, str) or len(key) != 64
                or backend not in {"cpu", "cuda"}
                or not isinstance(row.get("hardware"), str)
                or len(row["hardware"]) != 64
                or not isinstance(row.get("model"), str)
                or len(row["model"]) != 64
                or not isinstance(row.get("variant"), str)
                or len(row["variant"]) != 64
            ):
                continue
            prompt_rate = _valid_speed_rate(row.get("prompt_tps"))
            generation_rate = _valid_speed_rate(row.get("generation_tps"))
            if prompt_rate is None and generation_rate is None:
                continue
            observations[key] = {
                "key": key,
                "model": row["model"],
                "variant": row["variant"],
                "hardware": row["hardware"],
                "backend": backend,
                "multimodal": bool(row.get("multimodal", False)),
                "model_gb": max(0.1, min(float(row.get("model_gb") or 4), 500)),
                "prompt_tps": prompt_rate,
                "generation_tps": generation_rate,
                "prompt_samples": max(
                    0, min(int(row.get("prompt_samples") or 0), 10_000)
                ),
                "generation_samples": max(
                    0, min(int(row.get("generation_samples") or 0), 10_000)
                ),
                "updated": max(0, int(row.get("updated") or 0)),
            }
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        observations = {}
    _speed_observation_cache = observations
    _speed_observation_cache_identity = _safe_file_identity(path)
    return observations


def _save_speed_observations_locked(observations: dict) -> None:
    """Atomically persist only hashes and numeric calibration observations."""
    import json
    import tempfile

    global _speed_observation_cache_identity

    path = _speed_observation_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    rows = sorted(
        observations.values(), key=lambda row: int(row.get("updated") or 0),
        reverse=True,
    )[:_MAX_SPEED_OBSERVATIONS]
    payload = {
        "version": _SPEED_OBSERVATION_VERSION,
        "observations": rows,
    }
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".runtime-speed-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        temporary = ""
        _speed_observation_cache_identity = _safe_file_identity(path)
    finally:
        if temporary:
            try:
                os.remove(temporary)
            except OSError:
                pass


def _catalog_model_size_gb(
    model_id: str,
    local_gguf_path: str = "",
    runtime_snapshot: Optional[dict] = None,
) -> tuple[float, bool]:
    """Return a path-free model-size estimate and whether it is authoritative."""
    if local_gguf_path:
        try:
            return max(os.path.getsize(local_gguf_path) / (1024 ** 3), 0.1), True
        except OSError:
            pass
    if runtime_snapshot is None:
        with _runtime_status_lock:
            runtime_snapshot = {
                "model_id": _model_id,
                "model_size_gb": _runtime_model_size_gb,
            }
    if (
        model_id == runtime_snapshot.get("model_id")
        and float(runtime_snapshot.get("model_size_gb") or 0) > 0
    ):
        return float(runtime_snapshot["model_size_gb"]), True
    entry = MODEL_REGISTRY.get(model_id, {})
    try:
        weights = float(entry.get("weights_gb") or 0)
    except (TypeError, ValueError):
        weights = 0
    if weights > 0:
        return weights, True
    return 4.0, False


def _persist_speed_observation(
    metrics: dict,
    *,
    multimodal: bool,
    observed_rate_keys: Optional[set[str]] = None,
) -> None:
    """Refine an EMA using content-free llama.cpp timing measurements."""
    global _speed_observation_cache, _speed_observation_cache_identity
    if not _model_id or _runtime_backend not in {"cpu", "cuda"}:
        return
    observed_rate_keys = observed_rate_keys or {
        "prompt_per_second", "predicted_per_second",
    }
    prompt_rate = (
        _valid_speed_rate(metrics.get("prompt_per_second"))
        if "prompt_per_second" in observed_rate_keys else None
    )
    generation_rate = (
        _valid_speed_rate(metrics.get("predicted_per_second"))
        if "predicted_per_second" in observed_rate_keys else None
    )
    if prompt_rate is None and generation_rate is None:
        return
    hardware_digest, _hardware = _speed_hardware_identity(_runtime_backend)
    variant_digest = (
        _runtime_speed_variant_digest
        or _speed_variant_digest(_model_id, device=_runtime_backend)
    )
    key = _speed_observation_key(
        _model_id, _runtime_backend, hardware_digest, variant_digest, multimodal,
    )
    model_gb, _known_size = _catalog_model_size_gb(_model_id)
    with _speed_observation_lock:
        try:
            with _SpeedObservationFileLock():
                # Another Maestro process may have committed observations
                # since our last read. Reload under the interprocess lock,
                # merge this sample, then atomically replace the sidecar.
                _speed_observation_cache = None
                _speed_observation_cache_identity = None
                observations = _load_speed_observations_locked()
                previous = observations.get(key, {})
                prompt_samples = int(previous.get("prompt_samples") or 0)
                generation_samples = int(
                    previous.get("generation_samples") or 0
                )

                def blended(
                    field: str, latest: Optional[float],
                ) -> Optional[float]:
                    old = _valid_speed_rate(previous.get(field))
                    if latest is None:
                        return old
                    if old is None:
                        return round(latest, 4)
                    alpha = 0.35
                    return round((old * (1 - alpha)) + (latest * alpha), 4)

                observations[key] = {
                    "key": key,
                    "model": _speed_model_digest(_model_id),
                    "variant": variant_digest,
                    "hardware": hardware_digest,
                    "backend": _runtime_backend,
                    "multimodal": bool(multimodal),
                    "model_gb": round(model_gb, 3),
                    "prompt_tps": blended("prompt_tps", prompt_rate),
                    "generation_tps": blended(
                        "generation_tps", generation_rate,
                    ),
                    "prompt_samples": min(
                        prompt_samples + (1 if prompt_rate is not None else 0),
                        10_000,
                    ),
                    "generation_samples": min(
                        generation_samples
                        + (1 if generation_rate is not None else 0),
                        10_000,
                    ),
                    "updated": int(time.time()),
                }
                if len(observations) > _MAX_SPEED_OBSERVATIONS:
                    oldest = sorted(
                        observations,
                        key=lambda item: int(
                            observations[item].get("updated") or 0
                        ),
                    )[:len(observations) - _MAX_SPEED_OBSERVATIONS]
                    for old_key in oldest:
                        observations.pop(old_key, None)
                _save_speed_observations_locked(observations)
        except OSError:
            # Measurements are useful but must never fail a completed response.
            logger.debug("Unable to persist LLM speed calibration", exc_info=True)


def _speed_result(
    prompt_rate,
    generation_rate,
    *,
    source: str,
    confidence: str,
    reason: str,
    samples: int,
    backend: str,
) -> dict:
    prompt_rate = _valid_speed_rate(prompt_rate)
    generation_rate = _valid_speed_rate(generation_rate)
    return {
        "prompt_tokens_per_second": (
            round(prompt_rate, 1) if prompt_rate is not None else None
        ),
        "generation_tokens_per_second": (
            round(generation_rate, 1) if generation_rate is not None else None
        ),
        "source": source,
        "confidence": confidence,
        "reason": reason,
        "sample_count": max(0, int(samples or 0)),
        "backend": backend or None,
    }


def _heuristic_speed_estimate(model_gb: float, backend: str) -> tuple[float, float]:
    """Conservative fallback until this hardware has real observations."""
    hardware = _hardware_profile(probe_gpu=backend == "cuda")
    model_gb = max(float(model_gb or 4), 0.1)
    if backend == "cuda":
        vram_gb = float(hardware.get("gpu_vram_gb") or 0)
        generation = 600.0 / model_gb
        if vram_gb <= 0:
            generation *= 0.18
        elif model_gb > vram_gb * 0.75:
            fit = max((vram_gb * 0.75) / model_gb, 0.1)
            generation *= max(0.12, fit * fit)
        generation = max(0.5, min(generation, 160.0))
        prompt = min(generation * 3.2, 1200.0)
    else:
        physical = max(int(hardware.get("physical_threads") or 2), 2)
        generation = (physical * 2.8) / (model_gb ** 0.75)
        generation = max(0.4, min(generation, 40.0))
        prompt = min(generation * 2.4, 300.0)
    return prompt, generation


def _observation_sample_count(row: dict) -> int:
    counts = []
    if _valid_speed_rate(row.get("prompt_tps")) is not None:
        counts.append(int(row.get("prompt_samples") or 0))
    if _valid_speed_rate(row.get("generation_tps")) is not None:
        counts.append(int(row.get("generation_samples") or 0))
    return min(counts) if counts else 0


def get_model_speed_estimate(
    model_id: str,
    *,
    local_gguf_path: str = "",
    gguf_file_override: str = "",
    device: str = "auto",
    multimodal: bool = False,
    use_current_measurement: bool = True,
) -> dict:
    """Return path-free measured or calibrated throughput for one catalog item."""
    with _runtime_status_lock:
        current = {
            "model_id": _model_id,
            "backend": _runtime_backend,
            "requested_device": _requested_device,
            "timings": dict(_runtime_timings),
            "multimodal": _runtime_timings_multimodal,
            "variant": _runtime_speed_variant_digest,
            "model_size_gb": _runtime_model_size_gb,
        }
    requested = str(device or "auto").lower()
    if requested not in {"auto", "cpu", "cuda"}:
        requested = "auto"
    if requested == "auto":
        if model_id == current["model_id"] and current["backend"] in {"cpu", "cuda"}:
            backend = current["backend"]
        elif current["requested_device"] in {"cpu", "cuda"}:
            backend = current["requested_device"]
        else:
            backend = (
                "cuda"
                if _hardware_profile().get("gpu_vram_gb", 0) > 0
                else "cpu"
            )
    else:
        backend = requested

    variant_digest = _speed_variant_digest(
        model_id,
        local_gguf_path=local_gguf_path,
        gguf_file_override=gguf_file_override,
        device=backend,
    )
    if (
        use_current_measurement
        and model_id == current["model_id"]
        and backend == current["backend"]
        and bool(multimodal) == bool(current["multimodal"])
        and variant_digest == current["variant"]
    ):
        prompt_rate = _valid_speed_rate(current["timings"].get("prompt_per_second"))
        generation_rate = _valid_speed_rate(
            current["timings"].get("predicted_per_second")
        )
        if prompt_rate is not None or generation_rate is not None:
            return _speed_result(
                prompt_rate, generation_rate,
                source="measured", confidence="measured",
                reason="Latest completed request on this model and backend",
                samples=1, backend=backend,
            )

    hardware_digest, _hardware = _speed_hardware_identity(backend)
    key = _speed_observation_key(
        model_id, backend, hardware_digest, variant_digest, multimodal,
    )
    model_gb, known_size = _catalog_model_size_gb(
        model_id, local_gguf_path, runtime_snapshot=current,
    )
    with _speed_observation_lock:
        observations = dict(_load_speed_observations_locked())
    exact = observations.get(key)
    if exact:
        sample_count = _observation_sample_count(exact)
        confidence = "high" if sample_count >= 3 else "medium"
        return _speed_result(
            exact.get("prompt_tps"), exact.get("generation_tps"),
            source="calibrated", confidence=confidence,
            reason=(
                "Repeated measurements for this model on this hardware"
                if sample_count >= 3
                else "Early measurement for this model on this hardware"
            ),
            samples=sample_count, backend=backend,
        )

    comparable = [
        row for row in observations.values()
        if row.get("hardware") == hardware_digest
        and row.get("backend") == backend
        and bool(row.get("multimodal")) == bool(multimodal)
    ]
    if comparable and known_size:
        import math

        source_row = min(
            comparable,
            key=lambda row: abs(
                math.log(max(float(row.get("model_gb") or 4), 0.1) / model_gb)
            ),
        )
        scale = max(
            0.12,
            min((float(source_row.get("model_gb") or 4) / model_gb) ** 0.92, 8.0),
        )
        prompt_rate = _valid_speed_rate(source_row.get("prompt_tps"))
        generation_rate = _valid_speed_rate(source_row.get("generation_tps"))
        return _speed_result(
            prompt_rate * scale if prompt_rate is not None else None,
            generation_rate * scale if generation_rate is not None else None,
            source="calibrated", confidence="medium",
            reason="Scaled from measurements on this hardware and backend",
            samples=_observation_sample_count(source_row), backend=backend,
        )

    prompt_rate, generation_rate = _heuristic_speed_estimate(model_gb, backend)
    return _speed_result(
        prompt_rate, generation_rate,
        source="heuristic", confidence="low",
        reason="Conservative hardware and model-size estimate; no comparable measurement yet",
        samples=0, backend=backend,
    )


def _current_runtime_speed(snapshot: Optional[dict] = None) -> dict:
    snapshot = dict(snapshot or {})
    model_id = snapshot.get("model_id", _model_id)
    provider = snapshot.get("provider", _provider)
    backend = snapshot.get("backend", _runtime_backend)
    requested_device = snapshot.get("requested_device", _requested_device)
    timings = dict(snapshot.get("timings", _runtime_timings))
    multimodal = bool(snapshot.get("multimodal", _runtime_timings_multimodal))
    if model_id:
        prompt_rate = _valid_speed_rate(timings.get("prompt_per_second"))
        generation_rate = _valid_speed_rate(timings.get("predicted_per_second"))
        if provider == "local":
            if prompt_rate is not None or generation_rate is not None:
                return _speed_result(
                    prompt_rate, generation_rate,
                    source="measured", confidence="measured",
                    reason="Latest completed request on this model and backend",
                    samples=1, backend=backend,
                )
            return get_model_speed_estimate(
                model_id,
                device=backend or requested_device or "auto",
                multimodal=multimodal,
                use_current_measurement=False,
            )
        if prompt_rate is not None or generation_rate is not None:
            return _speed_result(
                prompt_rate, generation_rate,
                source="measured", confidence="measured",
                reason="Latest completed provider request",
                samples=1, backend=provider,
            )
        return _speed_result(
            None, None, source="unavailable", confidence="unavailable",
            reason="Provider has not returned throughput timings",
            samples=0, backend=provider,
        )
    return _speed_result(
        None, None, source="unavailable", confidence="unavailable",
        reason="No model is loaded", samples=0, backend="",
    )


def _download_gguf(repo_id: str, filename: str, cache_dir: str) -> str:
    """Download a GGUF file from HuggingFace and return the local path."""
    local_path = os.path.join(cache_dir, filename)
    if os.path.isfile(local_path):
        print(f"[LLM] GGUF file already cached: {local_path}")
        return local_path

    print(f"[LLM] Downloading {filename} from {repo_id}...")
    from huggingface_hub import hf_hub_download
    with _download_state_lock:
        _download_state.update({
            "model_id": repo_id,
            "filename": os.path.basename(filename),
            "downloaded_bytes": 0,
            "total_bytes": None,
        })
    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=cache_dir,
        )
    finally:
        with _download_state_lock:
            _download_state.clear()
    print(f"[LLM] Downloaded to: {downloaded}")
    return downloaded


# Minimum llama.cpp build Maestro requires. Builds below this lack correct
# support for newer model architectures we ship — notably Qwen3.5's hybrid
# attention/SSM arch ("qwen35") used by the Sulphur prompt enhancer, which
# crashes on older builds with: "error loading model: missing tensor
# 'blk.N.ssm_conv1d.weight'". Verified b9632 loads it; b9048 does not. Bump
# this (and FALLBACK_TAG below) when a newer model needs a newer runtime.
MIN_LLAMA_BUILD = 9632
# Linux CUDA releases are not published as ready-to-run artifacts.  Pin this
# source tag so a CUDA runtime is reproducible and can be audited independently
# of the moving llama.cpp default branch.
LLAMA_SERVER_VERSION = "b10289"
LLAMA_SERVER_BUILD = 10289
_CUDA_BUILD_ATTEMPTED = False


def _safe_file_identity(path: Optional[str]) -> tuple:
    """Return a reload identity that changes when a local artifact is replaced."""
    if not path:
        return ()
    try:
        link_stat = os.lstat(path)
        target_stat = os.stat(path)
        return (
            int(link_stat.st_dev), int(link_stat.st_ino), int(link_stat.st_size),
            int(link_stat.st_mtime_ns), int(link_stat.st_ctime_ns),
            int(target_stat.st_dev), int(target_stat.st_ino), int(target_stat.st_size),
            int(target_stat.st_mtime_ns), int(target_stat.st_ctime_ns),
        )
    except OSError:
        return ()


def _runtime_launch_identity(
    server_exe: str,
    extra_flags: Sequence,
    disable_jinja: bool,
) -> tuple:
    """Return command inputs not otherwise represented by the runtime profile."""
    return (
        _safe_file_identity(server_exe),
        tuple(str(flag) for flag in (extra_flags or ())),
        bool(disable_jinja),
    )


def _discover_nvcc() -> Optional[str]:
    """Find a usable CUDA compiler, including Pinokio's managed Miniforge."""
    import json
    import shutil

    global _nvcc_path_cache
    if (
        _nvcc_path_cache
        and os.path.isfile(_nvcc_path_cache)
        and os.access(_nvcc_path_cache, os.X_OK)
    ):
        return _nvcc_path_cache

    candidates = []
    for env_name in ("CUDACXX", "CUDA_HOME", "CUDA_PATH"):
        value = os.environ.get(env_name, "")
        if not value:
            continue
        candidates.append(
            value if os.path.basename(value) == "nvcc" else os.path.join(value, "bin", "nvcc")
        )
    resolved = shutil.which("nvcc")
    if resolved:
        candidates.append(resolved)
    try:
        with open(os.path.expanduser("~/.pinokio/config.json"), encoding="utf-8") as handle:
            pinokio_home = json.load(handle).get("home", "")
        if pinokio_home:
            candidates.append(os.path.join(pinokio_home, "bin", "miniforge", "bin", "nvcc"))
    except (OSError, ValueError, TypeError):
        pass
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(os.path.join(conda_prefix, "bin", "nvcc"))

    seen = set()
    usable: list[tuple[tuple[int, int], str]] = []
    for candidate in candidates:
        candidate = os.path.realpath(os.path.abspath(candidate))
        if candidate in seen or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 or "cuda" not in output.lower():
            continue
        match = _re.search(r"release\s+(\d+)\.(\d+)", output, _re.IGNORECASE)
        version = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
        usable.append((version, candidate))
    if not usable:
        return None
    # Prefer the newest compiler. This matters on machines where an older
    # system toolkit appears before Pinokio's managed toolkit; CUDA 12.0, for
    # example, cannot target the RTX 50-series compute capability while the
    # managed CUDA 12.8 compiler can.
    _, selected = max(usable, key=lambda item: (item[0], item[1]))
    _nvcc_path_cache = selected
    return selected


def _cuda_process_env(
    nvcc_path: Optional[str], runtime_bin_dir: Optional[str] = None,
) -> dict:
    """Return an environment that can find a managed CUDA toolkit at runtime."""
    env = os.environ.copy()
    if not nvcc_path:
        return env
    toolkit_root = os.path.dirname(os.path.dirname(os.path.realpath(nvcc_path)))
    bin_dir = os.path.join(toolkit_root, "bin")
    library_dirs = [
        os.path.realpath(runtime_bin_dir or DEFAULT_BIN_DIR),
        os.path.join(toolkit_root, "lib"),
        os.path.join(toolkit_root, "lib64"),
        os.path.join(toolkit_root, "targets", "x86_64-linux", "lib"),
    ]
    # A managed nvcc is paired with its compatible host compiler/sysroot. Keep
    # that toolchain together; the build configuration adds the host driver
    # library's rpath-link explicitly for Miniforge's isolated linker.
    env["PATH"] = os.pathsep.join([bin_dir, env.get("PATH", "")])
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [directory for directory in library_dirs if os.path.isdir(directory)]
        + ([existing] if existing else [])
    )
    env["CUDACXX"] = os.path.realpath(nvcc_path)
    env["CUDA_HOME"] = toolkit_root
    env["CUDA_PATH"] = toolkit_root
    return env


def _cuda_visible_tokens() -> Optional[set[str]]:
    raw = os.environ.get("CUDA_VISIBLE_DEVICES")
    if raw is None:
        return None
    return {token.strip() for token in raw.split(",") if token.strip()}


def _cuda_device_is_visible(index: str, uuid: str) -> bool:
    visible = _cuda_visible_tokens()
    if visible is None:
        return True
    if not visible or visible == {"-1"}:
        return False
    return any(
        token == index or uuid == token or uuid.startswith(token)
        for token in visible
    )


def _cuda_architecture() -> Optional[str]:
    """Return CMake architectures for every GPU visible to this process."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi", "--query-gpu=index,uuid,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        architectures = set()
        for line in (result.stdout or "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                continue
            index, uuid, capability = parts
            if not _cuda_device_is_visible(index, uuid):
                continue
            if _re.fullmatch(r"\d+\.\d+", capability):
                architectures.add(int(capability.replace(".", "")))
        if architectures:
            return ";".join(str(value) for value in sorted(architectures))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _llama_server_capabilities(exe_path: str, env: Optional[dict] = None) -> dict:
    """Probe the installed executable and report its actual compute backend."""
    if env is None:
        build, runnable = _llama_server_probe(exe_path)
    else:
        build, runnable = _llama_server_probe(exe_path, env=env)
    devices: list[str] = []
    backend = "unavailable"
    if runnable:
        backend = "cpu"
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [exe_path, "--list-devices"], capture_output=True, text=True,
                timeout=30, env=env, **kwargs,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            in_device_list = False
            for line in output.splitlines():
                clean = line.strip()
                if clean.lower() == "available devices:":
                    in_device_list = True
                    continue
                if not in_device_list or not clean or clean.lower() == "(none)":
                    continue
                if _re.search(r"\b(?:cuda|nvidia)\b", clean, _re.IGNORECASE):
                    # Device labels are hardware metadata, not filesystem paths.
                    devices.append(clean[:160])
            if result.returncode == 0 and devices:
                backend = "cuda"
        except Exception:
            pass
    return {
        "build": build,
        "runnable": runnable,
        "backend": backend,
        "devices": devices,
    }


def _copy_cuda_runtime(build_bin: str, staging_dir: str) -> None:
    """Copy only llama-server and its sibling shared libraries to staging."""
    import shutil

    os.makedirs(staging_dir, exist_ok=True)
    copied_server = False
    for name in os.listdir(build_bin):
        source = os.path.join(build_bin, name)
        if name == "llama-server" or name.startswith("lib") and ".so" in name:
            target = os.path.join(staging_dir, name)
            if os.path.islink(source):
                os.symlink(os.readlink(source), target)
            elif os.path.isfile(source):
                shutil.copy2(source, target)
            copied_server = copied_server or name == "llama-server"
    if not copied_server:
        raise FileNotFoundError("CUDA build completed without llama-server")
    os.chmod(os.path.join(staging_dir, "llama-server"), 0o755)
    _repair_linux_soname_links(staging_dir)


def _atomic_install_runtime(staging_dir: str, bin_dir: str) -> None:
    """Swap a fully probed staged runtime into place, restoring on failure."""
    import shutil
    import uuid

    parent = os.path.dirname(os.path.abspath(bin_dir))
    os.makedirs(parent, exist_ok=True)
    backup = os.path.join(parent, f".{os.path.basename(bin_dir)}.backup-{uuid.uuid4().hex}")
    had_existing = os.path.lexists(bin_dir)
    installed = False
    try:
        if had_existing:
            os.replace(bin_dir, backup)
        os.replace(staging_dir, bin_dir)
        installed = True
    except Exception:
        if os.path.lexists(bin_dir):
            shutil.rmtree(bin_dir, ignore_errors=True)
        if had_existing and os.path.lexists(backup):
            os.replace(backup, bin_dir)
        raise
    finally:
        if installed and os.path.lexists(backup):
            shutil.rmtree(backup, ignore_errors=True)


def _build_linux_cuda_runtime(bin_dir: str, nvcc_path: str) -> dict:
    """Build the pinned official llama.cpp source with CUDA, then install it."""
    import shutil
    import sys
    import tempfile

    parent = os.path.dirname(os.path.abspath(bin_dir))
    os.makedirs(parent, exist_ok=True)
    env = _cuda_process_env(nvcc_path)
    with tempfile.TemporaryDirectory(prefix="maestro-llama-src-") as source_tmp:
        source_dir = os.path.join(source_tmp, "llama.cpp")
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--branch", LLAMA_SERVER_VERSION,
                "https://github.com/ggml-org/llama.cpp.git", source_dir,
            ],
            check=True, capture_output=True, text=True, timeout=300, env=env,
        )
        build_dir = os.path.join(source_dir, "build")
        configure = [
            "cmake", "-S", source_dir, "-B", build_dir,
            "-DGGML_CUDA=ON", "-DGGML_NATIVE=ON", "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_BUILD_UI=OFF",
            f"-DCMAKE_CUDA_COMPILER={os.path.realpath(nvcc_path)}",
            # A depth-1 tag checkout cannot derive the historical commit count,
            # so llama.cpp otherwise reports build 1 even for tag b10289.
            f"-DLLAMA_BUILD_NUMBER={LLAMA_SERVER_BUILD}",
        ]
        architecture = _cuda_architecture()
        if architecture:
            configure.append(f"-DCMAKE_CUDA_ARCHITECTURES={architecture}")
        if sys.platform.startswith("linux"):
            host_driver_dirs = [
                directory for directory in (
                    "/lib/x86_64-linux-gnu",
                    "/usr/lib/x86_64-linux-gnu",
                )
                if os.path.isfile(os.path.join(directory, "libcuda.so.1"))
            ]
            if host_driver_dirs:
                link_flags = " ".join(
                    f"-Wl,-rpath-link,{directory} -L{directory}"
                    for directory in host_driver_dirs
                )
                configure.extend([
                    f"-DCMAKE_EXE_LINKER_FLAGS={link_flags}",
                    f"-DCMAKE_SHARED_LINKER_FLAGS={link_flags}",
                ])
        subprocess.run(
            configure, check=True, capture_output=True, text=True, timeout=300, env=env,
        )
        subprocess.run(
            [
                "cmake", "--build", build_dir, "--config", "Release",
                "--target", "llama-server", "--parallel", str(min(os.cpu_count() or 2, 16)),
            ],
            check=True, capture_output=True, text=True, timeout=1800, env=env,
        )
        staging_dir = tempfile.mkdtemp(prefix=".llama-runtime-stage-", dir=parent)
        try:
            _copy_cuda_runtime(os.path.join(build_dir, "bin"), staging_dir)
            capabilities = _llama_server_capabilities(
                os.path.join(staging_dir, "llama-server"),
                env=_cuda_process_env(nvcc_path, staging_dir),
            )
            if (
                not capabilities["runnable"]
                or capabilities["backend"] != "cuda"
                or capabilities["build"] is None
                or capabilities["build"] < LLAMA_SERVER_BUILD
            ):
                raise RuntimeError("built llama-server failed the CUDA/version probe")
            _atomic_install_runtime(staging_dir, bin_dir)
            staging_dir = ""
            return capabilities
        finally:
            if staging_dir:
                shutil.rmtree(staging_dir, ignore_errors=True)


def _llama_server_probe(exe_path: str, env: Optional[dict] = None):
    """Return ``(build, runnable)`` for an installed llama-server.

    A successful process with an unfamiliar version string is runnable and
    should be retained. Exit 127 is different: on Linux the dynamic loader
    uses it when a required shared-library link is missing, so treating that
    result as merely "unparseable" leaves a broken runtime installed forever.
    """
    try:
        import subprocess
        import re
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        out = subprocess.run(
            [exe_path, "--version"], capture_output=True, text=True, timeout=20,
            env=env, **kwargs,
        )
        m = re.search(r"version:\s*(\d+)", (out.stdout or "") + (out.stderr or ""))
        if m:
            return int(m.group(1)), out.returncode == 0
        return None, out.returncode == 0
    except Exception:
        return None, False


def _repair_linux_soname_links(bin_dir: str) -> list:
    """Recreate missing SONAME links for flattened llama.cpp Linux releases.

    Release tarballs store links such as ``libllama.so.0`` alongside files
    such as ``libllama.so.0.0.10289``. Older Maestro extraction copied only
    regular files, so existing installs can contain every library payload but
    still fail at process startup. Infer the stable major-version SONAME and
    point it at the newest matching versioned file without replacing a valid
    file or link.
    """
    if not os.path.isdir(bin_dir):
        return []

    candidates = {}
    for name in os.listdir(bin_dir):
        match = _re.match(r"^(lib.+\.so)\.(\d+)(?:\..+)+$", name)
        path = os.path.join(bin_dir, name)
        if match and os.path.isfile(path) and not os.path.islink(path):
            soname = f"{match.group(1)}.{match.group(2)}"
            candidates.setdefault(soname, []).append(name)

    def _version_key(name: str):
        suffix = name.split(".so.", 1)[1]
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in suffix.split(".")
        )

    repaired = []
    for soname, names in candidates.items():
        link_path = os.path.join(bin_dir, soname)
        if os.path.lexists(link_path):
            if not (os.path.islink(link_path) and not os.path.exists(link_path)):
                continue
            os.unlink(link_path)
        target_name = max(names, key=_version_key)
        os.symlink(target_name, link_path)
        repaired.append(soname)
    return repaired


def _extract_linux_tar(tar, bin_dir: str) -> None:
    """Flatten a llama.cpp Linux tarball while preserving safe symlinks."""
    import posixpath
    import shutil

    members = tar.getmembers()
    archive_members = {
        posixpath.normpath(member.name).lstrip("./"): member
        for member in members
    }

    # Extract payloads first so restored links never point at a target that
    # was skipped simply because it appeared later in the archive.
    for member in members:
        if not member.isfile():
            continue
        flat_name = posixpath.basename(member.name)
        if not flat_name:
            continue
        target = os.path.join(bin_dir, flat_name)
        src = tar.extractfile(member)
        if src is None:
            continue
        with src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            os.chmod(target, member.mode)
        except OSError:
            pass

    for member in members:
        if not member.issym() or posixpath.isabs(member.linkname):
            continue
        member_name = posixpath.normpath(member.name).lstrip("./")
        resolved_target = posixpath.normpath(
            posixpath.join(posixpath.dirname(member_name), member.linkname)
        )
        if resolved_target == ".." or resolved_target.startswith("../"):
            continue
        if resolved_target not in archive_members:
            continue
        flat_name = posixpath.basename(member_name)
        flat_target = posixpath.basename(resolved_target)
        if not flat_name or not flat_target or flat_name == flat_target:
            continue
        link_path = os.path.join(bin_dir, flat_name)
        if os.path.lexists(link_path):
            os.unlink(link_path)
        os.symlink(flat_target, link_path)

    _repair_linux_soname_links(bin_dir)


def _ensure_llama_server(bin_dir: str, requested_device: str = "cpu") -> dict:
    """Auto-download llama-server from llama.cpp GitHub releases if missing.

    Picks the appropriate prebuilt binary for the current platform:
      - Windows + CUDA (default for Maestro on NVIDIA): bin-win-cuda-12.4-x64.zip
      - Linux CUDA: pinned official source build via ``GGML_CUDA=ON``
      - Linux CPU fallback: bin-ubuntu-x64.tar.gz
      - macOS / AMD: not supported by Maestro itself (Pinokio install gates these),
        but if someone gets here, raise with a clear message.

    Uses urllib + zipfile/tarfile from the stdlib so no extra deps needed.
    Queries the GitHub releases API for the latest tag rather than
    hardcoding a version that goes stale within a week. Falls back to a
    known-good pinned tag if the API is unreachable (rate-limited,
    offline, etc.) so this still works on locked-down networks.

    Side effect: writes binaries to bin_dir/. Idempotent — exits early
    if a new-enough exe already exists; re-downloads the latest if the
    installed build is older than MIN_LLAMA_BUILD.
    """
    import sys
    import json
    import zipfile
    import tarfile
    import shutil
    import tempfile
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError

    global _CUDA_BUILD_ATTEMPTED, _runtime_fallback_reason

    requested_device = str(requested_device or "cpu").lower()
    if requested_device != "cuda":
        _runtime_fallback_reason = ""
    is_windows = sys.platform.startswith("win")
    is_linux = sys.platform.startswith("linux")
    exe_name = "llama-server.exe" if is_windows else "llama-server"
    exe_path = os.path.join(bin_dir, exe_name)

    # Linux release assets are CPU-only.  A CUDA request therefore has one
    # reproducible path: build our pinned official source tag with the managed
    # toolkit, stage and probe it, then atomically replace the old runtime.
    if is_linux and requested_device == "cuda":
        if os.path.isfile(exe_path):
            _repair_linux_soname_links(bin_dir)
            installed = _llama_server_capabilities(
                exe_path, env=_cuda_process_env(_discover_nvcc()),
            )
            if (
                installed["runnable"]
                and installed["backend"] == "cuda"
                and installed["build"] is not None
                and installed["build"] >= LLAMA_SERVER_BUILD
            ):
                _runtime_fallback_reason = ""
                return installed
        if not _CUDA_BUILD_ATTEMPTED:
            _CUDA_BUILD_ATTEMPTED = True
            nvcc_path = _discover_nvcc()
            try:
                if not nvcc_path:
                    raise RuntimeError("no compatible CUDA compiler was found")
                print(
                    f"[LLM] Building llama-server {LLAMA_SERVER_VERSION} with CUDA "
                    "on this host..."
                )
                with _download_state_lock:
                    _download_state.update({
                        "model_id": _loading_model_id or "llama.cpp",
                        "filename": f"llama-server {LLAMA_SERVER_VERSION} CUDA",
                        "phase": "building_runtime",
                        "downloaded_bytes": 0,
                        "total_bytes": None,
                    })
                try:
                    built = _build_linux_cuda_runtime(bin_dir, nvcc_path)
                finally:
                    with _download_state_lock:
                        if _download_state.get("phase") == "building_runtime":
                            _download_state.clear()
                _runtime_fallback_reason = ""
                return built
            except Exception as error:
                # One bounded attempt per Maestro process.  Keep any previously
                # runnable CPU install intact and be explicit about the fallback.
                _runtime_fallback_reason = (
                    f"CUDA runtime build failed; using CPU: {type(error).__name__}"
                )
                print(f"[LLM] {_runtime_fallback_reason}")
        else:
            _runtime_fallback_reason = (
                _runtime_fallback_reason
                or "CUDA runtime unavailable; using CPU"
            )

    if os.path.isfile(exe_path):
        if is_linux:
            repaired = _repair_linux_soname_links(bin_dir)
            if repaired:
                print(f"[LLM] Repaired llama.cpp library links: {', '.join(repaired)}")
        build, runnable = _llama_server_probe(exe_path)
        # Keep the existing binary if it's new enough — or if its version is
        # unparseable but runnable (don't risk a re-download loop on an unknown
        # build). A loader failure/exit 127 is unusable and must be replaced.
        if runnable and (build is None or build >= MIN_LLAMA_BUILD):
            return _llama_server_capabilities(exe_path)
        if runnable:
            print(f"[LLM] llama-server build {build} < required {MIN_LLAMA_BUILD}; "
                  "upgrading to the latest llama.cpp release.")
        else:
            print("[LLM] Installed llama-server is not runnable; repairing from "
                  "the latest llama.cpp release.")
        # fall through to re-download (extractall below overwrites in place)

    if not (is_windows or is_linux):
        raise RuntimeError(
            f"Auto-download of llama-server is supported on Windows and Linux only. "
            f"Detected platform: {sys.platform}. Download manually from "
            "https://github.com/ggml-org/llama.cpp/releases and place llama-server "
            f"in {bin_dir}."
        )

    os.makedirs(bin_dir, exist_ok=True)

    # Asset selection. cu12.4 build chosen because it's broadly compatible
    # with CUDA 12.x and 13.x drivers (forward compat within major).
    #
    # Windows: download TWO assets:
    #   1. llama-b<tag>-bin-win-cuda-12.4-x64.zip — the actual binaries
    #   2. cudart-llama-bin-win-cuda-12.4-x64.zip — CUDA runtime DLLs
    #      (cudart64_12.dll, cublas64_12.dll, etc). Required because
    #      llama-server needs them at runtime and we can't assume the
    #      user has system-wide CUDA — Pinokio's AI bundle installs it
    #      but a manual install or weird env may not have it on PATH.
    #      Putting them next to llama-server.exe lets it find them
    #      regardless of system state.
    # Linux: the official ubuntu asset is CPU-only and is used only for CPU
    # requests or as a truthful fallback after the bounded CUDA build fails.
    #
    # Both assets are matched by:
    #   (must_start_with, must_contain)
    # The startswith check disambiguates "llama-b..." from "cudart-..."
    # (both contain "bin-win-cuda-12.4-x64.zip" and we must download
    # the right one — and on Windows, both).
    if is_windows:
        asset_specs = [
            ("llama-",  "bin-win-cuda-12.4-x64.zip"),
            ("cudart-", "bin-win-cuda-12.4-x64.zip"),
        ]
        archive_ext = ".zip"
    else:  # linux
        asset_specs = [
            ("llama-", "bin-ubuntu-x64.tar.gz"),
        ]
        archive_ext = ".tar.gz"

    # Query GitHub for the latest release. If the API call fails (rate
    # limit, offline), fall back to a pinned known-good tag so we still
    # have a chance of downloading. Update the fallback tag occasionally
    # if a critical fix lands in newer builds.
    FALLBACK_TAG = "b9632"
    print("[LLM] llama-server not found, fetching llama.cpp latest release info...")
    release_info = None
    tag = None
    try:
        req = Request(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urlopen(req, timeout=15) as r:
            release_info = json.load(r)
        tag = release_info.get("tag_name", FALLBACK_TAG)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[LLM] GitHub API unavailable ({e}); falling back to pinned tag {FALLBACK_TAG}")
        tag = FALLBACK_TAG

    # Resolve each asset spec to a download URL — prefer GitHub API
    # (handles tag drift gracefully) but fall back to a constructed URL.
    asset_urls = []
    api_assets = (release_info or {}).get("assets", [])
    for prefix, contains in asset_specs:
        url = None
        for asset in api_assets:
            name = asset.get("name", "")
            if name.startswith(prefix) and contains in name:
                url = asset.get("browser_download_url")
                break
        if not url:
            # Construct conventional URL — works as long as the asset
            # naming convention is stable across releases.
            if prefix == "llama-":
                guess_name = f"{prefix}{tag}-{contains}"
            else:
                # cudart asset name doesn't include the tag (verified
                # via the API listing — cudart-llama-bin-win-cuda-12.4-x64.zip).
                guess_name = f"{prefix}llama-{contains}"
            url = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{guess_name}"
        asset_urls.append(url)

    # Windows needs two archives (server + CUDA DLLs).  Assemble both in one
    # private staging directory so a failed second download/extract cannot
    # leave the active runtime containing a mixture of old and new files.
    windows_stage = None
    runtime_extract_dir = bin_dir
    if is_windows:
        parent = os.path.dirname(os.path.abspath(bin_dir))
        windows_stage = tempfile.TemporaryDirectory(
            prefix=".llama-runtime-stage-", dir=parent,
        )
        runtime_extract_dir = windows_stage.name

    # Download + extract each asset in turn.
    for asset_url in asset_urls:
        print(f"[LLM] Downloading {os.path.basename(asset_url)} on this host (may take 1-2 min)...")
        archive_path = os.path.join(
            runtime_extract_dir, f"_llama_download_temp{archive_ext}",
        )
        with _download_state_lock:
            _download_state.update({
                "model_id": _loading_model_id or "llama.cpp",
                "filename": os.path.basename(asset_url),
                "phase": "downloading_runtime",
                "downloaded_bytes": 0,
                "total_bytes": None,
            })
        try:
            # Stream download so the user isn't waiting on full buffering
            with urlopen(asset_url, timeout=600) as r, open(archive_path, "wb") as f:
                total_bytes = int(r.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1 MB chunks
                last_pct = -10
                while True:
                    chunk = r.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    with _download_state_lock:
                        _download_state["downloaded_bytes"] = downloaded
                        _download_state["total_bytes"] = total_bytes or None
                    if total_bytes:
                        pct = (downloaded * 100) // total_bytes
                        if pct - last_pct >= 10:
                            print(f"[LLM]   {pct}% ({downloaded // (1024*1024)} / {total_bytes // (1024*1024)} MB)")
                            last_pct = pct
            print("[LLM] Extracting...")

            # Extract — Windows zips and Linux tarballs have different layouts.
            # llama.cpp's win zips put binaries in a top-level "build/bin/"
            # or similar subdirectory; flatten everything to bin_dir for
            # simplicity (llama-server expects siblings of itself, not
            # a nested layout).
            if archive_ext == ".zip":
                with zipfile.ZipFile(archive_path) as z:
                    for member in z.infolist():
                        if member.is_dir():
                            continue
                        flat_name = os.path.basename(member.filename)
                        if not flat_name:
                            continue
                        target = os.path.join(runtime_extract_dir, flat_name)
                        with z.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            else:  # tar.gz
                parent = os.path.dirname(os.path.abspath(bin_dir))
                staging_dir = tempfile.mkdtemp(
                    prefix=".llama-runtime-stage-", dir=parent,
                )
                try:
                    with tarfile.open(archive_path, "r:gz") as t:
                        _extract_linux_tar(t, staging_dir)
                    staged_exe = os.path.join(staging_dir, exe_name)
                    staged = _llama_server_capabilities(staged_exe)
                    if (
                        not staged["runnable"]
                        or (
                            staged["build"] is not None
                            and staged["build"] < MIN_LLAMA_BUILD
                        )
                    ):
                        raise RuntimeError(
                            "downloaded llama-server runtime failed validation"
                        )
                    _atomic_install_runtime(staging_dir, bin_dir)
                    staging_dir = ""
                finally:
                    if staging_dir:
                        shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception:
            if windows_stage is not None:
                windows_stage.cleanup()
                windows_stage = None
            raise
        finally:
            try:
                os.remove(archive_path)
            except OSError:
                pass
            with _download_state_lock:
                if _download_state.get("phase") == "downloading_runtime":
                    _download_state.clear()

    if windows_stage is not None:
        try:
            staged_exe = os.path.join(runtime_extract_dir, exe_name)
            staged = _llama_server_capabilities(staged_exe)
            if (
                not staged["runnable"]
                or (
                    staged["build"] is not None
                    and staged["build"] < MIN_LLAMA_BUILD
                )
            ):
                raise RuntimeError(
                    "downloaded llama-server runtime failed validation"
                )
            _atomic_install_runtime(runtime_extract_dir, bin_dir)
        finally:
            windows_stage.cleanup()

    if not os.path.isfile(exe_path):
        raise FileNotFoundError(
            f"Downloaded llama.cpp release but {exe_name} not found in {bin_dir} "
            f"after extraction. Tried: {asset_urls}"
        )
    print(f"[LLM] llama-server installed to {exe_path}")
    return _llama_server_capabilities(exe_path)


def _get_server_exe(requested_device: str = "cpu") -> str:
    """Find llama-server, downloading it to the host cache if missing.

    Lazy preparation matches the model-weights flow: nothing is fetched
    until an LLM call needs the runtime. The ~50-100 MB download is cached
    in bin_dir; an update or interrupted preparation may fetch it again.
    """
    bin_dir = os.environ.get("MAESTRO_LLAMA_BIN", DEFAULT_BIN_DIR)
    _ensure_llama_server(bin_dir, requested_device=requested_device)
    if os.name == "nt":
        return os.path.join(bin_dir, "llama-server.exe")
    return os.path.join(bin_dir, "llama-server")


def _flag_value(flags: Sequence, aliases: Sequence[str]):
    flags = list(flags or [])
    for index, raw in enumerate(flags):
        value = str(raw)
        if value in aliases and index + 1 < len(flags):
            return flags[index + 1]
        for alias in aliases:
            if value.startswith(alias + "="):
                return value.split("=", 1)[1]
    return None


def _has_flag(flags: Sequence, aliases: Sequence[str]) -> bool:
    return _flag_value(flags, aliases) is not None or any(
        str(flag) in aliases for flag in (flags or [])
    )


def _strip_flags_with_values(flags: Sequence, aliases: Sequence[str]) -> list:
    cleaned = []
    skip_next = False
    for raw in list(flags or []):
        if skip_next:
            skip_next = False
            continue
        value = str(raw)
        if value in aliases:
            skip_next = True
            continue
        if any(value.startswith(alias + "=") for alias in aliases):
            continue
        cleaned.append(raw)
    return cleaned


def _hardware_profile(probe_gpu: bool = True) -> dict:
    """Return cached, non-sensitive compute capacity used for safe tuning."""
    global _hardware_cache
    if probe_gpu and _hardware_cache is not None:
        return dict(_hardware_cache)
    logical_threads = max(int(os.cpu_count() or 2), 2)
    physical_threads = max(logical_threads // 2, 2)
    gpu_vram_gb = 0.0
    try:
        if not probe_gpu:
            raise RuntimeError("hardware probe disabled")
        result = subprocess.run(
            [
                "nvidia-smi", "--query-gpu=index,uuid,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            visible_memory = []
            for line in (result.stdout or "").splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) != 3:
                    continue
                index, uuid, memory_mib = parts
                if _cuda_device_is_visible(index, uuid):
                    visible_memory.append(float(memory_mib) / 1024)
            if visible_memory:
                gpu_vram_gb = min(visible_memory)
    except Exception:
        pass
    profile = {
        "logical_threads": logical_threads,
        "physical_threads": physical_threads,
        "gpu_vram_gb": round(gpu_vram_gb, 1),
    }
    if probe_gpu:
        _hardware_cache = profile
    return dict(profile)


def _runtime_profile_for(
    model_path: str,
    projector_path: Optional[str],
    requested_device: str,
    capabilities: dict,
    registry_entry: dict,
    *,
    probe_hardware: bool = True,
    cpu_coexistence: bool = False,
) -> dict:
    """Choose conservative fast llama.cpp flags for this model and host."""
    hardware = _hardware_profile(probe_gpu=probe_hardware)
    extra_flags = list(registry_entry.get("extra_flags") or [])
    overrides = dict(registry_entry.get("runtime_profile") or {})
    effective_cuda = (
        requested_device == "cuda" and capabilities.get("backend") == "cuda"
    )
    context = _flag_value(extra_flags, ("-c", "--ctx-size"))
    try:
        context = int(context)
    except (TypeError, ValueError):
        context = 65_536 if registry_entry else 32_768

    physical = hardware["physical_threads"]
    logical = hardware["logical_threads"]
    threads = physical if not effective_cuda else min(physical, 16)
    threads_batch = min(logical, 32)
    artifact_gb = (
        os.path.getsize(model_path)
        + (os.path.getsize(projector_path) if projector_path else 0)
    ) / (1024 ** 3)
    if (
        effective_cuda
        and hardware["gpu_vram_gb"] >= 12
        and artifact_gb <= hardware["gpu_vram_gb"] * 0.60
    ):
        batch_size, ubatch_size = 2048, 512
    elif effective_cuda:
        batch_size, ubatch_size = 1024, 256
    else:
        batch_size, ubatch_size = 512, 128

    def configured_int(aliases: Sequence[str], default: int) -> int:
        value = _flag_value(extra_flags, aliases)
        try:
            return max(1, int(value)) if value is not None else default
        except (TypeError, ValueError):
            return default

    threads = configured_int(("-t", "--threads"), threads)
    threads_batch = configured_int(("-tb", "--threads-batch"), threads_batch)
    batch_size = configured_int(("-b", "--batch-size"), batch_size)
    ubatch_size = configured_int(("-ub", "--ubatch-size"), ubatch_size)

    flash_attention = _flag_value(extra_flags, ("-fa", "--flash-attn"))
    if flash_attention is None:
        flash_attention = "on" if effective_cuda else "auto"
    cache_k = _flag_value(extra_flags, ("-ctk", "--cache-type-k"))
    cache_v = _flag_value(extra_flags, ("-ctv", "--cache-type-v"))
    if cache_k is None:
        cache_k = "q8_0" if effective_cuda else "f16"
    if cache_v is None:
        cache_v = "q8_0" if effective_cuda else "f16"

    gpu_layers = 0
    if effective_cuda:
        configured_layers = _flag_value(
            extra_flags, ("-ngl", "--gpu-layers", "--n-gpu-layers"),
        )
        if configured_layers is not None:
            gpu_layers = configured_layers
        else:
            gpu_layers = -1 if artifact_gb <= hardware["gpu_vram_gb"] * 0.82 else "auto"

    profile = {
        "backend": "cuda" if effective_cuda else "cpu",
        "context_size": context,
        "batch_size": batch_size,
        "ubatch_size": ubatch_size,
        "threads": max(2, threads),
        "threads_batch": max(2, threads_batch),
        "flash_attention": str(flash_attention),
        "cache_type_k": str(cache_k),
        "cache_type_v": str(cache_v),
        "gpu_layers": gpu_layers,
        "slots": 1,
        "prompt_cache": True,
        "projector_offload": bool(projector_path and effective_cuda),
    }
    for key in tuple(profile):
        if key in overrides and key not in {"backend", "slots", "prompt_cache"}:
            profile[key] = overrides[key]
    if cpu_coexistence:
        # This lane deliberately leaves CPU and memory-bandwidth headroom for
        # the foreground GPU worker and the rest of Maestro. Model-authored
        # flags remain authoritative for ordinary CPU/GPU execution; only the
        # explicitly requested coexistence lane applies these upper bounds.
        profile["backend"] = "cpu"
        profile["gpu_layers"] = 0
        profile["projector_offload"] = False
        def bounded(value, cap: int) -> int:
            try:
                return max(1, min(int(value), cap))
            except (TypeError, ValueError):
                return cap

        profile["threads"] = bounded(
            profile["threads"], _CPU_COEXISTENCE_MAX_THREADS,
        )
        profile["threads_batch"] = bounded(
            profile["threads_batch"], _CPU_COEXISTENCE_MAX_BATCH_THREADS,
        )
        profile["batch_size"] = bounded(
            profile["batch_size"], _CPU_COEXISTENCE_BATCH_SIZE,
        )
        profile["ubatch_size"] = min(
            bounded(profile["ubatch_size"], _CPU_COEXISTENCE_UBATCH_SIZE),
            profile["batch_size"],
        )
        profile["execution"] = CPU_COEXISTENCE_MODE
        profile["abort_capable"] = True
        profile["preemptible"] = False
        profile["preemption_requires_decision_evidence"] = True
    return profile


def _build_llama_server_command(
    server_exe: str,
    model_path: str,
    port: int,
    profile: dict,
    *,
    extra_flags: Sequence = (),
    mmproj_path: Optional[str] = None,
    disable_jinja: bool = False,
) -> list:
    """Build a testable launch command while retaining model-specific flags."""
    managed_value_aliases = (
        "-c", "--ctx-size", "-b", "--batch-size", "-ub", "--ubatch-size",
        "-t", "--threads", "-tb", "--threads-batch", "-np", "--parallel",
        "-fa", "--flash-attn", "-ctk", "--cache-type-k", "-ctv",
        "--cache-type-v", "-ngl", "--gpu-layers", "--n-gpu-layers",
        "-mm", "--mmproj", "--mtmd-batch-max-tokens",
    )
    retained = _strip_flags_with_values(extra_flags, managed_value_aliases)
    retained = [
        flag for flag in retained
        if str(flag) not in {
            "--cache-prompt", "--no-cache-prompt", "--perf", "--no-perf",
            "--mmproj-offload", "--no-mmproj-offload",
        }
    ]
    cmd = [
        server_exe, "--model", model_path, "--host", "127.0.0.1",
        "--port", str(port),
    ]
    if not disable_jinja:
        cmd.append("--jinja")
    cmd.extend(str(flag) for flag in retained)

    def add(option: str, value, aliases: Sequence[str]):
        if not _has_flag(retained, aliases):
            cmd.extend([option, str(value)])

    add("--ctx-size", profile["context_size"], ("-c", "--ctx-size"))
    add("--batch-size", profile["batch_size"], ("-b", "--batch-size"))
    add("--ubatch-size", profile["ubatch_size"], ("-ub", "--ubatch-size"))
    add("--threads", profile["threads"], ("-t", "--threads"))
    add(
        "--threads-batch", profile["threads_batch"],
        ("-tb", "--threads-batch"),
    )
    cmd.extend(["--parallel", "1"])
    add("--flash-attn", profile["flash_attention"], ("-fa", "--flash-attn"))
    add("--cache-type-k", profile["cache_type_k"], ("-ctk", "--cache-type-k"))
    add("--cache-type-v", profile["cache_type_v"], ("-ctv", "--cache-type-v"))
    cmd.extend(["--n-gpu-layers", str(profile["gpu_layers"])])
    cmd.append("--cache-prompt")
    cmd.append("--perf")
    if mmproj_path:
        cmd.extend(["--mmproj", mmproj_path])
        if profile.get("projector_offload") is False:
            cmd.append("--no-mmproj-offload")
        else:
            cmd.append("--mmproj-offload")
        if not _has_flag(retained, ("--mtmd-batch-max-tokens",)):
            cmd.extend(["--mtmd-batch-max-tokens", "1"])
    return cmd


@_with_failed_load_cleanup
def load_model(
    model_id: str = "",
    device: str = "cpu",
    force_reload: bool = False,
    provider: str = "local",
    remote_url: str = "",
    api_key: str = "",
    local_gguf_path: str = "",
    gguf_file_override: str = "",
    cpu_coexistence: bool = False,
) -> None:
    """Load an LLM model. Supports local (llama-server), remote (OpenAI-compatible),
    OpenAI API, and Anthropic API providers.

    Args:
        model_id: Model ID (HF repo for local, model name for remote/API)
        device: "cpu" or "cuda" (local only)
        force_reload: If True, restart even if already running
        provider: "local" | "remote" | "openai" | "anthropic"
        remote_url: Base URL for remote/openai servers (e.g. http://192.168.1.100:1234)
        api_key: API key for openai/anthropic providers
        cpu_coexistence: Force the bounded, externally abortable CPU profile.
    """
    global _process, _model_id, _device, _server_port, _vision_available
    global _provider, _remote_url, _api_key, _loaded_model_key
    global _runtime_backend, _runtime_build, _runtime_devices, _runtime_profile
    global _runtime_timings, _requested_device, _loading_model_id
    global _runtime_fallback_reason, _runtime_model_size_gb
    global _runtime_timings_multimodal, _runtime_speed_variant_digest
    global _runtime_generation_counter, _runtime_generation
    global _runtime_phase, _runtime_execution
    global _runtime_load_started_at, _runtime_load_finished_at
    global _runtime_request_started_at, _runtime_request_finished_at
    global _runtime_abort_requested_at, _runtime_last_release

    provider = str(provider or "local").lower()
    if cpu_coexistence and provider != "local":
        raise ValueError("CPU coexistence is available only for local LLMs")
    local_path_key = (
        os.path.realpath(os.path.abspath(local_gguf_path))
        if local_gguf_path else ""
    )
    credential_key = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if api_key else ""
    )

    # Handle remote/API providers — no subprocess needed
    if provider in ("remote", "openai", "anthropic"):
        load_key = (
            provider, model_id, provider, remote_url.rstrip("/"),
            credential_key, "", "",
        )
        with _lock:
            if is_loaded() and _loaded_model_key == load_key and not force_reload:
                _reset_idle_timer(load_key)
                return
            if is_loaded() or _process is not None:
                _unload_inner()
            with _runtime_status_lock:
                _provider = provider
                _remote_url = remote_url
                _api_key = api_key
                _model_id = model_id
                _device = provider
                _requested_device = provider
                _vision_available = False
                _runtime_backend = provider
                _runtime_build = None
                _runtime_devices = []
                _runtime_profile = {}
                _runtime_timings = {}
                _runtime_fallback_reason = ""
                _runtime_model_size_gb = 0.0
                _runtime_timings_multimodal = False
                _runtime_speed_variant_digest = ""
                _loaded_model_key = load_key
            print(f"[LLM] Connected to {provider} provider: model={model_id}, url={remote_url or 'API'}")
            _reset_idle_timer(load_key)
        return

    repo_id = model_id or DEFAULT_HF_REPO
    migrated_repo_id = _migrate_retired_model_id(repo_id)
    if migrated_repo_id != repo_id:
        print("[LLM] Retired saved model selection migrated to the text default")
        repo_id = migrated_repo_id
    requested_device = (
        "cpu" if cpu_coexistence
        else ("cuda" if str(device).lower() == "cuda" else "cpu")
    )

    with _lock:
        print(f"[LLM] Preparing model: {repo_id} for {requested_device}")

        base_cache_dir = get_model_dir()

        # Look up GGUF filename from registry, fall back to convention.
        # A discovered model arrives as a server-resolved local path; the
        # browser sees only its opaque model ID and can never supply this path.
        repo_basename = repo_id.split("/")[-1] if "/" in repo_id else repo_id
        model_stem = repo_basename.replace("-GGUF", "")
        if gguf_file_override:
            gguf_file = gguf_file_override
        elif repo_id in MODEL_REGISTRY:
            gguf_file = MODEL_REGISTRY[repo_id]["gguf_file"]
        else:
            gguf_file = f"{model_stem}-Q4_K_S.gguf"

        if local_gguf_path:
            gguf_path = os.path.realpath(os.path.abspath(local_gguf_path))
            if not os.path.isfile(gguf_path) or not gguf_path.lower().endswith(".gguf"):
                raise ValueError("Resolved local LLM is not a GGUF file")
            cache_dir = os.path.dirname(gguf_path)
        else:
            # Use model-specific subdirectory to avoid cache collisions between models
            dir_override = MODEL_REGISTRY.get(repo_id, {}).get("cache_dir_override")
            cache_dir = os.path.join(base_cache_dir, dir_override or model_stem)
            os.makedirs(cache_dir, exist_ok=True)
            with _runtime_status_lock:
                _loading_model_id = repo_id
            gguf_path = _download_gguf(repo_id, gguf_file, cache_dir)
            gguf_path = os.path.normpath(gguf_path)

        # Try to download mmproj for vision support (optional — not all models have it)
        registered_model = repo_id in MODEL_REGISTRY
        registry_entry = MODEL_REGISTRY.get(repo_id, {})
        # Known models have an explicit registry contract: missing/None means
        # text-only. Preserve the historical best-effort default only for
        # ad-hoc, unregistered Hugging Face repositories.
        mmproj_file = (
            registry_entry.get("mmproj_file")
            if registered_model
            else DEFAULT_MMPROJ_FILE
        )
        if local_gguf_path:
            mmproj_file = None
        mmproj_repo = registry_entry.get("mmproj_repo", repo_id)  # allow mmproj from different repo
        mmproj_path = _find_sibling_mmproj(gguf_path) if local_gguf_path else None
        if mmproj_path:
            print("[LLM] Vision support: linked sibling projector selected")
        elif mmproj_file:
            try:
                with _runtime_status_lock:
                    _loading_model_id = repo_id
                mmproj_path = _download_gguf(mmproj_repo, mmproj_file, cache_dir)
                mmproj_path = os.path.normpath(mmproj_path)
                print(f"[LLM] Vision support: mmproj loaded from {mmproj_repo}")
            except Exception as e:
                print(f"[LLM] No mmproj available (vision disabled): {e}")
        else:
            print("[LLM] Model is registered without an mmproj (vision disabled)")

        vision_enabled = bool(
            mmproj_path is not None or registry_entry.get("native_vision", False)
        )
        server_exe = _get_server_exe(requested_device)
        runtime_env = _cuda_process_env(
            _discover_nvcc() if requested_device == "cuda" else None
        )
        probe_runtime = os.path.isfile(server_exe)
        capabilities = (
            _llama_server_capabilities(server_exe, env=runtime_env)
            if probe_runtime
            else {
                "build": None, "runnable": True, "backend": "cpu", "devices": [],
            }
        )
        extra_flags = registry_entry.get("extra_flags", [])
        disable_jinja = bool(registry_entry.get("disable_jinja", False))
        profile = _runtime_profile_for(
            gguf_path, mmproj_path, requested_device, capabilities, registry_entry,
            probe_hardware=probe_runtime,
            cpu_coexistence=cpu_coexistence,
        )
        load_key = (
            "local", repo_id, requested_device, "", "", local_path_key,
            gguf_file_override, _safe_file_identity(gguf_path),
            _safe_file_identity(mmproj_path),
            tuple(sorted(profile.items())), capabilities.get("build"),
            _runtime_launch_identity(server_exe, extra_flags, disable_jinja),
        )
        if is_loaded() and _loaded_model_key == load_key and not force_reload:
            _activate_request_scope_after_load()
            _reset_idle_timer(load_key)
            return
        if is_loaded() or _process is not None:
            _unload_inner()

        runtime_model_size_gb = os.path.getsize(gguf_path) / (1024 ** 3)
        runtime_speed_variant_digest = _speed_variant_digest(
            repo_id,
            local_gguf_path=gguf_path,
            gguf_file_override=gguf_file_override,
            device=profile["backend"],
            effective_profile_override=profile,
        )
        server_port = _find_free_port()
        with _runtime_status_lock:
            _provider = "local"
            _remote_url = ""
            _api_key = ""
            _requested_device = requested_device
            _runtime_backend = profile["backend"]
            _runtime_build = capabilities.get("build")
            _runtime_devices = list(capabilities.get("devices") or [])
            _runtime_profile = dict(profile)
            _runtime_timings = {}
            _runtime_model_size_gb = runtime_model_size_gb
            _runtime_timings_multimodal = False
            _runtime_speed_variant_digest = runtime_speed_variant_digest
            _vision_available = vision_enabled
            _server_port = server_port
            _runtime_phase = "loading"
            _runtime_execution = (
                CPU_COEXISTENCE_MODE if cpu_coexistence else "standard"
            )
            _runtime_load_started_at = time.time()
            _runtime_load_finished_at = None
            _runtime_request_started_at = None
            _runtime_request_finished_at = None
            _runtime_abort_requested_at = None
            _runtime_last_release = {}
            _clear_runtime_remaining_evidence_locked("runtime_replaced")
        cmd = _build_llama_server_command(
            server_exe, gguf_path, server_port, profile,
            extra_flags=extra_flags,
            mmproj_path=mmproj_path,
            disable_jinja=disable_jinja,
        )

        print(f"[LLM] Starting llama-server on port {server_port}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            env=runtime_env,
        )
        with _runtime_status_lock:
            _runtime_generation_counter += 1
            _runtime_generation = _runtime_generation_counter
            _process = process
        # Start the sole stdout consumer immediately. Cold loads can emit far
        # more than a pipe buffer before /health becomes ready.
        log_drain_done = _start_log_reader(process)

        # Wait for server to be ready (poll /health)
        # Scale timeout with model size — large models need more time to load
        file_size_gb = os.path.getsize(gguf_path) / 1e9
        load_timeout = max(60, int(file_size_gb * 15))  # ~15s per GB, min 60s
        ready = False
        for i in range(load_timeout):
            if _process.poll() is not None:
                # The background reader is the sole stdout consumer.
                exit_code = _process.returncode
                # A confirmed process exit closes the pipe. Wait for this
                # process's own reader to observe EOF and publish every
                # bounded diagnostic chunk, but never wait indefinitely for a
                # misbehaving stream implementation.
                log_drain_done.wait(timeout=_LOG_DRAIN_EXIT_WAIT_SEC)
                output = _server_log_tail(20)
                _unload_inner()
                raise RuntimeError(
                    f"llama-server exited with code {exit_code}. "
                    f"Content-free diagnostic tail:\n{output}"
                )
            try:
                resp = requests.get(f"{_server_url()}/health", timeout=1)
                if resp.status_code == 200:
                    data = resp.json() if resp.text else {}
                    status = data.get("status", "ok")
                    if status == "ok":
                        ready = True
                        break
                    elif status == "loading model":
                        pass  # still loading
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(1)

        if not ready:
            server_output = _server_log_tail(20)
            _unload_inner()
            raise RuntimeError(
                f"llama-server did not become ready within {load_timeout}s (model: {file_size_gb:.1f}GB)\n"
                f"Content-free diagnostic tail:\n{server_output}"
            )

        with _runtime_status_lock:
            _model_id = repo_id
            _loading_model_id = ""
            _device = profile["backend"]
            _loaded_model_key = load_key
            _runtime_load_finished_at = time.time()
            _runtime_phase = "ready"
        _activate_request_scope_after_load()
        file_size = os.path.getsize(gguf_path) / 1e6
        print(
            f"[LLM] Model loaded: {repo_id} ({file_size:.0f}MB) on "
            f"{profile['backend']}, port {_server_port}"
        )
        _reset_idle_timer(load_key)


def _cancel_idle_timer():
    """Cancel any pending idle-unload timer."""
    global _idle_timer, _idle_timer_generation
    # Invalidate even a callback that has already fired and is waiting for the
    # model lease. Timer.cancel() alone cannot stop that queued callback.
    _idle_timer_generation += 1
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _reset_idle_timer(expected_identity: Optional[tuple] = None) -> bool:
    """Reset the idle-unload timer. Called after each LLM request."""
    global _idle_timer
    if getattr(_model_activity, "depth", 0) > 0:
        return False
    identity = _loaded_model_key if expected_identity is None else expected_identity
    if not identity or _loaded_model_key != identity or not is_loaded():
        return False
    _cancel_idle_timer()
    generation = _idle_timer_generation
    _idle_timer = threading.Timer(
        _idle_timeout, _auto_unload, args=(generation,),
    )
    _idle_timer.daemon = True
    _idle_timer.start()
    return True


def _finish_model_activity(identity: tuple) -> bool:
    """Arm idle expiry only while the activity's exact model still resides."""
    if not identity or _loaded_model_key != identity or not is_loaded():
        return False
    return _reset_idle_timer(identity)


def _auto_unload(generation: Optional[int] = None):
    """Called by the idle timer to unload the LLM after inactivity."""
    global _idle_timer
    with _lock:
        if generation is not None and generation != _idle_timer_generation:
            return
        _idle_timer = None
        if is_loaded():
            print("[LLM] Auto-unloading after idle timeout")
            _unload_inner()


def _start_log_reader(proc: subprocess.Popen) -> threading.Event:
    """Immediately drain stdout into bounded, content-free diagnostics.

    This function owns the only reader for ``proc.stdout``. Each raw chunk is
    reduced to its byte count and a small runtime-signal vocabulary; neither
    model output nor prompt-like text is retained or logged.
    """
    global _log_reader, _log_reader_generation
    _log_reader_generation += 1
    generation = _log_reader_generation
    _server_log.clear()
    done = threading.Event()

    def _drain():
        try:
            if proc.stdout is None:
                return
            sequence = 0
            for raw in iter(lambda: proc.stdout.readline(4096), b""):
                sequence += 1
                lowered = raw.lower()
                signals = []
                if any(marker in lowered for marker in (
                    b"out of memory", b"cudamalloc", b"erralloc",
                    b"alloc failed",
                )):
                    signals.append("memory-allocation")
                if b"cuda" in lowered:
                    signals.append("cuda")
                if any(marker in lowered for marker in (
                    b"error", b"failed", b"fatal", b"abort",
                )):
                    signals.append("failure")
                suffix = f"; signals={','.join(signals)}" if signals else ""
                if generation == _log_reader_generation:
                    _server_log.append(
                        f"chunk {sequence}: {len(raw)} bytes{suffix}"
                    )
        except Exception:
            pass
        finally:
            done.set()

    _log_reader = threading.Thread(target=_drain, name="llama-log-reader", daemon=True)
    _log_reader.start()
    return done


def _server_log_tail(n: int = 20) -> str:
    lines = list(_server_log)[-n:]
    return "\n".join(lines) if lines else "(no diagnostic chunks captured)"


def _terminate_process_and_confirm(
    process: subprocess.Popen,
    *,
    terminate_timeout: float,
    kill_timeout: float,
) -> tuple[bool, bool]:
    """Stop one captured process and confirm that its resources were released."""

    if process.poll() is not None:
        return True, False
    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=max(0.0, float(terminate_timeout)))
    except Exception:
        pass
    if process.poll() is not None:
        return True, False
    escalated = True
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=max(0.0, float(kill_timeout)))
    except Exception:
        pass
    return process.poll() is not None, escalated


def _clear_runtime_state_locked() -> None:
    """Clear singleton state after a confirmed process exit.

    The caller must hold ``_runtime_status_lock``. The monotonic generation
    counters and the last confirmed release record deliberately survive.
    """

    global _process, _model_id, _device, _server_port, _vision_available
    global _loaded_model_key, _loading_model_id
    global _provider, _remote_url, _api_key, _requested_device
    global _runtime_backend, _runtime_build, _runtime_devices, _runtime_profile
    global _runtime_timings, _runtime_fallback_reason, _runtime_model_size_gb
    global _runtime_timings_multimodal, _runtime_speed_variant_digest
    global _runtime_generation, _runtime_active_attempt_id, _runtime_phase
    global _runtime_execution, _runtime_load_started_at
    global _runtime_load_finished_at, _runtime_request_started_at
    global _runtime_request_finished_at, _runtime_abort_requested_at
    _process = None
    _model_id = ""
    _device = ""
    _server_port = 0
    _vision_available = False
    _loading_model_id = ""
    _loaded_model_key = ()
    _provider = "local"
    _remote_url = ""
    _api_key = ""
    _requested_device = ""
    _runtime_backend = ""
    _runtime_build = None
    _runtime_devices = []
    _runtime_profile = {}
    _runtime_timings = {}
    _runtime_fallback_reason = ""
    _runtime_model_size_gb = 0.0
    _runtime_timings_multimodal = False
    _runtime_speed_variant_digest = ""
    _runtime_generation = 0
    _runtime_active_attempt_id = 0
    _runtime_phase = "idle"
    _runtime_execution = ""
    _runtime_load_started_at = None
    _runtime_load_finished_at = None
    _runtime_request_started_at = None
    _runtime_request_finished_at = None
    _runtime_abort_requested_at = None
    _clear_runtime_remaining_evidence_locked("runtime_released")


def _stale_abort_result(
    expected_generation: int,
    expected_attempt_id: Optional[int],
) -> dict:
    return {
        "matched": False,
        "runtime_generation": int(expected_generation),
        "attempt_id": (
            int(expected_attempt_id) if expected_attempt_id is not None else None
        ),
        "resources_released": False,
        "released_at": None,
        "escalated_to_kill": False,
    }


def abort_local_cpu_runtime(
    expected_generation: int,
    expected_attempt_id: Optional[int] = None,
    *,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 5.0,
) -> dict:
    """Preempt one exact cooperative CPU request without taking ``_lock``.

    The captured ``Popen`` object is the cancellation target. A delayed abort
    therefore cannot act on a replacement process even if a newer runtime has
    already become resident by the time the termination waits finish.
    """

    global _runtime_abort_requested_at, _runtime_phase
    global _runtime_last_release, _runtime_last_aborted_attempt
    try:
        generation = int(expected_generation)
        attempt = (
            int(expected_attempt_id)
            if expected_attempt_id is not None else None
        )
    except (TypeError, ValueError):
        return _stale_abort_result(0, None)
    with _runtime_status_lock:
        process = _process
        active_attempt = _runtime_active_attempt_id
        if (
            generation <= 0
            or attempt is None
            or attempt <= 0
            or generation != _runtime_generation
            or _provider != "local"
            or _runtime_backend != "cpu"
            or _runtime_execution != CPU_COEXISTENCE_MODE
            or _runtime_phase != "requesting"
            or process is None
            or process.poll() is not None
            or active_attempt <= 0
            or attempt != active_attempt
        ):
            return _stale_abort_result(generation, attempt)
        attempt = active_attempt
        requested_at = time.time()
        _runtime_abort_requested_at = requested_at
        _runtime_phase = "abort_requested"
        _clear_runtime_remaining_evidence_locked("abort_requested")
        # Publish exact intent before signalling the process. The request can
        # observe the dead socket before the termination waiter returns; it
        # must still distinguish owner preemption from an unplanned crash.
        _runtime_last_aborted_attempt = (generation, attempt, False)

    released, escalated = _terminate_process_and_confirm(
        process,
        terminate_timeout=terminate_timeout,
        kill_timeout=kill_timeout,
    )
    released_at = time.time() if released else None
    with _runtime_status_lock:
        if released:
            if _runtime_last_aborted_attempt[:2] == (generation, attempt):
                _runtime_last_aborted_attempt = (generation, attempt, True)
            release = {
                "generation": generation,
                "attempt_id": attempt,
                "request_started_at": _runtime_request_started_at,
                "abort_requested_at": requested_at,
                "released_at": released_at,
                "resources_released": True,
                "escalated_to_kill": escalated,
            }
            # A replacement cannot be cleared by a delayed callback. Only the
            # exact process and generation captured above are eligible.
            if _process is process and _runtime_generation == generation:
                _runtime_last_release = release
                _clear_runtime_state_locked()
        elif _process is process and _runtime_generation == generation:
            _runtime_phase = "release_failed"
    return {
        "matched": True,
        "runtime_generation": generation,
        "attempt_id": attempt,
        "resources_released": released,
        "released_at": released_at,
        "escalated_to_kill": escalated,
    }


def _diagnose_llm_request_failure(exc: Exception) -> "RuntimeError":
    """Turn a raw request failure into an actionable error.

    Distinguishes "the llama-server subprocess died" (the common cause of a
    frozen Director run — bad GGUF quant, VRAM OOM at load, wrong binary)
    from a transient network blip and includes only content-free diagnostics.
    """
    token = _current_runtime_attempt_token()
    if token is not None:
        with _runtime_status_lock:
            aborted = _runtime_last_aborted_attempt
        if token[:2] == aborted[:2]:
            return LocalRuntimeAbortedError(
                token[0], token[1], resources_released=aborted[2],
            )
    proc = _process
    if _provider == "local" and proc is not None:
        # A reset socket usually means the subprocess is mid-death; poll()
        # can race the actual exit by a moment. Give it a beat to finish
        # dying so a crash is reported as a crash (with the server's last
        # diagnostics) instead of a generic connection error.
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    if _provider == "local" and proc is not None and proc.poll() is not None:
        code = proc.returncode
        tail = _server_log_tail(40)
        _unload_inner()  # reset singleton so the next call relaunches cleanly
        # Only use OOM wording when the diagnostic signals show an OOM —
        # services/oom_detect.py substring-matches "out of memory" on error
        # text, so speculative OOM wording here made every server crash pop
        # the "lower VRAM headroom?" recovery banner even when the GPU was
        # nearly empty (e.g. the clip.cpp image-batch abort).
        if "signals=memory-allocation" in tail:
            cause = "The GPU ran out of memory mid-request (e.g. a video/image model was still resident)."
        else:
            cause = "This is an internal llama-server failure; see its last output below."
        return RuntimeError(
            f"The local LLM server (llama-server) crashed while generating "
            f"(exit code {code}). {cause} "
            f"Content-free diagnostic tail:\n{tail}"
        )
    if _provider == "local" and proc is None:
        return RuntimeError(
            "The local LLM server is not running. It may have been unloaded "
            "or failed to start — retry, or check the Services settings."
        )
    if _provider == "local":
        # Server still alive — include its recent output anyway; CUDA errors
        # can surface as dropped requests without killing the process.
        tail = _server_log_tail(15)
        return RuntimeError(
            f"LLM request failed: {exc}\nContent-free diagnostic tail:\n{tail}"
        )
    # Remote provider — a real network/timeout issue.
    return RuntimeError(f"LLM request failed: {exc}")


def _unload_inner():
    global _runtime_phase, _runtime_last_release
    _cancel_idle_timer()
    with _runtime_status_lock:
        process = _process
        generation = _runtime_generation
        attempt_id = _runtime_active_attempt_id or None
        if process is not None and process.poll() is None:
            _runtime_phase = "releasing"
    released = True
    escalated = False
    if process is not None:
        released, escalated = _terminate_process_and_confirm(
            process,
            terminate_timeout=_PROCESS_TERMINATE_TIMEOUT_SEC,
            kill_timeout=_PROCESS_KILL_TIMEOUT_SEC,
        )
    if not released:
        with _runtime_status_lock:
            if _process is process and _runtime_generation == generation:
                _runtime_phase = "release_failed"
        raise RuntimeError(
            "Local LLM process did not exit after terminate and kill waits"
        )
    with _runtime_status_lock:
        if _process is process and _runtime_generation == generation:
            if process is not None:
                _runtime_last_release = {
                    "generation": generation,
                    "attempt_id": attempt_id,
                    "released_at": time.time(),
                    "resources_released": True,
                    "escalated_to_kill": escalated,
                }
            _clear_runtime_state_locked()
        elif process is None and _process is None:
            _clear_runtime_state_locked()
    gc.collect()


def unload_model() -> None:
    with _lock:
        _unload_inner()
        print("[LLM] Model unloaded")


def _image_to_data_url(image_path: str, max_size: int = 768) -> Optional[str]:
    """Read an image file, resize if needed, and return a data URL (base64-encoded).

    Large images are resized so the longest edge is at most *max_size* pixels
    and re-encoded as JPEG to keep the data URL compact for LLM context.
    """
    import base64
    if not image_path or not os.path.isfile(image_path):
        return None
    try:
        from PIL import Image
        import io
        img = Image.open(image_path)
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # ASCII arrow on purpose: a cp1252 console (plain cmd, some CI
            # shells) can't encode U+2192 and the print would crash the
            # whole vision request mid-flight.
            print(f"[LLM] Resized image for LLM: {w}x{h} -> {img.size[0]}x{img.size[1]}")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"
    except ImportError:
        # PIL not available — send raw file (no resize)
        import mimetypes
        mime, _ = mimetypes.guess_type(image_path)
        if not mime:
            mime = "image/png"
        with open(image_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{data}"


@_with_model_lease
def generate_chat(
    messages,
    *,
    model_id: str,
    device: str = "cpu",
    provider: str = "local",
    remote_url: str = "",
    api_key: str = "",
    local_gguf_path: str = "",
    gguf_file_override: str = "",
    cpu_coexistence: bool = False,
    system_prompt: str = "",
    image_paths: Optional[Sequence[str]] = None,
    max_new_tokens: int = 2048,
    temperature: float = 0.7,
    top_p: float = 0.9,
    enable_thinking: Optional[bool] = None,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Atomically load the selected model and run a role-preserving chat.

    The model singleton is shared by Director and the rest of Maestro. Holding
    the re-entrant model lease across resolution, load, and inference prevents
    an idle unload or another request from swapping the process mid-turn.
    """
    global _stream_done
    request_scoped_progress = callable(progress_callback)
    _cancellation_checkpoint(cancel_handle)
    clean_messages = validate_chat_messages(messages)
    if (
        isinstance(max_new_tokens, bool)
        or not isinstance(max_new_tokens, int)
        or not 1 <= max_new_tokens <= CHAT_MAX_NEW_TOKENS
    ):
        raise ValueError(
            f"max_new_tokens must be between 1 and {CHAT_MAX_NEW_TOKENS}"
        )
    if not isinstance(system_prompt, str):
        raise ValueError("system_prompt must be text")
    if image_paths is None:
        image_paths = []
    if (
        isinstance(image_paths, (str, bytes))
        or not isinstance(image_paths, Sequence)
        or len(image_paths) > 8
        or any(not isinstance(path, str) or not path for path in image_paths)
    ):
        raise ValueError("image_paths must be a list of at most 8 image files")

    load_model(
        model_id=model_id,
        device=device,
        provider=provider,
        remote_url=remote_url,
        api_key=api_key,
        local_gguf_path=local_gguf_path,
        gguf_file_override=gguf_file_override,
        cpu_coexistence=cpu_coexistence,
    )
    if not is_loaded():
        raise RuntimeError("LLM did not finish loading")
    if image_paths and not _vision_available:
        raise ValueError("The selected LLM has no available vision projector")
    _cancellation_checkpoint(cancel_handle)

    _cancel_idle_timer()
    prepared_system, enable_thinking, thinking_budget = _prepare_thinking(
        system_prompt, enable_thinking, 0,
    )
    api_messages = []
    if prepared_system:
        api_messages.append({"role": "system", "content": prepared_system})
    api_messages.extend(clean_messages)
    if image_paths:
        image_content = [
            {"type": "text", "text": api_messages[-1]["content"]},
        ]
        for image_path in image_paths:
            data_url = _image_to_data_url(image_path)
            if not data_url:
                raise ValueError("An authorized chat image is unavailable")
            image_content.append({
                "type": "image_url", "image_url": {"url": data_url},
            })
        api_messages[-1] = {"role": "user", "content": image_content}

    payload = {
        "messages": api_messages,
        "max_tokens": max_new_tokens + thinking_budget,
    }
    assistant_prefix = apply_local_assistant_prefill(
        api_messages,
        payload,
        options=response_assist,
        provider=_provider,
        structured=False,
        enable_thinking=enable_thinking,
    )
    if _provider == "local":
        payload["cache_prompt"] = not bool(image_paths)
    temperature, top_p = _apply_model_defaults(temperature, top_p, payload)
    payload["temperature"] = max(temperature, 0.01)
    payload["top_p"] = top_p
    if _provider != "local":
        payload["model"] = _model_id
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking
        payload["chat_template_kwargs"] = {
            "enable_thinking": enable_thinking,
        }
    if _active_registry_entry().get("disable_thinking", False):
        payload["stop"] = [
            "<think>", "<thinking>", "<|think|>", "<channel>",
            "<|channel|>",
        ]

    normalized_assist = normalize_response_assist(response_assist)
    retry_enabled = (
        _provider == "local"
        and response_assist_retry_enabled(response_assist)
    )
    use_stream = progress_callback is not None or retry_enabled
    response_data = {}
    if _provider == "anthropic":
        if progress_callback is not None:
            content = _generate_streaming_anthropic(
                api_messages,
                max_new_tokens + thinking_budget,
                max(temperature, 0.01),
                top_p,
                progress_callback=progress_callback,
                cancel_handle=cancel_handle,
            )
        else:
            content = _generate_anthropic(
                api_messages,
                max_new_tokens + thinking_budget,
                max(temperature, 0.01),
                top_p,
                cancel_handle=cancel_handle,
            )
    elif use_stream:
        progress = RequestProgress(progress_callback)
        content = ""
        for attempt in range(1, 3 if retry_enabled else 2):
            if attempt > 1:
                progress.retrying(attempt=attempt)
            else:
                progress.emit("generating", "", attempt=attempt)
            raw_content = ""
            reasoning_content = ""
            normalized_content = ""
            prefix_stripper = PrefixEchoStripper(
                assistant_prefix,
                enabled=normalized_assist.strip_assistant_prefill,
            )
            response_data = {}
            refused = False
            response = None
            attempt_payload = dict(payload)
            attempt_payload["stream"] = True
            _bind_runtime_request_budget(
                int(attempt_payload["max_tokens"]),
                multimodal=bool(image_paths),
                request_pass=attempt,
            )
            try:
                _cancellation_checkpoint(cancel_handle)
                response = requests.post(
                    f"{_server_url()}/v1/chat/completions",
                    json=attempt_payload,
                    headers=_api_headers(),
                    timeout=(10, 600),
                    stream=True,
                )
                _register_cancellable_response(cancel_handle, response)
                response.raise_for_status()
                import json as _json_mod
                for line in response.iter_lines(decode_unicode=True):
                    _cancellation_checkpoint(cancel_handle)
                    if not line or not line.startswith("data: "):
                        continue
                    encoded = line[6:]
                    if encoded.strip() == "[DONE]":
                        break
                    try:
                        chunk = _json_mod.loads(encoded)
                        if isinstance(chunk, dict):
                            for section in ("timings", "usage"):
                                values = chunk.get(section)
                                if isinstance(values, dict):
                                    response_data.setdefault(
                                        section, {},
                                    ).update(values)
                            _observe_runtime_output_metrics(
                                chunk, request_pass=attempt,
                            )
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        reasoning_token = delta.get("reasoning_content", "")
                        token = delta.get("content", "")
                        if reasoning_token:
                            reasoning_content += reasoning_token
                        if token:
                            raw_content += token
                            next_normalized = _strip_thinking_tags(raw_content)
                            if next_normalized.startswith(normalized_content):
                                visible = prefix_stripper.feed(
                                    next_normalized[len(normalized_content):],
                                )
                            else:
                                # A malformed/non-monotonic thinking marker is
                                # fail-open: preserve the normalized output.
                                prefix_stripper = PrefixEchoStripper(
                                    assistant_prefix,
                                    enabled=(
                                        normalized_assist
                                        .strip_assistant_prefill
                                    ),
                                )
                                visible = prefix_stripper.feed(next_normalized)
                            normalized_content = next_normalized
                        if token or reasoning_token:
                            progress.emit(
                                "generating" if token else "thinking",
                                visible if token else prefix_stripper.feed(""),
                                attempt=attempt,
                                meter_text=reasoning_content + raw_content,
                            )
                        if (
                            attempt == 1
                            and retry_enabled
                            and response_assist_refused(
                                prefix_stripper.feed(""),
                                response_assist,
                            )
                        ):
                            refused = True
                            break
                    except LlmRequestCancelled:
                        raise
                    except Exception:
                        continue
            except requests.exceptions.RequestException as error:
                _cancellation_checkpoint(cancel_handle)
                raise _diagnose_llm_request_failure(error) from error
            except LlmRequestCancelled:
                raise
            except Exception:
                _cancellation_checkpoint(cancel_handle)
                raise
            finally:
                close_owned_response = _unregister_cancellable_response(
                    cancel_handle, response,
                )
                close_response = getattr(response, "close", None)
                if close_owned_response and callable(close_response):
                    close_response()
                if not request_scoped_progress:
                    with _stream_lock:
                        _stream_done = True
            if refused:
                _cancellation_checkpoint(cancel_handle)
                print("[LLM] Response-assist retry 1/1")
                continue
            content = _strip_thinking_tags(prefix_stripper.finish()).strip()
            break
        usage = response_data.get("usage", {})
        timings = response_data.get("timings", {})
        final_tokens = usage.get("completion_tokens")
        if not isinstance(final_tokens, int) or isinstance(final_tokens, bool):
            final_tokens = None
        average_tps = timings.get("predicted_per_second")
        if (
            not isinstance(average_tps, (int, float))
            or isinstance(average_tps, bool)
        ):
            average_tps = None
        _cancellation_checkpoint(cancel_handle)
        progress.emit(
            "complete",
            content,
            attempt=attempt,
            done=True,
            final_tokens=final_tokens,
            average_tps=average_tps,
        )
    else:
        _bind_runtime_request_budget(
            int(payload["max_tokens"]),
            multimodal=bool(image_paths),
            request_pass=1,
        )
        response = None
        try:
            _cancellation_checkpoint(cancel_handle)
            response = requests.post(
                f"{_server_url()}/v1/chat/completions",
                json=payload,
                headers=_api_headers(),
                timeout=(10, 600),
                stream=True,
            )
            _register_cancellable_response(cancel_handle, response)
            response.raise_for_status()
            response_data = response.json()
            _cancellation_checkpoint(cancel_handle)
            _observe_runtime_output_metrics(response_data, request_pass=1)
            message = response_data["choices"][0]["message"]
        except requests.exceptions.RequestException as error:
            _cancellation_checkpoint(cancel_handle)
            raise _diagnose_llm_request_failure(error) from error
        except (KeyError, IndexError, TypeError, ValueError) as error:
            _cancellation_checkpoint(cancel_handle)
            raise RuntimeError("LLM returned an invalid chat response") from error
        finally:
            close_owned_response = _unregister_cancellable_response(
                cancel_handle, response,
            )
            close_response = getattr(response, "close", None)
            if close_owned_response and callable(close_response):
                close_response()
        raw_content = message.get("content") or ""
        content = _public_response_text(
            raw_content,
            assistant_prefix,
            strip_prefix=normalized_assist.strip_assistant_prefill,
        ).strip()
    if not content:
        raise RuntimeError("LLM returned an empty chat response")
    _cancellation_checkpoint(cancel_handle)
    if _provider != "anthropic":
        _record_response_metrics(
            response_data, multimodal=bool(image_paths),
        )
    return content


@_with_model_lease
def generate(
    prompt: str,
    system_prompt: str = "",
    max_new_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.9,
    seed: Optional[int] = None,
    image_paths: Optional[list] = None,
    thinking_budget: int = 0,
    enable_thinking: Optional[bool] = None,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    stop: Optional[list[str]] = None,
    json_schema: Optional[dict] = None,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Generate text via llama-server's OpenAI-compatible chat endpoint.

    Args:
        image_paths: Optional list of file paths to images. If provided and
            the model supports vision, images are sent as multimodal content.
        thinking_budget: Extra tokens reserved for model reasoning/thinking.
            Added on top of max_new_tokens so thinking doesn't eat into
            the content budget.
        enable_thinking: If False, disables Qwen3.5's thinking mode via the
            --jinja chat template. If None, uses model default (thinking on).
        json_schema: Optional JSON Schema dict. When set (local llama-server
            only), the output is grammar-constrained to schema-valid JSON —
            the sampler masks every token that would break the schema, so
            the model physically cannot emit prose, markdown fences, or the
            repeat-loop garbage that breaks structured planning passes.
    """
    _cancellation_checkpoint(cancel_handle)
    if not is_loaded():
        raise RuntimeError("LLM not loaded. Call load_model() first.")

    # A request-scoped callback opts this otherwise synchronous API into the
    # shared SSE path. No streamed text is added to durable/global status by
    # the callback facility itself.
    if progress_callback is not None:
        return generate_streaming(
            prompt=prompt,
            system_prompt=system_prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=-1 if seed is None else seed,
            image_paths=image_paths,
            thinking_budget=thinking_budget,
            enable_thinking=enable_thinking,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            stop=stop,
            json_schema=json_schema,
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )

    # Cancel idle timer during active request — prevents auto-unload mid-generation.
    # Timer is reset at the END of the request (after response is received).
    _cancel_idle_timer()

    # Grammar-constrained JSON mode requires thinking OFF. The grammar
    # constrains sampling from the FIRST token, so any thinking the chat
    # template force-opens (Gemma's `<|think|>`, Qwen's `<think>`) would
    # trap the model: it could only emit schema-JSON, never the think-close
    # marker, and the parser would file the entire output under
    # reasoning_content with empty content. Forcing enable_thinking=False
    # here makes _prepare_thinking skip every activation path.
    if json_schema is not None:
        enable_thinking = False
        thinking_budget = 0

    # Per-model thinking mode (Gemma vs Qwen)
    system_prompt, enable_thinking, thinking_budget = _prepare_thinking(system_prompt, enable_thinking, thinking_budget)

    total_tokens = max_new_tokens + thinking_budget

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    user_content, multimodal = _build_user_content(prompt, image_paths)
    messages.append({"role": "user", "content": user_content})

    payload = {
        "messages": messages,
        "max_tokens": total_tokens,
    }
    assistant_prefix = apply_local_assistant_prefill(
        messages,
        payload,
        options=response_assist,
        provider=_provider,
        structured=json_schema is not None,
        enable_thinking=enable_thinking,
    )
    if _provider == "local":
        payload["cache_prompt"] = not bool(image_paths)
    temperature, top_p = _apply_request_sampling(
        payload,
        temperature,
        top_p,
        frequency_penalty,
        presence_penalty,
    )
    if seed is not None and seed >= 0:
        payload["seed"] = seed
    # Qwen thinking mode via chat template kwargs (Gemma handled by _prepare_thinking)
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
    # Hard stop sequences. The Director Pass 3 polish path uses this with
    # `<think>` to abort generation the moment a Qwen3.5/3.6 model tries
    # to enter thinking mode despite enable_thinking=False being requested.
    # That cap caps wasted tokens at ~1 (the `<think>` token itself) instead
    # of the previous ~1024 the model would burn before producing nothing.
    #
    # For registry entries with `disable_thinking: True`, automatically
    # inject thinking-marker stop tokens so every call gets the same
    # protection — covers Gemma 4 fine-tunes that auto-activate thinking
    # mode despite chat_template_kwargs saying otherwise. Both Qwen-style
    # (`<think>`) and Gemma-style (`<channel>`, `<|think|>`) markers are
        # included because Gemma variants can emit the latter format.
    combined_stop = list(stop) if stop else []
    if _active_registry_entry().get("disable_thinking", False):
        for tok in ("<think>", "<thinking>", "<|think|>", "<channel>", "<|channel|>"):
            if tok not in combined_stop:
                combined_stop.append(tok)
    if combined_stop:
        payload["stop"] = combined_stop

    # Grammar-constrained JSON output. llama-server compiles the schema to
    # a GBNF grammar server-side ({"type": "json_object", "schema": ...} is
    # the long-standing llama.cpp extension form). Local provider only —
    # remote OpenAI-compatible endpoints vary in which response_format
    # flavor they accept, so we degrade to an unconstrained call there.
    if json_schema is not None:
        if _provider == "local":
            payload["response_format"] = {"type": "json_object", "schema": json_schema}
        else:
            print(f"[LLM] json_schema requested but provider={_provider} — sending unconstrained (grammar is local llama-server only)")

    if _provider == "anthropic":
        return _generate_anthropic(
            messages,
            total_tokens,
            max(temperature, 0.01),
            top_p,
            cancel_handle=cancel_handle,
        )

    retry_enabled = (
        _provider == "local"
        and response_assist_retry_enabled(response_assist)
    )
    data = {}
    raw_content = ""
    for attempt in range(1, 3 if retry_enabled else 2):
        _cancellation_checkpoint(cancel_handle)
        attempt_payload = dict(payload)
        if attempt > 1 and isinstance(payload.get("seed"), int):
            attempt_payload["seed"] = payload["seed"] + attempt - 1
        _bind_runtime_request_budget(
            int(attempt_payload["max_tokens"]),
            multimodal=multimodal,
            request_pass=attempt,
        )
        resp = None
        try:
            _cancellation_checkpoint(cancel_handle)
            resp = requests.post(
                f"{_server_url()}/v1/chat/completions",
                json=attempt_payload,
                headers=_api_headers(),
                # (connect, read): fail fast if the server socket is gone;
                # allow a long read for actual generation.
                timeout=(10, 600),
                stream=True,
            )
            _register_cancellable_response(cancel_handle, resp)
            resp.raise_for_status()
            data = resp.json()
            _cancellation_checkpoint(cancel_handle)
            _observe_runtime_output_metrics(data, request_pass=attempt)
            raw_content = data["choices"][0]["message"]["content"] or ""
            public_content = _public_response_text(
                raw_content,
                assistant_prefix,
                strip_prefix=normalize_response_assist(
                    response_assist,
                ).strip_assistant_prefill,
            )
        except requests.exceptions.RequestException as e:
            # A dead subprocess surfaces here as a ConnectionError; translate it
            # into an actionable error naming the real cause (see the helper).
            _cancellation_checkpoint(cancel_handle)
            raise _diagnose_llm_request_failure(e) from e
        except LlmRequestCancelled:
            raise
        except Exception:
            _cancellation_checkpoint(cancel_handle)
            raise
        finally:
            close_owned_response = _unregister_cancellable_response(
                cancel_handle, resp,
            )
            close_response = getattr(resp, "close", None)
            if close_owned_response and callable(close_response):
                close_response()
        if attempt == 1 and retry_enabled and response_assist_refused(
            public_content, response_assist,
        ):
            _cancellation_checkpoint(cancel_handle)
            print("[LLM] Response-assist retry 1/1")
            continue
        break
    _cancellation_checkpoint(cancel_handle)
    finish_reason = data["choices"][0].get("finish_reason", "unknown")
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", "?")
    completion_tokens = usage.get("completion_tokens", "?")
    print(f"[LLM] Response: {completion_tokens} tokens generated (prompt={prompt_tokens}, finish={finish_reason})")
    if not raw_content:
        print(f"[LLM] WARNING: Server returned empty content despite generating {completion_tokens} tokens (model likely consumed all tokens on internal reasoning)")
        # Check if reasoning_content is available (llama-server may separate it)
        reasoning = data["choices"][0]["message"].get("reasoning_content", "")
        if reasoning:
            print(f"[LLM] Reasoning content detected ({len(reasoning)} chars) — model used thinking mode. reasoning_budget=0 may not be active.")

    content = public_content

    if not content.strip() and raw_content:
        print(
            f"[LLM] WARNING: Model spent all {completion_tokens} tokens on "
            f"reasoning with no answer content ({len(raw_content)} raw chars)"
        )

    _cancellation_checkpoint(cancel_handle)
    _record_response_metrics(
        data, multimodal=multimodal,
    )
    return content.strip()


def get_stream_status() -> dict:
    """Return current streaming state for polling."""
    with _stream_lock:
        return {"text": _stream_buffer, "done": _stream_done}


@_with_model_lease
@_with_stream_done_finally
def generate_streaming(
    prompt: str,
    system_prompt: str = "",
    max_new_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.9,
    seed: int = -1,
    image_paths: list = None,
    thinking_budget: int = 0,
    enable_thinking: bool = None,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    stop: Optional[list[str]] = None,
    json_schema: Optional[dict] = None,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Generate text using SSE streaming, populating the stream buffer in real-time.

    Same interface as generate(), but tokens appear in _stream_buffer as they arrive.
    Returns the final stripped content (same as generate()).

    Args:
        thinking_budget: Extra tokens reserved for model reasoning/thinking.
            Added on top of max_new_tokens so thinking doesn't eat into
            the content budget. Set to 0 to use max_new_tokens as-is.
        json_schema: Optional JSON Schema dict — grammar-constrains the
            output to schema-valid JSON on local llama-server. Forces
            thinking OFF (see generate() for the rationale).
    """
    global _stream_buffer, _stream_done, _last_system_prompt, _last_user_prompt, _last_thinking_text

    _cancellation_checkpoint(cancel_handle)
    if not is_loaded():
        raise RuntimeError("LLM not loaded. Call load_model() first.")

    request_scoped_progress = callable(progress_callback)

    # Grammar-constrained JSON mode requires thinking OFF — same rationale
    # as the matching block in generate(): the grammar masks sampling from
    # the first token, so a force-opened think block could never close.
    if json_schema is not None:
        enable_thinking = False
        thinking_budget = 0

    # Per-model thinking mode (Gemma vs Qwen)
    system_prompt, enable_thinking, thinking_budget = _prepare_thinking(system_prompt, enable_thinking, thinking_budget)

    # Store for pipeline dashboard capture (system + user prompt both,
    # so the dashboard can render the full LLM input).
    if not request_scoped_progress:
        _last_system_prompt = system_prompt
        _last_user_prompt = prompt
        _last_thinking_text = ""

    # Cancel idle timer during active request — prevents auto-unload mid-streaming.
    # Timer is reset at the END of the request (after streaming completes).
    _cancel_idle_timer()

    total_tokens = max_new_tokens + thinking_budget

    if not request_scoped_progress:
        with _stream_lock:
            _stream_buffer = ""
            _stream_done = False

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    user_content, multimodal = _build_user_content(prompt, image_paths)
    messages.append({"role": "user", "content": user_content})

    payload = {
        "messages": messages,
        "max_tokens": total_tokens,
        "stream": True,
    }
    assistant_prefix = apply_local_assistant_prefill(
        messages,
        payload,
        options=response_assist,
        provider=_provider,
        structured=json_schema is not None,
        enable_thinking=enable_thinking,
    )
    if _provider == "local":
        payload["cache_prompt"] = not bool(image_paths)
    temperature, top_p = _apply_request_sampling(
        payload,
        temperature,
        top_p,
        frequency_penalty,
        presence_penalty,
    )
    if seed is not None and seed >= 0:
        payload["seed"] = seed
    # Qwen thinking mode via chat template kwargs (Gemma handled by _prepare_thinking)
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

    # Auto-inject thinking-marker stop tokens for `disable_thinking: True`
    # models. Mirrors the same protection in generate() — see comment there.
    # Critical for Gemma 4 fine-tunes whose embedded chat templates ignore
    # the enable_thinking=false kwarg and auto-enter reasoning mode anyway,
    # burning the entire token budget on `reasoning_content` and returning
    # empty `content`. Stopping on the marker token caps wasted tokens at 1.
    combined_stop = list(stop) if stop else []
    if _active_registry_entry().get("disable_thinking", False):
        for token in (
            "<think>", "<thinking>", "<|think|>", "<channel>",
            "<|channel|>",
        ):
            if token not in combined_stop:
                combined_stop.append(token)
    if combined_stop:
        payload["stop"] = combined_stop

    # Grammar-constrained JSON output — local llama-server only (see the
    # matching block in generate() for the full rationale).
    if json_schema is not None:
        if _provider == "local":
            payload["response_format"] = {"type": "json_object", "schema": json_schema}
        else:
            print(f"[LLM] json_schema requested but provider={_provider} — sending unconstrained (grammar is local llama-server only)")

    # Diagnostic — log every payload field except `messages` so we can
    # compare what Maestro sends to llama-server vs what LM Studio sends
    # for the same model. The messages array gets summarized (length per
    # role) instead of dumped, since system prompts can be multi-KB and
    # multimodal content includes base64-encoded images.
    try:
        _diag = {k: v for k, v in payload.items() if k != "messages"}
        # The compiled schema can be multi-KB — log its size, not its body.
        if "response_format" in _diag:
            _diag["response_format"] = f"<json grammar, {len(str(_diag['response_format']))} chars>"
        _msg_summary = []
        for _m in messages:
            _role = _m.get("role", "?")
            _content = _m.get("content")
            if isinstance(_content, str):
                _msg_summary.append(f"{_role}({len(_content)}c)")
            elif isinstance(_content, list):
                _parts = []
                for _p in _content:
                    _t = _p.get("type", "?")
                    if _t == "text":
                        _parts.append(f"text({len(_p.get('text',''))}c)")
                    elif _t == "image_url":
                        _parts.append("image")
                    else:
                        _parts.append(_t)
                _msg_summary.append(f"{_role}[{','.join(_parts)}]")
            else:
                _msg_summary.append(_role)
        print(f"[LLM] Payload to llama-server: {_diag} | messages=[{', '.join(_msg_summary)}]")
    except Exception:
        pass

    if _provider == "anthropic":
        return _generate_streaming_anthropic(
            messages, total_tokens, max(temperature, 0.01), top_p,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )

    normalized_assist = normalize_response_assist(response_assist)
    retry_enabled = (
        _provider == "local"
        and response_assist_retry_enabled(response_assist)
    )
    progress = RequestProgress(progress_callback)
    raw_content = ""
    reasoning_content = ""
    completed_stream_metrics = {}
    stream_completed = False
    for attempt in range(1, 3 if retry_enabled else 2):
        _cancellation_checkpoint(cancel_handle)
        if attempt > 1:
            progress.retrying(attempt=attempt)
            if not request_scoped_progress:
                with _stream_lock:
                    _stream_buffer = ""
        else:
            progress.emit("generating", "", attempt=attempt)
        raw_content = ""
        reasoning_content = ""
        normalized_content = ""
        prefix_stripper = PrefixEchoStripper(
            assistant_prefix,
            enabled=normalized_assist.strip_assistant_prefill,
        )
        completed_stream_metrics = {}
        stream_completed = False
        refused = False
        resp = None
        attempt_payload = dict(payload)
        if attempt > 1 and isinstance(payload.get("seed"), int):
            attempt_payload["seed"] = payload["seed"] + attempt - 1
        _bind_runtime_request_budget(
            int(attempt_payload["max_tokens"]),
            multimodal=multimodal,
            request_pass=attempt,
        )
        try:
            _cancellation_checkpoint(cancel_handle)
            resp = requests.post(
                f"{_server_url()}/v1/chat/completions",
                json=attempt_payload,
                headers=_api_headers(),
                timeout=(10, 600),
                stream=True,
            )
            _register_cancellable_response(cancel_handle, resp)
            resp.raise_for_status()

            import json as _json_mod
            for line in resp.iter_lines(decode_unicode=True):
                _cancellation_checkpoint(cancel_handle)
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]  # strip "data: "
                if data_str.strip() == "[DONE]":
                    stream_completed = True
                    break
                try:
                    chunk = _json_mod.loads(data_str)
                    if isinstance(chunk, dict):
                        for section in ("timings", "usage"):
                            values = chunk.get(section)
                            if isinstance(values, dict):
                                completed_stream_metrics.setdefault(
                                    section, {},
                                ).update(values)
                        _observe_runtime_output_metrics(
                            chunk, request_pass=attempt,
                        )
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    reasoning_token = delta.get("reasoning_content", "")
                    if reasoning_token:
                        reasoning_content += reasoning_token

                    token = delta.get("content", "")
                    if token:
                        raw_content += token
                        next_normalized = _strip_thinking_tags(raw_content)
                        if next_normalized.startswith(normalized_content):
                            visible_content = prefix_stripper.feed(
                                next_normalized[len(normalized_content):],
                            )
                        else:
                            prefix_stripper = PrefixEchoStripper(
                                assistant_prefix,
                                enabled=(
                                    normalized_assist.strip_assistant_prefill
                                ),
                            )
                            visible_content = prefix_stripper.feed(
                                next_normalized,
                            )
                        normalized_content = next_normalized

                    if reasoning_token or token:
                        legacy_display = ""
                        if reasoning_content:
                            legacy_display = (
                                f"<think>{reasoning_content}</think>\n"
                            )
                        else:
                            inline_blocks = _inline_thinking_blocks(raw_content)
                            if inline_blocks:
                                legacy_display = inline_blocks + "\n"
                        public_display = (
                            visible_content
                            if token
                            else prefix_stripper.feed("")
                        )
                        legacy_display += public_display
                        if not request_scoped_progress:
                            with _stream_lock:
                                _stream_buffer = legacy_display
                        progress.emit(
                            "generating" if token else "thinking",
                            public_display,
                            attempt=attempt,
                            meter_text=reasoning_content + raw_content,
                        )

                    # Only an explicitly enabled local retry evaluates the
                    # cumulative generated response. Prompts/messages are
                    # never passed to the detector.
                    if (
                        attempt == 1
                        and retry_enabled
                        and response_assist_refused(
                            prefix_stripper.feed(""),
                            response_assist,
                        )
                    ):
                        refused = True
                        break
                except LlmRequestCancelled:
                    raise
                except Exception:
                    continue

        except requests.exceptions.RequestException as e:
            # Server socket died mid-stream (common: subprocess crash). Surface
            # the real cause so the Director run reports it instead of hanging.
            if not request_scoped_progress:
                with _stream_lock:
                    _stream_done = True
            _cancellation_checkpoint(cancel_handle)
            raise _diagnose_llm_request_failure(e) from e
        except Exception:
            if not request_scoped_progress:
                with _stream_lock:
                    _stream_done = True
            _cancellation_checkpoint(cancel_handle)
            raise
        finally:
            close_owned_response = _unregister_cancellable_response(
                cancel_handle, resp,
            )
            close_response = getattr(resp, "close", None)
            if close_owned_response and callable(close_response):
                close_response()

        if refused:
            _cancellation_checkpoint(cancel_handle)
            print("[LLM] Response-assist retry 1/1")
            continue
        break

    # Capture thinking text for the pipeline dashboard. Two sources:
    #   1. reasoning_content — populated by chat templates that emit
    #      thinking via the OpenAI-style `reasoning_content` delta field
    #      (Qwen 3.x with --jinja, base Gemma 4 if the template fires).
    #   2. Inline <|channel>thought\n...<channel|> markers in raw_content
    #      — emitted by Gemma 4 Heretic and similar fine-tunes whose
    #      chat templates don't extract thinking into reasoning_content.
    # Prefer (1) when present, fall back to (2).
    _cancellation_checkpoint(cancel_handle)
    inline_gemma_match = _GEMMA_THINKING_INNER_RE.search(raw_content)
    inline_gemma_thinking = inline_gemma_match.group(1) if inline_gemma_match else ""
    if not request_scoped_progress:
        _last_thinking_text = reasoning_content or inline_gemma_thinking
    print(
        f"[LLM] Streaming complete: {len(raw_content)} chars, "
        f"reasoning_content: {len(reasoning_content)} chars, "
        f"gemma_inline_thinking: {len(inline_gemma_thinking)} chars"
    )

    # Build full raw for the legacy dashboard (includes captured thinking),
    # while public callbacks/detection use only normalized response text.
    thinking_raw = ""
    if reasoning_content:
        thinking_raw = f"<think>{reasoning_content}</think>\n"
    else:
        inline_blocks = _inline_thinking_blocks(raw_content)
        if inline_blocks:
            thinking_raw = inline_blocks + "\n"
    full_raw = thinking_raw + prefix_stripper.finish()

    # Strip thinking/reasoning blocks for the return value
    content = _strip_thinking_tags(full_raw)

    if not request_scoped_progress:
        with _stream_lock:
            _stream_buffer = full_raw  # keep full raw for the UI to show thinking
            _stream_done = True

    _cancellation_checkpoint(cancel_handle)
    if stream_completed and completed_stream_metrics:
        _record_response_metrics(
            completed_stream_metrics,
            multimodal=multimodal,
        )
    usage = completed_stream_metrics.get("usage", {})
    timings = completed_stream_metrics.get("timings", {})
    final_tokens = usage.get("completion_tokens")
    if not isinstance(final_tokens, int) or isinstance(final_tokens, bool):
        final_tokens = None
    average_tps = timings.get("predicted_per_second")
    if not isinstance(average_tps, (int, float)) or isinstance(average_tps, bool):
        average_tps = None
    final_content = content.strip()
    _cancellation_checkpoint(cancel_handle)
    progress.emit(
        "complete",
        final_content,
        attempt=attempt,
        done=True,
        final_tokens=final_tokens,
        average_tps=average_tps,
    )
    return final_content


def _generate_anthropic(
    messages: list,
    max_tokens: int,
    temperature: float,
    top_p: float,
    *,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Non-streaming generation via Anthropic Messages API."""
    # Anthropic uses system as a top-level param, not in messages
    system_text = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            api_messages.append(m)

    payload = {
        "model": _model_id,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "messages": api_messages,
    }
    if system_text:
        payload["system"] = system_text

    resp = None
    try:
        _cancellation_checkpoint(cancel_handle)
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=_api_headers(),
            timeout=600,
            stream=True,
        )
        _register_cancellable_response(cancel_handle, resp)
        resp.raise_for_status()
        data = resp.json()
        _cancellation_checkpoint(cancel_handle)
    except requests.exceptions.RequestException:
        _cancellation_checkpoint(cancel_handle)
        raise
    except LlmRequestCancelled:
        raise
    except Exception:
        _cancellation_checkpoint(cancel_handle)
        raise
    finally:
        close_owned_response = _unregister_cancellable_response(
            cancel_handle, resp,
        )
        close_response = getattr(resp, "close", None)
        if close_owned_response and callable(close_response):
            close_response()

    # Anthropic response: {"content": [{"type": "text", "text": "..."}], ...}
    raw_content = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            raw_content += block.get("text", "")

    usage = data.get("usage", {})
    _cancellation_checkpoint(cancel_handle)
    print(f"[LLM/Anthropic] Response: {usage.get('output_tokens', '?')} tokens (prompt={usage.get('input_tokens', '?')})")

    content = _strip_thinking_tags(raw_content)
    return content.strip()


def _generate_streaming_anthropic(
    messages: list,
    max_tokens: int,
    temperature: float,
    top_p: float,
    *,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Streaming generation via Anthropic Messages API with SSE."""
    global _stream_buffer, _stream_done
    request_scoped_progress = callable(progress_callback)

    system_text = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            api_messages.append(m)

    payload = {
        "model": _model_id,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "messages": api_messages,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text

    _cancellation_checkpoint(cancel_handle)
    progress = RequestProgress(progress_callback)
    progress.emit("generating", "", attempt=1)
    raw_content = ""
    resp = None
    try:
        _cancellation_checkpoint(cancel_handle)
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=_api_headers(),
            timeout=600,
            stream=True,
        )
        _register_cancellable_response(cancel_handle, resp)
        resp.raise_for_status()

        import json
        for line in resp.iter_lines():
            _cancellation_checkpoint(cancel_handle)
            if not line:
                continue
            line_str = line.decode("utf-8", errors="replace")
            if not line_str.startswith("data: "):
                continue
            json_str = line_str[6:]
            if json_str.strip() == "[DONE]":
                break
            try:
                event = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            if event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    raw_content += text
                    if not request_scoped_progress:
                        with _stream_lock:
                            _stream_buffer = raw_content
                    progress.emit("generating", raw_content, attempt=1)

    except LlmRequestCancelled:
        raise
    except Exception as e:
        _cancellation_checkpoint(cancel_handle)
        print(f"[LLM/Anthropic] Streaming error: {e}")
        if not request_scoped_progress:
            with _stream_lock:
                _stream_buffer = raw_content or f"Error: {e}"
                _stream_done = True
        return ""
    finally:
        close_owned_response = _unregister_cancellable_response(
            cancel_handle, resp,
        )
        close_response = getattr(resp, "close", None)
        if close_owned_response and callable(close_response):
            close_response()
        if not request_scoped_progress:
            with _stream_lock:
                _stream_done = True

    _cancellation_checkpoint(cancel_handle)
    content = _strip_thinking_tags(raw_content)

    if not request_scoped_progress:
        with _stream_lock:
            _stream_buffer = raw_content
            _stream_done = True

    final_content = content.strip()
    _cancellation_checkpoint(cancel_handle)
    progress.emit("complete", final_content, attempt=1, done=True)
    return final_content


def _build_enhance_user_prompt(
    prompt, mode, duration_seconds, window_count, window_size_seconds,
    preserve_global_timeline=False, h3_context_ir=False, h3_ref2va=False,
):
    """Prefix a prompt with duration and architecture-appropriate structure.

    H3 receives one coherent global-timeline contract. Other video families
    retain the legacy per-window paragraph contract. Shared by guide-based and
    raw per-model enhancement paths.
    """
    if duration_seconds and mode in ("video", "avatar"):
        parts = [f"Duration: {duration_seconds} seconds"]
        if h3_context_ir:
            parts.append(
                "one coherent global timeline spanning the complete Duration; "
                "keep identities, literal dialogue, speaker IDs, sound, music, "
                "authored global timestamps, and cuts consistent and in order"
            )
            if not h3_ref2va:
                parts.append(
                    "define every authored visible entity once in global "
                    "subject_definitions and reference stable Subject IDs or "
                    "names in shot records; for distinct authored beats, cuts, "
                    "or timestamp boundaries use multiple naturally unequal "
                    "records, while an explicitly sustained one-take remains "
                    "one record"
                )
            return f"[{', '.join(parts)}]\n\n{prompt}"
        if preserve_global_timeline:
            parts.append(
                "complete global timeline; keep every timestamp token exactly "
                "unchanged and in the same order; do not split or rebase it"
            )
            return f"[{', '.join(parts)}]\n\n{prompt}"
        if window_count and window_count > 1:
            parts.append(f"{window_count} sliding windows of ~{window_size_seconds}s each")
            # State the COUNT explicitly, not just the ratio — a fine-tuned
            # enhancer (e.g. Sulphur) can read "one paragraph per window" as
            # "one paragraph" and collapse a 2-window prompt into a single one.
            parts.append(
                f"Write EXACTLY {window_count} paragraphs (one per window), "
                "separated by newlines"
            )
        return f"[{', '.join(parts)}]\n\n{prompt}"
    return prompt


def _clean_enhancer_output(text):
    """Strip a fine-tuned enhancer's spurious leading style-framing clause —
    e.g. "The video, rendered in a high-quality 3D animation style," — which
    the Sulphur enhancer prepends even to live-action / real-people content,
    wrongly forcing a 3D/animation look. Applied per line so it also cleans
    multi-paragraph (multi-window) output. Conservative: only fires on a
    leading "The <video/clip/...> ... rendered in ... <style keyword> ...,"."""
    if not text:
        return text
    import re
    pat = re.compile(
        r'^\s*the\s+(?:video|clip|scene|footage|animation)\b[^.]*?'
        r'\brendered\s+in\b[^.]*?'
        r'\b(?:style|animation|cgi|3d|cg|render(?:ing)?)\b[^.]*?[,.]\s+',
        re.IGNORECASE,
    )
    out = []
    for line in text.split("\n"):
        new = pat.sub("", line, count=1)
        if new != line and new:
            new = new[0].upper() + new[1:]  # re-capitalize after the strip
        out.append(new)
    return "\n".join(out).strip()


_H3_EXACT_DIALOGUE_RE = re.compile(
    r"<d>\s*\[[^\]\r\n]+\]\s+.*?</d>", re.IGNORECASE | re.DOTALL,
)
_H3_REFERENCE_LABEL_RE = re.compile(
    r"<(?:Subject|Picture|Video|Audio)\s+\d+>", re.IGNORECASE,
)
_H3_SPEAKER_ID_RE = re.compile(r"\(S\d+\)", re.IGNORECASE)
_H3_LOOSE_TIME = r"(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?"
_H3_LOOSE_RANGE_RE = re.compile(
    rf"^\s*(?:\[?\s*(?:Shot|Scene)\s+\d+\s*\]?\s*)?"
    rf"\[?\s*(?P<start>{_H3_LOOSE_TIME})\s*s?\s*"
    rf"(?:-|–|—|\bto\b)\s*(?P<end>{_H3_LOOSE_TIME})\s*s?\s*"
    r"\]?\s*:?[ \t]*(?P<text>.+?)\s*$",
    re.IGNORECASE,
)


def _clean_h3_context_ir_output(text: str) -> str:
    """Remove only an outer response wrapper; H3 record text is opaque."""

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = re.fullmatch(r"```(?:text|plaintext)?\s*\n(.*?)\n```", value, re.DOTALL)
    if fenced:
        value = fenced.group(1).strip()
    value = re.sub(
        r"^(?:Enhanced Prompt|Prompt|Output|Result)\s*:\s*"
        r"(?=(?:subject_definitions|integrated_multimodal_description)\s*:)",
        "",
        value,
        count=1,
        flags=re.IGNORECASE,
    )
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def _h3_timeline_boundaries(value: str) -> list[float]:
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    _, events = parse_global_timeline_prompt(value)
    boundaries: list[float] = []
    for event in sorted(events, key=lambda item: int(item.get("order", 0))):
        start = float(event.get("start", 0.0))
        end = float(event.get("end", start))
        boundaries.append(start)
        if not math.isclose(end, start, abs_tol=1e-9):
            boundaries.append(end)
    return boundaries


def _ordered_float_subsequence(needles: Sequence[float], values: Sequence[float]) -> bool:
    cursor = 0
    for needle in needles:
        while cursor < len(values) and not math.isclose(
            float(needle), float(values[cursor]), abs_tol=0.001,
        ):
            cursor += 1
        if cursor >= len(values):
            return False
        cursor += 1
    return True


def _h3_locked_content_errors(source: str, candidate: str) -> list[str]:
    """Check only literal authored anchors, never creative subject matter."""

    errors: list[str] = []
    source_dialogue = _H3_EXACT_DIALOGUE_RE.findall(source or "")
    if source_dialogue and _H3_EXACT_DIALOGUE_RE.findall(candidate or "") != source_dialogue:
        errors.append("literal dialogue blocks changed")
    for label_re, label in (
        (_H3_REFERENCE_LABEL_RE, "reference labels"),
        (_H3_SPEAKER_ID_RE, "speaker IDs"),
    ):
        required = [match.group(0) for match in label_re.finditer(source or "")]
        present = [match.group(0) for match in label_re.finditer(candidate or "")]
        if any(present.count(value) < required.count(value) for value in set(required)):
            errors.append(f"authored {label} changed")
    source_times = _h3_timeline_boundaries(source)
    if source_times and not _ordered_float_subsequence(
        source_times, _h3_timeline_boundaries(candidate),
    ):
        errors.append("authored timestamp values or order changed")
    quoted = re.findall(r"[\"“]([^\"”\r\n]+)[\"”]", source or "")
    if any(fragment not in candidate for fragment in quoted):
        errors.append("quoted authored text changed")
    errors.extend(_h3_added_entity_errors(source, candidate))
    source_cuts = len(re.findall(
        r"\b(?:cut\s+to|scene\s+change|location\s+change|time\s+jump)\b",
        source or "",
        re.IGNORECASE,
    ))
    candidate_cuts = len(re.findall(
        r"\b(?:cut\s+to|scene\s+change|location\s+change|time\s+jump)\b",
        candidate or "",
        re.IGNORECASE,
    ))
    if candidate_cuts > source_cuts:
        errors.append("candidate invented an unauthored cut or scene change")
    errors.extend(_h3_event_association_errors(source, candidate))
    return errors


def _h3_added_entity_errors(source: str, candidate: str) -> list[str]:
    """Reject candidate subject identities that are absent from the request."""

    from services.director.h3_dialogue import (
        _extract_h3_fields,
        _h3_subject_identity_aliases,
        _parse_h3_subject_definitions,
    )

    definitions = _extract_h3_fields(candidate).get("subject_definitions", "")
    entries = _parse_h3_subject_definitions(definitions)
    source_compact = " ".join(str(source or "").split()).casefold()
    errors: list[str] = []
    for entry in entries:
        aliases: list[str] = []
        for alias in _h3_subject_identity_aliases(entry):
            normalized = " ".join(alias.split()).strip().casefold()
            if normalized:
                aliases.append(normalized)
                aliases.append(re.sub(r"^(?:the|a|an)\s+", "", normalized))
        aliases = list(dict.fromkeys(alias for alias in aliases if alias))
        if entry["label"].casefold() in source_compact or any(
            re.search(
                rf"(?<![\w]){re.escape(alias)}(?![\w])",
                source_compact,
            )
            for alias in aliases
        ):
            continue
        errors.append(
            f"candidate invented unauthored entity {entry['label']}"
        )
    return errors


def _h3_source_record_ranges(value: str) -> list[tuple[float, float]]:
    """Read only explicit source ranges; prose is never treated as a cut."""

    from services.director.h3_dialogue import (
        _H3_CANONICAL_RECORD_RE,
        _extract_h3_fields,
    )

    fields = _extract_h3_fields(value)
    visual = (
        fields.get("integrated_multimodal_description")
        or fields.get("detailed_description")
        or value
    )
    ranges: list[tuple[float, float]] = []
    for raw_line in str(visual or "").splitlines():
        line = raw_line.strip()
        canonical = _H3_CANONICAL_RECORD_RE.fullmatch(line)
        if canonical:
            start = _h3_time_value(canonical.group("start"))
            end = _h3_time_value(canonical.group("end"))
            if start is not None and end is not None and end > start:
                ranges.append((start, end))
            continue
        loose = _H3_LOOSE_RANGE_RE.fullmatch(line)
        if loose:
            start = _h3_time_value(loose.group("start"))
            end = _h3_time_value(loose.group("end"))
            if start is not None and end is not None and end > start:
                ranges.append((start, end))
    return ranges


def _h3_candidate_record_ranges(value: str) -> list[tuple[float, float]]:
    """Extract the final Base records without repairing or interpreting them."""

    from services.director.h3_dialogue import _H3_CANONICAL_RECORD_RE, _extract_h3_fields

    fields = _extract_h3_fields(value)
    visual = fields.get("integrated_multimodal_description", "")
    ranges: list[tuple[float, float]] = []
    for raw_line in visual.splitlines():
        match = _H3_CANONICAL_RECORD_RE.fullmatch(raw_line.strip())
        if not match:
            continue
        start = _h3_time_value(match.group("start"))
        end = _h3_time_value(match.group("end"))
        if start is not None and end is not None and end > start:
            ranges.append((start, end))
    return ranges


def _h3_authored_beat_count(source: str) -> int:
    """Count only explicit, meaningful multi-beat evidence in a request."""

    from services.director.h3_dialogue import _extract_h3_fields

    fields = _extract_h3_fields(source)
    text = str(
        fields.get("integrated_multimodal_description")
        or fields.get("detailed_description")
        or source
    )
    explicit_ranges = _h3_source_record_ranges(source)
    count = len(explicit_ranges)
    count = max(count, len(re.findall(r"\[\s*(?:Shot|Scene)\s+\d+", text, re.IGNORECASE)))
    beat_numbers = [
        int(number) for number in re.findall(
            r"\b(?:beat|step|phase)\s+([1-9]\d*)\b", text, re.IGNORECASE,
        )
    ]
    if beat_numbers:
        count = max(count, max(beat_numbers))
    if re.search(r"\b(?:first|initially|starts?|begins?)\b", text, re.IGNORECASE):
        if re.search(r"\b(?:then|next|after that|later|finally|ends?|lastly)\b", text, re.IGNORECASE):
            count = max(count, 3 if re.search(
                r"\b(?:finally|lastly|ends?)\b", text, re.IGNORECASE,
            ) else 2)
    transitions = re.findall(
        r"\b(?:then|next|after that|later|finally|lastly)\b",
        text,
        re.IGNORECASE,
    )
    if transitions and not re.search(r"\bif\b[^.?!]{0,80}\bthen\b", text, re.IGNORECASE):
        count = max(count, 1 + len(transitions))
    authored_time_markers = re.findall(
        r"\bAt\s+(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?\s*seconds?\b|"
        r"\b(?:\d+(?:\.\d+)?\s*s)\b",
        text,
        re.IGNORECASE,
    )
    if len(authored_time_markers) >= 2:
        count = max(count, len(authored_time_markers))
    if re.search(r"\b(?:cut\s+to|scene\s+change|location\s+change|time\s+jump)\b", text, re.IGNORECASE):
        count = max(count, 2)
    named_count = re.search(
        r"\b(?:two|three|four|five|six|several|multiple)\s+"
        r"(?:distinct\s+)?(?:beats?|moments?|actions?|events?)\b",
        text,
        re.IGNORECASE,
    )
    if named_count:
        count = max(count, {
            "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "several": 2, "multiple": 2,
        }[named_count.group(1).casefold()])
    return count


def _h3_source_structure_errors(
    source: str,
    candidate: str,
    *,
    duration_seconds: Optional[float],
) -> list[str]:
    """Reject Base rewrites that erase explicit beats or violate one-takes."""

    source_text = str(source or "")
    candidate_ranges = _h3_candidate_record_ranges(candidate)
    one_take = bool(re.search(
        r"\b(?:one|single|sustained|continuous|unbroken|uninterrupted)\s+"
        r"(?:(?:sustained|continuous|unbroken|uninterrupted)\s+)?"
        r"(?:one[- ]?)?take\b|"
        r"\b(?:one|single)\s+(?:sustained|continuous|unbroken|uninterrupted)\s+"
        r"(?:(?:camera\s+)?shot|(?:visual|camera)\s+composition)\b|"
        r"\b(?:sustained|continuous|unbroken|uninterrupted)\s+"
        r"(?:(?:camera\s+)?shot|(?:visual|camera)\s+composition)\b|"
        r"\b(?:one|single)\s+(?:visual|camera)\s+composition\b|"
        r"\bwithout\s+(?:a\s+)?cut\b|\bno\s+cuts?\b",
        source_text,
        re.IGNORECASE,
    ))
    if one_take:
        if len(candidate_ranges) != 1:
            return [
                "explicit sustained one-take must remain exactly one canonical record"
            ]
        # Chronological action words inside an explicitly continuous take are
        # internal beats, not permission to manufacture record boundaries.
        return []

    source_ranges = _h3_source_record_ranges(source_text)
    authored_beats = _h3_authored_beat_count(source_text)
    if authored_beats < 2:
        return (
            ["Base rewrite added shot records without an authored multi-beat request"]
            if len(candidate_ranges) > 1 else []
        )
    errors: list[str] = []
    if len(candidate_ranges) < 2:
        errors.append(
            "Base rewrite collapsed explicit multi-beat source into one shot record"
        )
    # Supplied numeric boundaries are authoritative and are checked by the
    # timestamp lock. Unequal timing is required only when the source asks for
    # distinct beats without supplying its own ranges.
    if not source_ranges and len(candidate_ranges) >= 2:
        durations = [end - start for start, end in candidate_ranges]
        if durations and max(durations) - min(durations) <= 0.01:
            errors.append(
                "Base multi-beat rewrite used equal-duration records; preserve natural unequal pacing"
            )
    return errors


def _h3_event_association_errors(source: str, candidate: str) -> list[str]:
    """Keep authored labels, speakers, and words bound to their time range."""

    from shared.utils.prompt_parser import parse_global_timeline_prompt

    _, source_events = parse_global_timeline_prompt(source)
    if not source_events:
        source_events = _h3_loose_visual_events(source)
    _, candidate_events = parse_global_timeline_prompt(candidate)
    errors: list[str] = []
    for source_event in source_events:
        source_start = float(source_event.get("start", 0.0))
        source_end = float(source_event.get("end", source_start))
        is_range = not math.isclose(source_start, source_end, abs_tol=1e-9)
        matches = [
            event for event in candidate_events
            if math.isclose(
                float(event.get("start", 0.0)), source_start, abs_tol=0.001,
            )
            and (
                not is_range
                or math.isclose(
                    float(event.get("end", event.get("start", 0.0))),
                    source_end,
                    abs_tol=0.001,
                )
            )
        ]
        if not matches:
            errors.append("authored timestamp-to-record association changed")
            continue
        source_text = str(source_event.get("text") or "")
        candidate_text = " ".join(str(event.get("text") or "") for event in matches)
        for pattern, label in (
            (_H3_REFERENCE_LABEL_RE, "reference"),
            (_H3_SPEAKER_ID_RE, "speaker"),
            (_H3_EXACT_DIALOGUE_RE, "dialogue"),
        ):
            required = [match.group(0) for match in pattern.finditer(source_text)]
            present = [match.group(0) for match in pattern.finditer(candidate_text)]
            if any(present.count(value) < required.count(value) for value in set(required)):
                errors.append(f"authored {label}-to-timestamp association changed")
    return list(dict.fromkeys(errors))


def _h3_time_value(value: str) -> float | None:
    try:
        parts = [float(part) for part in str(value or "").split(":")]
    except (TypeError, ValueError):
        return None
    if not parts or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds


def _h3_loose_visual_events(source: str) -> list[dict]:
    """Recover explicit ranges rejected by the conservative Studio parser."""

    from services.director.h3_dialogue import _extract_h3_fields

    fields = _extract_h3_fields(source)
    visual = (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
        or ""
    )
    events: list[dict] = []
    for order, line in enumerate(visual.splitlines()):
        match = _H3_LOOSE_RANGE_RE.fullmatch(line.strip())
        if not match:
            continue
        start = _h3_time_value(match.group("start"))
        end = _h3_time_value(match.group("end"))
        if start is None or end is None or end <= start:
            continue
        events.append({
            "kind": "range",
            "start": start,
            "end": end,
            "text": match.group("text").strip(),
            "order": order,
        })
    return events


def _h3_repair_semantic_fragments(before: str) -> list[str]:
    """Extract literal visual payloads even when timeline syntax is malformed."""

    from services.director.h3_dialogue import (
        _H3_CANONICAL_RECORD_RE,
        _extract_h3_fields,
    )

    fields = _extract_h3_fields(before)
    visual = (
        fields.get("detailed_description")
        or fields.get("integrated_multimodal_description")
        or ""
    )
    fragments: list[str] = []
    for raw_line in visual.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        canonical = _H3_CANONICAL_RECORD_RE.fullmatch(line)
        if canonical:
            fragments.extend(
                canonical.group(name).strip()
                for name in ("name", "description", "vocals")
                if canonical.group(name).strip()
            )
            continue
        line = re.sub(
            r"^\[?\s*(?:Shot|Scene)\s+\d+[^\]\d]*\]?\s*",
            "",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        line = re.sub(
            r"^\[?\s*(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?\s*s?\s*"
            r"(?:-|–|—|\bto\b)\s*"
            r"(?:(?:\d{1,2}:){1,2})?\d+(?:\.\d+)?\s*s?\s*\]?\s*:?[ \t]*",
            "",
            line,
            count=1,
            flags=re.IGNORECASE,
        )
        parts = line.split("|") if "|" in line else [line]
        for part in parts:
            payload = re.sub(
                r"^\s*(?:shot_name|audiovisual_description|"
                r"dialogue_and_vocalizations)\s*:\s*",
                "",
                part,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
            if payload:
                fragments.append(payload)
    return list(dict.fromkeys(fragments))


def _h3_format_repair_lock_errors(before: str, after: str) -> list[str]:
    """Require a repair to wrap source payloads instead of rewriting them."""

    from services.director.h3_dialogue import _extract_h3_fields
    from shared.utils.prompt_parser import parse_global_timeline_prompt

    errors = _h3_locked_content_errors(before, after)
    before_fields = _extract_h3_fields(before)
    after_fields = _extract_h3_fields(after)
    after_compact = " ".join(str(after or "").split())
    before_subjects = " ".join(str(before_fields.get("subject_definitions", "")).split())
    after_subjects = " ".join(str(after_fields.get("subject_definitions", "")).split())
    if bool(before_subjects) != bool(after_subjects):
        errors.append("format repair added or removed subject definitions")
    elif before_subjects and before_subjects != after_subjects:
        errors.append("format repair changed subject_definitions")
    for field, value in before_fields.items():
        if field in {"detailed_description", "integrated_multimodal_description"}:
            continue
        compact = " ".join(value.split())
        after_value = " ".join(after_fields.get(field, "").split())
        if compact and compact != after_value:
            errors.append(f"format repair changed {field}")

    # Bare authored timeline prose has no schema labels to reconstruct. Its
    # exact normalized wording must survive inside the repaired record.
    _, events = parse_global_timeline_prompt(before)
    _, repaired_events = parse_global_timeline_prompt(after)
    for event in events:
        fragment = re.sub(
            r"^\[\s*(?:Shot|Scene)\s+\d+[^\]]*\]\s*",
            "",
            str(event.get("text") or ""),
            flags=re.IGNORECASE,
        )
        compact = " ".join(fragment.split())
        start = float(event.get("start", 0.0))
        end = float(event.get("end", start))
        matching_text = " ".join(
            " ".join(str(repaired_event.get("text") or "").split())
            for repaired_event in repaired_events
            if math.isclose(
                float(repaired_event.get("start", 0.0)), start, abs_tol=0.001,
            )
            and math.isclose(
                float(repaired_event.get("end", repaired_event.get("start", 0.0))),
                end,
                abs_tol=0.001,
            )
        )
        if compact and compact not in matching_text:
            errors.append("format repair rewrote timed record content")
            break
    for fragment in _h3_repair_semantic_fragments(before):
        if " ".join(fragment.split()) not in after_compact:
            errors.append("format repair rewrote visual content")
            break
    errors.extend(_h3_exact_repair_payload_errors(before, after))
    return list(dict.fromkeys(errors))


def _h3_exact_repair_payload_errors(before: str, after: str) -> list[str]:
    """Reject every semantic addition; only record syntax may be added."""
    from services.director.h3_dialogue import _H3_CANONICAL_RECORD_RE, _extract_h3_fields

    def records(value: str) -> list[dict]:
        fields = _extract_h3_fields(value)
        visual = fields.get("detailed_description") or fields.get("integrated_multimodal_description") or ""
        rows = []
        for line in visual.splitlines():
            text = line.strip()
            exact = _H3_CANONICAL_RECORD_RE.fullmatch(text)
            if exact:
                rows.append({
                    "start": _h3_time_value(exact.group("start")),
                    "end": _h3_time_value(exact.group("end")),
                    "name": exact.group("name").strip(),
                    "description": exact.group("description").strip(),
                    "vocals": exact.group("vocals").strip(),
                    "canonical": True,
                })
                continue
            loose = _H3_LOOSE_RANGE_RE.fullmatch(text)
            if loose:
                rows.append({
                    "start": _h3_time_value(loose.group("start")),
                    "end": _h3_time_value(loose.group("end")),
                    "payload": " ".join(loose.group("text").split()),
                    "canonical": False,
                })
        return rows

    source_rows, repaired_rows = records(before), records(after)
    errors: list[str] = []
    if not source_rows and repaired_rows:
        return [
            "format repair cannot prove an exact record mapping from the "
            "malformed source"
        ]
    if source_rows and len(source_rows) != len(repaired_rows):
        return ["format repair added or removed shot records"]
    for source in source_rows:
        matched = [row for row in repaired_rows if math.isclose(row["start"], source["start"], abs_tol=0.001) and math.isclose(row["end"], source["end"], abs_tol=0.001)]
        if len(matched) != 1:
            errors.append("format repair changed shot timing associations")
            continue
        repaired = matched[0]
        if not repaired["canonical"]:
            errors.append("format repair did not produce a canonical record")
            continue
        if source["canonical"]:
            if any(" ".join(source[key].split()) != " ".join(repaired[key].split()) for key in ("name", "description", "vocals")):
                errors.append("format repair changed canonical record payload")
            continue
        payload = source["payload"]
        if " ".join(repaired["description"].split()) != payload or repaired["vocals"].casefold() not in {"none", "n/a"}:
            errors.append("format repair added or changed visual or vocal content")
        source_words = {word.casefold() for word in re.findall(r"[A-Za-z0-9']+", payload)}
        name_words = {word.casefold() for word in re.findall(r"[A-Za-z0-9']+", repaired["name"])}
        if not name_words.issubset(source_words | {"shot", "scene"}):
            errors.append("format repair invented shot-name content")
    for pattern, label in ((_H3_REFERENCE_LABEL_RE, "reference labels"), (_H3_SPEAKER_ID_RE, "speaker IDs"), (_H3_EXACT_DIALOGUE_RE, "dialogue")):
        before_values = [match.group(0) for match in pattern.finditer(before)]
        after_values = [match.group(0) for match in pattern.finditer(after)]
        if before_values != after_values:
            errors.append(f"format repair added or changed {label}")
    return list(dict.fromkeys(errors))


def _h3_enhance_contract_errors(
    candidate: str,
    source_prompt: str,
    *,
    ref2va: bool,
    duration_seconds: Optional[float],
) -> list[str]:
    from services.director.h3_dialogue import (
        validate_h3_context_ir_records,
        validate_h3_prompt_contract,
    )

    mode = "ref2va" if ref2va else "t2va"
    errors = [
        error for error in validate_h3_prompt_contract(candidate, mode=mode)
        if error != "silent prompt has no explicit H3 silence contract"
    ]
    errors.extend(validate_h3_context_ir_records(
        candidate,
        mode=mode,
        duration_seconds=duration_seconds,
    ))
    errors.extend(_h3_locked_content_errors(source_prompt, candidate))
    if not ref2va:
        errors.extend(_h3_source_structure_errors(
            source_prompt,
            candidate,
            duration_seconds=duration_seconds,
        ))
    return list(dict.fromkeys(errors))


def _finalize_h3_enhance_output(
    result: str,
    source_prompt: str,
    *,
    ref2va: bool,
    duration_seconds: Optional[float],
    max_new_tokens: int,
    response_assist: Optional[dict],
    progress_callback: Optional[Callable[[dict], None]],
    cancel_handle: Optional[LlmCancellationHandle],
) -> str:
    """Validate H3 output, make one local format-only repair, then fail closed."""

    _cancellation_checkpoint(cancel_handle)
    candidate = _clean_h3_context_ir_output(result)
    errors = _h3_enhance_contract_errors(
        candidate,
        source_prompt,
        ref2va=ref2va,
        duration_seconds=duration_seconds,
    )
    if not errors:
        return candidate
    mode_name = "Ref2VA" if ref2va else "Base"
    repair_system = (
        f"FORMAT-ONLY MINIMAX H3 {mode_name.upper()} REPAIR. Output only the "
        "complete corrected Context-IR. Use physical records exactly as "
        "[Shot N] [STARTs-ENDs] shot_name: ... | audiovisual_description: ... "
        "| dialogue_and_vocalizations: .... Change only missing or malformed "
        "field labels, brackets, timestamp wrappers, shot numbering, and record "
        "separators. Copy all names, descriptive terms, actions, reference "
        "labels, speaker IDs, timestamp values and associations, dialogue, "
        "vocalizations, sound, and music exactly. Do not summarize, expand, "
        "sanitize, or invent content."
    )
    repair_prompt = (
        "Contract errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\nORIGINAL REQUEST (literal anchors are immutable):\n"
        + str(source_prompt or "")
        + "\n\nPREVIOUS OUTPUT (repair only its format):\n"
        + candidate
    )
    # Provider selection is singleton state. Hold its re-entrant model lock
    # across the locality check and repair call so another request cannot swap
    # in a remote provider between those two operations.
    with _lock:
        _cancellation_checkpoint(cancel_handle)
        if _provider != "local":
            raise ValueError(
                "MiniMax H3 output violated its Context-IR contract and local "
                "repair is unavailable"
            )
        repaired = generate(
            prompt=repair_prompt,
            system_prompt=repair_system,
            max_new_tokens=max(768, int(max_new_tokens or 0)),
            temperature=0.2,
            enable_thinking=False,
            thinking_budget=0,
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )
    _cancellation_checkpoint(cancel_handle)
    repaired = _clean_h3_context_ir_output(repaired)
    final_errors = _h3_enhance_contract_errors(
        repaired,
        source_prompt,
        ref2va=ref2va,
        duration_seconds=duration_seconds,
    )
    final_errors.extend(_h3_format_repair_lock_errors(candidate, repaired))
    final_errors = list(dict.fromkeys(final_errors))
    if final_errors:
        raise ValueError(
            "MiniMax H3 output still violated its Context-IR contract after "
            "one format-only repair: " + "; ".join(final_errors)
        )
    return repaired


def enhance_prompt(
    prompt: str,
    mode: str = "video",
    max_new_tokens: int = 200,
    temperature: float = 0.6,
    nsfw: bool = False,
    model_type: str = "",
    image_paths: Optional[list] = None,
    duration_seconds: Optional[int] = None,
    window_count: Optional[int] = None,
    window_size_seconds: Optional[int] = None,
    system_override: Optional[str] = None,
    tts_enhance_mode: Optional[str] = None,
    tts_voice_count: int = 2,
    lora_system_hint: str = "",
    raw_enhancer_mode: bool = False,
    preserve_global_timeline: bool = False,
    visual_style: Optional[str] = None,
    h3_style_workflow_present: bool = False,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    _cancellation_checkpoint(cancel_handle)
    is_h3_context_ir = (
        mode in ("video", "avatar")
        and (model_type or "").lower().startswith("minimax_h3")
    )
    is_h3_ref2va = (
        is_h3_context_ir
        and (model_type or "").lower().startswith("minimax_h3_ref2va")
    )
    # If caller provides a system prompt override, use it directly (e.g., Director third-pass)
    if system_override:
        # Do NOT append the full model-specific enhance guide — the override is self-contained.
        # The enhance guides are designed for Studio mode (expand brief prompts) and would
        # contradict Director overrides (refine, don't expand).
        system = system_override
        if mode in ("image", "video", "avatar"):
            from services.director.policies import (
                build_visual_style_refinement_block,
            )
            system = (
                f"{system}\n\n"
                f"{build_visual_style_refinement_block()}"
            )
        # Inject content guidance
        from services.director.nsfw_guidance import inject_content_guidance
        system = inject_content_guidance(system, nsfw, "enhance")
        # Inject LoRA hints into system prompt
        if lora_system_hint:
            system = f"{system}\n\n{lora_system_hint}"

        # Disable thinking mode for system_override callers. The override path
        # is used exclusively by the Director third-pass polish, which is a
        # REFINEMENT task — there's no creative reasoning the LLM needs to do.
        # On Qwen3.5/3.6, thinking consumes the entire token budget before
        # any content is produced:
        #   [LLM] WARNING: Server returned empty content despite generating
        #         1024 tokens (model likely consumed all tokens on internal reasoning)
        #
        # THREE redundant suppression mechanisms — Qwen3.5/3.6 chat templates
        # in current llama.cpp builds ignore the first two, so the third
        # (hard stop sequence) is the actual safety net:
        #   1. enable_thinking=False     → chat_template_kwargs route (often ignored)
        #   2. /no_think user prefix     → Qwen Jinja template marker (often ignored)
        #   3. stop=["<think>", ...]     → llama-server stops generation when
        #      the model tries to enter thinking mode. Wastes ~1 token per call
        #      instead of the 1024-token budget. The polish pipeline's
        #      "unchanged passthrough" handler then falls back to the original
        #      Pass-2 prompt, so the user gets usable output even when polish
        #      is silently no-op'd by a stubborn thinking template.
        #
        # Net effect on a thinking-stubborn Qwen build: Pass 3 polish becomes
        # a ~free no-op (per-call cost drops from ~1024 tokens of waste to
        # ~1 token), and the user gets the unmodified Pass-2 prompt. Better
        # than the previous behavior of burning 26k+ tokens producing nothing.
        override_prompt = prompt
        if is_h3_context_ir:
            override_prompt = _build_enhance_user_prompt(
                prompt, mode, duration_seconds, window_count,
                window_size_seconds, preserve_global_timeline=True,
                h3_context_ir=True, h3_ref2va=is_h3_ref2va,
            )
        prompt_with_marker = (
            f"/no_think\n\n{override_prompt}" if override_prompt else "/no_think"
        )
        override_max_tokens = max(max_new_tokens, 1024)
        if is_h3_context_ir:
            override_max_tokens = max(
                override_max_tokens,
                min(
                    4096,
                    max(
                        1200 if is_h3_ref2va else 768,
                        int(float(duration_seconds or 0) * (
                            48 if is_h3_ref2va else 40
                        )),
                    ),
                ),
            )
        result = generate(
            prompt=prompt_with_marker,
            system_prompt=system,
            max_new_tokens=override_max_tokens,
            temperature=temperature,
            enable_thinking=False,
            stop=["<think>", "<thinking>"],
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )
        if is_h3_context_ir and result:
            # Director has already authored the complete Context-IR. Semantic
            # equivalence cannot be proven safely with token-level checks:
            # identities, sound/music intent, shot numbering, or hidden
            # execution prose can drift while timestamps and field labels stay
            # unchanged. Fail closed unless the refinement is byte-for-byte
            # equivalent after outer whitespace normalization.
            if result.strip() != (prompt or "").strip():
                print(
                    "[Enhance] H3 Director refinement changed locked Context-IR; "
                    "preserving the original global timeline"
                )
                result = prompt
        if is_h3_context_ir:
            return _finalize_h3_enhance_output(
                result or prompt,
                prompt,
                ref2va=is_h3_ref2va,
                duration_seconds=duration_seconds,
                max_new_tokens=override_max_tokens,
                response_assist=response_assist,
                progress_callback=progress_callback,
                cancel_handle=cancel_handle,
            )
        return result.strip() if result else prompt

    # Dedicated per-model enhancer (e.g. Sulphur's uncensored enhancer): the
    # model is trained to enhance directly. Ordinary visual requests receive
    # only the server-owned baseline visual-style policy. An explicitly
    # authorized request additionally gets the same explicit-authoring context
    # as every other enhancer; without it, abliterated/uncensored fine-tunes
    # often sanitize the output.
    if raw_enhancer_mode:
        # The fine-tuned enhancer (a) doesn't reliably honor a "write N
        # paragraphs" instruction and (b) likes to prepend a bogus "rendered
        # in a 3D animation style" clause. Always run the style cleanup; and
        # when the user gave one line per window, enhance each window
        # independently and collapse it to a single paragraph — that makes the
        # output EXACTLY window_count paragraphs regardless of the model.
        raw_max_tokens = max(max_new_tokens, 256)
        if is_h3_context_ir:
            raw_max_tokens = max(
                raw_max_tokens,
                min(
                    4096,
                    max(
                        1200 if is_h3_ref2va else 768,
                        int(float(duration_seconds or 0) * (
                            48 if is_h3_ref2va else 40
                        )),
                    ),
                ),
            )
        raw_system_prompt = ""
        if mode in ("image", "video", "avatar"):
            from services.director.policies import (
                build_visual_style_authority_block,
                build_visual_style_default_block,
            )
            raw_system_prompt = (
                build_visual_style_default_block(
                    structured_style_present=h3_style_workflow_present,
                )
                + "\n\n"
                + build_visual_style_authority_block(visual_style)
            ).strip()
        if nsfw:
            from services.director.nsfw_guidance import inject_content_guidance
            raw_system_prompt = inject_content_guidance(
                raw_system_prompt, True, "enhance",
            ).strip()
        gen_kw = dict(
            system_prompt=raw_system_prompt, max_new_tokens=raw_max_tokens,
            temperature=temperature, enable_thinking=False,
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )
        lines = [ln.strip() for ln in (prompt or "").split("\n") if ln.strip()]
        if (
            not is_h3_context_ir
            and window_count and window_count > 1
            and len(lines) == window_count
        ):
            print(f"[Enhance] Raw enhancer: per-window x{window_count} ({model_type})")
            outs = []
            for i, ln in enumerate(lines):
                _cancellation_checkpoint(cancel_handle)
                # Image only informs window 1; later windows continue from it.
                w_prompt = _build_enhance_user_prompt(ln, mode, window_size_seconds, 1, window_size_seconds)
                r = generate(prompt=w_prompt, image_paths=(image_paths if i == 0 else None), **gen_kw)
                r = _clean_enhancer_output(r)
                r = " ".join(r.split()) if r else ln  # collapse to one paragraph
                outs.append(r or ln)
            return "\n".join(outs)
        # Single call: 1-line "expand into N windows", or a line/window
        # mismatch. Falls back to the explicit-count instruction.
        raw_prompt = _build_enhance_user_prompt(
            prompt, mode, duration_seconds, window_count, window_size_seconds,
            preserve_global_timeline, h3_context_ir=is_h3_context_ir,
            h3_ref2va=is_h3_ref2va,
        )
        print(f"[Enhance] Raw enhancer ({model_type}, images={bool(image_paths)}, windows={window_count})")
        result = generate(prompt=raw_prompt, image_paths=image_paths, **gen_kw)
        if is_h3_context_ir:
            return _finalize_h3_enhance_output(
                result or prompt,
                prompt,
                ref2va=is_h3_ref2va,
                duration_seconds=duration_seconds,
                max_new_tokens=raw_max_tokens,
                response_assist=response_assist,
                progress_callback=progress_callback,
                cancel_handle=cancel_handle,
            )
        return _clean_enhancer_output(result) or prompt

    # Try to load a model-specific guide
    system = None
    if model_type:
        try:
            from services.enhance_guides import get_enhance_guide
            has_images = bool(image_paths)
            system = get_enhance_guide(model_type, mode, has_images=has_images)
            print(f"[Enhance] Using model-specific guide for {model_type} ({mode}, images={has_images})")
        except (Exception, SystemExit):
            pass

    # Fallback to generic prompts if no guide loaded
    if not system:
        if is_h3_ref2va:
            system = (
                "Rewrite the request as one complete MiniMax H3 Ref2VA prompt "
                "with these exact fields in order: subject_definitions:, "
                "summary:, retention_analysis:, detailed_description:, "
                "overall_soundscape:, non_diegetic_music:. Use one coherent "
                "global timeline through the supplied Duration. Preserve "
                "identities, reference labels, literal <d> dialogue, speaker "
                "IDs, sound, music, authored global timestamps, and cuts."
            )
        elif is_h3_context_ir:
            system = (
                "Rewrite the request as one complete MiniMax H3 prompt with "
                "these exact fields in order: subject_definitions:, "
                "integrated_multimodal_description:, "
                "overall_soundscape:, non_diegetic_music:. Use one coherent "
                "global timeline through the supplied Duration. Establish each "
                "authored entity once, reference stable Subject IDs or names, "
                "and use multiple unequal records for distinct authored beats "
                "or boundaries; preserve identities, literal <d> dialogue, "
                "speaker IDs, sound, music, authored global timestamps, and cuts."
            )
        else:
            system_prompts = {
                "video": (
                    "You are an expert cinematic director. Enhance the user's video prompt "
                    "with detailed descriptions of movements, camera angles, lighting, and "
                    "environment. Keep under 150 words. Output only the enhanced prompt."
                ),
                "image": (
                    "You are an expert photographer. Enhance the user's image prompt with "
                    "detailed descriptions of composition, lighting, colors, and mood. "
                    "The output is a STILL PHOTOGRAPH — describe static poses only, no motion verbs "
                    "(no walking, running, reaching, turning, dancing). "
                    "Keep under 150 words. Output only the enhanced prompt."
                ),
                "audio": (
                    "You are an expert audio producer. Enhance the user's audio description "
                    "with detailed descriptions of tone, pace, emotion, and sound qualities. "
                    "Keep under 100 words. Output only the enhanced prompt."
                ),
            }
            system = system_prompts.get(mode, system_prompts["video"])

    # TTS-specific enhance: override system prompt with monologue/dialogue templates.
    #
    # Model handlers can supply their own per-mode enhancer prompts via the
    # `text_prompt_enhancer_instructions` (monologue) and
    # `text_prompt_enhancer_instructions1` (dialogue) keys on their model_def.
    # We check the model_def first — if the active model provides a custom
    # prompt, it takes precedence over the generic TTS_*_PROMPT defaults.
    #
    # This is the path Scenema relies on: its handler points
    # text_prompt_enhancer_instructions[1] at the rich markdown guides under
    # llm_guides/prompt_enhancer/ (single-speaker speech rules and two-speaker
    # dialogue rules). Before this lookup existed, the generic
    # TTS_QWEN3_DIALOGUE_PROMPT below always won regardless of which model the
    # user picked — producing "Peter:"/"Sarah:" character-labeled dialogue
    # instead of Scenema's `Speaker N{voice=..., gender=..., scene=...}: [cue]`
    # format. Result: Scenema's parser saw no `Speaker N:` headers and
    # collapsed the entire output into a single-voice block.
    if mode == "audio" and tts_enhance_mode:
        from models.TTS.prompt_enhancers import TTS_MONOLOGUE_PROMPT, TTS_QWEN3_DIALOGUE_PROMPT

        # Look up model-specific enhancer prompts (Scenema, Kugel, Qwen3-TTS,
        # Index-TTS2, Chatterbox, IndexTTS2 all set these on their model_def).
        # Lazy import keeps llm_service.py importable in environments where
        # wgp.py is unavailable (e.g. lightweight tooling, tests).
        model_specific_monologue = None
        model_specific_dialogue = None
        if model_type:
            try:
                from wgp import get_model_def
                md = get_model_def(model_type)
                if md:
                    model_specific_monologue = md.get("text_prompt_enhancer_instructions")
                    model_specific_dialogue = md.get("text_prompt_enhancer_instructions1")
            except Exception as e:
                print(f"[Enhance] Could not load model_def for {model_type}: {e}")

        if tts_enhance_mode in ("dialogue", "dialogue_fast"):
            # Check for voice count to customize speaker count
            voice_count = tts_voice_count
            if voice_count <= 1:
                system = model_specific_monologue or TTS_MONOLOGUE_PROMPT
            elif voice_count == 2:
                # Two-speaker path: prefer the model's dialogue prompt
                # (Scenema's markdown guide, Kugel's NotebookLM rules, etc.)
                # before falling back to the generic Qwen-style template.
                system = model_specific_dialogue or TTS_QWEN3_DIALOGUE_PROMPT
            else:
                # Multi-speaker (3-6): adapt the dialogue template. Both
                # Scenema and Kugel cap at 2 voices, so this branch is for
                # legacy TTS engines that support 3+ speakers (Qwen3-TTS et al).
                system = (
                    f"You are a creative dialogue writer for a text-to-speech model. "
                    f"Write an engaging, natural conversation with exactly {voice_count} characters based on the user prompt.\n\n"
                    f"Output rules:\n"
                    f"- Output ONLY dialogue lines. No explanations, stage directions, or narration.\n"
                    f"- Every line must start with a character name followed by a colon.\n"
                    f"- Use exactly {voice_count} characters. Use names from the prompt, or invent fitting names.\n"
                    f"- NO brackets, NO emotion tags, NO parenthetical directions.\n"
                    f"- Write NATURAL, REALISTIC dialogue with interruptions, reactions, varied sentence lengths.\n"
                    f"- Write a FULL conversation (20-40+ lines) unless the user specifies shorter.\n"
                    f"- Use clear punctuation. Commas and periods create natural pauses.\n"
                    f"- Make sure all {voice_count} characters participate meaningfully — don't let any character fade out.\n"
                )
        else:
            # Monologue path. Model-specific prompt wins over the generic
            # TTS_MONOLOGUE_PROMPT (so Scenema speech rules get used).
            system = model_specific_monologue or TTS_MONOLOGUE_PROMPT

        if model_specific_dialogue or model_specific_monologue:
            picked = "dialogue" if (tts_enhance_mode in ("dialogue", "dialogue_fast") and tts_voice_count == 2 and model_specific_dialogue) else ("monologue" if model_specific_monologue else "generic")
            print(f"[Enhance] Using model-specific {picked} prompt for {model_type}")

    # Prompt writers use one conditional visual default. The model preserves
    # authored/reference styles; Maestro does not scan or classify prompt text.
    if mode in ("image", "video", "avatar"):
        from services.director.policies import (
            build_visual_style_authority_block,
            build_visual_style_default_block,
        )
        system = (
            f"{system}\n\n"
            f"{build_visual_style_default_block(structured_style_present=h3_style_workflow_present)}\n\n"
            f"{build_visual_style_authority_block(visual_style)}"
        ).strip()

    # Inject explicit enhance guidance only after the request-local server gate
    # has passed. Uses a SHARED,
    # VERSION-CONTROLLED guide (llm_guides/enhance/nsfw_shared.md) so it ships
    # via git to every install — the previous path read from the gitignored
    # supplement pack, which never travels through `git pull` (so edits never
    # reached the runtime). The shared guide preserves the request's authorized
    # detail and linguistic register while retaining strict scope fidelity.
    if nsfw:
        from services.director.nsfw_guidance import inject_content_guidance
        system = inject_content_guidance(system, True, "enhance")

    # Shared video-enhance rules — e.g. reference characters by a stable visual
    # appearance, not by name/relationship/pronoun (the model has no memory of
    # who anyone is). Appended for video so EVERY path gets it: per-model guides
    # (Sulphur, 10Eros) that don't include the generic LTX video guide, plus the
    # generic guide itself. Mirrors the Director-mode character-reference rule.
    # H3's guide already carries its own identity, pacing, and silence rules.
    # The generic appendix says to remove all character names and to write one
    # paragraph per sliding window, both of which conflict with H3's
    # knowledge-aware Context-IR format and single native timeline.
    if mode in ("video", "avatar") and not is_h3_context_ir:
        from services.guide_loader import load_guide as _load_vid_guide
        vid_block = _load_vid_guide("enhance", "video_shared")
        if vid_block:
            system = f"{system}\n\n{vid_block}"

    # Build the user prompt with context (duration + sliding-window count).
    # Shared with the raw per-model-enhancer path via the helper above.
    user_prompt = _build_enhance_user_prompt(
        prompt, mode, duration_seconds, window_count, window_size_seconds,
        preserve_global_timeline, h3_context_ir=is_h3_context_ir,
        h3_ref2va=is_h3_ref2va,
    )

    # Add image context
    if image_paths:
        if mode == "image":
            user_prompt = f"I have attached a reference image. Enhance this prompt based on what you see in the image:\n\n{user_prompt}"
        else:
            user_prompt = f"I have attached a start frame image. Enhance this video prompt to match what you see in the image and describe what should happen:\n\n{user_prompt}"
        print(f"[Enhance] Sending {len(image_paths)} image(s) to vision LLM")

    # Inject LoRA hints into system prompt (NOT user prompt) so LLM treats them as instructions
    if lora_system_hint:
        system += f"\n\n{lora_system_hint}"

    # Preserve structural elements in image prompts
    if mode == "image":
        system += (
            '\n\nSTRUCTURAL RULES for image prompts:'
            '\n- If the prompt starts with "create new scene", keep that prefix.'
            '\n- If the prompt ends with "Use original reference images" or similar, keep that suffix.'
            '\n- ALWAYS end the prompt with: "Preserve character identity, attire, body attributes, and the art style of the reference image."'
            '\n- NEVER include LoRA names or filenames in the output.'
        )

    # Reinforce the output constraint. MiniMax H3 is intentionally different:
    # its field labels and <d> blocks are part of the model input, not prose
    # headers to strip. The generic "no labels" rule previously contradicted
    # the H3 guide and encouraged ordinary quote-mark dialogue.
    if is_h3_context_ir:
        if is_h3_ref2va:
            system += (
                "\n\nCRITICAL MINIMAX H3 REF2VA OUTPUT CONTRACT: Output ONLY the "
                "structured prompt with these exact labels in order: "
                "subject_definitions:, summary:, retention_analysis:, "
                "detailed_description:, overall_soundscape:, and "
                "non_diegetic_music:. Keep every <Subject N>, <Picture N>, "
                "<Video N>, and <Audio N> label stable across all sections. "
                "Every spoken line needs a stable (S1), (S2), etc. ID and "
                "<d>[Language] literal words</d>. No markdown, explanation, or "
                "LoRA filenames."
            )
        system += (
            "\n\nLONG-DURATION H3 CONTRACT: Accept the complete supplied Duration, "
            "including 30 or 60 seconds, without shortening or rejecting it. "
            "Write one coherent global timeline from 0.00 seconds through the "
            "complete Duration. Preserve identities, literal dialogue, speaker "
            "IDs, sound, music, and every authored global timestamp and cut in "
            "the same order. Never restart the clock partway through."
        )
        if not is_h3_ref2va:
            system += (
                "\n\nCRITICAL MINIMAX H3 OUTPUT CONTRACT: Output ONLY the structured "
                "H3 prompt, with the exact field labels "
                "subject_definitions:, integrated_multimodal_description:, "
                "overall_soundscape:, and "
                "non_diegetic_music:. These labels are required model syntax, not "
                "explanatory headers. Define each authored visible entity once "
                "in subject_definitions and reference its stable Subject ID or "
                "name in the records; never repeat its full definition in a "
                "shot. Every spoken line must have a stable (S1), "
                "(S2), etc. speaker ID and use <d>[Language] literal words</d>. "
                "For distinct authored beats, cuts, or timestamp boundaries, "
                "write multiple naturally unequal records; use one record only "
                "for an explicitly sustained one-take. "
                "When the user requests a discussion without supplying lines, write "
                "short meaningful dialogue that fits the supplied Duration. Once the "
                "last line ends, describe silent visible action and closed mouths; do "
                "not invent more speech. No markdown, explanation, or LoRA filenames."
            )
    else:
        system += "\n\nCRITICAL: Output ONLY the enhanced prompt text. No headers, no labels, no markdown, no explanation, no \"Enhancement Logic\", no \"Edit Prompt:\". No LoRA filenames (.safetensors). Just the raw prompt text."
    if preserve_global_timeline and not is_h3_context_ir:
        system += (
            "\n\nGLOBAL TIMELINE LOCK: This is a complete Studio prompt. "
            "Keep every global timestamp token byte-for-byte unchanged and "
            "in the same order. Improve descriptions inside the authored "
            "structure only; do not add, remove, split, rebase, or reorder "
            "timed beats."
        )

    # Scale max tokens for multi-window video prompts
    effective_max_tokens = max_new_tokens
    if window_count and window_count > 1:
        effective_max_tokens = max(max_new_tokens, window_count * 300 + 256)
    if is_h3_context_ir:
        # Leave enough room for the Base entity namespace and required fields plus a compact timed
        # dialogue. Most H3 prompts finish well below this ceiling, but 512 can
        # truncate a vision-assisted 15-second rewrite before its sound fields.
        duration_budget = min(
            4096,
            max(768, int(float(duration_seconds or 0) * (48 if is_h3_ref2va else 40))),
        )
        effective_max_tokens = max(
            effective_max_tokens,
            1200 if is_h3_ref2va else 768,
            duration_budget,
        )

    # TTS: thinking mode for creative dialogue, disabled for fast mode
    is_tts = bool(tts_enhance_mode)
    is_fast = tts_enhance_mode and tts_enhance_mode.endswith('_fast')
    use_thinking = is_tts and not is_fast

    result = generate(
        prompt=user_prompt,
        system_prompt=system,
        max_new_tokens=effective_max_tokens,
        temperature=temperature,
        image_paths=image_paths,
        enable_thinking=use_thinking,
        thinking_budget=16384 if use_thinking else 4096,
        frequency_penalty=0.3,  # prevent repetition loops
        presence_penalty=0.1,   # encourage variety
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )

    # Post-process. H3's repeated record labels are required syntax, so its
    # output bypasses the generic repetition-loop truncator entirely.
    if result:
        if is_h3_context_ir:
            result = _finalize_h3_enhance_output(
                result,
                prompt,
                ref2va=is_h3_ref2va,
                duration_seconds=duration_seconds,
                max_new_tokens=effective_max_tokens,
                response_assist=response_assist,
                progress_callback=progress_callback,
                cancel_handle=cancel_handle,
            )
        else:
            result = _clean_enhance_output(result)
    if preserve_global_timeline and result:
        import re
        marker = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?(?!\d)")
        if marker.findall(result) != marker.findall(prompt):
            raise ValueError(
                "Prompt enhancement changed a locked global timestamp; "
                "the original Studio timeline was preserved"
            )
    _cancellation_checkpoint(cancel_handle)
    return result


def _clean_enhance_output(text: str) -> str:
    """Strip markdown formatting, headers, explanation, and repetition loops from enhance output."""
    import re
    # Remove markdown bold/headers
    text = re.sub(r'\*\*.*?\*\*:?\s*', '', text)
    # Remove markdown headers
    text = re.sub(r'^#{1,4}\s+.*$', '', text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    # Remove common label prefixes the model adds
    text = re.sub(r'^(?:Edit Prompt|Enhanced Prompt|Prompt|Output|Result|Enhancement Logic|Here is)[:\s]*', '', text, flags=re.IGNORECASE)
    # Remove leading/trailing quotes if the entire output is quoted
    text = text.strip()
    if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
        text = text[1:-1]
    # Collapse excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Detect and truncate repetition loops: if any 20+ char substring repeats 3+ times, keep only the first occurrence
    cleaned = text.strip()
    for chunk_len in range(30, 15, -1):
        if len(cleaned) < chunk_len * 3:
            continue
        for start in range(len(cleaned) - chunk_len * 2):
            chunk = cleaned[start:start + chunk_len]
            if chunk in cleaned[start + chunk_len:start + chunk_len * 3]:
                # Found a repeating pattern — truncate at the first repetition
                cleaned = cleaned[:start + chunk_len].rstrip('. ,;') + '.'
                print(f"[Enhance] Truncated repetition loop at position {start} (pattern: '{chunk[:40]}...')")
                return cleaned

    return cleaned


def describe_image(
    image_path: str = "",
    prompt: str = "Describe this image in detail for use as a video generation prompt.",
    max_new_tokens: int = 256,
    *,
    image_paths: Optional[Sequence[str]] = None,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> str:
    """Describe one or more caller-authorized images with a vision model."""
    _cancellation_checkpoint(cancel_handle)
    if image_paths is None:
        authorized_paths = [image_path] if image_path else []
    else:
        if (
            isinstance(image_paths, (str, bytes))
            or not isinstance(image_paths, Sequence)
            or len(image_paths) > 8
            or any(not isinstance(path, str) or not path for path in image_paths)
        ):
            raise ValueError("image_paths must be a list of at most 8 image files")
        authorized_paths = list(image_paths)
        if image_path and image_path not in authorized_paths:
            if len(authorized_paths) >= 8:
                raise ValueError("image_paths must be a list of at most 8 image files")
            authorized_paths.insert(0, image_path)
    if not authorized_paths:
        raise ValueError("At least one authorized image path is required")
    for authorized_path in authorized_paths:
        if not os.path.isfile(authorized_path):
            raise FileNotFoundError(f"Image not found: {authorized_path}")
    if not _vision_available:
        raise ValueError("The selected LLM has no available vision projector")

    basename = os.path.basename(authorized_paths[0])
    return generate(
        prompt=f"The user has an image file named '{basename}'. {prompt}",
        system_prompt="You are a helpful assistant that generates creative, detailed video prompts.",
        max_new_tokens=max_new_tokens,
        temperature=0.4,
        image_paths=authorized_paths,
        enable_thinking=(
            False
            if normalize_response_assist(response_assist).assistant_prefill
            else None
        ),
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )


def _section_hints(section: str) -> str:
    """Return cinematic variation hints based on section type."""
    hints = {
        "intro": "establishing wide shot, slow reveal, atmospheric, moody lighting",
        "verse": "medium shots, storytelling, character focus, steady camera",
        "chorus": "dynamic angles, fast cuts, peak energy, bold colors, wide and close-up mix",
        "bridge": "change of scenery, dreamy or surreal, unique angle, slow motion",
        "outro": "pulling back, reflective, fading light, wide shot",
        "instrumental": "abstract visuals, dramatic camera sweep, focus on environment",
    }
    return hints.get(section, "creative angle, vivid scene")


def _build_clip_description(clip: dict, index: int, lyrics: Optional[list] = None) -> str:
    """Build a rich description for a single clip."""
    start = clip.get("start", 0)
    end = clip.get("end", 0)
    section = clip.get("section_label", "verse")
    energy = clip.get("energy", 0.5)

    if energy > 0.7:
        energy_word = "very high energy, intense"
    elif energy > 0.5:
        energy_word = "high energy, dynamic"
    elif energy > 0.3:
        energy_word = "moderate energy, steady"
    else:
        energy_word = "low energy, calm"

    clip_lyrics = ""
    if lyrics:
        matching = [l["text"] for l in lyrics
                   if l["start"] < end and l["end"] > start]
        if matching:
            clip_lyrics = f'\n   Lyrics: "{" ".join(matching)}"'

    hints = _section_hints(section)
    return (
        f"Clip {index + 1} ({start:.1f}s-{end:.1f}s): {section} section, {energy_word}.\n"
        f"   Cinematic direction: {hints}{clip_lyrics}"
    )


def _inject_authorized_explicit_planner_guidance(
    system_prompt: str,
    explicit_guidance: bool,
    mode: str,
) -> str:
    """Compose explicit planner rules without changing ordinary legacy calls.

    Request/provider authorization belongs to the HTTP or durable-pipeline
    boundary. False remains a byte-for-byte no-op here.
    """
    if explicit_guidance is not True:
        return system_prompt
    from services.director.nsfw_guidance import inject_content_guidance
    return inject_content_guidance(system_prompt, True, mode)


_STRUCTURED_RESPONSE_ASSIST_PLANNERS = frozenset({
    "classify_song_sections",
    "plan_clip_prompts_and_images",
    "plan_short_film_prompts",
    "plan_short_film_from_story",
})
_PREFILL_RESPONSE_ASSIST_PLANNERS = frozenset({
    "plan_angle_prompts",
    "plan_clip_prompts",
})


def _planner_assist_thinking_mode(helper_name: str, response_assist):
    """Disable thinking for prefilled prose helpers, never structured ones."""
    if helper_name not in _PREFILL_RESPONSE_ASSIST_PLANNERS:
        return None
    if normalize_response_assist(response_assist).assistant_prefill:
        return False
    return None


def plan_clip_prompts(
    clips: list,
    style_prompt: str,
    lyrics: Optional[list] = None,
    bpm: float = 120.0,
    max_new_tokens: int = 150,
    nsfw: bool = False,
    *,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> list:
    """Generate per-clip video prompts based on audio analysis and style.

    Processes clips in small batches so the small LLM can produce varied output.

    Args:
        clips: List of dicts with keys: start, end, section_label, energy, suggested_prompt_hint
        style_prompt: Overall style/concept for the video
        lyrics: Optional list of dicts with keys: start, end, text
        bpm: Song BPM
        max_new_tokens: Max tokens per clip prompt

    Returns:
        List of prompt strings, one per clip
    """
    _cancellation_checkpoint(cancel_handle)
    if not clips:
        return []

    import re

    BATCH_SIZE = 100  # Process all clips at once so the LLM sees the full song arc
    all_prompts = []

    # Camera/action variety pool to inject into prompts
    camera_angles = [
        "low angle looking up", "overhead bird's eye view", "close-up detail shot",
        "wide establishing shot", "Dutch angle tilted frame", "tracking shot following action",
        "slow dolly push-in", "handheld shaky cam energy", "profile silhouette shot",
        "over-the-shoulder perspective", "sweeping crane shot", "ground-level shot",
    ]

    system_prompt = (
        "You are an expert music video director. Write vivid, UNIQUE video generation prompts. "
        "Each prompt must describe a DIFFERENT visual scene with specific camera angle, lighting, "
        "action, and composition. NEVER repeat the same description twice. "
        "Vary camera movements, subject framing, and visual mood between clips. "
        "Output numbered prompts like '1. prompt text'. Keep each under 40 words. "
        "Output ONLY the numbered prompts, nothing else."
    )
    system_prompt = _inject_authorized_explicit_planner_guidance(
        system_prompt, nsfw, "video",
    )

    for batch_start in range(0, len(clips), BATCH_SIZE):
        _cancellation_checkpoint(cancel_handle)
        batch = clips[batch_start:batch_start + BATCH_SIZE]
        batch_size = len(batch)

        clip_descriptions = []
        for j, clip in enumerate(batch):
            global_idx = batch_start + j
            desc = _build_clip_description(clip, global_idx, lyrics)
            # Suggest a specific camera angle to encourage variety
            angle = camera_angles[global_idx % len(camera_angles)]
            desc += f"\n   Suggested camera: {angle}"
            clip_descriptions.append(desc)

        clips_text = "\n".join(clip_descriptions)

        user_prompt = (
            f"Music video concept: {style_prompt}\n"
            f"Song tempo: {bpm:.0f} BPM\n\n"
            f"Write {batch_size} UNIQUE video prompts for these clips:\n\n"
            f"{clips_text}\n\n"
            f"Each prompt must be visually DIFFERENT. Use the suggested camera angles. Go:"
        )

        tokens_for_batch = max(max_new_tokens, batch_size * 80 + 1024)

        raw = generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_new_tokens=tokens_for_batch,
            temperature=0.8,
            enable_thinking=_planner_assist_thinking_mode(
                "plan_clip_prompts", response_assist,
            ),
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )

        # Parse numbered lines
        batch_prompts = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
            if cleaned and len(cleaned) > 10:
                batch_prompts.append(cleaned)

        # Pad short batches with style-based fallbacks
        while len(batch_prompts) < batch_size:
            idx = batch_start + len(batch_prompts)
            clip = clips[idx] if idx < len(clips) else {}
            section = clip.get("section_label", "verse")
            angle = camera_angles[idx % len(camera_angles)]
            batch_prompts.append(
                f"{style_prompt}, {section} section, {angle}, cinematic lighting"
            )
        batch_prompts = batch_prompts[:batch_size]

        all_prompts.extend(batch_prompts)
        print(f"[LLM] Planned prompts for clips {batch_start + 1}-{batch_start + batch_size}")

    return all_prompts[:len(clips)]


# Fixed angle categories for start image generation
ANGLE_CATEGORIES = [
    ("wide_establishing", "wide establishing shot, full body visible, environment and background prominent"),
    ("medium_subject", "medium shot, waist-up framing, subject centered, moderate background detail"),
    ("close_up", "close-up shot, face and upper body, shallow depth of field, intimate framing"),
    ("dynamic_angle", "dramatic low angle or Dutch tilt, dynamic perspective, bold composition"),
]


def plan_angle_prompts(
    style_prompt: str,
    num_angles: int = 4,
    nsfw: bool = False,
    *,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> list:
    """Generate image-edit prompts for camera angle variations of a reference photo.

    Uses the LLM to refine each angle category into a short prompt that
    incorporates the user's style description.

    Args:
        style_prompt: Overall visual style/concept from the user
        num_angles: Number of angle variations (default 4)

    Returns:
        List of image-edit prompt strings, one per angle
    """
    _cancellation_checkpoint(cancel_handle)
    angles = ANGLE_CATEGORIES[:num_angles]

    angle_list = "\n".join(
        f"{i + 1}. {name}: {desc}"
        for i, (name, desc) in enumerate(angles)
    )

    system_prompt = (
        "You are a photography director. Write short image-edit prompts that describe "
        "how to reframe a reference photo into different camera angles. "
        "Each prompt should be under 30 words and describe the framing, angle, and mood. "
        "Incorporate the user's style into each prompt. "
        "Output numbered prompts like '1. prompt text'. Output ONLY the numbered prompts."
    )
    system_prompt = _inject_authorized_explicit_planner_guidance(
        system_prompt, nsfw, "image",
    )

    user_prompt = (
        f"Visual style: {style_prompt}\n\n"
        f"Write {len(angles)} image-edit prompts to create these camera angle variations "
        f"of a reference photo:\n\n{angle_list}\n\n"
        f"Each prompt should describe the camera angle and incorporate the visual style. Go:"
    )

    raw = generate(
        prompt=user_prompt,
        system_prompt=system_prompt,
        max_new_tokens=len(angles) * 60,
        temperature=0.7,
        enable_thinking=_planner_assist_thinking_mode(
            "plan_angle_prompts", response_assist,
        ),
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )

    import re
    prompts = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
        if cleaned and len(cleaned) > 10:
            prompts.append(cleaned)

    # Pad with descriptive fallbacks if LLM output was too short
    while len(prompts) < len(angles):
        idx = len(prompts)
        name, desc = angles[idx]
        prompts.append(f"{style_prompt}, {desc}")
    prompts = prompts[:len(angles)]

    print(f"[LLM] Planned {len(prompts)} angle prompts")
    return prompts


# ---------------------------------------------------------------------------
# Song section classification via LLM
# ---------------------------------------------------------------------------

_VALID_SECTION_LABELS = {"intro", "verse", "chorus", "bridge", "outro", "instrumental"}

# Label normalization: maps common LLM output keywords to valid labels.
# Checked in order — "pre-chorus"/"pre chorus" must match before "chorus".
_LABEL_MAP = [
    ("intro", "intro"),
    ("outro", "outro"),
    ("pre-chorus", "bridge"),
    ("pre chorus", "bridge"),
    ("bridge", "bridge"),
    ("hook", "chorus"),
    ("chorus", "chorus"),
    ("verse", "verse"),
    ("instrumental", "instrumental"),
]


def _format_transcript(lyrics: list) -> str:
    """Format lyrics as a timestamped list with repetition and speaker markers.

    Detects lines that appear multiple times in the song (strong chorus signal)
    and includes speaker tags from diarization when available.
    """
    import re as _re

    # Collect texts with timestamps and speaker
    entries = []
    for lyr in lyrics:
        text = lyr.get("text", "").strip()
        if not text:
            continue
        start = int(lyr.get("start", 0))
        speaker = lyr.get("speaker")
        entries.append((start, text, speaker))

    if not entries:
        return ""

    # Count normalized occurrences to find repeated lyrics
    def _normalize(t: str) -> str:
        return _re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

    norm_counts: dict = {}
    for _, text, _ in entries:
        key = _normalize(text)
        if key:
            norm_counts[key] = norm_counts.get(key, 0) + 1

    # Build formatted lines with speaker tag, repetition marker
    lines = []
    for start, text, speaker in entries:
        m, s = divmod(start, 60)
        key = _normalize(text)
        tags = []
        if speaker:
            tags.append(f"[{speaker}]")
        if norm_counts.get(key, 0) >= 2:
            tags.append("[REPEATS]")
        suffix = "  " + " ".join(tags) if tags else ""
        lines.append(f"{m}:{s:02d}  {text}{suffix}")
    return "\n".join(lines)


def _deloop_sections(llm_sections: list) -> list:
    """Truncate if the LLM falls into a repeating pattern (length 2-4).

    Requires 3+ repetitions — 2x is normal in real songs (e.g.
    Verse→Chorus→Verse→Chorus or Pre-Chorus→Chorus→Bridge→Chorus).
    """
    if len(llm_sections) <= 6:
        return llm_sections

    labels = [s["label"] for s in llm_sections]
    for pattern_len in range(2, 5):
        for start_idx in range(3, len(labels) - pattern_len * 3 + 1):
            pattern = labels[start_idx:start_idx + pattern_len]
            repeats = 1
            pos = start_idx + pattern_len
            while pos + pattern_len <= len(labels):
                if labels[pos:pos + pattern_len] == pattern:
                    repeats += 1
                    pos += pattern_len
                else:
                    break
            if repeats >= 3:
                print(f"[LLM] Detected {repeats}x repeating pattern {pattern} "
                      f"at section {start_idx + 1}, truncating")
                return llm_sections[:start_idx]
    return llm_sections


def _normalize_label(raw: str) -> str:
    """Map a raw LLM section label to a valid label."""
    raw = raw.lower()
    for keyword, label in _LABEL_MAP:
        if keyword in raw:
            return label
    return "verse"


# ---------------------------------------------------------------------------
# Speaker diarization helpers for section refinement
# ---------------------------------------------------------------------------

def _build_speaker_runs(lyrics):
    """Find sustained speaker blocks from diarized lyrics.

    Groups consecutive lines by speaker.  Single-line interjections
    (ad-libs, backing vocals) are absorbed into the surrounding block
    to avoid spurious section splits.

    Returns list of dicts [{speaker, start, end, lines}, ...] sorted by time.
    """
    sorted_lyrs = sorted(
        [l for l in lyrics if l.get("speaker") and l.get("text", "").strip()],
        key=lambda l: float(l.get("start", 0)),
    )
    if not sorted_lyrs:
        return []

    # Phase 1: raw consecutive runs
    runs = []
    for lyr in sorted_lyrs:
        spk = lyr["speaker"]
        t = float(lyr.get("start", 0))
        if runs and runs[-1]["speaker"] == spk:
            runs[-1]["end"] = t
            runs[-1]["lines"] += 1
        else:
            runs.append({"speaker": spk, "start": t, "end": t, "lines": 1})

    if len(runs) <= 1:
        return runs

    # Phase 2: absorb single-line interjections into the previous block
    merged = [runs[0]]
    for r in runs[1:]:
        if r["lines"] <= 1:
            merged[-1]["end"] = r["end"]
            merged[-1]["lines"] += r["lines"]
        else:
            merged.append(r)

    # Phase 3: merge consecutive same-speaker blocks created by absorption
    final = [merged[0]]
    for r in merged[1:]:
        if r["speaker"] == final[-1]["speaker"]:
            final[-1]["end"] = r["end"]
            final[-1]["lines"] += r["lines"]
        else:
            final.append(r)

    return final


def _identify_chorus_speaker(lyrics):
    """Identify which speaker tends to sing repeated/hook lines.

    Returns (speaker_id, repeat_ratio) or (None, 0) if no clear signal.
    The speaker with the highest fraction of globally-repeated lines
    is the likely chorus/hook performer.
    """
    import re as _re

    def _norm(t):
        return _re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

    # Count all normalised lines globally
    global_counts: dict = {}
    for lyr in lyrics:
        text = lyr.get("text", "").strip()
        if not text:
            continue
        key = _norm(text)
        if len(key) > 5:
            global_counts[key] = global_counts.get(key, 0) + 1

    # Per-speaker stats
    speaker_stats: dict = {}
    for lyr in lyrics:
        spk = lyr.get("speaker")
        text = lyr.get("text", "").strip()
        if not spk or not text:
            continue
        key = _norm(text)
        if spk not in speaker_stats:
            speaker_stats[spk] = {"total": 0, "repeated": 0}
        speaker_stats[spk]["total"] += 1
        if len(key) > 5 and global_counts.get(key, 0) >= 2:
            speaker_stats[spk]["repeated"] += 1

    if len(speaker_stats) < 2:
        return None, 0

    for s in speaker_stats.values():
        s["ratio"] = s["repeated"] / s["total"] if s["total"] > 0 else 0

    best = max(speaker_stats, key=lambda k: speaker_stats[k]["ratio"])

    # Need a meaningful repeat ratio (>= 10%) to call someone the hook singer
    if speaker_stats[best]["ratio"] < 0.10:
        return None, 0

    # Must be noticeably higher than the next speaker
    ratios = sorted(speaker_stats.values(), key=lambda s: s["ratio"], reverse=True)
    if len(ratios) >= 2 and ratios[0]["ratio"] - ratios[1]["ratio"] < 0.05:
        return None, 0

    return best, speaker_stats[best]["ratio"]


def _refine_sections_with_speakers(sections, lyrics, duration):
    """Split long verse/bridge sections at sustained speaker-change points.

    After the initial repetition-based structure is built, this function
    looks for sections longer than 20 s that contain multiple speaker
    blocks.  When found it subdivides them — the speaker with the most
    repeated lines is labelled as singing hooks/choruses.
    """
    chorus_spk, chorus_ratio = _identify_chorus_speaker(lyrics)

    runs = _build_speaker_runs(lyrics)
    unique_speakers = set(r["speaker"] for r in runs)

    if len(unique_speakers) < 2:
        return sections

    if chorus_spk:
        print(f"[Sections] Hook speaker: {chorus_spk} "
              f"({chorus_ratio:.0%} repeat ratio)")

    refined = []
    verse_num = 0

    for i, sec in enumerate(sections):
        sec_start = sec["start"]
        sec_end = sections[i + 1]["start"] if i + 1 < len(sections) else duration
        sec_label = sec["label"]

        # Only split long verse / bridge sections
        if sec_label not in ("verse", "bridge") or (sec_end - sec_start) < 20:
            if sec_label == "verse":
                verse_num += 1
                sec = dict(sec)
                sec["display_label"] = f"Verse {verse_num}"
            refined.append(sec)
            continue

        # Speaker runs overlapping this section (>= 2 lines each)
        sec_runs = [
            r for r in runs
            if r["end"] > sec_start - 1 and r["start"] < sec_end
            and r["lines"] >= 2
        ]

        if len(sec_runs) < 2:
            if sec_label == "verse":
                verse_num += 1
                sec = dict(sec)
                sec["display_label"] = f"Verse {verse_num}"
            refined.append(sec)
            continue

        print(f"[Sections] Splitting {sec.get('display_label', sec_label)} "
              f"({sec_end - sec_start:.0f}s) into {len(sec_runs)} sub-sections")

        for j, run in enumerate(sec_runs):
            # First sub-section keeps the original section start time
            sub_start = sec_start if j == 0 else int(run["start"])
            sub_end = (
                int(sec_runs[j + 1]["start"]) if j + 1 < len(sec_runs)
                else sec_end
            )

            if sub_end - sub_start < 5:
                continue  # too short — replace_sections_with_structure merges < 5 s

            if chorus_spk and run["speaker"] == chorus_spk:
                refined.append({
                    "label": "chorus",
                    "display_label": "Hook",
                    "start": sub_start,
                })
            else:
                verse_num += 1
                refined.append({
                    "label": "verse",
                    "display_label": f"Verse {verse_num}",
                    "start": sub_start,
                })

    return refined if len(refined) >= 3 else sections


def _classify_by_speakers(lyrics, duration):
    """Build song structure purely from speaker diarization.

    Fallback when repetition detection finds no chorus pattern but
    we have 2+ speakers.  Creates section boundaries at sustained
    speaker changes and uses repetition ratio to label hooks.
    """
    runs = _build_speaker_runs(lyrics)
    unique_speakers = set(r["speaker"] for r in runs)

    if len(unique_speakers) < 2 or len(runs) < 3:
        return []

    chorus_spk, _ = _identify_chorus_speaker(lyrics)

    sections = []
    verse_num = 0

    # Detect intro (instrumental gap before first lyric)
    first_lyric_time = None
    for lyr in sorted(lyrics, key=lambda l: float(l.get("start", 0))):
        if lyr.get("text", "").strip():
            first_lyric_time = float(lyr.get("start", 0))
            break
    if first_lyric_time and first_lyric_time > 3:
        sections.append({"label": "intro", "display_label": "Intro", "start": 0})

    last_lyric_time = max(
        float(l.get("start", 0)) for l in lyrics if l.get("text", "").strip()
    )

    for i, run in enumerate(runs):
        run_end = runs[i + 1]["start"] if i + 1 < len(runs) else (last_lyric_time + 2)
        if run_end - run["start"] < 5:
            continue

        if chorus_spk and run["speaker"] == chorus_spk:
            sections.append({
                "label": "chorus",
                "display_label": "Hook",
                "start": int(run["start"]),
            })
        else:
            verse_num += 1
            sections.append({
                "label": "verse",
                "display_label": f"Verse {verse_num}",
                "start": int(run["start"]),
            })

    # Detect outro
    if duration - last_lyric_time > 10:
        sections.append({
            "label": "outro",
            "display_label": "Outro",
            "start": int(last_lyric_time) + 2,
        })

    if sections:
        print(f"[Classification] Speaker-based structure ({len(sections)} sections):")
        for s in sections:
            m, sec = divmod(s["start"], 60)
            print(f"  [{s['display_label']}] {int(m)}:{sec:02d}")

    return sections if len(sections) >= 3 else []


def _classify_by_repetition(lyrics: list, duration: float) -> list:
    """Build song structure from lyric repetition patterns.

    Finds chorus sections by detecting clusters of repeated lyric lines.
    More reliable than the 2B LLM for verse/chorus identification.

    Returns list of dicts [{label, display_label, start}, ...] or empty list.
    """
    import re as _re

    def _norm(t: str) -> str:
        return _re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

    entries = []
    counts: dict = {}
    for lyr in lyrics:
        text = lyr.get("text", "").strip()
        if not text:
            continue
        key = _norm(text)
        start = float(lyr.get("start", 0))
        if len(key) > 5:
            counts[key] = counts.get(key, 0) + 1
        entries.append({"start": start, "key": key})

    if len(entries) < 6:
        return []

    n = len(entries)

    # Mark each line: repeated if substantial and appears 2+ times
    for e in entries:
        e["rep"] = len(e["key"]) > 5 and counts.get(e["key"], 0) >= 2

    # Chorus mask: 5-line sliding window, chorus zone if >= 3 repeats
    chorus_mask = []
    for i in range(n):
        window = entries[max(0, i - 2):min(n, i + 3)]
        chorus_mask.append(sum(1 for w in window if w["rep"]) >= 3)

    # Extract contiguous chorus regions
    regions = []
    rstart = None
    for i, c in enumerate(chorus_mask):
        if c and rstart is None:
            rstart = i
        elif not c and rstart is not None:
            regions.append((rstart, i - 1))
            rstart = None
    if rstart is not None:
        regions.append((rstart, n - 1))

    if not regions:
        return []

    print(f"[Repetition] Detected {len(regions)} chorus region(s):")
    for cs, ce in regions:
        print(f"  {entries[cs]['start']:.0f}s - {entries[ce]['start']:.0f}s")

    # --- Build section list ---
    sections = []
    verse_num = 0

    first_chorus_time = entries[regions[0][0]]["start"]

    # Intro: look for a gap before substantial lyrics start
    first_substantial = next((e for e in entries if len(e["key"]) > 10), None)
    intro_end = first_substantial["start"] if first_substantial else 0

    if intro_end > 3:
        sections.append({"label": "intro", "display_label": "Intro", "start": 0})

    # Verse before first chorus
    if first_chorus_time > (intro_end + 5):
        verse_num += 1
        sections.append({
            "label": "verse", "display_label": f"Verse {verse_num}",
            "start": int(intro_end),
        })

    # Choruses and inter-chorus verses
    for i, (cs, ce) in enumerate(regions):
        sections.append({
            "label": "chorus", "display_label": "Chorus",
            "start": int(entries[cs]["start"]),
        })

        if i + 1 < len(regions):
            gap_start = ce + 1
            gap_end = regions[i + 1][0]
            if gap_end - gap_start >= 3:
                verse_num += 1
                sections.append({
                    "label": "verse", "display_label": f"Verse {verse_num}",
                    "start": int(entries[gap_start]["start"]),
                })
        else:
            # After last chorus
            if ce + 1 < n:
                post_lines = n - (ce + 1)
                post_time = duration - entries[ce + 1]["start"]
                if post_lines >= 5 and post_time > 15:
                    verse_num += 1
                    sections.append({
                        "label": "verse", "display_label": f"Verse {verse_num}",
                        "start": int(entries[ce + 1]["start"]),
                    })
                elif post_time > 5:
                    sections.append({
                        "label": "outro", "display_label": "Outro",
                        "start": int(entries[ce + 1]["start"]),
                    })

    # Refine with speaker diarization: split long sections at speaker changes
    if len(sections) >= 3:
        sections = _refine_sections_with_speakers(sections, lyrics, duration)

    return sections if len(sections) >= 3 else []


def _map_labels_to_sections(sections: list, structure: list) -> list:
    """Map each audio section to the structure entry containing its midpoint."""
    labels = []
    for sec in sections:
        mid = (sec.get("start", 0) + sec.get("end", 0)) / 2
        best_label = structure[0]["label"]
        for s in structure:
            if s["start"] <= mid:
                best_label = s["label"]
            else:
                break
        labels.append(best_label)
    return labels


def classify_song_sections(
    sections: list,
    lyrics: list,
    duration: float,
    *,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> dict:
    """Classify song sections using repetition detection, with LLM fallback.

    Primary: programmatic detection of repeated lyric clusters (chorus).
    Fallback: LLM-based classification if no repetition pattern is found.

    Returns:
        Dict with:
          labels: List of label strings, one per audio section
          song_structure: List of dicts [{label, display_label, start}, ...]
    """
    _cancellation_checkpoint(cancel_handle)
    empty_result = {"labels": [], "song_structure": []}
    if not sections:
        return empty_result

    fallback_labels = [s.get("label", "verse") for s in sections]

    if not lyrics:
        return {"labels": fallback_labels, "song_structure": []}

    transcript_text = _format_transcript(lyrics)
    if not transcript_text:
        return {"labels": fallback_labels, "song_structure": []}

    print(f"[Classification] Transcript ({len(transcript_text.splitlines())} lines):\n{transcript_text}")

    # --- Primary: repetition-based classification ---
    rep_structure = _classify_by_repetition(lyrics, duration)
    if rep_structure:
        labels = _map_labels_to_sections(sections, rep_structure)
        print(f"[Classification] Repetition-based structure:")
        for s in rep_structure:
            m, sec = divmod(s["start"], 60)
            print(f"  [{s['display_label']}] {m}:{sec:02d}")
        print(f"[Classification] Mapped to {len(sections)} sections: {labels}")
        return {"labels": labels, "song_structure": rep_structure}

    # --- Secondary: speaker diarization-based classification ---
    spk_structure = _classify_by_speakers(lyrics, duration)
    if spk_structure:
        labels = _map_labels_to_sections(sections, spk_structure)
        print(f"[Classification] Mapped to {len(sections)} sections: {labels}")
        return {"labels": labels, "song_structure": spk_structure}

    # --- Fallback: LLM-based classification ---
    import re

    # Strip [REPEATS] markers — they overwhelm the 2B model
    clean_transcript = transcript_text.replace("  [REPEATS]", "")

    print(f"[LLM] No repetition or speaker pattern found, using LLM classification")

    system_prompt = (
        "You are a music analyst. Given a timestamped song transcript, "
        "identify the song sections.\n"
        "Valid sections: [Intro], [Verse 1], [Verse 2], [Pre-Chorus], "
        "[Chorus], [Bridge], [Outro], [Instrumental].\n"
        "Rules:\n"
        "- Chorus: repeated hook/refrain\n"
        "- Verse: narrative lyrics, different each time\n"
        "- Bridge: contrasting section, usually appears once\n"
        "- Intro/Outro: beginning/end\n"
        "- Instrumental: no lyrics\n\n"
        "Output ONLY section labels with start times:\n"
        "[Intro] 0:00\n[Verse 1] 0:10\n[Chorus] 0:55\n"
        "No lyrics, no explanations."
    )

    user_prompt = (
        f"Song transcript ({duration:.0f}s total):\n\n"
        f"{clean_transcript}\n\n"
        f"Song sections:"
    )

    raw = generate(
        prompt=user_prompt,
        system_prompt=system_prompt,
        max_new_tokens=400,
        temperature=0.2,
        enable_thinking=_planner_assist_thinking_mode(
            "classify_song_sections", response_assist,
        ),
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )

    print(f"[LLM] Raw classification output:\n{raw}")

    llm_sections = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r"\[([^\]]+)\]\s*(\d+):(\d+)", line)
        if match:
            label = _normalize_label(match.group(1))
            start_time = int(match.group(2)) * 60 + int(match.group(3))
            llm_sections.append({
                "label": label,
                "display_label": match.group(1).strip(),
                "start": start_time,
            })

    if not llm_sections:
        print("[LLM] Could not parse classification output, using heuristic")
        return {"labels": fallback_labels, "song_structure": []}

    llm_sections = _deloop_sections(llm_sections)

    labels = _map_labels_to_sections(sections, llm_sections)

    print(f"[LLM] Structure: "
          + ", ".join(f"[{s['display_label']}] {s['start']//60}:{s['start']%60:02d}" for s in llm_sections))
    print(f"[LLM] Mapped to {len(sections)} sections: {labels}")

    song_structure = [
        {"label": s["label"], "display_label": s["display_label"], "start": s["start"]}
        for s in llm_sections
    ]

    return {"labels": labels[:len(sections)], "song_structure": song_structure}


# ---------------------------------------------------------------------------
# Unified per-clip video + image prompt planning
# ---------------------------------------------------------------------------

def _parse_performer_map(scene_description: str) -> dict:
    """Extract section→performer mapping from scene description.

    Looks for patterns like "man raps the verses", "woman sings chorus",
    "he performs the bridge", etc. Returns e.g. {"verse": "man", "chorus": "woman"}.
    """
    import re
    mapping = {}
    scene_lower = scene_description.lower()

    # Patterns: "<person> <verb> <section>" or "<section> by <person>"
    _PERSONS = r"(man|woman|guy|girl|boy|he|she|male|female|rapper|singer)"
    _SECTIONS = r"(verse|chorus|bridge|intro|outro|hook)"
    _VERBS = r"(?:raps?|sings?|signs?|performs?|does|delivers?|handles?)"

    # "man raps the verses"
    for m in re.finditer(
        _PERSONS + r"\s+" + _VERBS + r"\s+(?:the\s+)?" + _SECTIONS + r"s?",
        scene_lower,
    ):
        person, section = m.group(1), m.group(2)
        mapping[section] = person

    # "chorus by the woman"
    for m in re.finditer(
        _SECTIONS + r"s?\s+(?:are |is )?(?:by |from )(?:the\s+)?" + _PERSONS,
        scene_lower,
    ):
        section, person = m.group(1), m.group(2)
        mapping[section] = person

    # Normalize pronouns to gendered nouns
    _PRONOUN_MAP = {
        "he": "man", "she": "woman", "guy": "man", "girl": "woman",
        "boy": "man", "male": "man", "female": "woman",
        "rapper": "man", "singer": "woman",
    }
    return {k: _PRONOUN_MAP.get(v, v) for k, v in mapping.items()}


def _dominant_speaker(lyrics: list, start: float, end: float) -> Optional[str]:
    """Find the speaker with the most lines in a time range."""
    if not lyrics:
        return None
    counts: dict = {}
    for l in lyrics:
        if l["start"] < end and l["end"] > start and l.get("speaker"):
            s = l["speaker"]
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _build_clip_description_v2(
    clip: dict, index: int, lyrics: Optional[list] = None,
    performer_map: Optional[dict] = None,
    speaker_names: Optional[dict] = None,
    speaker_roles: Optional[dict] = None,
) -> str:
    """Build a clip description with lyrics context and performer info.

    Uses diarization speaker tags (if available) to tell the LLM exactly
    who to show. Falls back to section-based performer_map otherwise.
    Camera/shot choices are left to the LLM's creative judgment.
    """
    start = clip.get("start", 0)
    end = clip.get("end", 0)
    section = clip.get("section_label", "verse")
    beat_count = clip.get("beat_count", 16)

    # Gather overlapping lyrics
    lyrics_snippet = ""
    if lyrics:
        matching = [l["text"] for l in lyrics if l["start"] < end and l["end"] > start]
        if matching:
            lyrics_snippet = " ".join(matching)

    # Identify who is performing in this clip
    speaker = _dominant_speaker(lyrics, start, end) if lyrics else None
    role = ""
    if speaker and speaker_roles:
        role = speaker_roles.get(speaker, "")

    vocal_info = f'lyrics: "{lyrics_snippet}"' if lyrics_snippet else "instrumental"

    # Performer hint: prefer diarization speaker, fall back to section performer_map
    performer_hint = ""
    if speaker and speaker_names and speaker in speaker_names:
        name = speaker_names[speaker]
        role_suffix = f" ({role})" if role else ""
        performer_hint = f" Performer: the {name}{role_suffix}."
    elif performer_map and section in performer_map:
        performer_hint = f" Performer: the {performer_map[section]}."

    return (
        f"Clip {index + 1}: {section}, {beat_count} beats, {vocal_info}.{performer_hint}"
    )


def _build_fallback_prompt(
    clip: dict, index: int, section: str,
    lyrics: Optional[list] = None,
    speaker_names: Optional[dict] = None,
    prompt_type: str = "image",
) -> str:
    """Build a clip-specific fallback prompt when LLM parsing fails."""
    start = clip.get("start", 0)
    end = clip.get("end", 0)

    # Get performer name for this clip
    performer = ""
    if lyrics and speaker_names:
        speaker = _dominant_speaker(lyrics, start, end)
        if speaker and speaker in speaker_names:
            performer = speaker_names[speaker]

    # Get a lyrics snippet for context
    snippet = ""
    if lyrics:
        matching = [l["text"] for l in lyrics if l["start"] < end and l["end"] > start]
        if matching:
            snippet = " ".join(matching[:2])

    if prompt_type == "video":
        parts = []
        if performer:
            parts.append(f"{performer} performing")
        parts.append(f"{section} section")
        if snippet:
            parts.append(f"matching lyrics about: {snippet[:60]}")
        return ", ".join(parts)
    else:
        parts = []
        if performer:
            parts.append(performer)
        parts.append(f"{section} scene")
        if snippet:
            parts.append(f"inspired by: {snippet[:60]}")
        return ", ".join(parts)


def _director_reference_bundle(
    reference_image_path: Optional[str],
    character_ref_paths: Optional[list],
    character_ref_labels: Optional[list],
    location_ref_paths: Optional[list],
    location_ref_labels: Optional[list],
) -> tuple[Optional[list], str]:
    """Return ordered existing Director refs plus stable role instructions."""
    images: list[str] = []
    roles: list[str] = []

    def add(path, role: str, label: str = ""):
        if not isinstance(path, str) or not os.path.isfile(path) or path in images:
            return
        images.append(path)
        suffix = f" — {label.strip()}" if isinstance(label, str) and label.strip() else ""
        roles.append(f"Picture {len(images)}: {role}{suffix}")

    add(reference_image_path, "main visual/style reference")
    for index, path in enumerate(character_ref_paths or []):
        labels = character_ref_labels or []
        add(path, "character identity reference", labels[index] if index < len(labels) else "")
    for index, path in enumerate(location_ref_paths or []):
        labels = location_ref_labels or []
        add(path, "location/setting reference", labels[index] if index < len(labels) else "")
    return (images or None), "\n".join(roles)


def plan_clip_prompts_and_images(
    clips: list,
    scene_description: str,
    lyrics: Optional[list] = None,
    bpm: float = 120.0,
    max_new_tokens: int = 512,
    reference_image_path: Optional[str] = None,
    character_ref_paths: Optional[list] = None,
    character_ref_labels: Optional[list] = None,
    location_ref_paths: Optional[list] = None,
    location_ref_labels: Optional[list] = None,
    speaker_mappings: Optional[dict] = None,
    prompt_type: str = "both",
    existing_image_prompts: Optional[list] = None,
    nsfw: bool = False,
    visual_style: Optional[str] = None,
    *,
    h3_style_workflow_present: bool = False,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> list:
    """Generate per-clip prompts.  Supports three modes via *prompt_type*:

    - ``"image"`` — generate only starting-frame (image) descriptions.
    - ``"video"`` — generate only video-motion prompts (may use *existing_image_prompts* as context).
    - ``"both"``  — legacy mode, generates V + I in one pass.

    Returns:
        ``prompt_type="image"``  → ``[{"image_prompt": str}, ...]``
        ``prompt_type="video"``  → ``[{"video_prompt": str}, ...]``
        ``prompt_type="both"``   → ``[{"video_prompt": str, "image_prompt": str}, ...]``
    """
    _cancellation_checkpoint(cancel_handle)
    if not clips:
        return []

    import re

    BATCH_SIZE = 100  # Process all clips at once so the LLM sees the full song arc
    all_plans: list = []
    performer_map = _parse_performer_map(scene_description)
    if performer_map:
        print(f"[LLM] Performer map from scene: {performer_map}")

    # Build speaker→name and speaker→role mappings.
    # Prefer explicit UI mappings; fall back to auto-detection via repeat ratio.
    speaker_names: dict = {}
    speaker_roles: dict = {}

    if speaker_mappings:
        # User provided explicit mappings from the UI
        for spk_id, info in speaker_mappings.items():
            if info.get("name"):
                speaker_names[spk_id] = info["name"]
            if info.get("role"):
                speaker_roles[spk_id] = info["role"]
        print(f"[LLM] Speaker mappings from UI: {speaker_names} roles={speaker_roles}")

    elif lyrics and any(l.get("speaker") for l in lyrics):
        # Auto-detect: identify chorus vs verse speaker by repeat ratio
        import re as _re
        def _norm(t): return _re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()
        norm_counts: dict = {}
        for l in lyrics:
            key = _norm(l.get("text", ""))
            if key and len(key) > 5:
                norm_counts[key] = norm_counts.get(key, 0) + 1

        speaker_repeat: dict = {}  # speaker → [repeating_lines, total_lines]
        for l in lyrics:
            s = l.get("speaker")
            if not s:
                continue
            key = _norm(l.get("text", ""))
            if len(key) <= 5:
                continue
            if s not in speaker_repeat:
                speaker_repeat[s] = [0, 0]
            speaker_repeat[s][1] += 1
            if norm_counts.get(key, 0) >= 2:
                speaker_repeat[s][0] += 1

        if len(speaker_repeat) >= 2:
            ranked = sorted(speaker_repeat.items(),
                            key=lambda x: x[1][0] / max(x[1][1], 1), reverse=True)
            chorus_speaker = ranked[0][0]
            verse_speaker = ranked[-1][0]

            chorus_name = performer_map.get("chorus", "woman")
            verse_name = performer_map.get("verse", "man")

            speaker_names[chorus_speaker] = chorus_name
            speaker_names[verse_speaker] = verse_name

            print(f"[LLM] Speaker mapping (auto): {chorus_speaker}→{chorus_name} (chorus), "
                  f"{verse_speaker}→{verse_name} (verse)")
        elif len(speaker_repeat) == 1:
            only_speaker = list(speaker_repeat.keys())[0]
            speaker_names[only_speaker] = performer_map.get("verse", "performer")
            print(f"[LLM] Single speaker: {only_speaker}")


    batch_images, reference_roles = _director_reference_bundle(
        reference_image_path,
        character_ref_paths,
        character_ref_labels,
        location_ref_paths,
        location_ref_labels,
    )
    has_image = bool(batch_images)

    # ── Load LLM guides ──────────────────────────────────────────────
    guides_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_guides")
    def _load_guide(filename):
        p = os.path.join(guides_dir, filename)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
            print(f"[LLM] Loaded guide: {filename} ({len(content)} chars)")
            return content
        return ""

    video_guide = _load_guide("LTX-2_PROMPTING_GUIDE_Embedded_Audio.MD")
    image_guide = _load_guide("QWEN IMAGE EDIT PROMPTING GUIDE.md")

    guide_sections = ""
    if video_guide and prompt_type in ("video", "both"):
        guide_sections += (
            "\n\nVIDEO PROMPTING GUIDE — follow this when writing video prompts:\n"
            "---\n"
            f"{video_guide}\n"
            "---\n"
        )
    # NOTE: The Qwen image edit guide is NOT included here — it was designed for
    # direct user prompting (single edits) and its examples ("Edit the provided image",
    # "Preserve identity") actively conflict with Director mode requirements.
    # The system prompt instructions above are sufficient for Director image prompts.

    # ── Build system prompt per prompt_type ──────────────────────────
    photo_line = (
        "You are given a REFERENCE PHOTO — use it to identify the people, "
        "their clothing, and the setting.\n"
    ) if has_image else ""

    char_rule = (
        "- NEVER use character names in image OR video prompts — neither the image "
        "editor nor the video model can identify people by name. Instead, describe "
        "each person by their VISIBLE appearance: clothing, hair, position in frame, "
        "based on what you visually see in the attached reference image. For example, "
        "write 'the woman in the white lab coat' instead of a character name.\n"
        "- Character names ARE only allowed in spoken dialogue in the video prompt.\n"
        "- Describe people using what you SEE in the photo + Scene description."
    ) if has_image else (
        "- NEVER use character names in image OR video prompts. Instead, describe each "
        "person by their visual appearance: clothing, hair color, position.\n"
        "- Character names ARE only allowed in spoken dialogue in the video prompt.\n"
        "- ONLY use characters and clothing described in the Scene."
    )

    # Shared rules for all music video modes
    shared_rules = (
        "- Use the Scene Concept as your PRIMARY guide for locations, outfits, "
        "props, and activities.\n"
        "- LOCATIONS ARE BINDING: if the Scene Concept names a specific location "
        "or setting, EVERY clip stays in that location unless the Scene Concept "
        "itself calls for a move. Do NOT invent new locations for visual variety — "
        "vary the camera angle, framing, distance, and lighting instead.\n"
        "- Do NOT add subjects, creatures, or objects the Scene Concept and "
        "reference photos don't contain. Any examples in these instructions "
        "show FORMAT only — never copy their content into prompts.\n"
        "- Use the lyrics to inspire mood and visual metaphors, NOT literal text.\n"
        "- If a clip says 'Performer:', that person must appear.\n"
        f"{char_rule}\n"
        "- Do NOT put lyrics or spoken words in prompts.\n"
        "- CRITICAL: Each image and video prompt is generated INDEPENDENTLY with "
        "NO context from other clips. The image generator does NOT know who any "
        "character is. Every prompt must be FULLY SELF-CONTAINED — "
        "re-describe each character's clothing, hair, and appearance, plus the "
        "setting, lighting, and atmosphere in EVERY prompt. Never assume the "
        "generator 'remembers' anything from prior clips.\n"
    )

    if prompt_type == "image":
        if has_image:
            system_prompt = (
                f"You are a creative music video scene designer.\n{photo_line}\n"
                "Each clip's image prompt EDITS the reference photo to create a visually "
                "distinct starting frame. Think like a cinematographer choosing each shot — "
                "vary camera angle, framing, or lighting when it serves the scene, but "
                "maintain continuity when that makes more sense.\n\n"
                "Focus your prompt on WHAT TO CHANGE — new camera angles, different framing, "
                "new settings, repositioned characters, lighting shifts. You do NOT need to "
                "re-describe things that stay the same as the reference photo.\n\n"
                "WHEN TO DESCRIBE CHARACTERS: When you change the setting to a new location, "
                "re-describe characters by clothing/appearance so the editor knows who to place. "
                "When the setting stays the same, you only need to describe characters whose "
                "position changes.\n\n"
                "GOOD: 'change to a dramatic close-up of the woman in the red dress, soft backlight, shallow depth of field'\n"
                "GOOD: 'change the setting to a neon-lit club, the woman in the red dress stands center stage under purple lights'\n"
                "GOOD: 'change to an over-the-shoulder shot from behind the man in the dark jacket, looking at the stage'\n"
                "BAD: 'Edit the provided image. Show Sarah dancing. Preserve character identity.' — uses name, meta-instructions\n"
                "BAD: re-describing the entire reference photo when nothing changed\n\n"
                "RULES:\n"
                f"{shared_rules}"
                "- Use a mix of shot types (close-ups, wide shots, over-shoulder, etc.) "
                "where appropriate — but continuity between consecutive clips is fine when it fits.\n"
                "- The PERFORMER IS the person/character in the reference photo. Anchor them "
                "explicitly in EVERY prompt ('the [descriptor] from the reference image') — "
                "describing them loosely as a new character makes the image model invent a "
                "different-looking one.\n"
                "- NO motion blur, speed lines, or long-exposure effects — the image is a sharp "
                "still frame; motion belongs to the video prompt.\n"
                "- Focus on WHAT TO CHANGE from the reference. Do not re-describe things that stay the same.\n"
                "- Do NOT start with 'Edit the provided image' — just describe the changes.\n"
                "- Do NOT use preservation meta-language ('preserve', 'maintain', 'keep unchanged').\n"
                f"{guide_sections}\n"
                "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
            )
        else:
            system_prompt = (
                "You are a creative music video scene designer.\n\n"
                "For each clip, write a SCENE DESCRIPTION — describe the starting frame: "
                "where the characters are, what they are doing, the setting, lighting, "
                "mood, and composition. Be vivid and specific.\n\n"
                "RULES:\n"
                f"{shared_rules}"
                "- Vary shots creatively — mix close-ups, wide shots, different angles.\n"
                "- Consecutive clips should feel visually DIFFERENT.\n"
                "- Instrumental clips can use establishing shots, environment details, "
                "or abstract visuals.\n"
                f"{guide_sections}\n"
                "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
            )
    elif prompt_type == "video":
        system_prompt = (
            "You write video motion prompts for music video clips.\n"
            "Each clip is a SINGLE CONTINUOUS SHOT — one unbroken camera take with "
            "no cuts or edits. Write a flowing paragraph describing WHO is on screen, "
            "WHAT they are doing, the SETTING with lighting and atmosphere, and how "
            "the CAMERA moves during this one take.\n\n"
            "RULES:\n"
            "- NEVER say 'montage', 'quick cuts', 'cut to', 'series of shots', or "
            "'multiple angles' — these are impossible in a single take.\n"
            f"{shared_rules}"
            "- Include specific camera movement (slow dolly in, tracking shot, "
            "orbit, pan, handheld follow).\n"
            "- Describe character action, body language, and energy matching the "
            "music's mood for that section.\n"
            "- CRITICAL: Each video prompt is generated INDEPENDENTLY — the video "
            "model has NO memory of previous clips and does NOT know who any character "
            "is. You MUST re-describe every character's clothing, hair, and appearance "
            "in EVERY video prompt, even if you described them in the previous prompt.\n"
            "- For performance clips: describe how the performer moves, gestures, "
            "and engages with the camera.\n"
            "- For instrumental/atmospheric clips: focus on environment, lighting "
            "shifts, textures, and cinematic camera moves.\n"
            f"{guide_sections}\n"
            "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
        )
    else:
        # Combined "both" mode — LLM has full creative control
        image_instruction = (
            "- I (image edit): EDITS the reference photo to create the FIRST FRAME BEFORE action begins. "
            "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
            "show the room without them. Focus on WHAT TO CHANGE from reference. "
            "Describe POSES as static states (standing, seated, leaning). "
            "No motion verbs (walking, running, reaching, heaving, turning). "
            "No motion blur, speed lines, or long-exposure effects — the frame is sharp. "
            "Anchor the performer as 'the [descriptor] from the reference image'. "
            "NEVER use names. Actions belong ONLY in V, never in I.\n"
        ) if has_image else (
            "- I (image): the FIRST FRAME BEFORE action begins — a frozen still photograph. "
            "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
            "the room is empty. Describe static poses only (standing, seated, leaning). "
            "No motion verbs (walking, running, reaching, heaving, turning). "
            "Characters (by appearance, not name), positions, setting, lighting, mood.\n"
        )
        system_prompt = (
            f"You are a music video director.\n{photo_line}\n"
            "The user gives you a SCENE CONCEPT describing the overall vibe, locations, "
            "outfits, and activities. Your job is to bring THIS CONCEPT to life across "
            "all the clips.\n\n"
            "PROMPT TYPES:\n"
            "- V (video): a SINGLE CONTINUOUS SHOT — this is where ALL motion, action, "
            "dancing, gestures, and camera movement happen. Describe who is on screen (by "
            "clothing/appearance — re-describe in EVERY prompt), what they DO, setting "
            "with lighting, and camera movement. NEVER say 'montage', 'quick cuts', "
            "'cut to', or 'series of shots'. Character names ARE only allowed in spoken dialogue.\n"
            f"{image_instruction}"
            "- I prompts describe ONE SINGLE STATIC IMAGE — the starting frame that the "
            "video animates FROM. No motion, no actions, no dancing. Just a frozen "
            "establishing shot: where everyone is, what the setting looks like.\n\n"
            "CREATIVE DIRECTION:\n"
            f"{shared_rules}"
            "- YOU choose camera angles, movements, and shot composition.\n"
            "- Vary shots creatively — mix close-ups, wide shots, tracking shots, etc.\n"
            "- Consecutive clips should feel visually DIFFERENT.\n"
            "- Instrumental clips can use establishing shots, environment details, "
            "or abstract visuals.\n"
            "- Do NOT start I prompts with 'Edit the provided image'.\n"
            "- Do NOT use meta-language ('preserve', 'maintain', 'keep unchanged') in I prompts.\n"
            "- DO use action verbs in I prompts: 'change', 'make', 'move to', 'add'.\n"
            f"{guide_sections}\n"
            "Format: '1V. prompt' then '1I. prompt'. Output ONLY numbered prompts."
        )

    # Director V2's phase-two video planner calls this legacy helper directly,
    # bypassing the structured Director renderers.  Keep it on the same
    # conditional style policy: authored/reference style remains authoritative,
    # while an otherwise style-free request defaults to photorealistic realism.
    from services.director.policies import (
        build_visual_style_authority_block,
        build_visual_style_default_block,
    )
    system_prompt = (
        f"{system_prompt}\n\n"
        f"{build_visual_style_default_block(structured_style_present=h3_style_workflow_present)}\n\n"
        f"{build_visual_style_authority_block(visual_style)}"
    )

    # Send the reference image with every batch so the LLM can see who's in the scene
    if reference_roles:
        system_prompt += f"\n\nREFERENCE IMAGE ORDER:\n{reference_roles}"

    explicit_mode = (
        "image" if prompt_type == "image"
        else "video" if prompt_type == "video"
        else "both"
    )
    system_prompt = _inject_authorized_explicit_planner_guidance(
        system_prompt, nsfw, explicit_mode,
    )

    print(f"[LLM] Planning prompts: prompt_type={prompt_type}, {len(clips)} clips")

    for batch_start in range(0, len(clips), BATCH_SIZE):
        _cancellation_checkpoint(cancel_handle)
        batch = clips[batch_start:batch_start + BATCH_SIZE]
        batch_size = len(batch)

        clip_descriptions = []
        for j, clip in enumerate(batch):
            global_idx = batch_start + j
            desc = _build_clip_description_v2(clip, global_idx, lyrics, performer_map, speaker_names, speaker_roles)
            clip_descriptions.append(desc)

        clips_text = "\n".join(clip_descriptions)

        # Build user prompt with context appropriate to the prompt type
        if prompt_type == "image":
            user_prompt = (
                f"Scene Concept: {scene_description}\n\n"
                f"Clips:\n{clips_text}\n\n"
                f"Design scenes based on the Scene Concept above. "
                f"For each clip, write a detailed, self-contained image prompt "
                f"describing the setting, characters, lighting, and composition. "
                f"Output format: 1. prompt, 2. prompt, etc."
            )
            tokens_for_batch = max(max_new_tokens, batch_size * 200 + 512)
        elif prompt_type == "video":
            # Include existing image prompts as context so video prompts are consistent
            context_lines = ""
            if existing_image_prompts:
                img_context = []
                for j in range(batch_size):
                    global_idx = batch_start + j
                    if global_idx < len(existing_image_prompts):
                        ip = existing_image_prompts[global_idx]
                        img_context.append(f"Clip {global_idx + 1} starts as: {ip}")
                if img_context:
                    context_lines = "\nStarting frames:\n" + "\n".join(img_context) + "\n"

            user_prompt = (
                f"Scene Concept: {scene_description}\n{context_lines}\n"
                f"Clips:\n{clips_text}\n\n"
                f"Write a flowing video prompt paragraph for each clip. "
                f"Each prompt should describe the full scene: setting, lighting, "
                f"character action, and camera movement. "
                f"Output format: 1. prompt, 2. prompt, etc."
            )
            tokens_for_batch = max(max_new_tokens, batch_size * 200 + 1024)
        else:
            user_prompt = (
                f"Scene Concept: {scene_description}\n\n"
                f"Clips:\n{clips_text}\n\n"
                f"Direct a music video based on the Scene Concept above. "
                f"Write detailed V and I prompts for each clip. "
                f"Output format: 1V. ... then 1I. ... for each clip."
            )
            tokens_for_batch = max(max_new_tokens, batch_size * 300 + 1024)

        # Add thinking budget so reasoning tokens don't eat into the content budget
        thinking_budget = 8192

        print(f"[LLM] --- Batch clips {batch_start + 1}-{batch_start + batch_size} ({prompt_type}) ---")
        print(f"[LLM] Token budget: {tokens_for_batch} content + {thinking_budget} thinking")
        print(f"[LLM] User prompt:\n{user_prompt}")

        raw = generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_new_tokens=tokens_for_batch,
            temperature=0.8,
            image_paths=batch_images,
            thinking_budget=thinking_budget,
            enable_thinking=_planner_assist_thinking_mode(
                "plan_clip_prompts_and_images", response_assist,
            ),
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )

        print(f"[LLM] Output:\n{raw}")

        if prompt_type in ("image", "video"):
            # Parse simple numbered list: "1. prompt" or "1) prompt"
            prompts_list: list = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
                if cleaned and len(cleaned) > 5:
                    prompts_list.append(cleaned)

            key = "image_prompt" if prompt_type == "image" else "video_prompt"
            if len(prompts_list) < batch_size:
                print(f"[LLM] WARNING: Parsed {len(prompts_list)} prompts but need {batch_size} — using fallback for remaining clips")
            for k in range(batch_size):
                global_idx = batch_start + k
                clip = clips[global_idx] if global_idx < len(clips) else {}
                section = clip.get("section_label", "verse")
                if k < len(prompts_list):
                    p = prompts_list[k]
                else:
                    # Build a clip-specific fallback from clip context
                    p = _build_fallback_prompt(clip, global_idx, section, lyrics, speaker_names, prompt_type)
                all_plans.append({key: p})

        else:
            # Parse V/I pairs (legacy "both" mode)
            video_prompts: list = []
            image_prompts: list = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                v_match = re.match(r"^\d+\s*V[\.\)]\s*(.*)", line, re.IGNORECASE)
                i_match = re.match(r"^\d+\s*I[\.\)]\s*(.*)", line, re.IGNORECASE)
                if v_match and v_match.group(1).strip():
                    video_prompts.append(v_match.group(1).strip())
                elif i_match and i_match.group(1).strip():
                    image_prompts.append(i_match.group(1).strip())
                else:
                    cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
                    if cleaned and len(cleaned) > 10:
                        video_prompts.append(cleaned)

            for k in range(batch_size):
                global_idx = batch_start + k
                clip = clips[global_idx] if global_idx < len(clips) else {}
                section = clip.get("section_label", "verse")

                vp = video_prompts[k] if k < len(video_prompts) else (
                    _build_fallback_prompt(clip, global_idx, section, lyrics, speaker_names, "video")
                )
                ip = image_prompts[k] if k < len(image_prompts) else (
                    _build_fallback_prompt(clip, global_idx, section, lyrics, speaker_names, "image")
                )
                all_plans.append({"video_prompt": vp, "image_prompt": ip})

        print(f"[LLM] Planned {prompt_type} prompts for clips {batch_start + 1}-{batch_start + batch_size}")

    _cancellation_checkpoint(cancel_handle)
    return all_plans[:len(clips)]


# ---------------------------------------------------------------------------
# Short Film: cinematic prompt generation
# ---------------------------------------------------------------------------


def _build_short_film_clip_description(
    clip: dict,
    index: int,
    lyrics: Optional[list] = None,
    speaker_names: Optional[dict] = None,
    characters: Optional[list] = None,
) -> str:
    """Build a clip description for short film mode.

    Focuses on dialogue content and character presence rather than
    musical concepts like beats and sections.
    """
    start = clip.get("start", 0)
    end = clip.get("end", 0)
    duration = end - start

    # Gather dialogue lines in this clip
    dialogue_lines = clip.get("dialogue_lines", [])
    if not dialogue_lines and lyrics:
        dialogue_lines = [
            l["text"] for l in lyrics
            if l["start"] < end and l["end"] > start
        ]

    # Identify speakers in this clip
    speakers_in_clip: list = []
    if lyrics:
        seen = set()
        for l in sorted(lyrics, key=lambda x: x.get("start", 0)):
            if l["start"] < end and l["end"] > start:
                spk = l.get("speaker")
                if spk and spk not in seen:
                    seen.add(spk)
                    name = speaker_names.get(spk, spk) if speaker_names else spk
                    speakers_in_clip.append(name)

    # Build character info
    char_info = ""
    if characters:
        char_names = [c.get("name", "") for c in characters if c.get("name")]
        if char_names:
            char_info = f" Characters: {', '.join(char_names)}."

    if speakers_in_clip:
        char_info = f" On screen: {', '.join(speakers_in_clip)}."

    # Build dialogue snippet
    dialogue_text = ""
    if dialogue_lines:
        snippet = " / ".join(dialogue_lines[:4])
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        dialogue_text = f' Dialogue: "{snippet}"'

    scene_label = clip.get("section_label", "scene")
    return (
        f"Shot {index + 1}: {scene_label}, {duration:.1f}s.{char_info}{dialogue_text}"
    )


def plan_short_film_prompts(
    clips: list,
    scene_description: str,
    lyrics: Optional[list] = None,
    max_new_tokens: int = 512,
    reference_image_path: Optional[str] = None,
    character_ref_paths: Optional[list] = None,
    character_ref_labels: Optional[list] = None,
    location_ref_paths: Optional[list] = None,
    location_ref_labels: Optional[list] = None,
    speaker_mappings: Optional[dict] = None,
    characters: Optional[list] = None,
    prompt_type: str = "both",
    existing_image_prompts: Optional[list] = None,
    nsfw: bool = False,
    visual_style: Optional[str] = None,
    *,
    h3_style_workflow_present: bool = False,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> list:
    """Generate per-clip prompts for short film mode.

    Similar to ``plan_clip_prompts_and_images`` but uses cinematic/narrative
    system prompts instead of music video prompts. Focuses on dialogue,
    character acting, and cinematic camera work.

    Returns same format as plan_clip_prompts_and_images.
    """
    _cancellation_checkpoint(cancel_handle)
    if not clips:
        return []

    import re

    all_plans: list = []

    # Build speaker name map
    speaker_names: dict = {}
    if speaker_mappings:
        for spk_id, info in speaker_mappings.items():
            if info.get("name"):
                speaker_names[spk_id] = info["name"]

    batch_images, reference_roles = _director_reference_bundle(
        reference_image_path,
        character_ref_paths,
        character_ref_labels,
        location_ref_paths,
        location_ref_labels,
    )
    has_image = bool(batch_images)

    # ── Character context for system prompt ──────────────────────
    char_context = ""
    if characters:
        char_lines = []
        for c in characters:
            name = c.get("name", "")
            desc = c.get("description", "")
            if name:
                char_lines.append(f"  - {name}" + (f": {desc}" if desc else ""))
        if char_lines:
            char_context = "Characters:\n" + "\n".join(char_lines) + "\n\n"

    # ── Build system prompt ──────────────────────────────────────
    photo_line = (
        "You are given a REFERENCE PHOTO showing the characters. "
        "Use it to identify the people, their appearance, clothing, and setting.\n"
    ) if has_image else ""

    char_rule = (
        "- NEVER use character names in image OR video prompts — neither the image "
        "editor nor the video model can identify people by name. Instead, describe "
        "each person by their VISIBLE appearance: clothing, hair, position in frame, "
        "based on what you visually see in the attached reference image. For example, "
        "write 'the woman in the white lab coat' instead of 'Dr Ava', or "
        "'the man in the blue shirt' instead of 'Mr Johnson'.\n"
        "- Character names ARE only allowed in spoken dialogue in the video prompt.\n"
        "- Describe characters using what you SEE in the photo + the character descriptions."
    ) if has_image else (
        "- NEVER use character names in image OR video prompts. Instead, describe each "
        "person by their visual appearance: clothing, hair color, position. "
        "For example, 'the tall woman in the red dress' not 'Sarah'.\n"
        "- Character names ARE only allowed in spoken dialogue in the video prompt.\n"
        "- Describe characters using only the character descriptions provided."
    )

    if prompt_type == "image":
        if has_image:
            system_prompt = (
                f"You are a cinematic scene designer for a short film.\n{photo_line}\n"
                f"{char_context}"
                "Each shot's image prompt EDITS the reference photo to create a starting "
                "frame. Think like a cinematographer — vary camera angle, framing, or lighting "
                "when it serves the scene, but maintain continuity when that makes more sense.\n\n"
                "Focus your prompt on WHAT TO CHANGE — new camera angles, different framing, "
                "new settings, repositioned characters, lighting shifts. You do NOT need to "
                "re-describe things that stay the same as the reference photo.\n\n"
                "WHEN TO DESCRIBE CHARACTERS: When you change the setting to a new location, "
                "re-describe characters by clothing/appearance so the editor knows who to place. "
                "When the setting stays the same, you only need to describe characters whose "
                "position changes.\n\n"
                "GOOD EXAMPLES:\n"
                "- 'change to a dramatic close-up of the woman in the white coat, soft backlight from the window'\n"
                "- 'change the setting to a dark alley with rain. The woman in the white coat stands against a wall.'\n"
                "- 'change to an over-the-shoulder shot from behind the man, looking across the room'\n\n"
                "BAD EXAMPLES (never write prompts like this):\n"
                "- 'change to a bright living room. Woman in pink sits on couch. Man sits next to her.' — re-describes what's already in the reference photo\n"
                "- 'Edit the provided image. Show Dr Ava standing.' — uses name, meta-instruction\n\n"
                "RULES:\n"
                "- Use a mix of shot types (close-ups, wide shots, over-shoulder, etc.) where "
                "appropriate — but continuity between consecutive scenes is fine when it fits.\n"
                "- Focus on WHAT TO CHANGE from the reference. Do not re-describe things that stay the same.\n"
                "- When setting changes to a new location, describe the new setting AND re-describe characters by appearance.\n"
                "- Stay faithful to each scene's scripted location — do NOT relocate a scene or invent new places for visual variety.\n"
                "- Match the mood and tone of the dialogue for that scene.\n"
                f"{char_rule}\n"
                "- Do NOT start with 'Edit the provided image'.\n"
                "- Do NOT use preservation meta-language ('preserve', 'maintain', 'keep unchanged').\n"
                "- Do NOT describe actions or motion — that belongs in video prompts.\n"
                "- Keep each prompt under 40 words.\n\n"
                "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
            )
        else:
            system_prompt = (
                f"You are a cinematic scene designer for a short film.\n\n"
                f"{char_context}"
                "For each shot, write a SCENE DESCRIPTION — where the characters are, "
                "what they are doing, their expressions, the lighting and mood.\n\n"
                "RULES:\n"
                "- Match the mood and tone of the dialogue.\n"
                "- Use cinematic composition — think about framing, depth, lighting.\n"
                f"{char_rule}\n"
                "- Keep each prompt under 25 words.\n\n"
                "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
            )
    elif prompt_type == "video":
        system_prompt = (
            f"You write short video motion prompts for a short film.\n"
            f"{char_context}"
            "Each shot is a SINGLE CONTINUOUS TAKE — one unbroken camera move "
            "with no cuts or edits. Describe WHO is on screen (by clothing and "
            "appearance, NOT by name), WHAT they are doing "
            "(gestures, expressions, body language), the SETTING, and how the "
            "CAMERA moves during this one take.\n\n"
            "RULES:\n"
            "- NEVER say 'montage', 'quick cuts', 'cut to', 'series of shots', "
            "or 'multiple angles' — these are impossible in a single take.\n"
            "- NEVER use character names — describe people by clothing/appearance only. "
            "Character names ARE only allowed in spoken dialogue.\n"
            "- CRITICAL: Each video prompt is generated INDEPENDENTLY — the video "
            "model has NO memory of previous scenes and does NOT know who any character "
            "is. You MUST re-describe every character's clothing, hair, and appearance "
            "in EVERY video prompt.\n"
            "- Focus on acting, body language, and emotional expression.\n"
            "- Match camera complexity to the content. For someone talking to camera "
            "or a simple conversation, use steady framing with minimal movement. "
            "For dramatic or action scenes, use expressive camera work "
            "(push in, dolly, tracking, pan, orbit).\n"
            "- Match the camera style to the emotional tone of the dialogue.\n"
            "- Keep each prompt under 25 words.\n\n"
            "Examples: 'The man in the dark suit slowly stands, camera pushes in on his face as he slams the table.' / "
            "'The blonde woman in the red blouse whispers across the table, camera drifts to a close-up of her trembling hands.' / "
            "'Medium shot, the woman in the gray sweater speaks calmly to camera, soft natural light, steady framing.'\n\n"
            "Format: numbered list. '1. prompt' then '2. prompt' etc. Output ONLY numbered prompts."
        )
    else:
        # Combined "both" mode
        image_instruction = (
            "- I (image edit): EDITS the reference photo to create the FIRST FRAME BEFORE action begins. "
            "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
            "show the room without them. Focus on WHAT TO CHANGE from reference. "
            "Describe POSES as static states (standing, seated, leaning). "
            "No motion verbs (walking, running, reaching, heaving, turning). "
            "No motion blur, speed lines, or long-exposure effects — the frame is sharp. "
            "Anchor the performer as 'the [descriptor] from the reference image'. "
            "NEVER use names. Actions belong ONLY in V, never in I.\n"
        ) if has_image else (
            "- I (image): the FIRST FRAME BEFORE action begins — a frozen still photograph. "
            "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
            "the room is empty. Describe static poses only (standing, seated, leaning). "
            "No motion verbs (walking, running, reaching, heaving, turning). "
            "Characters (by appearance, not name), positions, setting, lighting, mood.\n"
        )
        system_prompt = (
            f"You are a short film director.\n{photo_line}\n"
            f"{char_context}"
            "The user gives you a STORY CONCEPT describing the setting, characters, "
            "and narrative. Your job is to bring this story to life shot by shot.\n\n"
            "IMPORTANT — understand what each prompt controls:\n"
            "- V (video): a SINGLE CONTINUOUS SHOT with no cuts or edits. This is where "
            "ALL motion, action, gestures, dialogue, and camera movement happen. "
            "Describe who is on screen (by clothing/appearance — re-describe in EVERY "
            "prompt), what they DO during this take, and how the camera moves. "
            "NEVER say 'montage', 'quick cuts', 'cut to', 'series of shots', or "
            "'multiple angles' — these are impossible in a single take. "
            "Character names ARE only allowed in spoken dialogue.\n"
            f"{image_instruction}"
            "- I prompts describe ONE SINGLE STATIC IMAGE — the starting frame that "
            "the video animates FROM. No motion, no actions, no gestures. Just a frozen "
            "establishing shot: where everyone is, what the setting looks like.\n\n"
            "CINEMATIC DIRECTION:\n"
            "- Match camera complexity to the content. If someone is speaking directly "
            "to camera or having a simple conversation, use steady framing — a medium "
            "or close-up shot with minimal movement. If the story involves action, "
            "multiple locations, or dramatic reveals, use varied cinematic shots "
            "(over-shoulder, tracking, wide establishing shots, close-ups).\n"
            "- Use the DIALOGUE to inform character emotions and body language.\n"
            "- Use the Story Concept for setting, mood, and visual style.\n"
            "- Think like a cinematographer — lighting, depth of field, composition.\n"
            "- Match camera movement to emotional intensity.\n\n"
            "RULES:\n"
            f"{char_rule}\n"
            "- CRITICAL: Each V and I prompt is generated INDEPENDENTLY — the video/image "
            "model has NO memory of previous scenes and does NOT know who any character "
            "is. You MUST re-describe every character's clothing, hair, and appearance "
            "in EVERY V and I prompt, even if you described them in the previous prompt.\n"
            "- Do NOT put dialogue text or spoken words in I prompts.\n"
            "- Do NOT start I prompts with 'Edit the provided image'.\n"
            "- Do NOT use meta-language ('preserve', 'maintain', 'keep unchanged') in I prompts.\n"
            "- DO use action verbs in I prompts: 'change', 'make', 'move to', 'add'.\n"
            "- Keep each prompt under 40 words.\n\n"
            "Format: '1V. prompt' then '1I. prompt'. Output ONLY numbered prompts."
        )

    from services.director.policies import (
        build_visual_style_authority_block,
        build_visual_style_default_block,
    )
    system_prompt = (
        f"{system_prompt}\n\n"
        f"{build_visual_style_default_block(structured_style_present=h3_style_workflow_present)}\n\n"
        f"{build_visual_style_authority_block(visual_style)}"
    )

    if reference_roles:
        system_prompt += f"\n\nREFERENCE IMAGE ORDER:\n{reference_roles}"

    explicit_mode = (
        "image" if prompt_type == "image"
        else "video" if prompt_type == "video"
        else "both"
    )
    system_prompt = _inject_authorized_explicit_planner_guidance(
        system_prompt, nsfw, explicit_mode,
    )

    print(f"[LLM] Short film prompts: prompt_type={prompt_type}, {len(clips)} clips")

    # Process all clips in one batch for narrative coherence
    clip_descriptions = []
    for j, clip in enumerate(clips):
        desc = _build_short_film_clip_description(
            clip, j, lyrics, speaker_names, characters,
        )
        clip_descriptions.append(desc)

    clips_text = "\n".join(clip_descriptions)
    batch_size = len(clips)

    if prompt_type == "image":
        user_prompt = (
            f"Story Concept: {scene_description}\n\n"
            f"Shots:\n{clips_text}\n\n"
            f"Design each shot's starting frame based on the Story Concept and dialogue context. "
            f"Output format: 1. description, 2. description, etc."
        )
        tokens_for_batch = max(max_new_tokens, batch_size * 80)
    elif prompt_type == "video":
        context_lines = ""
        if existing_image_prompts:
            img_context = [
                f"Shot {i + 1} starts as: {ip}"
                for i, ip in enumerate(existing_image_prompts) if ip
            ]
            if img_context:
                context_lines = "\nStarting frames:\n" + "\n".join(img_context) + "\n"

        user_prompt = (
            f"Story Concept: {scene_description}\n{context_lines}\n"
            f"Shots:\n{clips_text}\n\n"
            f"Write a video prompt for each shot. Focus on character acting, "
            f"body language, and cinematic camera movement. "
            f"Output format: 1. prompt, 2. prompt, etc."
        )
        tokens_for_batch = max(max_new_tokens, batch_size * 80 + 1024)
    else:
        user_prompt = (
            f"Story Concept: {scene_description}\n\n"
            f"Shots:\n{clips_text}\n\n"
            f"Direct this short film scene by scene. "
            f"Write V and I prompts for each shot. "
            f"Output format: 1V. ... then 1I. ... for each shot."
        )
        tokens_for_batch = max(max_new_tokens, batch_size * 150 + 1024)

    thinking_budget = 8192
    print(f"[LLM] Token budget: {tokens_for_batch} content + {thinking_budget} thinking")
    print(f"[LLM] User prompt:\n{user_prompt}")

    raw = generate(
        prompt=user_prompt,
        system_prompt=system_prompt,
        max_new_tokens=tokens_for_batch,
        temperature=0.8,
        image_paths=batch_images,
        thinking_budget=thinking_budget,
        enable_thinking=_planner_assist_thinking_mode(
            "plan_short_film_prompts", response_assist,
        ),
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )

    print(f"[LLM] Output:\n{raw}")

    if prompt_type in ("image", "video"):
        prompts_list: list = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
            if cleaned and len(cleaned) > 5:
                prompts_list.append(cleaned)

        key = "image_prompt" if prompt_type == "image" else "video_prompt"
        for k in range(batch_size):
            if k < len(prompts_list):
                all_plans.append({key: prompts_list[k]})
            else:
                fallback = f"Cinematic shot {k + 1}, {clips[k].get('section_label', 'scene')}"
                all_plans.append({key: fallback})
    else:
        video_prompts: list = []
        image_prompts: list = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            v_match = re.match(r"^\d+\s*V[\.\)]\s*(.*)", line, re.IGNORECASE)
            i_match = re.match(r"^\d+\s*I[\.\)]\s*(.*)", line, re.IGNORECASE)
            if v_match and v_match.group(1).strip():
                video_prompts.append(v_match.group(1).strip())
            elif i_match and i_match.group(1).strip():
                image_prompts.append(i_match.group(1).strip())
            else:
                cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
                if cleaned and len(cleaned) > 10:
                    video_prompts.append(cleaned)

        for k in range(batch_size):
            vp = video_prompts[k] if k < len(video_prompts) else f"Cinematic shot {k + 1}"
            ip = image_prompts[k] if k < len(image_prompts) else f"Scene {k + 1} establishing frame"
            all_plans.append({"video_prompt": vp, "image_prompt": ip})

    _cancellation_checkpoint(cancel_handle)
    return all_plans[:len(clips)]


# ---------------------------------------------------------------------------
# Short Film Path C — plan scenes from a story description (no audio)
# ---------------------------------------------------------------------------


def plan_short_film_from_story(
    story_description: str,
    characters: Optional[list] = None,
    reference_image_path: Optional[str] = None,
    character_ref_paths: Optional[list] = None,
    character_ref_labels: Optional[list] = None,
    location_ref_paths: Optional[list] = None,
    location_ref_labels: Optional[list] = None,
    target_duration: int = 30,
    target_scenes: Optional[int] = None,
    narrative_mode: bool = True,
    fps: int = 24,
    frames_steps: int = 4,
    frames_minimum: int = 5,
    max_new_tokens: int = 1024,
    nsfw: bool = False,
    visual_style: Optional[str] = None,
    *,
    h3_style_workflow_present: bool = False,
    response_assist: Optional[dict] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    cancel_handle: Optional[LlmCancellationHandle] = None,
) -> dict:
    """Plan a short film scene structure from a story description.

    Unlike ``plan_dialogue_scenes`` which analyses uploaded audio, this uses
    the LLM to create scenes from scratch — deciding how many scenes there
    are, what dialogue occurs, and how to pace them within *target_duration*.

    Returns ``{"clips": [...], "clip_plans": [...]}``:
    - clips: same format as ``plan_dialogue_scenes`` output
    - clip_plans: same format as ``plan_short_film_prompts`` output
    """
    _cancellation_checkpoint(cancel_handle)
    import json as _json
    import re

    from services.audio_analysis import _snap_to_valid_frames

    if not target_scenes:
        # ~15 seconds per scene, cap at 30 scenes
        target_scenes = max(2, min(30, target_duration // 15))

    batch_images, reference_roles = _director_reference_bundle(
        reference_image_path,
        character_ref_paths,
        character_ref_labels,
        location_ref_paths,
        location_ref_labels,
    )
    has_image = bool(batch_images)

    # ── Character context ─────────────────────────────────────────
    char_context = ""
    if characters:
        char_lines = []
        for c in characters:
            name = c.get("name", "")
            desc = c.get("description", "")
            if name:
                char_lines.append(f"  - {name}" + (f": {desc}" if desc else ""))
        if char_lines:
            char_context = "Characters:\n" + "\n".join(char_lines) + "\n\n"

    photo_line = (
        "You are given a REFERENCE PHOTO showing the characters. "
        "Use it to identify the people, their appearance, clothing, and setting.\n"
    ) if has_image else ""

    char_rule = (
        "- NEVER use character names in image_prompt OR video_prompt — neither the "
        "image editor nor the video model can identify people by name. Instead, "
        "describe each person by their VISIBLE appearance: clothing, hair, position "
        "in frame, based on what you visually see in the attached reference image. "
        "For example, write 'the woman in the white lab coat' instead of a character name.\n"
        "- Character names ARE only allowed in spoken dialogue within the video_prompt.\n"
        "- Describe characters using what you SEE in the photo + the character descriptions."
    ) if has_image else (
        "- NEVER use character names in image_prompt OR video_prompt. Instead, describe "
        "each person by their visual appearance: clothing, hair color, position.\n"
        "- Character names ARE only allowed in spoken dialogue within the video_prompt.\n"
        "- Describe characters using only the character descriptions provided."
    )

    # ── Load prompting guides ────────────────────────────────────
    guides_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_guides")

    def _load_guide(filename):
        p = os.path.join(guides_dir, filename)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
            print(f"[LLM] Loaded guide: {filename} ({len(content)} chars)")
            return content
        return ""

    video_guide = _load_guide("LTX-2_PROMPTING_GUIDE_Embedded_Audio.MD")
    story_guide = _load_guide("Expert short-form storyteller.md") if narrative_mode else ""

    # ── System prompt: scene planner + prompt writer ──────────────
    image_instruction = (
        "- image_prompt: EDITS the reference photo to create the FIRST FRAME BEFORE action begins. "
        "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
        "show the room without them. Focus on WHAT TO CHANGE from reference. "
        "Describe POSES as static states (standing, seated, leaning). "
        "No motion verbs (walking, running, reaching, heaving, turning). "
        "Actions belong ONLY in video_prompt, never in image_prompt. "
        "NEVER use character names or meta-instructions.\n"
    ) if has_image else (
        "- image_prompt: the FIRST FRAME BEFORE action begins — a frozen still photograph. "
        "Show the INITIAL STATE: if clothing will be removed, it's still on; if someone enters, "
        "the room is empty. Describe static poses only (standing, seated, leaning). "
        "No motion verbs (walking, running, reaching, heaving, turning). "
        "Characters (by appearance, NOT by name), positions, setting, lighting, mood.\n"
    )

    # Build guide sections
    guide_sections = ""
    if video_guide:
        guide_sections += (
            "\n\nVIDEO PROMPTING GUIDE — follow this when writing video_prompt values:\n"
            "---\n"
            f"{video_guide}\n"
            "---\n"
        )
    # NOTE: The Qwen image edit guide is NOT included — its examples conflict
    # with Director mode requirements (uses character names, meta-instructions).
    # The system prompt rules are sufficient for Director image prompts.

    # Narrative vs non-narrative role
    if narrative_mode and story_guide:
        role_section = (
            "You are a short film director, screenwriter, and expert storyteller.\n"
            f"{photo_line}\n{char_context}"
            "Follow this storytelling guide when structuring your scenes:\n"
            "---\n"
            f"{story_guide}\n"
            "---\n\n"
            "The user gives you a STORY CONCEPT. Plan the short film with a clear "
            "narrative arc: setup, rising conflict, climax, and resolution.\n"
        )
    else:
        role_section = (
            f"You are a short film director and screenwriter.\n{photo_line}\n"
            f"{char_context}"
            "The user gives you a CONCEPT. Plan the short film as a sequence of "
            "visually compelling scenes that cover the concept thoroughly. "
            "Focus on variety, pacing, and visual impact rather than narrative arc.\n"
        )

    system_prompt = (
        f"{role_section}"
        f"Break the concept into scenes within {target_duration} seconds total. "
        f"YOU decide how many scenes based on the story — let pacing dictate the cuts.\n"
        "For each scene, write a video_prompt and image_prompt.\n\n"
        "PROMPT TYPES:\n"
        "- video_prompt: a SINGLE CONTINUOUS SHOT — one flowing paragraph following the "
        "video prompting guide below. Each clip can be up to 20 seconds long. "
        "NEVER say 'montage', 'quick cuts', 'cut to'. "
        "Each video_prompt is rendered independently with no memory of other scenes "
        "and the video model does NOT know who any character is. "
        "NEVER use character names in video_prompt — describe people by clothing and "
        "appearance. Character names ARE only allowed in spoken dialogue. "
        "Re-describe every character's clothing, hair, and appearance in EVERY "
        "video_prompt, plus re-state all relevant visual context (weather, time of day, "
        "environment state) within each prompt.\n"
        "CAMERA STYLE — match to the content:\n"
        "- If the concept is someone talking to camera, giving a speech, or having a "
        "simple conversation in one place: use steady, consistent framing (medium or "
        "close-up shots). Minimal camera movement. Keep image_prompts similar across "
        "scenes — same angle, same setting, subtle lighting or expression changes only.\n"
        "- If the concept is a narrative with action, multiple locations, or dramatic "
        "reveals: use varied cinematic shots (over-shoulder, tracking, wide establishing, "
        "close-ups). Vary image_prompts to show different angles, settings, and compositions.\n"
        "- Let the story concept guide you — don't force cinematic complexity onto simple content.\n\n"
        f"{image_instruction}\n"
        "IMAGE PROMPT GUIDE (how to write image_prompt values):\n"
        "The image_prompt EDITS the reference photo to create a starting frame for each "
        "scene. Match your approach to the camera style above — vary angle and framing "
        "when the story calls for it, maintain continuity when it doesn't.\n\n"
        "The image editor starts with the reference photo and applies your changes. "
        "Focus your prompt on WHAT TO CHANGE — new camera angles, different framing, "
        "new settings, repositioned characters, lighting shifts. You do NOT need to "
        "re-describe things that stay the same as the reference photo.\n\n"
        "IMPORTANT — what each prompt does:\n"
        "- image_prompt = the FIRST FRAME BEFORE action begins. Describe the shot setup: camera "
        "angle, framing, setting, lighting, character positions.\n"
        "- video_prompt = all motion, action, dialogue, gestures, camera movement\n\n"
        "WHEN TO DESCRIBE CHARACTERS: When you change the setting to a new location, "
        "re-describe characters by clothing/appearance so the editor knows who to place. "
        "When the setting stays the same, you only need to describe characters whose "
        "position changes.\n\n"
        "GOOD image_prompt examples (assuming reference = people sitting on couch in living room):\n"
        "- 'change to a dramatic close-up of the blonde woman in the pink shirt, soft "
        "backlight from the window, shallow depth of field'\n"
        "- 'change the setting to a restaurant at night. The blonde woman in the pink "
        "shirt and the man in the dark shirt sit across from each other at a candlelit table.'\n"
        "- 'change to an over-the-shoulder shot from behind the man in the dark shirt, "
        "looking at the girl in the light blue dress across the room'\n\n"
        "BAD image_prompt examples (NEVER write like this):\n"
        "- 'change to a bright modern living room. Blonde woman in pink shirt sits on "
        "beige couch. Man in dark shirt sits in middle. Girl in light blue dress sits "
        "on right.' — re-describes everything already in the reference with no visual change\n"
        "- 'The girl in blue raises her hand' — this is ACTION, belongs in video_prompt\n"
        "- 'Dr Ava stands in the hallway' — uses character name\n\n"
        "RULES for image_prompt:\n"
        "- Use a mix of shot types (close-ups, wide shots, over-shoulder, etc.) where "
        "appropriate — but continuity between consecutive scenes is fine when it fits.\n"
        "- Focus on WHAT TO CHANGE from the reference. Do not re-describe things that "
        "stay the same.\n"
        "- When setting changes to a new location, describe the new setting AND "
        "re-describe which characters are present by clothing/appearance.\n"
        "- If the user's concept pins the story to a specific location, every scene "
        "stays in that location — do NOT relocate scenes or invent new places for "
        "visual variety.\n"
        "- When setting stays the same, you can keep the same framing or adjust it — "
        "only mention characters whose positions change.\n"
        "- NEVER use character names — describe people by clothing/appearance only, "
        "based on what you visually see in the attached reference image\n"
        "- Character names ARE only allowed in spoken dialogue in the video_prompt\n"
        "- Do NOT start with 'Edit the provided image'\n"
        "- Do NOT use meta-language ('preserve', 'maintain', 'keep unchanged')\n"
        "- image_prompt = STILL PHOTOGRAPH. No motion verbs whatsoever: no walking, "
        "running, reaching, heaving, turning, gesturing, raising, dancing. "
        "Describe static poses only (standing, seated, leaning). All action belongs in video_prompt.\n"
        "- Carry forward cumulative visual state changes: if a character got wet, "
        "injured, or changed clothes earlier, mention that difference.\n"
        f"{char_rule}\n"
        f"{guide_sections}\n"
        "OUTPUT FORMAT — respond with ONLY a JSON array, no markdown fences, no thinking tags:\n"
        "[\n"
        '  {"title": "Scene title", "duration": 15, '
        '"dialogue": ["Character: \\"Full sentence of dialogue matching clip length\\""], '
        '"scene_type": "dialogue|action|opening|closing", '
        '"video_prompt": "Single flowing paragraph describing action, setting, and lighting. '
        'Character speaks with emotion, \\"Full dialogue woven into the scene with speaker cues '
        'and enough words to fill most of the clip duration.\\" '
        'Camera movement and reaction described.", '
        '"image_prompt": "Starting frame description"}\n'
        "]\n\n"
        "CRITICAL RULES:\n"
        f"- The durations must sum to approximately {target_duration} seconds.\n"
        "- Each scene should be 10-20 seconds long.\n"
        "- Every scene MUST be unique — never repeat the same video_prompt or image_prompt.\n"
        "- Do NOT repeat scenes to fill the duration. Use fewer, longer scenes instead.\n"
        '- DIALOGUE IN VIDEO_PROMPT: Any spoken dialogue MUST appear inside the video_prompt '
        'as quoted text with a speaker cue, woven into the scene description. '
        "The dialogue field is just a metadata summary — the video_prompt is what the "
        "video model actually reads and generates from.\n"
        "- DIALOGUE LENGTH: People speak at ~2 words per second. Aim for roughly "
        "duration × 2 words of dialogue per scene (e.g. ~20 words for a 10s scene, "
        "~30 for 15s). Don't write throwaway one-liners, but don't overpack either. "
        "The system will adjust if needed."
    )

    from services.director.policies import (
        build_visual_style_authority_block,
        build_visual_style_default_block,
    )
    system_prompt = (
        f"{system_prompt}\n\n"
        f"{build_visual_style_default_block(structured_style_present=h3_style_workflow_present)}\n\n"
        f"{build_visual_style_authority_block(visual_style)}"
    )

    user_prompt = f"Story Concept: {story_description}"

    if reference_roles:
        system_prompt += f"\n\nREFERENCE IMAGE ORDER:\n{reference_roles}"

    system_prompt = _inject_authorized_explicit_planner_guidance(
        system_prompt, nsfw, "director",
    )

    print(f"[LLM] Planning short film from story: {target_scenes} scenes, {target_duration}s")
    print(f"[LLM] Story: {story_description}")
    print(f"[LLM] Token budget: {target_scenes * 400 + 256} content + 8192 thinking = {target_scenes * 400 + 256 + 8192} total")

    # Scale tokens to scene count — each scene needs ~200 tokens of JSON
    # Guide-quality prompts are longer (~400 tokens/scene with rich descriptions)
    tokens_needed = max(max_new_tokens, target_scenes * 400 + 256)

    # Reserve extra tokens for model thinking/reasoning so it doesn't eat
    # into the content budget. The 27B model can use 5000+ thinking tokens.
    thinking_budget = 8192

    raw = generate_streaming(
        prompt=user_prompt,
        system_prompt=system_prompt,
        max_new_tokens=tokens_needed,
        temperature=0.8,
        image_paths=batch_images,
        thinking_budget=thinking_budget,
        enable_thinking=_planner_assist_thinking_mode(
            "plan_short_film_from_story", response_assist,
        ),
        response_assist=response_assist,
        progress_callback=progress_callback,
        cancel_handle=cancel_handle,
    )

    _cancellation_checkpoint(cancel_handle)
    print(f"[LLM] Story plan output:\n{raw}")

    # ── Parse JSON response ───────────────────────────────────────
    cleaned = raw.strip()

    # Strip thinking blocks (Qwen <think>...</think> and Gemma <|channel>thought\n...<channel|>)
    cleaned = _strip_thinking_tags(cleaned).strip()

    # Strip markdown fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    scenes = []
    try:
        scenes = _json.loads(cleaned)
    except _json.JSONDecodeError:
        # Try to find the LAST JSON array (skip any in think block remnants)
        matches = list(re.finditer(r"\[[\s\S]*?\](?=\s*$)", cleaned))
        if matches:
            try:
                scenes = _json.loads(matches[-1].group())
            except _json.JSONDecodeError:
                pass
        if not scenes:
            # Broader search
            match = re.search(r"\[[\s\S]*\]", cleaned)
            if match:
                try:
                    scenes = _json.loads(match.group())
                except _json.JSONDecodeError:
                    pass

    # If full parse failed, try to salvage complete JSON objects from truncated output
    if not scenes:
        obj_pattern = re.finditer(
            r'\{\s*"title"\s*:.*?"image_prompt"\s*:\s*"[^"]*"\s*\}',
            cleaned, re.DOTALL,
        )
        salvaged = [m.group() for m in obj_pattern]
        for s in salvaged:
            try:
                scenes.append(_json.loads(s))
            except _json.JSONDecodeError:
                continue
        if scenes:
            print(f"[LLM] Salvaged {len(scenes)} complete scenes from truncated output")

    if not scenes:
        # Fallback: create evenly spaced scenes
        print("[LLM] WARNING: Could not parse scene plan, using fallback")
        scene_dur = target_duration / target_scenes
        for i in range(target_scenes):
            scenes.append({
                "title": f"Scene {i + 1}",
                "duration": scene_dur,
                "dialogue": [],
                "scene_type": "action",
                "video_prompt": f"Cinematic shot {i + 1}, characters in motion",
                "image_prompt": f"Scene {i + 1} establishing frame",
            })

    # ── Post-process: deduplicate and cap dialogue ────────────────
    # Remove scenes with identical video_prompt (LLM repetition loop)
    seen_prompts = set()
    unique_scenes = []
    for scene in scenes:
        vp = scene.get("video_prompt", "")
        if vp not in seen_prompts:
            seen_prompts.add(vp)
            unique_scenes.append(scene)
        else:
            print(f"[LLM] Removed duplicate scene: {scene.get('title', '?')}")
    if len(unique_scenes) < len(scenes):
        print(f"[LLM] Deduplicated {len(scenes)} → {len(unique_scenes)} scenes")
        scenes = unique_scenes

    # Cap dialogue lines per scene to prevent repetition loops
    MAX_DIALOGUE_LINES = 6
    for scene in scenes:
        dialogue = scene.get("dialogue", [])
        if len(dialogue) > MAX_DIALOGUE_LINES:
            print(f"[LLM] Capped dialogue in '{scene.get('title', '?')}': {len(dialogue)} → {MAX_DIALOGUE_LINES} lines")
            scene["dialogue"] = dialogue[:MAX_DIALOGUE_LINES]

    # ── Dialogue budget check: ask LLM to rewrite over-budget scenes ──
    # People speak at ~2-2.5 words per second. If dialogue exceeds that,
    # ask the LLM to condense just those scenes (it keeps narrative sense).
    over_budget = []
    for i, scene in enumerate(scenes):
        vp = scene.get("video_prompt", "")
        duration = float(scene.get("duration", 15))
        max_words = int(duration * 2.5)
        quotes = re.findall(r'"([^"]*)"', vp)
        if not quotes:
            continue
        total_words = sum(len(q.split()) for q in quotes)
        if total_words > max_words:
            over_budget.append((i, scene, total_words, max_words))

    if over_budget:
        _cancellation_checkpoint(cancel_handle)
        print(f"[LLM] {len(over_budget)} scene(s) have dialogue over budget, requesting rewrite")
        rewrite_lines = []
        for idx, scene, actual, budget in over_budget:
            rewrite_lines.append(
                f"Scene {idx + 1} \"{scene.get('title', '')}\" ({scene.get('duration', 15)}s): "
                f"has {actual} words of dialogue, max {budget}. "
                f"Current video_prompt: {scene.get('video_prompt', '')}"
            )

        rewrite_prompt = (
            "The following scenes have too much spoken dialogue for their duration. "
            "People speak at about 2 words per second — if there are too many words, "
            "the actor will speak unnaturally fast or get cut off.\n\n"
            "Condense the dialogue in each video_prompt to fit the word budget. "
            "Keep the same meaning and narrative flow — just say it more concisely. "
            "Keep all non-dialogue parts (action, camera, setting) unchanged.\n\n"
            + "\n\n".join(rewrite_lines) + "\n\n"
            "Output ONLY the rewritten video_prompt for each scene, numbered to match:\n"
            f"Format: '{over_budget[0][0] + 1}. rewritten video_prompt'"
        )

        rewrite_raw = generate(
            prompt=rewrite_prompt,
            system_prompt="You condense dialogue to fit time constraints while preserving meaning and story continuity.",
            max_new_tokens=len(over_budget) * 200,
            temperature=0.5,
            thinking_budget=1024,
            enable_thinking=_planner_assist_thinking_mode(
                "plan_short_film_from_story", response_assist,
            ),
            response_assist=response_assist,
            progress_callback=progress_callback,
            cancel_handle=cancel_handle,
        )
        print(f"[LLM] Rewrite output:\n{rewrite_raw}")

        # Parse rewritten prompts and apply them
        for line in rewrite_raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d+)[\.\)]\s*(.*)", line)
            if m:
                scene_num = int(m.group(1))
                new_vp = m.group(2).strip()
                if len(new_vp) < 20:
                    continue
                # Find the matching over-budget scene
                for idx, scene, actual, budget in over_budget:
                    if idx + 1 == scene_num:
                        # Verify the rewrite actually reduced dialogue
                        new_quotes = re.findall(r'"([^"]*)"', new_vp)
                        new_word_count = sum(len(q.split()) for q in new_quotes)
                        print(f"[LLM] Scene {scene_num} dialogue: {actual} → {new_word_count} words (budget: {budget})")
                        scene["video_prompt"] = new_vp
                        # Update dialogue metadata from the new prompt
                        if new_quotes:
                            scene["dialogue"] = [f'"{q}"' for q in new_quotes]
                        break

    # ── Convert scenes to clip dicts ──────────────────────────────
    clips = []
    clip_plans = []
    current_time = 0.0

    for i, scene in enumerate(scenes):
        scene_dur = float(scene.get("duration", target_duration / len(scenes)))
        scene_dur = max(3.0, min(scene_dur, 20.0))  # clamp to reasonable range

        clip_start = round(current_time, 3)
        clip_end = round(current_time + scene_dur, 3)

        dialogue = scene.get("dialogue", [])
        scene_type = scene.get("scene_type", "dialogue" if dialogue else "action")

        clips.append({
            "start": clip_start,
            "end": clip_end,
            "beat_count": 0,
            "section_label": scene_type,
            "energy": 0.5,
            "suggested_prompt_hint": scene.get("title", f"Scene {i + 1}"),
            "duration_frames": _snap_to_valid_frames(scene_dur, fps, frames_steps, frames_minimum),
            "dominant_speaker": None,
            "dialogue_lines": dialogue,
        })

        clip_plans.append({
            "video_prompt": scene.get("video_prompt", f"Cinematic shot {i + 1}"),
            "image_prompt": scene.get("image_prompt", f"Scene {i + 1} establishing frame"),
        })

        current_time += scene_dur

    _cancellation_checkpoint(cancel_handle)
    return {"clips": clips, "clip_plans": clip_plans}
