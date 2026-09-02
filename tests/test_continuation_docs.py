"""Durable contract checks for the Maestro continuation guide."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = ROOT / "docs/operations/CONTINUATION.md"
GUIDE = GUIDE_PATH.read_text(encoding="utf-8")
HANDOFF_PATH = ROOT / "docs/operations/FRESH_THREAD_HANDOFF.md"
HANDOFF = HANDOFF_PATH.read_text(encoding="utf-8")
SAMPLE_QUEUE_PATH = ROOT / "docs/operations/SAMPLE_CAMPAIGN_QUEUE.md"
SAMPLE_QUEUE = SAMPLE_QUEUE_PATH.read_text(encoding="utf-8")


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

        launch = self.guide_section("Start Maestro Continuum")
        for required in (
            "start.js",
            "python wgp.py",
            "pterm search",
            "pterm run",
            "app/env-rtx50",
            "preserved `app/env`",
            "SERVER_PORT",
            "{{port}}",
            "Caddy",
            "MAESTRO_STRICT_SERVER_PORT=true",
            "/health",
            "/ready",
        ):
            with self.subTest(launch_contract=required):
                self.assertIn(required, launch)

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

        credits = self.guide_section("Optional hosted credit scheduling")
        self.assertIn("MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED=true", credits)
        self.assertIn("MAESTRO_COMPUTE_EXECUTION_REALM=hosted", credits)
        self.assertIn("safe tracked defaults", credits)
        self.assertIn("sealed account store", credits)
        self.assertIn("Otherwise-valid zero/partial/refunded/expired-credit", credits)
        self.assertIn("flat `402`", credits)
        self.assertIn("cross-band starvation bound", credits)

        samples = self.guide_section("GPU-idle comparative sample campaign")
        for required in (
            "Maestro.git-134",
            "Maestro.git-28",
            "Reference Lock",
            "Pocket to Picture Lock",
            "same normalized prompt/input",
            "ordinary durable generation job",
            "`queue_class=background_sample`",
            "request cancellation only through Maestro",
            "in-flight loss",
            "`not_before` time",
            "bounded exponential backoff with jitter",
            "low-frequency watcher",
            "five qualifying snapshots over at least 8",
            "1 percent incidental floor",
            "min(1 GiB, 10 percent of total GPU memory)",
            "no greater than 25 percent",
            "min(4 GiB, 15 percent of total GPU memory)",
            "WDDM unknown graphics bytes",
            "overlap counts as compute",
            "fail closed and reset the window",
            "PID/name-redaction contract",
            "30-second base",
            "1800-second cap",
            "zero-to-25-percent",
            "15-second minimum poll",
            "reset-after-durable-arm-commit",
            "2–5 sequential, non-adjacent, nearby frames",
            "normalized sampling positions",
            "human review",
            "control",
            "atomic two-arm held",
            "guarded one-arm release",
            "durable sample-specific preemption/retry",
            "owner/project-authorized read-only paired queue projection",
            "local, recently",
            "reauthenticated owner",
            "no live authenticated browser/NVML/model acceptance",
            "no VLM execution",
            "no durable receipt/CAS store",
            "no human review decision mutations or human-review UI",
            "existing recent-password-reauthentication gate",
            "historical SQLite tracker mutation",
        ):
            with self.subTest(sample_campaign_contract=required):
                self.assertIn(required, " ".join(samples.split()))

        for required in (
            "stride = max(2, (last_index + 10) // 20)",
            "start = (last_index - span) // 2",
            "half-up integer rounding",
            "one quarter of normalized duration",
            "two frames after",
            "`queue_class=background_sample`",
            "min(1800, 30 * 2 ** min(attempt, 6))",
            "zero through 25 percent",
            "compute the delay with the current",
            "persist `attempt + 1` with `not_before`",
            "first failure therefore waits 30 seconds",
            "Reset the attempt only after",
            "15 seconds and must never release",
            "process-name, executable, or command-line",
            "`nvmlDeviceGetProcessUtilization`",
            "`lastSeenTimeStamp=0`",
            "wall-clock-derived NVML cursor is forbidden",
            "3 seconds old",
            "explicit 1 percent incidental floor",
            "`min(1 GiB, 10 percent of total GPU memory)`",
            "`min(4 GiB, 15 percent of total GPU memory)`",
            "valid conservative residual",
            "five qualifying snapshots spanning at least 8 seconds",
            "positive adjacent gap at most 3 seconds",
            "No PID, process name, raw memory",
            "Launch-side atomic pair submission",
            "Guarded one-arm release",
            "preemption/retry paths are",
            "implemented and model-free verified",
            "owner/project-authorized, read-only paired",
            "no live authenticated browser/NVML/model acceptance",
            "no VLM execution",
            "no durable receipt/CAS store",
            "no human review decision mutations or human-review UI",
            "outputs_unbound",
            "recent-password-reauthentication gate",
            "preserved historical SQLite tracker",
        ):
            with self.subTest(sample_queue_contract=required):
                self.assertIn(required, " ".join(SAMPLE_QUEUE.split()))

        wave_one = re.search(
            r"(?ms)^## Wave 1 .*?^## Wave 2 ", SAMPLE_QUEUE,
        )
        self.assertIsNotNone(wave_one)
        self.assertNotIn("blocked on launch coordinator", wave_one.group())
        self.assertIn("launch substrate verified model-free", wave_one.group())
        self.assertIn("preemption/retry verified model-free", wave_one.group())

        implementation = re.search(
            r"(?ms)^## Current implementation evidence\s*$\n(?P<body>.*)\Z",
            SAMPLE_QUEUE,
        )
        self.assertIsNotNone(implementation)
        implementation_body = implementation.group("body")
        normalized_implementation = " ".join(implementation_body.split())
        for required in (
            "Launch-side atomic pair submission",
            "Guarded one-arm release",
            "implemented and model-free verified",
            "local, recently reauthenticated owner",
            "owner/project-authorized",
            "read-only paired queue projection",
            "no durable receipt/CAS store",
        ):
            with self.subTest(sample_implementation_contract=required):
                self.assertIn(required, normalized_implementation)
        for obsolete in (
            "blocked on launch coordinator",
            "Launch-side pair submission, allocator-gated release",
            "sample-specific recovery dispatch, VLM execution",
        ):
            with self.subTest(obsolete_sample_claim=obsolete):
                self.assertNotIn(obsolete, SAMPLE_QUEUE)

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
            "Active owner, projects, and hosted-credit milestone",
            "Prior thread reference",
            "Primary fresh-thread prompt",
            "Post-owner prompt",
            "Hosted credit prompt",
            "Deferred SSO prompt",
            "historical SQLite",
            "explicitly transferred",
            "zero quarantine",
            "do not reintroduce flat 402",
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
