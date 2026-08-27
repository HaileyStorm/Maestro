"""Model-free tests for the unwired 10Eros H3 Beta3 runtime admission seam."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.minimax_h3 import beta3_runtime as runtime
from services.h3_10eros_beta3 import get_10eros_beta3_catalog
from services.h3_checkpoint_receipts import (
    CHECKPOINT_CONTRACT_REVISION,
    CHECKPOINT_RECEIPT_SCHEMA_VERSION,
)


def _request(artifact: dict, **changes) -> runtime.Beta3RuntimeRequest:
    value = runtime.Beta3RuntimeRequest(
        artifact_id=artifact["artifact_id"],
        profile_id=artifact["profile_id"],
        filename=artifact["filename"],
        authored_evaluations=6,
        sampler="er_sde/simple",
        attention_engine="sdpa",
    )
    return replace(value, **changes)


def _binding(artifact: dict, **changes) -> dict:
    value = {
        "schema_version": CHECKPOINT_RECEIPT_SCHEMA_VERSION,
        "contract_revision": CHECKPOINT_CONTRACT_REVISION,
        "family": "minimax_h3",
        "role": "transformer",
        "expected_sha256": artifact["sha256"],
        "expected_size": artifact["size"],
        "path_digest": "a" * 64,
        "dev": 1,
        "ino": 2,
        "size": artifact["size"],
        "mtime_ns": 3,
        "ctime_ns": 4,
        "uid": 5,
    }
    value.update(changes)
    return value


def _receipt(artifact: dict, *, binding: dict | None = None, **changes) -> dict:
    value = {
        "verified": True,
        "sha256": artifact["sha256"],
        "size": artifact["size"],
        "family": "minimax_h3",
        "role": "transformer",
        "contract_revision": CHECKPOINT_CONTRACT_REVISION,
        "compatibility": "10eros_beta3_turbo_hybrid_runtime_admission",
        "receipt_reused": True,
        "_checkpoint_binding": _binding(artifact) if binding is None else binding,
    }
    value.update(changes)
    return value


def _tiny_artifact(source: dict, size: int) -> dict:
    artifact = deepcopy(source)
    artifact["size"] = size
    artifact["sha256"] = "b" * 64
    return artifact


def _binding_for_path(path: Path, artifact: dict) -> dict:
    opened = path.stat()
    canonical = os.path.realpath(os.path.abspath(path))
    path_digest = hashlib.sha256(
        os.fsencode(os.path.normcase(canonical))
    ).hexdigest()
    return _binding(
        artifact,
        path_digest=path_digest,
        dev=int(opened.st_dev),
        ino=int(opened.st_ino),
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
        uid=int(getattr(opened, "st_uid", -1)),
    )


class _AlternatingPath:
    def __init__(self, first: str, second: str):
        self._values = (first, second)
        self.calls = 0

    def __fspath__(self) -> str:
        value = self._values[min(self.calls, 1)]
        self.calls += 1
        return value


class _DictSubclass(dict):
    pass


class _StringSubclass(str):
    pass


class H310ErosBeta3RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = get_10eros_beta3_catalog()["artifacts"]

    def _admit_mocked(self, artifact: dict, **request_changes):
        path = os.path.abspath(os.path.join("/synthetic", artifact["filename"]))
        with mock.patch.object(
            runtime, "verify_checkpoint_integrity", return_value=_receipt(artifact),
        ), mock.patch.object(
            runtime, "recheck_checkpoint_binding", return_value=True,
        ):
            return runtime.admit_beta3_runtime(
                path, _request(artifact, **request_changes),
            )

    def test_both_variants_use_exact_catalog_and_private_receipt_binding(self):
        for index, artifact in enumerate(self.artifacts):
            with self.subTest(artifact_id=artifact["artifact_id"]), mock.patch.object(
                runtime, "verify_checkpoint_integrity",
                return_value=_receipt(artifact),
            ) as verify, mock.patch.object(
                runtime, "recheck_checkpoint_binding", return_value=True,
            ) as recheck:
                path = Path("/synthetic") / artifact["filename"]
                normalized = os.path.abspath(path)
                sampler = "er_sde/simple" if index == 0 else "multires/simple"
                admission = runtime.admit_beta3_runtime(
                    path, _request(artifact, sampler=sampler),
                )
                public = admission.public_projection()

                verify.assert_called_once_with(
                    normalized,
                    expected_sha256=artifact["sha256"],
                    expected_size=artifact["size"],
                    compatibility="10eros_beta3_turbo_hybrid_runtime_admission",
                    family="minimax_h3",
                    role="transformer",
                    receipt_root=None,
                    include_private_binding=True,
                )
                recheck.assert_called_once_with(normalized, _binding(artifact))
                self.assertEqual(public["artifact_id"], artifact["artifact_id"])
                self.assertEqual(public["profile_id"], artifact["profile_id"])
                self.assertEqual(public["filename"], artifact["filename"])
                self.assertEqual(public["authored_evaluations"], 6)
                self.assertEqual(public["sampler"], sampler)
                self.assertEqual(public["attention_engine"], "sdpa")
                self.assertEqual(
                    public["checkpoint"]["contract_revision"],
                    CHECKPOINT_CONTRACT_REVISION,
                )
                self.assertTrue(public["runtime_admission_ready"])
                self.assertFalse(public["execution_available"])
                self.assertFalse(public["enabled_by_default"])
                self.assertFalse(public["automatic_fallback"])
                self.assertFalse(public["wgp_wired"])
                self.assertFalse(public["handler_wired"])
                serialized = json.dumps(public, sort_keys=True)
                self.assertNotIn("_checkpoint_binding", serialized)
                self.assertNotIn("path_digest", serialized)
                for private_field in (
                    "path", "dev", "ino", "uid", "mtime_ns", "ctime_ns",
                ):
                    self.assertNotIn(private_field, public)

    def test_admission_is_not_dataclass_or_vars_serializable(self):
        admission = self._admit_mocked(self.artifacts[0])
        self.assertEqual(repr(admission), "<Beta3RuntimeAdmission path-free>")
        with self.assertRaises(TypeError):
            vars(admission)
        with self.assertRaises(TypeError):
            asdict(admission)
        with self.assertRaises(AttributeError):
            admission.extra = True

    def test_pathlike_is_normalized_once_before_verifier_and_recheck(self):
        artifact = self.artifacts[0]
        correct = os.path.join("/synthetic", artifact["filename"])
        alternating = _AlternatingPath(correct, "/wrong/second.safetensors")
        receipt_root = _AlternatingPath("/synthetic/receipts", "/wrong/receipts")
        with mock.patch.object(
            runtime, "verify_checkpoint_integrity", return_value=_receipt(artifact),
        ) as verify, mock.patch.object(
            runtime, "recheck_checkpoint_binding", return_value=True,
        ) as recheck:
            runtime.admit_beta3_runtime(
                alternating, _request(artifact), receipt_root=receipt_root,
            )
        self.assertEqual(alternating.calls, 1)
        self.assertEqual(receipt_root.calls, 1)
        normalized = os.path.abspath(correct)
        self.assertEqual(verify.call_args.args[0], normalized)
        self.assertEqual(recheck.call_args.args[0], normalized)
        self.assertEqual(
            verify.call_args.kwargs["receipt_root"],
            os.path.abspath("/synthetic/receipts"),
        )

    def test_wrong_artifact_profile_filename_path_and_path_type_fail(self):
        artifact = self.artifacts[0]
        cases = (
            _request(artifact, artifact_id="unknown-beta3"),
            _request(artifact, profile_id="wrong-profile"),
            _request(artifact, filename="wrong.safetensors"),
        )
        with mock.patch.object(runtime, "verify_checkpoint_integrity") as verify:
            for request in cases:
                with self.subTest(request=request), self.assertRaises(
                    runtime.H310ErosBeta3RuntimeAdmissionError
                ):
                    runtime.admit_beta3_runtime(
                        Path("/synthetic") / artifact["filename"], request,
                    )
            with self.assertRaisesRegex(
                runtime.H310ErosBeta3RuntimeAdmissionError, "wrong filename",
            ):
                runtime.admit_beta3_runtime(
                    Path("/synthetic/wrong.safetensors"), _request(artifact),
                )
            with self.assertRaisesRegex(
                runtime.H310ErosBeta3RuntimeAdmissionError, "path is invalid",
            ):
                runtime.admit_beta3_runtime(
                    os.fsencode(f"/synthetic/{artifact['filename']}"),
                    _request(artifact),
                )
            verify.assert_not_called()

    def test_exact_request_types_fail_closed_without_verification(self):
        artifact = self.artifacts[0]
        cases = (
            ("string subclass", {"artifact_id": _StringSubclass(artifact["artifact_id"])}),
            ("bool evaluations", {"authored_evaluations": True}),
            ("list references", {"image_references": []}),
            ("list loras", {"activated_loras": []}),
            ("numeric accelerator", {"step_cache": 0}),
            ("numeric fallback", {"automatic_fallback": 0}),
        )
        with mock.patch.object(runtime, "verify_checkpoint_integrity") as verify:
            for label, changes in cases:
                with self.subTest(label=label), self.assertRaises(
                    runtime.H310ErosBeta3RuntimeAdmissionError
                ):
                    runtime.admit_beta3_runtime(
                        Path("/synthetic") / artifact["filename"],
                        _request(artifact, **changes),
                    )
            verify.assert_not_called()

    def test_incompatible_settings_fail_closed_without_verification(self):
        artifact = self.artifacts[0]
        cases = (
            ("evaluations", {"authored_evaluations": 5}),
            ("sampler", {"sampler": "other/simple"}),
            ("sage", {"attention_engine": "sage2"}),
            ("image refs", {"image_references": (object(),)}),
            ("video refs", {"video_references": (object(),)}),
            ("audio refs", {"audio_references": (object(),)}),
            ("start keyframe", {"start_keyframe": object()}),
            ("end keyframe", {"end_keyframe": object()}),
            ("loras", {"activated_loras": ("turbo.safetensors",)}),
            ("managed turbo", {"managed_turbo_profile": "h3_turbo_v4"}),
            ("spectrum", {"spectrum_profile": "spectrum_h3_v1"}),
            ("lightx2v", {"lightx2v_profile": "h3_lightx2v_fl2v_4_v1"}),
            ("step cache", {"step_cache": "tea"}),
            ("fallback", {"automatic_fallback": True}),
        )
        with mock.patch.object(runtime, "verify_checkpoint_integrity") as verify:
            for label, changes in cases:
                with self.subTest(label=label), self.assertRaises(
                    runtime.H310ErosBeta3RuntimeAdmissionError
                ):
                    runtime.admit_beta3_runtime(
                        Path("/synthetic") / artifact["filename"],
                        _request(artifact, **changes),
                    )
            verify.assert_not_called()

    def test_receipt_shape_revision_and_exact_types_fail_closed(self):
        artifact = self.artifacts[0]
        cases = []
        extra = _receipt(artifact)
        extra["extra"] = True
        cases.append(("extra key", extra))
        cases.append(("mapping subclass", _DictSubclass(_receipt(artifact))))
        cases.append(("wrong revision", _receipt(artifact, contract_revision="v0")))
        cases.append(("bool size", _receipt(artifact, size=True)))
        cases.append(("string verified", _receipt(artifact, verified="true")))
        cases.append(("numeric reuse", _receipt(artifact, receipt_reused=1)))
        path = Path("/synthetic") / artifact["filename"]
        for label, receipt in cases:
            with self.subTest(label=label), mock.patch.object(
                runtime, "verify_checkpoint_integrity", return_value=receipt,
            ), mock.patch.object(runtime, "recheck_checkpoint_binding") as recheck:
                with self.assertRaises(runtime.H310ErosBeta3RuntimeAdmissionError):
                    runtime.admit_beta3_runtime(path, _request(artifact))
                recheck.assert_not_called()

    def test_private_binding_shape_identity_and_types_fail_closed(self):
        artifact = self.artifacts[0]
        bindings = []
        extra = _binding(artifact)
        extra["extra"] = True
        bindings.append(("extra key", extra))
        bindings.append(("mapping subclass", _DictSubclass(_binding(artifact))))
        bindings.append(("wrong revision", _binding(artifact, contract_revision="v0")))
        bindings.append(("wrong digest", _binding(artifact, path_digest="not-a-digest")))
        bindings.append(("wrong sha", _binding(artifact, expected_sha256="c" * 64)))
        bindings.append(("bool device", _binding(artifact, dev=True)))
        bindings.append(("negative inode", _binding(artifact, ino=-1)))
        bindings.append(("wrong size", _binding(artifact, size=artifact["size"] - 1)))
        path = Path("/synthetic") / artifact["filename"]
        for label, binding in bindings:
            with self.subTest(label=label), mock.patch.object(
                runtime, "verify_checkpoint_integrity",
                return_value=_receipt(artifact, binding=binding),
            ), mock.patch.object(runtime, "recheck_checkpoint_binding") as recheck:
                with self.assertRaises(runtime.H310ErosBeta3RuntimeAdmissionError):
                    runtime.admit_beta3_runtime(path, _request(artifact))
                recheck.assert_not_called()

    def test_binding_must_recheck_current_before_admission(self):
        artifact = self.artifacts[0]
        path = Path("/synthetic") / artifact["filename"]
        with mock.patch.object(
            runtime, "verify_checkpoint_integrity", return_value=_receipt(artifact),
        ), mock.patch.object(
            runtime, "recheck_checkpoint_binding", return_value=False,
        ):
            with self.assertRaisesRegex(
                runtime.H310ErosBeta3RuntimeAdmissionError, "not current",
            ):
                runtime.admit_beta3_runtime(path, _request(artifact))

    def test_both_variants_recheck_without_exposing_private_binding(self):
        for artifact in self.artifacts:
            admission = self._admit_mocked(artifact)
            path = Path("/synthetic") / artifact["filename"]
            alternating = _AlternatingPath(str(path), "/wrong/second.safetensors")
            with self.subTest(artifact_id=artifact["artifact_id"]), mock.patch.object(
                runtime, "recheck_checkpoint_binding", return_value=True,
            ) as recheck:
                self.assertTrue(runtime.recheck_beta3_admission(alternating, admission))
                self.assertEqual(alternating.calls, 1)
                self.assertEqual(recheck.call_args.args[0], os.path.abspath(path))
            self.assertFalse(runtime.recheck_beta3_admission(None, admission))
            self.assertFalse(runtime.recheck_beta3_admission(path, object()))

    def _admit_tiny_file(self, directory: str, source: dict, content: bytes):
        artifact = _tiny_artifact(source, len(content))
        path = Path(directory) / artifact["filename"]
        path.write_bytes(content)
        binding = _binding_for_path(path, artifact)
        catalog = {"artifacts": [artifact]}
        with mock.patch.object(
            runtime, "get_10eros_beta3_catalog", return_value=catalog,
        ), mock.patch.object(
            runtime, "verify_checkpoint_integrity",
            return_value=_receipt(artifact, binding=binding),
        ):
            admission = runtime.admit_beta3_runtime(path, _request(artifact))
        return artifact, path, admission

    @unittest.skipUnless(os.name == "posix", "same-descriptor path is POSIX-only")
    def test_held_descriptor_survives_path_replacement_and_closes_after_exit(self):
        for source in self.artifacts:
            with self.subTest(artifact_id=source["artifact_id"]), tempfile.TemporaryDirectory() as directory:
                _, path, admission = self._admit_tiny_file(directory, source, b"old")
                with runtime.hold_beta3_checkpoint(path, admission) as held:
                    self.assertRegex(held, r"^/proc/self/fd/[0-9]+$")
                    self.assertEqual(Path(held).read_bytes(), b"old")
                    replacement = path.with_name("replacement.tmp")
                    replacement.write_bytes(b"new")
                    os.replace(replacement, path)
                    self.assertEqual(Path(held).read_bytes(), b"old")
                self.assertFalse(os.path.exists(held))

    @unittest.skipUnless(os.name == "posix", "same-descriptor path is POSIX-only")
    def test_replacement_and_symlink_paths_fail_before_descriptor_yield(self):
        source = self.artifacts[0]
        with tempfile.TemporaryDirectory() as directory:
            _, path, admission = self._admit_tiny_file(directory, source, b"old")
            replacement = path.with_name("replacement.tmp")
            replacement.write_bytes(b"new")
            os.replace(replacement, path)
            with self.assertRaisesRegex(
                runtime.H310ErosBeta3RuntimeAdmissionError, "changed after admission",
            ):
                with runtime.hold_beta3_checkpoint(path, admission):
                    self.fail("a replaced checkpoint must never be yielded")

        with tempfile.TemporaryDirectory() as directory:
            _, path, admission = self._admit_tiny_file(directory, source, b"old")
            target = path.with_name("original-target.bin")
            os.replace(path, target)
            path.symlink_to(target)
            with self.assertRaisesRegex(
                runtime.H310ErosBeta3RuntimeAdmissionError, "changed after admission",
            ):
                with runtime.hold_beta3_checkpoint(path, admission):
                    self.fail("a symlink checkpoint must never be yielded")


if __name__ == "__main__":
    unittest.main()
