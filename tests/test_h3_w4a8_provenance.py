from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_w4a8_provenance as provenance


def _write_package(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _expected_digest(files: dict[str, bytes]) -> str:
    manifest = {
        relative: hashlib.sha256(content).hexdigest()
        for relative, content in files.items()
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


class _FakeWindowsApi:
    """Path-backed test adapter for the platform-neutral Windows walker."""

    def __init__(self, *, mutate_file: Path | None = None) -> None:
        self._mutate_file = mutate_file
        self._snapshot_calls: dict[Path, int] = {}

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.absolute())

    def open(self, path: Path) -> Path:
        os.lstat(path)
        return path

    @staticmethod
    def close(_handle: Path) -> None:
        return None

    def snapshot(self, handle: Path):
        information = os.lstat(handle)
        attributes = 0
        if stat.S_ISDIR(information.st_mode):
            attributes |= provenance._WIN_FILE_ATTRIBUTE_DIRECTORY
        if stat.S_ISLNK(information.st_mode):
            attributes |= provenance._WIN_FILE_ATTRIBUTE_REPARSE_POINT
        snapshot = provenance._WindowsSnapshot(
            identity=(information.st_dev, information.st_ino),
            attributes=attributes,
            size=information.st_size,
            creation_time=information.st_ctime_ns,
            last_write_time=information.st_mtime_ns,
            change_time=information.st_ctime_ns,
            final_path_key=self._key(handle),
        )
        calls = self._snapshot_calls.get(handle, 0) + 1
        self._snapshot_calls[handle] = calls
        if handle == self._mutate_file and calls == 2:
            return replace(snapshot, change_time=snapshot.change_time + 1)
        return snapshot

    @staticmethod
    def scan_names(path: Path) -> list[str]:
        return sorted(entry.name for entry in os.scandir(path))

    @staticmethod
    def read_digest(handle: Path, size: int) -> str:
        content = handle.read_bytes()
        if len(content) != size:
            raise ValueError("test file changed size")
        return hashlib.sha256(content).hexdigest()

    def root_path_key(self, root: Path) -> str:
        return self._key(root)

    def expected_path_key(self, root_key: str, parts: tuple[str, ...]) -> str:
        return self._key(Path(root_key, *parts))


class H3W4A8ProvenanceTests(unittest.TestCase):
    def test_pinned_constants_match_authoritative_proof(self):
        self.assertEqual(
            provenance.RUNTIME_REVISION,
            "b812819a97ac11d01f4a3a16ba47dd38de3b2519",
        )
        self.assertEqual(
            provenance.EXPECTED_PACKAGE_DIGEST,
            "2028f87be20ad79158b47895280fdc4ecf1491d7c010bfd4058cabf89e2b778b",
        )

    def test_fingerprint_is_sorted_canonical_relative_manifest(self):
        files = {
            "tensor/z.py": b"last",
            "__init__.py": b"first",
            "backends/triton/kernel.py": b"middle",
        }
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            first.mkdir()
            second.mkdir()
            _write_package(first, files)
            _write_package(second, dict(reversed(list(files.items()))))
            expected = _expected_digest(files)
            self.assertEqual(provenance.package_fingerprint(first), expected)
            self.assertEqual(provenance.package_fingerprint(second), expected)

    def test_require_pinned_rejects_changed_extra_and_missing_source(self):
        baseline = {"__init__.py": b"init", "kernel.py": b"kernel"}
        expected = _expected_digest(baseline)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            provenance,
            "EXPECTED_PACKAGE_DIGEST",
            expected,
        ):
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            _write_package(root, baseline)
            self.assertEqual(provenance.require_pinned_package(root), expected)

            cases = {
                "changed": {"__init__.py": b"changed", "kernel.py": b"kernel"},
                "extra": {**baseline, "extra.py": b"extra"},
                "missing": {"__init__.py": b"init"},
            }
            for name, files in cases.items():
                with self.subTest(name=name):
                    candidate = Path(temporary) / name
                    candidate.mkdir()
                    _write_package(candidate, files)
                    with self.assertRaisesRegex(ValueError, "pinned runtime"):
                        provenance.require_pinned_package(candidate)

    def test_bytecode_cache_churn_is_excluded(self):
        files = {"__init__.py": b"init", "nested/kernel.py": b"source"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            _write_package(root, files)
            baseline = provenance.package_fingerprint(root)

            _write_package(
                root,
                {
                    "__pycache__/__init__.cpython-311.pyc": b"cache one",
                    "nested/kernel.pyc": b"cache two",
                    "nested/legacy.pyo": b"cache three",
                },
            )
            self.assertEqual(provenance.package_fingerprint(root), baseline)
            (root / "__pycache__/__init__.cpython-311.pyc").write_bytes(b"new cache")
            self.assertEqual(provenance.package_fingerprint(root), baseline)

    def test_regular_hardlinks_are_fingerprinted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            source = root / "source.py"
            source.write_bytes(b"same bytes")
            os.link(source, root / "hardlinked.py")
            expected = _expected_digest(
                {"source.py": b"same bytes", "hardlinked.py": b"same bytes"}
            )
            self.assertEqual(provenance.package_fingerprint(root), expected)

    def test_symlinks_are_rejected_without_following(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real_root = base / "real"
            real_root.mkdir()
            (real_root / "source.py").write_bytes(b"source")

            root_link = base / "root-link"
            root_link.symlink_to(real_root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                provenance.package_fingerprint(root_link)
            with self.assertRaisesRegex(ValueError, "path traversal"):
                provenance.package_fingerprint(real_root / ".." / "real")

            outside = base / "outside.py"
            outside.write_bytes(b"outside")
            (real_root / "linked.py").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                provenance.package_fingerprint(real_root)
            (real_root / "linked.py").unlink()

            outside_dir = base / "outside-dir"
            outside_dir.mkdir()
            (outside_dir / "hidden.py").write_bytes(b"outside")
            (real_root / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                provenance.package_fingerprint(real_root)

    def test_nonregular_files_and_caps_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fifo_root = base / "fifo"
            fifo_root.mkdir()
            try:
                os.mkfifo(fifo_root / "pipe")
            except (AttributeError, NotImplementedError, OSError) as error:
                self.skipTest(f"FIFO unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "non-regular"):
                provenance.package_fingerprint(fifo_root)

            files_root = base / "files"
            files_root.mkdir()
            _write_package(files_root, {"one.py": b"1", "two.py": b"2"})
            with mock.patch.object(provenance, "_MAX_FILES", 1), self.assertRaisesRegex(
                ValueError,
                "file count",
            ):
                provenance.package_fingerprint(files_root)

            size_root = base / "size"
            size_root.mkdir()
            (size_root / "large.py").write_bytes(b"12")
            with mock.patch.object(provenance, "_MAX_FILE_BYTES", 1), self.assertRaisesRegex(
                ValueError,
                "file size",
            ):
                provenance.package_fingerprint(size_root)

            with mock.patch.object(provenance, "_MAX_TOTAL_BYTES", 1), self.assertRaisesRegex(
                ValueError,
                "total size",
            ):
                provenance.package_fingerprint(size_root)

    def test_file_stat_mutation_during_read_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            (root / "source.py").write_bytes(b"stable bytes")
            real_fstat = provenance.os.fstat
            regular_calls = 0

            def changing_fstat(file_descriptor: int):
                nonlocal regular_calls
                result = real_fstat(file_descriptor)
                if stat.S_ISREG(result.st_mode):
                    regular_calls += 1
                    if regular_calls == 2:
                        changed = list(result)
                        changed[8] = result.st_mtime + 1
                        return os.stat_result(changed)
                return result

            with (
                mock.patch.object(
                    provenance.os,
                    "fstat",
                    side_effect=changing_fstat,
                ),
                self.assertRaisesRegex(ValueError, "changed while being read"),
            ):
                provenance.package_fingerprint(root)

    def test_posix_rescan_rejects_late_runtime_entry_but_ignores_bytecode(self):
        if os.name == "nt":
            self.skipTest("requires POSIX descriptor traversal")
        real_snapshot = provenance._posix_runtime_entries

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime-addition"
            root.mkdir()
            (root / "source.py").write_bytes(b"source")
            def add_runtime_entry(directory, *, excluded_cache):
                (root / "late.py").write_bytes(b"late")
                return real_snapshot(directory, excluded_cache=excluded_cache)

            with (
                mock.patch.object(
                    provenance,
                    "_posix_runtime_entries",
                    side_effect=add_runtime_entry,
                ),
                self.assertRaisesRegex(ValueError, "directory contents changed"),
            ):
                provenance.package_fingerprint(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bytecode-addition"
            root.mkdir()
            source = {"source.py": b"source"}
            _write_package(root, source)
            def add_bytecode_entries(directory, *, excluded_cache):
                (root / "late.pyc").write_bytes(b"bytecode")
                _write_package(root, {"__pycache__/source.pyc": b"cache"})
                return real_snapshot(directory, excluded_cache=excluded_cache)

            with mock.patch.object(
                provenance,
                "_posix_runtime_entries",
                side_effect=add_bytecode_entries,
            ):
                self.assertEqual(
                    provenance.package_fingerprint(root),
                    _expected_digest(source),
                )

    def test_marker_requires_exact_fields_and_a_fresh_fingerprint(self):
        files = {"__init__.py": b"init"}
        expected = _expected_digest(files)
        marker = {
            "schema_version": 2,
            "runtime_revision": provenance.RUNTIME_REVISION,
            "package_digest": expected,
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            provenance,
            "EXPECTED_PACKAGE_DIGEST",
            expected,
        ):
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            _write_package(root, files)
            self.assertTrue(provenance.marker_package_matches(marker, root))

            for field, value in (
                ("schema_version", True),
                ("schema_version", 1),
                ("runtime_revision", "other"),
                ("package_digest", "0" * 64),
            ):
                with self.subTest(field=field, value=value):
                    changed = dict(marker)
                    changed[field] = value
                    self.assertFalse(provenance.marker_package_matches(changed, root))

            (root / "__init__.py").write_bytes(b"changed")
            self.assertFalse(provenance.marker_package_matches(marker, root))
            self.assertFalse(provenance.marker_package_matches(marker, root / "missing"))
            self.assertFalse(provenance.marker_package_matches(None, root))

    def test_locator_validates_without_executing_package_init(self):
        with tempfile.TemporaryDirectory() as temporary:
            search_root = Path(temporary)
            package_root = search_root / "comfy_kitchen"
            package_root.mkdir()
            execution_sentinel = search_root / "package-imported"
            (package_root / "__init__.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(execution_sentinel)!r}).write_text('executed')\n",
                encoding="utf-8",
            )
            digest = provenance.package_fingerprint(package_root)

            with (
                mock.patch.object(provenance.sys, "path", [str(search_root)]),
                mock.patch.object(provenance, "EXPECTED_PACKAGE_DIGEST", digest),
            ):
                located_root, located_digest = provenance.locate_pinned_package()
            self.assertEqual(located_root, package_root)
            self.assertEqual(located_digest, digest)
            self.assertFalse(execution_sentinel.exists())

            with (
                mock.patch.object(provenance.sys, "path", [str(search_root)]),
                mock.patch.object(provenance, "EXPECTED_PACKAGE_DIGEST", "0" * 64),
                self.assertRaisesRegex(ValueError, "pinned runtime"),
            ):
                provenance.locate_pinned_package()
            self.assertFalse(execution_sentinel.exists())

    def test_mocked_windows_handle_walker_preserves_digest_and_rejects_links(self):
        files = {
            "__init__.py": b"init",
            "backends/triton/kernel.py": b"kernel",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            _write_package(root, files)
            self.assertEqual(
                provenance._package_fingerprint_windows(
                    root,
                    _api=_FakeWindowsApi(),
                ),
                _expected_digest(files),
            )

            outside = Path(temporary) / "outside.py"
            outside.write_bytes(b"outside")
            (root / "linked.py").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "reparse point"):
                provenance._package_fingerprint_windows(
                    root,
                    _api=_FakeWindowsApi(),
                )

    def test_mocked_windows_handle_walker_rejects_file_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            source = root / "source.py"
            source.write_bytes(b"source")
            with self.assertRaisesRegex(ValueError, "changed while being read"):
                provenance._package_fingerprint_windows(
                    root,
                    _api=_FakeWindowsApi(mutate_file=source),
                )

    def test_windows_dispatch_uses_native_walker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            with (
                mock.patch.object(provenance, "_WINDOWS", True),
                mock.patch.object(
                    provenance,
                    "_package_fingerprint_windows",
                    return_value="f" * 64,
                ) as windows_fingerprint,
            ):
                self.assertEqual(provenance.package_fingerprint(root), "f" * 64)
            windows_fingerprint.assert_called_once_with(root)

    @unittest.skipUnless(os.name == "nt", "requires native Windows handles")
    def test_native_windows_handle_walker_matches_canonical_digest(self):
        files = {"__init__.py": b"init", "nested/kernel.py": b"kernel"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comfy_kitchen"
            root.mkdir()
            _write_package(root, files)
            self.assertEqual(
                provenance.package_fingerprint(root),
                _expected_digest(files),
            )


if __name__ == "__main__":
    unittest.main()
