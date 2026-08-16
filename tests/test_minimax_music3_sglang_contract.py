"""Focused tests for the non-executable MiniMax Music 3 SGLang contract."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import struct
import sys
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services.minimax_music3_sglang_contract import (  # noqa: E402
    MAX_INSTRUCTIONS_BYTES,
    MAX_NEW_TOKENS,
    MAX_SEED,
    HOSTED_SERVICE_AUTHORIZATION_SCOPE,
    LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
    LOCAL_EXPERIMENT_REQUIRED_GATES,
    MUSIC3_HF_EXACT_REVISION,
    MUSIC3_HF_REVISION,
    MUSIC3_MODEL_ID,
    SUPPORTED_AUTHORIZATION_SCOPES,
    UNAPPROVED_AUTHORIZATION_SCOPES,
    UNSUPPORTED_REQUEST_FIELDS,
    Music3HealthEvidence,
    Music3ModelEvidence,
    Music3SglangSourceBinding,
    Music3WavEvidence,
    MusicModelContractError,
    UnsupportedMusicRequest,
    ValidatedMusic3SglangRequest,
    bind_music3_sglang_source,
    parse_music3_wav_bytes,
    validate_music3_sglang_request,
    validate_sglang_health_response,
    validate_sglang_models_response,
)
from services.music_model_contract import MAX_LYRICS_BYTES  # noqa: E402


MODULE_PATH = APP_DIR / "services" / "minimax_music3_sglang_contract.py"


class _ExecutableMapping(dict):
    calls = 0

    def __iter__(self):
        type(self).calls += 1
        return super().__iter__()


class _ExecutableBytes(bytes):
    calls = 0

    def __len__(self):
        type(self).calls += 1
        return super().__len__()


def _request(**updates):
    value = {
        "model": MUSIC3_MODEL_ID,
        "input": "Locally authored lyrics",
        "instructions": "An energetic electronic arrangement",
        "response_format": "wav",
        "seed": 42,
        "max_new_tokens": 9_000,
        "stream": False,
    }
    value.update(updates)
    return value


def _models_response(**entry_updates):
    entry = {
        "id": MUSIC3_MODEL_ID,
        "object": "model",
        "created": 0,
        "owned_by": "sglang-omni",
        "permission": [{
            "id": "modelperm-default",
            "object": "model_permission",
            "allow_create_engine": False,
            "allow_sampling": True,
            "allow_logprobs": True,
        }],
        "root": MUSIC3_MODEL_ID,
    }
    entry.update(entry_updates)
    return {"object": "list", "data": [entry]}


def _health_response(**updates):
    value = {
        "status": "healthy",
        "running": True,
        "stages": ["music-ar", "music-acoustic"],
        "entry_stage": "music-ar",
        "total_requests": 3,
        "pending_completions": 1,
        "request_states": {"completed": 2, "running": 1},
    }
    value.update(updates)
    return value


def _wav_bytes(
    *,
    sample_rate=32_000,
    sample_width=2,
    channels=2,
    frames=8,
    compression=None,
):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        if compression is not None:
            writer.setcomptype(*compression)
        writer.writeframes(b"\x00" * (frames * channels * sample_width))
    return output.getvalue()


class SourceBindingTests(unittest.TestCase):
    def test_source_binding_pins_model_and_caller_runtime_revision(self):
        revision = "sha256:" + ("a" * 64)
        binding = bind_music3_sglang_source(revision)
        rendered = binding.to_mapping()
        self.assertEqual(rendered["model_identity"], {
            "purpose": "music.generate",
            "provider": "local",
            "engine": "sglang-omni",
            "model": MUSIC3_MODEL_ID,
            "exact_revision": MUSIC3_HF_EXACT_REVISION,
        })
        self.assertEqual(MUSIC3_HF_REVISION, "fbdf52fbaaca799592917417eb05f1899f1255ec")
        self.assertEqual(rendered["runtime_source_revision"], revision)
        self.assertEqual(
            rendered["authorization_scope"],
            LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
        )
        self.assertEqual(
            rendered["required_local_experiment_gates"],
            list(LOCAL_EXPERIMENT_REQUIRED_GATES),
        )
        self.assertEqual(
            LOCAL_EXPERIMENT_REQUIRED_GATES,
            (
                "acceptable_use_approval",
                "attribution_approval",
                "license_approval",
                "locality_approval",
                "united_states_approval",
                "local_experiment_approval",
            ),
        )
        self.assertNotIn(
            "hosted_service_approval",
            LOCAL_EXPERIMENT_REQUIRED_GATES,
        )
        self.assertEqual(
            rendered["unapproved_authorization_scopes"],
            [HOSTED_SERVICE_AUTHORIZATION_SCOPE],
        )
        self.assertEqual(
            SUPPORTED_AUTHORIZATION_SCOPES,
            (LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,),
        )
        self.assertEqual(
            UNAPPROVED_AUTHORIZATION_SCOPES,
            (HOSTED_SERVICE_AUTHORIZATION_SCOPE,),
        )
        self.assertIs(rendered["execution_authorized"], False)

    def test_runtime_revision_must_be_exact_content_address(self):
        for revision in (
            None,
            True,
            "",
            "latest",
            "main",
            "git:abc",
            "git:" + ("A" * 40),
            "sha256:" + ("0" * 63),
        ):
            with self.subTest(revision=revision):
                with self.assertRaises(MusicModelContractError):
                    bind_music3_sglang_source(revision)
        self.assertEqual(
            bind_music3_sglang_source("git:" + ("b" * 40)).runtime_source_revision,
            "git:" + ("b" * 40),
        )

    def test_direct_binding_cannot_replace_model_or_external_gates(self):
        good = bind_music3_sglang_source("sha256:" + ("0" * 64))
        wrong = type(good.model_identity)(
            purpose="music.generate",
            provider="local",
            engine="sglang-omni",
            model="replacement",
            exact_revision=MUSIC3_HF_EXACT_REVISION,
        )
        with self.assertRaises(MusicModelContractError):
            Music3SglangSourceBinding(wrong, "sha256:" + ("0" * 64))
        with self.assertRaises(MusicModelContractError):
            Music3SglangSourceBinding(
                good.model_identity,
                good.runtime_source_revision,
                required_local_experiment_gates=(),
            )
        with self.assertRaises(FrozenInstanceError):
            good.runtime_source_revision = "sha256:" + ("1" * 64)


class RequestTests(unittest.TestCase):
    def test_request_maps_lyrics_and_caption_to_exact_wire_fields(self):
        validated = validate_music3_sglang_request(_request())
        self.assertEqual(validated.to_mapping(), _request())

    def test_request_requires_exact_model_identity(self):
        for model in ("replacement", "", 1, True):
            with self.subTest(model=model):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_sglang_request(_request(model=model))

    def test_seed_accepts_full_nonnegative_uint64_only(self):
        for seed in (0, 1, 2**63, MAX_SEED):
            with self.subTest(seed=seed):
                self.assertEqual(
                    validate_music3_sglang_request(_request(seed=seed)).seed,
                    seed,
                )
        for seed in (-1, MAX_SEED + 1, True, 1.0, "1"):
            with self.subTest(seed=seed):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_sglang_request(_request(seed=seed))

    def test_new_token_bounds_are_exact(self):
        for count in (1, MAX_NEW_TOKENS):
            self.assertEqual(
                validate_music3_sglang_request(
                    _request(max_new_tokens=count)
                ).max_new_tokens,
                count,
            )
        for count in (0, MAX_NEW_TOKENS + 1, True, 1.0, "1"):
            with self.subTest(count=count):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_sglang_request(_request(max_new_tokens=count))

    def test_only_wav_and_nonstreaming_are_supported(self):
        for field, value in (
            ("response_format", "mp3"),
            ("response_format", 1),
            ("stream", True),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(UnsupportedMusicRequest):
                    validate_music3_sglang_request(_request(**{field: value}))
        with self.assertRaises(MusicModelContractError):
            validate_music3_sglang_request(_request(stream=0))

    def test_speed_one_is_canonical_and_every_other_value_is_rejected(self):
        for speed in (1, 1.0):
            self.assertEqual(
                validate_music3_sglang_request(_request(speed=speed)).to_mapping()["speed"],
                1.0,
            )
        for speed in (None, 0, 0.999, 2, True, float("nan"), "1"):
            with self.subTest(speed=speed):
                with self.assertRaises((MusicModelContractError, UnsupportedMusicRequest)):
                    validate_music3_sglang_request(_request(speed=speed))

    def test_every_named_unsupported_field_fails_explicitly(self):
        expected = {
            "voice",
            "ref_audio",
            "ref_text",
            "language",
            "task_type",
            "temperature",
            "top_p",
            "top_k",
            "repetition_penalty",
        }
        self.assertEqual(UNSUPPORTED_REQUEST_FIELDS, expected)
        for field in sorted(expected):
            with self.subTest(field=field):
                with self.assertRaises(UnsupportedMusicRequest):
                    validate_music3_sglang_request(_request(**{field: "ignored"}))

    def test_unknown_missing_and_nonplain_mappings_fail_closed(self):
        with self.assertRaises(MusicModelContractError):
            validate_music3_sglang_request({**_request(), "callback": object()})
        missing = _request()
        del missing["instructions"]
        with self.assertRaises(MusicModelContractError):
            validate_music3_sglang_request(missing)
        _ExecutableMapping.calls = 0
        with self.assertRaises(MusicModelContractError):
            validate_music3_sglang_request(_ExecutableMapping(_request()))
        self.assertEqual(_ExecutableMapping.calls, 0)

    def test_text_is_utf8_bounded_nonempty_and_trimmed(self):
        invalid = (
            _request(input=""),
            _request(input=" padded "),
            _request(input="nul\x00byte"),
            _request(input="x" * (MAX_LYRICS_BYTES + 1)),
            _request(instructions=""),
            _request(instructions="x" * (MAX_INSTRUCTIONS_BYTES + 1)),
            _request(instructions=7),
        )
        for request in invalid:
            with self.subTest(field_values=list(request.values())[-2:]):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_sglang_request(request)

    def test_direct_request_construction_cannot_bypass_validation(self):
        with self.assertRaises(MusicModelContractError):
            ValidatedMusic3SglangRequest("lyrics", "caption", -1, 1)
        with self.assertRaises(MusicModelContractError):
            ValidatedMusic3SglangRequest("lyrics", "caption", 1, 9_001)


class ResponseValidationTests(unittest.TestCase):
    def test_health_requires_exact_200_and_healthy_coordinator_shape(self):
        evidence = validate_sglang_health_response(200, _health_response())
        self.assertEqual(evidence.stages, ("music-ar", "music-acoustic"))
        self.assertEqual(evidence.entry_stage, "music-ar")
        self.assertEqual(evidence.total_requests, 3)
        for status, body in (
            (201, _health_response()),
            (True, _health_response()),
            (200, "healthy"),
            (200, _health_response(status="unhealthy")),
            (200, _health_response(running=False)),
            (200, _health_response(stages=[])),
            (200, _health_response(entry_stage="missing")),
            (200, _health_response(total_requests=True)),
            (200, _health_response(request_states={"completed": 2})),
            (200, {**_health_response(), "debug": "extra"}),
        ):
            with self.subTest(status=status, body_type=type(body).__name__):
                with self.assertRaises(MusicModelContractError):
                    validate_sglang_health_response(status, body)

    def test_health_rejects_mapping_subclass_without_callbacks(self):
        _ExecutableMapping.calls = 0
        with self.assertRaises(MusicModelContractError):
            validate_sglang_health_response(
                200,
                _ExecutableMapping(_health_response()),
            )
        self.assertEqual(_ExecutableMapping.calls, 0)

    def test_health_evidence_direct_construction_is_validated(self):
        with self.assertRaises(MusicModelContractError):
            Music3HealthEvidence(("music-ar",), "music-ar", 0, 0)
        with self.assertRaises(MusicModelContractError):
            Music3HealthEvidence((), "missing", 0, 0)
        with self.assertRaises(MusicModelContractError):
            Music3HealthEvidence(("stage",), "stage", True, 0)

    def test_models_response_requires_exact_music3_identity(self):
        evidence = validate_sglang_models_response(_models_response())
        self.assertEqual(evidence.model_id, MUSIC3_MODEL_ID)
        self.assertEqual(evidence.owned_by, "sglang-omni")
        self.assertEqual(evidence.root, MUSIC3_MODEL_ID)
        invalid = (
            _models_response(id="replacement"),
            _models_response(object="unknown"),
            _models_response(created=True),
            _models_response(created=-1),
            _models_response(owned_by=""),
            _models_response(root="replacement"),
            _models_response(permission=[]),
            _models_response(permission=[{
                "id": "modelperm-default",
                "object": "model_permission",
                "allow_create_engine": True,
                "allow_sampling": True,
                "allow_logprobs": True,
            }]),
            {"object": "list", "data": []},
            {"object": "list", "data": [_models_response()["data"][0]] * 2},
            {"object": "list", "data": [_models_response()["data"][0]], "next": None},
        )
        for response in invalid:
            with self.subTest(response=response):
                with self.assertRaises(MusicModelContractError):
                    validate_sglang_models_response(response)

    def test_models_response_rejects_mapping_subclasses_without_callbacks(self):
        _ExecutableMapping.calls = 0
        with self.assertRaises(MusicModelContractError):
            validate_sglang_models_response(_ExecutableMapping(_models_response()))
        self.assertEqual(_ExecutableMapping.calls, 0)

    def test_model_evidence_direct_construction_is_validated(self):
        with self.assertRaises(MusicModelContractError):
            Music3ModelEvidence(
                MUSIC3_MODEL_ID,
                0,
                "sglang-omni",
                MUSIC3_MODEL_ID,
            )
        with self.assertRaises(MusicModelContractError):
            Music3ModelEvidence("replacement", 0, "sglang-omni", "replacement")
        with self.assertRaises(MusicModelContractError):
            Music3ModelEvidence(MUSIC3_MODEL_ID, True, "sglang-omni", MUSIC3_MODEL_ID)


class WavValidationTests(unittest.TestCase):
    def test_valid_pcm16_32khz_stereo_is_parsed_in_memory(self):
        payload = _wav_bytes(frames=32)
        evidence = parse_music3_wav_bytes(payload)
        self.assertEqual(evidence.byte_count, len(payload))
        self.assertEqual(evidence.frame_count, 32)
        self.assertEqual(evidence.duration_seconds, 32 / 32_000)
        self.assertEqual(len(evidence.sha256), 64)

    def test_wrong_audio_shape_and_empty_frames_fail_closed(self):
        payloads = (
            _wav_bytes(sample_rate=44_100),
            _wav_bytes(sample_width=1),
            _wav_bytes(channels=1),
            _wav_bytes(frames=0),
        )
        for payload in payloads:
            with self.subTest(size=len(payload)):
                with self.assertRaises(MusicModelContractError):
                    parse_music3_wav_bytes(payload)

    def test_malformed_truncated_and_nonbytes_fail_closed(self):
        valid = _wav_bytes(frames=8)
        declared_too_large = bytearray(valid)
        declared_too_large[40:44] = struct.pack("<I", 10_000)
        misaligned = bytearray(valid)
        misaligned[40:44] = struct.pack("<I", 33)
        misaligned.extend(b"\x01\x00")
        misaligned[4:8] = struct.pack("<I", len(misaligned) - 8)
        for payload in (
            b"",
            b"not a wave",
            valid[:-1],
            bytes(declared_too_large),
            bytes(misaligned),
            bytearray(valid),
            memoryview(valid),
        ):
            with self.subTest(payload_type=type(payload).__name__, size=len(payload)):
                with self.assertRaises(MusicModelContractError):
                    parse_music3_wav_bytes(payload)
        _ExecutableBytes.calls = 0
        with self.assertRaises(MusicModelContractError):
            parse_music3_wav_bytes(_ExecutableBytes(valid))
        self.assertEqual(_ExecutableBytes.calls, 0)

    def test_wav_evidence_direct_construction_is_validated(self):
        with self.assertRaises(MusicModelContractError):
            Music3WavEvidence(44, 1, 1 / 32_000, "0" * 64)
        with self.assertRaises(MusicModelContractError):
            Music3WavEvidence(1, 1, 1.0, "0" * 64)
        with self.assertRaises(MusicModelContractError):
            Music3WavEvidence(1, 1, 1 / 32_000, "not-a-digest")


class NonExecutableBoundaryTests(unittest.TestCase):
    def test_module_has_no_runtime_network_or_filesystem_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(
            imports.isdisjoint({
                "asyncio",
                "httpx",
                "os",
                "pathlib",
                "requests",
                "shutil",
                "socket",
                "subprocess",
                "urllib",
            })
        )

    def test_source_contract_exposes_no_authorize_or_execute_callable(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        function_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            function_names.isdisjoint({
                "authorize",
                "download",
                "execute",
                "install",
                "launch",
                "load",
                "register",
                "run",
            })
        )


if __name__ == "__main__":
    unittest.main()
