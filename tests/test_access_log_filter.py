"""Regression tests for Maestro's quiet Uvicorn polling access log."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import unittest


_APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(_APP))

from services.access_log_filter import (  # noqa: E402
    QUIET_POLL_PATHS,
    QUIET_POLL_PREFIXES,
    QuietPollingAccessFilter,
    install_quiet_access_filter,
)


def _access_record(
    method: str = "GET",
    path: str = "/health",
    status: int = 200,
    *,
    args: object | None = None,
) -> logging.LogRecord:
    record_args = (
        ("127.0.0.1:12345", method, path, "1.1", status)
        if args is None
        else args
    )
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        record_args,  # type: ignore[arg-type]
        None,
    )


class TestQuietPollingAccessFilter(unittest.TestCase):
    def setUp(self):
        self.quiet_filter = QuietPollingAccessFilter()

    def test_successful_polling_gets_and_heads_are_suppressed(self):
        for path in QUIET_POLL_PATHS:
            with self.subTest(path=path):
                self.assertFalse(self.quiet_filter.filter(_access_record(path=path)))
                self.assertFalse(
                    self.quiet_filter.filter(
                        _access_record(method="HEAD", path=path, status=204)
                    )
                )

        for prefix in QUIET_POLL_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertFalse(
                    self.quiet_filter.filter(
                        _access_record(path=prefix + "fixture-id")
                    )
                )

    def test_query_strings_do_not_restore_poll_noise(self):
        self.assertFalse(
            self.quiet_filter.filter(
                _access_record(path="/api/v1/system-stats?detail=1")
            )
        )

    def test_errors_mutations_and_meaningful_requests_remain_visible(self):
        for status in (199, 400, 404, 500):
            with self.subTest(status=status):
                self.assertTrue(
                    self.quiet_filter.filter(
                        _access_record(path="/api/v1/system-stats", status=status)
                    )
                )
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                self.assertTrue(
                    self.quiet_filter.filter(
                        _access_record(
                            method=method,
                            path="/api/v1/status/job-id",
                        )
                    )
                )
        self.assertTrue(
            self.quiet_filter.filter(_access_record(path="/api/v1/models"))
        )

    def test_share_capability_and_security_routes_remain_visible(self):
        paths = (
            "/share/bearer-token",
            "/api/v1/output-shares/bearer-token/media",
            "/api/v1/jobs/job-id/delivery-recovery",
            "/api/v1/access-context",
            "/api/v1/workspaces",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(self.quiet_filter.filter(_access_record(path=path)))

    def test_unexpected_log_record_shapes_are_preserved(self):
        unexpected_args = (
            (),
            ("client", "GET", "/health", "1.1"),
            ("client", "GET", "/health", "1.1", 200, "extra"),
            ["client", "GET", "/health", "1.1", 200],
            ("client", None, "/health", "1.1", 200),
            ("client", "GET", None, "1.1", 200),
            ("client", "GET", "/health", "1.1", "200"),
            ("client", "GET", "/health", "1.1", True),
        )
        for args in unexpected_args:
            with self.subTest(args=args):
                self.assertTrue(
                    self.quiet_filter.filter(_access_record(args=args))
                )

    def test_installer_is_idempotent(self):
        logger_name = "maestro.tests.quiet-access"
        logger = logging.getLogger(logger_name)
        original_filters = list(logger.filters)
        try:
            logger.filters.clear()
            first = install_quiet_access_filter(logger_name)
            second = install_quiet_access_filter(logger_name)
            self.assertIs(first, second)
            self.assertEqual(
                sum(
                    isinstance(item, QuietPollingAccessFilter)
                    for item in logger.filters
                ),
                1,
            )
        finally:
            logger.filters[:] = original_filters


if __name__ == "__main__":
    unittest.main()
