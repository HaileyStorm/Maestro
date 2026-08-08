"""Bounded, public-only discovery sources for Maestro research cycles.

Remote text is always treated as hostile data.  This module deliberately has
no hooks for Maestro projects, prompts, jobs, media, logs, credentials, or
arbitrary user-provided URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode, urlparse
from urllib.parse import unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RECORDS_PER_SOURCE = 20
MAX_CANDIDATES_PER_CYCLE = 24
DEFAULT_CANDIDATES_PER_CYCLE = 6
MAX_TITLE_CHARS = 180
MAX_EXCERPT_CHARS = 1_200
MAX_TAGS = 16

_FETCH_ALLOWLIST: Mapping[str, tuple[str, ...]] = {
    "api.github.com": ("/search/repositories",),
    "huggingface.co": ("/api/models",),
    "civitai.com": ("/api/v1/models",),
}
_REFERENCE_ALLOWLIST = frozenset({
    "github.com", "huggingface.co", "civitai.com",
})
_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        r"(?:system|developer)\s+(?:message|prompt|instructions?)",
        r"you\s+are\s+(?:now|an?\s+ai|chatgpt)",
        r"reveal\s+(?:the\s+)?(?:secret|credential|api\s*key|prompt)",
        r"(?:execute|run)\s+(?:this\s+)?(?:command|code|script)",
        r"<\/?(?:system|developer|assistant|tool)[^>]*>",
    )
)


class ResearchSourceError(RuntimeError):
    """A public source violated the discovery boundary or response contract."""


@dataclass(frozen=True)
class SourceSpec:
    lane: str
    url: str
    parser: str


def _github_url() -> str:
    query = urlencode({
        "q": "(video generation OR image generation) (model OR lora OR workflow)",
        "sort": "updated",
        "order": "desc",
        "per_page": str(MAX_RECORDS_PER_SOURCE),
    })
    return f"https://api.github.com/search/repositories?{query}"


def _huggingface_url() -> str:
    query = urlencode({
        "pipeline_tag": "text-to-video",
        "sort": "lastModified",
        "direction": "-1",
        "limit": str(MAX_RECORDS_PER_SOURCE),
        "full": "false",
    })
    return f"https://huggingface.co/api/models?{query}"


def _civitai_url(model_type: str) -> str:
    query = urlencode({
        "types": model_type,
        "sort": "Newest",
        "period": "Week",
        "limit": str(MAX_RECORDS_PER_SOURCE),
        "nsfw": "true",
    })
    return f"https://civitai.com/api/v1/models?{query}"


DEFAULT_SOURCE_SPECS = (
    SourceSpec("github_tools", _github_url(), "github"),
    SourceSpec("huggingface_models", _huggingface_url(), "huggingface"),
    SourceSpec("civitai_loras", _civitai_url("LORA"), "civitai"),
    SourceSpec("civitai_models", _civitai_url("Checkpoint"), "civitai"),
)


def validate_fetch_url(url: str) -> str:
    """Return a canonical public fetch URL or fail closed."""
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ResearchSourceError("research sources must use allowlisted HTTPS endpoints")
    prefixes = _FETCH_ALLOWLIST.get(host)
    if not prefixes or not any(parsed.path == prefix for prefix in prefixes):
        raise ResearchSourceError("research source endpoint is not allowlisted")
    return parsed.geturl()


def _hostile(value: str) -> bool:
    normalized = value.replace("-", " ").replace("_", " ")
    return any(pattern.search(normalized) for pattern in _INJECTION_PATTERNS)


def _reference_url(value: Any, fallback: str) -> tuple[str, list[str]]:
    for position, candidate in enumerate((value, fallback)):
        try:
            parsed = urlparse(str(candidate))
            host = (parsed.hostname or "").lower().rstrip(".")
            valid = (
                parsed.scheme == "https"
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
                and host in _REFERENCE_ALLOWLIST
            )
        except Exception:
            valid = False
            host = ""
        if not valid:
            continue
        result = parsed.geturl()[:1_024]
        if _hostile(unquote(result)):
            digest = hashlib.sha256(result.encode("utf-8")).hexdigest()[:16]
            return f"https://{host}/withheld-{digest}", ["possible_prompt_injection_url"]
        return result, ([] if position == 0 else ["invalid_remote_url"])
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return f"https://github.com/withheld-{digest}", ["invalid_remote_url"]


def _clean_scalar(value: Any, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
    text = " ".join(text.split())
    return text[:limit]


def sanitize_untrusted(value: Any) -> tuple[str, list[str]]:
    """Bound remote prose and suppress obvious instruction-shaped payloads."""
    text = _clean_scalar(value, MAX_EXCERPT_CHARS)
    flags: list[str] = []
    if _hostile(text):
        flags.append("possible_prompt_injection")
        # Analysis value comes from metadata, not from preserving a directive.
        text = "[remote prose withheld: possible prompt injection]"
    if len(str(value or "")) > MAX_EXCERPT_CHARS:
        flags.append("excerpt_truncated")
    return text, flags


def _safe_tags(values: Any) -> tuple[list[str], list[str]]:
    if not isinstance(values, list):
        return [], []
    tags: set[str] = set()
    flags: list[str] = []
    for value in values[: MAX_TAGS * 2]:
        tag, tag_flags = sanitize_untrusted(_clean_scalar(value, 64).lower())
        if tag_flags:
            flags.append("possible_prompt_injection_tag")
            continue
        if tag:
            tags.add(tag)
    return sorted(tags)[:MAX_TAGS], sorted(set(flags))


def _iso_timestamp(value: Any) -> str:
    text = _clean_scalar(value, 64)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _candidate(
    *,
    lane: str,
    source_id: Any,
    kind: str,
    title: Any,
    canonical_url: Any,
    fallback_url: str,
    updated_at: Any,
    excerpt: Any,
    tags: Any,
) -> dict[str, Any] | None:
    raw_id = _clean_scalar(source_id, 1_024)
    raw_title = _clean_scalar(title, 1_024)
    if not raw_id or not raw_title:
        return None
    safe_title, title_flags = sanitize_untrusted(raw_title)
    if title_flags:
        safe_title = f"[withheld remote title {hashlib.sha256(raw_title.encode()).hexdigest()[:12]}]"
    safe_excerpt, excerpt_flags = sanitize_untrusted(excerpt)
    safe_tags, tag_flags = _safe_tags(tags)
    safe_url, url_flags = _reference_url(canonical_url, fallback_url)
    flags = sorted(set(title_flags + excerpt_flags + tag_flags + url_flags))
    source_hash = hashlib.sha256(f"{lane}\0{raw_id}".encode("utf-8")).hexdigest()
    parsed = urlparse(safe_url)
    path = parsed.path.strip("/").lower()
    if parsed.hostname == "github.com" and len(path.split("/")) >= 2:
        aliases = ["github:" + "/".join(path.split("/")[:2])]
    elif parsed.hostname == "huggingface.co" and len(path.split("/")) >= 2:
        aliases = ["huggingface:" + "/".join(path.split("/")[:2])]
    elif parsed.hostname == "civitai.com" and path.startswith("models/"):
        aliases = ["civitai:" + path.split("/", 2)[1]]
    else:
        aliases = [f"source:{source_hash}"]
    normalized = {
        "source_lane": lane,
        "source_id": f"{lane}:{source_hash}",
        "identity_aliases": aliases,
        "kind": kind if kind in {"model", "tune", "tool", "lora"} else "tool",
        "title": safe_title,
        "canonical_url": safe_url,
        "updated_at": _iso_timestamp(updated_at),
        "untrusted_excerpt": safe_excerpt,
        "tags": safe_tags,
        "content_flags": flags,
        "untrusted": True,
    }
    digest_payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    normalized["source_digest"] = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    return normalized


def _parse_github(payload: Any, spec: SourceSpec) -> Iterable[dict[str, Any]]:
    records = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ResearchSourceError("GitHub response does not contain an items array")
    for record in records[:MAX_RECORDS_PER_SOURCE]:
        if not isinstance(record, dict):
            continue
        full_name = record.get("full_name")
        fallback = f"https://github.com/{_clean_scalar(full_name, 240)}"
        candidate = _candidate(
            lane=spec.lane,
            source_id=f"github:{full_name}",
            kind="tool",
            title=record.get("name") or full_name,
            canonical_url=record.get("html_url"),
            fallback_url=fallback,
            updated_at=record.get("updated_at"),
            excerpt=record.get("description"),
            tags=record.get("topics"),
        )
        if candidate:
            yield candidate


def _parse_huggingface(payload: Any, spec: SourceSpec) -> Iterable[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ResearchSourceError("Hugging Face response must be an array")
    for record in payload[:MAX_RECORDS_PER_SOURCE]:
        if not isinstance(record, dict):
            continue
        model_id = record.get("modelId") or record.get("id")
        tags = record.get("tags")
        lowered = {str(item).lower() for item in tags} if isinstance(tags, list) else set()
        kind = "lora" if "lora" in lowered else "model"
        fallback = f"https://huggingface.co/{_clean_scalar(model_id, 240)}"
        candidate = _candidate(
            lane=spec.lane,
            source_id=f"huggingface:{model_id}",
            kind=kind,
            title=model_id,
            canonical_url=fallback,
            fallback_url=fallback,
            updated_at=record.get("lastModified") or record.get("createdAt"),
            excerpt=record.get("description") or record.get("pipeline_tag"),
            tags=tags,
        )
        if candidate:
            yield candidate


def _parse_civitai(payload: Any, spec: SourceSpec) -> Iterable[dict[str, Any]]:
    records = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ResearchSourceError("Civitai response does not contain an items array")
    for record in records[:MAX_RECORDS_PER_SOURCE]:
        if not isinstance(record, dict):
            continue
        model_id = record.get("id")
        model_type = _clean_scalar(record.get("type"), 32).lower()
        kind = "lora" if model_type == "lora" else "tune"
        fallback = f"https://civitai.com/models/{_clean_scalar(model_id, 40)}"
        candidate = _candidate(
            lane=spec.lane,
            source_id=f"civitai:{model_id}",
            kind=kind,
            title=record.get("name"),
            canonical_url=fallback,
            fallback_url=fallback,
            updated_at=record.get("updatedAt") or record.get("createdAt"),
            excerpt=record.get("description"),
            tags=record.get("tags"),
        )
        if candidate:
            yield candidate


_PARSERS: Mapping[str, Callable[[Any, SourceSpec], Iterable[dict[str, Any]]]] = {
    "github": _parse_github,
    "huggingface": _parse_huggingface,
    "civitai": _parse_civitai,
}


class _AllowlistedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_fetch_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicJsonFetcher:
    """Fetch JSON without auth, cookies, arbitrary URLs, or unbounded reads."""

    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_AllowlistedRedirects())

    def __call__(self, spec: SourceSpec) -> Any:
        url = validate_fetch_url(spec.url)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Maestro-public-research/1",
            },
            method="GET",
        )
        with self._opener.open(request, timeout=self.timeout_seconds) as response:
            validate_fetch_url(response.geturl())
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ResearchSourceError("research source response exceeds the byte limit")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResearchSourceError("research source returned invalid JSON") from error


def discover_public_candidates(
    *,
    fetcher: Callable[[SourceSpec], Any] | None = None,
    specs: Iterable[SourceSpec] = DEFAULT_SOURCE_SPECS,
    max_candidates: int = MAX_CANDIDATES_PER_CYCLE,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Discover bounded candidates, retaining per-lane failures as safe metadata."""
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool) or not 1 <= max_candidates <= MAX_CANDIDATES_PER_CYCLE:
        raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES_PER_CYCLE}")
    fetch = fetcher or PublicJsonFetcher()
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    lane_candidates: list[list[dict[str, Any]]] = []
    for spec in tuple(specs):
        validate_fetch_url(spec.url)
        parser = _PARSERS.get(spec.parser)
        if parser is None:
            raise ResearchSourceError(f"unsupported source parser {spec.parser!r}")
        try:
            lane_candidates.append(list(parser(fetch(spec), spec)))
        except Exception as error:
            safe_message, message_flags = sanitize_untrusted(error)
            safe_type, type_flags = sanitize_untrusted(type(error).__name__[:80])
            lane_candidates.append([])
            failures.append({
                "source_lane": spec.lane,
                "error_type": safe_type[:80],
                "message": safe_message[:240],
                "content_flags": ",".join(sorted(set(message_flags + type_flags))),
            })
    # Round-robin prevents a noisy first endpoint from starving later lanes.
    for position in range(MAX_RECORDS_PER_SOURCE):
        for lane in lane_candidates:
            if position >= len(lane):
                continue
            candidate = lane[position]
            source_id = candidate["source_id"]
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            candidates.append(candidate)
            if len(candidates) >= max_candidates:
                return candidates, failures
    return candidates, failures
