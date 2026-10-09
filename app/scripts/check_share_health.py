"""Bounded, credential-free connectivity checks for Pinokio's current share.

This helper never owns, starts, or stops a tunnel or Maestro. The existing
launcher polls it; only consecutive failures over a grace period mark a route
unavailable. Runtime receipts bind that history to the exact current URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

from register_share_url import (
    _canonical_loopback_origin,
    _canonical_quick_tunnel_url,
    _canonical_workers_dev_url,
    _default_open,
    _public_route_ready,
)
from share_registration_watch import (
    WatchConfigurationError,
    default_runtime_directory,
    secure_publish_runtime_text,
    secure_read_runtime_text,
)

FAILURE_GRACE_SECONDS = 60
MIN_FAILURES = 3
MAX_RECEIPT_AGE_SECONDS = 360
REQUEST_TIMEOUT_SECONDS = 3


def _bounded_open(request, timeout):
    return _default_open(request, timeout=min(REQUEST_TIMEOUT_SECONDS, timeout))


def probe_app(origin: str, *, open_request=_default_open) -> bool:
    """Require Maestro JSON health and readiness; never follow redirects."""
    try:
        headers = {"Accept": "application/json", "User-Agent": "Maestro-Share-Health/1.0"}
        with open_request(Request(origin + "/health", headers=headers),
                          timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(4097)
            if response.status != 200 or len(body) > 4096:
                return False
            health = json.loads(body)
            if not isinstance(health, dict) or health.get("status") != "ok":
                return False
        with open_request(Request(origin + "/ready", headers=headers),
                          timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status == 200
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError):
        return False


def _route_state(previous: dict, healthy: bool, checked_at: float) -> dict:
    if healthy:
        return {"state": "healthy", "failures": 0, "first_failure": None}
    if not isinstance(previous, dict):
        previous = {}
    failures = previous.get("failures", 0)
    first = previous.get("first_failure")
    if not isinstance(failures, int) or not 0 <= failures < 100000:
        failures = 0
    if not isinstance(first, (int, float)) or not 0 <= first <= checked_at:
        first = checked_at
        failures = 0
    failures += 1
    failed = failures >= MIN_FAILURES and checked_at - first >= FAILURE_GRACE_SECONDS
    return {"state": "unavailable" if failed else "checking",
            "failures": failures, "first_failure": first}


def check_share_health(origin: str, quick_url: str, stable_url: str = "", *,
                       state_file: Path | None = None, now=time.time,
                       probe=probe_app, stable_probe=_public_route_ready) -> tuple[str, str]:
    local = _canonical_loopback_origin(origin)
    quick = _canonical_quick_tunnel_url(quick_url)
    stable = _canonical_workers_dev_url(stable_url) if stable_url else ""
    if state_file is None:
        digest = hashlib.sha256(local.encode("utf-8")).hexdigest()[:24]
        state_file = default_runtime_directory() / f"maestro-share-health-{digest}.json"
    previous = {}
    try:
        payload = json.loads(secure_read_runtime_text(state_file))
        age = now() - payload.get("checked_at", 0)
        if (payload.get("origin") == local and payload.get("quick_url") == quick
                and payload.get("stable_url") == stable
                and 0 <= age <= MAX_RECEIPT_AGE_SECONDS):
            previous = payload
    except (OSError, ValueError, TypeError, AttributeError, WatchConfigurationError):
        pass
    if probe(local):
        quick_healthy = probe(quick)
        # /direct also binds the Worker's route to this exact Quick Tunnel.
        stable_healthy = bool(stable) and stable_probe(
            stable, quick, open_request=_bounded_open,
            deadline=time.monotonic() + 9, monotonic=time.monotonic,
        )
        checked_at = now()
        quick_state = _route_state(previous.get("quick", {}), quick_healthy, checked_at)
        stable_state = _route_state(previous.get("stable", {}), stable_healthy, checked_at)
    else:
        # Local startup/recovery is not evidence that the tunnel disconnected.
        checked_at = now()
        quick_state = stable_state = {"state": "unknown", "failures": 0, "first_failure": None}
    if not stable:
        stable_state = {"state": "absent", "failures": 0, "first_failure": None}
    receipt = {"origin": local, "quick_url": quick, "stable_url": stable,
               "checked_at": checked_at, "quick": quick_state, "stable": stable_state}
    secure_publish_runtime_text(state_file, json.dumps(receipt, separators=(",", ":")) + "\n")
    return quick_state["state"], stable_state["state"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--quick-url", required=True)
    parser.add_argument("--stable-url", default="")
    options = parser.parse_args(argv)
    try:
        quick = _canonical_quick_tunnel_url(options.quick_url)
        stable = _canonical_workers_dev_url(options.stable_url) if options.stable_url else ""
        quick_state, stable_state = check_share_health(options.origin, quick, stable)
        print(f"MAESTRO_SHARE_HEALTH {quick} {stable or 'none'} {quick_state} {stable_state}", flush=True)
        return 0
    except (OSError, ValueError, TypeError, WatchConfigurationError):
        print("MAESTRO_SHARE_HEALTH_FAILED invalid_configuration", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
