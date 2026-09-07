"""CPU-only execution tests for active H3 continuation reference selection."""
import ast
import copy
import os
import sys
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'app/models/minimax_h3/minimax_h3_main.py'
sys.path.insert(0, str(ROOT / 'app'))
from services.h3_reference_inputs import selected_h3_video_slots


def _helpers():
    module = ast.parse((ROOT / 'app/launch.py').read_text())
    nodes = [node for node in module.body if isinstance(node, ast.FunctionDef)
             and node.name in {'_h3_ref2va_reference_capacity', '_attach_h3_ref2va_handoff',
                               '_set_h3_ref2va_tail', '_apply_h3_recovered_continuation'}]
    calls = []
    namespace = {
        'os': os,
        'wgp': types.SimpleNamespace(get_video_info=lambda path: (24, 1, 1, 24)),
        '_H3_REF2VA_HANDOFF_FRAMES': 56,
        'QueueRecoveryRuntimeError': RuntimeError,
        '_create_h3_ref2va_tail_video': lambda *args: calls.append(args),
        '_queue_recovery_continuation_path': lambda root, descriptor: descriptor['path'],
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'handoff-helpers', 'exec'), namespace)
    return namespace, calls


def _runtime_video_refs(params):
    module = ast.parse(MAIN.read_text())
    assignment = next(node for node in ast.walk(module)
                      if isinstance(node, ast.Assign) and any(
                          isinstance(target, ast.Name) and target.id == 'video_refs'
                          for target in node.targets
                      ) and 'selected_h3_video_slots' in ast.unparse(node.value))
    namespace = {'video_prompt_type': params.get('video_prompt_type', ''),
                 'input_frames': params.get('video_guide'),
                 'input_frames2': params.get('video_guide2'),
                 'input_frames3': params.get('video_guide3'),
                 'selected_h3_video_slots': selected_h3_video_slots}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'runtime-video-selection', 'exec'), namespace)
    return namespace['video_refs']


class H3ReferenceHandoffTests(unittest.TestCase):
    def attach(self, params, boundary='continuous'):
        helpers, calls = _helpers()
        result = helpers['_attach_h3_ref2va_handoff'](
            params, latest_video='previous.mp4', last_frame_path='previous.png',
            out_dir='staging', task_no=4, boundary_type=boundary,
        )
        return result, calls

    def test_paired_soundtracks_use_still_without_changing_authored_audio(self):
        params = {'video_guide': 'user.mp4', 'video_prompt_type': 'V-',
                  'audio_prompt_type': 'KABC', 'audio_guide': 'paired.wav',
                  'audio_guide2': 'inactive.wav', 'image_refs': ['identity.png']}
        before = copy.deepcopy(params)
        result, calls = self.attach(params)
        self.assertEqual(result['mode'], 'semantic_still')
        self.assertEqual(calls, [])
        for key in ('video_guide', 'video_prompt_type', 'audio_prompt_type', 'audio_guide', 'audio_guide2'):
            self.assertEqual(params[key], before[key], key)
        self.assertEqual(params['image_refs'], ['identity.png', 'previous.png'])
        self.assertEqual(_runtime_video_refs(params), _runtime_video_refs(before))

    def test_paired_mode_with_full_image_capacity_never_manufactures_a_pair(self):
        params = {'video_guide': 'user.mp4', 'video_prompt_type': 'V-',
                  'audio_prompt_type': 'K', 'audio_guide': 'paired.wav',
                  'image_refs': [f'image{i}.png' for i in range(9)]}
        before = copy.deepcopy(params)
        result, calls = self.attach(params)
        self.assertEqual(result['mode'], 'prompt_only')
        self.assertEqual(calls, [])
        self.assertEqual(params['image_refs'], before['image_refs'])
        self.assertEqual(_runtime_video_refs(params), _runtime_video_refs(before))
        self.assertEqual(params['audio_prompt_type'], 'K')

    def test_silent_tail_does_not_claim_cached_audio(self):
        params = {'audio_prompt_type': 'A', 'audio_guide': 'voice.wav',
                  'custom_settings': {'h3_ref2va_handoff_audio': True}}
        result, calls = self.attach(params)
        self.assertEqual(result['mode'], 'temporal_tail')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('h3_ref2va_handoff_audio', params['custom_settings'])
        self.assertEqual(params['audio_prompt_type'], 'A')
        self.assertEqual(params['audio_guide'], 'voice.wav')
        self.assertEqual(_runtime_video_refs(params), [result['path']])

    def test_append_preserves_runtime_ordinals_across_sparse_physical_slots(self):
        for params in (
            {'video_guide': 'first.mp4', 'video_prompt_type': 'V-'},
            {'video_guide2': 'first.mp4', 'video_prompt_type': 'V+-'},
            {'video_guide': 'first.mp4', 'video_guide2': 'second.mp4', 'video_prompt_type': 'V+-'},
        ):
            with self.subTest(params=params):
                prior = _runtime_video_refs(params)
                result, calls = self.attach(params)
                self.assertEqual(result['mode'], 'temporal_tail')
                self.assertEqual(len(calls), 1)
                self.assertEqual(_runtime_video_refs(params), prior + [result['path']])

    def test_no_handoff_activates_hidden_uploads_or_fills_before_existing_ordinal(self):
        for params in (
            {'video_guide2': 'inactive.mp4', 'video_prompt_type': ''},
            {'video_guide': 'first.mp4', 'video_guide2': 'inactive.mp4', 'video_prompt_type': 'V-'},
            {'video_guide': 'first.mp4', 'video_guide3': 'second.mp4', 'video_prompt_type': 'V-'},
        ):
            with self.subTest(params=params):
                before = copy.deepcopy(params)
                prior = _runtime_video_refs(params)
                result, calls = self.attach(params)
                self.assertEqual(result['mode'], 'semantic_still')
                self.assertEqual(calls, [])
                for key, value in before.items():
                    self.assertEqual(params[key], value)
                self.assertEqual(_runtime_video_refs(params), prior)

    def test_paired_soundtrack_capacity_excludes_suppressed_abc_inputs(self):
        helpers, _ = _helpers()
        params = {'video_guide': 'one.mp4', 'video_guide2': 'two.mp4',
                  'video_guide3': 'three.mp4', 'video_prompt_type': 'V++-',
                  'audio_prompt_type': 'KABC',
                  'image_refs': [f'image{i}.png' for i in range(8)]}
        capacity = helpers['_h3_ref2va_reference_capacity'](params, handoff_seconds=56 / 24)
        self.assertEqual(capacity['mixed_count'], 11)
        self.assertTrue(capacity['image'])
        self.assertFalse(capacity['video'])

    def test_recovered_tail_preserves_the_fresh_physical_slot_and_audio_policy(self):
        original = {'video_guide2': 'authored.mp4', 'video_prompt_type': 'V+-',
                    'custom_settings': {'h3_ref2va_handoff_audio': True}}
        fresh = copy.deepcopy(original)
        handoff, _ = self.attach(fresh)
        self.assertEqual(handoff['video_slot'], 3)
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                descriptor = {'dependency': 'prior', **handoff}
                if legacy:
                    descriptor.pop('video_slot')
                task = {'params': copy.deepcopy(original)}
                helpers, _ = _helpers()
                helpers['_apply_h3_recovered_continuation'](
                    {'unit_id': 'prior', 'continuation': descriptor}, task, 'project',
                )
                self.assertEqual(_runtime_video_refs(task['params']), _runtime_video_refs(fresh))
                self.assertEqual(task['params']['custom_settings'], fresh['custom_settings'])
                self.assertEqual(task['params']['video_guide2'], 'authored.mp4')

    def test_recovered_tail_rejects_changed_slots_and_unpaired_soundtrack_before_mutation(self):
        for bad_slot in (1, 2, '3', True):
            task = {'params': {'video_guide2': 'authored.mp4', 'video_prompt_type': 'V+-'}}
            before = copy.deepcopy(task)
            helpers, _ = _helpers()
            with self.assertRaisesRegex(RuntimeError, 'slot changed'):
                helpers['_apply_h3_recovered_continuation'](
                    {'unit_id': 'prior', 'continuation': {'dependency': 'prior',
                     'mode': 'temporal_tail', 'path': 'tail.mp4', 'video_slot': bad_slot}},
                    task, 'project',
                )
            self.assertEqual(task, before)
        task = {'params': {'video_guide': 'authored.mp4', 'video_prompt_type': 'V-',
                           'audio_prompt_type': 'K', 'audio_guide': 'authored.wav'}}
        before = copy.deepcopy(task)
        helpers, _ = _helpers()
        with self.assertRaisesRegex(RuntimeError, 'cannot preserve'):
            helpers['_apply_h3_recovered_continuation'](
                {'unit_id': 'prior', 'continuation': {'dependency': 'prior',
                 'mode': 'temporal_tail', 'path': 'tail.mp4', 'video_slot': 2}},
                task, 'project',
            )
        self.assertEqual(task, before)

    def test_chosen_slot_survives_continuation_and_durable_descriptor_projection(self):
        module = ast.parse((ROOT / 'app/launch.py').read_text())
        prepare = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                       and node.name == '_prepare_task_continuation')
        namespace = {'handoff': {'mode': 'temporal_tail', 'path': 'tail.mp4', 'video_slot': 3}}
        found = False
        for parent in ast.walk(prepare):
            body = getattr(parent, 'body', None)
            if not isinstance(body, list):
                continue
            for index, node in enumerate(body):
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'h3_continuation'
                    for target in node.targets
                ) and isinstance(node.value, ast.Dict) and 'handoff' in ast.unparse(node.value):
                    exec(compile(ast.Module(body=body[index:index + 2], type_ignores=[]), 'continuation-projection', 'exec'), namespace)
                    found = True
        self.assertTrue(found)
        self.assertEqual(namespace['h3_continuation']['video_slot'], 3)
        descriptor_builder = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                                  and node.name == '_queue_recovery_continuation_descriptor')
        metadata_branch = next(node for node in descriptor_builder.body if isinstance(node, ast.If)
                               and isinstance(node.test, ast.Name) and node.test.id == 'metadata')
        namespace = {'metadata': namespace['h3_continuation'], 'descriptor': {}}
        exec(compile(ast.Module(body=[metadata_branch], type_ignores=[]), 'descriptor-projection', 'exec'), namespace)
        self.assertEqual(namespace['descriptor']['video_slot'], 3)

    def test_audio_cache_override_is_retired_from_runtime_and_admission(self):
        self.assertNotIn('override_last_audio_latent', MAIN.read_text())
        self.assertNotIn('h3_ref2va_handoff_audio', MAIN.read_text())
        handler = ROOT / 'app/models/minimax_h3/minimax_h3_handler.py'
        self.assertNotIn('h3_ref2va_handoff_audio', handler.read_text())

    def test_soundtrack_presentation_uses_only_explicit_input(self):
        module = ast.parse(MAIN.read_text())
        prepare = next(node for node in ast.walk(module)
                       if isinstance(node, ast.FunctionDef) and node.name == '_prepare_references')
        loop = next(node for node in prepare.body if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Tuple)
                    and any(isinstance(item, ast.Name) and item.id == 'video_index'
                            for item in node.target.elts))
        index = next(i for i, node in enumerate(loop.body)
                     if isinstance(node, ast.Assign) and any(
                         isinstance(target, ast.Name) and target.id == 'soundtrack_latent'
                         for target in node.targets))
        fragment = compile(ast.Module(body=loop.body[index:index + 3], type_ignores=[]), 'runtime-audio-presentation', 'exec')

        for waveform in (None, 'explicit.wav'):
            with self.subTest(waveform=waveform):
                encoded = []
                def encode(value):
                    encoded.append(value)
                    return 'encoded-audio'
                namespace = {'soundtrack': waveform,
                             'self': types.SimpleNamespace(_encode_reference_audio=encode),
                             'presentation': [], 'audio_latents': []}
                exec(fragment, namespace)
                self.assertEqual(len(namespace['audio_latents']), int(waveform is not None))
                self.assertEqual(namespace['presentation'], [{'type': 'audio'}] if waveform else [])
                self.assertEqual(encoded, [waveform] if waveform else [])


if __name__ == '__main__':
    unittest.main()
