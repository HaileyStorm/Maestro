"""Actual CPU media replay and asset-selection fences; no native weight proof."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_gallery_control as gallery
from services import h3_gallery_av_guide as av
from services.h3_control_plan import plan_h3_control_request


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class GalleryControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="h3-control-cpu-")
        cls.root = Path(cls.storage.name)
        cls.video = cls.root / "bands.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=black:s=96x64:r=24:d=1,drawbox=x=0:y=0:w=32:h=64:color=red:t=fill,"
            "drawbox=x=32:y=0:w=32:h=64:color=lime:t=fill,drawbox=x=64:y=0:w=32:h=64:color=blue:t=fill",
            "-c:v", "libx264", "-crf", "0", "-threads", "1", "-pix_fmt", "yuv420p", str(cls.video)],
            check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.probe = av.probe_gallery_av(str(cls.video), "video")
        cls.original_contract = gallery._asset_contract()

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    def setUp(self):
        self.assets = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.assets.cleanup)
        self.directory = Path(self.assets.name)
        self.base = self.directory / "base"
        self.base.mkdir()
        shards = []
        for name, _, _ in self.original_contract[0]:
            data = ("fixture:" + name).encode()
            (self.base / name).write_bytes(data)
            shards.append((name, len(data), hashlib.sha256(data).hexdigest()))
        index = json.dumps({"weight_map": {f"key{n}": row[0] for n, row in enumerate(shards)}}).encode()
        (self.base / gallery._INDEX_NAME).write_bytes(index)
        self.control = self.directory / gallery._CONTROL_NAME
        self.control.write_bytes(b"small Control identity fixture")
        self.contract = (tuple(shards), hashlib.sha256(index).hexdigest(), self.control.stat().st_size,
                         hashlib.sha256(self.control.read_bytes()).hexdigest())
        self.addCleanup(patch.stopall)
        patch.object(gallery, "_asset_contract", return_value=self.contract).start()
        self.environment = {
            "MAESTRO_H3_CONTROL_EXPERIMENTAL": "1", "MAESTRO_H3_CONTROL_GALLERY_EXPERIMENTAL": "1",
            "MAESTRO_H3_CONTROL_BASE_CHECKPOINT": str(self.base / shards[0][0]),
            "MAESTRO_H3_CONTROL_CHECKPOINT": str(self.control)}
        patch.dict(os.environ, self.environment).start()
        self.record = dict(asdict(self.probe), workspace="project", name="bands.mp4", revision="r1",
                           source_private=True, source_explicit=True)
        self.plan = self.make_plan()
        self.binding = gallery.make_gallery_control_source(
            self.record, self.plan, gallery.control_assets_from_environment()["binding"])

    def make_plan(self, record=None, **kwargs):
        source = self.record if record is None else record
        values = dict(control_kind="canny", source_sha256=source["sha256"], source_width=source["width"],
            source_height=source["height"], source_frame_count=source["frame_count"], target_width=96,
            target_height=96, strength=0.7)
        values.update(kwargs)
        return plan_h3_control_request(**values)

    def decode(self, **kwargs):
        return gallery.decode_gallery_control_source(str(self.video), self.binding, self.plan, **kwargs)

    def dispatch(self):
        return gallery.make_gallery_control_dispatch(str(self.video), self.binding, self.plan)

    def validate(self, dispatch):
        return gallery.validate_gallery_control_dispatch(dispatch, frame_num=22, height=64, width=96)

    def test_real_full_image_unit_range_and_legal_prefix(self):
        import torch
        pixels = self.decode()
        self.assertEqual(self.probe.frame_count, 24)
        self.assertEqual(tuple(pixels.shape), (1, 3, 22, 64, 96))
        self.assertEqual(pixels.dtype, torch.float32)
        self.assertEqual(pixels.device.type, "cpu")
        self.assertGreaterEqual(pixels.min().item(), 0)
        self.assertLessEqual(pixels.max().item(), 1)
        labels = pixels[0, :, 0, 32].argmax(dim=0)
        self.assertEqual([int((labels == channel).sum()) for channel in range(3)], [32, 32, 32])
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(self.video), "-vf",
            "fps=fps=24:round=near,scale=96:64,setsar=1", "-frames:v", "22", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1"], timeout=20)
        independent = torch.frombuffer(bytearray(raw), dtype=torch.uint8).reshape(22, 64, 96, 3)
        self.assertTrue(torch.equal(pixels[0].permute(1, 2, 3, 0), independent.float().div(255)))

    def test_source_bytes_and_all_frame_geometry_commitment(self):
        changed = self.directory / "changed.mp4"
        changed.write_bytes(self.video.read_bytes() + b"changed tail outside selected prefix")
        with self.assertRaisesRegex(gallery.H3GalleryControlError, "bytes changed"):
            gallery.decode_gallery_control_source(str(changed), self.binding, self.plan)
        for key, value in (("width", 128), ("frame_count", 23)):
            with self.subTest(key=key):
                record = {**self.record, key: value}
                plan = self.make_plan(record)
                binding = gallery.make_gallery_control_source(record, plan, self.binding["assets"])
                with self.assertRaisesRegex(gallery.H3GalleryControlError, "geometry or full frame count"):
                    gallery.decode_gallery_control_source(str(self.video), binding, plan)

    def test_private_precomputed_only_and_exact_envelopes(self):
        for record in ({**self.record, "frame_index": 0}, {**self.record, "sample_count": 3}):
            with self.subTest(record=record), self.assertRaises(gallery.H3GalleryControlError):
                gallery.make_gallery_control_source(record, self.plan, self.binding["assets"])
        for plan in (self.make_plan(control_kind="inpaint", mask_sha256="sha256:" + "0" * 64),
                     self.make_plan(source_sha256="sha256:" + "0" * 64)):
            with self.assertRaises(gallery.H3GalleryControlError):
                gallery.make_gallery_control_source(self.record, plan, self.binding["assets"])
        zero = self.make_plan(strength=0)
        zero_binding = gallery.make_gallery_control_source(self.record, zero, self.binding["assets"])
        self.assertEqual(gallery.make_gallery_control_dispatch(str(self.video), zero_binding, zero).plan["control"]["strength"], 0)
        for binding in ({**self.binding, "extra": "private"}, {**self.binding, "plan_sha256": "sha256:" + "0" * 64}):
            with self.assertRaises(gallery.H3GalleryControlError):
                gallery.decode_gallery_control_source(str(self.video), binding, self.plan)
        self.record["revision"] = "later"
        self.assertEqual(self.binding["source"]["revision"], "r1")
        self.assertNotIn(str(self.root), json.dumps(self.binding))

    def test_source_privacy_and_explicit_flags_are_preserved_without_gate(self):
        for private, explicit in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(private=private, explicit=explicit):
                record = {**self.record, "source_private": private, "source_explicit": explicit}
                binding = gallery.make_gallery_control_source(record, self.plan, self.binding["assets"])
                dispatch = gallery.make_gallery_control_dispatch(str(self.video), binding, self.plan)
                self.assertEqual(dispatch.source_binding["source"], record)
                self.assertEqual(tuple(dispatch.video.shape), (1, 3, 22, 64, 96))

    def test_asset_replacement_and_selection_invalidates_dispatch(self):
        dispatch = self.dispatch()
        self.assertIs(self.validate(dispatch), dispatch)
        replacement = self.control.with_suffix(".new")
        replacement.write_bytes(self.control.read_bytes())
        replacement.replace(self.control)
        with self.assertRaisesRegex(gallery.H3GalleryControlError, "assets changed"):
            self.validate(dispatch)
        with self.assertRaisesRegex(gallery.H3GalleryControlError, "selection changed"):
            self.dispatch()

    def test_asset_roster_flags_and_unsafe_files_fail_before_decode(self):
        for flag in ("MAESTRO_H3_CONTROL_EXPERIMENTAL", "MAESTRO_H3_CONTROL_GALLERY_EXPERIMENTAL"):
            with patch.dict(os.environ, {flag: "0"}), self.assertRaises(gallery.H3GalleryControlError):
                gallery.control_assets_from_environment()
        shard = self.base / self.contract[0][-1][0]
        original = shard.read_bytes()
        shard.write_bytes(original + b"x")
        with self.assertRaises(gallery.H3GalleryControlError):
            gallery.control_assets_from_environment()
        shard.write_bytes(original)
        (self.base / gallery._INDEX_NAME).write_bytes(b"wrong index")
        with self.assertRaises(gallery.H3GalleryControlError):
            gallery.control_assets_from_environment()

    def test_asset_alias_sidecar_and_same_bytes_new_selection(self):
        alias = self.directory / "alias"
        alias.mkdir()
        clone = alias / gallery._CONTROL_NAME
        clone.write_bytes(self.control.read_bytes())
        before = gallery.control_assets_from_environment()["binding"]
        with patch.dict(os.environ, {"MAESTRO_H3_CONTROL_CHECKPOINT": str(clone)}):
            after = gallery.control_assets_from_environment()["binding"]
            self.assertNotEqual(before, after)
            with self.assertRaisesRegex(gallery.H3GalleryControlError, "selection changed"):
                self.dispatch()
        sidecar = self.control.with_name(self.control.stem + "_map.json")
        sidecar.write_text("{}")
        with self.assertRaises(gallery.H3GalleryControlError):
            gallery.control_assets_from_environment()
        sidecar.unlink()
        self.control.unlink()
        self.control.symlink_to(clone)
        with self.assertRaises(gallery.H3GalleryControlError):
            gallery.control_assets_from_environment()

    def test_cancellation_during_stream_reaps_owned_child(self):
        processes = []
        original = subprocess.Popen
        cancelled = False
        def start(command, *args, **kwargs):
            nonlocal cancelled
            process = original(command, *args, **kwargs)
            processes.append(process)
            cancelled = "-frames:v" in command
            return process
        with patch.object(av.subprocess, "Popen", side_effect=start):
            with self.assertRaises(InterruptedError):
                self.decode(cancel_check=lambda: cancelled)
        self.assertTrue(processes)
        self.assertTrue(cancelled)
        self.assertTrue(all(process.poll() is not None for process in processes))
        with self.assertRaises(InterruptedError):
            self.decode(cancel_check=lambda: True)

    def test_decode_budget_and_redacted_path_failures(self):
        with patch.object(av, "MAX_DECODED_BYTES", 1), patch.object(av, "_snapshot") as snapshot:
            with self.assertRaisesRegex(gallery.H3GalleryControlError, "limit"):
                self.decode()
            snapshot.assert_not_called()
        missing = str(self.directory / "secret-owner-path.mp4")
        with self.assertRaises(gallery.H3GalleryControlError) as error:
            gallery.decode_gallery_control_source(missing, self.binding, self.plan)
        self.assertNotIn(str(self.directory), str(error.exception))

    def test_transport_rejects_untrusted_type_and_pixels(self):
        import torch
        dispatch = self.dispatch()
        tail_nan = dispatch.video.clone()
        tail_nan[0, 0, -1, 0, 0] = float("nan")
        for video in (dispatch.video * 2, dispatch.video - 1, torch.full_like(dispatch.video, float("nan")),
                      tail_nan, dispatch.video.to(torch.uint8), dispatch.video.squeeze(0), dispatch.video.clone().requires_grad_()):
            with self.subTest(shape=video.shape), self.assertRaises(gallery.H3GalleryControlError):
                self.validate(replace(dispatch, video=video))
        self.assertIsNotNone(self.validate(replace(dispatch, video=dispatch.video.to(torch.float64))))
        with self.assertRaises(gallery.H3GalleryControlError):
            self.validate(asdict(dispatch))
        with self.assertRaises(gallery.H3GalleryControlError):
            gallery.validate_gallery_control_dispatch(dispatch, frame_num=24, height=64, width=96)
        with patch.dict(os.environ, {"MAESTRO_H3_CONTROL_GALLERY_EXPERIMENTAL": "0"}), self.assertRaises(gallery.H3GalleryControlError):
            self.validate(dispatch)


if __name__ == "__main__":
    unittest.main()
