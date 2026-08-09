"""Static source contracts for the concise research settings card."""

from pathlib import Path
import unittest


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
            "Queued",
            "eligible",
            "Next cycle",
            "Last cycle",
            "Last implementation",
            "Active:",
            "Run research now",
            "Start implementation",
        ):
            self.assertIn(copy, card)
        self.assertIn(".slice(0, 3)", card)
        self.assertIn("status?.research_phase", card)
        self.assertIn("status.last_implementation_run.started_at", card)
        self.assertIn("status.last_implementation_run.completed_at", card)
        self.assertIn("formatResearchTime(", card)
        self.assertNotIn("Coming soon", card)
        self.assertNotIn("planned", card.lower())

    def test_disclosure_is_persistent_and_names_every_excluded_data_class(self):
        card = PANEL[PANEL.index("function ResearchCard()"):]
        self.assertIn("DeepSeek through Nous", card)
        self.assertIn("mechanical gate fails or its circuit opens", card)
        self.assertIn("GPT-5.6 Luna is the only fallback", card)
        for excluded in ("Project names", "prompts", "jobs", "media", "logs"):
            self.assertIn(excluded, card)

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
