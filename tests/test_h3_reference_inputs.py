"""CPU-only selection/preprocessing contracts for actual H3 reference slots."""
import ast
import itertools
from functools import lru_cache
from pathlib import Path
import sys
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from services.h3_reference_inputs import (
    H3ReferenceInputError, selected_h3_video_slots,
    prepare_h3_reference_video_slots, extract_h3_reference_soundtracks,
)


@lru_cache(maxsize=None)
def _module(path):
    return ast.parse((ROOT / path).read_text())


def _pairing_branch(semantic=True):
    wgp = _module('app/wgp.py')
    if semantic:
        return next(node for node in ast.walk(wgp) if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "'K' in audio_prompt_type"
                    and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                            and child.func.id == 'extract_h3_reference_soundtracks'
                            for child in ast.walk(node)))
    return next(node for node in ast.walk(wgp) if isinstance(node, ast.If)
                and ast.unparse(node.test) ==
                "'K' in audio_prompt_type and video_guide is not None and (not semantic_reference_mode)")


class H3ReferenceInputTests(unittest.TestCase):
    def test_native_video_selection_all_occupancies_and_flags(self):
        for occupied in itertools.product((False, True), repeat=3):
            values = tuple(f'video-{i}' if yes else None for i, yes in enumerate(occupied))
            for flags in ('', 'V-', 'V+-', 'V++-'):
                with self.subTest(occupied=occupied, flags=flags):
                    # Original native selection before extraction of its owner.
                    wanted = []
                    if 'V' in flags:
                        wanted.append((1, values[0]))
                        if '+' in flags:
                            wanted.append((2, values[1]))
                        if '++' in flags or values[2] is not None:
                            wanted.append((3, values[2]))
                    self.assertEqual(selected_h3_video_slots(flags, values), tuple(
                        pair for pair in wanted if pair[1] is not None))

    def test_loaded_values_are_never_truth_tested(self):
        class LoadedVideo:
            def __bool__(self):
                raise AssertionError('loaded tensor was truth-tested')
        value = LoadedVideo()
        self.assertIs(selected_h3_video_slots('V-', (value, None, None))[0][1], value)

    def test_preprocessing_keeps_third_video_in_its_physical_slot(self):
        prepared = prepare_h3_reference_video_slots(
            'V-', ('first.mp4', 'inactive.mp4', 'third.mp4'),
            prepare=lambda path: f'prepared:{path}',
        )
        self.assertEqual(prepared, ('prepared:first.mp4', None, 'prepared:third.mp4'))
        self.assertEqual([value for _, value in selected_h3_video_slots('V-', prepared)],
                         ['prepared:first.mp4', 'prepared:third.mp4'])
        wgp = _module('app/wgp.py')
        call = next(node for node in ast.walk(wgp) if isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == 'prepare_h3_reference_video_slots')
        namespace = dict(video_prompt_type='V-', video_guide='first.mp4',
                         video_guide2='inactive.mp4', video_guide3='third.mp4', fps=24,
                         model_def={}, prepare_h3_reference_video_slots=prepare_h3_reference_video_slots,
                         prepare_semantic_reference_video=lambda path, fps, model: f'prepared:{path}')
        exec(compile(ast.Module(body=[call], type_ignores=[]), 'active-reference-preparation', 'exec'), namespace)
        self.assertEqual((namespace['src_video'], namespace['src_video2'], namespace['src_video3']), prepared)

    def _run_wgp_pairing(self, *, semantic=True, videos=('one.mp4', 'two.mp4', 'three.mp4')):
        extracted, registered = [], []
        def extract(path, output):
            extracted.append((path, output))
            return output
        namespace = dict(
            audio_prompt_type='K', video_prompt_type='V++-', semantic_reference_mode=semantic,
            video_guide=videos[0], video_guide2=videos[1], video_guide3=videos[2],
            audio_guide='old-one.wav', audio_guide2='old-two.wav', audio_guide3='old-three.wav',
            extract_h3_reference_soundtracks=extract_h3_reference_soundtracks,
            extract_audio_tracks=lambda path, **kwargs: 1,
            _recovery_preprocess_path=lambda label: f'staging/unit-pre-{label}.wav',
            extract_audio_track_to_wav=extract, temp_filenames_list=registered,
            remove_temp_filenames=lambda paths: None,
        )
        exec(compile(ast.Module(body=[_pairing_branch(semantic)], type_ignores=[]), 'active-WGP-pairing', 'exec'), namespace)
        return namespace, extracted, registered

    def test_active_wgp_extracts_every_selected_h3_soundtrack(self):
        namespace, extracted, registered = self._run_wgp_pairing()
        expected = ['staging/unit-pre-control-audio.wav', 'staging/unit-pre-control-audio-2.wav',
                    'staging/unit-pre-control-audio-3.wav']
        self.assertEqual(extracted, list(zip(('one.mp4', 'two.mp4', 'three.mp4'), expected)))
        self.assertEqual(registered, expected)
        self.assertEqual([namespace[key] for key in ('audio_guide', 'audio_guide2', 'audio_guide3')], expected)

    def test_non_h3_pairing_keeps_its_existing_single_video_behavior(self):
        namespace, extracted, registered = self._run_wgp_pairing(semantic=False)
        self.assertEqual(extracted, [('one.mp4', 'staging/unit-pre-control-audio.wav')])
        self.assertEqual(registered, ['staging/unit-pre-control-audio.wav'])
        self.assertIsNone(namespace['audio_guide2'])
        self.assertEqual(namespace['audio_guide3'], 'old-three.wav')

    def test_missing_or_failed_soundtrack_is_bounded_and_cleanup_owned(self):
        for missing in (True, False):
            registered = []
            def extract(path, output):
                if path == 'private-two.mp4':
                    raise RuntimeError('extraction failed')
                return output
            with self.subTest(missing=missing):
                with self.assertRaises(H3ReferenceInputError) as caught:
                    extract_h3_reference_soundtracks(
                        'V+-', ('one.mp4', 'private-two.mp4', None),
                        has_audio=lambda path: not (missing and path == 'private-two.mp4'),
                        destination=lambda ordinal, path: f'track-{ordinal}.wav',
                        extract=extract, register=registered.append, cleanup=lambda paths: None,
                    )
                self.assertEqual(registered, ['track-1.wav'] if missing else ['track-1.wav', 'track-2.wav'])
                self.assertIn('Reference video 2', str(caught.exception))
                self.assertNotIn('private-two', str(caught.exception))

    def test_soundtrack_errors_hide_private_details_and_preserve_cancellation(self):
        for stage in ('probe', 'destination', 'register', 'extract'):
            for error_type in (RuntimeError, InterruptedError, KeyboardInterrupt, SystemExit):
                registered = []
                error = error_type('/private/source.mp4: decoder diagnostic')
                def fail(*args):
                    raise error
                with self.subTest(stage=stage, error_type=error_type):
                    expected = H3ReferenceInputError if error_type is RuntimeError else error_type
                    with self.assertRaises(expected) as caught:
                        extract_h3_reference_soundtracks(
                            'V-', ('/private/source.mp4', None, None),
                            has_audio=fail if stage == 'probe' else lambda path: True,
                            destination=fail if stage == 'destination' else lambda ordinal, path: 'track.wav',
                            extract=fail if stage == 'extract' else lambda path, output: output,
                            register=fail if stage == 'register' else registered.append,
                            cleanup=lambda paths: None,
                        )
                    self.assertEqual(registered, ['track.wav'] if stage == 'extract' else [])
                    if error_type is RuntimeError:
                        self.assertNotIn('/private', str(caught.exception))
                        self.assertNotIn('decoder diagnostic', str(caught.exception))
                        self.assertIs(caught.exception.__cause__, error)
                    else:
                        self.assertIs(caught.exception, error)

    def test_failed_attempt_removes_prior_and_partial_soundtracks(self):
        for error_type in (RuntimeError, InterruptedError, KeyboardInterrupt):
            with self.subTest(error_type=error_type), tempfile.TemporaryDirectory() as folder:
                outputs = [Path(folder) / f'track-{n}.wav' for n in (1, 2)]
                unrelated = Path(folder) / 'unrelated.wav'
                unrelated.write_bytes(b'preserved')
                registered = []
                def extract(path, output):
                    Path(output).write_bytes(b'partial')
                    if path == 'second.mp4':
                        raise error_type('private decoder error')
                    return output
                def cleanup(paths):
                    for path in paths:
                        Path(path).unlink(missing_ok=True)
                expected = H3ReferenceInputError if error_type is RuntimeError else error_type
                with self.assertRaises(expected):
                    extract_h3_reference_soundtracks(
                        'V+-', ('first.mp4', 'second.mp4', None),
                        has_audio=lambda path: True,
                        destination=lambda n, path: outputs[n - 1],
                        extract=extract, register=registered.append, cleanup=cleanup,
                    )
                self.assertEqual(registered, [str(path) for path in outputs])
                self.assertTrue(all(not path.exists() for path in outputs))
                self.assertEqual(unrelated.read_bytes(), b'preserved')

    def test_semantic_soundtracks_do_not_enter_target_audio_preprocessing(self):
        wgp = _module('app/wgp.py')
        gate = next(node for node in ast.walk(wgp) if isinstance(node, ast.If)
                    and ast.unparse(node.test) ==
                    'audio_guide != None and (not semantic_reference_mode)')
        initial = next(node for node in ast.walk(wgp) if isinstance(node, ast.Assign)
                       and ast.unparse(node) ==
                       'video_length_not_limited_by_audio = semantic_reference_mode')
        cap = next(node for node in ast.walk(gate) if isinstance(node, ast.If)
                   and ast.unparse(node.test) == 'not video_length_not_limited_by_audio')
        for semantic in (False, True):
            # Unequal 12s/2s references must not shorten a requested 10s H3 output.
            namespace = dict(semantic_reference_mode=semantic, audio_guide='one.wav',
                             current_video_length=244, duration=min(12, 2), fps=24, latent_size=4)
            probe = ast.If(test=gate.test, body=[cap], orelse=[])
            exec(compile(ast.fix_missing_locations(ast.Module(
                body=[initial, probe], type_ignores=[])), 'active-audio-duration-gate', 'exec'), namespace)
            self.assertEqual(namespace['current_video_length'], 244 if semantic else 53)
        pairing = _pairing_branch()
        owner = next(node for node in ast.walk(wgp) if isinstance(node, ast.Try)
                     and any(child is pairing for stmt in node.body for child in ast.walk(stmt)))
        self.assertTrue(any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                            and child.func.id == 'remove_temp_filenames'
                            for handler in owner.handlers for child in ast.walk(handler)))

    def test_semantic_audio_bypasses_target_window_slicing_and_tail_fallback(self):
        wgp = _module('app/wgp.py')
        gate = next(node for node in ast.walk(wgp) if isinstance(node, ast.If)
                    and ast.unparse(node.test).startswith('audio_guide is not None and model_def.get(')
                    and 'audio_guide_window_slicing' in ast.unparse(node.test))
        for guide in (None, 'stale-suppressed.wav'):
            namespace = dict(semantic_reference_mode=True, audio_guide=guide,
                             model_def={'audio_guide_window_slicing': True},
                             input_waveform='unchanged', input_waveform_sample_rate=32000)
            # Execute both actual branches. Any slicing or fallback would access
            # intentionally absent locals and fail instead of silently passing.
            exec(compile(ast.Module(body=[gate], type_ignores=[]), 'active-window-audio-gate', 'exec'), namespace)
            self.assertEqual(namespace['input_waveform'], 'unchanged')
            self.assertEqual(namespace['input_waveform_sample_rate'], 32000)



if __name__ == '__main__':
    unittest.main()
