"""CPU/mock coverage for the pinned LightX2V CUDA 13 source installer."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from scripts import install_lightx2v_runtime as installer  # noqa: E402


class FakeVersionInfo(tuple):
    major = 3
    minor = 11
    micro = 14

    def __new__(cls):
        return super().__new__(cls, (cls.major, cls.minor, cls.micro))


def runtime_identity(root: Path) -> dict[str, str]:
    cuda_lib = root / "site/nvidia/cu13/lib"
    return {
        "python": "3.11.14", "python_abi": "cp311", "torch": "2.10.0+cu130",
        "torch_cuda": "13.0", "platform": "linux_x86_64",
        "prefix": str(root / "venv"), "site": str(root / "site"),
        "cuda_include": str(root / "site/nvidia/cu13/include"),
        "cuda_lib": str(cuda_lib),
        "cublas": str(cuda_lib / "libcublas.so.13"),
        "cublas_lt": str(cuda_lib / "libcublasLt.so.13"),
        "cuda_runtime": str(cuda_lib / "libcudart.so.13"),
        "executable": sys.executable,
    }


def write_distribution(site: Path, *, version: str, package_text: str, metadata_text: str) -> None:
    package = site / "lightx2v_kernel"
    package.mkdir()
    (package / "__init__.py").write_text(package_text, encoding="utf-8")
    (package / "gemm.py").write_text(f"gemm:{package_text}", encoding="utf-8")
    (package / "utils.py").write_text(f"utils:{package_text}", encoding="utf-8")
    (package / "version.py").write_text(f"version:{package_text}", encoding="utf-8")
    (package / "common_ops.so").write_bytes(f"elf:{package_text}".encode("utf-8"))
    info = site / f"lightx2v_kernel-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(metadata_text, encoding="utf-8")


class LightX2VSourceAndBuildTests(unittest.TestCase):
    def test_pins_complete_build_without_runtime_override(self):
        self.assertEqual(installer.KERNELS_REVISION, "2808bfb073bd91e4fe3ef83712f600b8d642579b")
        self.assertEqual(installer.CUTLASS_REVISION, "dcf215af68a2d08d305076c152a06f201728cd53")
        self.assertEqual(installer.VERSION, "0.0.2+torch2.10.0.cu130.maestro1")
        self.assertEqual(len(installer.TRANSLATION_UNITS), 9)
        for flag in (
            "-U__CUDA_NO_HALF_OPERATORS__", "-U__CUDA_NO_HALF_CONVERSIONS__",
            "-U__CUDA_NO_BFLOAT16_CONVERSIONS__", "-U__CUDA_NO_HALF2_OPERATORS__",
            "-gencode=arch=compute_120,code=sm_120",
            "-gencode=arch=compute_120a,code=sm_120a", "--threads=2",
        ):
            self.assertIn(flag, installer.CUDA_FLAGS)
        self.assertEqual(
            installer.NINJA_RUNPATH_FLAG,
            "-Wl,-rpath,'$$ORIGIN/../torch/lib:$$ORIGIN/../nvidia/cu13/lib'",
        )
        source = Path(installer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("LIGHTX2V_NVFP4_GEMM", source)

    def test_safe_tar_extracts_regular_files_and_rejects_links_and_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = root / "good.tar"
            with tarfile.open(good, "w") as archive:
                data = b"verified"
                member = tarfile.TarInfo("selected/file.txt")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            installer._safe_extract_tar(good, root / "good", strip_prefix="selected")
            self.assertEqual((root / "good/file.txt").read_bytes(), b"verified")

            for name, kind in (("../escape", "file"), ("selected/link", "link")):
                with self.subTest(name=name):
                    archive_path = root / (kind + ".tar")
                    with tarfile.open(archive_path, "w") as archive:
                        member = tarfile.TarInfo(name)
                        if kind == "link":
                            member.type = tarfile.SYMTYPE
                            member.linkname = "target"
                            archive.addfile(member)
                        else:
                            member.size = 1
                            archive.addfile(member, io.BytesIO(b"x"))
                    with self.assertRaisesRegex(RuntimeError, "unsafe path|link or special"):
                        installer._safe_extract_tar(
                            archive_path, root / f"rejected-{kind}", strip_prefix="selected"
                        )

    def test_source_patch_is_bound_to_expected_upstream_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            paths = {
                "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu":
                    "prefix " + installer.UPSTREAM_CUBLAS_ERROR + " suffix\n",
                "python/lightx2v_kernel/__init__.py": "upstream init\n",
                "python/lightx2v_kernel/version.py": "upstream version\n",
            }
            for relative, text in paths.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            base = {name: installer._sha256(source / name) for name in paths}
            predicted = {
                "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu":
                    paths["csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu"].replace(
                        installer.UPSTREAM_CUBLAS_ERROR, installer.MAESTRO_CUBLAS_ERROR
                    ),
                "python/lightx2v_kernel/__init__.py": installer.MAESTRO_INIT,
                "python/lightx2v_kernel/version.py": installer.MAESTRO_VERSION,
            }
            manifest = {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in predicted.items()
            }
            with mock.patch.object(installer, "PATCH_BASE_SHA256", base), \
                 mock.patch.object(installer, "PATCHED_SOURCE_TREE_SHA256",
                                   installer._manifest_digest(manifest)):
                installer._patch_source(source)
            for relative, text in predicted.items():
                self.assertEqual((source / relative).read_text(encoding="utf-8"), text)

            (source / "python/lightx2v_kernel/version.py").write_text("changed", encoding="utf-8")
            before = (source / "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu").read_bytes()
            with mock.patch.object(installer, "PATCH_BASE_SHA256", base):
                with self.assertRaisesRegex(RuntimeError, "source bytes changed"):
                    installer._patch_source(source)
            self.assertEqual((source / "csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu").read_bytes(), before)

    def test_fetch_requires_exact_revision_before_archive(self):
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer, "_run", side_effect=lambda command, **_: calls.append(list(command))), \
             mock.patch.object(installer, "_output", return_value="wrong"):
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "revision mismatch"):
                installer._fetch_archive(
                    git="git", repository="https://example.invalid/source.git",
                    revision="a" * 40, checkout=root / "repo", archive=root / "source.tar",
                    members=("selected",),
                )
        self.assertTrue(any(
            command[1] == "fetch" and "--no-tags" in command and "--filter=blob:none" in command
            for command in calls
        ))
        self.assertFalse(any(command[1] == "archive" for command in calls))

    def test_managed_toolchain_command_is_isolated_and_pinned(self):
        identity = {"packages_sha256": "toolchain"}
        calls = []
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer, "STATE_ROOT", Path(temporary) / "state"), \
             mock.patch.object(installer, "_toolchain_identity", side_effect=[None, identity]), \
             mock.patch.object(installer, "_run", side_effect=lambda command, **kwargs: calls.append((list(command), kwargs))):
            prefix = Path(temporary) / "state/toolchain"
            self.assertIs(installer.ensure_toolchain(prefix, conda="/pinokio/conda"), identity)
        command, kwargs = calls[0]
        self.assertEqual(command[:6], ["/pinokio/conda", "create", "--yes", "--prefix", str(prefix), "--override-channels"])
        self.assertIn(installer.CUDA_CHANNEL, command)
        for package in installer.TOOLCHAIN_PACKAGES:
            self.assertIn(package, command)
        self.assertEqual(kwargs["env"]["CONDA_PKGS_DIRS"], str(Path(temporary) / "state/conda-pkgs"))

    def test_toolchain_accepts_contained_links_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "toolchain"
            metadata = prefix / "conda-meta"
            metadata.mkdir(parents=True)
            for name, version in installer.REQUIRED_TOOLCHAIN_PACKAGES.items():
                if name in {"gcc_linux-64", "gxx_linux-64"}:
                    version = "14.3.0"
                channel = (
                    "https://conda.anaconda.org/nvidia/label/cuda-13.0.2/linux-64"
                    if name in {"cuda-nvcc", "cuda-nvcc_linux-64", "cuda-cccl_linux-64"}
                    else "https://conda.anaconda.org/conda-forge/linux-64"
                )
                (metadata / f"{name}.json").write_text(json.dumps({
                    "name": name, "version": version or "fixture", "build": "0",
                    "channel": channel, "sha256": name,
                }), encoding="utf-8")
            real = prefix / "libexec"
            real.mkdir()
            bin_dir = prefix / "bin"
            bin_dir.mkdir()
            for link, target in (("nvcc", "nvcc-real"), ("ninja", "ninja-real"),
                                 ("x86_64-conda-linux-gnu-gcc", "gcc-real"),
                                 ("x86_64-conda-linux-gnu-g++", "gxx-real")):
                path = real / target
                path.write_text("fixture", encoding="utf-8")
                path.chmod(0o755)
                (bin_dir / link).symlink_to(path)
            real_sysroot = prefix / "libexec/sysroot"
            real_sysroot.mkdir()
            triple = prefix / "x86_64-conda-linux-gnu"
            triple.mkdir()
            (triple / "sysroot").symlink_to(real_sysroot, target_is_directory=True)
            real_cccl = prefix / "libexec/cccl"
            real_cccl.mkdir()
            cccl_parent = prefix / "targets/x86_64-linux/include/cccl/cuda"
            cccl_parent.mkdir(parents=True)
            (cccl_parent / "std").symlink_to(real_cccl, target_is_directory=True)
            def version(command, **_kwargs):
                return "14.3.0" if command[1] == "-dumpversion" else "release 13.0, V13.0.88"
            with mock.patch.object(installer.subprocess, "check_output", side_effect=version):
                identity = installer._toolchain_identity(prefix)
                self.assertIsNotNone(identity)
                self.assertEqual(identity["nvcc"], str((real / "nvcc-real").resolve()))
                self.assertEqual(
                    identity["cccl_include"],
                    str((prefix / "targets/x86_64-linux/include/cccl").resolve()),
                )
                with mock.patch.object(
                    installer.subprocess, "check_output",
                    side_effect=lambda command, **_kwargs: (
                        "16.0.0" if command[1] == "-dumpversion" else "release 13.0, V13.0.88"
                    ),
                ):
                    self.assertIsNone(installer._toolchain_identity(prefix))
                outside_cccl = Path(temporary) / "outside-cccl"
                outside_cccl.mkdir()
                (cccl_parent / "std").unlink()
                (cccl_parent / "std").symlink_to(outside_cccl, target_is_directory=True)
                self.assertIsNone(installer._toolchain_identity(prefix))
                (cccl_parent / "std").unlink()
                (cccl_parent / "std").symlink_to(real_cccl, target_is_directory=True)
                outside = Path(temporary) / "outside-nvcc"
                outside.write_text("fixture", encoding="utf-8")
                outside.chmod(0o755)
                (bin_dir / "nvcc").unlink()
                (bin_dir / "nvcc").symlink_to(outside)
                self.assertIsNone(installer._toolchain_identity(prefix))

    def test_build_runs_selected_python_under_conda_with_one_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "attempt"
            attempt.mkdir()
            wheel = attempt / "wheel/candidate.whl"
            wheel.parent.mkdir()
            wheel.write_bytes(b"wheel")
            result = {"wheel": str(wheel), "wheel_sha256": installer._sha256(wheel), "payload": {}}
            calls = []

            def run(command, **kwargs):
                calls.append((list(command), kwargs))
                (attempt / "package-manifest.json").write_text(json.dumps(result), encoding="utf-8")

            toolchain = {
                "cc": "/toolchain/cc", "cxx": "/toolchain/cxx",
                "cccl_include": "/toolchain/targets/x86_64-linux/include/cccl",
            }
            runtime = runtime_identity(root)
            with mock.patch.object(installer, "_run", side_effect=run), \
                 mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/legacy/cuda12"}):
                self.assertEqual(
                    installer.build_package(
                        attempt, conda="/pinokio/conda", toolchain_prefix=root / "toolchain",
                        toolchain=toolchain, runtime=runtime, readelf="/usr/bin/readelf",
                    ), result,
                )
            command, kwargs = calls[0]
            self.assertEqual(command[:4], ["/pinokio/conda", "run", "--prefix", str(root / "toolchain")])
            self.assertIn(runtime["executable"], command)
            self.assertEqual(kwargs["env"]["MAX_JOBS"], "1")
            self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(kwargs["env"]["CC"], "/toolchain/cc")
            self.assertEqual(kwargs["env"]["LIBRARY_PATH"], runtime["cuda_lib"] + os.pathsep + str(root / "toolchain/lib"))
            self.assertNotIn("legacy", kwargs["env"]["LD_LIBRARY_PATH"])
            plan = json.loads((attempt / "build-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["toolchain"]["cccl_include"], toolchain["cccl_include"])

    def test_elf_gate_requires_cuda13_and_relative_runtime_path(self):
        good = """
 0x1 (NEEDED) Shared library: [libcublasLt.so.13]
 0x1 (NEEDED) Shared library: [libcudart.so.13]
 0x1d (RUNPATH) Library runpath: [$ORIGIN/../torch/lib:$ORIGIN/../nvidia/cu13/lib]
"""
        with tempfile.TemporaryDirectory() as temporary:
            extension = Path(temporary) / "common_ops.so"
            extension.write_bytes(b"elf")
            with mock.patch.object(installer.subprocess, "check_output", return_value=good):
                self.assertEqual(installer._verify_elf(extension, readelf="readelf")["sha256"], installer._sha256(extension))
            cuda12 = good.replace("libcudart.so.13", "libcudart.so.12")
            with mock.patch.object(installer.subprocess, "check_output", return_value=cuda12):
                with self.assertRaisesRegex(RuntimeError, "missing CUDA 13|CUDA 12"):
                    installer._verify_elf(extension, readelf="readelf")
            with mock.patch.object(installer.subprocess, "check_output", return_value=good.replace("$ORIGIN", "/absolute", 1)):
                with self.assertRaisesRegex(RuntimeError, "runpath"):
                    installer._verify_elf(extension, readelf="readelf")

    def test_wheel_packaging_is_deterministic_and_carries_both_licenses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            for relative in installer.PACKAGE_SOURCES:
                (package / Path(relative).name).write_text(relative, encoding="utf-8")
            (package / "common_ops.so").write_bytes(b"ELF fixture")
            source = root / "source"
            cutlass = root / "cutlass"
            source.mkdir(); cutlass.mkdir()
            (source / "LICENSE").write_text("Apache", encoding="utf-8")
            (cutlass / "LICENSE.txt").write_text("CUTLASS", encoding="utf-8")
            first = installer._write_wheel(package, source, cutlass, root / "one")
            second = installer._write_wheel(package, source, cutlass, root / "two")
            self.assertEqual(first["wheel_sha256"], second["wheel_sha256"])
            import zipfile
            with zipfile.ZipFile(first["wheel"]) as archive:
                names = archive.namelist()
                metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
                metadata = archive.read(metadata_name).decode("utf-8")
            self.assertTrue(any(name.endswith("/licenses/LICENSE") for name in names))
            self.assertTrue(any(name.endswith("/licenses/CUTLASS-LICENSE.txt") for name in names))
            self.assertIn(f"Version: {installer.VERSION}\n", metadata)
            self.assertIn("Requires-Dist: torch==2.10.0\n", metadata)


class LightX2VRuntimeAndTransactionTests(unittest.TestCase):
    def test_environment_gate_accepts_uv_venv_and_selected_conda_but_rejects_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conda_root = root / "conda"
            selected = conda_root / "envs/selected"
            executable = conda_root / "bin/conda"
            executable.parent.mkdir(parents=True)
            executable.write_text("fixture", encoding="utf-8")
            executable.chmod(0o755)
            for prefix in (conda_root, selected):
                history = prefix / "conda-meta/history"
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_text("fixture", encoding="utf-8")

            with mock.patch.object(installer.subprocess, "check_output", side_effect=AssertionError):
                self.assertTrue(installer._selected_python_is_isolated(
                    root / "uv-venv", root / "uv-base", environment={},
                ))

            selected_info = json.dumps({
                "active_prefix": str(selected), "root_prefix": str(conda_root),
            })
            selected_environment = {
                "CONDA_PREFIX": str(selected), "CONDA_EXE": str(executable),
            }
            with mock.patch.object(installer.subprocess, "check_output", return_value=selected_info):
                self.assertTrue(installer._selected_python_is_isolated(
                    selected, selected, environment=selected_environment,
                ))

            base_info = json.dumps({
                "active_prefix": str(conda_root), "root_prefix": str(conda_root),
            })
            base_environment = {
                "CONDA_PREFIX": str(conda_root), "CONDA_EXE": str(executable),
            }
            with mock.patch.object(installer.subprocess, "check_output", return_value=base_info):
                self.assertFalse(installer._selected_python_is_isolated(
                    conda_root, conda_root, environment=base_environment,
                ))
            self.assertFalse(installer._selected_python_is_isolated(
                root, root, environment={},
            ))

    def test_runtime_gate_checks_python_torch_cuda_and_selected_paths_without_gpu_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "venv"
            site = prefix / "lib/python3.11/site-packages"
            executable = prefix / "bin/python"
            executable.parent.mkdir(parents=True)
            executable.symlink_to(Path(sys.executable).resolve())
            include = site / "nvidia/cu13/include"
            library = site / "nvidia/cu13/lib"
            include.mkdir(parents=True); library.mkdir(parents=True)
            for path in (include / "cuda.h", library / "libcublas.so.13",
                         library / "libcublasLt.so.13", library / "libcudart.so.13"):
                path.write_bytes(b"fixture")
            cuda = SimpleNamespace(cuda="13.0")
            torch = SimpleNamespace(__version__="2.10.0+cu130", version=cuda)
            version_info = FakeVersionInfo()
            with mock.patch.object(installer.platform, "system", return_value="Linux"), \
                 mock.patch.object(installer.platform, "machine", return_value="x86_64"), \
                 mock.patch.object(installer.sys, "version_info", version_info), \
                 mock.patch.object(installer.sys, "prefix", str(prefix)), \
                 mock.patch.object(installer.sys, "base_prefix", str(root / "base")), \
                 mock.patch.object(installer.sys, "executable", str(executable)), \
                 mock.patch.object(installer.sysconfig, "get_path", return_value=str(site)):
                identity = installer._runtime_identity(torch)
                self.assertEqual(identity["torch_cuda"], "13.0")
                self.assertEqual(identity["executable"], str(executable))
                torch.__version__ = "2.10.0+cu128"
                with self.assertRaisesRegex(RuntimeError, r"2.10.0\+cu130"):
                    installer._runtime_identity(torch)
            self.assertFalse(hasattr(torch, "cuda"))

    def test_run_terminates_the_process_group_on_timeout(self):
        process = mock.Mock(pid=4312)
        process.wait.side_effect = [subprocess.TimeoutExpired(["worker"], 1), 0]
        with mock.patch.object(installer.subprocess, "Popen", return_value=process) as opened, \
             mock.patch.object(installer.os, "killpg") as killed:
            with self.assertRaises(subprocess.TimeoutExpired):
                installer._run(["worker"], timeout=1)
        self.assertTrue(opened.call_args.kwargs["start_new_session"])
        self.assertEqual(killed.call_args_list, [
            mock.call(4312, installer.signal.SIGTERM),
            mock.call(4312, installer.signal.SIGKILL),
        ])

    def test_runtime_and_state_locks_serialize_competing_installers(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "selected/.maestro.lock"
            with installer._exclusive_lock(lock):
                with self.assertRaisesRegex(TimeoutError, "another LightX2V installer"):
                    with installer._exclusive_lock(lock, timeout=0):
                        self.fail("a competing installer acquired the selected runtime lock")
            with installer._exclusive_lock(lock, timeout=0):
                self.assertTrue(lock.is_file())

    def test_postinstall_binds_fresh_process_to_selected_cuda13_libraries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            write_distribution(
                site, version=installer.VERSION, package_text="verified", metadata_text="metadata",
            )
            identity = runtime_identity(root)
            package = site / "lightx2v_kernel"
            payload = {
                f"lightx2v_kernel/{name}": digest
                for name, digest in installer._tree_manifest(package, ignore_cache=True).items()
            }
            expected_mapped = {
                "extension": str((package / "common_ops.so").resolve()),
                "cublas_lt": str(Path(identity["cublas_lt"]).resolve()),
                "cuda_runtime": str(Path(identity["cuda_runtime"]).resolve()),
            }

            def imported(command, **kwargs):
                self.assertIn("/proc/self/maps", command[-1])
                self.assertIn("libcudart.so.12", command[-1])
                self.assertEqual(
                    kwargs["env"]["LD_LIBRARY_PATH"],
                    str(site / "torch/lib") + os.pathsep + identity["cuda_lib"],
                )
                self.assertNotIn("legacy-cuda12", kwargs["env"]["LD_LIBRARY_PATH"])
                return SimpleNamespace(stdout=json.dumps({
                    "version": installer.VERSION,
                    "operators": list(installer.EXPECTED_OPERATORS),
                    "mapped": expected_mapped,
                }))

            with mock.patch.object(installer, "_verify_elf", return_value={"sha256": payload["lightx2v_kernel/common_ops.so"]}), \
                 mock.patch.object(installer.subprocess, "run", side_effect=imported), \
                 mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/legacy-cuda12"}):
                validation = installer._validate_installed(
                    site=site, package_manifest={"payload": payload},
                    identity=identity, readelf="readelf",
                )
            self.assertEqual(validation["abi"]["mapped"], expected_mapped)

    def test_postinstall_rejects_duplicate_metadata_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            (site / "lightx2v_kernel").mkdir()
            (site / f"lightx2v_kernel-{installer.VERSION}.dist-info").mkdir()
            (site / "lightx2v_kernel-legacy.dist-info").mkdir()
            with mock.patch.object(installer.subprocess, "run") as imported:
                with self.assertRaisesRegex(RuntimeError, "missing, duplicated"):
                    installer._validate_installed(
                        site=site, package_manifest={"payload": {}},
                        identity=runtime_identity(root), readelf="readelf",
                    )
            imported.assert_not_called()

    def test_validation_failure_restores_exact_package_metadata_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            write_distribution(site, version="0.0.2+torch2.10.0", package_text="old", metadata_text="old metadata")
            unrelated = site / "unrelated.txt"
            unrelated.write_text("preserve", encoding="utf-8")
            receipt = root / "venv" / installer.RECEIPT_NAME
            receipt.parent.mkdir()
            receipt.write_bytes(b"old receipt")
            attempt = root / "attempt"
            attempt.mkdir()
            old_snapshot = installer._snapshot_distribution(site)

            def run(command, **_kwargs):
                self.assertEqual(command[:5], ["/pinokio/uv", "pip", "install", "--python", sys.executable])
                for flag in ("--no-index", "--no-deps", "--force-reinstall"):
                    self.assertIn(flag, command)
                installer._remove_distribution(site)
                write_distribution(site, version=installer.VERSION, package_text="new", metadata_text="new metadata")

            with mock.patch.object(installer, "STATE_ROOT", root / "state"), \
                 mock.patch.object(installer, "_run", side_effect=run), \
                 mock.patch.object(installer, "_atomic_bytes", wraps=installer._atomic_bytes) as restored:
                with self.assertRaisesRegex(RuntimeError, "synthetic ABI failure"):
                    installer.install_transaction(
                        wheel=root / "candidate.whl", uv="/pinokio/uv", site=site,
                        attempt=attempt, receipt_path=receipt, identity=runtime_identity(root),
                        toolchain={"packages_sha256": "toolchain"},
                        package_manifest={"wheel_sha256": "wheel", "payload": {}},
                        readelf="readelf",
                        validator=lambda **_: (_ for _ in ()).throw(RuntimeError("synthetic ABI failure")),
                    )
            self.assertEqual(installer._snapshot_distribution(site), old_snapshot)
            self.assertEqual(receipt.read_bytes(), b"old receipt")
            restored.assert_called_once_with(receipt, b"old receipt")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(
                json.loads((attempt / "baseline.json").read_text(encoding="utf-8"))["files"],
                old_snapshot,
            )

    def test_receipt_idempotency_detects_changed_installed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            write_distribution(site, version=installer.VERSION, package_text="verified", metadata_text="metadata")
            identity = runtime_identity(root)
            installed = installer._snapshot_distribution(site)
            payload = {
                name: digest for name, digest in installed.items()
                if name.startswith("lightx2v_kernel/")
            }
            receipt = {
                "schema_version": installer.RECEIPT_SCHEMA,
                "context": installer._receipt_context(identity),
                "roots": [path.name for path in installer._distribution_roots(site)],
                "installed_files": installed,
                "wheel_sha256": "a" * 64,
                "payload": payload,
                "toolchain_packages_sha256": "b" * 64,
                "validation": {
                    "evidence": "package-and-abi-only-no-gpu-execution",
                    "elf": {"sha256": payload["lightx2v_kernel/common_ops.so"]},
                    "abi": {
                        "version": installer.VERSION,
                        "operators": list(installer.EXPECTED_OPERATORS),
                        "mapped": {
                            "extension": str((site / "lightx2v_kernel/common_ops.so").resolve()),
                            "cublas_lt": str(Path(identity["cublas_lt"]).resolve()),
                            "cuda_runtime": str(Path(identity["cuda_runtime"]).resolve()),
                        },
                    },
                },
            }
            self.assertTrue(installer.installation_matches_receipt(site, receipt, identity))
            with mock.patch.object(installer, "_installer_recipe_sha256", return_value="c" * 64):
                self.assertFalse(installer.installation_matches_receipt(site, receipt, identity))
            missing_payload = dict(receipt)
            missing_payload.pop("payload")
            self.assertFalse(installer.installation_matches_receipt(site, missing_payload, identity))
            (site / "lightx2v_kernel/gemm.py").write_text("changed bytes", encoding="utf-8")
            self.assertFalse(installer.installation_matches_receipt(site, receipt, identity))

    def test_new_attempt_never_discards_earlier_rollback_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer, "APP_ROOT", Path(temporary)):
            state = Path(temporary) / ".lightx2v-runtime"
            first = installer._new_attempt(state)
            rollback = first / "rollback/lightx2v_kernel/gemm.py"
            rollback.parent.mkdir(parents=True)
            rollback.write_text("only rollback copy", encoding="utf-8")
            second = installer._new_attempt(state)
            third = installer._new_attempt(state)
            self.assertEqual(rollback.read_text(encoding="utf-8"), "only rollback copy")
            self.assertEqual(len({first.name, second.name, third.name}), 3)

    def test_main_rejects_symlinked_state_before_creating_an_outside_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            state = root / ".lightx2v-runtime"
            state.symlink_to(outside, target_is_directory=True)
            with mock.patch.object(installer, "APP_ROOT", root), \
                 mock.patch.object(installer, "STATE_ROOT", state), \
                 mock.patch.object(installer, "_runtime_identity", return_value=runtime_identity(root)):
                with self.assertRaisesRegex(RuntimeError, "inside the Maestro app directory"):
                    installer.main()
            self.assertEqual(list(outside.iterdir()), [])

    def test_build_failure_occurs_before_any_installed_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            site = root / "site"
            site.mkdir()
            write_distribution(site, version="legacy", package_text="old", metadata_text="old")
            before = installer._snapshot_distribution(site)
            identity = runtime_identity(root)
            install = mock.Mock()
            with mock.patch.object(installer, "STATE_ROOT", root / "state"), \
                 mock.patch.object(installer, "APP_ROOT", root), \
                 mock.patch.object(installer, "_runtime_identity", return_value=identity), \
                 mock.patch.object(installer, "_read_receipt", return_value={}), \
                 mock.patch.object(installer, "installation_matches_receipt", return_value=False), \
                 mock.patch.object(installer, "_resolve_tool", side_effect=lambda name: f"/{name}"), \
                 mock.patch.object(installer, "_conda_executable", return_value="/conda"), \
                 mock.patch.object(installer, "prepare_sources"), \
                 mock.patch.object(installer, "ensure_toolchain", return_value={}), \
                 mock.patch.object(installer, "build_package", side_effect=RuntimeError("compile failed")), \
                 mock.patch.object(installer, "install_transaction", install):
                with self.assertRaisesRegex(RuntimeError, "compile failed"):
                    installer.main()
            install.assert_not_called()
            self.assertEqual(installer._snapshot_distribution(site), before)


if __name__ == "__main__":
    unittest.main()
