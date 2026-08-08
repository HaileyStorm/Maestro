"""Register the current verified Maestro share origin with the local UI.

The Cloudflare update secret is read only from the process environment.  It is
never accepted on argv, included in a local API payload, printed, or persisted.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_HEALTH_PATH = "/.well-known/maestro-share/health"
_UPDATE_PATH = "/.well-known/maestro-share/target"
_LOCAL_REGISTRATION_PATH = "/api/v1/access-context/share-url"
_MAX_RESPONSE_BYTES = 16 * 1024
_REQUEST_USER_AGENT = "Maestro-Stable-Share/1.0"
_REQUEST_ACCEPT = "application/json"


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the Worker bearer secret to a redirect destination."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_open(request: Request, timeout: float):
    return build_opener(_NoRedirect).open(request, timeout=timeout)


def _canonical_loopback_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Maestro share registration origin has an invalid port") from error
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Maestro share registration origin must be loopback HTTP")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}{f':{port}' if port is not None else ''}"


def _canonical_public_origin(value: str, suffix: str, message: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(message) from error
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or not hostname.endswith(suffix)
        or hostname == suffix.removeprefix(".")
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(message)
    return f"https://{hostname}"


def _canonical_quick_tunnel_url(value: str) -> str:
    return _canonical_public_origin(
        value, ".trycloudflare.com", "Invalid Cloudflare quick-tunnel URL",
    )


def _canonical_workers_dev_url(value: str) -> str:
    return _canonical_public_origin(
        value, ".workers.dev", "Invalid Cloudflare Workers URL",
    )


def _read_json_response(response) -> dict[str, Any]:
    length_header = response.headers.get("Content-Length") if response.headers else None
    if length_header:
        try:
            if int(length_header) > _MAX_RESPONSE_BYTES:
                raise ValueError("Share service response was too large")
        except ValueError as error:
            raise ValueError("Invalid share service response length") from error
    content = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(content) > _MAX_RESPONSE_BYTES:
        raise ValueError("Share service response was too large")
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Invalid share service response")
    return value


def _json_request(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    open_request: Callable = _default_open,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            **headers,
            "User-Agent": _REQUEST_USER_AGENT,
            "Accept": _REQUEST_ACCEPT,
        },
    )
    with open_request(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Share service rejected the request")
        return _read_json_response(response)


def _verified_stable_origin(
    quick_url: str,
    stable_url: str,
    update_secret: str,
    *,
    open_request: Callable = _default_open,
    sleep: Callable[[float], None] = time.sleep,
    health_attempts: int = 13,
    health_interval: float = 5.0,
) -> str | None:
    if not stable_url or not update_secret:
        return None
    try:
        stable = _canonical_workers_dev_url(stable_url)
    except ValueError:
        return None
    if len(update_secret.encode("utf-8")) < 32:
        return None
    authorization = {"Authorization": f"Bearer {update_secret}"}
    try:
        updated = _json_request(
            stable + _UPDATE_PATH,
            method="PUT",
            payload={"target": quick_url},
            headers={**authorization, "Content-Type": "application/json"},
            open_request=open_request,
        )
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not _confirms_target(updated, quick_url):
        return None
    for attempt in range(max(1, health_attempts)):
        try:
            healthy = _json_request(
                stable + _HEALTH_PATH,
                method="GET",
                payload=None,
                headers=authorization,
                open_request=open_request,
            )
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                return None
            healthy = None
        except (URLError, OSError, TimeoutError):
            healthy = None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if healthy is not None and _confirms_target(healthy, quick_url):
            return stable
        if attempt + 1 < max(1, health_attempts):
            sleep(health_interval)
    return None


def _confirms_target(response: dict[str, Any], quick_url: str) -> bool:
    target = response.get("target")
    return (
        response.get("ok") is True
        and response.get("configured") is True
        and isinstance(target, str)
        and hmac.compare_digest(target, quick_url)
    )


def register_share_url(
    origin: str,
    quick_tunnel_url: str,
    *,
    stable_url: str = "",
    update_secret: str = "",
    open_request: Callable = _default_open,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    local_origin = _canonical_loopback_origin(origin)
    quick_url = _canonical_quick_tunnel_url(quick_tunnel_url)
    stable = _verified_stable_origin(
        quick_url,
        stable_url,
        update_secret,
        open_request=open_request,
        sleep=sleep,
    )
    selected = stable or quick_url
    kind = "stable" if stable else "quick"
    payload: dict[str, Any] = {
        "share_url": selected,
        "quick_tunnel_url": quick_url,
        "stable_verified": bool(stable),
    }
    result = _json_request(
        local_origin + _LOCAL_REGISTRATION_PATH,
        method="PUT",
        payload=payload,
        headers={"Content-Type": "application/json", "Origin": local_origin},
        open_request=open_request,
    )
    if result.get("status") != "ok" or result.get("share_url") != selected:
        raise ValueError("Maestro rejected the runtime share URL")
    return selected, kind


def main() -> int:
    try:
        selected, kind = register_share_url(
            os.environ.get("MAESTRO_LOCAL_ORIGIN", ""),
            os.environ.get("MAESTRO_QUICK_SHARE_URL", ""),
            stable_url=os.environ.get("PINOKIO_STABLE_SHARE_URL", ""),
            update_secret=os.environ.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET", ""),
        )
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise SystemExit("Maestro share registration failed") from error
    print(f"MAESTRO_SHARE_READY {selected} {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
