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

    def test_unrepresentable_multiline_dialogue_is_rejected_not_flattened(self):
        literal = '<d>[English] Keep\nthis line.</d>'
        with self.assertRaises(ValueError):
            self.namespace['_director_h3_canonical_prompt'](
                f'A person speaks {literal}', duration_seconds=5, mode='t2va')


if __name__ == '__main__':
    unittest.main()
