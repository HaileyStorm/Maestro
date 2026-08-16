"""Fail-closed HTTP boundary for an already-running Music 3 SGLang server.

This module never discovers, downloads, launches, registers, or unloads a
runtime.  Callers provide both an asynchronous transport and short-lived,
server-authored authorization evidence for the owner's local experiment only.
Hosted, LAN, and Cloudflare exposure remain outside this boundary.  A
request-disconnect event or task cancellation cancels only the exact in-flight
transport task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from services.minimax_music3_sglang_contract import (
    MAX_WAV_BYTES,
    MAX_NEW_TOKENS,
    MAX_SEED,
    MIN_NEW_TOKENS,
    MUSIC3_HF_EXACT_REVISION,
    MUSIC3_MODEL_ID,
    LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
    LOCAL_EXPERIMENT_REQUIRED_GATES,
    Music3HealthEvidence,
    Music3ModelEvidence,
    Music3SglangSourceBinding,
    Music3WavEvidence,
    MusicModelContractError,
    ValidatedMusic3SglangRequest,
    bind_music3_sglang_source,
    parse_music3_wav_bytes,
    validate_music3_sglang_request,
    validate_sglang_health_response,
    validate_sglang_models_response,
)


HEALTH_PATH = "/health_generate"
MODELS_PATH = "/v1/models"
GENERATE_PATH = "/v1/audio/speech"
AUTHORIZATION_SCHEMA = "maestro.music3.external-authorization.v2"
AUTHORIZATION_AUDIENCE = "maestro.music3.sglang-omni"
LOCAL_EXPERIMENT_EXECUTION_LOCALITY = "local_loopback_only"

MAX_JSON_RESPONSE_BYTES = 256 * 1024
MAX_AUTHORIZATION_BYTES = 16 * 1024
MAX_TRANSPORT_RESPONSE_BYTES = max(MAX_JSON_RESPONSE_BYTES, MAX_WAV_BYTES)
MAX_AUTHORIZATION_LIFETIME_SECONDS = 60 * 60
MAX_HEADERS = 64
MAX_HEADER_VALUE_BYTES = 8 * 1024
MIN_TIMEOUT_SECONDS = 0.05
MAX_TIMEOUT_SECONDS = 60 * 60
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
DEFAULT_GENERATION_TIMEOUT_SECONDS = 20 * 60.0
DEFAULT_CANCELLATION_GRACE_SECONDS = 0.25
MIN_CANCELLATION_GRACE_SECONDS = 0.001
MAX_CANCELLATION_GRACE_SECONDS = 1.0

_HEADER_NAME = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]{1,128}")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_ED25519_PUBLIC_KEY = re.compile(r"ed25519:[0-9a-f]{64}")
_ED25519_SIGNATURE = re.compile(r"ed25519:[0-9a-f]{128}")
AUTHORIZATION_SIGNATURE_HEADER = "x-maestro-authorization-ed25519"


class Music3SglangClientError(RuntimeError):
    """Content-free client failure suitable for an API error boundary."""

    def __init__(self, code: str, stage: str) -> None:
        if not _OPAQUE_ID.fullmatch(code) or not _OPAQUE_ID.fullmatch(stage):
            raise ValueError("client error code and stage must be bounded identifiers")
        self.code = code
        self.stage = stage
        super().__init__(f"Music 3 {stage} failed ({code})")


class Music3RequestDisconnected(Music3SglangClientError):
    """The caller disconnected while the exact request was in flight."""


def _bounded_timeout(value: object, *, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise MusicModelContractError(f"{label} must be a finite number")
    result = float(value)
    if not MIN_TIMEOUT_SECONDS <= result <= MAX_TIMEOUT_SECONDS:
        raise MusicModelContractError(f"{label} is outside supported bounds")
    return result


def _canonical_loopback_base_url(value: object) -> str:
    if type(value) is not str or not value:
        raise MusicModelContractError("SGLang base URL must be text")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise MusicModelContractError("SGLang base URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise MusicModelContractError(
            "SGLang base URL must be exact IPv4 loopback with an explicit port"
        )
    canonical = f"http://127.0.0.1:{port}"
    if value != canonical or parsed.netloc != f"127.0.0.1:{port}":
        raise MusicModelContractError("SGLang base URL is not canonical")
    return canonical


def _canonical_loopback_request_url(value: object) -> str:
    if type(value) is not str:
        raise MusicModelContractError("HTTP request URL must be text")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise MusicModelContractError("HTTP request URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in {HEALTH_PATH, MODELS_PATH, GENERATE_PATH}
        or parsed.query
        or parsed.fragment
    ):
        raise MusicModelContractError("HTTP request URL is outside the closed loopback API")
    canonical = f"http://127.0.0.1:{port}{parsed.path}"
    if value != canonical or parsed.netloc != f"127.0.0.1:{port}":
        raise MusicModelContractError("HTTP request URL is not canonical")
    return canonical


def _https_origin(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise MusicModelContractError(f"{label} must be text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise MusicModelContractError(f"{label} is invalid Unicode") from error
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise MusicModelContractError(f"{label} is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or len(encoded) > 2048
    ):
        raise MusicModelContractError(f"{label} must be an HTTPS origin")
    hostname = parsed.hostname.casefold()
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    canonical = f"https://{netloc}"
    if value != canonical:
        raise MusicModelContractError(f"{label} is not canonical")
    return canonical


def _https_resource_url(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise MusicModelContractError(f"{label} must be text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise MusicModelContractError(f"{label} is invalid Unicode") from error
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise MusicModelContractError(f"{label} is invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or parsed.query
        or parsed.fragment
        or len(encoded) > 2048
    ):
        raise MusicModelContractError(f"{label} must be a canonical HTTPS URL")
    hostname = parsed.hostname.casefold()
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    canonical = f"https://{netloc}{parsed.path}"
    if value != canonical:
        raise MusicModelContractError(f"{label} is not canonical")
    return canonical


def _headers(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple or len(value) > MAX_HEADERS:
        raise MusicModelContractError("HTTP headers must be a bounded exact tuple")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise MusicModelContractError("HTTP header entries must be exact pairs")
        name, header_value = item
        if (
            type(name) is not str
            or _HEADER_NAME.fullmatch(name) is None
            or name != name.casefold()
            or name in seen
        ):
            raise MusicModelContractError("HTTP header name is invalid or duplicated")
        if type(header_value) is not str or "\r" in header_value or "\n" in header_value:
            raise MusicModelContractError("HTTP header value is invalid")
        try:
            encoded = header_value.encode("latin-1", errors="strict")
        except UnicodeError as error:
            raise MusicModelContractError("HTTP header value is invalid") from error
        if len(encoded) > MAX_HEADER_VALUE_BYTES:
            raise MusicModelContractError("HTTP header value is too large")
        seen.add(name)
        normalized.append((name, header_value))
    return tuple(normalized)


def _header_map(value: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(_headers(value))


def _strict_json_object(body: object, *, maximum_bytes: int) -> dict[str, object]:
    if type(body) is not bytes or not body or len(body) > maximum_bytes:
        raise MusicModelContractError("JSON response body size is invalid")
    if body.startswith(b"\xef\xbb\xbf"):
        raise MusicModelContractError("JSON response must not use a BOM")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise MusicModelContractError("JSON response is not valid UTF-8") from error

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MusicModelContractError("JSON response contains duplicate keys")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> object:
        raise MusicModelContractError("JSON response contains a non-finite number")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
        )
    except MusicModelContractError:
        raise
    except (ValueError, RecursionError) as error:
        raise MusicModelContractError("JSON response is malformed") from error
    if type(parsed) is not dict:
        raise MusicModelContractError("JSON response must be an exact object")
    return parsed


@dataclass(frozen=True, slots=True)
class Music3TransportRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes | None
    timeout_seconds: float
    max_response_bytes: int
    redirect_policy: str = "error"

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST"}:
            raise MusicModelContractError("HTTP method is invalid")
        _canonical_loopback_request_url(self.url)
        _headers(self.headers)
        if self.body is not None and type(self.body) is not bytes:
            raise MusicModelContractError("HTTP request body must be exact bytes")
        object.__setattr__(
            self,
            "timeout_seconds",
            _bounded_timeout(self.timeout_seconds, label="request timeout"),
        )
        if (
            type(self.max_response_bytes) is not int
            or type(self.max_response_bytes) is bool
            or not 1 <= self.max_response_bytes <= MAX_TRANSPORT_RESPONSE_BYTES
        ):
            raise MusicModelContractError("HTTP response size bound is invalid")
        if type(self.redirect_policy) is not str or self.redirect_policy != "error":
            raise MusicModelContractError("HTTP redirects must fail before following")


@dataclass(frozen=True, slots=True)
class Music3TransportResponse:
    status_code: int
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    redirect_count: int = 0

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise MusicModelContractError("HTTP response status is invalid")
        if type(self.url) is not str:
            raise MusicModelContractError("HTTP response URL is invalid")
        _headers(self.headers)
        if type(self.body) is not bytes:
            raise MusicModelContractError("HTTP response body must be exact bytes")
        if type(self.redirect_count) is not int or self.redirect_count < 0:
            raise MusicModelContractError("HTTP redirect count is invalid")


class Music3AsyncTransport(Protocol):
    def __call__(
        self, request: Music3TransportRequest
    ) -> Awaitable[Music3TransportResponse]: ...


@dataclass(frozen=True, slots=True)
class Music3AuthorizationTrustBinding:
    """Server-owned authorization endpoint, owner, and TLS trust anchor."""

    authorization_url: str
    owner_subject_id: str
    peer_certificate_sha256: str
    authorization_public_key_ed25519: str
    binding_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        authorization_url = _https_resource_url(
            self.authorization_url,
            label="authorization trust URL",
        )
        if (
            type(self.owner_subject_id) is not str
            or _OPAQUE_ID.fullmatch(self.owner_subject_id) is None
        ):
            raise MusicModelContractError(
                "authorization trust owner subject is invalid"
            )
        if (
            type(self.peer_certificate_sha256) is not str
            or _HEX_SHA256.fullmatch(self.peer_certificate_sha256) is None
        ):
            raise MusicModelContractError(
                "authorization trust certificate digest is invalid"
            )
        if (
            type(self.authorization_public_key_ed25519) is not str
            or _ED25519_PUBLIC_KEY.fullmatch(
                self.authorization_public_key_ed25519
            ) is None
        ):
            raise MusicModelContractError(
                "authorization trust public key is invalid"
            )
        material = json.dumps(
            {
                "authorization_url": authorization_url,
                "owner_subject_id": self.owner_subject_id,
                "peer_certificate_sha256": self.peer_certificate_sha256,
                "authorization_public_key_ed25519": (
                    self.authorization_public_key_ed25519
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        object.__setattr__(
            self,
            "binding_sha256",
            "sha256:" + hashlib.sha256(material).hexdigest(),
        )


def bind_music3_authorization_trust(
    *,
    authorization_url: object,
    owner_subject_id: object,
    peer_certificate_sha256: object,
    authorization_public_key_ed25519: object,
) -> Music3AuthorizationTrustBinding:
    """Bind server configuration once; generation callers cannot replace it."""

    return Music3AuthorizationTrustBinding(
        authorization_url=authorization_url,
        owner_subject_id=owner_subject_id,
        peer_certificate_sha256=peer_certificate_sha256,
        authorization_public_key_ed25519=authorization_public_key_ed25519,
    )


def _verify_authorization_signature(
    *,
    document_bytes: object,
    signature_ed25519: object,
    public_key_ed25519: object,
) -> None:
    if (
        type(document_bytes) is not bytes
        or not document_bytes
        or len(document_bytes) > MAX_AUTHORIZATION_BYTES
    ):
        raise MusicModelContractError("authorization signed document is invalid")
    if (
        type(signature_ed25519) is not str
        or _ED25519_SIGNATURE.fullmatch(signature_ed25519) is None
        or type(public_key_ed25519) is not str
        or _ED25519_PUBLIC_KEY.fullmatch(public_key_ed25519) is None
    ):
        raise MusicModelContractError("authorization signature is invalid")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(public_key_ed25519.removeprefix("ed25519:"))
        )
        public_key.verify(
            bytes.fromhex(signature_ed25519.removeprefix("ed25519:")),
            document_bytes,
        )
    except (InvalidSignature, ValueError):
        raise MusicModelContractError("authorization signature is invalid") from None


@dataclass(frozen=True, slots=True)
class Music3ExternalAuthorizationEvidence:
    """Validated, transport-authenticated local-experiment evidence."""

    issuer: str
    evidence_id: str
    subject_id: str
    scope: str
    approved_gates: tuple[str, ...]
    model_id: str
    model_revision: str
    base_url: str
    authorization_url: str
    runtime_source_revision: str
    authorization_trust_binding_sha256: str
    authorization_public_key_ed25519: str
    issued_at_unix: int
    expires_at_unix: int
    document_sha256: str
    peer_certificate_sha256: str
    signed_document_bytes: bytes = field(repr=False)
    signature_ed25519: str = field(repr=False)

    def __post_init__(self) -> None:
        _verify_authorization_signature(
            document_bytes=self.signed_document_bytes,
            signature_ed25519=self.signature_ed25519,
            public_key_ed25519=self.authorization_public_key_ed25519,
        )
        if (
            type(self.document_sha256) is not str
            or _HEX_SHA256.fullmatch(self.document_sha256) is None
            or self.document_sha256
            != "sha256:" + hashlib.sha256(self.signed_document_bytes).hexdigest()
        ):
            raise MusicModelContractError(
                "authorization signed document digest is invalid"
            )
        signed_document = _strict_json_object(
            self.signed_document_bytes,
            maximum_bytes=MAX_AUTHORIZATION_BYTES,
        )
        expected_document = {
            "schema": AUTHORIZATION_SCHEMA,
            "issuer": self.issuer,
            "evidence_id": self.evidence_id,
            "subject_id": self.subject_id,
            "audience": AUTHORIZATION_AUDIENCE,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "runtime_source_revision": self.runtime_source_revision,
            "base_url": self.base_url,
            "authorization_url": self.authorization_url,
            "authorization_trust_binding_sha256": (
                self.authorization_trust_binding_sha256
            ),
            "peer_certificate_sha256": self.peer_certificate_sha256,
            "scope": self.scope,
            "approved_gates": list(self.approved_gates),
            "issued_at_unix": self.issued_at_unix,
            "expires_at_unix": self.expires_at_unix,
        }
        if (
            type(signed_document.get("issued_at_unix")) is not int
            or type(signed_document.get("expires_at_unix")) is not int
            or signed_document != expected_document
        ):
            raise MusicModelContractError(
                "authorization evidence does not match its signed document"
            )
        _https_origin(self.issuer, label="authorization issuer")
        if _OPAQUE_ID.fullmatch(self.evidence_id) is None:
            raise MusicModelContractError("authorization evidence_id is invalid")
        if _OPAQUE_ID.fullmatch(self.subject_id) is None:
            raise MusicModelContractError("authorization subject_id is invalid")
        if (
            type(self.scope) is not str
            or self.scope != LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE
        ):
            raise MusicModelContractError(
                "authorization scope is not the local experiment"
            )
        if (
            type(self.approved_gates) is not tuple
            or self.approved_gates != LOCAL_EXPERIMENT_REQUIRED_GATES
            or not all(type(gate) is str for gate in self.approved_gates)
        ):
            raise MusicModelContractError(
                "authorization local-experiment gates are incomplete"
            )
        if (
            self.model_id != MUSIC3_MODEL_ID
            or self.model_revision != MUSIC3_HF_EXACT_REVISION
        ):
            raise MusicModelContractError("authorization model binding is invalid")
        _canonical_loopback_base_url(self.base_url)
        authorization_url = _https_resource_url(
            self.authorization_url,
            label="authorization evidence URL",
        )
        authorization_parts = urlsplit(authorization_url)
        authorization_origin = _https_origin(
            f"https://{authorization_parts.hostname}"
            + (
                f":{authorization_parts.port}"
                if authorization_parts.port not in (None, 443)
                else ""
            ),
            label="authorization evidence origin",
        )
        if self.issuer != authorization_origin:
            raise MusicModelContractError(
                "authorization evidence issuer does not match its URL"
            )
        if (
            type(self.runtime_source_revision) is not str
            or not self.runtime_source_revision
        ):
            raise MusicModelContractError("authorization runtime revision is invalid")
        if (
            type(self.authorization_trust_binding_sha256) is not str
            or _HEX_SHA256.fullmatch(
                self.authorization_trust_binding_sha256
            ) is None
        ):
            raise MusicModelContractError(
                "authorization trust binding digest is invalid"
            )
        if (
            type(self.authorization_public_key_ed25519) is not str
            or _ED25519_PUBLIC_KEY.fullmatch(
                self.authorization_public_key_ed25519
            ) is None
        ):
            raise MusicModelContractError(
                "authorization public key binding is invalid"
            )
        if (
            type(self.issued_at_unix) is not int
            or type(self.expires_at_unix) is not int
            or self.issued_at_unix < 0
            or self.expires_at_unix <= self.issued_at_unix
            or self.expires_at_unix - self.issued_at_unix
            > MAX_AUTHORIZATION_LIFETIME_SECONDS
        ):
            raise MusicModelContractError("authorization validity window is invalid")
        for digest in (self.document_sha256, self.peer_certificate_sha256):
            if type(digest) is not str or _HEX_SHA256.fullmatch(digest) is None:
                raise MusicModelContractError("authorization digest is invalid")


@dataclass(frozen=True, slots=True)
class Music3GenerationProvenance:
    model_id: str
    model_revision: str
    runtime_source_revision: str
    seed: int
    max_new_tokens: int
    speed: float | None
    authorization_scope: str
    execution_locality: str
    hosted_service_authorized: bool
    authorization_document_sha256: str
    response: Music3WavEvidence

    def __post_init__(self) -> None:
        if self.model_id != MUSIC3_MODEL_ID or self.model_revision != MUSIC3_HF_EXACT_REVISION:
            raise MusicModelContractError("generation provenance model is invalid")
        if (
            bind_music3_sglang_source(self.runtime_source_revision).runtime_source_revision
            != self.runtime_source_revision
        ):
            raise MusicModelContractError("generation provenance runtime revision is invalid")
        if type(self.seed) is not int or not 0 <= self.seed <= MAX_SEED:
            raise MusicModelContractError("generation provenance seed is invalid")
        if (
            type(self.max_new_tokens) is not int
            or not MIN_NEW_TOKENS <= self.max_new_tokens <= MAX_NEW_TOKENS
        ):
            raise MusicModelContractError("generation provenance token count is invalid")
        if self.speed is not None and (
            type(self.speed) is not float or self.speed != 1.0
        ):
            raise MusicModelContractError("generation provenance speed is invalid")
        if (
            type(self.authorization_scope) is not str
            or self.authorization_scope != LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE
        ):
            raise MusicModelContractError(
                "generation provenance authorization scope is invalid"
            )
        if (
            type(self.execution_locality) is not str
            or self.execution_locality != LOCAL_EXPERIMENT_EXECUTION_LOCALITY
        ):
            raise MusicModelContractError(
                "generation provenance execution locality is invalid"
            )
        if (
            type(self.hosted_service_authorized) is not bool
            or self.hosted_service_authorized is not False
        ):
            raise MusicModelContractError(
                "generation provenance cannot authorize hosted service"
            )
        if (
            type(self.authorization_document_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.authorization_document_sha256) is None
        ):
            raise MusicModelContractError("generation provenance digest is invalid")
        if type(self.response) is not Music3WavEvidence:
            raise MusicModelContractError("generation provenance response is invalid")

    def to_mapping(self) -> dict[str, object]:
        request: dict[str, object] = {
            "lyrics_supplied": True,
            "instructions_supplied": True,
            "response_format": "wav",
            "seed": self.seed,
            "max_new_tokens": self.max_new_tokens,
            "stream": False,
        }
        if self.speed is not None:
            request["speed"] = self.speed
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "runtime_source_revision": self.runtime_source_revision,
            "authorization_scope": self.authorization_scope,
            "execution_locality": self.execution_locality,
            "hosted_service_authorized": self.hosted_service_authorized,
            "request": request,
            "authorization_document_sha256": self.authorization_document_sha256,
            "response_sha256": self.response.sha256,
            "response_bytes": self.response.byte_count,
            "frame_count": self.response.frame_count,
            "duration_seconds": self.response.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class Music3GenerationResult:
    audio_bytes: bytes
    provenance: Music3GenerationProvenance

    def __post_init__(self) -> None:
        if type(self.audio_bytes) is not bytes:
            raise MusicModelContractError("generation audio must be exact bytes")
        if type(self.provenance) is not Music3GenerationProvenance:
            raise MusicModelContractError("generation provenance is invalid")
        if hashlib.sha256(self.audio_bytes).hexdigest() != self.provenance.response.sha256:
            raise MusicModelContractError("generation audio does not match provenance")


def validate_music3_external_authorization_response(
    *,
    request_url: object,
    final_url: object,
    status_code: object,
    headers: object,
    body: object,
    peer_certificate_sha256: object,
    authorization_trust: Music3AuthorizationTrustBinding,
    source_binding: Music3SglangSourceBinding,
    base_url: object,
    now_unix: object,
) -> Music3ExternalAuthorizationEvidence:
    """Validate one exact HTTPS response authored by the authorization server.

    ``peer_certificate_sha256`` is transport-authentication evidence supplied by
    the separate HTTPS connector.  It is an exact digest, never a client-side
    ``authorized=True`` assertion.  This module deliberately does not make that
    external request or choose the trust anchor.
    """

    if type(source_binding) is not Music3SglangSourceBinding:
        raise MusicModelContractError("authorization source binding is invalid")
    if type(authorization_trust) is not Music3AuthorizationTrustBinding:
        raise MusicModelContractError("authorization trust binding is invalid")
    canonical_base_url = _canonical_loopback_base_url(base_url)
    requested_url = _https_resource_url(
        request_url,
        label="authorization request URL",
    )
    returned_url = _https_resource_url(final_url, label="authorization final URL")
    if requested_url != authorization_trust.authorization_url:
        raise MusicModelContractError(
            "authorization request does not match server trust binding"
        )
    if returned_url != requested_url:
        raise MusicModelContractError("authorization response redirected")
    if type(status_code) is not int or status_code != 200:
        raise MusicModelContractError("authorization response status must be exactly 200")
    header_map = _header_map(headers)
    if header_map.get("content-type") != "application/json":
        raise MusicModelContractError("authorization content type must be application/json")
    signature_ed25519 = header_map.get(AUTHORIZATION_SIGNATURE_HEADER)
    if "location" in header_map:
        raise MusicModelContractError("authorization response must not contain a redirect")
    if (
        type(peer_certificate_sha256) is not str
        or _HEX_SHA256.fullmatch(peer_certificate_sha256) is None
        or peer_certificate_sha256
        != authorization_trust.peer_certificate_sha256
    ):
        raise MusicModelContractError("authorization peer certificate digest is invalid")
    if "content-length" in header_map:
        raw_length = header_map["content-length"]
        if (
            not raw_length
            or not raw_length.isascii()
            or not raw_length.isdecimal()
            or type(body) is not bytes
            or raw_length != str(len(body))
        ):
            raise MusicModelContractError("authorization content length is invalid")
    if type(now_unix) is not int or now_unix < 0:
        raise MusicModelContractError("authorization clock value is invalid")
    _verify_authorization_signature(
        document_bytes=body,
        signature_ed25519=signature_ed25519,
        public_key_ed25519=(
            authorization_trust.authorization_public_key_ed25519
        ),
    )

    document = _strict_json_object(body, maximum_bytes=MAX_AUTHORIZATION_BYTES)
    expected_keys = {
        "schema",
        "issuer",
        "evidence_id",
        "subject_id",
        "audience",
        "model_id",
        "model_revision",
        "runtime_source_revision",
        "base_url",
        "authorization_url",
        "authorization_trust_binding_sha256",
        "peer_certificate_sha256",
        "scope",
        "approved_gates",
        "issued_at_unix",
        "expires_at_unix",
    }
    if set(document) != expected_keys:
        raise MusicModelContractError("authorization document uses an invalid key set")
    issuer = _https_origin(document["issuer"], label="authorization issuer")
    request_parts = urlsplit(requested_url)
    request_origin = _https_origin(
        f"https://{request_parts.hostname}"
        + (
            f":{request_parts.port}"
            if request_parts.port not in (None, 443)
            else ""
        ),
        label="authorization request origin",
    )
    if issuer != request_origin:
        raise MusicModelContractError("authorization issuer does not match server origin")
    if document["subject_id"] != authorization_trust.owner_subject_id:
        raise MusicModelContractError(
            "authorization owner subject does not match server trust binding"
        )

    exact_values = {
        "schema": AUTHORIZATION_SCHEMA,
        "audience": AUTHORIZATION_AUDIENCE,
        "model_id": MUSIC3_MODEL_ID,
        "model_revision": MUSIC3_HF_EXACT_REVISION,
        "runtime_source_revision": source_binding.runtime_source_revision,
        "base_url": canonical_base_url,
        "authorization_url": authorization_trust.authorization_url,
        "authorization_trust_binding_sha256": authorization_trust.binding_sha256,
        "peer_certificate_sha256": authorization_trust.peer_certificate_sha256,
        "scope": LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
    }
    for field, expected in exact_values.items():
        if type(document[field]) is not str or document[field] != expected:
            raise MusicModelContractError(f"authorization {field} is invalid")
    if (
        type(document["approved_gates"]) is not list
        or document["approved_gates"] != list(LOCAL_EXPERIMENT_REQUIRED_GATES)
        or not all(type(gate) is str for gate in document["approved_gates"])
    ):
        raise MusicModelContractError(
            "authorization local-experiment gates are incomplete"
        )
    for field in ("evidence_id", "subject_id"):
        if type(document[field]) is not str or _OPAQUE_ID.fullmatch(document[field]) is None:
            raise MusicModelContractError(f"authorization {field} is invalid")
    issued = document["issued_at_unix"]
    expires = document["expires_at_unix"]
    if (
        type(issued) is not int
        or type(expires) is not int
        or issued < 0
        or expires <= issued
        or expires - issued > MAX_AUTHORIZATION_LIFETIME_SECONDS
        or not issued <= now_unix < expires
    ):
        raise MusicModelContractError("authorization is not currently valid")

    document_bytes = body
    assert type(document_bytes) is bytes
    evidence_fields = dict(
        issuer=issuer,
        evidence_id=document["evidence_id"],
        subject_id=document["subject_id"],
        scope=LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
        approved_gates=LOCAL_EXPERIMENT_REQUIRED_GATES,
        model_id=MUSIC3_MODEL_ID,
        model_revision=MUSIC3_HF_EXACT_REVISION,
        base_url=canonical_base_url,
        authorization_url=authorization_trust.authorization_url,
        runtime_source_revision=source_binding.runtime_source_revision,
        authorization_trust_binding_sha256=authorization_trust.binding_sha256,
        authorization_public_key_ed25519=(
            authorization_trust.authorization_public_key_ed25519
        ),
        issued_at_unix=issued,
        expires_at_unix=expires,
        document_sha256="sha256:" + hashlib.sha256(document_bytes).hexdigest(),
        peer_certificate_sha256=peer_certificate_sha256,
        signed_document_bytes=document_bytes,
        signature_ed25519=signature_ed25519,
    )
    return Music3ExternalAuthorizationEvidence(**evidence_fields)


class Music3SglangClient:
    """Three-stage, exact-response client for one authorized loopback runtime."""

    def __init__(
        self,
        *,
        base_url: object,
        source_binding: Music3SglangSourceBinding,
        authorization_trust: Music3AuthorizationTrustBinding,
        transport: Music3AsyncTransport,
        probe_timeout_seconds: object = DEFAULT_PROBE_TIMEOUT_SECONDS,
        generation_timeout_seconds: object = DEFAULT_GENERATION_TIMEOUT_SECONDS,
        cancellation_grace_seconds: object = DEFAULT_CANCELLATION_GRACE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._base_url = _canonical_loopback_base_url(base_url)
        if type(source_binding) is not Music3SglangSourceBinding:
            raise MusicModelContractError("SGLang source binding is invalid")
        if type(authorization_trust) is not Music3AuthorizationTrustBinding:
            raise MusicModelContractError(
                "SGLang authorization trust binding is invalid"
            )
        if not callable(transport):
            raise MusicModelContractError("SGLang transport must be callable")
        if not callable(clock):
            raise MusicModelContractError("SGLang clock must be callable")
        self._source_binding = source_binding
        self._authorization_trust = authorization_trust
        self._transport = transport
        self._probe_timeout = _bounded_timeout(
            probe_timeout_seconds,
            label="probe timeout",
        )
        self._generation_timeout = _bounded_timeout(
            generation_timeout_seconds,
            label="generation timeout",
        )
        if (
            type(cancellation_grace_seconds) not in (int, float)
            or not math.isfinite(cancellation_grace_seconds)
            or not MIN_CANCELLATION_GRACE_SECONDS
            <= float(cancellation_grace_seconds)
            <= MAX_CANCELLATION_GRACE_SECONDS
        ):
            raise MusicModelContractError("cancellation grace is outside supported bounds")
        self._cancellation_grace = float(cancellation_grace_seconds)
        self._clock = clock

    @property
    def base_url(self) -> str:
        return self._base_url

    @staticmethod
    def _request_body(validated: ValidatedMusic3SglangRequest) -> bytes:
        return json.dumps(
            validated.to_mapping(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _authorization_is_current(
        self,
        authorization: object,
    ) -> Music3ExternalAuthorizationEvidence:
        if type(authorization) is not Music3ExternalAuthorizationEvidence:
            raise MusicModelContractError(
                "validated external authorization evidence is required"
            )
        authorization.__post_init__()
        if (
            authorization.base_url != self._base_url
            or authorization.authorization_url
            != self._authorization_trust.authorization_url
            or authorization.runtime_source_revision
            != self._source_binding.runtime_source_revision
            or authorization.model_id != MUSIC3_MODEL_ID
            or authorization.model_revision != MUSIC3_HF_EXACT_REVISION
            or authorization.authorization_trust_binding_sha256
            != self._authorization_trust.binding_sha256
            or authorization.authorization_public_key_ed25519
            != self._authorization_trust.authorization_public_key_ed25519
            or authorization.subject_id
            != self._authorization_trust.owner_subject_id
            or authorization.peer_certificate_sha256
            != self._authorization_trust.peer_certificate_sha256
            or authorization.scope != LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE
            or authorization.approved_gates != LOCAL_EXPERIMENT_REQUIRED_GATES
        ):
            raise MusicModelContractError("external authorization binding does not match")
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise MusicModelContractError("authorization clock is invalid")
        if not authorization.issued_at_unix <= now < authorization.expires_at_unix:
            raise MusicModelContractError("external authorization is expired")
        return authorization

    @staticmethod
    def _consume_detached_task(task: asyncio.Future[object]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _cancel_transport_task(self, task: asyncio.Future[object]) -> bool:
        """Request abort and wait only for the configured bounded grace."""

        if task.done():
            self._consume_detached_task(task)
            return True
        task.cancel()
        done, _pending = await asyncio.wait(
            {task},
            timeout=self._cancellation_grace,
            return_when=asyncio.ALL_COMPLETED,
        )
        if task in done:
            self._consume_detached_task(task)
            return True
        task.add_done_callback(self._consume_detached_task)
        return False

    async def _send(
        self,
        request: Music3TransportRequest,
        *,
        stage: str,
        disconnect_event: asyncio.Event,
    ) -> Music3TransportResponse:
        if type(disconnect_event) is not asyncio.Event:
            raise MusicModelContractError("disconnect_event must be an asyncio.Event")
        if disconnect_event.is_set():
            raise Music3RequestDisconnected("disconnected", stage)

        try:
            operation = self._transport(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise Music3SglangClientError("transport_error", stage) from None
        if not isinstance(operation, Awaitable):
            raise Music3SglangClientError("transport_contract", stage)

        transport_task = asyncio.ensure_future(operation)
        disconnect_task = asyncio.create_task(disconnect_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {transport_task, disconnect_task},
                timeout=request.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done and disconnect_task.result():
                aborted = await self._cancel_transport_task(transport_task)
                if not aborted:
                    raise Music3SglangClientError("abort_unconfirmed", stage)
                raise Music3RequestDisconnected("disconnected", stage)
            if transport_task not in done:
                aborted = await self._cancel_transport_task(transport_task)
                if not aborted:
                    raise Music3SglangClientError("abort_unconfirmed", stage)
                raise Music3SglangClientError("timeout", stage)
            try:
                response = transport_task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise Music3SglangClientError("transport_error", stage) from None
        except asyncio.CancelledError:
            await self._cancel_transport_task(transport_task)
            raise
        finally:
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)

        if type(response) is not Music3TransportResponse:
            raise Music3SglangClientError("transport_contract", stage)
        if response.url != request.url or response.redirect_count != 0:
            raise Music3SglangClientError("redirect_or_origin", stage)
        if len(response.body) > request.max_response_bytes:
            raise Music3SglangClientError("response_too_large", stage)
        headers = _header_map(response.headers)
        if "location" in headers:
            raise Music3SglangClientError("redirect_or_origin", stage)
        if "content-length" in headers:
            raw_length = headers["content-length"]
            if (
                not raw_length
                or not raw_length.isascii()
                or not raw_length.isdecimal()
                or raw_length != str(len(response.body))
            ):
                raise Music3SglangClientError("invalid_response", stage)
        return response

    @staticmethod
    def _json_response(
        response: Music3TransportResponse,
        *,
        stage: str,
    ) -> dict[str, object]:
        if response.status_code != 200:
            raise Music3SglangClientError("http_status", stage)
        headers = _header_map(response.headers)
        if headers.get("content-type") != "application/json":
            raise Music3SglangClientError("content_type", stage)
        try:
            return _strict_json_object(
                response.body,
                maximum_bytes=MAX_JSON_RESPONSE_BYTES,
            )
        except MusicModelContractError:
            raise Music3SglangClientError("invalid_response", stage) from None

    async def generate(
        self,
        request: object,
        *,
        authorization: Music3ExternalAuthorizationEvidence,
        disconnect_event: asyncio.Event,
    ) -> Music3GenerationResult:
        """Generate after gates and probes; cancel only this in-flight call."""

        validated = validate_music3_sglang_request(request)
        authorization = self._authorization_is_current(authorization)

        health_request = Music3TransportRequest(
            method="GET",
            url=self._base_url + HEALTH_PATH,
            headers=(("accept", "application/json"),),
            body=None,
            timeout_seconds=self._probe_timeout,
            max_response_bytes=MAX_JSON_RESPONSE_BYTES,
        )
        health_response = await self._send(
            health_request,
            stage="health",
            disconnect_event=disconnect_event,
        )
        health_payload = self._json_response(health_response, stage="health")
        try:
            health: Music3HealthEvidence = validate_sglang_health_response(
                health_response.status_code,
                health_payload,
            )
        except MusicModelContractError:
            raise Music3SglangClientError("invalid_response", "health") from None
        del health

        authorization = self._authorization_is_current(authorization)
        models_request = Music3TransportRequest(
            method="GET",
            url=self._base_url + MODELS_PATH,
            headers=(("accept", "application/json"),),
            body=None,
            timeout_seconds=self._probe_timeout,
            max_response_bytes=MAX_JSON_RESPONSE_BYTES,
        )
        models_response = await self._send(
            models_request,
            stage="models",
            disconnect_event=disconnect_event,
        )
        models_payload = self._json_response(models_response, stage="models")
        try:
            model: Music3ModelEvidence = validate_sglang_models_response(models_payload)
        except MusicModelContractError:
            raise Music3SglangClientError("invalid_response", "models") from None
        del model

        authorization = self._authorization_is_current(authorization)
        request_body = self._request_body(validated)
        generation_request = Music3TransportRequest(
            method="POST",
            url=self._base_url + GENERATE_PATH,
            headers=(
                ("accept", "audio/wav"),
                ("content-type", "application/json"),
            ),
            body=request_body,
            timeout_seconds=self._generation_timeout,
            max_response_bytes=MAX_WAV_BYTES,
        )
        generation_response = await self._send(
            generation_request,
            stage="generation",
            disconnect_event=disconnect_event,
        )
        if generation_response.status_code != 200:
            raise Music3SglangClientError("http_status", "generation")
        response_headers = _header_map(generation_response.headers)
        if response_headers.get("content-type") != "audio/wav":
            raise Music3SglangClientError("content_type", "generation")
        try:
            wav_evidence = parse_music3_wav_bytes(generation_response.body)
        except MusicModelContractError:
            raise Music3SglangClientError("invalid_response", "generation") from None
        provenance = Music3GenerationProvenance(
            model_id=MUSIC3_MODEL_ID,
            model_revision=MUSIC3_HF_EXACT_REVISION,
            runtime_source_revision=self._source_binding.runtime_source_revision,
            seed=validated.seed,
            max_new_tokens=validated.max_new_tokens,
            speed=validated.speed,
            authorization_scope=authorization.scope,
            execution_locality=LOCAL_EXPERIMENT_EXECUTION_LOCALITY,
            hosted_service_authorized=False,
            authorization_document_sha256=authorization.document_sha256.removeprefix(
                "sha256:"
            ),
            response=wav_evidence,
        )
        return Music3GenerationResult(
            audio_bytes=generation_response.body,
            provenance=provenance,
        )
