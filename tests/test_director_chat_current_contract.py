"""Source-level guards for Director Chat's current voice-reference contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DIRECTOR_CHAT = (
    ROOT / "ui/src/components/Sidebar/DirectorChat.tsx"
).read_text(encoding="utf-8")


class TestDirectorChatCurrentContract(unittest.TestCase):
    def test_voice_reference_requires_model_support_and_service_opt_in(self):
        section = DIRECTOR_CHAT[
            DIRECTOR_CHAT.index("function AdditionalRefsSection()"):
            DIRECTOR_CHAT.index("function AnalysisSummary(")
        ]

        self.assertIn(
            "selectedVideoDefinition?.director?.supports_voice_reference === true",
            section,
        )
        self.assertIn("&& voiceReferenceEnabled", section)
        self.assertIn("voiceReferenceMode === 'id_lora'", section)
        self.assertNotIn("native_reference", section)


if __name__ == "__main__":
    unittest.main()
