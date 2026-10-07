"""CPU regression checks for SeedVC media preservation and observations."""

from pathlib import Path
from abc import ABC
import ast
import importlib.util
import os
import threading
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch
import torchaudio
from tqdm import tqdm


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import postprocessing
from postprocessing.voice_clone import (
    _ffmpeg_demux_audio, _ffmpeg_remux_audio, _remix_vocals_with_background, apply_voice_clone_to_file,
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
                with patch("builtins.print") as emit, self.assertRaises(_VoiceCloneCancelled):
                    _convert_seedvc_with_cancellation(converter, lambda: len(estimator.batches) >= 2,
                                                      diffusion_steps=6, cfg_rate=cfg_rate)
                emit.assert_called_once_with(
                    "[VoiceClone] Diffusion-step cancellation reached; estimator call skipped.", flush=True)
                self.assertEqual(estimator.batches, [2 if cfg_rate else 1] * 2)
                self.assertEqual(dict(estimator._forward_pre_hooks), existing_hooks)
                with patch("builtins.print", side_effect=BrokenPipeError), self.assertRaises(_VoiceCloneCancelled):
                    _convert_seedvc_with_cancellation(converter, lambda: True,
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
        observations = []
        def blocked_convert(**kwargs):
            if threading.current_thread().name == "cancelled-conversion":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("CPU conversion barrier timed out")
            return convert(**kwargs)
        converter.convert_tensor = blocked_convert
        def cancelled_conversion():
            try:
                _convert_seedvc_with_cancellation(converter, lambda: True,
                    step_callback=lambda *event: observations.append(event))
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
        self.assertEqual(observations, [])
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
                observations = []
                cancel_delivered = False
                def cancel_once():
                    nonlocal cancel_delivered
                    if len(estimator.batches) >= 2 and not cancel_delivered:
                        cancel_delivered = True
                        return True
                    return False
                with patch.object(postprocessing, "seedvc", seedvc, create=True), \
                        patch.dict(sys.modules, {"postprocessing.seedvc": seedvc,
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
                        cancel_check=cancel_once, strict_in_place=True,
                        progress_callback=observations.append)
                self.assertFalse(result)
                self.assertEqual(observations, [{"audio_chunk": 1, "diffusion_step": 1, "diffusion_steps": 6}])
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
            with patch.object(postprocessing, "seedvc", seedvc, create=True), \
                        patch.dict(sys.modules, {"postprocessing.seedvc": seedvc,
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

    def test_installed_euler_observations_are_neutral_across_passes_cfg_and_errors(self):
        for cfg_rate in (0.0, 0.5):
            with self.subTest(cfg_rate=cfg_rate):
                converter, estimator = self.installed_euler_converter()
                convert = converter.convert_tensor.side_effect
                def two_passes(**kwargs):
                    convert(**kwargs)
                    return convert(**kwargs)
                converter.convert_tensor.side_effect = two_passes
                torch.manual_seed(321)
                expected = _convert_seedvc_with_cancellation(converter, None, diffusion_steps=6, cfg_rate=cfg_rate)
                estimator.batches.clear()
                observations = []
                def observe(step, steps):
                    observations.append((step, steps, len(estimator.batches)))
                    if step == 2:
                        raise ValueError("observer failed")
                    return torch.ones((1, 8))  # Must never replace estimator inputs.
                torch.manual_seed(321)
                result = _convert_seedvc_with_cancellation(converter, None, step_callback=observe,
                    diffusion_steps=6, cfg_rate=cfg_rate)
                torch.testing.assert_close(result, expected, rtol=0, atol=0)
                self.assertEqual([event[:2] for event in observations], [(step, 6) for step in range(1, 7)] * 2)
                self.assertEqual([event[2] for event in observations], list(range(12)))
                self.assertEqual(estimator.batches, [2 if cfg_rate else 1] * 12)
                self.assertEqual(dict(estimator._forward_pre_hooks), {})
                estimator.batches.clear(); estimator.fail_at = 2
                with self.assertRaisesRegex(ValueError, "estimator failed"):
                    _convert_seedvc_with_cancellation(converter, None, step_callback=observe, diffusion_steps=6)
                self.assertEqual(dict(estimator._forward_pre_hooks), {})

    def test_wrapper_progress_tracks_actual_chunks_resets_after_segment_error_and_clears_before_remix(self):
        for branch in ("single", "two-fallback", "two-segments", "two-partial"):
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                video, ref_a, ref_b = (root / name for name in ("source.mp4", "a.wav", "b.wav"))
                for path in (video, ref_a, ref_b):
                    path.write_bytes(path.name.encode())
                converter, estimator = self.installed_euler_converter()
                convert = converter.convert_tensor.side_effect
                def two_passes(**kwargs):
                    convert(**kwargs)
                    return convert(**kwargs)
                converter.convert_tensor.side_effect = two_passes
                if branch == "two-partial":
                    estimator.fail_at = 2
                seedvc = types.ModuleType("postprocessing.seedvc")
                seedvc.download_assets = Mock(); seedvc.get_model = Mock(return_value=converter)
                separator = types.ModuleType("preprocessing.extract_vocals")
                separator.get_stems = Mock(return_value=("vocals.wav", "background.wav"))
                segments = [(0.0, 0.5, "first"), (0.5, 1.0, "second")] if branch in ("two-segments", "two-partial") else []
                observations, saved = [], []
                def observe(event):
                    observations.append(event)
                    return "ignored"
                def remix(*_args):
                    self.assertIsNone(observations[-1])
                with patch.object(postprocessing, "seedvc", seedvc, create=True), \
                        patch.dict(sys.modules, {"postprocessing.seedvc": seedvc,
                        "preprocessing.extract_vocals": separator}), \
                        patch("postprocessing.voice_clone._ffmpeg_demux_audio", return_value=True), \
                        patch("postprocessing.voice_clone.torchaudio.load", return_value=(torch.zeros((1, 100)), 100)), \
                        patch("postprocessing.voice_clone._load_reference_voice", return_value=(torch.zeros((1, 100)), 100)), \
                        patch("postprocessing.voice_clone._diarize_audio_for_segments", return_value=segments), \
                        patch("postprocessing.voice_clone.torch.cuda.is_available", return_value=False), \
                        patch("postprocessing.voice_clone.torchaudio.save", side_effect=lambda _path, audio, _sr: saved.append(audio.clone())), \
                        patch("postprocessing.voice_clone._remix_vocals_with_background", side_effect=remix), \
                        patch("postprocessing.voice_clone._ffmpeg_remux_audio", return_value=True), \
                        patch("postprocessing.voice_clone.time.monotonic", side_effect=[i * 0.1 for i in range(50)]):
                    torch.manual_seed(123)
                    self.assertTrue(apply_voice_clone_to_file(str(video), [str(ref_a), str(ref_b)],
                        mode="single" if branch == "single" else "two", diffusion_steps=6,
                        progress_callback=observe))
                    expected = saved[-1].clone()
                    observations.clear(); saved.clear(); estimator.batches.clear()
                    torch.manual_seed(123)
                    def broken_observer(event):
                        observe(event)
                        raise RuntimeError("UI observer unavailable")
                    self.assertTrue(apply_voice_clone_to_file(str(video), [str(ref_a), str(ref_b)],
                        mode="single" if branch == "single" else "two", diffusion_steps=6,
                        progress_callback=broken_observer))
                    torch.testing.assert_close(saved[-1], expected, rtol=0, atol=0)
                chunks = [event["audio_chunk"] for event in observations if event and event["diffusion_step"] == 1]
                self.assertEqual(chunks, list(range(1, (4 if branch == "two-segments" else 3 if branch == "two-partial" else 2) + 1)))
                self.assertTrue(all(1 <= event["diffusion_step"] <= 6 for event in observations if event))
                self.assertLess(len([event for event in observations if event]), len(estimator.batches))
                self.assertIsNone(observations[-1])
                self.assertEqual(dict(estimator._forward_pre_hooks), {})
                self.assertEqual(video.read_bytes(), b"source.mp4")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
    def test_remux_short_and_long_audio_preserves_every_video_packet_and_timing(self):
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100", "-t", str(350/24),
                "-c:v", "libx264", "-g", "48", "-bf", "3", "-c:a", "aac", str(source)], check=True, capture_output=True, timeout=30)
            def probe(path, *args):
                return json.loads(subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)],
                    check=True, capture_output=True, text=True, timeout=30).stdout)
            def packets(path):
                return probe(path, "-select_streams", "v:0", "-show_packets", "-show_data_hash", "sha256",
                    "-show_entries", "packet=pts,dts,duration,data_hash")['packets']
            original_packets = packets(source)
            self.assertEqual(len(original_packets), 350)
            original_stream = probe(source, "-select_streams", "v:0", "-show_streams")['streams'][0]
            for seconds in (1, 30):
                with self.subTest(replacement_seconds=seconds):
                    output = root / f"revoiced-{seconds}.mp4"; shutil.copyfile(source, output)
                    wav = root / f"voice-{seconds}.wav"
                    torchaudio.save(str(wav), torch.zeros((1, 44100 * seconds)), 44100)
                    self.assertTrue(_ffmpeg_remux_audio(str(output), str(wav), strict_in_place=True))
                    self.assertEqual(packets(output), original_packets)
                    stream = probe(output, "-select_streams", "v:0", "-show_streams")['streams'][0]
                    for field in ("time_base", "start_pts", "duration_ts", "nb_frames"):
                        self.assertEqual(stream[field], original_stream[field])
                    audio = probe(output, "-select_streams", "a:0", "-show_streams")['streams'][0]
                    self.assertAlmostEqual(float(audio['duration']), float(stream['duration']), delta=0.1)
            self.assertEqual(packets(source), original_packets)

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
            with patch.object(postprocessing, "seedvc", seedvc, create=True), patch.dict(sys.modules, {
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
            with patch.object(postprocessing, "seedvc", seedvc, create=True), patch.dict(sys.modules, {
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
            self.assertTrue(cancelled, "The fake converter must run before cancellation")
            seedvc.get_model.assert_called_once()
            remux.assert_not_called()
            self.assertEqual(video.read_bytes(), b"original")


class DiarizerModelRootsTests(unittest.TestCase):
    def exercise(self, consumer, layout):
        root = Path(__file__).resolve().parents[1]
        source = root / "app/services/audio_analysis.py"
        tree = ast.parse(source.read_text())
        loader = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_diarizer_pipeline")
        profiles = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "_DIARIZER_PROFILES" for t in n.targets))
        spec = importlib.util.spec_from_file_location("diarizer_test_locator", root / "app/shared/utils/files_locator.py")
        locator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(locator)
        filenames = ["pyannote_model_wespeaker-voxceleb-resnet34-LM.bin", "pytorch_model_segmentation-3.0.bin"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            primary, linked = base / "models", base / "linked"
            locator.set_checkpoints_paths([str(primary), str(linked)])
            embedding = (primary if layout == "primary" else linked) / "pyannote" / filenames[0]
            segmentation = primary / "pyannote" / filenames[1]
            embedding.parent.mkdir(parents=True)
            embedding.write_bytes(b"embedding-original")
            if layout != "missing":
                segmentation.parent.mkdir(parents=True, exist_ok=True)
                segmentation.write_bytes(b"segmentation-original")
            def fetch(**kwargs):
                self.assertEqual(kwargs["filename"], filenames[1])
                path = Path(kwargs["local_dir"]) / kwargs["filename"]
                path.write_bytes(b"downloaded-segmentation")
                return str(path)
            download = Mock(side_effect=fetch)
            pipe = Mock()
            pipe.to.return_value = pipe
            model = Mock(side_effect=lambda path: Path(path).read_bytes())
            modules = {"shared.utils.files_locator": locator,
                "shared.utils": types.SimpleNamespace(files_locator=locator),
                "pyannote.audio": types.SimpleNamespace(Model=types.SimpleNamespace(from_pretrained=model)),
                "pyannote.audio.pipelines": types.SimpleNamespace(SpeakerDiarization=Mock(return_value=pipe)),
                "huggingface_hub": types.SimpleNamespace(hf_hub_download=download)}
            with patch.dict(sys.modules, modules), patch.object(torch.cuda, "is_available", return_value=False):
                if consumer == "analysis":
                    ns = dict(os=os, __file__=str(base / "app/services/audio_analysis.py"),
                        _diarizer_pipe=None, _diarizer_profile=None, _DIARIZER_PROFILES=ast.literal_eval(profiles))
                    exec(compile(ast.Module(body=[loader], type_ignores=[]), str(source), "exec"), ns)
                    original_load = torch.load
                    self.assertIs(ns["get_diarizer_pipeline"](), pipe)
                    self.assertIs(torch.load, original_load)
                    self.assertIs(ns["get_diarizer_pipeline"](), pipe)
                else:
                    separator_tree = ast.parse((root / "app/preprocessing/speakers_separator.py").read_text())
                    cls = next(n for n in separator_tree.body if isinstance(n, ast.ClassDef) and n.name == "OptimizedPyannote31SpeakerSeparator")
                    constructor = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
                    ns = dict(torch=torch, os=os, Path=Path,
                        __file__=str(root / "app/preprocessing/speakers_separator.py"), xprint=lambda *args: None)
                    exec(compile(ast.Module(body=[constructor], type_ignores=[]), "speakers_separator.py", "exec"), ns)
                    instance = types.SimpleNamespace()
                    ns["__init__"](instance)
                    self.assertIs(instance.pipeline, pipe)
            self.assertEqual({Path(call.args[0]) for call in model.call_args_list}, {embedding, segmentation})
            self.assertEqual(embedding.read_bytes(), b"embedding-original")
            self.assertEqual(download.call_count, int(layout == "missing"))
            if layout == "missing":
                self.assertEqual(segmentation.read_bytes(), b"downloaded-segmentation")
                self.assertFalse((linked / "pyannote" / filenames[1]).exists())

    def test_configured_primary_and_split_linked_models_need_no_download(self):
        for consumer in ("analysis", "speakers"):
            for layout in ("primary", "split"):
                with self.subTest(consumer=consumer, layout=layout):
                    self.exercise(consumer, layout)

    def test_missing_model_downloads_only_to_primary_and_keeps_linked_embedding(self):
        self.exercise("analysis", "missing")

    def test_direct_separator_constructor_resolves_app_imports_without_pythonpath(self):
        source = Path(__file__).resolve().parents[1] / "app/preprocessing/speakers_separator.py"
        script = """
import ast,sys,types
from pathlib import Path
from unittest.mock import Mock
source=Path(sys.argv[1]);tree=ast.parse(source.read_text())
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OptimizedPyannote31SpeakerSeparator')
constructor=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
model=Mock(side_effect=lambda path: Path(path).read_bytes());pipe=Mock()
sys.modules['pyannote.audio']=types.SimpleNamespace(Model=types.SimpleNamespace(from_pretrained=model))
sys.modules['pyannote.audio.pipelines']=types.SimpleNamespace(SpeakerDiarization=Mock(return_value=pipe))
ns=dict(__file__=str(source),Path=Path,torch=types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda:False)),xprint=lambda *args:None)
exec(compile(ast.Module(body=[constructor],type_ignores=[]),str(source),'exec'),ns)
instance=types.SimpleNamespace();ns['__init__'](instance)
assert instance.pipeline is pipe and model.call_count==2
"""
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "ckpts/pyannote"
            folder.mkdir(parents=True)
            for name in ("pyannote_model_wespeaker-voxceleb-resnet34-LM.bin", "pytorch_model_segmentation-3.0.bin"):
                (folder / name).write_bytes(b"local-model")
            result = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(source)],
                cwd=temporary, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
