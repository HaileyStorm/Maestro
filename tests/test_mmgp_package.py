from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
try:
    import tomllib
except ModuleNotFoundError:  # Maestro also supports Python 3.10.
    import tomli as tomllib
import unittest
from unittest import mock
import warnings
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "app" / "dependencies" / "mmgp"


def _load_backend():
    spec = importlib.util.spec_from_file_location(
        "maestro_mmgp_build_backend", PACKAGE_ROOT / "backend.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


backend = _load_backend()


def _digest(data: bytes) -> str:
    value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + value.decode("ascii")


def _record(contents: dict[str, bytes], record_name: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name in sorted(contents):
        writer.writerow((name, _digest(contents[name]), str(len(contents[name]))))
    writer.writerow((record_name, "", ""))
    return stream.getvalue().encode("utf-8")


def _zip(contents: dict[str, bytes], *, duplicate: str | None = None) -> bytes:
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in contents.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 2, 3, 4, 6))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
            if duplicate is not None:
                archive.writestr(duplicate, b"duplicate")
    return stream.getvalue()


def _fake_upstream() -> tuple[bytes, dict[str, bytes]]:
    old_record = f"{backend.OLD_DIST_INFO}/RECORD"
    contents = {
        "__init__.py": b"",
        "mmgp/__init__.py": b"__version__ = '3.7.12'\n",
        "mmgp/fp8_quanto_bridge.py": b"bridge = True\n",
        "mmgp/offload.py": b"line one\r\nline two\r\n",
        "mmgp/quant_router.py": b"router = True\n",
        "mmgp/safetensors2.py": b"reader = True\n",
        f"{backend.OLD_DIST_INFO}/licenses/LICENSE.md": b"upstream license\n",
        f"{backend.OLD_DIST_INFO}/METADATA": (
            b"Metadata-Version: 2.4\r\n"
            b"Name: mmgp\r\n"
            b"Version: 3.7.12\r\n"
            b"Requires-Dist: torch>=2.1.0\r\n"
            b"\r\nupstream description\r\n"
        ),
        f"{backend.OLD_DIST_INFO}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: setuptools (83.0.0)\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        f"{backend.OLD_DIST_INFO}/top_level.txt": b"mmgp\n",
    }
    contents[old_record] = _record(contents, old_record)
    return _zip(contents), contents


class MmgpPackageTests(unittest.TestCase):
    def test_pep517_and_uv_recipe_inputs_are_explicit(self):
        config = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
        self.assertEqual(config["build-system"]["requires"], [])
        self.assertEqual(config["build-system"]["build-backend"], "backend")
        self.assertEqual(config["build-system"]["backend-path"], ["."])
        self.assertEqual(
            {entry["file"] for entry in config["tool"]["uv"]["cache-keys"]},
            {"pyproject.toml", "backend.py", "dora-input-scale.patch"},
        )

    def test_pins_and_patch_are_exact_and_crlf(self):
        self.assertEqual(backend.VERSION, "3.7.12+maestro1")
        self.assertEqual(
            backend.UPSTREAM_SHA256,
            "2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf",
        )
        patch = (PACKAGE_ROOT / backend.PATCH_FILENAME).read_bytes()
        self.assertEqual(hashlib.sha256(patch).hexdigest(), backend.PATCH_SHA256)
        self.assertEqual(patch.count(b"@@ "), 2)
        self.assertGreater(patch.count(b"\r\n"), 0)
        self.assertNotIn(b"\n", patch.replace(b"\r\n", b""))
        self.assertIn(b"torch.isfinite(effective_weight_input_scale)", patch)
        self.assertIn(b"effective DoRA and LoKr", patch)

    def test_unified_patch_preserves_crlf_and_unaffected_lines(self):
        source = b"one\r\ntwo\r\nthree\r\nfour\r\nfive\r\nsix\r\n"
        patch = (
            b"--- a/mmgp/offload.py\r\n"
            b"+++ b/mmgp/offload.py\r\n"
            b"@@ -1,3 +1,3 @@\r\n"
            b" one\r\n"
            b"-two\r\n"
            b"+TWO\r\n"
            b" three\r\n"
            b"@@ -5,2 +5,3 @@\r\n"
            b" five\r\n"
            b"+five-half\r\n"
            b" six\r\n"
        )
        expected = b"one\r\nTWO\r\nthree\r\nfour\r\nfive\r\nfive-half\r\nsix\r\n"
        self.assertEqual(backend._apply_unified_patch(source, patch), expected)
        with self.assertRaisesRegex(backend.BuildError, "preimage"):
            backend._apply_unified_patch(source.replace(b"two", b"other"), patch)
        with self.assertRaisesRegex(backend.BuildError, "CRLF"):
            backend._apply_unified_patch(source.replace(b"\r\n", b"\n"), patch)

    def test_archive_reader_rejects_unsafe_unexpected_and_duplicate_members(self):
        cases = [
            ({"../escape": b"x"}, frozenset({"../escape"}), None, "unsafe"),
            ({"/absolute": b"x"}, frozenset({"/absolute"}), None, "unsafe"),
            ({"mmgp\\bad.py": b"x"}, frozenset({"mmgp\\bad.py"}), None, "unsafe"),
            ({"expected": b"x"}, frozenset({"expected"}), "expected", "duplicate"),
            ({"Expected": b"x"}, frozenset({"Expected", "expected"}), "expected", "duplicate"),
            ({"surprise": b"x"}, frozenset({"expected"}), None, "unexpected"),
        ]
        for contents, expected, duplicate, message in cases:
            with self.subTest(message=message, contents=contents):
                with self.assertRaisesRegex(backend.BuildError, message):
                    backend._read_wheel(_zip(contents, duplicate=duplicate), expected)

    def test_archive_reader_rejects_non_regular_members(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
        with self.assertRaisesRegex(backend.BuildError, "non-regular"):
            backend._read_wheel(stream.getvalue(), frozenset({"link"}))

    def test_complete_record_detects_tampering_and_duplicate_rows(self):
        record_name = "example.dist-info/RECORD"
        clean = {"module.py": b"value = 1\n"}
        clean[record_name] = _record(clean, record_name)
        backend._validate_record(clean, record_name)

        tampered = dict(clean)
        tampered["module.py"] = b"value = 2\n"
        with self.assertRaisesRegex(backend.BuildError, "mismatch"):
            backend._validate_record(tampered, record_name)

        duplicate = dict(clean)
        duplicate[record_name] += b"module.py,,\n"
        with self.assertRaisesRegex(backend.BuildError, "duplicate"):
            backend._validate_record(duplicate, record_name)

    def test_offline_fake_wheel_build_is_deterministic_and_preserves_upstream(self):
        upstream_wheel, upstream = _fake_upstream()
        fake_offload = upstream["mmgp/offload.py"]
        patched_offload = fake_offload + b"protocol = True\r\n"
        replacements = {
            "UPSTREAM_SHA256": hashlib.sha256(upstream_wheel).hexdigest(),
            "UPSTREAM_OFFLOAD_SHA256": hashlib.sha256(fake_offload).hexdigest(),
            "PATCHED_OFFLOAD_SHA256": hashlib.sha256(patched_offload).hexdigest(),
        }
        with mock.patch.multiple(backend, **replacements):
            with mock.patch.object(backend, "_patch_offload", return_value=patched_offload):
                first = backend._build_wheel_bytes(upstream_wheel)
                second = backend._build_wheel_bytes(upstream_wheel)
        self.assertEqual(first, second)

        expected_names = {
            backend._mapped_name(name)
            for name in upstream
            if name != f"{backend.OLD_DIST_INFO}/RECORD"
        }
        expected_names.update({backend.PROVENANCE_FILENAME, backend.RECORD_FILENAME})
        built, _ = backend._read_wheel(first, frozenset(expected_names))
        backend._validate_record(built, backend.RECORD_FILENAME)
        self.assertNotIn(backend.OLD_DIST_INFO, "\n".join(built))
        self.assertEqual(
            built[f"{backend.NEW_DIST_INFO}/licenses/LICENSE.md"],
            upstream[f"{backend.OLD_DIST_INFO}/licenses/LICENSE.md"],
        )
        metadata = built[f"{backend.NEW_DIST_INFO}/METADATA"]
        self.assertIn(b"Version: 3.7.12+maestro1\r\n", metadata)
        self.assertIn(b"Requires-Dist: torch>=2.1.0\r\n", metadata)
        expected_wheel_metadata = upstream[f"{backend.OLD_DIST_INFO}/WHEEL"].replace(
            backend.UPSTREAM_WHEEL_GENERATOR, backend.WHEEL_GENERATOR, 1
        )
        self.assertEqual(
            built[f"{backend.NEW_DIST_INFO}/WHEEL"], expected_wheel_metadata
        )
        provenance = json.loads(built[backend.PROVENANCE_FILENAME])
        self.assertEqual(provenance["schema"], "maestro/mmgp-wheel-provenance/v1")
        self.assertEqual(
            set(provenance["recipe_inputs_sha256"]),
            {"backend.py", "dora-input-scale.patch", "pyproject.toml"},
        )
        self.assertNotIn(str(REPO_ROOT), built[backend.PROVENANCE_FILENAME].decode())
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertTrue(
                all(info.date_time == backend.FIXED_ZIP_TIMESTAMP for info in archive.infolist())
            )

    @unittest.skipUnless(
        os.environ.get("MAESTRO_TEST_LIVE_MMGP_WHEEL") == "1",
        "set MAESTRO_TEST_LIVE_MMGP_WHEEL=1 for the bounded public-wheel test",
    )
    def test_live_pinned_wheel_builds_through_pep517_hook(self):
        upstream = backend._download_upstream_wheel()
        self.assertEqual(hashlib.sha256(upstream).hexdigest(), backend.UPSTREAM_SHA256)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(backend, "_download_upstream_wheel", return_value=upstream):
                first_name = backend.build_wheel(str(root / "first"))
                second_name = backend.build_wheel(str(root / "second"))
            first = (root / "first" / first_name).read_bytes()
            second = (root / "second" / second_name).read_bytes()
        self.assertEqual(first_name, backend.WHEEL_FILENAME)
        self.assertEqual(first, second)
        expected_names = frozenset(
            {
                backend._mapped_name(name)
                for name in backend.EXPECTED_UPSTREAM_FILES
                if name != f"{backend.OLD_DIST_INFO}/RECORD"
            }
            | {backend.PROVENANCE_FILENAME, backend.RECORD_FILENAME}
        )
        built, _ = backend._read_wheel(first, expected_names)
        backend._validate_record(built, backend.RECORD_FILENAME)
        self.assertEqual(
            hashlib.sha256(built["mmgp/offload.py"]).hexdigest(),
            backend.PATCHED_OFFLOAD_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
