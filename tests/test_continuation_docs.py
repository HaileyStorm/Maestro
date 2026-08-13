"""Durable contract checks for the Maestro continuation guide."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = ROOT / "docs/operations/CONTINUATION.md"
GUIDE = GUIDE_PATH.read_text(encoding="utf-8")
HANDOFF_PATH = ROOT / "docs/operations/FRESH_THREAD_HANDOFF.md"
HANDOFF = HANDOFF_PATH.read_text(encoding="utf-8")


class TestContinuationDocs(unittest.TestCase):
    def guide_section(self, heading: str) -> str:
        match = re.search(
            rf"(?ms)^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
            GUIDE,
        )
        self.assertIsNotNone(match, f"missing continuation-guide section: {heading}")
        return match.group("body")

    def test_guide_covers_the_durable_operator_contract(self):
        self.assertTrue(GUIDE_PATH.is_file())
        self.assertEqual(
            GUIDE_PATH.relative_to(ROOT).as_posix(),
            "docs/operations/CONTINUATION.md",
        )

        startup = self.guide_section("Start at the repository root")
        self.assertIn('REPO_ROOT="$(git rev-parse --show-toplevel)"', startup)
        self.assertIn('cd "$REPO_ROOT"', startup)
        self.assertIn("AGENTS.md", startup)
        self.assertIn("git status --short --branch", startup)

        accounts = self.guide_section("Accounts and existing projects")
        for required in (
            "MAESTRO_ACCOUNTS_ENABLED=true",
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=true",
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=false",
            "recovery codes",
            "GET /api/v1/account/projects/migration",
            "POST /api/v1/account/projects/migration",
            "zero-quarantine",
            "needs_attention",
            "GET /api/v1/account/context",
        ):
            with self.subTest(account_contract=required):
                self.assertIn(required, accounts)

        sso = self.guide_section("Preserve a future SSO migration path")
        for required in (
            "opaque internal `account_id`",
            "provider-neutral OIDC",
            "not a selected dependency or release commitment",
            "`(issuer, subject)`",
            "Never create, merge, or link accounts solely by email",
            "never from provider claims",
            "project permissions from the sealed membership store",
            "local password and recovery codes as a break-glass path",
            "Do not initially treat an OIDC login as recent privileged reauthentication",
            "Contribution events never grant login",
            "fixed algorithm policy",
            "case-folding or normalization",
            "must not re-key projects, credits, jobs, or historical data",
            "bird-in-the-hand account activation milestone",
        ):
            with self.subTest(sso_contract=required):
                self.assertIn(required, sso)

        credits = self.guide_section("Credits remain a separate activation gate")
        self.assertIn(
            "Runtime credit accounting is currently compiled hard-off",
            credits,
        )
        self.assertIn("sealed account store", credits)
        self.assertIn("Otherwise-valid zero/partial/refunded/expired-credit", credits)
        self.assertIn("Keep credit activation off", credits)

        restart = self.guide_section("Coordinated restart and status")
        for required in (
            "Restart Maestro",
            "restart.js",
            "python app/scripts/restart_status.py set --state planned --reason restart",
            (
                "python app/scripts/restart_status.py clear "
                '--generation "$RESTART_GENERATION"'
            ),
            'pterm status "$MAESTRO_REF" --probe --timeout=5000',
            'curl -fsS "${MAESTRO_URL%/}/health"',
            "port is dynamic",
            "ready_url",
            "NOT_CLEARED",
        ):
            with self.subTest(restart_contract=required):
                self.assertIn(required, restart)

        verification = self.guide_section("Verification matrix")
        for required in (
            "Direct local",
            "LAN",
            "Stable/Cloudflare",
            "/health",
            "/api/v1/account/context",
            "/api/v1/workspaces",
        ):
            with self.subTest(verification_contract=required):
                self.assertIn(required, verification)

    def test_guide_contains_no_machine_specific_home_or_private_values(self):
        machine_home = re.compile(
            r"(?i)(?:/home/[a-z0-9._-]+/|/users/[a-z0-9._-]+/|"
            r"[a-z]:\\users\\[a-z0-9._-]+\\)"
        )
        self.assertIsNone(
            machine_home.search(GUIDE),
            "continuation guide must not contain a machine-specific home path",
        )

        private_assignment = re.compile(
            r"(?im)^\s*(?:export\s+)?"
            r"(?:maestro_(?:account_)?(?:signing_secret|store_key)|"
            r"owner_password|password|recovery(?:_|[ -])codes?)"
            r"\s*[:=]\s*[\"']?"
            r"(?!\$|\{|<|\*|redacted\b|changeme\b|example\b)"
            r"\S{8,}"
        )
        self.assertIsNone(
            private_assignment.search(GUIDE),
            "continuation guide must not contain credential or recovery-code values",
        )

    def test_fresh_thread_handoff_covers_checkpoint_and_priority(self):
        self.assertTrue(HANDOFF_PATH.is_file())
        for required in (
            "019fd895-21e8-7f03-86ea-a1296103337e",
            "b528ab7fd467be21c0567f6a619ef1d33208df2b",
            "Bird-in-the-hand next milestone",
            "Prior thread reference",
            "Primary fresh-thread prompt",
            "Post-owner prompt",
            "Deferred credit prompt",
            "Deferred SSO prompt",
            "historical SQLite",
            "explicitly transfers/releases",
            "zero quarantine",
            "Do not reintroduce flat 402",
            "one writer per file",
            "not a hard dependency or final product choice",
            "auto-link by email",
        ):
            with self.subTest(handoff_contract=required):
                self.assertIn(required, HANDOFF)

        self.assertNotRegex(
            HANDOFF,
            r"(?i)(?:/home/[a-z0-9._-]+/|/media/[a-z0-9._-]+/|"
            r"[a-z]:\\users\\[a-z0-9._-]+\\)",
        )

    def test_handoff_shell_blocks_do_not_run_beads_lifecycle(self):
        shell_blocks = re.findall(r"(?ms)```(?:bash|sh)\s*\n(.*?)```", HANDOFF)
        runnable = "\n".join(shell_blocks)
        for forbidden in (
            "bd where",
            "bd init",
            "bd migrate",
            "bd sync",
            "bd hook",
        ):
            with self.subTest(forbidden_command=forbidden):
                self.assertNotIn(forbidden, runnable)


if __name__ == "__main__":
    unittest.main()
