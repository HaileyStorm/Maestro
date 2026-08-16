"""Non-executable source contract for MiniMax Music 3 on SGLang-Omni.

This module validates values that a future runtime adapter would exchange.  It
does not discover, download, launch, register, or authorize that adapter.  In
particular, the external license, acceptable-use, attribution, locality, and
hosting decisions listed below cannot be satisfied by anything in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, InitVar
import hashlib
import io
import math
import wave

from services.music_model_contract import (
    MAX_LYRICS_BYTES,
    MusicModelContractError,
    MusicModelIdentity,
    UnsupportedMusicRequest,
    parse_music_model_identity,
)


MUSIC3_MODEL_ID = "MiniMaxAI/MiniMax-Music3"
MUSIC3_HF_REVISION = "fbdf52fbaaca799592917417eb05f1899f1255ec"
MUSIC3_HF_EXACT_REVISION = f"git:{MUSIC3_HF_REVISION}"

SGLANG_ENGINE_ID = "sglang-omni"
SGLANG_RESPONSE_FORMAT = "wav"
MIN_NEW_TOKENS = 1
MAX_NEW_TOKENS = 9_000
MIN_SEED = 0
MAX_SEED = 2**64 - 1
MAX_INSTRUCTIONS_BYTES = 64 * 1024
MAX_WAV_BYTES = 512 * 1024 * 1024
MAX_HEALTH_STAGES = 64
MAX_HEALTH_STATES = 64
MAX_COUNTER = 2**63 - 1

WAV_SAMPLE_RATE = 32_000
WAV_SAMPLE_WIDTH_BYTES = 2
WAV_CHANNELS = 2

REQUIRED_REQUEST_KEYS = frozenset({
    "model",
    "input",
    "instructions",
    "response_format",
    "seed",
    "max_new_tokens",
    "stream",
})
OPTIONAL_REQUEST_KEYS = frozenset({"speed"})
UNSUPPORTED_REQUEST_FIELDS = frozenset({
    "voice",
    "ref_audio",
    "ref_text",
    "language",
    "task_type",
    "temperature",
    "top_p",
    "top_k",
    "repetition_penalty",
})
LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE = "local_experiment"
HOSTED_SERVICE_AUTHORIZATION_SCOPE = "hosted_service"
LOCAL_EXPERIMENT_REQUIRED_GATES = (
    "acceptable_use_approval",
    "attribution_approval",
    "license_approval",
    "locality_approval",
    "united_states_approval",
    "local_experiment_approval",
)
SUPPORTED_AUTHORIZATION_SCOPES = (LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,)
UNAPPROVED_AUTHORIZATION_SCOPES = (HOSTED_SERVICE_AUTHORIZATION_SCOPE,)
_VALIDATED_RESPONSE_TOKEN = object()


def _bounded_text(value: object, *, label: str, maximum_bytes: int) -> str:
    if type(value) is not str:
        raise MusicModelContractError(f"{label} must be text")
    if not value or value != value.strip():
        raise MusicModelContractError(f"{label} must be non-empty and trimmed")
    if "\x00" in value:
        raise MusicModelContractError(f"{label} contains a NUL character")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise MusicModelContractError(f"{label} is not valid Unicode") from error
    if len(encoded) > maximum_bytes:
        raise MusicModelContractError(f"{label} is too large")
    return value


def _exact_content_address(value: object) -> str:
    """Reuse the generic identity parser for exact revision validation."""

    identity = parse_music_model_identity({
        "purpose": "music.runtime-source",
        "provider": "caller-bound",
        "engine": SGLANG_ENGINE_ID,
        "model": SGLANG_ENGINE_ID,
        "exact_revision": value,
    })
    return identity.exact_revision


def _fixed_model_identity() -> MusicModelIdentity:
    return parse_music_model_identity({
        "purpose": "music.generate",
        "provider": "local",
        "engine": SGLANG_ENGINE_ID,
        "model": MUSIC3_MODEL_ID,
        "exact_revision": MUSIC3_HF_EXACT_REVISION,
    })


@dataclass(frozen=True, slots=True)
class Music3SglangSourceBinding:
    """Exact source identities without an execution-authority signal."""

    model_identity: MusicModelIdentity
    runtime_source_revision: str
    required_local_experiment_gates: tuple[str, ...] = LOCAL_EXPERIMENT_REQUIRED_GATES

    def __post_init__(self) -> None:
        if type(self.model_identity) is not MusicModelIdentity:
            raise MusicModelContractError(
                "model_identity must be an exact MusicModelIdentity"
            )
        if self.model_identity != _fixed_model_identity():
            raise MusicModelContractError("model_identity is not MiniMax Music 3")
        revision = _exact_content_address(self.runtime_source_revision)
        object.__setattr__(self, "runtime_source_revision", revision)
        if (
            type(self.required_local_experiment_gates) is not tuple
            or not all(
                type(gate) is str
                for gate in self.required_local_experiment_gates
            )
            or self.required_local_experiment_gates
            != LOCAL_EXPERIMENT_REQUIRED_GATES
        ):
            raise MusicModelContractError(
                "required local-experiment gates are immutable"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "model_identity": self.model_identity.to_mapping(),
            "runtime_source_revision": self.runtime_source_revision,
            "authorization_scope": LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
            "required_local_experiment_gates": list(
                self.required_local_experiment_gates
            ),
            "unapproved_authorization_scopes": list(
                UNAPPROVED_AUTHORIZATION_SCOPES
            ),
            "execution_authorized": False,
        }


def bind_music3_sglang_source(
    runtime_source_revision: object,
) -> Music3SglangSourceBinding:
    """Bind caller-supplied runtime source without granting execution rights."""

    return Music3SglangSourceBinding(
        model_identity=_fixed_model_identity(),
        runtime_source_revision=_exact_content_address(runtime_source_revision),
    )


@dataclass(frozen=True, slots=True)
class ValidatedMusic3SglangRequest:
    """Closed SGLang-Omni wire request with canonical scalar types."""

    lyrics_input: str
    caption_instructions: str
    seed: int
    max_new_tokens: int
    speed: float | None = None

    def __post_init__(self) -> None:
        _bounded_text(
            self.lyrics_input,
            label="input",
            maximum_bytes=MAX_LYRICS_BYTES,
        )
        _bounded_text(
            self.caption_instructions,
            label="instructions",
            maximum_bytes=MAX_INSTRUCTIONS_BYTES,
        )
        if type(self.seed) is not int or not MIN_SEED <= self.seed <= MAX_SEED:
            raise MusicModelContractError("seed must be a nonnegative uint64")
        if (
            type(self.max_new_tokens) is not int
            or not MIN_NEW_TOKENS <= self.max_new_tokens <= MAX_NEW_TOKENS
        ):
            raise MusicModelContractError("max_new_tokens is outside supported bounds")
        if self.speed is not None:
            if type(self.speed) not in (int, float) or not math.isfinite(self.speed):
                raise MusicModelContractError("speed must be a finite number")
            if float(self.speed) != 1.0:
                raise UnsupportedMusicRequest("SGLang-Omni supports only speed 1")
            object.__setattr__(self, "speed", 1.0)

    def to_mapping(self) -> dict[str, object]:
        request: dict[str, object] = {
            "model": MUSIC3_MODEL_ID,
            "input": self.lyrics_input,
            "instructions": self.caption_instructions,
            "response_format": SGLANG_RESPONSE_FORMAT,
            "seed": self.seed,
            "max_new_tokens": self.max_new_tokens,
            "stream": False,
        }
        if self.speed is not None:
            request["speed"] = self.speed
        return request


def validate_music3_sglang_request(
    value: object,
) -> ValidatedMusic3SglangRequest:
    """Validate the exact supported request surface before any runtime work."""

    if type(value) is not dict:
        raise MusicModelContractError("Music 3 request must be a plain mapping")
    keys = set(value)
    if not all(type(key) is str for key in keys):
        raise MusicModelContractError("Music 3 request keys must be text")
    unsupported = sorted(keys & UNSUPPORTED_REQUEST_FIELDS)
    if unsupported:
        raise UnsupportedMusicRequest(
            f"Music 3 request contains unsupported fields: {unsupported}"
        )
    allowed = REQUIRED_REQUEST_KEYS | OPTIONAL_REQUEST_KEYS
    unknown = sorted(keys - allowed)
    missing = sorted(REQUIRED_REQUEST_KEYS - keys)
    if unknown or missing:
        raise MusicModelContractError(
            f"Music 3 request keys are invalid (missing={missing}, unknown={unknown})"
        )
    if type(value["model"]) is not str or value["model"] != MUSIC3_MODEL_ID:
        raise MusicModelContractError("request model identity does not match Music 3")
    if (
        type(value["response_format"]) is not str
        or value["response_format"] != SGLANG_RESPONSE_FORMAT
    ):
        raise UnsupportedMusicRequest("response_format must be wav")
    if type(value["stream"]) is not bool:
        raise MusicModelContractError("stream must be boolean")
    if value["stream"] is not False:
        raise UnsupportedMusicRequest("streaming is not supported")

    speed = value.get("speed")
    if "speed" in value and speed is None:
        raise MusicModelContractError("speed cannot be null")
    return ValidatedMusic3SglangRequest(
        lyrics_input=_bounded_text(
            value["input"],
            label="input",
            maximum_bytes=MAX_LYRICS_BYTES,
        ),
        caption_instructions=_bounded_text(
            value["instructions"],
            label="instructions",
            maximum_bytes=MAX_INSTRUCTIONS_BYTES,
        ),
        seed=value["seed"],
        max_new_tokens=value["max_new_tokens"],
        speed=speed,
    )


@dataclass(frozen=True, slots=True)
class Music3HealthEvidence:
    stages: tuple[str, ...]
    entry_stage: str
    total_requests: int
    pending_completions: int
    _validation_token: InitVar[object] = None

    def __post_init__(self, _validation_token: object) -> None:
        if _validation_token is not _VALIDATED_RESPONSE_TOKEN:
            raise MusicModelContractError(
                "health evidence must come from response validation"
            )
        if (
            type(self.stages) is not tuple
            or not self.stages
            or len(self.stages) > MAX_HEALTH_STAGES
        ):
            raise MusicModelContractError("health evidence stages are invalid")
        stages = tuple(
            _bounded_text(stage, label="health stage", maximum_bytes=512)
            for stage in self.stages
        )
        if len(set(stages)) != len(stages):
            raise MusicModelContractError("health evidence stages are invalid")
        entry_stage = _bounded_text(
            self.entry_stage,
            label="health entry_stage",
            maximum_bytes=512,
        )
        if entry_stage not in stages:
            raise MusicModelContractError("health evidence entry_stage is invalid")
        for field, count in (
            ("total_requests", self.total_requests),
            ("pending_completions", self.pending_completions),
        ):
            if type(count) is not int or not 0 <= count <= MAX_COUNTER:
                raise MusicModelContractError(f"health evidence {field} is invalid")


def validate_sglang_health_response(
    status_code: object,
    value: object,
) -> Music3HealthEvidence:
    """Validate the exact healthy SGLang-Omni coordinator response."""

    if type(status_code) is not int or status_code != 200:
        raise MusicModelContractError("SGLang health status must be exactly 200")
    expected_keys = {
        "status",
        "running",
        "stages",
        "entry_stage",
        "total_requests",
        "pending_completions",
        "request_states",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise MusicModelContractError("health response uses an invalid key set")
    if type(value["status"]) is not str or value["status"] != "healthy":
        raise MusicModelContractError("SGLang health status is not healthy")
    if type(value["running"]) is not bool or value["running"] is not True:
        raise MusicModelContractError("SGLang coordinator is not running")
    raw_stages = value["stages"]
    if (
        type(raw_stages) is not list
        or not raw_stages
        or len(raw_stages) > MAX_HEALTH_STAGES
    ):
        raise MusicModelContractError("SGLang health stages are invalid")
    stages = tuple(
        _bounded_text(stage, label="health stage", maximum_bytes=512)
        for stage in raw_stages
    )
    if len(set(stages)) != len(stages):
        raise MusicModelContractError("SGLang health stages are invalid")
    entry_stage = _bounded_text(
        value["entry_stage"],
        label="health entry_stage",
        maximum_bytes=512,
    )
    if entry_stage not in stages:
        raise MusicModelContractError("SGLang health entry_stage is invalid")
    for field in ("total_requests", "pending_completions"):
        if (
            type(value[field]) is not int
            or not 0 <= value[field] <= MAX_COUNTER
        ):
            raise MusicModelContractError(f"SGLang health {field} is invalid")
    request_states = value["request_states"]
    if (
        type(request_states) is not dict
        or len(request_states) > MAX_HEALTH_STATES
        or not all(
            type(state) is str
            and state
            and type(count) is int
            and 0 <= count <= MAX_COUNTER
            for state, count in request_states.items()
        )
    ):
        raise MusicModelContractError("SGLang health request_states are invalid")
    for state in request_states:
        _bounded_text(state, label="health request state", maximum_bytes=512)
    if sum(request_states.values()) != value["total_requests"]:
        raise MusicModelContractError("SGLang health request counts are inconsistent")
    return Music3HealthEvidence(
        stages=stages,
        entry_stage=entry_stage,
        total_requests=value["total_requests"],
        pending_completions=value["pending_completions"],
        _validation_token=_VALIDATED_RESPONSE_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class Music3ModelEvidence:
    model_id: str
    created: int
    owned_by: str
    root: str
    _validation_token: InitVar[object] = None

    def __post_init__(self, _validation_token: object) -> None:
        if _validation_token is not _VALIDATED_RESPONSE_TOKEN:
            raise MusicModelContractError(
                "model evidence must come from response validation"
            )
        if type(self.model_id) is not str or self.model_id != MUSIC3_MODEL_ID:
            raise MusicModelContractError("model evidence identity is invalid")
        if type(self.created) is not int or not 0 <= self.created <= MAX_COUNTER:
            raise MusicModelContractError("model evidence created is invalid")
        if type(self.owned_by) is not str or self.owned_by != "sglang-omni":
            raise MusicModelContractError("model evidence owner is invalid")
        if type(self.root) is not str or self.root != MUSIC3_MODEL_ID:
            raise MusicModelContractError("model evidence root is invalid")


def validate_sglang_models_response(value: object) -> Music3ModelEvidence:
    """Require one exact OpenAI-compatible model identity response."""

    if type(value) is not dict or set(value) != {"object", "data"}:
        raise MusicModelContractError("models response must use the exact list envelope")
    if type(value["object"]) is not str or value["object"] != "list":
        raise MusicModelContractError("models response object must be list")
    data = value["data"]
    if type(data) is not list or len(data) != 1 or type(data[0]) is not dict:
        raise MusicModelContractError("models response must contain one plain model entry")
    entry = data[0]
    if set(entry) != {
        "id",
        "object",
        "created",
        "owned_by",
        "permission",
        "root",
    }:
        raise MusicModelContractError("model entry uses an invalid key set")
    if type(entry["id"]) is not str or entry["id"] != MUSIC3_MODEL_ID:
        raise MusicModelContractError("model response identity does not match Music 3")
    if type(entry["object"]) is not str or entry["object"] != "model":
        raise MusicModelContractError("model entry object must be model")
    if (
        type(entry["created"]) is not int
        or not 0 <= entry["created"] <= MAX_COUNTER
    ):
        raise MusicModelContractError("model entry created must be nonnegative integer")
    if type(entry["owned_by"]) is not str or entry["owned_by"] != "sglang-omni":
        raise MusicModelContractError("model entry owned_by must be sglang-omni")
    if type(entry["root"]) is not str or entry["root"] != MUSIC3_MODEL_ID:
        raise MusicModelContractError("model response root does not match Music 3")
    permissions = entry["permission"]
    if type(permissions) is not list or len(permissions) != 1:
        raise MusicModelContractError("model entry permission list is invalid")
    permission = permissions[0]
    if type(permission) is not dict or set(permission) != {
        "id",
        "object",
        "allow_create_engine",
        "allow_sampling",
        "allow_logprobs",
    }:
        raise MusicModelContractError("model permission uses an invalid key set")
    expected_permission = {
        "id": "modelperm-default",
        "object": "model_permission",
        "allow_create_engine": False,
        "allow_sampling": True,
        "allow_logprobs": True,
    }
    if any(
        type(permission[key]) is not type(expected)
        or permission[key] != expected
        for key, expected in expected_permission.items()
    ):
        raise MusicModelContractError("model permission values are invalid")
    return Music3ModelEvidence(
        model_id=entry["id"],
        created=entry["created"],
        owned_by=entry["owned_by"],
        root=entry["root"],
        _validation_token=_VALIDATED_RESPONSE_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class Music3WavEvidence:
    byte_count: int
    frame_count: int
    duration_seconds: float
    sha256: str
    _validation_token: InitVar[object] = None

    def __post_init__(self, _validation_token: object) -> None:
        if _validation_token is not _VALIDATED_RESPONSE_TOKEN:
            raise MusicModelContractError(
                "WAV evidence must come from response validation"
            )
        if type(self.byte_count) is not int or not 0 < self.byte_count <= MAX_WAV_BYTES:
            raise MusicModelContractError("WAV evidence byte_count is invalid")
        if type(self.frame_count) is not int or self.frame_count <= 0:
            raise MusicModelContractError("WAV evidence frame_count is invalid")
        expected_duration = self.frame_count / WAV_SAMPLE_RATE
        if (
            type(self.duration_seconds) is not float
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds != expected_duration
        ):
            raise MusicModelContractError("WAV evidence duration is invalid")
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or self.sha256 != self.sha256.casefold()
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise MusicModelContractError("WAV evidence digest is invalid")


def _pcm_data_chunk_size(value: bytes) -> int:
    if len(value) < 12 or value[:4] != b"RIFF" or value[8:12] != b"WAVE":
        raise MusicModelContractError("Music 3 WAV RIFF header is invalid")
    if int.from_bytes(value[4:8], "little") != len(value) - 8:
        raise MusicModelContractError("Music 3 WAV RIFF size is inconsistent")
    position = 12
    data_sizes: list[int] = []
    while position < len(value):
        if position + 8 > len(value):
            raise MusicModelContractError("Music 3 WAV chunk header is truncated")
        chunk_id = value[position : position + 4]
        chunk_size = int.from_bytes(value[position + 4 : position + 8], "little")
        payload_end = position + 8 + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if payload_end > len(value) or padded_end > len(value):
            raise MusicModelContractError("Music 3 WAV chunk is truncated")
        if chunk_id == b"data":
            data_sizes.append(chunk_size)
        position = padded_end
    if len(data_sizes) != 1 or data_sizes[0] <= 0:
        raise MusicModelContractError("Music 3 WAV must contain one nonempty data chunk")
    block_size = WAV_CHANNELS * WAV_SAMPLE_WIDTH_BYTES
    if data_sizes[0] % block_size:
        raise MusicModelContractError("Music 3 WAV data chunk is not frame-aligned")
    return data_sizes[0]


def parse_music3_wav_bytes(value: object) -> Music3WavEvidence:
    """Prove a bounded in-memory response is nonempty 32 kHz stereo PCM16 WAV."""

    if type(value) is not bytes:
        raise MusicModelContractError("Music 3 WAV response must be exact bytes")
    if not value or len(value) > MAX_WAV_BYTES:
        raise MusicModelContractError("Music 3 WAV response size is invalid")
    data_chunk_size = _pcm_data_chunk_size(value)
    try:
        with wave.open(io.BytesIO(value), "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise MusicModelContractError("Music 3 WAV must be uncompressed PCM")
            if reader.getframerate() != WAV_SAMPLE_RATE:
                raise MusicModelContractError("Music 3 WAV sample rate must be 32000 Hz")
            if reader.getsampwidth() != WAV_SAMPLE_WIDTH_BYTES:
                raise MusicModelContractError("Music 3 WAV sample width must be 16-bit")
            if reader.getnchannels() != WAV_CHANNELS:
                raise MusicModelContractError("Music 3 WAV must be stereo")
            frame_count = reader.getnframes()
            if frame_count <= 0:
                raise MusicModelContractError("Music 3 WAV must contain PCM frames")
            pcm = reader.readframes(frame_count)
            expected_bytes = frame_count * WAV_CHANNELS * WAV_SAMPLE_WIDTH_BYTES
            if (
                expected_bytes != data_chunk_size
                or len(pcm) != expected_bytes
                or reader.readframes(1)
            ):
                raise MusicModelContractError("Music 3 WAV PCM payload is truncated")
    except MusicModelContractError:
        raise
    except (EOFError, wave.Error) as error:
        raise MusicModelContractError("Music 3 WAV response is malformed") from error

    return Music3WavEvidence(
        byte_count=len(value),
        frame_count=frame_count,
        duration_seconds=frame_count / WAV_SAMPLE_RATE,
        sha256=hashlib.sha256(value).hexdigest(),
        _validation_token=_VALIDATED_RESPONSE_TOKEN,
    )
