"""CPU contracts for LTX options, routing flags, and conditioning-path authority."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app/launch.py'


def function(tree, name):
    return next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def assignment(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


def evaluate(node, namespace):
    return eval(compile(ast.Expression(node), str(SOURCE), 'eval'), namespace)


class LtxApiRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text())

    def normalize_audio(self, body, enabled=True):
        generate = function(self.tree, 'generate')
        block = next(node for node in generate.body if isinstance(node, ast.If) and 'infer_audio_prompt_from_guide' in ast.unparse(node.test))
        namespace = {'body': body, '_generation_model_def': {'infer_audio_prompt_from_guide': enabled}, 'HTTPException': HTTPException}
        exec(compile(ast.Module(body=[block], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        return body

    def test_standalone_soundtrack_repairs_only_missing_source_flags(self):
        for flags in ('', 'V', 'N', 'L', 'NVL'):
            with self.subTest(flags=flags):
                body = {'audio_guide': 'owned.wav', 'audio_prompt_type': flags}
                self.assertEqual(self.normalize_audio(body)['audio_prompt_type'], 'A' + flags)
                self.assertEqual(self.normalize_audio(body)['audio_prompt_type'], 'A' + flags)
        for flags in ('A', 'AV', 'K', 'KV', '2', '2L'):
            self.assertEqual(self.normalize_audio({'audio_guide': 'owned.wav', 'audio_prompt_type': flags})['audio_prompt_type'], flags)

    def test_control_video_missing_soundtrack_and_other_models_are_unchanged(self):
        for body, enabled in (
            ({'audio_guide': 'owned.wav', 'video_guide': 'control.mp4', 'video_prompt_type': 'TVG', 'audio_prompt_type': ''}, True),
            ({'audio_guide': 'owned.wav', 'image_mode': 1, 'audio_prompt_type': 'NV'}, True),
            ({'audio_prompt_type': 'V'}, True),
            ({'audio_guide': 'owned.wav', 'audio_prompt_type': 'V'}, False),
        ):
            expected = body.copy()
            self.assertEqual(self.normalize_audio(body, enabled), expected)
        self.assertEqual(self.normalize_audio({'audio_guide': 'owned.wav', 'video_guide': 'stale.mp4', 'video_prompt_type': ''})['audio_prompt_type'], 'A')

    def test_malformed_selector_is_not_interpreted_as_source_flags(self):
        for value in (['K'], {'A': True}, 2, [], {}):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                self.normalize_audio({'audio_guide': 'owned.wav', 'audio_prompt_type': value})
            self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.normalize_audio({'audio_guide': 'owned.wav', 'audio_prompt_type': None})['audio_prompt_type'], 'A')

    def test_api_options_forward_declared_vae_choices_and_audio_capability(self):
        result = next(node.value for node in function(self.tree, 'get_model_options').body if isinstance(node, ast.Return))
        wanted = {'infer_audio_prompt_from_guide', 'ltx25_video_vae_choices', 'ltx25_video_vae_default'}
        fields = {key.value: value for key, value in zip(result.keys, result.values) if isinstance(key, ast.Constant) and key.value in wanted}
        self.assertEqual(set(fields), wanted)
        choices = [{'value': 'fast', 'label': 'Fast'}, {'value': 'nad', 'label': 'Quality'}]
        declared = {'infer_audio_prompt_from_guide': True, 'ltx25_video_vae_choices': choices, 'ltx25_video_vae_default': 'nad'}
        self.assertEqual({key: evaluate(value, {'md': declared}) for key, value in fields.items()}, declared)
        self.assertEqual({key: evaluate(value, {'md': {}}) for key, value in fields.items()}, {'infer_audio_prompt_from_guide': False, 'ltx25_video_vae_choices': None, 'ltx25_video_vae_default': 'fast'})

    def authorization(self, resolver):
        namespace = {'Request': object, 'HTTPException': HTTPException, '_resolve_authorized_request_media': resolver}
        nodes = [assignment(self.tree, '_GENERATION_MEDIA_INPUTS'), function(self.tree, '_authorize_generation_media_inputs')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        return namespace['_authorize_generation_media_inputs']

    def test_conditioning_and_soundtrack_are_both_authorized_and_resolved(self):
        calls = []
        request = object()
        def resolve(req, path, project):
            calls.append((req, path, project))
            return '/owned/' + path
        body = {'audio_guide': 'song.wav', 'audio_conditioning_guide': 'vocals.wav'}
        self.authorization(resolve)(request, body, 'project-a')
        self.assertEqual(body, {'audio_guide': '/owned/song.wav', 'audio_conditioning_guide': '/owned/vocals.wav'})
        self.assertCountEqual(calls, [(request, 'song.wav', 'project-a'), (request, 'vocals.wav', 'project-a')])

    def test_conditioning_rejects_denied_and_non_scalar_paths(self):
        with self.assertRaises(HTTPException) as caught:
            self.authorization(lambda *_args: None)(object(), {'audio_conditioning_guide': 'foreign.wav'}, 'project-a')
        self.assertEqual(caught.exception.status_code, 404)
        for value in ([], ['vocals.wav'], {'path': 'vocals.wav'}, 17):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                self.authorization(lambda *_args: self.fail('invalid input reached path resolution'))(object(), {'audio_conditioning_guide': value}, 'project-a')
            self.assertEqual(caught.exception.status_code, 400)

    def test_conditioning_is_recoverable_and_excluded_from_public_campaign_settings(self):
        wgp = ast.parse((ROOT / 'app/wgp.py').read_text())
        attachments = ast.literal_eval(assignment(wgp, 'ATTACHMENT_KEYS').value)
        self.assertIn('audio_conditioning_guide', attachments)
        namespace = {'wgp': SimpleNamespace(ATTACHMENT_KEYS=attachments)}
        nodes = [assignment(self.tree, '_GENERATION_MEDIA_INPUTS'), assignment(self.tree, '_RECOVERABLE_INPUT_KEYS'), assignment(self.tree, '_SAMPLE_CAMPAIGN_NON_SETTINGS_FIELDS')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.assertIn('audio_conditioning_guide', namespace['_RECOVERABLE_INPUT_KEYS'])
        self.assertIn('audio_conditioning_guide', namespace['_SAMPLE_CAMPAIGN_NON_SETTINGS_FIELDS'])


if __name__ == '__main__':
    unittest.main()
