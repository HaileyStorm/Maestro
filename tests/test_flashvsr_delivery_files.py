"""Model-free delivery publication and CPU codec regressions."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def load_functions(*names, **namespace):
    names = (*names, "_run_delivery_encoder")
    tree = ast.parse((ROOT / "app/launch.py").read_text())
    module = ast.Module(body=[node for node in tree.body
                              if isinstance(node, ast.FunctionDef) and node.name in names],
                        type_ignores=[])
    namespace.setdefault("os", os)
    namespace.setdefault("is_cancel_requested", lambda job: job.get("cancelled", False))
    exec(compile(module, "delivery-functions", "exec"), namespace)
    return namespace


class DeliveryPublicationTests(unittest.TestCase):
    def run_upscale(self, *, audio=False, mux_failure=False, replace_failure=False,
                    cancel_after_probe=False, bad_mux=False):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / ".native.work.mp4"
            source.write_bytes(b"original")
            temp = Path(folder) / ".encoded.mp4"
            temp.write_bytes(b"upscaled")
            cancelled = False
            tracks = ["audio"] if audio else []
            cleanup = Mock()

            def mux(video, audio, dest, **kwargs):
                Path(dest).write_bytes(b"muxed")
                if mux_failure:
                    raise RuntimeError("mux failure")

            def probe(path):
                nonlocal cancelled
                if cancel_after_probe:
                    cancelled = True
                return (1, 1) if bad_mux and str(path) != str(temp) else (2688, 1536)

            wgp = SimpleNamespace(
                extract_audio_tracks=lambda _: (tracks, {}),
                cleanup_temp_audio_files=cleanup,
                server_config={"video_container": "mp4"},
                combine_video_with_audio_tracks=mux,
            )
            ns = load_functions("_apply_spatial_upsampling_to_file", wgp=wgp,
                                _chunked_flashvsr_upscale=lambda *a, **k: str(temp),
                                _coded_video_size=probe)
            original_replace = os.replace
            def replace(src, dst):
                self.assertEqual(source.read_bytes(), b"original")
                if replace_failure:
                    raise OSError("replacement unavailable")
                original_replace(src, dst)
            failed = mux_failure or replace_failure or cancel_after_probe or bad_mux
            with patch.object(os, "replace", side_effect=replace):
                if failed:
                    with self.assertRaises((RuntimeError, OSError, InterruptedError)):
                        ns["_apply_spatial_upsampling_to_file"](
                            str(source), "flashvsr2", abort_check=lambda: cancelled)
                else:
                    ns["_apply_spatial_upsampling_to_file"](
                        str(source), "flashvsr2", abort_check=lambda: cancelled)
            self.assertEqual(source.read_bytes(), b"original" if failed else
                             (b"muxed" if audio else b"upscaled"))
            self.assertEqual(list(Path(folder).iterdir()), [source])
            cleanup.assert_called_once_with(tracks)

    def test_atomic_publication_with_and_without_audio(self):
        for audio in (False, True):
            with self.subTest(audio=audio):
                self.run_upscale(audio=audio)

    def test_failures_and_late_cancellation_preserve_original_and_cleanup(self):
        for kwargs in ({"audio": True, "mux_failure": True},
                       {"audio": True, "bad_mux": True},
                       {"replace_failure": True}, {"audio": True, "replace_failure": True},
                       {"cancel_after_probe": True},
                       {"audio": True, "cancel_after_probe": True}):
            with self.subTest(**kwargs):
                self.run_upscale(**kwargs)

    def test_segment_geometry_rejects_truncated_frame_count(self):
        metadata = {"width": 2016, "height": 1152, "frame_count": 1}
        module = SimpleNamespace(probe_video_stream_metadata=lambda _: metadata)
        ns = load_functions("_coded_video_size")
        with patch.dict(sys.modules, {"shared.utils.video_decode": module}):
            with self.assertRaisesRegex(RuntimeError, "unexpected frame count"):
                ns["_coded_video_size"]("segment.mp4", expected_frames=2)
            metadata["frame_count"] = 2
            self.assertEqual(ns["_coded_video_size"]("segment.mp4", expected_frames=2), (2016, 1152))

    def test_api_deferral_uses_the_bridge_classifier(self):
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        runner = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_generation")
        branch = next(n for n in ast.walk(runner) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "gen_mode != 'image'"
                      and any(isinstance(child, ast.Name) and child.id == "_su_val" for child in ast.walk(n)))
        for value, supported in (("flashvsr2", True), ("FlashVSR2", True),
                                  (" FLASHVSR2 ", True), ("not-flashvsr", False)):
            classify = Mock(return_value=supported)
            params = {"spatial_upsampling": value}
            ns = {"gen_mode": "video", "raw_params": params, "pp_spatial_upsampling": None,
                  "wgp": SimpleNamespace(flashvsr=SimpleNamespace(is_upsampling=classify))}
            exec(compile(ast.Module(body=[branch], type_ignores=[]), "api-deferral", "exec"), ns)
            classify.assert_called_once_with(value)
            self.assertEqual(ns["pp_spatial_upsampling"], value if supported else None)
            self.assertEqual("spatial_upsampling" in params, not supported)

    def test_display_metadata_does_not_authorize_enlargement(self):
        ns = load_functions("_apply_delivery_fit_to_file",
                            _coded_video_size=lambda _: (1344, 768),
                            wgp=SimpleNamespace(get_video_info=lambda _: (24, 2688, 1536, 2)))
        for fit in ("upscale_exact", "center_crop"):
            with self.subTest(fit=fit), patch.dict(ns, {"_run_delivery_encoder": Mock()}) as patched:
                with self.assertRaises(RuntimeError):
                    ns["_apply_delivery_fit_to_file"]("native.mp4", "2688x1536", fit)
                ns["_run_delivery_encoder"].assert_not_called()

    def test_fit_encode_failure_is_path_free_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            source.write_bytes(b"original")
            ns = load_functions("_apply_delivery_fit_to_file", _coded_video_size=lambda _: (2016,1152))
            def fail(command, **kwargs):
                Path(command[-1]).write_bytes(b"partial")
                return 1
            with patch.dict(ns, {"_run_delivery_encoder": fail}):
                with self.assertRaisesRegex(RuntimeError, "^Exact delivery fit failed while encoding the video$"):
                    ns["_apply_delivery_fit_to_file"](str(source), "1920x1080", "center_crop")
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_fit_late_cancellation_and_wrong_canvas_preserve_source(self):
        for wrong_size in (False, True):
            with self.subTest(wrong_size=wrong_size), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "source.mp4"
                source.write_bytes(b"original")
                job = {}
                def probe(path):
                    if path == str(source):
                        return (2016, 1152)
                    job["cancelled"] = not wrong_size
                    return (1280, 720) if wrong_size else (1920, 1080)
                ns = load_functions("_apply_delivery_fit_to_file", _coded_video_size=probe)
                def encode(command, **kwargs):
                    Path(command[-1]).write_bytes(b"encoded")
                    return 0
                with patch.dict(ns, {"_run_delivery_encoder": encode}):
                    with self.assertRaises((RuntimeError, InterruptedError)):
                        ns["_apply_delivery_fit_to_file"](str(source), "1920x1080", "center_crop", job=job)
                self.assertEqual(source.read_bytes(), b"original")
                self.assertEqual(list(Path(folder).iterdir()), [source])


class EncoderCancellationTests(unittest.TestCase):
    def test_cancel_and_timeout_reap_owned_child(self):
        ns = load_functions("_run_delivery_encoder")
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                processes = []
                real_popen = subprocess.Popen
                def launch(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    return process
                with patch.object(subprocess, "Popen", side_effect=launch):
                    with self.assertRaises(InterruptedError if cancel else TimeoutError):
                        ns["_run_delivery_encoder"](
                            [sys.executable, "-c", "import time; time.sleep(30)"],
                            timeout=0.1, abort_check=lambda: cancel and bool(processes))
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_pre_cancel_never_starts_child(self):
        ns = load_functions("_run_delivery_encoder")
        with patch.object(subprocess, "Popen") as launch:
            with self.assertRaises(InterruptedError):
                ns["_run_delivery_encoder"](["ffmpeg"], timeout=1, abort_check=lambda: True)
            launch.assert_not_called()


class ChunkOwnershipTests(unittest.TestCase):
    def test_release_precedes_cache_and_failed_encode_cleans_owned_scratch(self):
        events = []
        class Frames:
            shape = (2, 4, 4, 3)
            dtype = "uint8"
            def permute(self, *args):
                return self
            def __getitem__(self, index):
                return self
        torch = SimpleNamespace(uint8="uint8", cuda=SimpleNamespace(
            is_available=lambda: True, empty_cache=lambda: events.append("cache")))
        def release():
            events.append("release")
        def save(**kwargs):
            events.append("encode")
            Path(kwargs["save_file"]).write_bytes(b"partial")
            raise RuntimeError("encode failed")
        wgp = SimpleNamespace(
            server_config={"flashvsr_persistence": 0}, loaded_profile=5,
            release_generation_residency_for_postprocess=release,
            release_flashvsr_vram=lambda: events.append("release_flashvsr"),
            get_resampled_video=lambda *args: Frames(), save_video=save,
            process_files_def=None, vae_config=None, init_pipe=None,
            flashvsr=SimpleNamespace(
                scale_for_upsampling=lambda _: 2,
                settings=lambda: (True, "tiny-long", 0),
                PERSIST_RAM=1, PERSIST_UNLOAD=0,
                upscale=lambda *args, **kwargs: (Frames(), None)))
        modules = {
            "torch": torch,
            "shared.utils.utils": SimpleNamespace(get_video_info=lambda _: (24, 4, 4, 2)),
            "postprocessing.flashvsr.runtime": SimpleNamespace(FLASHVSR_CONTINUE_CACHE_FRAMES=11),
        }
        ns = load_functions("_chunked_flashvsr_upscale", wgp=wgp, update_job=Mock())
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, modules):
            source = Path(folder) / "source.mp4"
            source.write_bytes(b"original")
            with self.assertRaisesRegex(RuntimeError, "encode failed"):
                ns["_chunked_flashvsr_upscale"](str(source), "flashvsr2")
            self.assertEqual(events, ["release", "cache", "encode", "release_flashvsr"])
            self.assertEqual(list(Path(folder).iterdir()), [source])
            self.assertEqual(wgp.server_config["flashvsr_persistence"], 0)
            events.clear()
            def partial_save(**kwargs):
                Path(kwargs["save_file"]).write_bytes(b"probeable partial")
                return None
            wgp.save_video = partial_save
            with self.assertRaisesRegex(RuntimeError, "Could not encode"):
                ns["_chunked_flashvsr_upscale"](str(source), "flashvsr2")
            self.assertEqual(list(Path(folder).iterdir()), [source])
            self.assertEqual(source.read_bytes(), b"original")
            events.clear()
            wgp.release_generation_residency_for_postprocess = Mock(side_effect=RuntimeError("release failed"))
            with self.assertRaisesRegex(RuntimeError, "release failed"):
                ns["_chunked_flashvsr_upscale"](str(source), "flashvsr2")
            self.assertEqual(events, [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU ffmpeg/ffprobe required")
class DeliveryCodecTests(unittest.TestCase):
    def test_hidden_source_coded_canvas_square_pixels_and_audio(self):
        ns = load_functions("_apply_delivery_fit_to_file", "_coded_video_size")
        for dimensions, target, fit in (("2016x1152", "1920x1080", "center_crop"),
                                         ("2688x1536", "2688x1536", "upscale_exact"),
                                         ("1920x1080", "1920x1080", "center_crop")):
            with self.subTest(fit=fit, dimensions=dimensions), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / ".protected.work.mp4"
                subprocess.run([
                    "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",
                    "-i", f"color=c=blue:s={dimensions}:r=2:d=1", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                    "-vf", "setsar=2/3", "-c:v", "libx264", "-threads", "1",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(source),
                ], check=True, capture_output=True, timeout=30)
                ns["_apply_delivery_fit_to_file"](str(source), target, fit)
                data = json.loads(subprocess.check_output([
                    "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)], timeout=15))
                video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
                self.assertEqual((video["width"], video["height"]), tuple(map(int, target.split("x"))))
                self.assertEqual(video["sample_aspect_ratio"], "1:1")
                self.assertEqual(int(video["nb_frames"]), 2)
                self.assertTrue(any(stream["codec_type"] == "audio" for stream in data["streams"]))
                self.assertEqual(list(Path(folder).iterdir()), [source])


if __name__ == "__main__":
    unittest.main()
