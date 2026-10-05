"""CPU regression checks for SeedVC background channels and cancellation."""

from pathlib import Path
from abc import ABC
import ast
import threading
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch
import torchaudio
from tqdm import tqdm


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from postprocessing.voice_clone import (
    _ffmpeg_demux_audio, _remix_vocals_with_background, apply_voice_clone_to_file,
    _convert_seedvc_with_cancellation, _VoiceCloneCancelled, _VoiceCloneCancellationUnavailable,
)


class VoiceCloneStereoTests(unittest.TestCase):
    def installed_euler_converter(self):
        path = Path(__file__).resolve().parents[1] / "app/postprocessing/seedvc/modules/flow_matching.py"
        if not path.is_file():
            self.skipTest("Installed SeedVC v1 Euler source is required for this CPU qualification")
        tree = ast.parse(path.read_text())
        base = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BASECFM")
        namespace = dict(torch=torch, F=torch.nn.functional, ABC=ABC,
                         tqdm=lambda values: tqdm(values, disable=True))
        # Execute the installed class unchanged without importing its model-loader package.
        exec(compile(ast.Module(body=[base], type_ignores=[]), str(path), "exec"), namespace)
        cfm = namespace["BASECFM"](types.SimpleNamespace(
            DiT=types.SimpleNamespace(in_channels=1), reg_loss_type="l2"))
        class Estimator(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.batches = []
                self.fail_at = None
            def forward(self, x, *_args):
                self.batches.append(x.shape[0])
                if len(self.batches) == self.fail_at:
                    raise ValueError("estimator failed")
                return torch.zeros_like(x)
        cfm.estimator = Estimator()
        def convert(**kwargs):
            return cfm.inference(torch.zeros((1, 8, 2)), torch.tensor([8]),
                torch.zeros((1, 1, 1)), torch.zeros((1, 2)), None,
                kwargs.get("diffusion_steps", 6), inference_cfg_rate=kwargs.get("cfg_rate", 0.5))[0]
        return types.SimpleNamespace(convert_tensor=Mock(side_effect=convert),
            _app_vc=types.SimpleNamespace(model=types.SimpleNamespace(cfm=cfm))), cfm.estimator

    def test_installed_euler_cfg_and_noncfg_cancel_before_next_step_and_remove_only_owned_hook(self):
        for cfg_rate in (0.0, 0.5):
            with self.subTest(cfg_rate=cfg_rate):
                converter, estimator = self.installed_euler_converter()
                foreign = estimator.register_forward_pre_hook(lambda *_: None)
                self.addCleanup(foreign.remove)
                existing_hooks = dict(estimator._forward_pre_hooks)
                with self.assertRaises(_VoiceCloneCancelled):
                    _convert_seedvc_with_cancellation(converter, lambda: len(estimator.batches) >= 2,
                                                      diffusion_steps=6, cfg_rate=cfg_rate)
                self.assertEqual(estimator.batches, [2 if cfg_rate else 1] * 2)
                self.assertEqual(dict(estimator._forward_pre_hooks), existing_hooks)
                estimator.batches.clear()
                result = _convert_seedvc_with_cancellation(converter, None, diffusion_steps=6, cfg_rate=cfg_rate)
                self.assertEqual(result.shape, (1, 8))
                self.assertEqual(estimator.batches, [2 if cfg_rate else 1] * 6)
                self.assertEqual(dict(estimator._forward_pre_hooks), existing_hooks)

    def test_installed_euler_success_and_error_remove_hook_and_later_conversion_runs_normally(self):
        for fail_at in (None, 2):
            with self.subTest(fail_at=fail_at):
                converter, estimator = self.installed_euler_converter()
                estimator.fail_at = fail_at
                if fail_at:
                    with self.assertRaisesRegex(ValueError, "estimator failed"):
                        _convert_seedvc_with_cancellation(converter, lambda: False)
                else:
                    _convert_seedvc_with_cancellation(converter, lambda: False)
                    self.assertEqual(len(estimator.batches), 6)
                self.assertEqual(dict(estimator._forward_pre_hooks), {})
                estimator.fail_at = None
                estimator.batches.clear()
                _convert_seedvc_with_cancellation(converter, None)
                self.assertEqual(len(estimator.batches), 6)

    def test_scoped_cancel_hook_does_not_cancel_callback_free_conversion_in_another_thread(self):
        converter, estimator = self.installed_euler_converter()
        convert = converter.convert_tensor
        entered, release = threading.Event(), threading.Event()
        failures = []
        def blocked_convert(**kwargs):
            if threading.current_thread().name == "cancelled-conversion":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("CPU conversion barrier timed out")
            return convert(**kwargs)
        converter.convert_tensor = blocked_convert
        def cancelled_conversion():
            try:
                _convert_seedvc_with_cancellation(converter, lambda: True)
            except Exception as error:
                failures.append(error)
        worker = threading.Thread(target=cancelled_conversion, name="cancelled-conversion")
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            _convert_seedvc_with_cancellation(converter, None)
            self.assertEqual(len(estimator.batches), 6)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], _VoiceCloneCancelled)
        self.assertEqual(len(estimator.batches), 6)
        self.assertEqual(dict(estimator._forward_pre_hooks), {})

    def test_each_wrapper_conversion_branch_cancels_before_save_remix_remux_and_cleans_scratch(self):
        for branch in ("single", "two-fallback", "two-segments"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                video, ref_a, ref_b = (root / name for name in ("source.mp4", "a.wav", "b.wav"))
                for path in (video, ref_a, ref_b):
                    path.write_bytes(path.name.encode())
                originals = [path.read_bytes() for path in (video, ref_a, ref_b)]
                converter, estimator = self.installed_euler_converter()
                seedvc = types.ModuleType("postprocessing.seedvc")
                seedvc.download_assets = Mock()
                seedvc.get_model = Mock(return_value=converter)
                separator = types.ModuleType("preprocessing.extract_vocals")
                separator.get_stems = Mock(return_value=("vocals.wav", "background.wav"))
                scratch = []
                def demux(_source, destination, **_kwargs):
                    Path(destination).write_bytes(b"demuxed")
                    scratch.append(Path(destination).parent)
                    return True
                segments = [(0.0, 0.5, "first"), (0.5, 1.0, "second")] if branch == "two-segments" else []
                cancel_delivered = False
                def cancel_once():
                    nonlocal cancel_delivered
                    if len(estimator.batches) >= 2 and not cancel_delivered:
                        cancel_delivered = True
                        return True
                    return False
                with patch.dict(sys.modules, {"postprocessing.seedvc": seedvc,
                        "preprocessing.extract_vocals": separator}), \
                        patch("postprocessing.voice_clone._ffmpeg_demux_audio", side_effect=demux), \
                        patch("postprocessing.voice_clone.torchaudio.load", return_value=(torch.zeros((1, 100)), 100)), \
                        patch("postprocessing.voice_clone._load_reference_voice", return_value=(torch.zeros((1, 100)), 100)), \
                        patch("postprocessing.voice_clone._diarize_audio_for_segments", return_value=segments), \
                        patch("postprocessing.voice_clone.torch.cuda.is_available", return_value=False), \
                        patch("postprocessing.voice_clone.torchaudio.save") as save, \
                        patch("postprocessing.voice_clone._remix_vocals_with_background") as remix, \
                        patch("postprocessing.voice_clone._ffmpeg_remux_audio") as remux:
                    result = apply_voice_clone_to_file(str(video), [str(ref_a), str(ref_b)],
                        mode="single" if branch == "single" else "two", diffusion_steps=6,
                        cancel_check=cancel_once, strict_in_place=True)
                self.assertFalse(result)
                self.assertEqual(len(estimator.batches), 2)
                self.assertEqual(converter.convert_tensor.call_count, 1)
                save.assert_not_called(); remix.assert_not_called(); remux.assert_not_called()
                self.assertEqual(dict(estimator._forward_pre_hooks), {})
                self.assertEqual([path.read_bytes() for path in (video, ref_a, ref_b)], originals)
                self.assertEqual(len(scratch), 1)
                self.assertFalse(scratch[0].exists())

    def test_callback_free_converter_needs_no_cancellation_seam_but_active_callback_is_explicitly_unsupported(self):
        converter = types.SimpleNamespace(convert_tensor=Mock(return_value=torch.zeros((1, 4))))
        _convert_seedvc_with_cancellation(converter, None)
        self.assertEqual(converter.convert_tensor.call_count, 1)
        with self.assertRaisesRegex(_VoiceCloneCancellationUnavailable, "does not support"):
            _convert_seedvc_with_cancellation(converter, lambda: False)
        self.assertEqual(converter.convert_tensor.call_count, 1)

    def test_segment_fallback_does_not_report_applied_when_cancellation_seam_is_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video, ref_a, ref_b = (root / name for name in ("source.mp4", "a.wav", "b.wav"))
            for path in (video, ref_a, ref_b):
                path.write_bytes(path.name.encode())
            converter = types.SimpleNamespace(convert_tensor=Mock(return_value=torch.zeros((1, 50))))
            seedvc = types.ModuleType("postprocessing.seedvc")
            seedvc.download_assets = Mock()
            seedvc.get_model = Mock(return_value=converter)
            separator = types.ModuleType("preprocessing.extract_vocals")
            separator.get_stems = Mock(return_value=("vocals.wav", "background.wav"))
            with patch.dict(sys.modules, {"postprocessing.seedvc": seedvc,
                    "preprocessing.extract_vocals": separator}), \
                    patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True), \
                    patch("postprocessing.voice_clone.torchaudio.load", return_value=(torch.zeros((1, 100)), 100)), \
                    patch("postprocessing.voice_clone._load_reference_voice", return_value=(torch.zeros((1, 100)), 100)), \
                    patch("postprocessing.voice_clone._diarize_audio_for_segments", return_value=[(0.0, 0.5, "first")]), \
                    patch("postprocessing.voice_clone.torch.cuda.is_available", return_value=False), \
                    patch("postprocessing.voice_clone.torchaudio.save") as save, \
                    patch("postprocessing.voice_clone._ffmpeg_remux_audio") as remux:
                with self.assertRaisesRegex(_VoiceCloneCancellationUnavailable, "does not support"):
                    apply_voice_clone_to_file(str(video), [str(ref_a), str(ref_b)], mode="two",
                                             cancel_check=lambda: False)
            converter.convert_tensor.assert_not_called()
            save.assert_not_called(); remux.assert_not_called()
            self.assertEqual(video.read_bytes(), b"source.mp4")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for demux")
    def test_demux_retains_stereo_source_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            extracted = root / "extracted.wav"
            original = torch.stack((torch.full((4096,), 0.1), torch.full((4096,), 0.3)))
            torchaudio.save(str(source), original, 44100)

            self.assertTrue(_ffmpeg_demux_audio(str(source), str(extracted), sample_rate=44100))
            audio, rate = torchaudio.load(str(extracted))
            self.assertEqual(rate, 44100)
            self.assertEqual(audio.shape[0], 2)
            self.assertAlmostEqual(float((audio[1] - audio[0]).mean()), 0.2, delta=0.001)

    def test_remix_centers_voice_without_collapsing_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            voice = root / "voice.wav"
            background = root / "background.wav"
            remixed = root / "remixed.wav"
            torchaudio.save(str(voice), torch.full((1, 4096), 0.2), 44100)
            torchaudio.save(
                str(background),
                torch.stack((torch.full((4096,), 0.1), torch.full((4096,), 0.3))),
                44100,
            )

            _remix_vocals_with_background(str(voice), str(background), str(remixed))
            audio, rate = torchaudio.load(str(remixed))
            self.assertEqual(rate, 44100)
            self.assertEqual(audio.shape[0], 2)
            self.assertAlmostEqual(float(audio[0].mean()), 0.3, delta=0.001)
            self.assertAlmostEqual(float(audio[1].mean()), 0.5, delta=0.001)
            self.assertAlmostEqual(float((audio[1] - audio[0]).mean()), 0.2, delta=0.001)

    def test_cancel_after_vocal_separation_skips_seedvc_model_load(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            reference = Path(directory) / "reference.wav"
            video.write_bytes(b"original")
            reference.write_bytes(b"reference")
            cancelled = False

            def get_stems(*_args):
                nonlocal cancelled
                cancelled = True
                return "vocals.wav", "instrumental.wav"

            seedvc = types.ModuleType("postprocessing.seedvc")
            seedvc.download_assets = Mock()
            seedvc.get_model = Mock()
            separator = types.ModuleType("preprocessing.extract_vocals")
            separator.get_stems = get_stems
            with patch.dict(sys.modules, {
                "postprocessing.seedvc": seedvc,
                "preprocessing.extract_vocals": separator,
            }), patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True):
                result = apply_voice_clone_to_file(
                    str(video), [str(reference)], cancel_check=lambda: cancelled,
                )

            self.assertFalse(result)
            seedvc.download_assets.assert_not_called()
            seedvc.get_model.assert_not_called()
            self.assertEqual(video.read_bytes(), b"original")

    def test_cancel_after_seedvc_conversion_skips_remux(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            reference = Path(directory) / "reference.wav"
            video.write_bytes(b"original")
            reference.write_bytes(b"reference")
            cancelled = False

            def convert_tensor(**_kwargs):
                nonlocal cancelled
                cancelled = True
                return torch.zeros((2, 64))

            seedvc = types.ModuleType("postprocessing.seedvc")
            seedvc.download_assets = Mock()
            seedvc.get_model = Mock(return_value=types.SimpleNamespace(convert_tensor=convert_tensor,
                _app_vc=types.SimpleNamespace(model=types.SimpleNamespace(
                    cfm=types.SimpleNamespace(estimator=torch.nn.Identity())))))
            separator = types.ModuleType("preprocessing.extract_vocals")
            separator.get_stems = Mock(side_effect=RuntimeError("unavailable"))
            with patch.dict(sys.modules, {
                "postprocessing.seedvc": seedvc,
                "preprocessing.extract_vocals": separator,
            }), patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True), \
                    patch("postprocessing.voice_clone.torchaudio.load", return_value=(torch.zeros((1, 64)), 44100)), \
                    patch("postprocessing.voice_clone._load_reference_voice", return_value=(torch.zeros((1, 64)), 44100)), \
                    patch("postprocessing.voice_clone.torch.cuda.is_available", return_value=False), \
                    patch("postprocessing.voice_clone._ffmpeg_remux_audio") as remux:
                result = apply_voice_clone_to_file(
                    str(video), [str(reference)], cancel_check=lambda: cancelled,
                )

            self.assertFalse(result)
            remux.assert_not_called()
            self.assertEqual(video.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
