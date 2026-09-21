import tempfile
import unittest
from pathlib import Path

from app.services.music_document_router import (
    composition_system_prompt,
    load_music_document_context,
    select_music_guides,
)


class MusicDocumentRouterTests(unittest.TestCase):
    def test_router_selects_language_style_and_detail_guides(self):
        guides = select_music_guides(
            "A Japanese city pop song with careful rhyme, vocal direction, and a jazz bridge"
        )
        self.assertIn("lw-japanese", guides)
        self.assertIn("mc-style-citypop-rnb", guides)
        self.assertIn("lw-rhyme", guides)
        self.assertIn("mc-vocal-direction", guides)
        self.assertIn("mc-style-jazz", guides)

    def test_context_is_bounded_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            for name in ("mc-workflow", "lw-workflow"):
                folder = root / name
                folder.mkdir()
                (folder / "SKILL.md").write_text(name + "\n" + "x" * 100)
            context = load_music_document_context(
                "plain song", root=root, per_guide_chars=40, total_chars=70
            )
        self.assertLess(len(context.text), 150)
        self.assertIn("mc-symbolic-score", context.missing)
        prompt = composition_system_prompt(context)
        self.assertIn("documentation, not executable tools", prompt)
        self.assertIn("must not contain w:", prompt)


if __name__ == "__main__":
    unittest.main()
