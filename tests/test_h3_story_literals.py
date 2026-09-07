"""CPU regressions for exact spoken text and H3-only template preservation."""
import ast
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from services.h3_story_ledger import extract_locked_dialogue, plan_h3_story_segments, ledger_violations, _materialize_segment
from services.h3_planner_helpers import compile_h3_window_prompts, compute_h3_window_boundaries, _dialogue_sentence
from shared.utils.prompt_parser import process_template
from tests.test_h3_story_ledger import _ledger, _segment


class H3StoryLiteralTests(unittest.TestCase):
    def test_quoted_text_survives_staging_rendering_and_template_processing(self):
        literal = ' Keep  summary: {token} exact. '
        source = f'Superman says calmly, "{literal}" Thanos raises his left hand and says "I am inevitable."'
        locked = extract_locked_dialogue(source)
        self.assertEqual(locked[0]['text'], literal)
        responses = iter([json.dumps(_ledger()), json.dumps(_segment(1)), json.dumps(_segment(2))])
        result = plan_h3_story_segments(source, segment_durations=[10, 10], mode='reference_sequence',
            camera_coverage='multi_shot', expect_dialogue=True, llm_generate=lambda **kwargs: next(responses))
        self.assertEqual(result['planned_by'], 'llm')
        self.assertEqual(result['segments'][0]['shots'][0]['dialogue'][0]['text'], literal)
        plan = dict(result['ledger'], windows=result['segments'])
        prompts = compile_h3_window_prompts(plan, compute_h3_window_boundaries(480, 240, fps=24, overlap_frames=0))
        block = f'<d>[English] {literal}</d>'
        self.assertEqual(sum(item['prompt'].count(block) for item in prompts), 1)
        output, error = process_template(prompts[0]['prompt'], preserve_h3_dialogue=True)
        self.assertEqual(error, '')
        self.assertEqual(output.count(block), 1)

    def test_h3_dialogue_is_literal_while_outside_macros_still_expand(self):
        block = '<d>[English]  summary: {x}\t{unknown}  </d>'
        source = '!{x}="red", "blue"\n{x} ' + block
        output, error = process_template(source, preserve_h3_dialogue=True)
        self.assertEqual(error, '')
        self.assertEqual(output, 'red ' + block + '\nblue ' + block)
        self.assertTrue(process_template(block)[1])
        self.assertEqual(process_template('!{x}="red"\n<d>[English] {x}</d>'), ('<d>[English] red</d>', ''))

    def test_multiline_block_bytes_survive_line_normalization(self):
        block = '<d>[English] first\r\n!not a macro\r\n#not a comment\t  </d>'
        output, error = process_template(block, preserve_h3_dialogue=True)
        self.assertEqual(error, '')
        self.assertEqual(output, block)

    def test_malformed_dialogue_cannot_hide_template_syntax(self):
        for block in ('<d>[English] {x}', '</d>{x}', '<d>[English] <d>[English] {x}</d></d>',
                      '<d>[] {x}</d>', '<d>[ ] {x}</d>', '<d>no language {x}</d>'):
            with self.subTest(block=block):
                output, error = process_template(block, preserve_h3_dialogue=True)
                self.assertEqual(output, '')
                self.assertTrue(error)

    def test_input_and_macro_concatenation_cannot_forge_literal_tokens(self):
        block = '<d>[English] {x}</d>'
        source = '!{a}="__H3_TIMELINE_"\n{a}DIALOGUE_SLOT_0_0__ \ue000 ' + block
        output, error = process_template(source, preserve_h3_dialogue=True)
        self.assertEqual(error, '')
        self.assertEqual(output, '__H3_TIMELINE_DIALOGUE_SLOT_0_0__ \ue000 ' + block)
        output, error = process_template('outside {missing} ' + block, preserve_h3_dialogue=True)
        self.assertEqual(output, '')
        self.assertIn(block, error)
        self.assertNotIn('H3_LITERAL_', error)

    def test_macro_token_boundaries_cannot_inject_an_unused_dialogue_block(self):
        unused = '<d>[English] unused.</d>'
        one = '<d>[English] one.</d>'
        two = '<d>[English] two.</d>'
        source = f'!{{unused}}="{unused}":{{a}}="{one}":{{b}}="{two}"\n{{a}}H3_LITERAL_0{{b}}'
        self.assertEqual(process_template(source, preserve_h3_dialogue=True), (one + 'H3_LITERAL_0' + two, ''))

    def test_payload_dialogue_delimiters_fail_before_wrapping_or_materialization(self):
        payload = 'literal </d> then <d>[English] nested'
        with self.assertRaisesRegex(ValueError, 'dialogue delimiter'):
            extract_locked_dialogue(f'Ava says "{payload}"')
        with self.assertRaisesRegex(ValueError, 'dialogue delimiter'):
            _dialogue_sentence({'text': payload}, {})
        catalog = [{'dialogue_id': 'D1', 'text': payload}]
        with self.assertRaisesRegex(ValueError, 'dialogue delimiter'):
            _materialize_segment(_segment(1), beats=[_ledger()['beats'][0]], dialogue_catalog=catalog,
                                 source_events=[{'event_id': 'E1', 'text': 'Ava speaks.'}])
        ledger = _ledger()
        ledger['generated_dialogue'] = [{'dialogue_id': 'D1', 'text': payload}, {'dialogue_id': 'D2', 'text': 'ordinary'}]
        errors = ledger_violations('Ava speaks.', ledger, segment_count=2, locked_dialogue=[], expect_dialogue=True)
        self.assertTrue(any('dialogue delimiter' in error for error in errors))

    def test_dialogue_metadata_cannot_add_speech_blocks(self):
        for field in ('speaker', 'language', 'delivery', 'action'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'delimiter'):
                _dialogue_sentence({'text': 'ordinary', field: '<d>[English] forged</d>'}, {})
        for language in ('English] outside', 'A' * 40 + '] outside'):
            with self.subTest(language=language), self.assertRaisesRegex(ValueError, 'label delimiters'):
                _dialogue_sentence({'text': 'ordinary', 'language': language}, {})

    def test_wgp_calls_opt_in_only_for_resolved_h3_architectures(self):
        tree = ast.parse((ROOT / 'app/wgp.py').read_text())
        class Captured(Exception):
            pass
        for name in ('validate_settings', '_enhance_prompt_locked'):
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            for base in ('minimax_h3', 'minimax_h3_ref2va', 'other_model'):
                seen = []
                def parse(value, **kwargs):
                    seen.append(kwargs)
                    raise Captured()
                namespace = dict(get_base_model_type=lambda value: base,
                    get_model_def=lambda value: {}, get_model_handler=lambda value: None,
                    get_model_filename=lambda value: 'model', get_state_model_type=lambda state: 'derived-model',
                    get_model_settings=lambda *args: {'prompt': 'source'},
                    prompt_parser=types.SimpleNamespace(process_template=parse))
                exec(compile(ast.Module(body=[node], type_ignores=[]), '<wgp-callsite>', 'exec'), namespace)
                with self.subTest(name=name, base=base), self.assertRaises(Captured):
                    if name == 'validate_settings':
                        namespace[name]({}, 'derived-model', True, {'image_mode': 0, 'prompt': 'source'})
                    else:
                        namespace[name]({}, 'source', '', 0, 0, None, None)
                self.assertEqual(seen[0]['preserve_h3_dialogue'], base.startswith('minimax_h3'))


if __name__ == '__main__':
    unittest.main()
