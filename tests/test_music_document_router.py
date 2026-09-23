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
        self.assertIn('V: Vocal clef=treble name="Vocal Melody" snm="Vocal"', prompt)
        self.assertIn('V: Ins clef=treble name="Ins Melody" snm="Inst."', prompt)
        self.assertIn("The body must alternate V: Vocal and V: Ins", prompt)
        self.assertIn("one shared song plan", prompt)
        self.assertIn("one sung syllable per Vocal note", prompt)
        self.assertIn("Add enough bars or reduce words", prompt)

    def test_long_core_guides_cannot_starve_requested_language_and_style(self):
        brief = 'Japanese city pop with jazz harmony'
        guides = select_music_guides(brief)
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            for name in guides:
                folder = root / name
                folder.mkdir()
                (folder / 'SKILL.md').write_text(f'Guidance for {name}\n' + 'x' * 6000)
            context = load_music_document_context(brief, root=root)
        self.assertEqual(context.selected, guides)
        for name in ('lw-japanese', 'mc-style-citypop-rnb', 'mc-style-jazz', 'mc-harmony'):
            self.assertIn(f'Guidance for {name}', context.text)
        self.assertLessEqual(len(context.text), 28000)

    def test_tiny_budget_reports_only_included_guides_and_all_missing(self):
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            folder = root / 'mc-workflow'
            folder.mkdir()
            (folder / 'SKILL.md').write_text('Available guide')
            context = load_music_document_context('Japanese', root=root, total_chars=4)
        self.assertEqual(context.selected, ())
        self.assertEqual(context.text, '')
        self.assertIn('lw-japanese', context.missing)


if __name__ == "__main__":
    unittest.main()
