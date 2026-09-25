"""Register the current verified Maestro share origin with the local UI.

The Cloudflare update secret is read only from the process environment.  It is
never accepted on argv, included in a local API payload, printed, or persisted.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_HEALTH_PATH = "/.well-known/maestro-share/health"
_UPDATE_PATH = "/.well-known/maestro-share/target"
_PUBLIC_DIRECT_PATH = "/.well-known/maestro-share/direct"
_PUBLIC_HEALTH_PATH = "/health"
_PUBLIC_READY_PATH = "/ready"
_LOCAL_REGISTRATION_PATH = "/api/v1/access-context/share-url"
_MAX_RESPONSE_BYTES = 16 * 1024
_REQUEST_USER_AGENT = "Maestro-Stable-Share/1.0"
_REQUEST_ACCEPT = "application/json"
_STABLE_VERIFICATION_BUDGET_SECONDS = 60.0


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
    timeout: float = 10,
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
    with open_request(request, timeout=timeout) as response:
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
    monotonic: Callable[[], float] = time.monotonic,
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
    deadline = monotonic() + _STABLE_VERIFICATION_BUDGET_SECONDS
    for attempt in range(max(1, health_attempts)):
        try:
            healthy = _json_request(
                stable + _HEALTH_PATH,
                method="GET",
                payload=None,
                headers=authorization,
                open_request=open_request,
                timeout=_remaining_timeout(deadline, monotonic),
            )
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                return None
            healthy = None
        except (URLError, OSError, TimeoutError):
            healthy = None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        # The authenticated endpoint confirms only the Worker's target record.
        # Its public route can still return 503 while the new tunnel warms up.
        if (
            healthy is not None
            and _confirms_target(healthy, quick_url)
            and _public_route_ready(
                stable, quick_url, open_request=open_request,
                deadline=deadline, monotonic=monotonic,
            )
        ):
            return stable
        if attempt + 1 < max(1, health_attempts):
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            sleep(min(health_interval, remaining))
    return None


def _remaining_timeout(deadline: float, monotonic: Callable[[], float]) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Stable-share verification deadline passed")
    return min(10.0, remaining)


def _open_public_path(
    stable: str, quick_url: str, path: str, *, open_request: Callable,
    deadline: float, monotonic: Callable[[], float],
):
    """Read the public route, following only its exact configured rollback target."""

    headers = {"User-Agent": _REQUEST_USER_AGENT, "Accept": _REQUEST_ACCEPT}
    try:
        return open_request(
            Request(stable + path, method="GET", headers=headers),
            timeout=_remaining_timeout(deadline, monotonic),
        )
    except HTTPError as error:
        location = error.headers.get("Location") if error.headers else None
        status = error.code
        error.close()
        if status != 307 or location != quick_url + path:
            raise
        return open_request(
            Request(quick_url + path, method="GET", headers=headers),
            timeout=_remaining_timeout(deadline, monotonic),
        )


def _public_route_ready(
    stable: str, quick_url: str, *, open_request: Callable,
    deadline: float, monotonic: Callable[[], float],
) -> bool:
    """Bind the public route to this tunnel and require app health/readiness."""

    try:
        direct = Request(
            stable + _PUBLIC_DIRECT_PATH,
            method="GET",
            headers={"User-Agent": _REQUEST_USER_AGENT, "Accept": _REQUEST_ACCEPT},
        )
        try:
            with open_request(direct, timeout=_remaining_timeout(deadline, monotonic)):
                return False
        except HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            status = error.code
            error.close()
            if status != 307 or location != quick_url + "/":
                return False
        with _open_public_path(
            stable, quick_url, _PUBLIC_HEALTH_PATH, open_request=open_request,
            deadline=deadline, monotonic=monotonic,
        ) as response:
            if response.status != 200:
                return False
            health = _read_json_response(response)
        if health.get("status") != "ok":
            return False
        with _open_public_path(
            stable, quick_url, _PUBLIC_READY_PATH, open_request=open_request,
            deadline=deadline, monotonic=monotonic,
        ) as response:
            return response.status == 200
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _confirms_target(response: dict[str, Any], quick_url: str) -> bool:
    target = response.get("target")
    return (
        response.get("ok") is True
        and response.get("configured") is True
        and isinstance(target, str)
        and hmac.compare_digest(target, quick_url)
    )


def replay_share_url(
    origin: str,
    quick_tunnel_url: str,
    selected_url: str,
    *,
    stable_verified: bool,
    open_request: Callable = _default_open,
) -> tuple[str, str]:
    """Replay a previously verified selection into one backend process."""

    local_origin = _canonical_loopback_origin(origin)
    quick_url = _canonical_quick_tunnel_url(quick_tunnel_url)
    if stable_verified:
        selected = _canonical_workers_dev_url(selected_url)
        kind = "stable"
    else:
        selected = _canonical_quick_tunnel_url(selected_url)
        if selected != quick_url:
            raise ValueError("Quick share replay did not match the current tunnel")
        kind = "quick"
    payload: dict[str, Any] = {
        "share_url": selected,
        "quick_tunnel_url": quick_url,
        "stable_verified": stable_verified,
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
    return replay_share_url(
        local_origin,
        quick_url,
        selected,
        stable_verified=bool(stable),
        open_request=open_request,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or ())
    if arguments == ["--watch"]:
        from share_registration_watch import main as watch_main

        return watch_main([])
    if len(arguments) == 2 and arguments[0] == "--wait-watch":
        from share_registration_watch import main as watch_main

        return watch_main(["--wait-registered-url", arguments[1]])
    if arguments:
        raise SystemExit("Maestro share registration failed")
    try:
        selected, kind = register_share_url(
            os.environ.get("MAESTRO_LOCAL_ORIGIN", ""),
            os.environ.get("MAESTRO_QUICK_SHARE_URL", ""),
            stable_url=os.environ.get("PINOKIO_STABLE_SHARE_URL", ""),
            update_secret=os.environ.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET", ""),
        )
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        raise SystemExit("Maestro share registration failed") from None
    print(f"MAESTRO_SHARE_READY {selected} {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
