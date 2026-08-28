"""CPU-only tests for blocked H3 prompt-rewriter runtime admission."""

from __future__ import annotations

import ast
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SOURCE = APP / "services" / "h3_prompt_rewriter_runtime.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


# Establish the no-runtime-import/no-spawn proof before importing the module.
_SOURCE_TREE = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
_FORBIDDEN_IMPORTS = {
    "aiohttp",
    "ftplib",
    "grpc",
    "http",
    "httpx",
    "peft",
    "requests",
    "socket",
    "ssl",
    "subprocess",
    "torch",
    "transformers",
    "urllib",
    "urllib3",
    "websockets",
}
_IMPORTED_NAMES = {
    (node.module or "").split(".")[0]
    if isinstance(node, ast.ImportFrom)
    else alias.name.split(".")[0]
    for node in ast.walk(_SOURCE_TREE)
    if isinstance(node, (ast.Import, ast.ImportFrom))
    for alias in node.names
}
if not _FORBIDDEN_IMPORTS.isdisjoint(_IMPORTED_NAMES):
    raise AssertionError("runtime module imports a model, process, or network library")

_FORBIDDEN_CALLS = []
for node in ast.walk(_SOURCE_TREE):
    if not isinstance(node, ast.Call):
        continue
    if isinstance(node.func, ast.Name) and node.func.id in {
        "__import__",
        "eval",
        "exec",
        "import_module",
    }:
        _FORBIDDEN_CALLS.append(node.func.id)
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"import_module", "exec_module"}
    ):
        _FORBIDDEN_CALLS.append(node.func.attr)
    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and (
            node.func.attr in {
                "fork",
                "forkpty",
                "kill",
                "posix_spawn",
                "posix_spawnp",
                "system",
            }
            or node.func.attr.startswith(("exec", "spawn"))
        )
    ):
        _FORBIDDEN_CALLS.append(f"os.{node.func.attr}")
if _FORBIDDEN_CALLS:
    raise AssertionError(f"runtime module has forbidden calls: {_FORBIDDEN_CALLS}")

_RUNTIME_MODULE_NAME = "services.h3_prompt_rewriter_runtime"
_RUNTIME_MODULE_ABSENT_BEFORE_IMPORT = _RUNTIME_MODULE_NAME not in sys.modules
if not _RUNTIME_MODULE_ABSENT_BEFORE_IMPORT:
    raise AssertionError("runtime module was already present before clean import")
_MODULES_BEFORE_RUNTIME_IMPORT = set(sys.modules)
with ExitStack() as _import_guards:
    for _name in (
        "Popen",
        "call",
        "check_call",
        "check_output",
        "run",
    ):
        _import_guards.enter_context(
            mock.patch.object(
                subprocess,
                _name,
                side_effect=AssertionError("spawn during runtime import"),
            )
        )
    _import_guards.enter_context(
        mock.patch.object(
            os,
            "system",
            side_effect=AssertionError("os.system during runtime import"),
        )
    )
    for _name in dir(os):
        if _name in {"fork", "forkpty", "posix_spawn", "posix_spawnp"} or (
            _name.startswith(("exec", "spawn"))
        ):
            _import_guards.enter_context(
                mock.patch.object(
                    os,
                    _name,
                    side_effect=AssertionError("os.spawn during runtime import"),
                )
            )
    from services import h3_prompt_rewriter as rewriter  # noqa: E402
    from services import h3_prompt_rewriter_dependency_closure as closure  # noqa: E402
    from services import h3_prompt_rewriter_runtime as runtime  # noqa: E402

_MODULES_AFTER_RUNTIME_IMPORT = set(sys.modules)
_RUNTIME_IMPORT_DELTA = _MODULES_AFTER_RUNTIME_IMPORT - _MODULES_BEFORE_RUNTIME_IMPORT


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _dependency_evidence() -> tuple[bytes, str]:
    payload = closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
    plan = closure.build_h3_prompt_rewriter_dependency_closure_plan(payload)
    return payload, plan.document["input_sha256"]


def _make_layout(
    directory: str,
) -> tuple[Path, runtime.H3PromptRewriterRuntimeLayout]:
    feature_root = Path(directory) / runtime.RUNTIME_ROOT_NAME
    feature_root.mkdir(mode=0o700)
    for name in ("generations", "staging", "state", "cache", "tmp", "home"):
        (feature_root / name).mkdir(mode=0o700)
    return feature_root, runtime.resolve_h3_prompt_rewriter_runtime_layout(
        feature_root,
        allow_root_owned_sticky_temp_ancestor=True,
    )


def _make_metadata_candidates(directory: str) -> tuple[Path, Path, Path]:
    trust_root = Path(directory) / "artifacts"
    adapter = trust_root / "adapter"
    base = trust_root / "base"
    adapter.mkdir(mode=0o700, parents=True)
    base.mkdir(mode=0o700)
    trust_root.chmod(0o700)

    adapter_weight = adapter / rewriter.ADAPTER_FILENAME
    adapter_weight.touch(mode=0o600)
    os.truncate(adapter_weight, rewriter.ADAPTER_SIZE_BYTES)
    (adapter / "adapter_model.maestro-source.json").write_text(
        json.dumps({
            "repo_id": rewriter.ADAPTER_REPO_ID,
            "revision": rewriter.ADAPTER_REVISION,
            "filename": rewriter.ADAPTER_FILENAME,
            "size_bytes": rewriter.ADAPTER_SIZE_BYTES,
            "sha256": rewriter.ADAPTER_SHA256,
            "tensor_count": rewriter.ADAPTER_TENSOR_COUNT,
        }),
        encoding="utf-8",
    )
    (adapter / "adapter_config.json").write_text(
        json.dumps({
            "base_model_name_or_path": rewriter.BASE_REPO_ID,
            "peft_version": rewriter.PEFT_VERSION,
            "r": rewriter.ADAPTER_RANK,
            "target_modules": list(rewriter.ADAPTER_TARGET_MODULES),
        }),
        encoding="utf-8",
    )

    metadata_root = base / ".cache" / "huggingface" / "download"
    metadata_root.mkdir(mode=0o700, parents=True)
    for parent in (base / ".cache", base / ".cache" / "huggingface"):
        parent.chmod(0o700)
    for name, size, digest in rewriter.BASE_SHARDS:
        shard = base / name
        shard.touch(mode=0o600)
        os.truncate(shard, size)
        (metadata_root / f"{name}.metadata").write_text(
            f"{rewriter.BASE_REVISION} {digest}\n",
            encoding="utf-8",
        )
    (base / "config.json").write_text(
        json.dumps({
            "model_type": "qwen3_vl",
            "architectures": ["Qwen3VLForConditionalGeneration"],
        }),
        encoding="utf-8",
    )
    (base / "preprocessor_config.json").write_text(
        json.dumps({
            "image_processor_type": "Qwen2VLImageProcessorFast",
            "processor_class": "Qwen3VLProcessor",
        }),
        encoding="utf-8",
    )
    (base / "model.safetensors.index.json").write_text(
        json.dumps({
            "metadata": {"total_size": rewriter.BASE_TENSOR_TOTAL_SIZE},
            "weight_map": {
                f"tensor_{index}": name
                for index, (name, _size, _digest) in enumerate(
                    rewriter.BASE_SHARDS
                )
            },
        }),
        encoding="utf-8",
    )
    return trust_root, adapter, base


def _build_admission(
    runtime_root: Path,
    trust_root: Path,
    adapter: Path,
    base: Path,
    *,
    mode: str = "t2va",
    ambient_environment: dict[str, str] | None = None,
) -> runtime.H3PromptRewriterRuntimeAdmission:
    payload, expected = _dependency_evidence()
    return runtime.build_h3_prompt_rewriter_runtime_admission(
        runtime_root,
        mode=mode,
        artifact_trust_root=trust_root,
        adapter_directory=adapter,
        base_directory=base,
        dependency_payload=payload,
        expected_dependency_input_sha256=expected,
        ambient_environment=ambient_environment,
        allow_root_owned_sticky_temp_ancestor=True,
    )


class H3PromptRewriterRuntimeTests(unittest.TestCase):
    def test_clean_import_was_guarded_and_loaded_no_runtime_libraries(self):
        self.assertTrue(_RUNTIME_MODULE_ABSENT_BEFORE_IMPORT)
        self.assertEqual(_FORBIDDEN_CALLS, [])
        self.assertTrue(_FORBIDDEN_IMPORTS.isdisjoint(_IMPORTED_NAMES))
        self.assertFalse(
            any(
                name == prefix or name.startswith(f"{prefix}.")
                for name in _RUNTIME_IMPORT_DELTA
                for prefix in ("torch", "transformers", "peft")
            )
        )

    def test_clean_subprocess_import_starts_absent_and_never_loads_runtime_libs(self):
        script = f"""
import sys
assert {_RUNTIME_MODULE_NAME!r} not in sys.modules
assert all(name not in sys.modules for name in ('torch', 'transformers', 'peft'))
sys.path.insert(0, {str(APP)!r})
import services.h3_prompt_rewriter_runtime
assert {_RUNTIME_MODULE_NAME!r} in sys.modules
assert all(name not in sys.modules for name in ('torch', 'transformers', 'peft'))
"""
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
            "HIP_VISIBLE_DEVICES": "",
            "ROCR_VISIBLE_DEVICES": "",
            "NVIDIA_VISIBLE_DEVICES": "void",
        }
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_portable_dry_root_does_not_trust_configured_pinokio_home(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            configured_pinokio_home = parent / "configured-pinokio-home"
            configured_pinokio_home.mkdir(mode=0o700)
            configured_pinokio_home.chmod(0o775)

            unsafe_feature = configured_pinokio_home / runtime.RUNTIME_ROOT_NAME
            unsafe_feature.mkdir(mode=0o700)
            for name in (
                "generations",
                "staging",
                "state",
                "cache",
                "tmp",
                "home",
            ):
                (unsafe_feature / name).mkdir(mode=0o700)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError,
                "ancestor",
            ):
                runtime.resolve_h3_prompt_rewriter_runtime_layout(
                    unsafe_feature,
                    allow_root_owned_sticky_temp_ancestor=True,
                )
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime.resolve_h3_prompt_rewriter_runtime_layout(
                    configured_pinokio_home,
                    allow_root_owned_sticky_temp_ancestor=True,
                )

            private_parent = parent / "private-runtime-parent"
            private_parent.mkdir(mode=0o700)
            feature_root = private_parent / runtime.RUNTIME_ROOT_NAME
            feature_root.mkdir(mode=0o700)
            for name in (
                "generations",
                "staging",
                "state",
                "cache",
                "tmp",
                "home",
            ):
                (feature_root / name).mkdir(mode=0o700)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError,
                "ancestor",
            ):
                runtime.resolve_h3_prompt_rewriter_runtime_layout(feature_root)
            layout = runtime.resolve_h3_prompt_rewriter_runtime_layout(
                feature_root,
                allow_root_owned_sticky_temp_ancestor=True,
            )
            self.assertEqual(layout.root, feature_root)
            self.assertNotEqual(layout.root, configured_pinokio_home)
            self.assertEqual(
                configured_pinokio_home.stat().st_mode & 0o777,
                0o775,
            )

    def test_layout_is_canonical_owner_private_and_returns_stat_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            admission = _build_admission(pinokio, trust_root, adapter, base)
            receipt = admission.private_receipt()
            self.assertEqual(receipt.layout, layout)
            self.assertEqual(receipt.artifact_trust_root, trust_root)
            self.assertEqual(receipt.adapter_directory, adapter)
            self.assertEqual(receipt.base_directory, base)
            self.assertTrue(receipt.identities)
            for identity in receipt.identities:
                self.assertIs(type(identity.dev), int)
                self.assertIs(type(identity.inode), int)
                self.assertIs(type(identity.mode), int)
                self.assertIs(type(identity.uid), int)
                self.assertTrue(identity.path.is_absolute())
            self.assertTrue(
                runtime.recheck_h3_prompt_rewriter_runtime_admission(admission)
            )
            adapter.chmod(0o750)
            self.assertFalse(
                runtime.recheck_h3_prompt_rewriter_runtime_admission(admission)
            )

    def test_layout_and_artifact_directory_failures_are_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)

            layout.staging.rmdir()
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime.resolve_h3_prompt_rewriter_runtime_layout(
                    pinokio,
                    allow_root_owned_sticky_temp_ancestor=True,
                )
            layout.staging.mkdir(mode=0o700)

            layout.root.chmod(0o750)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError, "owner-private"
            ):
                runtime.resolve_h3_prompt_rewriter_runtime_layout(
                    pinokio,
                    allow_root_owned_sticky_temp_ancestor=True,
                )
            layout.root.chmod(0o700)

            with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(
                    runtime.H3PromptRewriterRuntimeSecurityError, "owned"
                ):
                    runtime.resolve_h3_prompt_rewriter_runtime_layout(
                        pinokio,
                        allow_root_owned_sticky_temp_ancestor=True,
                    )

            trust_root.chmod(0o777)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError, "owner-private"
            ):
                runtime._validate_artifact_directories(
                    trust_root,
                    adapter,
                    base,
                    allow_root_owned_sticky_temp_ancestor=True,
                )
            trust_root.chmod(0o700)

            unsafe_parent = Path(directory) / "world-writable-nonsticky"
            unsafe_parent.mkdir(mode=0o700)
            unsafe_parent.chmod(0o777)
            unsafe_trust = unsafe_parent / "artifact-trust"
            unsafe_adapter = unsafe_trust / "adapter"
            unsafe_base = unsafe_trust / "base"
            unsafe_adapter.mkdir(mode=0o700, parents=True)
            unsafe_base.mkdir(mode=0o700)
            unsafe_trust.chmod(0o700)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError,
                "ancestor",
            ):
                runtime._validate_artifact_directories(
                    unsafe_trust,
                    unsafe_adapter,
                    unsafe_base,
                    allow_root_owned_sticky_temp_ancestor=True,
                )

            with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(
                    runtime.H3PromptRewriterRuntimeSecurityError,
                    "owned",
                ):
                    runtime._validate_artifact_directories(
                        trust_root,
                        adapter,
                        base,
                        allow_root_owned_sticky_temp_ancestor=True,
                    )

            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._validate_artifact_directories(
                    trust_root,
                    trust_root / "missing",
                    base,
                    allow_root_owned_sticky_temp_ancestor=True,
                )
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError,
                "canonical",
            ):
                runtime._validate_artifact_directories(
                    trust_root,
                    adapter,
                    adapter / ".." / "base",
                    allow_root_owned_sticky_temp_ancestor=True,
                )

            target = Path(directory) / "adapter-target"
            adapter.rename(target)
            adapter.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                runtime.H3PromptRewriterRuntimeSecurityError, "symlink"
            ):
                runtime._validate_artifact_directories(
                    trust_root,
                    adapter,
                    base,
                    allow_root_owned_sticky_temp_ancestor=True,
                )

    def test_artifact_directory_substitution_invalidates_private_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_root, _layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            admission = _build_admission(
                runtime_root,
                trust_root,
                adapter,
                base,
            )
            self.assertTrue(
                runtime.recheck_h3_prompt_rewriter_runtime_admission(admission)
            )
            original = trust_root / "adapter-original"
            adapter.rename(original)
            adapter.mkdir(mode=0o700)
            self.assertFalse(
                runtime.recheck_h3_prompt_rewriter_runtime_admission(admission)
            )

    def test_dependency_digest_is_mandatory_and_precedes_artifact_checks(self):
        payload, expected = _dependency_evidence()
        with mock.patch.object(
            runtime, "resolve_h3_prompt_rewriter_runtime_layout"
        ) as layout, mock.patch.object(
            runtime.rewriter, "inspect_local_candidate"
        ) as inspect:
            for invalid in (None, "a" * 63, "A" * 64, "0" * 64):
                with self.subTest(invalid=invalid), self.assertRaises(
                    runtime.H3PromptRewriterRuntimeError
                ):
                    runtime.build_h3_prompt_rewriter_runtime_admission(
                        "/never-inspected",
                        mode="t2va",
                        artifact_trust_root="/never-inspected",
                        adapter_directory="/never-inspected",
                        base_directory="/never-inspected",
                        dependency_payload=payload,
                        expected_dependency_input_sha256=invalid,
                    )
            layout.assert_not_called()
            inspect.assert_not_called()

        with self.assertRaises(TypeError):
            runtime.build_h3_prompt_rewriter_runtime_admission(
                "/never-inspected",
                mode="t2va",
                artifact_trust_root="/never-inspected",
                adapter_directory="/never-inspected",
                base_directory="/never-inspected",
                dependency_payload=payload,
            )
        self.assertRegex(expected, r"^[0-9a-f]{64}$")

    def test_modes_reuse_canonical_lowercase_and_ref2va_is_earliest_rejection(self):
        self.assertIs(runtime.SUPPORTED_MODES, rewriter.SUPPORTED_MODES)
        self.assertEqual(
            runtime.SUPPORTED_MODES,
            ("t2va", "i2va", "l2va", "fl2va"),
        )
        for invalid in ("Ref2VA", "ref2va", "T2VA"):
            with self.subTest(invalid=invalid), mock.patch.object(
                runtime, "_validated_dependency_plan"
            ) as dependency, mock.patch.object(
                runtime, "resolve_h3_prompt_rewriter_runtime_layout"
            ) as layout, mock.patch.object(
                runtime.rewriter, "inspect_local_candidate"
            ) as inspect:
                with self.assertRaisesRegex(
                    runtime.H3PromptRewriterRuntimeError,
                    "Ref2VA is unsupported",
                ):
                    runtime.build_h3_prompt_rewriter_runtime_admission(
                        "/never-inspected",
                        mode=invalid,
                        artifact_trust_root="/never-inspected",
                        adapter_directory="/never-inspected",
                        base_directory="/never-inspected",
                        dependency_payload=b"never-inspected",
                        expected_dependency_input_sha256="0" * 64,
                    )
                dependency.assert_not_called()
                layout.assert_not_called()
                inspect.assert_not_called()

    def test_candidate_metadata_never_claims_exact_bytes_or_complete_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, _layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            payload, expected = _dependency_evidence()
            bound_plan = closure.build_h3_prompt_rewriter_dependency_closure_plan(
                payload,
                expected_input_sha256=expected,
            )
            admission = _build_admission(pinokio, trust_root, adapter, base)
            status = admission.public_status()
            self.assertTrue(status["candidate_metadata_compatible"])
            self.assertFalse(status["artifact_bytes_verified"])
            self.assertFalse(status["exact_byte_receipts_available"])
            self.assertFalse(status["launch_time_byte_recheck_available"])
            self.assertFalse(status["admission_complete"])
            self.assertFalse(status["runtime_admission_ready"])
            self.assertFalse(status["execution_available"])
            self.assertEqual(
                status["expected_adapter_identity_sha256"],
                _sha(rewriter.adapter_descriptor()),
            )
            self.assertEqual(
                status["expected_base_identity_sha256"],
                _sha(rewriter.base_descriptor()),
            )
            self.assertEqual(
                status["dependency_plan_sha256"], bound_plan.sha256
            )
            self.assertEqual(status["dependency_input_sha256"], expected)
            self.assertEqual(
                status["dependency_blockers"], bound_plan.document["blockers"]
            )

    def test_passive_status_never_opens_or_hashes_model_weight_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, _layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            weight_names = {
                rewriter.ADAPTER_FILENAME,
                *(name for name, _size, _digest in rewriter.BASE_SHARDS),
            }
            real_os_open = os.open
            opened_weights: list[str] = []

            def guarded_os_open(path, *args, **kwargs):
                candidate = os.fspath(path)
                if Path(candidate).name in weight_names:
                    opened_weights.append(candidate)
                    raise AssertionError("model weight bytes were opened")
                return real_os_open(path, *args, **kwargs)

            before_files = {
                item.relative_to(directory) for item in Path(directory).rglob("*")
            }
            with mock.patch.object(
                rewriter.os,
                "open",
                side_effect=guarded_os_open,
            ):
                status = _build_admission(
                    pinokio, trust_root, adapter, base
                ).public_status()
            after_files = {
                item.relative_to(directory) for item in Path(directory).rglob("*")
            }
            self.assertEqual(opened_weights, [])
            self.assertEqual(before_files, after_files)
            self.assertTrue(status["candidate_metadata_compatible"])
            self.assertFalse(status["artifact_bytes_verified"])

    def test_child_environment_is_offline_and_unconditionally_gpu_masked(self):
        with tempfile.TemporaryDirectory() as directory:
            _pinokio, layout = _make_layout(directory)
            environment = runtime.build_h3_prompt_rewriter_child_environment(
                layout,
                ambient_environment={
                    "PATH": "/ambient/bin",
                    "CUDA_VISIBLE_DEVICES": "2",
                    "HIP_VISIBLE_DEVICES": "3",
                    "ROCR_VISIBLE_DEVICES": "4",
                    "NVIDIA_VISIBLE_DEVICES": "all",
                },
                allow_root_owned_sticky_temp_ancestor=True,
            )
            self.assertEqual(environment["PATH"], "/usr/bin:/bin")
            self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(environment["HIP_VISIBLE_DEVICES"], "")
            self.assertEqual(environment["ROCR_VISIBLE_DEVICES"], "")
            self.assertEqual(environment["NVIDIA_VISIBLE_DEVICES"], "void")
            self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
            self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
            self.assertNotIn("PYTHONPATH", environment)

            for forbidden in (
                {"PYTHONPATH": "/ambient/source"},
                {"OPENAI_API_KEY": "not-a-real-secret"},
                {"HF_TOKEN": "not-a-real-secret"},
                {"CUSTOM_SECRET": "not-a-real-secret"},
            ):
                with self.subTest(forbidden=next(iter(forbidden))), self.assertRaises(
                    runtime.H3PromptRewriterRuntimeSecurityError
                ):
                    runtime.build_h3_prompt_rewriter_child_environment(
                        layout,
                        ambient_environment=forbidden,
                        allow_root_owned_sticky_temp_ancestor=True,
                    )

    def test_public_schema_has_exact_false_semantics_and_recursive_path_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, _layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            admission = _build_admission(pinokio, trust_root, adapter, base)
            status = admission.public_status()
            runtime._assert_public_status(status)
            encoded = json.dumps(status, sort_keys=True)
            self.assertNotIn(directory, encoded)
            self.assertNotIn(str(pinokio), encoded)
            self.assertNotIn(str(adapter), encoded)
            self.assertNotIn(str(base), encoded)
            for field in (
                "artifact_bytes_verified",
                "exact_byte_receipts_available",
                "launch_time_byte_recheck_available",
                "admission_complete",
                "runtime_admission_ready",
                "execution_available",
                "runtime_accepted",
                "gpu_accepted",
                "human_accepted",
                "automatic_fallback",
                "provider_fallback",
                "fallback_used",
                "spawn_supported",
                "cancellation_supported",
                "process_lifecycle_supported",
            ):
                self.assertIs(status[field], False)
            for field in (
                "expected_adapter_identity_sha256",
                "expected_base_identity_sha256",
                "candidate_metadata_status_sha256",
                "dependency_plan_sha256",
                "dependency_input_sha256",
            ):
                self.assertRegex(status[field], r"^[0-9a-f]{64}$")

            bad = dict(status)
            bad["execution_available"] = 0
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime._assert_public_status(bad)
            bad = dict(status)
            bad["dependency_plan_sha256"] = "A" * 64
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime._assert_public_status(bad)
            bad = dict(status)
            bad["reason"] = "candidate_metadata_incomplete"
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime._assert_public_status(bad)
            bad = dict(status)
            bad["dependency_blockers"] = ["not-valid/path"]
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime._assert_public_status(bad)
            self.assertFalse(
                runtime._public_value_is_path_free(
                    {"nested": [{"safe": "/private/unix/path"}]}
                )
            )
            self.assertFalse(
                runtime._public_value_is_path_free(
                    {"nested": ({"safe": r"C:\private\windows"},)}
                )
            )
            self.assertFalse(
                runtime._public_value_is_path_free(
                    {"nested": [{"output_path": "opaque"}]}
                )
            )

    def test_all_modes_remain_blocked_with_no_fallback_or_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            pinokio, _layout = _make_layout(directory)
            trust_root, adapter, base = _make_metadata_candidates(directory)
            for mode in runtime.SUPPORTED_MODES:
                with self.subTest(mode=mode):
                    admission = _build_admission(
                        pinokio,
                        trust_root,
                        adapter,
                        base,
                        mode=mode,
                    )
                    status = admission.public_status()
                    self.assertEqual(status["mode"], mode)
                    self.assertIs(status["execution_available"], False)
                    self.assertIs(status["automatic_fallback"], False)
                    self.assertIs(status["provider_fallback"], False)
                    self.assertIs(status["fallback_used"], False)
                    self.assertIs(status["spawn_supported"], False)
                    self.assertIs(status["cancellation_supported"], False)
                    self.assertIs(status["process_lifecycle_supported"], False)
                    self.assertNotIn(directory, repr(admission))
            self.assertFalse(runtime.PROCESS_LIFECYCLE_SUPPORTED)
            self.assertFalse(runtime.CANCELLATION_SUPPORTED)
            self.assertFalse(
                any(
                    name.startswith(("start_", "spawn_", "stop_", "cancel_"))
                    for name in vars(runtime)
                )
            )


if __name__ == "__main__":
    unittest.main()
