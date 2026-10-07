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
    raise AssertionError("runtime module imports a model or network library")

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


class H3PromptRewriterChildTests(unittest.TestCase):
    """Real owned CPU children; these fixtures never qualify model execution."""

    def _child_script(self, directory, body):
        script = Path(directory) / "disposable_child.py"
        script.write_text("import os, pathlib, signal, sys, time, json\n" + body)
        return [sys.executable, "-I", "-B", str(script)]

    def _assert_reaped(self, pid_path):
        pid = int(pid_path.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_reap_observer_records_uncertainty_before_spawn_and_exact_reap(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "pid"
            marker = Path(directory) / "running"
            command = self._child_script(directory, f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\npathlib.Path({str(marker)!r}).touch()\ntime.sleep(60)\n")
            observed = []
            def observer(reaped):
                observed.append(reaped)
                if reaped:
                    self._assert_reaped(pid_path)
                else:
                    self.assertFalse(pid_path.exists())
            with self.assertRaises(runtime.H3PromptRewriterCancelled):
                runtime._owned_child(command, cwd=Path(directory), environment={},
                    cancel_check=marker.exists, stop_seconds=.2, child_reaped_observer=observer)
            self.assertEqual(observed, [False, True])

    def test_failed_checkpoint_never_spawns_and_constructor_failure_retains_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            def failed_checkpoint(_reaped):
                raise RuntimeError("checkpoint unavailable")
            with mock.patch.object(runtime.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
                    runtime._owned_child([], cwd=Path(directory), environment={},
                        child_reaped_observer=failed_checkpoint)
                spawn.assert_not_called()
            observed = []
            with mock.patch.object(runtime.subprocess, "Popen", side_effect=OSError("no child")):
                with self.assertRaises(OSError):
                    runtime._owned_child([], cwd=Path(directory), environment={},
                        child_reaped_observer=observed.append)
            self.assertEqual(observed, [False])

    def test_failed_final_wait_does_not_claim_reap(self):
        with tempfile.TemporaryDirectory() as directory:
            child = mock.Mock()
            child.poll.return_value = None
            child.wait.side_effect = subprocess.TimeoutExpired("owned", .1)
            checks = iter([True, True, False])
            observed = []
            with mock.patch.object(runtime.subprocess, "Popen", return_value=child):
                with self.assertRaises(subprocess.TimeoutExpired):
                    runtime._owned_child([], cwd=Path(directory), environment={},
                        execution_guard=lambda: next(checks), stop_seconds=.1,
                        child_reaped_observer=observed.append)
            self.assertEqual(observed, [False])
            child.terminate.assert_called_once()
            child.kill.assert_called_once()

    def test_cancellation_or_authority_loss_during_checkpoint_never_spawns(self):
        for boundary in ("cancel", "authority"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                persisted = []
                def observer(reaped):
                    persisted.append(reaped)
                with mock.patch.object(runtime.subprocess, "Popen") as spawn:
                    with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                        runtime._owned_child([], cwd=Path(directory), environment={},
                            child_reaped_observer=observer,
                            cancel_check=lambda: boundary == "cancel" and persisted == [False],
                            execution_guard=lambda: boundary != "authority" or persisted != [False])
                    spawn.assert_not_called()
                self.assertEqual(persisted, [False, True])

    def test_cancellation_stops_cold_load_and_generation_and_reaps(self):
        for phase in ("cold_load", "generation"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                pid_path = Path(directory) / "pid"
                marker = Path(directory) / phase
                command = self._child_script(directory, f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\npathlib.Path({str(marker)!r}).touch()\ntime.sleep(60)\n")
                with self.assertRaises(runtime.H3PromptRewriterCancelled):
                    runtime._owned_child(command, cwd=Path(directory), environment={"PATH": "/usr/bin:/bin"},
                                         cancel_check=marker.exists, timeout_seconds=2, stop_seconds=.2)
                self._assert_reaped(pid_path)

    def test_ignored_terminate_is_killed_and_other_process_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                pid_path, marker = Path(directory) / "pid", Path(directory) / "ready"
                command = self._child_script(directory, f"signal.signal(signal.SIGTERM, signal.SIG_IGN)\npathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\npathlib.Path({str(marker)!r}).touch()\ntime.sleep(60)\n")
                with self.assertRaises(runtime.H3PromptRewriterCancelled):
                    runtime._owned_child(command, cwd=Path(directory), environment={}, cancel_check=marker.exists,
                                         timeout_seconds=2, stop_seconds=.1)
                self._assert_reaped(pid_path)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=2)

    def test_timeout_lost_child_and_guard_failure_have_no_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "attempts"
            command = self._child_script(directory, f"pathlib.Path({str(marker)!r}).write_text('one')\ntime.sleep(60)\n")
            with self.assertRaisesRegex(runtime.H3PromptRewriterRuntimeError, "timed out"):
                runtime._owned_child(command, cwd=Path(directory), environment={}, timeout_seconds=.15, stop_seconds=.1)
            self.assertEqual(marker.read_text(), "one")
            code = runtime._owned_child(self._child_script(directory, "os._exit(7)\n"), cwd=Path(directory), environment={}, timeout_seconds=2)
            self.assertEqual(code, 7)
            marker.unlink()
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._owned_child(command, cwd=Path(directory), environment={}, execution_guard=lambda: False)
            self.assertFalse(marker.exists())

    def test_ongoing_authority_loss_stops_exact_child(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path, marker = Path(directory) / "pid", Path(directory) / "running"
            command = self._child_script(directory, f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\npathlib.Path({str(marker)!r}).touch()\ntime.sleep(60)\n")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._owned_child(command, cwd=Path(directory), environment={},
                                     execution_guard=lambda: not marker.exists(), timeout_seconds=2, stop_seconds=.1)
            self._assert_reaped(pid_path)

    @unittest.skipUnless(sys.platform == "linux", "Linux parent-death contract")
    def test_sigkill_parent_stops_and_reaps_child_even_on_supervisor_failure(self):
        # The disposable subreaper contains failure cleanup; runner settings
        # and unrelated processes remain untouched.
        for fail_supervisor in (False, True):
            with self.subTest(fail_supervisor=fail_supervisor), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ready, pid_record, receipt = root / "ready", root / "pids", root / "receipt"
                guard_import = (
                    "import importlib.util\n"
                    f"spec=importlib.util.spec_from_file_location('owned_worker_guard', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
                    "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n")
                model_child = root / "disposable_model_child.py"
                model_child.write_text("import os, pathlib, sys, time\n" + guard_import
                    + "worker.protect_parent_lifetime(int(sys.argv[1]))\n"
                    + f"pathlib.Path({str(ready)!r}).touch()\n"
                    + "time.sleep(60)\n")
                parent = root / "disposable_parent.py"
                parent.write_text("import os, pathlib, subprocess, sys, time\n" + guard_import
                    + "worker.protect_parent_lifetime(int(sys.argv[1]))\n"
                    + f"child=subprocess.Popen([sys.executable,'-I','-B',{str(model_child)!r},str(os.getpid())])\n"
                    + f"pathlib.Path({str(pid_record)!r}).write_text(str(child.pid))\n"
                    + "time.sleep(60)\n")
                supervisor = root / "disposable_supervisor.py"
                supervisor.write_text("import ctypes, json, os, pathlib, signal, subprocess, sys, time\n" + guard_import
                    + "worker.protect_parent_lifetime(os.getppid())\n"
                    + "assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0\n"
                    + f"parent=subprocess.Popen([sys.executable,'-I','-B',{str(parent)!r},str(os.getpid())])\n"
                    + "try:\n"
                    + " deadline=time.monotonic()+3\n"
                    + f" while not pathlib.Path({str(ready)!r}).exists():\n"
                    + "  assert time.monotonic()<deadline\n  time.sleep(.01)\n"
                    + (" raise RuntimeError('injected supervisor failure')\n" if fail_supervisor else " pass\n")
                    + "finally:\n"
                    + " if parent.poll() is None: parent.kill()\n"
                    + " parent.wait(timeout=2)\n"
                    + f" record=pathlib.Path({str(pid_record)!r})\n"
                    + " if record.exists():\n"
                    + "  child_pid=int(record.read_text())\n"
                    + "  deadline=time.monotonic()+2\n"
                    + "  while True:\n"
                    + "   pid,status=os.waitpid(child_pid,os.WNOHANG)\n"
                    + "   if pid: break\n"
                    + "   assert time.monotonic()<deadline\n   time.sleep(.01)\n"
                    + "  assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL\n"
                    + f"  pathlib.Path({str(receipt)!r}).write_text(json.dumps({{'parent':parent.pid,'child':child_pid,'signal':os.WTERMSIG(status)}}))\n")
                code = runtime._owned_child([sys.executable, "-I", "-B", str(supervisor)], cwd=root,
                                            environment={}, timeout_seconds=8, stop_seconds=.2)
                self.assertEqual(code, 1 if fail_supervisor else 0)
                result = json.loads(receipt.read_text())
                self.assertEqual(result["signal"], 9)
                for pid in (result["parent"], result["child"]):
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)

    def test_parent_pid_mismatch_fails_before_child_work(self):
        with tempfile.TemporaryDirectory() as directory:
            script = self._child_script(directory,
                "import importlib.util\n"
                f"spec=importlib.util.spec_from_file_location('owned_worker_guard', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
                "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n"
                "try: worker.protect_parent_lifetime(os.getpid())\n"
                "except ValueError: sys.exit(0)\n"
                "sys.exit(1)\n")
            self.assertEqual(runtime._owned_child(script, cwd=Path(directory), environment={}, timeout_seconds=3), 0)

    def test_unsealed_shadow_removed_or_linked_member_prevents_import_and_load(self):
        for mutation in ("shadow", "removed", "symlink", "hardlink", "directory_replaced"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                assets, directories = [], []
                payload = {}
                for role in ("adapter", "base"):
                    model_root = root / role
                    model_root.mkdir(mode=0o700)
                    info = model_root.stat()
                    directories.append({"path": str(model_root), "dev": info.st_dev,
                        "inode": info.st_ino, "mode": 0o700, "uid": info.st_uid})
                    path = model_root / "sealed.safetensors"
                    path.write_bytes(role.encode())
                    path.chmod(0o600)
                    assets.append(dict(runtime._sealed_file(path, {"size_bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}), asset_id=role + "/sealed.safetensors"))
                    payload[role + "_directory"] = str(model_root)
                payload.update(assets=assets, model_directories=directories, images=[])
                base = root / "base"
                if mutation == "shadow":
                    (base / "model.safetensors").write_bytes(b"unsealed loader shadow")
                elif mutation == "removed":
                    (base / "sealed.safetensors").unlink()
                elif mutation == "symlink":
                    (base / "model.safetensors").symlink_to(base / "sealed.safetensors")
                elif mutation == "hardlink":
                    os.link(base / "sealed.safetensors", root / "outside-link")
                else:
                    base.rename(root / "old-base")
                    base.mkdir(mode=0o700)
                    (base / "sealed.safetensors").write_bytes(b"base")
                payload_path, result_path = root / "payload", root / "checked"
                runtime._write_private_protocol(payload_path, payload)
                script = self._child_script(directory,
                    "import builtins, importlib.util, types\n"
                    f"spec=importlib.util.spec_from_file_location('owned_worker_roster', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
                    "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n"
                    "imports=[]; calls=[]; original_import=builtins.__import__\n"
                    "class FakeLoader:\n"
                    " @staticmethod\n def from_pretrained(*args,**kwargs): calls.append('loaded'); raise AssertionError('loader reached')\n"
                    "fake=types.ModuleType('transformers'); fake.AutoProcessor=FakeLoader; fake.Qwen3VLForConditionalGeneration=FakeLoader\n"
                    "sys.modules['transformers']=fake\n"
                    "def guarded_import(name,*args,**kwargs):\n"
                    " if name.split('.')[0] in {'torch','PIL','transformers','peft'}: imports.append(name); raise AssertionError('model import reached')\n"
                    " return original_import(name,*args,**kwargs)\n"
                    "builtins.__import__=guarded_import\n"
                    f"payload=json.loads(pathlib.Path({str(payload_path)!r}).read_text())\n"
                    "try: worker.rewrite(payload)\n"
                    "except ValueError: pass\n"
                    "else: raise AssertionError('roster admitted')\n"
                    "assert imports==[] and calls==[]\n"
                    f"pathlib.Path({str(result_path)!r}).write_text('no import or load')\n")
                self.assertEqual(runtime._owned_child(script, cwd=root, environment={}, timeout_seconds=3), 0)
                self.assertEqual(result_path.read_text(), "no import or load")

    def test_byte_seals_reject_changed_and_linked_files_and_cancel_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            path.write_bytes(b"sealed original")
            path.chmod(0o600)
            seal = {"size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            observed = runtime._sealed_file(path, seal)
            self.assertEqual(observed["sha256"], seal["sha256"])
            path.write_bytes(b"changed content")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._sealed_file(path, seal)
            os.link(path, Path(directory) / "foreign-link")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._sealed_file(path, seal)
            (Path(directory) / "foreign-link").unlink()
            with self.assertRaises(runtime.H3PromptRewriterCancelled):
                runtime._sealed_file(path, {"size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, cancel_check=lambda: True)

    def test_result_protocol_bounds_and_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result"
            runtime._write_private_protocol(path, {"state": "completed"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime._read_private_protocol(path), {"state": "completed"})
            path.chmod(0o644)
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._read_private_protocol(path)
            path.unlink()
            path.write_bytes(b"x" * (runtime._MAX_PROTOCOL_BYTES + 1))
            path.chmod(0o600)
            with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                runtime._read_private_protocol(path)

    def _disposable_execution_fixture(self, directory, *, result_override=None, mode="t2va"):
        """Inject only the model child, not lifecycle or completed-result validation."""
        pinokio, _layout = _make_layout(directory)
        trust, adapter, base = _make_metadata_candidates(directory)
        passive = _build_admission(pinokio, trust, adapter, base, mode=mode)
        worker = Path(directory) / "fake_model_worker.py"
        worker.write_text("import json, os, pathlib, sys\n"
            "request = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
            "assert 'PRIVATE_PROMPT' not in ' '.join(sys.argv)\n"
            "assert os.environ.get('HF_HUB_OFFLINE') == '1'\n"
            "assert os.environ.get('CUDA_VISIBLE_DEVICES') == ''\n"
            "assert 'OPENAI_API_KEY' not in os.environ\n"
            "result = {'state':'completed','operation':request['operation'],'nonce':request['nonce'],"
            "'request_commitment':request['request']['commitment'],"
            "'runtime_metadata_sha256':request['runtime_metadata_sha256'],"
            "'base_candidate':'base comparison','adapted_candidate':'adapted comparison'}\n"
            + (f"result.update({result_override!r})\n" if result_override else "")
            + "fd = os.open(sys.argv[2], os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)\n"
            "with os.fdopen(fd,'w') as f: json.dump(result,f)\n")
        configuration = {"nonce": "a" * 32, "python_executable": sys.executable, "worker": str(worker),
            "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(), "runtime_metadata_sha256": "b" * 64,
            "runtime_receipt_sha256": "c" * 64, "device": "cpu", "assets": []}
        return runtime.H3PromptRewriterExecutionAdmission(runtime._EXECUTION_TOKEN, passive, configuration), passive

    def test_real_exchange_mints_completed_result_and_keeps_prompt_out_of_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, passive = self._disposable_execution_fixture(directory)
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT controversial creative subject", mode="t2va")
            result = runtime.execute_h3_prompt_rewrite(admission, request)
            self.assertIs(runtime.validate_h3_prompt_rewriter_execution_result(result, request_commitment=request["commitment"]), result)
            self.assertEqual(result.deterministic_candidate, request["original_prompt"])
            self.assertEqual(result.base_candidate, "base comparison")
            self.assertEqual(result.adapted_candidate, "adapted comparison")
            preview = rewriter.create_executed_rewrite_preview(request, result)
            self.assertEqual(preview["schema_version"], 2)
            self.assertIsNone(preview["selection"])
            self.assertEqual(preview["runtime_evidence"]["execution_receipt_sha256"], result.execution_receipt_sha256)
            for kind, expected in (("base", result.base_candidate), ("adapted", result.adapted_candidate)):
                decision = rewriter.create_apply_decision(request, preview, kind)
                self.assertEqual(rewriter.apply_preview_decision(request, preview, decision), expected)
                self.assertEqual(request["original_prompt"], result.deterministic_candidate)
            changed = rewriter.create_rewrite_request(original_prompt="changed request", mode="t2va")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                rewriter.create_executed_rewrite_preview(changed, result)
            self.assertTrue(runtime._public_value_is_path_free(result.public_receipt()))
            self.assertFalse(result.public_receipt()["gpu_accepted"])
            self.assertEqual(len(result.execution_receipt_sha256), 64)
            self.assertEqual(list(passive.private_receipt().layout.staging.iterdir()), [])
            with self.assertRaises(AttributeError):
                result.base_candidate = "forged"
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime.validate_h3_prompt_rewriter_execution_result(result, request_commitment="d" * 64)
            forged = object.__new__(runtime.H3PromptRewriterExecutionResult)
            object.__setattr__(forged, "_H3PromptRewriterExecutionResult__document", result._H3PromptRewriterExecutionResult__document)
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                runtime.validate_h3_prompt_rewriter_execution_result(forged, request_commitment=request["commitment"])

    def test_ordered_reference_bytes_bind_all_image_modes_without_source_mutation(self):
        roles_by_mode = {"i2va": ("first_frame",), "l2va": ("last_frame",), "fl2va": ("first_frame", "last_frame")}
        for mode, roles in roles_by_mode.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                admission, passive = self._disposable_execution_fixture(directory, mode=mode)
                trust = Path(directory) / "authorized-inputs"
                trust.mkdir(mode=0o700)
                bindings, image_roles = [], []
                for index, role in enumerate(roles):
                    path = trust / f"input-{index}"
                    path.write_bytes(f"reference-{index}".encode())
                    path.chmod(0o600)
                    input_id = f"input-{index}"
                    seal = {"size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    bindings.append({"input_id": input_id, "path": str(path), "trust_root": str(trust), "seal": seal})
                    image_roles.append({"role": role, "input_id": input_id})
                request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode=mode, image_roles=image_roles)
                result = runtime.execute_h3_prompt_rewrite(admission, request, image_bindings=bindings)
                self.assertEqual(len(result.public_receipt()["reference_manifest_sha256"]), 64)
                for binding in bindings:
                    self.assertEqual(hashlib.sha256(Path(binding["path"]).read_bytes()).hexdigest(), binding["seal"]["sha256"])
                Path(bindings[0]["path"]).unlink()
                with self.assertRaises(runtime.H3PromptRewriterRuntimeSecurityError):
                    runtime.execute_h3_prompt_rewrite(admission, request, image_bindings=bindings)
                self.assertEqual(list(passive.private_receipt().layout.staging.iterdir()), [])

    def test_actual_template_import_keeps_sealed_roster_unchanged_in_isolated_child(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, _passive = self._disposable_execution_fixture(directory)
            configuration = json.loads(admission._H3PromptRewriterExecutionAdmission__configuration)
            root = Path(directory)
            assets, directories = [], []
            for role in ("adapter", "base"):
                model_root = root / ("template-test-" + role)
                model_root.mkdir(mode=0o700)
                info = model_root.stat()
                directories.append({"path": str(model_root), "dev": info.st_dev, "inode": info.st_ino,
                                    "mode": 0o700, "uid": info.st_uid})
                configuration[role + "_directory"] = str(model_root)
            template = Path(configuration["adapter_directory"]) / "prompt_template.py"
            template.write_text("TEMPLATE_WAS_IMPORTED = True\ndef build_messages(*args, **kwargs): return []\n")
            template.chmod(0o600)
            assets.append(dict(runtime._sealed_file(template, {"size_bytes": template.stat().st_size,
                "sha256": hashlib.sha256(template.read_bytes()).hexdigest()}), asset_id="adapter/prompt_template.py"))
            configuration.update(assets=assets, model_directories=directories, template_path=str(template))
            worker_path = Path(configuration["worker"])
            fake_protocol = worker_path.read_text()
            worker_path.write_text(
                "import builtins, importlib.util, json, pathlib, sys\n"
                f"spec=importlib.util.spec_from_file_location('production_worker_template_test', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
                "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n"
                "payload=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "modules=[]; original_factory=worker.importlib.util.module_from_spec\n"
                "def record_module(spec):\n module=original_factory(spec); modules.append(module); return module\n"
                "worker.importlib.util.module_from_spec=record_module\n"
                "class ModelBoundary(Exception): pass\n"
                "original_import=builtins.__import__\n"
                "def prevent_model_import(name,*args,**kwargs):\n"
                " if name.split('.')[0] in {'torch','transformers','peft','PIL'}: raise ModelBoundary()\n"
                " return original_import(name,*args,**kwargs)\n"
                "builtins.__import__=prevent_model_import\n"
                "try:\n"
                " try: worker.rewrite(payload)\n"
                " except ModelBoundary: pass\n"
                " else: raise AssertionError('model boundary not reached')\n"
                "finally: builtins.__import__=original_import\n"
                "assert any(getattr(module,'TEMPLATE_WAS_IMPORTED',False) for module in modules)\n"
                "worker.verify_asset_roster(payload)\n"
                "assert sys.dont_write_bytecode\n"
                "assert not pathlib.Path(payload['adapter_directory'],'__pycache__').exists()\n"
                + fake_protocol)
            configuration["worker_sha256"] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
            qualified_cpu_fixture = runtime.H3PromptRewriterExecutionAdmission(
                runtime._EXECUTION_TOKEN, admission._H3PromptRewriterExecutionAdmission__passive, configuration)
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
            result = runtime.execute_h3_prompt_rewrite(qualified_cpu_fixture, request)
            self.assertEqual(result.base_candidate, "base comparison")
            self.assertEqual(list(template.parent.iterdir()), [template])

    def test_late_cancellation_rejects_child_output_and_retains_private_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, passive = self._disposable_execution_fixture(directory)
            configuration = json.loads(admission._H3PromptRewriterExecutionAdmission__configuration)
            worker = Path(configuration["worker"])
            marker = Path(directory) / "child-completed"
            with worker.open("a") as stream:
                stream.write(f"pathlib.Path({str(marker)!r}).touch()\nimport time; time.sleep(.2)\n")
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
            with self.assertRaises(runtime.H3PromptRewriterCancelled):
                runtime.execute_h3_prompt_rewrite(admission, request, cancel_check=marker.exists)
            stages = list(passive.private_receipt().layout.staging.iterdir())
            self.assertEqual(len(stages), 1)
            self.assertEqual(runtime._read_private_protocol(stages[0] / "parent-outcome.json")["state"], "cancelled")
            self.assertEqual(runtime._read_private_protocol(stages[0] / "request.json")["request"], request)

    def test_missing_or_mismatched_completed_child_has_no_candidate_or_retry(self):
        for override in ({"nonce": "wrong"}, {"request_commitment": "d" * 64}, {"adapted_candidate": ""}, {"state": "failed"}):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                admission, passive = self._disposable_execution_fixture(directory, result_override=override)
                request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
                with self.assertRaises(runtime.H3PromptRewriterRuntimeError):
                    runtime.execute_h3_prompt_rewrite(admission, request)
                stages = list(passive.private_receipt().layout.staging.iterdir())
                self.assertEqual(len(stages), 1)
                self.assertEqual(runtime._read_private_protocol(stages[0] / "parent-outcome.json")["state"], "failed")
                self.assertEqual(runtime._read_private_protocol(stages[0] / "request.json")["request"], request)

    def _private_failure_child(self, directory, *, sleep_after_checkpoint=False):
        admission, passive = self._disposable_execution_fixture(directory)
        configuration = json.loads(admission._H3PromptRewriterExecutionAdmission__configuration)
        worker_path = Path(configuration["worker"])
        worker_path.write_text(
            "import importlib.util, json, pathlib, sys, time\n"
            f"spec=importlib.util.spec_from_file_location('production_private_evidence', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
            "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n"
            "payload=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
            "result={'state':'running','operation':payload['operation'],'nonce':payload['nonce'],"
            "'request_commitment':payload['request']['commitment'],"
            "'base_candidate':'already-produced base comparison',"
            "'private_diagnostic':{'phase':'adapterload'}}\n"
            "worker.write_private_result(pathlib.Path(sys.argv[2]),result)\n"
            + ("time.sleep(60)\n" if sleep_after_checkpoint else
               "try: raise RuntimeError('PRIVATE_DIAGNOSTIC /private/path '+chr(937)*10000)\n"
               "except Exception as error: worker.record_private_failure(result,error)\n"
               "worker.write_private_result(pathlib.Path(sys.argv[2]),result)\nsys.exit(1)\n"))
        configuration["worker_sha256"] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
        return runtime.H3PromptRewriterExecutionAdmission(runtime._EXECUTION_TOKEN, passive, configuration), passive

    def test_actual_child_failure_keeps_bounded_private_diagnostic_and_partial_base(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, passive = self._private_failure_child(directory)
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError) as raised:
                runtime.execute_h3_prompt_rewrite(admission, request)
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertNotIn(directory, str(raised.exception))
            stages = list(passive.private_receipt().layout.staging.iterdir())
            self.assertEqual(len(stages), 1)
            stage = stages[0]
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            self.assertEqual(runtime._read_private_protocol(stage / "request.json")["request"], request)
            result = runtime._read_private_protocol(stage / "result.json")
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["base_candidate"], "already-produced base comparison")
            diagnostic = result["private_diagnostic"]
            self.assertEqual(diagnostic["phase"], "adapterload")
            self.assertEqual(diagnostic["exception_type"], "builtins.RuntimeError")
            self.assertTrue(diagnostic["exception_message"].startswith("PRIVATE_DIAGNOSTIC"))
            self.assertLessEqual(len(diagnostic["exception_message"].encode()), 8192)
            self.assertLessEqual((stage / "result.json").stat().st_size, runtime._MAX_PROTOCOL_BYTES)
            self.assertEqual(runtime._read_private_protocol(stage / "parent-outcome.json")["state"], "failed")
            for member in stage.iterdir():
                self.assertEqual(member.stat().st_mode & 0o777, 0o600)

    def test_timeout_keeps_last_private_phase_and_partial_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, passive = self._private_failure_child(directory, sleep_after_checkpoint=True)
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
            with self.assertRaisesRegex(runtime.H3PromptRewriterRuntimeError, "timed out"):
                runtime.execute_h3_prompt_rewrite(admission, request, timeout_seconds=.25)
            stage, = passive.private_receipt().layout.staging.iterdir()
            result = runtime._read_private_protocol(stage / "result.json")
            self.assertEqual(result["state"], "running")
            self.assertEqual(result["private_diagnostic"]["phase"], "adapterload")
            self.assertEqual(result["base_candidate"], "already-produced base comparison")
            self.assertEqual(runtime._read_private_protocol(stage / "parent-outcome.json")["state"], "failed")

    def test_lost_result_public_error_is_generic_and_private_request_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            admission, passive = self._disposable_execution_fixture(directory)
            configuration = json.loads(admission._H3PromptRewriterExecutionAdmission__configuration)
            Path(configuration["worker"]).write_text("# Exit without a result; never retry.\n")
            request = rewriter.create_rewrite_request(original_prompt="PRIVATE_PROMPT", mode="t2va")
            with self.assertRaises(runtime.H3PromptRewriterRuntimeError) as raised:
                runtime.execute_h3_prompt_rewrite(admission, request)
            self.assertEqual(str(raised.exception), "H3 prompt-rewriter private protocol failed")
            self.assertNotIn(directory, str(raised.exception))
            stage, = passive.private_receipt().layout.staging.iterdir()
            self.assertEqual(runtime._read_private_protocol(stage / "request.json")["request"], request)
            self.assertFalse((stage / "result.json").exists())
            self.assertEqual(runtime._read_private_protocol(stage / "parent-outcome.json")["state"], "failed")

    def test_private_result_full_protocol_bound_cannot_report_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result"
            command = self._child_script(directory,
                "import importlib.util\n"
                f"spec=importlib.util.spec_from_file_location('bounded_private_result', {str(SOURCE.with_name('h3_prompt_rewriter_worker.py'))!r})\n"
                "worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)\n"
                "result={'state':'completed','base_candidate':chr(0)*65536,'adapted_candidate':chr(0)*65536,"
                "'private_diagnostic':{'phase':'finalize'}}\n"
                f"assert worker.write_private_result(pathlib.Path({str(path)!r}),result)=='failed'\n")
            self.assertEqual(runtime._owned_child(command, cwd=Path(directory), environment={}, timeout_seconds=3), 0)
            result = runtime._read_private_protocol(path)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["error_code"], "result_bound_exceeded")
            self.assertTrue(result["private_diagnostic"]["protocol_truncated"])
            self.assertTrue(result["private_diagnostic"]["base_candidate_truncated"])
            self.assertLessEqual(path.stat().st_size, runtime._MAX_PROTOCOL_BYTES)

    def test_pinned_qwen_generation_accepts_processor_inputs_and_keeps_ordered_images(self):
        """Model boundary rejects the native02 unsupported kwarg, without model imports."""
        import importlib.util
        from contextlib import nullcontext
        from types import SimpleNamespace

        spec = importlib.util.spec_from_file_location("h3_worker_qwen_inputs", SOURCE.with_name("h3_prompt_rewriter_worker.py"))
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        for image_count in (0, 2):
            with self.subTest(image_count=image_count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                template = root / "prompt_template.py"
                template.write_text("def build_messages(prompt, **kwargs): return [{'text': prompt}]\n")
                processed_images, calls = [], []

                class Tensor:
                    shape = (1, 3)
                    def to(self, device):
                        self.device = device
                        return self
                    def __getitem__(self, key):
                        return [101]

                input_ids, attention_mask, pixels, grid = (Tensor() for _ in range(4))

                class Processor:
                    def apply_chat_template(self, *args, **kwargs):
                        return "synthetic rendered request"
                    def __call__(self, *, text, return_tensors, padding, images=None, return_mm_token_type_ids=False):
                        self_outer.assertEqual(text, ["synthetic rendered request"])
                        self_outer.assertEqual(return_tensors, "pt")
                        self_outer.assertIs(padding, False)
                        data = {"input_ids": input_ids, "attention_mask": attention_mask}
                        if images is not None:
                            processed_images.extend(images)
                            data.update(pixel_values=pixels, image_grid_thw=grid)
                        if return_mm_token_type_ids:
                            data["mm_token_type_ids"] = Tensor()
                        return data
                    def decode(self, *args, **kwargs):
                        return "base comparison" if len(calls) == 1 else "adapted comparison"

                class Model:
                    def eval(self):
                        return self
                    # Pinned Qwen uses image placeholders/grid, not mm_token_type_ids.
                    def generate(self, *, input_ids, attention_mask, max_new_tokens, do_sample, pixel_values=None, image_grid_thw=None):
                        calls.append((input_ids, attention_mask, pixel_values, image_grid_thw))
                        self_outer.assertEqual(max_new_tokens, 4096)
                        self_outer.assertIs(do_sample, False)
                        return Tensor()

                class ImageFixture:
                    def __init__(self, path):
                        self.path = path
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        return None
                    def convert(self, mode):
                        self_outer.assertEqual(mode, "RGB")
                        return self
                    def copy(self):
                        return Path(self.path).name

                self_outer = self
                bindings = []
                for name in ("first.png", "last.png")[:image_count]:
                    path = root / name
                    path.write_bytes(b"synthetic image boundary")
                    path.chmod(0o600)
                    bindings.append(runtime._sealed_file(path, {"size_bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
                payload = {"assets": [], "images": bindings, "template_path": str(template),
                    "base_directory": str(root / "base"), "adapter_directory": str(root / "adapter"), "device": "cpu",
                    "request": {"original_prompt": "PRIVATE_SYNTHETIC", "mode": "fl2va" if image_count else "t2va", "commitment": "a" * 64},
                    "controls": {"min_pixels": 65536, "max_pixels": 1048576, "duration": 10,
                        "resolution": "adaptive" if image_count else "16:9", "greedy": True, "seed": 42, "max_new_tokens": 4096}}
                modules = {
                    "torch": SimpleNamespace(Tensor=Tensor, bfloat16="bfloat16", manual_seed=lambda seed: None, inference_mode=nullcontext),
                    "transformers": SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=lambda *args, **kwargs: Processor()),
                        Qwen3VLForConditionalGeneration=SimpleNamespace(from_pretrained=lambda *args, **kwargs: Model())),
                    "peft": SimpleNamespace(PeftModel=SimpleNamespace(from_pretrained=lambda *args, **kwargs: Model())),
                    "PIL": SimpleNamespace(Image=SimpleNamespace(open=ImageFixture), ImageOps=SimpleNamespace(exif_transpose=lambda image: image)),
                }
                with mock.patch.dict(sys.modules, modules), mock.patch.object(worker, "verify_asset_roster"), mock.patch.object(worker, "verify_file"):
                    result = worker.rewrite(payload)
                self.assertEqual(result["base_candidate"], "base comparison")
                self.assertEqual(result["adapted_candidate"], "adapted comparison")
                self.assertEqual(processed_images, ["first.png", "last.png"] if image_count else [])
                self.assertEqual(len(calls), 2)
                for call in calls:
                    self.assertEqual(call, (input_ids, attention_mask, pixels if image_count else None, grid if image_count else None))

    def test_real_worker_rejects_unqualified_interpreter_without_model_stack(self):
        # The currently managed interpreter is deliberately NOT the isolated pin.
        modules_before = set(sys.modules)
        with tempfile.TemporaryDirectory() as directory:
            worker = SOURCE.with_name("h3_prompt_rewriter_worker.py")
            exe = Path(sys.executable).resolve()
            info = exe.stat()
            seal = {"path": str(exe), "dev": info.st_dev, "inode": info.st_ino,
                "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                "sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
            request_path, result_path = Path(directory) / "request", Path(directory) / "result"
            runtime._write_private_protocol(request_path, {"operation": "probe", "nonce": "a" * 32,
                "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(), "executable": seal,
                "package_pins": dict(closure.ROOT_PACKAGE_PINS), "parent_pid": os.getpid()})
            code = runtime._owned_child([sys.executable, "-I", "-B", str(worker), str(request_path), str(result_path)],
                cwd=Path(directory), environment={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": ""}, timeout_seconds=5)
            self.assertEqual(code, 1)
            result = runtime._read_private_protocol(result_path)
            self.assertEqual(result["state"], "failed")
            self.assertNotIn("runtime", result)
            self.assertNotIn(directory, json.dumps(result))
            self.assertNotIn("torch", set(sys.modules) - modules_before)


if __name__ == "__main__":
    unittest.main()
