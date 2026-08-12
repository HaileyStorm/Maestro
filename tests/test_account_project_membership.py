"""Focused model-free tests for account/project membership state."""

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

from services import account_project_membership as membership
from services import account_project_migration as migration
from services.queue_recovery_adapter import project_instance_digest


class AccountProjectMembershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.projects = self.base / "projects"
        self.projects.mkdir()
        self.state = self.base / "state"
        self.state.mkdir()
        self.migration_path = self.state / "migration.json"
        self.membership_path = self.state / "membership.json"
        self.secret = b"membership-test-secret-32-bytes!"
        self.owner = "a" * 32
        self.editor = "b" * 32
        self.viewer = "c" * 32

    @staticmethod
    def marker(directory: Path, value: str) -> None:
        (directory / migration.PROJECT_MARKER_NAME).write_text(
            value + "\n",
            encoding="ascii",
        )

    def make_ledger(self, *, include_quarantine: bool = True):
        self.marker(self.projects, "1" * 32)
        project = self.projects / "project"
        project.mkdir(exist_ok=True)
        self.marker(project, "2" * 32)
        if include_quarantine:
            (self.projects / "missing-marker").mkdir(exist_ok=True)
        return migration.AccountProjectMigrationLedger(
            self.migration_path,
            self.secret,
        ).migrate_inventory(self.projects, self.owner)

    def store(self, *, path=None, secret=None):
        return membership.AccountProjectMembershipStore(
            path or self.membership_path,
            secret or self.secret,
        )

    def test_import_translates_runtime_identity_and_classifies_every_row(self):
        ledger = self.make_ledger()
        result = self.store().initialize_from_ledger(ledger)
        expected = {
            project_instance_digest(self.secret, "1" * 32),
            project_instance_digest(self.secret, "2" * 32),
        }
        self.assertEqual(
            {record["project_instance"] for record in result["projects"]},
            expected,
        )
        self.assertTrue(
            expected.isdisjoint(
                {row["project_digest"] for row in ledger["entries"]},
            )
        )
        summary = result["migration"]
        self.assertEqual(summary["ledger_schema_version"], 2)
        self.assertEqual(summary["classified_entries"], len(ledger["entries"]))
        self.assertEqual(summary["bound_entries"], 2)
        expected_quarantined = [
            row for row in ledger["entries"] if row["disposition"] == "quarantined"
        ]
        self.assertEqual(summary["quarantined_entries"], len(expected_quarantined))
        self.assertEqual(
            summary["excluded_non_projects"], ledger["excluded_non_projects"]
        )
        self.assertEqual(summary["excluded_non_projects"]["count"], 1)
        self.assertEqual(summary["excluded_non_projects"]["kinds"], {"regular_file": 1})
        quarantine = next(
            row for row in result["quarantine"] if row["name"] == "missing-marker"
        )
        census_row = next(
            row for row in ledger["entries"] if row["name"] == "missing-marker"
        )
        self.assertEqual(quarantine["classification"], "quarantined")
        self.assertEqual(quarantine["reason"], "missing_marker")
        self.assertEqual(
            quarantine["migration_project_digest"],
            census_row["project_digest"],
        )
        self.assertEqual(result["projects"][0]["provenance"]["classification"], "bound")
        self.assertFalse(
            any(
                row["name"] == migration.PROJECT_MARKER_NAME
                for row in result["quarantine"]
            )
        )
        encoded = self.membership_path.read_bytes()
        self.assertIn(b'"seal"', encoded)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.membership_path.stat().st_mode), 0o600)

    def test_exact_import_replay_is_byte_idempotent_after_mutations(self):
        ledger = self.make_ledger(include_quarantine=False)
        store = self.store()
        initial = store.initialize_from_ledger(ledger)
        project = initial["projects"][0]["project_instance"]
        store.bind(self.editor, "editor", project_instance=project)
        before = self.membership_path.read_bytes()
        replay = store.initialize_from_ledger(ledger)
        self.assertEqual(self.membership_path.read_bytes(), before)
        self.assertEqual(replay["generation"], 2)
        self.assertEqual(store.lookup(project_instance=project)["revision"], 2)

        other_projects = self.base / "other-projects"
        other_projects.mkdir()
        self.marker(other_projects, "3" * 32)
        other_ledger = migration.AccountProjectMigrationLedger(
            self.state / "other-migration.json",
            self.secret,
        ).migrate_inventory(other_projects, self.owner)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.initialize_from_ledger(other_ledger)
        self.assertEqual(self.membership_path.read_bytes(), before)

    def test_role_matrix_is_closed_and_listing_reports_role(self):
        ledger = self.make_ledger(include_quarantine=False)
        store = self.store()
        project = store.initialize_from_ledger(ledger)["projects"][0][
            "project_instance"
        ]
        store.bind(self.editor, "editor", project_instance=project)
        store.bind(self.viewer, "viewer", project_instance=project)

        viewer_expected = {"project.list", "project.open", "project.read"}
        editor_expected = viewer_expected | {"project.mutate", "project.generate"}
        self.assertEqual(membership.permissions_for_role("viewer"), viewer_expected)
        self.assertEqual(membership.permissions_for_role("editor"), editor_expected)
        self.assertEqual(
            membership.permissions_for_role("owner"),
            membership.PROJECT_PERMISSIONS,
        )
        for role in membership.PROJECT_ROLES:
            for permission in membership.PROJECT_PERMISSIONS:
                self.assertEqual(
                    membership.role_allows(role, permission),
                    permission in membership.permissions_for_role(role),
                )
        listed = store.list_for_account(self.viewer, permission="project.open")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["account_role"], "viewer")
        self.assertEqual(
            store.list_for_account(self.viewer, permission="project.generate"),
            [],
        )
        with self.assertRaises(ValueError):
            membership.role_allows("administrator", "project.read")
        with self.assertRaises(ValueError):
            membership.role_allows("owner", "project.destroy-host")

    def test_many_to_many_changes_cannot_orphan_a_project(self):
        ledger = self.make_ledger(include_quarantine=False)
        store = self.store()
        project = store.initialize_from_ledger(ledger)["projects"][0][
            "project_instance"
        ]
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.bind(self.owner, "editor", project_instance=project)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.unbind(self.owner, project_instance=project)
        second_owner = store.bind(self.editor, "owner", project_instance=project)
        demoted = store.bind(
            self.owner,
            "editor",
            project_instance=project,
            expected_revision=second_owner["revision"],
        )
        self.assertEqual(
            {item["role"] for item in demoted["bindings"]},
            {"owner", "editor"},
        )
        unchanged = store.bind(self.owner, "editor", project_instance=project)
        self.assertEqual(unchanged["revision"], demoted["revision"])
        removed = store.unbind(self.owner, project_instance=project)
        self.assertEqual(
            removed["bindings"], [{"account_id": self.editor, "role": "owner"}]
        )

    def test_new_project_requires_owner_and_recreation_gets_new_identity(self):
        store = self.store()
        store.initialize_from_ledger(self.make_ledger(include_quarantine=False))
        marker_one = "3" * 32
        identity_one = store.project_identity(marker=marker_one)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.bind(self.editor, "editor", marker=marker_one)
        created = store.bind(self.owner, "owner", marker=marker_one)
        self.assertEqual(created["project_instance"], identity_one)
        self.assertEqual(created["origin"], "runtime")
        marker_two = "4" * 32
        identity_two = store.project_identity(marker=marker_two)
        self.assertNotEqual(identity_one, identity_two)
        recreated = store.bind(self.owner, "owner", marker=marker_two)
        self.assertNotEqual(created["project_instance"], recreated["project_instance"])
        with self.assertRaises(ValueError):
            store.project_identity(marker=marker_one, project_instance=identity_two)
        with self.assertRaises(ValueError):
            store.bind(self.owner, "owner", project_instance="3" * 64)

    def test_deletion_is_recoverable_idempotent_and_tombstoned(self):
        store = self.store()
        project = store.initialize_from_ledger(
            self.make_ledger(include_quarantine=False),
        )["projects"][0]["project_instance"]
        operation = "d" * 32
        pending = store.begin_deletion(
            project_instance=project,
            operation_id=operation,
            expected_revision=1,
        )
        self.assertEqual(pending["state"], "deleting")
        self.assertIsNone(store.lookup(project_instance=project))
        self.assertEqual(
            store.begin_deletion(
                project_instance=project,
                operation_id=operation,
                expected_revision=1,
            ),
            pending,
        )
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.begin_deletion(
                project_instance=project,
                operation_id=operation,
                expected_revision=99,
            )
        for invalid_revision in (True, 1.0):
            with self.assertRaises(ValueError):
                store.begin_deletion(
                    project_instance=project,
                    operation_id=operation,
                    expected_revision=invalid_revision,
                )
        restored = store.cancel_deletion(operation, project_instance=project)
        self.assertEqual(restored["state"], "active")
        self.assertEqual(restored["deletion"]["status"], "cancelled")
        self.assertEqual(
            store.cancel_deletion(operation, project_instance=project),
            restored,
        )
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.cancel_deletion("e" * 32, project_instance=project)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.begin_deletion(
                project_instance=project,
                operation_id=operation,
                expected_revision=1,
            )
        operation = "f" * 32
        pending = store.begin_deletion(
            project_instance=project,
            operation_id=operation,
            expected_revision=restored["revision"],
        )
        second_restored = store.cancel_deletion(operation, project_instance=project)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.begin_deletion(
                project_instance=project,
                operation_id="d" * 32,
                expected_revision=1,
            )
        operation = "9" * 32
        pending = store.begin_deletion(
            project_instance=project,
            operation_id=operation,
            expected_revision=second_restored["revision"],
        )
        deleted = store.finish_deletion(operation, project_instance=project)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(deleted["deletion"]["status"], "completed")
        self.assertEqual(
            store.finish_deletion(operation, project_instance=project),
            deleted,
        )
        self.assertIsNone(store.lookup(project_instance=project))
        self.assertEqual(
            store.lookup(project_instance=project, include_inactive=True),
            deleted,
        )
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.bind(self.owner, "owner", project_instance=project)
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.cancel_deletion(operation, project_instance=project)
        self.assertEqual(pending["deletion"]["operation_id"], operation)

    def test_revision_conflicts_and_missing_store_fail_closed(self):
        store = self.store()
        self.assertIsNone(store.load())
        with self.assertRaises(membership.ProjectMembershipStoreUnavailableError):
            store.load(required=True)
        with self.assertRaises(membership.ProjectMembershipStoreUnavailableError):
            store.lookup(marker="1" * 32)
        project = store.initialize_from_ledger(
            self.make_ledger(include_quarantine=False),
        )["projects"][0]["project_instance"]
        with self.assertRaises(membership.ProjectMembershipConflictError):
            store.bind(
                self.editor,
                "editor",
                project_instance=project,
                expected_revision=99,
            )
        self.assertEqual(store.lookup(project_instance=project)["revision"], 1)

        self.membership_path.unlink()
        fresh = self.store()
        with self.assertRaises(membership.ProjectMembershipStoreUnavailableError):
            fresh.initialize_from_ledger(
                self.make_ledger(include_quarantine=False),
                require_existing=True,
            )
        self.assertFalse(self.membership_path.exists())

    def test_import_rejects_unclassified_or_forged_rows_without_writing(self):
        ledger = self.make_ledger()
        forged = json.loads(json.dumps(ledger))
        forged["entries"][0]["disposition"] = "pending"
        with self.assertRaises(membership.ProjectMembershipConflictError):
            self.store().initialize_from_ledger(forged)
        self.assertFalse(self.membership_path.exists())

        forged = json.loads(json.dumps(ledger))
        forged["entries"][0]["reason"] = "symlink"
        forged["census_digest"] = membership.hashlib.sha256(
            membership._canonical(
                {
                    "entries": forged["entries"],
                    "excluded_non_projects": forged["excluded_non_projects"],
                }
            ),
        ).hexdigest()
        forged["seal"] = membership.hmac.new(
            self.secret,
            membership._MIGRATION_SEAL_DOMAIN
            + membership._canonical(
                {key: value for key, value in forged.items() if key != "seal"}
            ),
            membership.hashlib.sha256,
        ).hexdigest()
        with self.assertRaises(membership.ProjectMembershipConflictError):
            self.store().initialize_from_ledger(forged)
        self.assertFalse(self.membership_path.exists())

        forged = json.loads(json.dumps(ledger))
        forged["excluded_non_projects"]["count"] += 1
        forged["census_digest"] = membership.hashlib.sha256(
            membership._canonical(
                {
                    "entries": forged["entries"],
                    "excluded_non_projects": forged["excluded_non_projects"],
                }
            ),
        ).hexdigest()
        forged["seal"] = membership.hmac.new(
            self.secret,
            membership._MIGRATION_SEAL_DOMAIN
            + membership._canonical(
                {key: value for key, value in forged.items() if key != "seal"}
            ),
            membership.hashlib.sha256,
        ).hexdigest()
        with self.assertRaises(membership.ProjectMembershipConflictError):
            self.store().initialize_from_ledger(forged)
        self.assertFalse(self.membership_path.exists())

        forged = json.loads(json.dumps(ledger))
        forged["entries"][0]["marker"] = "f" * 32
        with self.assertRaises(membership.ProjectMembershipConflictError):
            self.store().initialize_from_ledger(forged)
        self.assertFalse(self.membership_path.exists())

    def test_tamper_wrong_secret_duplicate_json_and_path_copy_fail_closed(self):
        store = self.store()
        store.initialize_from_ledger(self.make_ledger(include_quarantine=False))
        original = self.membership_path.read_bytes()
        document = json.loads(original)
        document["generation"] += 1
        self.membership_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(membership.ProjectMembershipStoreCorruptError):
            store.load(required=True)
        self.membership_path.write_bytes(
            original.replace(b'"version": 2', b'"version": 2, "version": 2'),
        )
        with self.assertRaises(membership.ProjectMembershipStoreCorruptError):
            store.load(required=True)
        self.membership_path.write_bytes(original)
        with self.assertRaises(membership.ProjectMembershipStoreCorruptError):
            self.store(secret=b"different-membership-secret-32-bytes").load(
                required=True
            )
        copied = self.state / "copied-membership.json"
        copied.write_bytes(original)
        if os.name != "nt":
            os.chmod(copied, 0o600)
        with self.assertRaises(membership.ProjectMembershipStoreCorruptError):
            self.store(path=copied).load(required=True)

    def test_failed_atomic_update_preserves_previous_store(self):
        store = self.store()
        project = store.initialize_from_ledger(
            self.make_ledger(include_quarantine=False),
        )["projects"][0]["project_instance"]
        before = self.membership_path.read_bytes()
        with (
            mock.patch.object(
                membership,
                "_atomic_replace_private_file",
                side_effect=membership.AccountStoreCorruptError(),
            ),
            self.assertRaises(membership.ProjectMembershipStoreUnavailableError),
        ):
            store.bind(self.editor, "editor", project_instance=project)
        self.assertEqual(self.membership_path.read_bytes(), before)
        self.assertEqual(store.lookup(project_instance=project)["revision"], 1)

    def test_constructor_has_no_filesystem_side_effect(self):
        target = self.base / "not-created" / "membership.json"
        membership.AccountProjectMembershipStore(target, self.secret)
        self.assertFalse(target.parent.exists())


if __name__ == "__main__":
    unittest.main()
