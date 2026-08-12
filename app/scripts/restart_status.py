"""Bounded operator client for the stable-share restart status endpoint.

The client provides strict payload construction plus authenticated show, set,
and exact-generation clear operations for coordinated restart workflows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

STATUS_PATH = "/.well-known/maestro-share/status"
STATES = frozenset({
    "planned",
    "waiting_for_boundary",
    "restarting",
    "verifying",
    "complete",
    "postponed",
    "forced_emergency",
})
REASONS = frozenset({"restart", "maintenance", "shutdown", "incident"})
MAX_TTL_SECONDS = 24 * 60 * 60
MAX_RESPONSE_BYTES = 16 * 1024
REQUEST_TIMEOUT_SECONDS = 10
_MAX_MESSAGE_CHARS = 240
_GENERATION_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_USER_AGENT = "Maestro-Restart-Status/1.0"


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the Worker bearer secret to a redirect destination."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_open(request: Request, timeout: float):
    return build_opener(_NoRedirect).open(request, timeout=timeout)


def canonical_stable_url(value: str) -> str:
    """Return a canonical HTTPS workers.dev origin."""
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid stable-share URL") from error
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or not hostname.endswith(".workers.dev")
        or hostname == "workers.dev"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid stable-share URL")
    return f"https://{hostname}"


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not _UTC_PATTERN.fullmatch(value):
        raise ValueError("Timestamp must use canonical UTC form")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError as error:
        raise ValueError("Timestamp must use canonical UTC form") from error
    canonical = parsed.astimezone(timezone.utc)
    if canonical.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("Timestamp must use canonical UTC form")
    return canonical


def canonical_utc(value: str) -> str:
    """Validate the Worker's exact second-precision UTC timestamp format."""
    return _parse_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must be timezone-aware")
    return current.astimezone(timezone.utc).replace(microsecond=0)


def new_generation() -> str:
    """Create an opaque URL-safe generation identifier."""
    return secrets.token_urlsafe(24)


def _validated_generation(value: str | None) -> str:
    generation = new_generation() if value is None else value
    if not isinstance(generation, str) or not _GENERATION_PATTERN.fullmatch(generation):
        raise ValueError("Invalid generation")
    return generation


def build_eta(
    *,
    at: str | None = None,
    earliest: str | None = None,
    latest: str | None = None,
) -> dict[str, str] | None:
    """Build one of the Worker's exact optional ETA forms."""
    if at is not None:
        if earliest is not None or latest is not None:
            raise ValueError("ETA must be either at or range")
        return {"kind": "at", "at": canonical_utc(at)}
    if earliest is None and latest is None:
        return None
    if earliest is None or latest is None:
        raise ValueError("ETA range requires earliest and latest")
    canonical_earliest = canonical_utc(earliest)
    canonical_latest = canonical_utc(latest)
    if canonical_earliest > canonical_latest:
        raise ValueError("ETA range is reversed")
    return {
        "kind": "range",
        "earliest": canonical_earliest,
        "latest": canonical_latest,
    }


def build_status_payload(
    *,
    state: str,
    reason: str,
    message: str,
    ttl_seconds: int,
    generation: str | None = None,
    eta: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the Worker's exact eight-key schema from public operator values."""
    if state not in STATES:
        raise ValueError("Invalid restart state")
    if reason not in REASONS:
        raise ValueError("Invalid restart reason")
    if (
        not isinstance(message, str)
        or not message
        or len(message) > _MAX_MESSAGE_CHARS
        or any(unicodedata.category(character) == "Cc" for character in message)
    ):
        raise ValueError("Invalid restart message")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise TypeError("TTL must be an integer")
    if ttl_seconds < 1 or ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError("TTL must be between 1 second and 24 hours")
    if eta is not None:
        if set(eta) == {"kind", "at"} and eta.get("kind") == "at":
            eta = build_eta(at=eta.get("at"))
        elif (
            set(eta) == {"kind", "earliest", "latest"}
            and eta.get("kind") == "range"
        ):
            eta = build_eta(
                earliest=eta.get("earliest"),
                latest=eta.get("latest"),
            )
        else:
            raise ValueError("Invalid ETA")
    issued = _utc_now(now)
    expires = issued + timedelta(seconds=ttl_seconds)
    issued_at = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    if eta is not None:
        if eta["kind"] == "at":
            if eta["at"] < issued_at or eta["at"] > expires_at:
                raise ValueError("ETA is outside the status lifetime")
        elif eta["earliest"] < issued_at or eta["latest"] > expires_at:
            raise ValueError("ETA is outside the status lifetime")
    return {
        "schema_version": 1,
        "generation": _validated_generation(generation),
        "state": state,
        "reason": reason,
        "message": message,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "eta": eta,
    }


def validate_status_payload(value: object) -> dict[str, Any]:
    """Validate an existing payload against the same closed Worker contract."""
    keys = {
        "schema_version",
        "generation",
        "state",
        "reason",
        "message",
        "issued_at",
        "expires_at",
        "eta",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise ValueError("Invalid restart status")
    issued = _parse_utc(value.get("issued_at"))
    expires = _parse_utc(value.get("expires_at"))
    ttl_seconds = int((expires - issued).total_seconds())
    rebuilt = build_status_payload(
        state=value.get("state"),
        reason=value.get("reason"),
        message=value.get("message"),
        ttl_seconds=ttl_seconds,
        generation=value.get("generation"),
        eta=value.get("eta"),
        now=issued,
    )
    if rebuilt != value:
        raise ValueError("Invalid restart status")
    return rebuilt


def _read_json_response(response) -> dict[str, Any]:
    length_header = response.headers.get("Content-Length") if response.headers else None
    if length_header is not None:
        try:
            length = int(length_header)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid restart-status response") from error
        if length < 0 or length > MAX_RESPONSE_BYTES:
            raise ValueError("Invalid restart-status response")
    content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Invalid restart-status response")
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Invalid restart-status response")
    return value


def _request(
    stable_url: str,
    update_secret: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
    open_request: Callable = _default_open,
) -> dict[str, Any]:
    if not update_secret or len(update_secret.encode("utf-8")) < 32:
        raise ValueError("Restart-status operator configuration is unavailable")
    data = None if payload is None else json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {update_secret}",
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        canonical_stable_url(stable_url) + STATUS_PATH,
        data=data,
        method=method,
        headers=headers,
    )
    with open_request(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError("Restart-status request was rejected")
        return _read_json_response(response)


def _environment_config(environ: Mapping[str, str] | None) -> tuple[str, str]:
    source = os.environ if environ is None else environ
    return (
        source.get("PINOKIO_STABLE_SHARE_URL", ""),
        source.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET", ""),
    )


def _status_response(response: dict[str, Any]) -> dict[str, Any] | None:
    if set(response) != {"ok", "status"} or response.get("ok") is not True:
        raise ValueError("Invalid restart-status response")
    status = response.get("status")
    return None if status is None else validate_status_payload(status)


def show_status(
    *,
    environ: Mapping[str, str] | None = None,
    open_request: Callable = _default_open,
) -> dict[str, Any] | None:
    stable_url, update_secret = _environment_config(environ)
    return _status_response(
        _request(
            stable_url,
            update_secret,
            method="GET",
            payload=None,
            open_request=open_request,
        ),
    )


def set_status(
    payload: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    open_request: Callable = _default_open,
) -> dict[str, Any]:
    stable_url, update_secret = _environment_config(environ)
    proposed = validate_status_payload(payload)
    accepted = _status_response(_request(
        stable_url,
        update_secret,
        method="PUT",
        payload=proposed,
        open_request=open_request,
    ))
    if accepted != proposed:
        raise ValueError("Restart-status update was not confirmed")
    return accepted


def clear_status(
    generation: str,
    *,
    environ: Mapping[str, str] | None = None,
    open_request: Callable = _default_open,
) -> bool:
    stable_url, update_secret = _environment_config(environ)
    response = _request(
        stable_url,
        update_secret,
        method="DELETE",
        payload={"generation": _validated_generation(generation)},
        open_request=open_request,
    )
    if (
        set(response) != {"ok", "cleared"}
        or response.get("ok") is not True
        or not isinstance(response.get("cleared"), bool)
    ):
        raise ValueError("Invalid restart-status response")
    return response["cleared"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage stable-share restart status")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="Show the current public restart state")

    setter = commands.add_parser("set", help="Set the public restart state")
    setter.add_argument("--state", choices=sorted(STATES), required=True)
    setter.add_argument("--reason", choices=sorted(REASONS), required=True)
    setter.add_argument("--message", required=True)
    setter.add_argument("--ttl-seconds", type=int, default=900)
    setter.add_argument("--generation")
    setter.add_argument("--eta-at")
    setter.add_argument("--eta-earliest")
    setter.add_argument("--eta-latest")

    clearer = commands.add_parser("clear", help="Clear one restart generation")
    clearer.add_argument("--generation", required=True)
    return parser


def _summary_state(status: dict[str, Any] | None) -> str:
    return status["state"] if status is not None else "none"


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    open_request: Callable = _default_open,
    output: Callable[[str], None] = print,
) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "show":
            status = show_status(environ=environ, open_request=open_request)
            output(f"MAESTRO_RESTART_STATUS {_summary_state(status)}")
        elif args.command == "set":
            eta = build_eta(
                at=args.eta_at,
                earliest=args.eta_earliest,
                latest=args.eta_latest,
            )
            payload = build_status_payload(
                state=args.state,
                reason=args.reason,
                message=args.message,
                ttl_seconds=args.ttl_seconds,
                generation=args.generation,
                eta=eta,
            )
            set_status(payload, environ=environ, open_request=open_request)
            output(f"MAESTRO_RESTART_STATUS_SET {args.state}")
        else:
            cleared = clear_status(
                args.generation,
                environ=environ,
                open_request=open_request,
            )
            output(
                "MAESTRO_RESTART_STATUS_CLEARED"
                if cleared
                else "MAESTRO_RESTART_STATUS_NOT_CLEARED",
            )
            if not cleared:
                return 1
    except (
        HTTPError,
        URLError,
        OSError,
        TimeoutError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as error:
        raise SystemExit("Maestro restart-status request failed") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
