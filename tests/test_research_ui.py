"""Static source contracts for the concise research settings card."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
PANEL = (
    ROOT / "ui/src/components/SettingsDrawer/SystemSettingsPanel.tsx"
).read_text(encoding="utf-8")


class ResearchUiContracts(unittest.TestCase):
    def test_client_uses_no_store_and_keeps_nonce_inside_one_action(self):
        for route in (
            "/api/v1/research/status",
            "/api/v1/research/run",
            "/api/v1/research/implementation/nonce",
            "/api/v1/research/implementation/run",
        ):
            self.assertIn(route, CLIENT)
        research_client = CLIENT[CLIENT.index("// --- Scheduled public research ---"):]
        research_client = research_client[:research_client.index("// --- System Config ---")]
        self.assertGreaterEqual(research_client.count("cache: 'no-store'"), 4)
        self.assertIn("JSON.stringify({ nonce: capability.nonce, force })", research_client)
        self.assertNotIn("finding_ids", research_client)
        self.assertNotIn("prompt:", research_client)
        self.assertNotIn("path:", research_client)

    def test_card_shows_required_state_without_future_feature_copy(self):
        card = PANEL[PANEL.index("function ResearchCard()"):]
        card = card[:card.index("export function SystemSettingsPanel")]
        for copy in (
            "Improvement checks",
            "Potential ideas left to check",
            "Next check",
            "Last check",
            "Last update",
            "Recent ideas",
            "Checking sources for new ideas…",
            "Applying improvements…",
            "Check for ideas now",
            "Starting a code update can change Maestro&apos;s code and settings on this computer.",
            "Start code update",
            "Confirm code update",
        ):
            self.assertIn(copy, card)
        self.assertIn(
            "onClick={() => confirmImplementation ? void startImplementation() : setConfirmImplementation(true)}",
            card,
        )
        self.assertIn("{eligible === 1 ? 'idea' : 'ideas'} ready", card)
        self.assertIn("'group ready'", card)
        self.assertIn("more for a group", card)
        self.assertIn(".slice(0, 3)", card)
        self.assertIn("status?.research_active", card)
        self.assertIn("status?.implementation_active", card)
        self.assertIn("status.last_implementation_run.started_at", card)
        self.assertIn("status.last_implementation_run.completed_at", card)
        self.assertIn("formatResearchTime(", card)
        self.assertNotIn("Coming soon", card)
        self.assertNotIn("planned", card.lower())
        for retired_copy in (
            "eligible ·",
            "Active:",
            "Run research now",
            "Start implementation",
            "Apply ready ideas",
        ):
            self.assertNotIn(retired_copy, card)

    def test_card_presents_plain_language_with_optional_technical_details(self):
        card = PANEL[PANEL.index("function ResearchCard()"):]
        replacements = PANEL[
            PANEL.index("const SUGGESTION_COPY_REPLACEMENTS"):PANEL.index(
                "function presentSuggestionText"
            )
        ]
        extracted_pairs = re.findall(
            r"\[/\\b(.+?)\\b/gi, '([^']+)'\],", replacements
        )
        expected_pairs = (
            ("durable plan", "saved plan"),
            ("durable plans", "saved plans"),
            ("evidence ledger", "review history"),
            ("evidence ledgers", "review histories"),
            ("structural proof", "consistency check"),
            ("implementation[- ]eligible finding", "idea ready for a code update"),
            ("implementation[- ]eligible findings", "ideas ready for a code update"),
            ("agent note", "working note"),
            ("agent notes", "working notes"),
            ("runtime error", "service error"),
            ("runtime errors", "service errors"),
        )
        self.assertEqual(extracted_pairs, list(expected_pairs))

        fixtures = (
            ("durable plan", "saved plan"),
            ("durable plans", "saved plans"),
            ("evidence ledger", "review history"),
            ("evidence ledgers", "review histories"),
            ("structural proof", "consistency check"),
            ("implementation-eligible finding", "idea ready for a code update"),
            ("implementation eligible findings", "ideas ready for a code update"),
            ("agent note", "working note"),
            ("agent notes", "working notes"),
            ("runtime error", "service error"),
            ("runtime errors", "service errors"),
        )
        for original, expected_display in fixtures:
            displayed = original
            for pattern, replacement in extracted_pairs:
                displayed = re.sub(
                    rf"\b{pattern}\b", replacement, displayed, flags=re.IGNORECASE
                )
            technical = original if displayed and displayed != original else None
            self.assertEqual(displayed, expected_display)
            self.assertEqual(technical, original)

        self.assertIn("presentSuggestionText(suggestion.title, 'Improvement idea')", card)
        self.assertIn("technical: presented && presented !== original ? original : null", PANEL)
        self.assertIn("Original technical note", card)
        self.assertIn(
            "const researchError = presentResearchError(pollError || status?.runtime_error)",
            card,
        )
        self.assertIn("Technical details", card)
        self.assertIn("{researchError.technical}", card)
        self.assertNotIn("{pollError || status?.runtime_error}", card)

    def test_disclosure_is_persistent_and_names_every_excluded_data_class(self):
        card = PANEL[PANEL.index("function ResearchCard()"):]
        self.assertIn("presentResearchDisclosure(status?.disclosure)", card)
        self.assertIn("return `Privacy: ${value}`", PANEL)
        self.assertIn("only public model, tool, and LoRA catalog details", PANEL)
        self.assertIn("the only fallback is an isolated GPT-5.6 Luna session", PANEL)
        for excluded in ("Project names", "prompts", "jobs", "media", "logs"):
            self.assertIn(excluded, PANEL)
        self.assertIn("If DeepSeek cannot be used", PANEL)

    def test_polling_is_resilient_and_below_threshold_action_is_enabled(self):
        card = PANEL[PANEL.index("function ResearchCard()"):]
        self.assertIn("useVisibilityPolling(refresh, POLL_INTERVAL_MS.researchVisible)", card)
        self.assertNotIn("window.setInterval", card)
        self.assertIn("sequence !== requestSequence.current", card)
        self.assertIn("catch (error)", card)
        self.assertIn("signal?.aborted || sequence !== requestSequence.current", card)
        self.assertIn("!status.implementation_ready", card)
        self.assertIn("eligible < 1", card)
        self.assertNotIn("eligible < threshold", card)


if __name__ == "__main__":
    unittest.main()
