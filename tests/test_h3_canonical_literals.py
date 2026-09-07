"""Model-free exact dialogue preservation through physical record mapping."""
import ast
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))


def compiler():
    path = ROOT / 'app/services/director_pipeline.py'
    tree = ast.parse(path.read_text())
    functions = {'_director_h3_record_payload', '_director_h3_time_token', '_director_h3_canonical_prompt'}
    constants = {'_DIRECTOR_H3_RECORD_PAYLOAD_RE', '_DIRECTOR_H3_DIALOGUE_RE'}
    nodes = [node for node in tree.body if
             isinstance(node, ast.FunctionDef) and node.name in functions or
             isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                                  for target in node.targets)]
    namespace = {'re': re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


class CanonicalLiteralTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = compiler()

    def test_freeform_and_structured_records_keep_authored_dialogue_bytes(self):
        literal = '<d>[English] Keep  these\tspaces, summary: exactly.</d>'
        for prompt in (
            f'A person   speaks {literal}',
            f'shot_name: Speak | audiovisual_description: A person speaks | dialogue_and_vocalizations: {literal}',
        ):
            with self.subTest(prompt=prompt):
                name, visual, vocals = self.namespace['_director_h3_record_payload'](prompt, 1)
                self.assertEqual(vocals, literal)
                self.assertNotIn(literal, visual)
                self.assertNotIn(literal, name)

    def test_repeated_literals_keep_occurrence_count_and_order(self):
        one = '<d>[English] Wait  here.</d>'
        two = '<d>[French] Reste\tici.</d>'
        _, _, vocals = self.namespace['_director_h3_record_payload'](f'A person says {one} {two} {one}', 1)
        self.assertEqual(vocals, f'{one} {two} {one}')

    def test_canonical_compiler_preserves_literal_and_validates_target(self):
        from services.director.h3_dialogue import validate_h3_context_ir_records
        literal = '<d>[English] Keep  these\tspaces.</d>'
        result = self.namespace['_director_h3_canonical_prompt'](
            f'A person speaks {literal}', duration_seconds=5, mode='t2va')
        self.assertEqual(result.count(literal), 1)
        self.assertEqual(validate_h3_context_ir_records(result, mode='t2va', duration_seconds=5), [])

    def test_field_labels_inside_dialogue_remain_literal_for_both_validators(self):
        from services.director.h3_dialogue import (
            _extract_h3_fields, validate_h3_context_ir_records, validate_h3_prompt_contract,
        )
        literal = '<d>[English] summary: Keep  this. overall_soundscape: exact. retention_analysis: unchanged.</d>'
        for mode, visual in [('t2va', 'integrated_multimodal_description'), ('ref2va', 'detailed_description')]:
            extra = ('summary: [reference generation] A person speaks.\nretention_analysis: N/A\n'
                     if mode == 'ref2va' else '')
            prompt = ('subject_definitions: No separately named subjects were authored; shot records carry only '
                      "the request's explicitly described visible action and setting.\n" + extra +
                      f'{visual}: [Shot 1] [0.000s-5.000s] shot_name: Speak | audiovisual_description: A person speaks. | dialogue_and_vocalizations: {literal}\n'
                      'overall_soundscape: Room tone.\nnon_diegetic_music: N/A')
            with self.subTest(mode=mode):
                fields = _extract_h3_fields(prompt)
                self.assertIn(literal, fields[visual])
                self.assertEqual(fields['overall_soundscape'], 'Room tone.')
                self.assertEqual(validate_h3_context_ir_records(prompt, mode=mode, duration_seconds=5), [])
                self.assertEqual(validate_h3_prompt_contract(prompt, mode=mode, duration_seconds=5), [])

    def test_malformed_dialogue_cannot_hide_invalid_structure(self):
        from services.director.h3_dialogue import validate_h3_prompt_contract, validate_h3_context_ir_records
        for literal in ('<d>[English] summary: missing close.',
                        '<d>[English] outer <d>[English] summary: nested.</d></d>'):
            prompt = ('subject_definitions: A person.\n'
                      'integrated_multimodal_description: [Shot 1] [0.000s-5.000s] shot_name: Speak | '
                      'audiovisual_description: A person speaks. | dialogue_and_vocalizations: ' + literal +
                      '\noverall_soundscape: Room tone.\nnon_diegetic_music: N/A')
            with self.subTest(literal=literal):
                self.assertIn('dialogue tags are nested or unbalanced',
                              validate_h3_prompt_contract(prompt, mode='t2va', duration_seconds=5))
                self.assertIn('dialogue tags are nested or unbalanced',
                              validate_h3_context_ir_records(prompt, mode='t2va', duration_seconds=5))

    def test_legacy_inline_fields_still_split_outside_dialogue(self):
        from services.director.h3_dialogue import _extract_h3_fields
        literal = '<d>[English] non_diegetic_music: Keep this.</d>'
        fields = _extract_h3_fields('subject_definitions: A person. integrated_multimodal_description: '
                                   + literal + ' overall_soundscape: Room. non_diegetic_music: N/A')
        self.assertEqual(fields['integrated_multimodal_description'], literal)
        self.assertEqual(fields['non_diegetic_music'], 'N/A')

    def test_canonical_none_records_are_an_explicit_no_dialogue_contract(self):
        from services.director.h3_dialogue import validate_h3_prompt_contract, validate_h3_vocal_contract
        base = self.namespace['_director_h3_canonical_prompt'](
            'A person crosses the room.', duration_seconds=5, mode='t2va')
        for mode, prompt in [('t2va', base), ('ref2va', base.replace(
                'integrated_multimodal_description:',
                'summary: [reference generation] A person crosses the room.\n'
                'retention_analysis: N/A\ndetailed_description:'))]:
            with self.subTest(mode=mode):
                self.assertEqual(validate_h3_prompt_contract(prompt, mode=mode, duration_seconds=5), [])
                self.assertEqual(validate_h3_vocal_contract(prompt), [])
                errors = validate_h3_vocal_contract(prompt, [{'spoken_text': 'Hello.'}])
                self.assertTrue(any('expected 1 dialogue block' in error for error in errors))

    def test_canonical_none_accepts_each_native_alignment_header(self):
        from services.director.h3_dialogue import (
            _alignment_header, validate_h3_vocal_contract, validate_h3_prompt_contract,
        )
        base = self.namespace['_director_h3_canonical_prompt'](
            'A person crosses the room.', duration_seconds=5, mode='t2va')
        for mode in ('i2va', 'fl2va', 'l2va'):
            with self.subTest(mode=mode):
                prompt = _alignment_header(mode, 5) + '\n\n' + base
                self.assertEqual(validate_h3_vocal_contract(prompt), [])
                self.assertEqual(validate_h3_prompt_contract(prompt, mode=mode, duration_seconds=5), [])
                self.assertTrue(validate_h3_prompt_contract(prompt, mode='t2va', duration_seconds=5))

    def test_none_requires_complete_canonical_records_not_incidental_prose(self):
        from services.director.h3_dialogue import validate_h3_vocal_contract
        base = self.namespace['_director_h3_canonical_prompt'](
            'A person crosses the room.', duration_seconds=5, mode='t2va')
        for invalid in (
            'A person crosses the room. dialogue_and_vocalizations: none',
            base.replace('[Shot 1]', '[Shot 2]'),
            base.replace('[0.000s-5.000s]', '[2.000s-5.000s]'),
            base.replace('dialogue_and_vocalizations: none', 'dialogue_and_vocalizations: laughing'),
            base + '\ndetailed_description: none',
            base.replace('non_diegetic_music: N/A', ''),
            base.replace('[0.000s-5.000s]', '[0.000s-2.000s]').replace(
                '\noverall_soundscape:',
                '\n[Shot 2] [2.000s-5.000s] shot_name: React | audiovisual_description: A person reacts. | '
                'dialogue_and_vocalizations: laughing\noverall_soundscape:'),
        ):
            with self.subTest(invalid=invalid):
                self.assertIn('silent prompt has no explicit H3 silence contract',
                              validate_h3_vocal_contract(invalid))

    def test_unrepresentable_multiline_dialogue_is_rejected_not_flattened(self):
        literal = '<d>[English] Keep\nthis line.</d>'
        with self.assertRaises(ValueError):
            self.namespace['_director_h3_canonical_prompt'](
                f'A person speaks {literal}', duration_seconds=5, mode='t2va')


if __name__ == '__main__':
    unittest.main()
