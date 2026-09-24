import json
import tempfile
import unittest
from pathlib import Path

from app.services.music_document_router import (
    compose_yue2_with_density_revision,
    composition_system_prompt,
    english_lyric_note_counts,
    load_music_document_context,
    select_music_guides,
    yue2_density_revision_feedback,
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

        instrumental = select_music_guides(
            "An instrumental Japanese city pop piece with a vocal-like lead, jazz harmony",
            language="Japanese", instrumental=True,
        )
        self.assertIn("mc-arrangement-arch", instrumental)
        self.assertIn("mc-melody", instrumental)
        self.assertIn("mc-style-citypop-rnb", instrumental)
        self.assertNotIn("lw-workflow", instrumental)
        self.assertNotIn("lw-japanese", instrumental)
        self.assertNotIn("mc-vocal-direction", instrumental)

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

        instrumental_prompt = composition_system_prompt(context, instrumental=True)
        self.assertIn("JSON lyrics field to exactly [Instrumental]", instrumental_prompt)
        self.assertIn("it does not request a singer", instrumental_prompt)
        self.assertIn("V: Vocal clef=treble", instrumental_prompt)
        self.assertNotIn("one sung syllable per Vocal note", instrumental_prompt)
        self.assertNotIn("final chorus or outro develop the story", instrumental_prompt)

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

    def test_gross_english_density_requests_revision(self):
        abc = (
            'V: Vocal clef=treble name="Vocal Melody"\n'
            '"Am" C8D8E8G8| % four sung notes\n'
            'V: Ins clef=treble name="Ins Melody"\n'
            'C8 E8 G8 E8|\n'
        )
        lyrics = '[Verse]\nCold brass key in my hand as I walk through the night'
        self.assertEqual(
            english_lyric_note_counts(lyrics, abc, language='English'), (12, 4),
        )
        feedback = yue2_density_revision_feedback(
            lyrics, abc, language='English',
        )
        self.assertIn('12 English lyric words but only 4 Vocal notes', feedback)
        self.assertIn('at most 3 short words', feedback)
        self.assertIn('no more than 3 lyric words in total', feedback)
        self.assertIsNone(yue2_density_revision_feedback(
            lyrics, abc, language='Japanese',
        ))
        self.assertIsNone(yue2_density_revision_feedback(
            lyrics, abc, language='English', instrumental=True,
        ))
        self.assertIsNone(yue2_density_revision_feedback(
            '[Verse]\nCold brass key', abc, language='English',
        ))
        decorated = abc.replace('C8D8E8G8|', '!fermata![K:C]C8D8E8G8|')
        self.assertEqual(
            english_lyric_note_counts(lyrics, decorated, language='English'),
            (12, 4),
        )
        self.assertIsNone(english_lyric_note_counts(
            lyrics, abc.replace('C8D8E8G8|', '[CEG]8|'), language='English',
        ))


class Yue2DensityRevisionTests(unittest.IsolatedAsyncioTestCase):
    ABC_FOUR = (
        'V: Vocal\nC8D8E8G8|\nV: Ins\nC8E8G8E8|\n'
    )
    ABC_EIGHT = (
        'V: Vocal\nC8D8E8G8|C8D8E8G8|\n'
        'V: Ins\nC8E8G8E8|C8E8G8E8|\n'
    )
    LONG = '[Verse]\nCold brass key in my hand as I walk through the night'

    async def compose(self, first, revised=None, *, fails=False):
        calls = []

        async def generate(prompt):
            calls.append(prompt)
            if len(calls) == 2 and fails:
                raise RuntimeError('local model unavailable')
            return json.dumps(first if len(calls) == 1 else revised)

        result = await compose_yue2_with_density_revision(
            'original song brief', language='English', instrumental=False,
            generate=generate, parse=json.loads,
        )
        return result, calls

    async def test_sufficient_note_slots_make_one_model_call(self):
        first = {'style': 'warm', 'lyrics': 'cold brass key', 'abc': self.ABC_FOUR}
        result, calls = await self.compose(first)
        self.assertEqual(result, first)
        self.assertEqual(len(calls), 1)

    async def test_gross_density_gets_one_revision_and_accepts_fit(self):
        first = {'style': 'warm', 'lyrics': self.LONG, 'abc': self.ABC_FOUR}
        revised = {'style': 'warm', 'lyrics': 'cold brass key', 'abc': self.ABC_FOUR}
        result, calls = await self.compose(first, revised)
        self.assertEqual(result, revised)
        self.assertEqual(len(calls), 2)
        self.assertIn('12 English lyric words but only 4 Vocal notes', calls[1])

    async def test_partial_revision_still_overfull_keeps_first_draft(self):
        first = {'style': 'warm', 'lyrics': self.LONG, 'abc': self.ABC_FOUR}
        revised = {'style': 'warm', 'lyrics': self.LONG, 'abc': self.ABC_EIGHT}
        result, calls = await self.compose(first, revised)
        self.assertEqual(result, first)
        self.assertEqual(len(calls), 2)

    async def test_failed_revision_keeps_first_draft(self):
        first = {'style': 'warm', 'lyrics': self.LONG, 'abc': self.ABC_FOUR}
        with self.assertLogs('app.services.music_document_router', level='WARNING'):
            result, calls = await self.compose(first, fails=True)
        self.assertEqual(result, first)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
