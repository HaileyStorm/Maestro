"""Adaptive estimate and generation request checkpoint parity, CPU only."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))


class RequestError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class AdaptiveRequestTests(unittest.TestCase):
    def setUp(self):
        names = {'_h3_preferred_fl2va_model', '_apply_h3_adaptive_checkpoint',
                 '_h3_estimate_context', 'h3_estimate'}
        nodes = [n for n in ast.parse((APP / 'launch.py').read_text()).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        for n in nodes:
            n.decorator_list = []
        self.fl = {'minimax_h3', 'minimax_h3_pinkcherry_fl2va', 'minimax_h3_w4a8_fl2va'}
        self.ns = dict(_H3_FL2VA_MODELS=self.fl,
                       _H3_LONG_STUDIO_MODELS=self.fl | {'minimax_h3_ref2va'},
                       _H3_BASE_FL2VA_MODEL='minimax_h3', _H3_REF2VA_MODEL='minimax_h3_ref2va',
                       _H3_ESTIMATE_PROMPT_FIELD='prompt', Request=object, HTTPException=RequestError,
                       wgp=types.SimpleNamespace(get_model_def=lambda _: {'fps': 24, 'frames_maximum': 345}))
        for name in ['_require_remote_visible_models', '_require_h3_legal_execution',
                     '_validate_h3_lora_request', '_validate_h3_turbo_estimate_context',
                     '_validate_h3_spectrum_estimate_context', '_validate_h3_lightx2v_estimate_context']:
            self.ns[name] = Mock()
        self.ns['_h3_profile_estimate_payload'] = lambda context, **kwargs: context
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'adaptive-request', 'exec'), self.ns)

    def test_picker_wins_and_output_metadata_cannot_select_flavor(self):
        resolve = self.ns['_h3_preferred_fl2va_model']
        for chosen in self.fl:
            self.assertEqual(resolve({'model_type': 'minimax_h3_ref2va',
                                      'h3_adaptive_fl2va_model': chosen}), chosen)
            self.assertEqual(resolve({'model_type': chosen, 'explicit_output': True}), chosen)
        self.assertEqual(resolve({'model_type': 'minimax_h3_ref2va', 'explicit_output': True}), 'minimax_h3')

    def test_adaptive_routing_retains_explicit_flavor_after_reference_route(self):
        body = {'model_type': 'minimax_h3', 'h3_adaptive_fl2va_model': 'minimax_h3_w4a8_fl2va',
                'image_refs': ['reference'], 'video_length': 124}
        self.assertEqual(self.ns['_apply_h3_adaptive_checkpoint'](body), 'minimax_h3_ref2va')
        self.assertEqual(self.ns['_h3_preferred_fl2va_model'](body), 'minimax_h3_w4a8_fl2va')

    def test_pinned_selection_ignores_inactive_adaptive_picker(self):
        body = {'model_type': 'minimax_h3', 'h3_adaptive_conditioning': False,
                'h3_adaptive_fl2va_model': 'minimax_h3_w4a8_fl2va'}
        self.assertEqual(self.ns['_apply_h3_adaptive_checkpoint'](body), 'minimax_h3')

    def call_endpoint(self, body, *, remote=False):
        async def payload():
            return body
        request = types.SimpleNamespace(json=payload, state=types.SimpleNamespace(maestro_remote=remote))
        return asyncio.run(self.ns['h3_estimate'](request))

    def test_estimate_accepts_choices_preserves_empty_lists_and_checks_access(self):
        body = {'model_type': 'minimax_h3', 'h3_adaptive_fl2va_model': 'minimax_h3_w4a8_fl2va',
                'h3_adaptive_ref2va_model': 'minimax_h3_ref2va',
                'activated_loras': ['legacy.safetensors'], 'h3_fl2va_loras': [],
                'h3_ref2va_loras': ['ref.safetensors'], 'h3_ref2va_loras_multipliers': '0.25'}
        result = self.call_endpoint(body)
        self.assertEqual(result['model_type'], 'minimax_h3_w4a8_fl2va')
        self.assertEqual(result['activated_loras'], [])
        self.assertEqual(result['h3_ref2va_loras'], ['ref.safetensors'])
        checked = self.ns['_require_h3_legal_execution'].call_args.args[0]
        self.assertEqual(set(checked), {'minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va'})
        self.assertEqual(set(self.ns['_require_remote_visible_models'].call_args.args[1]), set(checked))
        self.assertEqual(body['model_type'], 'minimax_h3')

    def test_invalid_picker_rejected_even_when_semantic_routing_would_hide_it(self):
        for key in ['h3_adaptive_fl2va_model', 'h3_adaptive_ref2va_model']:
            with self.subTest(key=key), self.assertRaises(RequestError) as caught:
                self.call_endpoint({'model_type': 'minimax_h3', key: 'unavailable',
                                    'reference_shape': {'image_count': 1}})
            self.assertEqual(caught.exception.status_code, 400)

    def test_remote_hidden_and_unknown_choices_are_gated_before_validation(self):
        self.ns['_h3_estimate_context'] = Mock(side_effect=AssertionError('routed before visibility'))
        for model in ('hidden-checkpoint', 'unknown-checkpoint'):
            with self.subTest(model=model):
                def visibility(request, models):
                    self.assertTrue(request.state.maestro_remote)
                    self.assertIn(model, models)
                    raise RequestError(404, 'Model not found')
                self.ns['_require_remote_visible_models'] = visibility
                with self.assertRaises(RequestError) as caught:
                    self.call_endpoint({'model_type': 'minimax_h3',
                                        'h3_adaptive_fl2va_model': model,
                                        'custom_settings': 'malformed'}, remote=True)
                self.assertEqual((caught.exception.status_code, caught.exception.detail),
                                 (404, 'Model not found'))
        self.ns['_h3_estimate_context'].assert_not_called()

    def test_denied_adaptive_checkpoint_stops_estimate(self):
        def gate(models):
            if 'minimax_h3_w4a8_fl2va' in models:
                raise RequestError(403, 'Unavailable')
        self.ns['_require_h3_legal_execution'] = gate
        with self.assertRaises(RequestError) as caught:
            self.call_endpoint({'model_type': 'minimax_h3',
                                'h3_adaptive_fl2va_model': 'minimax_h3_w4a8_fl2va'})
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
