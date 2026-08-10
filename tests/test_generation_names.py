"""Focused contracts for the durable project generation-name registry."""

from __future__ import annotations

from dataclasses import asdict, fields
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from services import generation_names as names  # noqa: E402


def _concurrent_operation(path: str, operation: str, generation_id: str, value, start, results):
    try:
        registry = names.GenerationNameRegistry(path)
        start.wait(timeout=10)
        if operation == "allocate":
            result = registry.allocate(generation_id)
        else:
            result = registry.rename(generation_id, value)
        results.put((asdict(result), None))
    except Exception as error:  # pragma: no cover - reported to parent process
        results.put((None, type(error).__name__))


class GenerationNameRegistryTests(unittest.TestCase):
    def _registry(self, root: str) -> names.GenerationNameRegistry:
        project = Path(root) / "project"
        project.mkdir(exist_ok=True)
        return names.GenerationNameRegistry(
            project / "generation-names.json",
        )

    def test_allocate_persists_only_fixed_schema_and_public_result_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            result = registry.allocate("generation-opaque-001")

            self.assertEqual([field.name for field in fields(result)], [
                "id", "name", "revision",
            ])
            self.assertEqual(result.id, "generation-opaque-001")
            self.assertEqual(result.revision, 1)
            self.assertEqual(len(result.name.split(" ")), 2)
            document = json.loads(registry.path.read_text(encoding="utf-8"))
            self.assertEqual(set(document), {"schema", "entries"})
            self.assertEqual(document, {
                "schema": names.GENERATION_NAMES_SCHEMA,
                "entries": {
                    result.id: {"name": result.name, "revision": 1},
                },
            })
            encoded = registry.path.read_text(encoding="utf-8")
            for forbidden in (
                "prompt", "session", "media", "sidecar", "hash", "job_id",
                str(registry.path),
            ):
                self.assertNotIn(forbidden, encoded)
                self.assertNotIn(forbidden, repr(result))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(registry.path).st_mode), 0o600)
            self.assertEqual(list(registry.path.parent.glob("*.tmp")), [])

    def test_allocate_is_idempotent_and_restart_persists_renames(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            allocated = registry.allocate("generation-1")
            self.assertEqual(registry.allocate("generation-1"), allocated)

            first_rename = registry.rename("generation-1", "My First Cut")
            second_rename = registry.rename("generation-1", "My Final Cut")
            self.assertEqual(first_rename.revision, 2)
            self.assertEqual(second_rename.revision, 3)
            self.assertEqual(registry.rename("generation-1", "My Final Cut"), second_rename)

            restarted = self._registry(temporary)
            self.assertEqual(restarted.lookup("generation-1"), second_rename)
            # Any number of shot parts can join this one current value at read time.
            self.assertEqual(
                [restarted.lookup("generation-1") for _part in range(3)],
                [second_rename, second_rename, second_rename],
            )

    def test_defaults_are_collision_checked_and_manual_names_stay_unique(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            with mock.patch.object(names, "_default_start_index", return_value=0):
                first = registry.allocate("generation-1")
                second = registry.allocate("generation-2")
            self.assertNotEqual(first.name.casefold(), second.name.casefold())
            with self.assertRaises(names.GenerationNameConflictError):
                registry.rename("generation-2", first.name.swapcase())
            self.assertEqual(registry.lookup("generation-2"), second)

    def test_ids_and_names_have_strict_bounded_unicode_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            invalid_ids = (
                None, b"id", "", "id with spaces", "../id", "id\\path",
                "id\x00tail", "e\u0301", "x" * (names.MAX_GENERATION_ID_CHARS + 1),
                "\ud800",
            )
            for value in invalid_ids:
                with self.subTest(identifier=repr(value)), self.assertRaises(
                    names.GenerationNameValidationError,
                ):
                    registry.allocate(value)

            registry.allocate("valid-id")
            invalid_names = (
                None, b"name", "", " leading", "trailing ", "two  spaces",
                "line\nbreak", "tab\tname", "control\x7f", "e\u0301", "\ud800",
                "x" * (names.MAX_GENERATION_NAME_CHARS + 1),
            )
            for value in invalid_names:
                with self.subTest(name=repr(value)), self.assertRaises(
                    names.GenerationNameValidationError,
                ):
                    registry.rename("valid-id", value)
            valid = registry.rename("valid-id", "Caf\u00e9 \u00c9tude")
            self.assertEqual(valid.name, "Caf\u00e9 \u00c9tude")

    def test_missing_rename_and_malformed_registry_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            self.assertIsNone(registry.lookup("missing-id"))
            with self.assertRaises(names.GenerationNameNotFoundError):
                registry.rename("missing-id", "Manual Name")

            registry.path.parent.mkdir(parents=True, exist_ok=True)
            malformed_documents = (
                b"not json",
                b'{"schema":1,"schema":1,"entries":{}}',
                b'{"schema":true,"entries":{}}',
                b'{"schema":1,"entries":{},"extra":0}',
                b'{"schema":1,"entries":{"id":{"name":"One Name","revision":true}}}',
                b'{"schema":1,"entries":{"a":{"name":"Same Name","revision":1},'
                b'"b":{"name":"same name","revision":1}}}',
            )
            for raw in malformed_documents:
                with self.subTest(raw=raw):
                    registry.path.write_bytes(raw)
                    if os.name != "nt":
                        os.chmod(registry.path, 0o600)
                    with self.assertRaises(names.GenerationNameStorageError):
                        registry.lookup("missing-id")
                    with self.assertRaises(names.GenerationNameStorageError):
                        registry.allocate("new-id")
                    self.assertEqual(registry.path.read_bytes(), raw)

    def test_missing_project_directory_is_never_created_implicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "missing-project"
            registry = names.GenerationNameRegistry(project / "names.json")
            self.assertIsNone(registry.lookup("generation-1"))
            with self.assertRaises(names.GenerationNameStorageError):
                registry.allocate("generation-1")
            self.assertFalse(project.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_registry_directory_target_and_lock_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            real_directory = root / "real-project"
            real_directory.mkdir()
            linked_directory = root / "linked-project"
            linked_directory.symlink_to(real_directory, target_is_directory=True)
            via_directory = names.GenerationNameRegistry(linked_directory / "names.json")
            with self.assertRaises(names.GenerationNameStorageError):
                via_directory.allocate("generation-1")
            self.assertFalse((real_directory / "names.json").exists())

            target = root / "target.json"
            target.write_text("unchanged", encoding="utf-8")
            project = root / "project"
            project.mkdir()
            linked_target = project / "names.json"
            linked_target.symlink_to(target)
            via_target = names.GenerationNameRegistry(linked_target)
            with self.assertRaises(names.GenerationNameStorageError):
                via_target.lookup("generation-1")
            with self.assertRaises(names.GenerationNameStorageError):
                via_target.allocate("generation-1")
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

            lock_registry = names.GenerationNameRegistry(project / "other.json")
            lock_path = project / ".other.json.lock"
            lock_path.symlink_to(target)
            with self.assertRaises(names.GenerationNameStorageError):
                lock_registry.allocate("generation-2")
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_failed_atomic_write_does_not_replace_published_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            first = registry.allocate("generation-1")
            original = registry.path.read_bytes()
            with mock.patch.object(
                registry,
                "_write_locked",
                side_effect=names.GenerationNameStorageError("synthetic"),
            ), self.assertRaises(names.GenerationNameStorageError):
                registry.rename("generation-1", "Replacement Name")
            self.assertEqual(registry.path.read_bytes(), original)
            self.assertEqual(registry.lookup("generation-1"), first)

            replacement_owner = names if os.name == "nt" else names.os
            replacement_name = (
                "_windows_replace_write_through" if os.name == "nt" else "replace"
            )
            with mock.patch.object(
                replacement_owner,
                replacement_name,
                side_effect=OSError("synthetic replace failure"),
            ), self.assertRaises(names.GenerationNameStorageError):
                registry.rename("generation-1", "Another Replacement")
            self.assertEqual(registry.path.read_bytes(), original)
            self.assertEqual(list(registry.path.parent.glob("*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "POSIX directory descriptors required")
    def test_parent_symlink_swap_cannot_redirect_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            outside = root / "outside"
            relocated = root / "relocated-project"
            project.mkdir()
            outside.mkdir()
            registry = names.GenerationNameRegistry(project / "names.json")
            real_replace = os.replace
            swapped = False

            def swap_parent_then_replace(source, target, **kwargs):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    project.rename(relocated)
                    project.symlink_to(outside, target_is_directory=True)
                return real_replace(source, target, **kwargs)

            with mock.patch.object(names.os, "replace", side_effect=swap_parent_then_replace):
                with self.assertRaises(names.GenerationNameStorageError):
                    registry.allocate("generation-1")
            self.assertTrue(swapped)
            self.assertFalse((outside / "names.json").exists())

    def test_lookup_is_serialized_with_concurrent_atomic_renames(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            registry.allocate("generation-1")
            writer = self._registry(temporary)
            failures = []

            def rename_repeatedly():
                try:
                    for index in range(40):
                        writer.rename("generation-1", f"Concurrent Name {index}")
                except Exception as error:  # pragma: no cover - asserted below
                    failures.append(error)

            thread = threading.Thread(target=rename_repeatedly)
            thread.start()
            while thread.is_alive():
                try:
                    observed = registry.lookup("generation-1")
                    self.assertEqual(observed.id, "generation-1")
                except Exception as error:  # pragma: no cover - asserted below
                    failures.append(error)
                    break
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(registry.lookup("generation-1").revision, 41)

    def test_windows_replace_requests_replace_existing_and_write_through(self):
        move_file_ex = mock.Mock(return_value=1)
        fake_windll = types.SimpleNamespace(
            kernel32=types.SimpleNamespace(MoveFileExW=move_file_ex),
        )
        with mock.patch.object(ctypes, "windll", fake_windll, create=True):
            names._windows_replace_write_through(
                Path("source.tmp"),
                Path("registry.json"),
            )
        move_file_ex.assert_called_once_with("source.tmp", "registry.json", 0x1 | 0x8)

    def test_independent_instances_merge_without_lost_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = self._registry(temporary)
            second = self._registry(temporary)
            first.allocate("generation-1")
            second.allocate("generation-2")
            first.rename("generation-1", "First Rename")
            second.rename("generation-2", "Second Rename")
            restarted = self._registry(temporary)
            self.assertEqual(restarted.lookup("generation-1").name, "First Rename")
            self.assertEqual(restarted.lookup("generation-2").name, "Second Rename")

    def test_process_lock_serializes_concurrent_allocations_and_renames(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = self._registry(temporary)
            registry.allocate("rename-me")
            context = multiprocessing.get_context(
                "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn",
            )
            start = context.Event()
            results = context.Queue()
            operations = (
                ("allocate", "new-a", None),
                ("allocate", "new-b", None),
                ("rename", "rename-me", "Concurrent One"),
                ("rename", "rename-me", "Concurrent Two"),
            )
            processes = [
                context.Process(
                    target=_concurrent_operation,
                    args=(str(registry.path), operation, generation_id, value, start, results),
                )
                for operation, generation_id, value in operations
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=20)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=5) for _process in processes]
            self.assertTrue(all(error is None for _result, error in outcomes), outcomes)

            restarted = self._registry(temporary)
            self.assertIsNotNone(restarted.lookup("new-a"))
            self.assertIsNotNone(restarted.lookup("new-b"))
            renamed = restarted.lookup("rename-me")
            self.assertEqual(renamed.revision, 3)
            self.assertIn(renamed.name, {"Concurrent One", "Concurrent Two"})


if __name__ == "__main__":
    unittest.main()
