from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_dasiwa import DASIWA_FILENAME  # noqa: E402
from services.h3_lora_compat import (  # noqa: E402
    h3_lora_block_reason,
    h3_lora_contract,
    h3_request_loras_for_model,
    validate_h3_lora_selection,
)


class H3LoraCompatTests(unittest.TestCase):

    def test_architecture_lists_are_independent(self):
        body = {
            "h3_fl2va_loras": ["minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors"],
            "h3_fl2va_loras_multipliers": "1.00",
            "h3_ref2va_loras": [DASIWA_FILENAME],
            "h3_ref2va_loras_multipliers": "1.00",
            "activated_loras": [
                "minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors",
                DASIWA_FILENAME,
            ],
        }
        fl_names, _weights = h3_request_loras_for_model(body, "minimax_h3")
        ref_names, _ref_weights = h3_request_loras_for_model(
            body, "minimax_h3_ref2va",
        )
        self.assertEqual(
            fl_names,
            ["minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors"],
        )
        self.assertEqual(ref_names, [DASIWA_FILENAME])
        validate_h3_lora_selection(
            model_type="minimax_h3_ref2va",
            planned_model_types=["minimax_h3", "minimax_h3_ref2va"],
            fl2va_loras=body["h3_fl2va_loras"],
            fl2va_loras_multipliers=body["h3_fl2va_loras_multipliers"],
            ref2va_loras=body["h3_ref2va_loras"],
            ref2va_loras_multipliers=body["h3_ref2va_loras_multipliers"],
            num_inference_steps=4,
        )

    def test_exclusive_files_cannot_share_one_picker(self):
        reason = h3_lora_block_reason(
            "ordinary.safetensors",
            architecture="ref2va",
            activated_loras=[DASIWA_FILENAME],
        )
        self.assertIn("cannot be stacked", reason or "")
        self.assertIn(
            "not compatible",
            h3_lora_block_reason(
                DASIWA_FILENAME,
                architecture="fl2va",
            ) or "",
        )

    def test_paths_are_classified_but_asset_identity_and_weights_are_preserved(self):
        turbo = "models/minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors"
        dasiwa = "models\\" + DASIWA_FILENAME
        body = {"activated_loras": [turbo, dasiwa, "styles/look.safetensors"],
                "loras_multipliers": "1.00 0.70 0.20"}
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3"),
                         ([turbo, "styles/look.safetensors"], "1.00 0.20"))
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3_ref2va"),
                         ([dasiwa, "styles/look.safetensors"], "0.70 0.20"))
        self.assertEqual(h3_lora_contract(dasiwa)["architectures"], frozenset({"ref2va"}))
        self.assertEqual(h3_request_loras_for_model({"activated_loras": ["a/look", "b/look"]}, "minimax_h3")[0],
                         ["a/look", "b/look"])

    def test_partial_null_and_empty_split_lists_have_explicit_semantics(self):
        body = {"activated_loras": ["ordinary.safetensors"], "loras_multipliers": "0.25",
                "h3_fl2va_loras": []}
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3"), ([], ""))
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3_ref2va"), (["ordinary.safetensors"], "0.25"))
        body["h3_fl2va_loras"] = None
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3"), (["ordinary.safetensors"], "0.25"))
        body["h3_ref2va_loras"] = []
        self.assertEqual(h3_request_loras_for_model(body, "minimax_h3_ref2va"), ([], ""))
        for malformed in (False, {}, "ordinary.safetensors"):
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                h3_request_loras_for_model(dict(body, h3_fl2va_loras=malformed), "minimax_h3")

    def test_wrong_architecture_in_an_explicit_list_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incompatible"):
            h3_request_loras_for_model({"h3_fl2va_loras": [DASIWA_FILENAME]}, "minimax_h3")
        with self.assertRaisesRegex(ValueError, "planned models"):
            validate_h3_lora_selection(model_type="minimax_h3", activated_loras=[DASIWA_FILENAME])
        validate_h3_lora_selection(model_type="minimax_h3", activated_loras=[DASIWA_FILENAME], fl2va_loras=[])

    def test_empty_items_duplicates_and_invalid_weights_do_not_silently_shift_selection(self):
        for names, weights in ((["first", "", "last"], "0.1 0.2 0.3"),
                               (["same", "same"], "1 2"), ([None], ""), (["ordinary"], [1])):
            with self.subTest(names=names), self.assertRaises(ValueError):
                h3_request_loras_for_model({"activated_loras": names, "loras_multipliers": weights}, "minimax_h3")

    def test_exclusive_aliases_and_dasiwa_paths_keep_validation(self):
        turbo = "minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors"
        with self.assertRaisesRegex(ValueError, "cannot be stacked"):
            validate_h3_lora_selection(model_type="minimax_h3", fl2va_loras=["a/" + turbo, "b/" + turbo])
        dasiwa = "folder\\" + DASIWA_FILENAME
        with self.assertRaisesRegex(ValueError, "four sampling steps"):
            validate_h3_lora_selection(model_type="minimax_h3_ref2va", ref2va_loras=[dasiwa], num_inference_steps=20)
        validate_h3_lora_selection(model_type="minimax_h3_ref2va", ref2va_loras=[dasiwa], num_inference_steps=4)



if __name__ == "__main__":
    os.chdir(ROOT)
    unittest.main()


class H3LoraLaunchProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ast
        import types
        source = (APP / 'launch.py').read_text()
        names = {
            '_h3_effective_model_types', '_validate_h3_lora_request',
            '_h3_lora_asset_selections', '_apply_h3_loras_to_manifest',
            '_h3_estimate_context', '_apply_h3_adaptive_checkpoint',
            '_h3_preferred_fl2va_model', '_validate_h3_turbo_estimate_context',
        }
        module = ast.Module(body=[n for n in ast.parse(source).body
                                  if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[])
        cls.helpers = {
            '_H3_LONG_STUDIO_MODELS': {
                'minimax_h3', 'minimax_h3_pinkcherry_fl2va',
                'minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va',
            },
            '_H3_REF2VA_MODEL': 'minimax_h3_ref2va',
            '_H3_BASE_FL2VA_MODEL': 'minimax_h3',
            '_H3_FL2VA_MODELS': {'minimax_h3', 'minimax_h3_pinkcherry_fl2va', 'minimax_h3_w4a8_fl2va'},
            'wgp': types.SimpleNamespace(get_model_def=lambda _: {'fps': 24, 'frames_maximum': 360}, get_base_model_type=lambda m: m),
        }
        exec(compile(module, 'lora-launch-projection', 'exec'), cls.helpers)

    def request(self):
        return {
            'model_type': 'minimax_h3_ref2va',
            'h3_adaptive_conditioning': False,
            'activated_loras': ['cached.safetensors'],
            'h3_fl2va_loras': ['fl/path.safetensors'],
            'h3_fl2va_loras_multipliers': '0.75',
            'h3_ref2va_loras': ['ref/path.safetensors'],
            'h3_ref2va_loras_multipliers': '0.25',
            'num_inference_steps': 20,
        }

    def test_asset_manifest_and_estimate_selections_match_each_segment(self):
        import copy
        body = self.request()
        original = copy.deepcopy(body)
        models = ['minimax_h3_w4a8_fl2va', 'minimax_h3_ref2va']
        plan = {'clip_frames': [120, 120],
                'segment_models': [{'model_type': m} for m in models]}
        assets = self.helpers['_h3_lora_asset_selections'](body, plan)
        manifest = [{'params': {'model_type': m}} for m in models]
        self.helpers['_apply_h3_loras_to_manifest'](manifest, body)
        estimates = self.helpers['_h3_estimate_context'](body, plan)['_segment_contexts']
        for row, estimate, model in zip(manifest, estimates, models):
            names, weights = assets[model]
            self.assertEqual((names, weights),
                             (row['params']['activated_loras'], row['params']['loras_multipliers']))
            self.assertEqual((names, weights),
                             (estimate['activated_loras'], estimate['loras_multipliers']))
        self.assertEqual(body, original)
        self.assertEqual(assets[models[0]], (['fl/path.safetensors'], '0.75'))
        self.assertEqual(assets[models[1]], (['ref/path.safetensors'], '0.25'))

    def test_single_task_and_restored_aliases_resolve_before_mutation(self):
        from services.h3_dasiwa import BETTER_MOTION_FILENAME
        body = {'model_type': 'minimax_h3', 'activated_loras': [BETTER_MOTION_FILENAME],
                'loras_multipliers': '0.6'}
        other = {'model_type': 'minimax_h3_ref2va'}
        # A single-task row aliases the request; a restored mixed manifest must
        # still use the original shared choices for later rows.
        self.helpers['_apply_h3_loras_to_manifest']([{'params': body}, {'params': other}], body)
        self.assertEqual(body['activated_loras'], [])
        self.assertEqual(other['activated_loras'], [BETTER_MOTION_FILENAME])
        self.assertEqual(other['loras_multipliers'], '0.6')
        single = self.request()
        self.helpers['_apply_h3_loras_to_manifest']([{'params': single}], single)
        self.assertEqual(single['activated_loras'], ['ref/path.safetensors'])

    def test_admission_rejects_invalid_restored_selection_before_asset_work(self):
        body = self.request()
        body['h3_ref2va_loras'] = [DASIWA_FILENAME]
        with self.assertRaisesRegex(ValueError, 'four sampling steps'):
            self.helpers['_validate_h3_lora_request'](body)
        body['num_inference_steps'] = 4
        body['h3_ref2va_loras_multipliers'] = '1.0'
        self.helpers['_validate_h3_lora_request'](body)
        body['h3_ref2va_loras'] = []
        body['h3_ref2va_loras_multipliers'] = ''
        self.assertEqual(self.helpers['_h3_lora_asset_selections'](body),
                         {'minimax_h3_ref2va': ([], '')})

    def test_single_segment_estimate_uses_actual_checkpoint(self):
        body = self.request()
        plan = {'clip_frames': [120],
                'segment_models': [{'model_type': 'minimax_h3_w4a8_fl2va'}]}
        estimate = self.helpers['_h3_estimate_context'](body, plan)
        segment = estimate['_segment_contexts'][0]
        self.assertEqual(segment['activated_loras'], ['fl/path.safetensors'])
        self.assertEqual(segment['loras_multipliers'], '0.75')

    def test_non_h3_task_keeps_native_lora_fields(self):
        body = {'model_type': 'another-model', 'activated_loras': 'legacy',
                'loras_multipliers': None, 'h3_fl2va_loras': 'unused'}
        self.helpers['_validate_h3_lora_request'](body)
        self.helpers['_apply_h3_loras_to_manifest']([{'params': body}], body)
        self.assertEqual(body['activated_loras'], 'legacy')
        self.assertEqual(self.helpers['_h3_lora_asset_selections'](body),
                         {'another-model': ('legacy', None)})

    def test_adaptive_estimate_routes_before_selecting_loras(self):
        body = self.request()
        body.update(model_type='minimax_h3', h3_adaptive_conditioning=True,
                    reference_shape={'image_count': 1})
        context = self.helpers['_h3_estimate_context'](body)
        self.assertEqual(context['model_type'], 'minimax_h3_ref2va')
        self.assertEqual(context['activated_loras'], ['ref/path.safetensors'])
        self.assertEqual(context['loras_multipliers'], '0.25')
        body['h3_ref2va_loras'] = [DASIWA_FILENAME]
        context = self.helpers['_h3_estimate_context'](body)
        with self.assertRaisesRegex(ValueError, 'four sampling steps'):
            self.helpers['_validate_h3_lora_request']({**body, 'model_type': context['model_type']})
        body.update(model_type='minimax_h3_ref2va', reference_shape={})
        context = self.helpers['_h3_estimate_context'](body)
        self.assertEqual(context['model_type'], 'minimax_h3')
        self.assertEqual(context['activated_loras'], ['fl/path.safetensors'])
        self.assertEqual(context['loras_multipliers'], '0.75')

    def test_turbo_validation_preserves_each_segment_selection(self):
        from unittest import mock
        body = self.request()
        body['custom_settings'] = {'h3_turbo_profile': 'h3_turbo_v4'}
        plan = {'clip_frames': [120, 120], 'segment_models': [
            {'model_type': 'minimax_h3'}, {'model_type': 'minimax_h3_ref2va'}]}
        context = self.helpers['_h3_estimate_context'](body, plan)
        with mock.patch('services.h3_turbo.validate_turbo_request') as validate:
            self.helpers['_validate_h3_turbo_estimate_context'](context)
        self.assertEqual([(c.kwargs['activated_loras'], c.kwargs['loras_multipliers'])
                          for c in validate.call_args_list],
                         [(['fl/path.safetensors'], '0.75'), (['ref/path.safetensors'], '0.25')])

    def test_profile_candidate_matches_retained_split_settings(self):
        import ast
        body = self.request()
        plan = {'clip_frames': [120, 120], 'segment_models': [
            {'model_type': 'minimax_h3'}, {'model_type': 'minimax_h3_ref2va'}]}
        context = self.helpers['_h3_estimate_context'](body, plan)
        function = next(n for n in ast.walk(ast.parse((APP/'launch.py').read_text()))
                        if isinstance(n, ast.FunctionDef) and n.name == 'candidate_for_settings')
        namespace = {'context': context}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'profile-candidate', 'exec'), namespace)
        settings = {'model_type': 'minimax_h3_ref2va', 'num_inference_steps': 20,
                    'resolution': '1344x768', 'activated_loras': ['profile.safetensors'],
                    'loras_multipliers': '0.9', 'custom_settings': {}}
        candidate = namespace['candidate_for_settings'](settings)
        for segment in candidate['_segment_contexts']:
            expected = h3_request_loras_for_model({**body, **settings}, segment['model_type'])
            self.assertEqual((segment['activated_loras'], segment['loras_multipliers']), expected)
        self.assertEqual(candidate['activated_loras'], ['ref/path.safetensors'])

    def test_surplus_shared_and_split_weights_are_rejected(self):
        for body in [
            {'activated_loras': ['a'], 'loras_multipliers': '0.5 0.7'},
            {'h3_fl2va_loras': ['a'], 'h3_fl2va_loras_multipliers': '0.5 0.7'},
            {'h3_fl2va_loras': [], 'h3_fl2va_loras_multipliers': '0.5'},
        ]:
            with self.assertRaisesRegex(ValueError, 'exceed'):
                h3_request_loras_for_model(body, 'minimax_h3')


if __name__ == '__main__':
    unittest.main()
