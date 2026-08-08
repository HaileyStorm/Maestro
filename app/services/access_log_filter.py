"""Keep high-frequency UI polling out of Maestro's console output."""

from __future__ import annotations

import logging


# Poll inventory adapted from official Maestro v1.6.5 commit
# d500f58e0c2be948800c757fd106c5254c70b605. Local access, share, and
# capability routes remain visible even if a future poll pattern overlaps them.
QUIET_POLL_PATHS = frozenset(
    {
        "/health",
        "/api/v1/audio/analyze/status",
        "/api/v1/civitai/downloads",
        "/api/v1/downloads/active",
        "/api/v1/jobs",
        "/api/v1/llm/status",
        "/api/v1/llm/stream-status",
        "/api/v1/models/downloads/status",
        "/api/v1/outputs",
        "/api/v1/sam/status",
        "/api/v1/system-stats",
    }
)

QUIET_POLL_PREFIXES = (
    "/api/v1/director/pipeline/",
    "/api/v1/director/pipelines/",
    "/api/v1/loras/scan-status/",
    "/api/v1/status/",
)

_ALWAYS_VISIBLE_PATHS = frozenset(
    {
        "/api/v1/access-context",
        "/api/v1/output-shares",
        "/api/v1/workspaces",
    }
)

_ALWAYS_VISIBLE_PREFIXES = (
    "/api/v1/access-context/",
    "/api/v1/jobs/",
    "/api/v1/output-shares/",
    "/api/v1/workspaces/",
    "/share/",
)


def _normalized_path(path: str) -> str:
    return path.split("?", 1)[0]


def _is_always_visible_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized in _ALWAYS_VISIBLE_PATHS or any(
        normalized.startswith(prefix) for prefix in _ALWAYS_VISIBLE_PREFIXES
    )


def _is_quiet_poll_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized in QUIET_POLL_PATHS or any(
        normalized.startswith(prefix) for prefix in QUIET_POLL_PREFIXES
    )


class QuietPollingAccessFilter(logging.Filter):
    """Drop only successful read-only polls from Uvicorn's access logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records use exactly:
        # (client_addr, method, full_path, http_version, status_code).
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True

        client_addr, method, path, http_version, status_code = args
        if (
            not isinstance(client_addr, str)
            or not isinstance(method, str)
            or not isinstance(path, str)
            or not isinstance(http_version, str)
            or not isinstance(status_code, int)
            or isinstance(status_code, bool)
        ):
            return True

        if (
            method.upper() not in {"GET", "HEAD"}
            or status_code < 200
            or status_code >= 400
            or _is_always_visible_path(path)
        ):
            return True
        return not _is_quiet_poll_path(path)


def install_quiet_access_filter(
    logger_name: str = "uvicorn.access",
) -> QuietPollingAccessFilter:
    """Install the polling filter once and return the active instance."""

    logger = logging.getLogger(logger_name)
    for current in logger.filters:
        if isinstance(current, QuietPollingAccessFilter):
            return current
    quiet_filter = QuietPollingAccessFilter()
    logger.addFilter(quiet_filter)
    return quiet_filter


__all__ = [
    "QUIET_POLL_PATHS",
    "QUIET_POLL_PREFIXES",
    "QuietPollingAccessFilter",
    "install_quiet_access_filter",
]
