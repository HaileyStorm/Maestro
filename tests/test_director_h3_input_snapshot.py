"""Actual filesystem checks for fresh H3 repair inputs; no model execution."""
from pathlib import Path
import copy
import hashlib
import os
import stat
import tempfile
import unittest
from unittest import mock

from services import director_h3_input_snapshot as snapshots
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError, atomic_write_request_manifest,
    cleanup_orphan_staged_outputs, load_request_manifest, validate_manifest_inputs,
)


class DirectorH3InputSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original.mp4"
        self.content = b"model-free original native AV bytes"
        self.source.write_bytes(self.content)
        self.artifact = {"basename": self.source.name, "size": len(self.content),
                         "sha256": hashlib.sha256(self.content).hexdigest()}
        self.identity = {"project_directory": str(self.root), "job_id": "director-p-repair-1",
                         "owner_digest": "owner:v1:" + "a" * 64,
                         "project_digest": "project:v1:" + "b" * 64}

    def snapshot(self, **changes):
        return snapshots.snapshot_predecessor(**{**self.identity, "source_path": str(self.source),
            "source_artifact": self.artifact, **changes})

    def validate(self, descriptor, **changes):
        return snapshots.validate_predecessor_snapshot(descriptor, **dict(self.identity, **changes))

    def test_fresh_private_input_is_independent_of_original_and_not_completed_work(self):
        descriptor = self.snapshot()
        path = Path(descriptor["path"])
        self.assertNotEqual(path, self.source)
        self.assertEqual(path.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertNotIn("unit_id", descriptor)
        self.assertNotIn("completed_units", descriptor)
        self.assertTrue(self.validate(descriptor))
        self.source.unlink()
        self.assertTrue(self.validate(descriptor))
        self.assertEqual(path.read_bytes(), self.content)

    def test_manifest_binds_snapshot_to_its_new_job_without_old_unit_adoption(self):
        descriptor = self.snapshot()
        pointer = atomic_write_request_manifest(self.root, job_id=self.identity["job_id"],
            params={"_h3_rerun_predecessor_path": descriptor["path"]}, inputs=[descriptor])
        manifest = load_request_manifest(self.root, pointer, expected_job_id=self.identity["job_id"])
        validate_manifest_inputs(manifest, self.validate)
        changed = copy.deepcopy(manifest)
        changed["job_id"] = "director-other-repair"
        with self.assertRaises(QueueRecoveryRuntimeError):
            validate_manifest_inputs(changed, lambda _descriptor: True)
        self.assertNotIn("completed_units", manifest)

    def test_scope_media_and_permissions_cannot_change_or_move_to_another_job(self):
        descriptor = self.snapshot()
        for key, value in (("job_id", "other"), ("owner_digest", "other"), ("project_digest", "other")):
            with self.subTest(key=key):
                self.assertFalse(self.validate(descriptor, **{key: value}))
        for key, value in (("field", "audio_source:0"), ("size", True),
                           ("sha256", "c" * 64), ("scope", "project")):
            with self.subTest(key=key):
                changed = dict(descriptor, **{key: value})
                self.assertFalse(self.validate(changed))
        path = Path(descriptor["path"])
        path.chmod(0o644)
        if os.name != "nt":
            self.assertFalse(self.validate(descriptor))
        path.chmod(0o600)
        path.write_bytes(b"changed")
        self.assertFalse(self.validate(descriptor))

    def test_source_mismatch_and_copy_failure_leave_no_new_input_or_source_mutation(self):
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.snapshot(source_artifact=dict(self.artifact, sha256="c" * 64))
        staging = self.root / ".maestro-recovery" / "staging"
        self.assertEqual(list(staging.iterdir()), [])
        with mock.patch.object(snapshots.os, "write", side_effect=OSError("injected disk failure")):
            with self.assertRaises(QueueRecoveryRuntimeError):
                self.snapshot()
        self.assertEqual(list(staging.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_source_and_private_directory_links_never_authorize_copy_or_recovery(self):
        target = self.root / "target.mp4"
        self.source.rename(target)
        self.source.symlink_to(target)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.snapshot()
        self.source.unlink()
        target.rename(self.source)
        descriptor = self.snapshot()
        staging = Path(descriptor["path"]).parent
        replacement = self.root / "outside"
        staging.rename(replacement)
        staging.symlink_to(replacement, target_is_directory=True)
        self.assertFalse(self.validate(descriptor))
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.snapshot()
        self.assertEqual((replacement / Path(descriptor["path"]).name).read_bytes(), self.content)

    def test_existing_staging_cleanup_keeps_active_input_then_retires_terminal_orphan(self):
        descriptor = self.snapshot()
        path = Path(descriptor["path"])
        self.assertEqual(cleanup_orphan_staged_outputs(self.root, [self.identity["job_id"]]), 0)
        self.assertTrue(path.is_file())
        self.assertEqual(cleanup_orphan_staged_outputs(self.root, []), 1)
        self.assertFalse(path.exists())
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_directory_swap_at_creation_cannot_receive_copied_media(self):
        staging = Path(snapshots.ensure_recovery_staging_directory(self.root))
        moved = self.root / "moved-private-staging"
        outside = self.root / "replacement"
        outside.mkdir()
        original_open = os.open
        def swap_before_create(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                staging.rename(moved)
                staging.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)
        # The capability set contains the original function, not the patched
        # test wrapper. Preserve supported operations while injecting the race.
        supported = set(os.supports_dir_fd)
        supported.add(swap_before_create)
        with mock.patch.object(snapshots.os, "open", side_effect=swap_before_create) as opened:
            supported.add(opened)
            with mock.patch.object(snapshots.os, "supports_dir_fd", supported):
                with self.assertRaises(QueueRecoveryRuntimeError):
                    self.snapshot()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(moved.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_unsupported_directory_binding_fails_before_copy_or_private_file_creation(self):
        with mock.patch.object(snapshots.os, "supports_dir_fd", set()):
            with self.assertRaises(QueueRecoveryRuntimeError):
                self.snapshot()
        self.assertFalse((self.root / ".maestro-recovery").exists())
        self.assertEqual(self.source.read_bytes(), self.content)


if __name__ == "__main__":
    unittest.main()
