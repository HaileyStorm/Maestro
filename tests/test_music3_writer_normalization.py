import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"


def _load_helpers():
    source = LAUNCH.read_text(encoding="utf-8")
    module = ast.parse(source)
    keep = {
        "_parse_song_output",
        "_normalize_written_song_for_model",
    }
    selected = [
        node for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in keep
    ]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(LAUNCH), "exec"), namespace)
    return source, namespace


class Music3WriterNormalizationTests(unittest.TestCase):
    def test_generated_music3_song_uses_the_canonical_structure_helper(self):
        _, helpers = _load_helpers()
        style, lyrics = helpers["_parse_song_output"](
            "[STYLE]\nDream pop\n[LYRICS]\n[Chorus - huge drums] Sing it\n(whispered)",
            False,
        )
        style, lyrics = helpers["_normalize_written_song_for_model"](
            "minimax_music3", style, lyrics,
        )
        self.assertIn("### Arrangement", style)
        self.assertIn("Chorus: huge drums", style)
        self.assertNotIn("Chorus: whispered", style)
        self.assertEqual("[Chorus]\nSing it\n(whispered)", lyrics)

    def test_generated_music3_normalization_preserves_sung_parentheticals(self):
        _, helpers = _load_helpers()
        for parenthetical in ("(I whisper your name)", "(choir)", "(softly)"):
            with self.subTest(parenthetical=parenthetical):
                style, lyrics = helpers["_normalize_written_song_for_model"](
                    "minimax_music3", "Dream pop", f"[Verse]\n{parenthetical}",
                )
                self.assertEqual("Dream pop", style)
                self.assertEqual(f"[Verse]\n{parenthetical}", lyrics)

    def test_other_music_models_preserve_the_existing_writer_result(self):
        _, helpers = _load_helpers()
        original = ("Dream pop", "[Chorus - huge drums] Sing it")
        self.assertEqual(
            original,
            helpers["_normalize_written_song_for_model"]("ace_step_v1_5_xl_sft_lm_4b", *original),
        )

    def test_both_writer_callers_route_generated_music3_output_through_helper(self):
        source, _ = _load_helpers()
        self.assertEqual(source.count("_normalize_written_song_for_model("), 3)
        self.assertIn('body.get("model_type"), style, lyrics', source)
        self.assertIn("model_type, w_style, w_lyrics", source)


if __name__ == "__main__":
    unittest.main()
