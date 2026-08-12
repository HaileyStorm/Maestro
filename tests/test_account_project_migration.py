"""Focused model-free tests for the first-owner project migration ledger."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from services import account_project_migration as migration


class AccountProjectMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.projects = self.base / "projects"
        self.projects.mkdir()
        self.ledger_path = self.base / "state" / "migration.json"
        self.ledger_path.parent.mkdir()
        self.secret = b"migration-test-secret-32-bytes!!"
        self.owner = "a" * 32

    def store(self, secret=None):
        return migration.AccountProjectMigrationLedger(
            self.ledger_path, secret or self.secret,
        )

    @staticmethod
    def marker(directory: Path, value: str) -> None:
        (directory / migration.PROJECT_MARKER_NAME).write_text(
            value + "\n", encoding="ascii",
        )

    def snapshot(self):
        result = {}
        for path in sorted(self.projects.rglob("*")):
            relative = str(path.relative_to(self.projects))
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ("link", os.readlink(path), info.st_ino)
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ("directory", info.st_ino)
            else:
                result[relative] = ("file", path.read_bytes(), info.st_ino)
        return result

    def test_complete_deterministic_census_disposes_every_edge_without_mutation(self):
        self.marker(self.projects, "1" * 32)
        valid = self.projects / "valid"
        valid.mkdir()
        self.marker(valid, "2" * 32)
        (valid / "private.bin").write_bytes(b"PRIVATE-CONTENT-MUST-NOT-PERSIST")
        for name in ("missing-marker", ".hidden", "_formerly-skipped"):
            (self.projects / name).mkdir()
        self.marker(self.projects / ".hidden", "3" * 32)
        trash = self.projects / ".trash_123_old-project"
        trash.mkdir()
        self.marker(trash, "8" * 32)
        corrupt = self.projects / "corrupt"
        corrupt.mkdir()
        self.marker(corrupt, "PRIVATE-CORRUPT-MARKER")
        (self.projects / "default").mkdir()
        (self.projects / "plain-file").write_text("PRIVATE FILE", encoding="utf-8")
        if os.name != "nt":
            (self.projects / "unsafe\\name").mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        link = self.projects / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            link = None

        before = self.snapshot()
        result = self.store().migrate(self.projects, self.owner)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["entries"][0]["source"], "default")
        candidate_names = sorted(
            (
                name for name in os.listdir(self.projects)
                if stat.S_ISDIR(os.lstat(self.projects / name).st_mode)
                or stat.S_ISLNK(os.lstat(self.projects / name).st_mode)
            ),
            key=os.fsencode,
        )
        self.assertEqual(len(result["entries"]), len(candidate_names) + 1)
        self.assertEqual(
            [row["name"] for row in result["entries"][1:]],
            candidate_names,
        )
        rows = {(row["source"], row["name"]): row for row in result["entries"]}
        self.assertEqual(rows[("entry", "valid")]["marker"], "2" * 32)
        self.assertEqual(rows[("entry", "valid")]["disposition"], "owned")
        self.assertEqual(rows[("entry", "missing-marker")]["reason"], "missing_marker")
        self.assertEqual(rows[("entry", ".hidden")]["disposition"], "quarantined")
        self.assertEqual(rows[("entry", ".hidden")]["reason"], "unsafe_name")
        self.assertEqual(
            rows[("entry", "_formerly-skipped")]["disposition"], "quarantined",
        )
        self.assertEqual(
            rows[("entry", "_formerly-skipped")]["reason"],
            "unsafe_name",
        )
        self.assertEqual(
            rows[("entry", ".trash_123_old-project")]["disposition"],
            "quarantined",
        )
        self.assertEqual(
            rows[("entry", ".trash_123_old-project")]["reason"],
            "unsafe_name",
        )
        self.assertEqual(rows[("entry", "default")]["reason"], "reserved_default_collision")
        self.assertNotIn(("entry", "plain-file"), rows)
        self.assertNotIn(("entry", migration.PROJECT_MARKER_NAME), rows)
        self.assertEqual(result["excluded_non_projects"]["count"], 2)
        self.assertEqual(
            result["excluded_non_projects"]["kinds"], {"regular_file": 2},
        )
        self.assertRegex(result["excluded_non_projects"]["digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(rows[("entry", "corrupt")]["reason"], "corrupt_marker")
        if os.name != "nt":
            self.assertEqual(rows[("entry", "unsafe\\name")]["reason"], "unsafe_name")
        if link is not None:
            self.assertEqual(rows[("entry", "linked")]["reason"], "symlink")
        for row in result["entries"]:
            owned = row["disposition"] == "owned"
            self.assertNotEqual(owned, row["disposition"] == "quarantined")
            self.assertEqual(
                row["account_bindings"],
                [{"account_id": self.owner, "role": "owner"}] if owned else [],
            )
            self.assertEqual(row["recoverable"], not owned)
        self.assertEqual(
            len({row["project_digest"] for row in result["entries"]}),
            len(result["entries"]),
        )
        raw = self.ledger_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE-CONTENT-MUST-NOT-PERSIST", raw)
        self.assertNotIn("PRIVATE-CORRUPT-MARKER", raw)
        self.assertNotIn("plain-file", raw)
        self.assertNotIn(str(self.projects), raw)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.ledger_path.stat().st_mode), 0o600)

    def test_inventory_preview_is_read_only_and_publication_requires_exact_match(self):
        self.marker(self.projects, "1" * 32)
        project = self.projects / "project"
        project.mkdir()
        self.marker(project, "2" * 32)
        store = self.store()
        before = self.snapshot()

        with mock.patch.object(
            store,
            "_read",
            side_effect=AssertionError("preview read the ledger"),
        ), mock.patch.object(
            store,
            "_write",
            side_effect=AssertionError("preview wrote the ledger"),
        ):
            preview = store.inspect_inventory(self.projects, self.owner)

        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse(
            (self.ledger_path.parent / ".migration.json.lock").exists(),
        )
        self.assertTrue(store.matches_root(preview, self.projects))
        other_root = self.base / "other-root"
        other_root.mkdir()
        self.assertFalse(store.matches_root(preview, other_root))
        self.assertTrue(all(
            row["disposition"] == "owned" for row in preview["entries"]
        ))

        self.assertIsNone(store.load_if_present())
        self.assertFalse(
            (self.ledger_path.parent / ".migration.json.lock").exists(),
        )

        late = self.projects / "late"
        late.mkdir()
        with self.assertRaises(migration.ProjectMigrationSafetyError):
            store.migrate_inventory(
                self.projects,
                self.owner,
                expected_inventory=preview,
            )
        self.assertFalse(self.ledger_path.exists())

        late.rmdir()
        published = store.migrate_inventory(
            self.projects,
            self.owner,
            expected_inventory=preview,
        )
        self.assertEqual(published, preview)
        lock_path = self.ledger_path.parent / ".migration.json.lock"
        if lock_path.exists():
            lock_path.unlink()
        self.assertEqual(store.load_if_present(), preview)
        self.assertFalse(lock_path.exists())
        self.assertEqual(store.load(required=True), preview)

    def test_real_default_files_are_not_projects_and_need_no_quarantine(self):
        self.marker(self.projects, "9" * 32)
        output = self.projects / "finished.mp4"
        sidecar = self.projects / "history.json"
        output.write_bytes(b"PRIVATE-OUTPUT-V1")
        sidecar.write_bytes(b"PRIVATE-HISTORY-V1")

        first = self.store().migrate(self.projects, self.owner)
        self.assertEqual(len(first["entries"]), 1)
        self.assertEqual(first["entries"][0]["source"], "default")
        self.assertEqual(first["entries"][0]["disposition"], "owned")
        self.assertEqual(first["entries"][0]["marker"], "9" * 32)
        self.assertFalse(any(
            row["disposition"] == "quarantined" for row in first["entries"]
        ))
        self.assertEqual(first["excluded_non_projects"], {
            "count": 3,
            "kinds": {"regular_file": 3},
            "digest": first["excluded_non_projects"]["digest"],
        })

        output.write_bytes(b"PRIVATE-OUTPUT-V2")
        sidecar.write_bytes(b"PRIVATE-HISTORY-V2")
        self.assertEqual(self.store().migrate(self.projects, self.owner), first)
        raw = self.ledger_path.read_text(encoding="utf-8")
        for forbidden in ("finished.mp4", "history.json", "PRIVATE-OUTPUT"):
            self.assertNotIn(forbidden, raw)
        output.rename(self.projects / "renamed.mp4")
        with self.assertRaises(migration.ProjectMigrationConflictError):
            self.store().migrate(self.projects, self.owner)

    def test_restart_is_byte_idempotent_and_observed_inventory_drift_conflicts(self):
        project = self.projects / "project"
        project.mkdir()
        self.marker(project, "3" * 32)
        first = self.store().migrate(self.projects, self.owner)
        encoded = self.ledger_path.read_bytes()
        self.assertEqual(self.store().migrate(self.projects, self.owner), first)
        self.assertEqual(self.ledger_path.read_bytes(), encoded)
        with self.assertRaises(migration.ProjectMigrationConflictError):
            self.store().migrate(self.projects, "b" * 32)
        (self.projects / "late-project").mkdir()
        with self.assertRaises(migration.ProjectMigrationConflictError):
            self.store().migrate(self.projects, self.owner)
        other = self.base / "other-projects"
        other.mkdir()
        with self.assertRaises(migration.ProjectMigrationConflictError):
            self.store().migrate(other, self.owner)
        self.assertEqual(self.ledger_path.read_bytes(), encoded)

    def test_duplicate_and_unsafe_markers_are_recoverably_quarantined(self):
        for name in ("one", "two"):
            project = self.projects / name
            project.mkdir()
            self.marker(project, "4" * 32)
        hardlinked = self.projects / "hardlinked"
        hardlinked.mkdir()
        self.marker(hardlinked, "5" * 32)
        try:
            os.link(
                hardlinked / migration.PROJECT_MARKER_NAME,
                self.base / "marker-alias",
            )
        except OSError:
            hardlinked = None
        rows = {
            row["name"]: row
            for row in self.store().migrate(self.projects, self.owner)["entries"]
        }
        self.assertEqual(rows["one"]["reason"], "duplicate_marker")
        self.assertEqual(rows["two"]["reason"], "duplicate_marker")
        self.assertNotEqual(rows["one"]["project_digest"], rows["two"]["project_digest"])
        if hardlinked is not None:
            self.assertEqual(rows["hardlinked"]["reason"], "unsafe_marker")

    def test_missing_root_marker_and_reserved_default_directory_are_distinct(self):
        (self.projects / "default").mkdir()
        result = self.store().migrate(self.projects, self.owner)
        self.assertEqual(len(result["entries"]), 2)
        logical, collision = result["entries"]
        self.assertEqual((logical["source"], logical["name"]), ("default", "default"))
        self.assertEqual(logical["reason"], "missing_marker")
        self.assertEqual((collision["source"], collision["name"]), ("entry", "default"))
        self.assertEqual(collision["reason"], "reserved_default_collision")
        self.assertNotEqual(logical["project_digest"], collision["project_digest"])
        self.assertTrue(all(
            row["disposition"] == "quarantined" for row in result["entries"]
        ))

    def test_tamper_duplicate_json_wrong_key_and_unsafe_paths_fail_closed(self):
        self.store().migrate(self.projects, self.owner)
        original = self.ledger_path.read_bytes()
        document = json.loads(original)
        document["owner_account_id"] = "b" * 32
        self.ledger_path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(migration.ProjectMigrationCorruptError):
            self.store().load()
        self.ledger_path.write_bytes(
            original.replace(b'"generation":1', b'"generation":1,"generation":1'),
        )
        with self.assertRaises(migration.ProjectMigrationCorruptError):
            self.store().load()
        self.ledger_path.write_bytes(original)
        with self.assertRaises(migration.ProjectMigrationCorruptError):
            self.store(b"different-secret-that-is-long-enough!!").load()

        project = self.projects / "project"
        project.mkdir()
        self.marker(project, "a" * 32)
        semantic_store = migration.AccountProjectMigrationLedger(
            self.base / "semantic.json", self.secret,
        )
        baseline = semantic_store.migrate(self.projects, self.owner)

        def reseal(document):
            document["census_digest"] = migration.hashlib.sha256(
                migration._canonical({
                    "entries": document["entries"],
                    "excluded_non_projects": document["excluded_non_projects"],
                }),
            ).hexdigest()
            document["seal"] = semantic_store._seal(document)
            return document

        semantic_cases = []
        owned_symlink = json.loads(json.dumps(baseline))
        owned_symlink["entries"][1]["candidate_kind"] = "symlink"
        semantic_cases.append(owned_symlink)
        duplicate_default_source = json.loads(json.dumps(baseline))
        duplicate_default_source["entries"][1]["source"] = "default"
        semantic_cases.append(duplicate_default_source)
        impossible_marker_state = json.loads(json.dumps(baseline))
        impossible_marker_state["entries"][1].update({
            "disposition": "quarantined",
            "account_bindings": [],
            "reason": "missing_marker",
            "recoverable": True,
        })
        semantic_cases.append(impossible_marker_state)
        owned_reserved_name = json.loads(json.dumps(baseline))
        owned_reserved_name["entries"][1]["name"] = "default"
        owned_reserved_name["entries"][1]["name_digest"] = migration._mac(
            self.secret, "name", b"default",
        )
        semantic_cases.append(owned_reserved_name)
        if os.name != "nt":
            owned_unsafe_name = json.loads(json.dumps(baseline))
            owned_unsafe_name["entries"][1]["name"] = "unsafe\\name"
            owned_unsafe_name["entries"][1]["name_digest"] = migration._mac(
                self.secret, "name", b"unsafe\\name",
            )
            semantic_cases.append(owned_unsafe_name)
        boolean_generation = json.loads(json.dumps(baseline))
        boolean_generation["generation"] = True
        semantic_cases.append(boolean_generation)
        false_name_digest = json.loads(json.dumps(baseline))
        false_name_digest["entries"][1]["name_digest"] = "f" * 64
        semantic_cases.append(false_name_digest)
        for document in semantic_cases:
            with self.subTest(document=document), self.assertRaises(
                migration.ProjectMigrationCorruptError,
            ):
                semantic_store._validate(reseal(document))

        target = self.base / "target"
        target.mkdir()
        linked_root = self.base / "linked-root"
        try:
            linked_root.symlink_to(target, target_is_directory=True)
        except OSError:
            linked_root = None
        if linked_root is not None:
            with self.assertRaises(migration.ProjectMigrationSafetyError):
                migration.AccountProjectMigrationLedger(
                    self.base / "other.json", self.secret,
                ).migrate(linked_root, self.owner)

    def test_atomic_failure_publishes_nothing_and_leaves_projects_unchanged(self):
        project = self.projects / "project"
        project.mkdir()
        (project / "payload").write_bytes(b"PRIVATE")
        before = self.snapshot()
        publisher = (
            "_publish_no_replace_windows" if os.name == "nt"
            else "_publish_no_replace"
        )
        with mock.patch.object(
            migration.AccountProjectMigrationLedger,
            publisher,
            side_effect=OSError("synthetic failure"),
        ), self.assertRaises(migration.ProjectMigrationSafetyError):
            self.store().migrate(self.projects, self.owner)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(list(self.ledger_path.parent.glob("*.tmp")), [])

        if os.name != "nt":
            with mock.patch.object(
                migration.os, "fsync",
                side_effect=OSError("unsupported directory fsync"),
            ), self.assertRaises(migration.ProjectMigrationSafetyError):
                self.store().migrate(self.projects, self.owner)
            self.assertFalse(self.ledger_path.exists())
            self.assertEqual(list(self.ledger_path.parent.glob("*.tmp")), [])

    def test_project_payload_is_never_opened(self):
        project = self.projects / "project"
        project.mkdir()
        self.marker(project, "6" * 32)
        payload = project / "payload.bin"
        payload.write_bytes(b"PRIVATE")
        root_output = self.projects / "default-output.mp4"
        root_output.write_bytes(b"PRIVATE-DEFAULT")
        real_open = migration.os.open

        def reject_payload(path, *args, **kwargs):
            self.assertNotIn(
                Path(os.fspath(path)).name, {payload.name, root_output.name},
            )
            return real_open(path, *args, **kwargs)

        with mock.patch.object(migration.os, "open", side_effect=reject_payload):
            self.store().migrate(self.projects, self.owner)

    @unittest.skipIf(os.name == "nt", "POSIX directory fsync required")
    def test_post_publish_sync_failure_durably_rolls_back(self):
        real_fsync = migration.os.fsync
        directory_syncs = 0

        def fail_critical_directory_sync(descriptor):
            nonlocal directory_syncs
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_syncs += 1
                if directory_syncs == 2:
                    raise OSError("synthetic critical sync failure")
            return real_fsync(descriptor)

        with mock.patch.object(
            migration.os, "fsync", side_effect=fail_critical_directory_sync,
        ), self.assertRaises(migration.ProjectMigrationSafetyError):
            self.store().migrate(self.projects, self.owner)
        self.assertGreaterEqual(directory_syncs, 3)
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(list(self.ledger_path.parent.glob("*.tmp")), [])

    def test_marker_change_between_census_passes_fails_closed(self):
        project = self.projects / "project"
        project.mkdir()
        marker = project / migration.PROJECT_MARKER_NAME
        self.marker(project, "7" * 32)
        store = self.store()
        real_entry = store._entry
        calls = 0

        def mutate_before_second_observation(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                marker.write_text("8" * 32 + "\n", encoding="ascii")
            return real_entry(*args, **kwargs)

        with mock.patch.object(
            store, "_entry", side_effect=mutate_before_second_observation,
        ), self.assertRaises(migration.ProjectMigrationSafetyError):
            store.migrate(self.projects, self.owner)
        self.assertFalse(self.ledger_path.exists())

    def test_rival_publication_is_never_clobbered(self):
        rival = b"rival-ledger"
        if os.name == "nt":
            publisher = "_publish_no_replace_windows"
            real_publish = (
                migration.AccountProjectMigrationLedger._publish_no_replace_windows
            )

            def publish_rival_then_commit(temporary, destination):
                self.ledger_path.write_bytes(rival)
                return real_publish(temporary, destination)
        else:
            publisher = "_publish_no_replace"
            real_publish = migration.AccountProjectMigrationLedger._publish_no_replace

            def publish_rival_then_commit(temporary, destination, directory_fd):
                self.ledger_path.write_bytes(rival)
                return real_publish(temporary, destination, directory_fd)

        with mock.patch.object(
            migration.AccountProjectMigrationLedger,
            publisher,
            side_effect=publish_rival_then_commit,
        ), self.assertRaises(migration.ProjectMigrationConflictError):
            self.store().migrate(self.projects, self.owner)
        self.assertEqual(self.ledger_path.read_bytes(), rival)
        self.assertEqual(list(self.ledger_path.parent.glob("*.tmp")), [])

    def test_arguments_and_ledger_location_are_strict(self):
        with self.assertRaises(ValueError):
            self.store().migrate("relative", self.owner)
        with self.assertRaises(ValueError):
            self.store().migrate(self.projects, "not-an-account")
        with self.assertRaises(ValueError):
            migration.AccountProjectMigrationLedger(
                self.projects / "ledger.json", self.secret,
            ).migrate(self.projects, self.owner)
        with self.assertRaises(migration.ProjectMigrationCorruptError):
            self.store().load(required=True)
        with self.assertRaises(migration.ProjectMigrationCorruptError):
            self.store().migrate(
                self.projects, self.owner, require_existing=True,
            )


if __name__ == "__main__":
    unittest.main()
