from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from services.h3_reference_binding import (
    H3ReferenceBindingError,
    build_h3_reference_binding,
    cleanup_h3_reference_files,
    cleanup_orphan_h3_reference_files,
    materialize_h3_reference_image,
    materialize_h3_reference_files,
)


def _descriptor(field, path, *, digit="a", size=7, **extra):
    return {
        "field": field,
        "path": path,
        "sha256": digit * 64,
        "size": size,
        **extra,
    }


class H3ReferenceBindingTests(unittest.TestCase):
    def build(self, raw, descriptors, *, validate=lambda descriptor: True,
              has_audio=lambda path: True):
        return build_h3_reference_binding(
            raw,
            descriptors=descriptors,
            validate_descriptor=validate,
            has_audio=has_audio,
        )

    def test_binds_images_videos_and_audio_in_mapper_order(self):
        raw = {
            "image_refs": ["one.png", "two.png"],
            "video_prompt_type": "V+-",
            "video_guide": "one.mp4",
            "video_guide2": "two.mp4",
            "audio_prompt_type": "AC",
            "audio_guide": "one.wav",
            "audio_guide3": "three.wav",
        }
        descriptors = [
            _descriptor("audio_guide3:0", "three.wav", digit="6"),
            _descriptor("video_guide2:0", "two.mp4", digit="4"),
            _descriptor("image_refs:0", "one.png", digit="1"),
            _descriptor("audio_guide:0", "one.wav", digit="5"),
            _descriptor("image_refs:1", "two.png", digit="2"),
            _descriptor("video_guide:0", "one.mp4", digit="3"),
        ]
        manifest = self.build(raw, descriptors)
        self.assertEqual(
            [(item["type"], item["source_key"]) for item in manifest],
            [
                ("image", "image_refs:0"),
                ("image", "image_refs:1"),
                ("video", "video_guide:0"),
                ("video", "video_guide2:0"),
                ("audio", "audio_guide:0"),
                ("audio", "audio_guide3:0"),
            ],
        )
        self.assertEqual(manifest[0]["sha256"], "1" * 64)
        self.assertEqual(manifest[0]["size"], 7)

    def test_native_slot_one_and_three_selection_stays_physical(self):
        raw = {
            "video_prompt_type": "V-",
            "video_guide": "one.mp4",
            "video_guide2": "stale-two.mp4",
            "video_guide3": "three.mp4",
            "audio_prompt_type": "",
        }
        descriptors = [
            _descriptor("video_guide3:0", "three.mp4", digit="3"),
            _descriptor("video_guide:0", "one.mp4", digit="1"),
        ]
        manifest = self.build(raw, descriptors)
        self.assertEqual(
            [item["source_key"] for item in manifest],
            ["video_guide:0", "video_guide3:0"],
        )

    def test_k_pairs_selected_video_soundtracks_and_suppresses_stale_abc(self):
        probed = []
        raw = {
            "image_refs": ["image.png"],
            "video_prompt_type": "V-",
            "video_guide": "one.mp4",
            "video_guide3": "three.mp4",
            "audio_prompt_type": "KABC",
            "audio_guide": object(),
            "audio_guide2": "stale-two.wav",
            "audio_guide3": None,
        }
        descriptors = [
            _descriptor("image_refs:0", "image.png"),
            _descriptor("video_guide:0", "one.mp4", digit="b"),
            _descriptor("video_guide3:0", "three.mp4", digit="c"),
            _descriptor("audio_guide2:0", "stale-two.wav", digit="d"),
        ]
        manifest = self.build(
            raw, descriptors,
            has_audio=lambda path: probed.append(path) or True,
        )
        self.assertEqual(probed, ["one.mp4", "three.mp4"])
        self.assertEqual([item["type"] for item in manifest], ["image", "video", "video"])
        self.assertTrue(all(
            item.get("include_audio") is True and item.get("has_audio") is True
            for item in manifest if item["type"] == "video"
        ))
        self.assertTrue(all("audio_path" not in item for item in manifest))

    def test_callback_exact_true_is_required_after_field_and_path_match(self):
        descriptor = _descriptor("image_refs:0", "image.png")
        seen = []
        for result in (False, None, 1, "true"):
            with self.subTest(result=result), self.assertRaisesRegex(
                H3ReferenceBindingError, "no longer authorized"
            ):
                self.build(
                    {"image_refs": ["image.png"]}, [descriptor],
                    validate=lambda value, result=result: seen.append(value) or result,
                )
        self.assertEqual(len(seen), 4)
        self.assertEqual(seen[0], descriptor)

    def test_missing_stale_and_duplicate_selected_descriptors_fail_closed(self):
        raw = {"image_refs": ["current.png"]}
        cases = (
            ([], "no frozen"),
            ([_descriptor("image_refs:0", "old.png")], "frozen path"),
            ([
                _descriptor("image_refs:0", "current.png"),
                _descriptor("image_refs:0", "current.png", digit="b"),
            ], "ambiguous"),
        )
        for descriptors, phrase in cases:
            with self.subTest(phrase=phrase), self.assertRaisesRegex(
                H3ReferenceBindingError, phrase
            ):
                self.build(raw, descriptors)

    def test_hash_size_and_selected_path_values_are_strict(self):
        base = _descriptor("image_refs:0", "image.png")
        cases = (
            ({**base, "sha256": "A" * 64}, "SHA-256"),
            ({**base, "sha256": "a" * 63}, "SHA-256"),
            ({**base, "size": True}, "byte size"),
            ({**base, "size": -1}, "byte size"),
        )
        for descriptor, phrase in cases:
            with self.subTest(descriptor=descriptor), self.assertRaisesRegex(
                H3ReferenceBindingError, phrase
            ):
                self.build({"image_refs": ["image.png"]}, [descriptor])
        with self.assertRaisesRegex(H3ReferenceBindingError, "exact path"):
            self.build(
                {"image_refs": [" image.png"]},
                [_descriptor("image_refs:0", " image.png")],
            )

    def test_malformed_raw_inputs_flags_and_descriptor_collections_fail_closed(self):
        cases = (
            ([], [], "inputs must be an object"),
            ({"image_refs": ("image.png",)}, [], "image_refs must be a list"),
            ({"video_prompt_type": 1}, [], "video_prompt_type must be text"),
            ({"audio_prompt_type": []}, [], "audio_prompt_type must be text"),
            ({"source_audio_requested": 1}, [], "must be boolean"),
            ({}, "descriptor", "descriptors must be a sequence"),
            ({}, [None], "descriptor 1 must be an object"),
            ({}, [{"field": " field"}], "invalid field"),
        )
        for raw, descriptors, phrase in cases:
            with self.subTest(phrase=phrase), self.assertRaisesRegex(
                H3ReferenceBindingError, phrase
            ):
                self.build(raw, descriptors)

    def test_k_requires_strict_audio_proof_and_does_not_fabricate_wav(self):
        raw = {
            "video_prompt_type": "V-",
            "video_guide": "private.mp4",
            "audio_prompt_type": "K",
        }
        descriptor = _descriptor("video_guide:0", "private.mp4")
        for result in (False, None, 1):
            with self.subTest(result=result), self.assertRaisesRegex(
                H3ReferenceBindingError, "no authorized soundtrack"
            ):
                self.build(raw, [descriptor], has_audio=lambda path, result=result: result)
        manifest = self.build(raw, [descriptor])
        self.assertNotIn("audio_path", manifest[0])

    def test_k_audio_probe_cancellation_propagates(self):
        raw = {
            "video_prompt_type": "V-",
            "video_guide": "private.mp4",
            "audio_prompt_type": "K",
        }
        descriptor = _descriptor("video_guide:0", "private.mp4")
        for error_type in (InterruptedError, KeyboardInterrupt, SystemExit):
            error = error_type("cancel")
            with self.subTest(error_type=error_type), self.assertRaises(error_type) as caught:
                self.build(raw, [descriptor], has_audio=lambda path, error=error: (_ for _ in ()).throw(error))
            self.assertIs(caught.exception, error)

    def test_k_audio_probe_error_hides_private_details_and_retains_cause(self):
        raw = {
            "video_prompt_type": "V-",
            "video_guide": "/private/reference.mp4",
            "audio_prompt_type": "K",
        }
        descriptor = _descriptor(
            "video_guide:0", "/private/reference.mp4"
        )
        error = RuntimeError(
            "/private/reference.mp4: ffprobe stderr includes private details"
        )
        with self.assertRaises(H3ReferenceBindingError) as caught:
            self.build(
                raw,
                [descriptor],
                has_audio=lambda path: (_ for _ in ()).throw(error),
            )
        self.assertEqual(
            str(caught.exception),
            "A selected reference video soundtrack could not be verified.",
        )
        self.assertNotIn("/private", str(caught.exception))
        self.assertNotIn("ffprobe", str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)

    def test_descriptor_validator_cancellation_propagates(self):
        descriptor = _descriptor("image_refs:0", "image.png")
        for error_type in (InterruptedError, KeyboardInterrupt, SystemExit):
            error = error_type("cancel")
            with self.subTest(error_type=error_type), self.assertRaises(error_type) as caught:
                self.build(
                    {"image_refs": ["image.png"]}, [descriptor],
                    validate=lambda value, error=error: (_ for _ in ()).throw(error),
                )
            self.assertIs(caught.exception, error)

    def test_descriptor_validator_error_hides_private_details_and_retains_cause(self):
        descriptor = _descriptor("image_refs:0", "/private/image.png")
        error = RuntimeError("/private/image.png: access-sidecar diagnostic")
        with self.assertRaises(H3ReferenceBindingError) as caught:
            self.build(
                {"image_refs": ["/private/image.png"]},
                [descriptor],
                validate=lambda value: (_ for _ in ()).throw(error),
            )
        self.assertEqual(
            str(caught.exception),
            "Selected H3 reference image_refs:0 could not be revalidated.",
        )
        self.assertNotIn("/private", str(caught.exception))
        self.assertNotIn("sidecar", str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)

    def test_no_roles_or_intents_are_inferred(self):
        manifest = self.build(
            {
                "image_refs": ["person.png"],
                "audio_prompt_type": "A",
                "audio_guide": "voice.wav",
            },
            [
                _descriptor("image_refs:0", "person.png"),
                _descriptor("audio_guide:0", "voice.wav", digit="b"),
            ],
        )
        for item in manifest:
            self.assertFalse(set(item) & {"role", "image_intent", "audio_intent"})

    def test_late_dependency_is_snapshotted_without_interpretation(self):
        metadata = {"dependency": "unit-7", "mode": "temporal_tail", "video_slot": 3}
        descriptor = _descriptor(
            "video_guide3:0", "tail.mp4", digit="b",
            late_dependency=metadata,
        )
        manifest = self.build(
            {
                "video_prompt_type": "V-",
                "video_guide3": "tail.mp4",
            },
            [descriptor],
        )
        self.assertEqual(manifest[0]["late_dependency"], metadata)
        metadata["mode"] = "changed"
        descriptor["late_dependency"]["video_slot"] = 1
        self.assertEqual(
            manifest[0]["late_dependency"],
            {"dependency": "unit-7", "mode": "temporal_tail", "video_slot": 3},
        )

        direct = _descriptor(
            "video_guide3:0", "tail.mp4", digit="b", dependency="unit-8"
        )
        direct_manifest = self.build(
            {"video_prompt_type": "V-", "video_guide3": "tail.mp4"},
            [direct],
        )
        self.assertEqual(
            direct_manifest[0]["late_dependency"], {"dependency": "unit-8"}
        )

        ambiguous = {**direct, "late_dependency": {"dependency": "unit-8"}}
        with self.assertRaisesRegex(H3ReferenceBindingError, "ambiguous late"):
            self.build(
                {"video_prompt_type": "V-", "video_guide3": "tail.mp4"},
                [ambiguous],
            )

    def test_inputs_and_descriptors_are_not_mutated(self):
        raw = {
            "image_refs": ["image.png"],
            "video_prompt_type": "V-",
            "video_guide": "video.mp4",
        }
        descriptors = [
            _descriptor("image_refs:0", "image.png"),
            _descriptor("video_guide:0", "video.mp4", digit="b", scope="upload"),
        ]
        raw_before = copy.deepcopy(raw)

        def mutate_validation_copy(descriptor):
            descriptor["path"] = "changed"
            descriptor.setdefault("nested", {})["changed"] = True
            return True

        descriptors[1]["nested"] = {"preserved": True}
        descriptors_before = copy.deepcopy(descriptors)
        self.build(raw, descriptors, validate=mutate_validation_copy)
        self.assertEqual(raw, raw_before)
        self.assertEqual(descriptors, descriptors_before)

    def test_empty_binding_and_unsupported_source_audio(self):
        self.assertEqual(self.build({}, []), [])
        with self.assertRaisesRegex(H3ReferenceBindingError, "source audio"):
            self.build({"source_audio_requested": True}, [])

    def test_audio_to_visual_ratio_and_limits_fail_closed(self):
        raw = {
            "image_refs": ["one.png"],
            "audio_prompt_type": "AB",
            "audio_guide": "one.wav",
            "audio_guide2": "two.wav",
        }
        descriptors = [
            _descriptor("image_refs:0", "one.png"),
            _descriptor("audio_guide:0", "one.wav", digit="b"),
            _descriptor("audio_guide2:0", "two.wav", digit="c"),
        ]
        with self.assertRaisesRegex(H3ReferenceBindingError, "cannot exceed"):
            self.build(raw, descriptors)

        images = [f"image-{index}.png" for index in range(10)]
        descriptors = [
            _descriptor(f"image_refs:{index}", path, digit="d")
            for index, path in enumerate(images)
        ]
        with self.assertRaisesRegex(H3ReferenceBindingError, "at most 9"):
            self.build({"image_refs": images}, descriptors)

    def _authorized_image_binding(self, path):
        payload = Path(path).read_bytes()
        descriptor = {
            "field": "image_refs:0",
            "path": os.fspath(path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
        return self.build(
            {"image_refs": [os.fspath(path)]}, [descriptor]
        )[0]

    def _authorized_file_bindings(self, video_path, audio_path=None):
        video_payload = Path(video_path).read_bytes()
        raw = {
            "video_prompt_type": "V-",
            "video_guide": os.fspath(video_path),
            "audio_prompt_type": "A" if audio_path is not None else "",
        }
        descriptors = [{
            "field": "video_guide:0",
            "path": os.fspath(video_path),
            "sha256": hashlib.sha256(video_payload).hexdigest(),
            "size": len(video_payload),
        }]
        if audio_path is not None:
            audio_payload = Path(audio_path).read_bytes()
            raw["audio_guide"] = os.fspath(audio_path)
            descriptors.append({
                "field": "audio_guide:0",
                "path": os.fspath(audio_path),
                "sha256": hashlib.sha256(audio_payload).hexdigest(),
                "size": len(audio_payload),
            })
        return self.build(raw, descriptors)

    @unittest.skipIf(
        os.name == "nt",
        "Windows denies replacement while PIL retains the source handle",
    )
    def test_materializer_defeats_lazy_image_path_swap(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "reference.png"
            blue = Path(folder) / "blue.png"
            saved_red = Path(folder) / "saved-red.png"
            held_blue = Path(folder) / "held-blue.png"
            Image.new("RGB", (3, 2), (255, 0, 0)).save(target)
            Image.new("RGB", (3, 2), (0, 0, 255)).save(blue)
            binding = self._authorized_image_binding(target)

            os.replace(target, saved_red)
            os.replace(blue, target)
            lazy_blue = Image.open(target)
            try:
                os.replace(target, held_blue)
                os.replace(saved_red, target)
                pinned = materialize_h3_reference_image(binding)
                self.assertEqual(lazy_blue.getpixel((0, 0)), (0, 0, 255))
                self.assertEqual(pinned.getpixel((0, 0)), (255, 0, 0))
            finally:
                lazy_blue.close()

    def test_materializer_rejects_same_size_changed_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "reference.bmp"
            Image.new("RGB", (4, 4), (255, 0, 0)).save(target)
            binding = self._authorized_image_binding(target)
            original_size = target.stat().st_size
            Image.new("RGB", (4, 4), (0, 0, 255)).save(target)
            self.assertEqual(target.stat().st_size, original_size)

            with self.assertRaises(H3ReferenceBindingError) as caught:
                materialize_h3_reference_image(binding)
            self.assertEqual(
                str(caught.exception),
                "The selected H3 reference image could not be materialized.",
            )
            self.assertNotIn(os.fspath(target), str(caught.exception))

    def test_materialized_image_retains_no_source_file_handle(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "reference.png"
            Image.new("RGBA", (2, 2), (10, 20, 30, 40)).save(target)
            binding = self._authorized_image_binding(target)
            pinned = materialize_h3_reference_image(binding)

            self.assertIsNone(getattr(pinned, "fp", None))
            target.unlink()
            self.assertEqual(pinned.getpixel((1, 1)), (10, 20, 30, 40))

    def test_materializer_bounds_decode_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "private-invalid.png"
            target.write_bytes(b"not an image")
            binding = self._authorized_image_binding(target)
            with self.assertRaises(H3ReferenceBindingError) as caught:
                materialize_h3_reference_image(binding)
            self.assertEqual(
                str(caught.exception),
                "The selected H3 reference image could not be decoded.",
            )
            self.assertNotIn(os.fspath(target), str(caught.exception))

    def test_materializer_rejects_links_and_preserves_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "reference.png"
            link = Path(folder) / "linked.png"
            Image.new("RGB", (2, 2), (1, 2, 3)).save(target)
            os.link(target, link)
            binding = self._authorized_image_binding(target)
            with self.assertRaises(H3ReferenceBindingError):
                materialize_h3_reference_image(binding)

        binding = {
            "type": "image",
            "path": "/unreadable/private.png",
            "sha256": "a" * 64,
            "size": 1,
        }
        original_lstat = os.lstat
        for error_type in (InterruptedError, KeyboardInterrupt, SystemExit):
            error = error_type("cancel")
            try:
                os.lstat = lambda path, error=error: (_ for _ in ()).throw(error)
                with self.subTest(error_type=error_type), self.assertRaises(error_type) as caught:
                    materialize_h3_reference_image(binding)
            finally:
                os.lstat = original_lstat
            self.assertIs(caught.exception, error)

    def test_file_materializer_pins_video_and_audio_bytes_and_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.MP4"
            audio = root / "source.wav"
            video.write_bytes(b"original-video-bytes")
            audio.write_bytes(b"original-audio-bytes")
            bindings = self._authorized_file_bindings(video, audio)

            token = materialize_h3_reference_files(bindings, staging, "job-one")
            self.assertEqual(
                set(token), {"directory", "identity", "paths"}
            )
            self.assertEqual(
                set(token["paths"]), {"video_guide:0", "audio_guide:0"}
            )
            journals = list(staging.glob(".h3ref-job-one-*.json"))
            self.assertEqual(len(journals), 1)
            self.assertEqual(journals[0].name, token["identity"]["journal"]["name"])
            self.assertEqual(stat.S_IMODE(journals[0].stat().st_mode), 0o600)
            video_copy = Path(token["paths"]["video_guide:0"])
            audio_copy = Path(token["paths"]["audio_guide:0"])
            self.assertEqual(video_copy.suffix, ".MP4")
            self.assertEqual(audio_copy.suffix, ".wav")
            video.write_bytes(b"replacement-video!!")
            audio.write_bytes(b"replacement-audio!!")
            self.assertEqual(video_copy.read_bytes(), b"original-video-bytes")
            self.assertEqual(audio_copy.read_bytes(), b"original-audio-bytes")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(video_copy.stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE(audio_copy.stat().st_mode), 0o400)
                self.assertEqual(
                    stat.S_IMODE(Path(token["directory"]).stat().st_mode), 0o500
                )
            self.assertTrue(cleanup_h3_reference_files(token))
            self.assertFalse(Path(token["directory"]).exists())
            self.assertFalse(journals[0].exists())
            self.assertTrue(staging.is_dir())

    def test_file_materializer_rejects_post_bind_change_and_cleans_partial_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            audio = root / "source.wav"
            video.write_bytes(b"stable-video")
            audio.write_bytes(b"original-audio")
            bindings = self._authorized_file_bindings(video, audio)
            audio.write_bytes(b"replaced-audio")
            self.assertEqual(len(b"original-audio"), len(b"replaced-audio"))

            with self.assertRaises(H3ReferenceBindingError) as caught:
                materialize_h3_reference_files(bindings, staging, "job-change")
            self.assertEqual(
                str(caught.exception),
                "H3 reference files could not be materialized.",
            )
            self.assertNotIn(os.fspath(audio), str(caught.exception))
            self.assertEqual(list(staging.iterdir()), [])

    def test_file_cleanup_refuses_changed_identity_or_unexpected_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            video.write_bytes(b"video")
            bindings = self._authorized_file_bindings(video)
            token = materialize_h3_reference_files(bindings, staging, "job-guard")
            snapshot_dir = Path(token["directory"])
            copied = Path(token["paths"]["video_guide:0"])

            changed_identity = copy.deepcopy(token)
            changed_identity["identity"]["inode"] += 1
            self.assertFalse(cleanup_h3_reference_files(changed_identity))
            self.assertTrue(copied.exists())

            snapshot_dir.chmod(0o700)
            foreign = snapshot_dir / "foreign.txt"
            foreign.write_bytes(b"foreign")
            snapshot_dir.chmod(0o500)
            self.assertFalse(cleanup_h3_reference_files(token))
            self.assertTrue(copied.exists())
            self.assertEqual(foreign.read_bytes(), b"foreign")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(snapshot_dir.stat().st_mode), 0o500)
                self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o400)

            snapshot_dir.chmod(0o700)
            foreign.unlink()
            snapshot_dir.chmod(0o500)
            self.assertTrue(cleanup_h3_reference_files(token))

    def test_file_materializer_cleans_after_cancellation_and_preserves_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            video.write_bytes(b"video")
            bindings = self._authorized_file_bindings(video)
            original_read = os.read
            error = InterruptedError("cancel")
            source_identity = (video.stat().st_dev, video.stat().st_ino)
            interrupted = False
            journal_snapshot = None

            def interrupt_source_read(descriptor, size):
                nonlocal interrupted, journal_snapshot
                opened = os.fstat(descriptor)
                if not interrupted and (opened.st_dev, opened.st_ino) == source_identity:
                    interrupted = True
                    journal_path = next(staging.glob(".h3ref-job-cancel-*.json"))
                    journal_snapshot = json.loads(journal_path.read_bytes())
                    raise error
                return original_read(descriptor, size)

            try:
                os.read = interrupt_source_read
                with self.assertRaises(InterruptedError) as caught:
                    materialize_h3_reference_files(bindings, staging, "job-cancel")
            finally:
                os.read = original_read
            self.assertIs(caught.exception, error)
            self.assertEqual(
                set(journal_snapshot["token"]["identity"]["files"]),
                {"video_guide:0"},
            )
            self.assertEqual(
                set(journal_snapshot["token"]["identity"]["files"]["video_guide:0"]),
                {"name", "device", "inode"},
            )
            self.assertEqual(list(staging.iterdir()), [])

    def test_file_materializer_skips_images_and_rejects_duplicate_keys(self):
        self.assertEqual(
            materialize_h3_reference_files(
                [{"type": "image", "source_key": "image_refs:0"}],
                "/unused",
                "job-images",
            ),
            {"directory": None, "identity": None, "paths": {}},
        )
        binding = {
            "type": "video",
            "source_key": "video_guide:0",
            "path": "/canonical/video.mp4",
            "sha256": "a" * 64,
            "size": 1,
        }
        with self.assertRaisesRegex(H3ReferenceBindingError, "duplicate source key"):
            materialize_h3_reference_files(
                [binding, dict(binding)], "/unused", "job-duplicate",
            )

    def test_orphan_cleanup_preserves_live_job_then_removes_terminal_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            video.write_bytes(b"crash-pinned-video")
            bindings = self._authorized_file_bindings(video)
            token = materialize_h3_reference_files(bindings, staging, "job-live")
            snapshot = Path(token["directory"])
            journal = staging / token["identity"]["journal"]["name"]

            self.assertEqual(
                cleanup_orphan_h3_reference_files(staging, ["job-live"]), 0
            )
            self.assertTrue(snapshot.is_dir())
            self.assertTrue(journal.is_file())
            self.assertEqual(
                cleanup_orphan_h3_reference_files(staging, []), 1
            )
            self.assertFalse(snapshot.exists())
            self.assertFalse(journal.exists())

    def test_orphan_cleanup_fails_closed_for_tampered_journal_and_foreign_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            video.write_bytes(b"guarded-video")
            bindings = self._authorized_file_bindings(video)
            token = materialize_h3_reference_files(bindings, staging, "job-tamper")
            snapshot = Path(token["directory"])
            copied = Path(token["paths"]["video_guide:0"])
            journal = staging / token["identity"]["journal"]["name"]
            original_journal = journal.read_bytes()
            payload = json.loads(original_journal)
            payload["token"]["directory"] = os.fspath(root / "foreign")
            journal.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            foreign_target = root / "foreign-journal.json"
            foreign_target.write_text("{}", encoding="utf-8")
            linked_journal = staging / (".h3ref-job-link-" + "1" * 32 + ".json")
            linked_journal.symlink_to(foreign_target)

            self.assertEqual(cleanup_orphan_h3_reference_files(staging, []), 0)
            self.assertTrue(snapshot.is_dir())
            self.assertEqual(copied.read_bytes(), b"guarded-video")
            self.assertTrue(linked_journal.is_symlink())

            journal.write_bytes(original_journal)
            snapshot.chmod(0o700)
            foreign = snapshot / "foreign.txt"
            foreign.write_bytes(b"do-not-delete")
            snapshot.chmod(0o500)
            self.assertEqual(cleanup_orphan_h3_reference_files(staging, []), 0)
            self.assertEqual(foreign.read_bytes(), b"do-not-delete")
            self.assertTrue(journal.exists())

            snapshot.chmod(0o700)
            foreign.unlink()
            snapshot.chmod(0o500)
            self.assertEqual(cleanup_orphan_h3_reference_files(staging, []), 1)
            linked_journal.unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX hard-crash recovery")
    def test_hard_exit_before_second_file_journal_does_not_strand_first_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            audio = root / "source.wav"
            video.write_bytes(b"v" * (2 * 1024 * 1024))
            audio.write_bytes(b"second-source")
            script = textwrap.dedent("""
                import hashlib
                import os
                import sys
                import services.h3_reference_binding as binding

                staging, video, audio = sys.argv[1:]
                items = []
                for kind, source_key, path in (
                    ("video", "video_guide:0", video),
                    ("audio", "audio_guide:0", audio),
                ):
                    payload = open(path, "rb").read()
                    items.append({
                        "type": kind,
                        "source_key": source_key,
                        "path": path,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size": len(payload),
                    })
                original = binding._write_reference_journal
                calls = 0
                def crash_before_second_journal(token, **kwargs):
                    global calls
                    calls += 1
                    if calls == 3:
                        os._exit(71)
                    return original(token, **kwargs)
                binding._write_reference_journal = crash_before_second_journal
                binding.materialize_h3_reference_files(
                    items, staging, "job-prejournal-crash",
                )
            """)
            result = subprocess.run(
                [
                    sys.executable, "-c", script, os.fspath(staging),
                    os.fspath(video), os.fspath(audio),
                ],
                env={**os.environ, "PYTHONPATH": os.fspath(ROOT / "app")},
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 71, result.stderr.decode())
            snapshots = list(staging.glob(".h3-reference-*"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual((snapshots[0] / "01-video.mp4").stat().st_size, 2 * 1024 * 1024)
            residue = list(staging.glob(".h3ref-copy-*.tmp"))
            self.assertEqual(len(residue), 1)
            self.assertEqual(residue[0].stat().st_size, 0)

            self.assertEqual(cleanup_orphan_h3_reference_files(staging, []), 1)
            self.assertFalse(snapshots[0].exists())
            self.assertEqual(list(staging.glob(".h3ref-job-prejournal-crash-*.json")), [])
            self.assertTrue(residue[0].is_file())
            residue[0].unlink()

    @unittest.skipUnless(os.name == "posix", "POSIX hard-crash recovery")
    def test_hard_exit_after_first_unlink_resumes_terminal_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            audio = root / "source.wav"
            video.write_bytes(b"first-source")
            audio.write_bytes(b"second-source")
            script = textwrap.dedent("""
                import hashlib
                import os
                import sys
                import services.h3_reference_binding as binding

                staging, video, audio = sys.argv[1:]
                items = []
                for kind, source_key, path in (
                    ("video", "video_guide:0", video),
                    ("audio", "audio_guide:0", audio),
                ):
                    payload = open(path, "rb").read()
                    items.append({
                        "type": kind,
                        "source_key": source_key,
                        "path": path,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size": len(payload),
                    })
                token = binding.materialize_h3_reference_files(
                    items, staging, "job-unlink-crash",
                )
                original = binding.os.unlink
                def crash_after_first_unlink(path, *args, **kwargs):
                    result = original(path, *args, **kwargs)
                    if os.fspath(path).startswith(token["directory"] + os.sep):
                        os._exit(72)
                    return result
                binding.os.unlink = crash_after_first_unlink
                binding.cleanup_h3_reference_files(token)
            """)
            result = subprocess.run(
                [
                    sys.executable, "-c", script, os.fspath(staging),
                    os.fspath(video), os.fspath(audio),
                ],
                env={**os.environ, "PYTHONPATH": os.fspath(ROOT / "app")},
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 72, result.stderr.decode())
            snapshots = list(staging.glob(".h3-reference-*"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(len(list(snapshots[0].iterdir())), 1)
            journal = next(staging.glob(".h3ref-job-unlink-crash-*.json"))
            self.assertTrue(json.loads(journal.read_bytes())["cleanup_started"])

            self.assertEqual(cleanup_orphan_h3_reference_files(staging, []), 1)
            self.assertFalse(snapshots[0].exists())
            self.assertFalse(journal.exists())

    def test_cleanup_resumes_when_only_matching_journal_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "recovery-staging"
            staging.mkdir(mode=0o700)
            video = root / "source.mp4"
            video.write_bytes(b"video")
            token = materialize_h3_reference_files(
                self._authorized_file_bindings(video), staging, "job-rmdir",
            )
            journal = staging / token["identity"]["journal"]["name"]
            original_rmdir = os.rmdir

            def remove_then_fail(path, *args, **kwargs):
                original_rmdir(path, *args, **kwargs)
                raise OSError("simulated crash boundary")

            with mock.patch(
                "services.h3_reference_binding.os.rmdir", remove_then_fail,
            ):
                self.assertFalse(cleanup_h3_reference_files(token))
            self.assertFalse(Path(token["directory"]).exists())
            self.assertTrue(journal.exists())
            self.assertTrue(cleanup_h3_reference_files(token))
            self.assertFalse(journal.exists())

    def test_file_materializer_rejects_unsafe_job_and_unsupported_platform_early(self):
        binding = {
            "type": "video",
            "source_key": "video_guide:0",
            "path": "/canonical/video.mp4",
            "sha256": "a" * 64,
            "size": 1,
        }
        with self.assertRaisesRegex(H3ReferenceBindingError, "safe job ID"):
            materialize_h3_reference_files([binding], "/missing", "../unsafe")
        with mock.patch(
            "services.h3_reference_binding._POSIX_REFERENCE_FILE_SUPPORT", False,
        ):
            with self.assertRaisesRegex(H3ReferenceBindingError, "POSIX support"):
                materialize_h3_reference_files([binding], "/missing", "job-platform")
            self.assertFalse(cleanup_h3_reference_files({
                "directory": "/missing/.h3-reference-" + "1" * 32,
                "identity": {
                    "device": 1,
                    "inode": 1,
                    "files": {},
                    "job_id": "job-platform",
                    "nonce": "1" * 32,
                    "journal": {
                        "name": ".h3ref-job-platform-" + "1" * 32 + ".json",
                        "device": 1,
                        "inode": 1,
                    },
                },
                "paths": {},
            }))


if __name__ == "__main__":
    unittest.main()
