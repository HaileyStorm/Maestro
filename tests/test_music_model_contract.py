"""Focused tests for the dependency-light music model product contract."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services.music_model_contract import (  # noqa: E402
    CAPABILITY_KEYS,
    DURATION_KEYS,
    IDENTITY_KEYS,
    LIFECYCLE_TRANSITIONS,
    MAX_DURATION_SECONDS,
    MAX_FRAME_COUNT,
    MAX_SEED,
    MIN_SEED,
    MusicModelContract,
    MusicModelContractError,
    UnsupportedMusicRequest,
    canonical_music_provenance,
    parse_music_capability_probe,
    parse_music_model_identity,
    validate_music_lifecycle_state,
    validate_music_lifecycle_transition,
    validate_music_request,
)


MODULE_PATH = APP_DIR / "services" / "music_model_contract.py"


class _ExecutableString(str):
    calls = 0

    def partition(self, separator):
        type(self).calls += 1
        return "sha256", ":", "0" * 64

    def __eq__(self, other):
        type(self).calls += 1
        return True

    __hash__ = str.__hash__


class _ExecutableInteger(int):
    calls = 0

    def __float__(self):
        type(self).calls += 1
        return 1.0


class _ExecutableMapping(dict):
    calls = 0

    def __iter__(self):
        type(self).calls += 1
        return super().__iter__()


def _identity(**updates):
    value = {
        "purpose": "music.generate",
        "provider": "local",
        "engine": "adapter-under-test",
        "model": "model-under-test",
        "exact_revision": "sha256:" + ("0" * 64),
    }
    value.update(updates)
    return value


def _probe(**updates):
    value = {
        "schema_version": 1,
        "lyrics": True,
        "instrumental": True,
        "duration_seconds": {"minimum": 1, "maximum": 600},
        "seed": True,
        "max_frames": 576_000,
        "output_formats": ["flac", "wav"],
        "cancel": True,
        "health": True,
        "unload": True,
    }
    value.update(updates)
    return value


class MusicIdentityTests(unittest.TestCase):
    def test_identity_requires_the_exact_closed_key_set(self):
        identity = parse_music_model_identity(_identity())
        self.assertEqual(set(identity.to_mapping()), IDENTITY_KEYS)
        with self.assertRaises(MusicModelContractError):
            parse_music_model_identity({**_identity(), "artifact": "guess.bin"})
        incomplete = _identity()
        del incomplete["provider"]
        with self.assertRaises(MusicModelContractError):
            parse_music_model_identity(incomplete)

    def test_identity_separates_every_target_dimension(self):
        identity = parse_music_model_identity(_identity())
        self.assertEqual(identity.purpose, "music.generate")
        self.assertEqual(identity.provider, "local")
        self.assertEqual(identity.engine, "adapter-under-test")
        self.assertEqual(identity.model, "model-under-test")
        self.assertEqual(identity.exact_revision, "sha256:" + ("0" * 64))

    def test_identity_rejects_malformed_and_floating_values(self):
        for field in IDENTITY_KEYS:
            for invalid in (None, True, "", " padded ", "line\nbreak"):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(MusicModelContractError):
                        parse_music_model_identity(_identity(**{field: invalid}))
        for revision in (
            "latest",
            "refs/heads/main",
            "origin/main",
            "trunk",
            "current",
            "tip",
            "git:abc",
            "sha256:" + ("A" * 64),
        ):
            with self.subTest(revision=revision):
                with self.assertRaises(MusicModelContractError):
                    parse_music_model_identity(_identity(exact_revision=revision))

    def test_identity_is_frozen(self):
        identity = parse_music_model_identity(_identity())
        with self.assertRaises(FrozenInstanceError):
            identity.model = "replacement"

    def test_direct_construction_cannot_bypass_identity_validation(self):
        with self.assertRaises(MusicModelContractError):
            type(parse_music_model_identity(_identity()))(
                purpose="music.generate",
                provider="local",
                engine="adapter-under-test",
                model="model-under-test",
                exact_revision="origin/main",
            )

    def test_executable_string_subclass_is_rejected_without_callbacks(self):
        _ExecutableString.calls = 0
        with self.assertRaises(MusicModelContractError):
            parse_music_model_identity(_identity(
                exact_revision=_ExecutableString("origin/main"),
            ))
        self.assertEqual(_ExecutableString.calls, 0)

    def test_mapping_subclass_is_rejected_without_iteration(self):
        _ExecutableMapping.calls = 0
        with self.assertRaises(MusicModelContractError):
            parse_music_model_identity(_ExecutableMapping(_identity()))
        self.assertEqual(_ExecutableMapping.calls, 0)


class MusicCapabilityProbeTests(unittest.TestCase):
    def test_probe_requires_exact_top_level_and_duration_keys(self):
        capabilities = parse_music_capability_probe(_probe())
        rendered = capabilities.to_mapping()
        self.assertEqual(set(rendered), CAPABILITY_KEYS)
        self.assertEqual(set(rendered["duration_seconds"]), DURATION_KEYS)
        with self.assertRaises(MusicModelContractError):
            parse_music_capability_probe({**_probe(), "callback": "run"})
        duration = _probe()
        duration["duration_seconds"] = {"minimum": 1, "maximum": 2, "step": 1}
        with self.assertRaises(MusicModelContractError):
            parse_music_capability_probe(duration)

    def test_probe_rejects_missing_keys_without_silent_defaults(self):
        for key in CAPABILITY_KEYS:
            with self.subTest(key=key):
                value = _probe()
                del value[key]
                with self.assertRaises(MusicModelContractError):
                    parse_music_capability_probe(value)

    def test_probe_boolean_fields_reject_integer_truthiness(self):
        for key in ("lyrics", "instrumental", "seed", "cancel", "health", "unload"):
            with self.subTest(key=key):
                with self.assertRaises(MusicModelContractError):
                    parse_music_capability_probe(_probe(**{key: 1}))

    def test_probe_rejects_nonfinite_or_invalid_duration_bounds(self):
        invalid_bounds = (
            {"minimum": True, "maximum": 10},
            {"minimum": float("nan"), "maximum": 10},
            {"minimum": 1, "maximum": float("inf")},
            {"minimum": 0, "maximum": 10},
            {"minimum": 11, "maximum": 10},
            {"minimum": 1, "maximum": MAX_DURATION_SECONDS + 1},
        )
        for duration in invalid_bounds:
            with self.subTest(duration=duration):
                with self.assertRaises(MusicModelContractError):
                    parse_music_capability_probe(_probe(duration_seconds=duration))

    def test_probe_rejects_huge_and_executable_numeric_values(self):
        with self.assertRaises(MusicModelContractError):
            parse_music_capability_probe(_probe(
                duration_seconds={"minimum": 1, "maximum": 10**10_000},
            ))
        _ExecutableInteger.calls = 0
        with self.assertRaises(MusicModelContractError):
            parse_music_capability_probe(_probe(
                duration_seconds={"minimum": 1, "maximum": _ExecutableInteger(10)},
            ))
        self.assertEqual(_ExecutableInteger.calls, 0)

    def test_probe_rejects_invalid_frame_bounds(self):
        for frames in (True, 0, -1, MAX_FRAME_COUNT + 1, 1.5):
            with self.subTest(frames=frames):
                with self.assertRaises(MusicModelContractError):
                    parse_music_capability_probe(_probe(max_frames=frames))

    def test_output_formats_are_bounded_unique_and_canonical(self):
        for formats in (
            [],
            ["wav", "wav"],
            ["wav", "flac"],
            [" wav"],
            [1],
            "wav",
        ):
            with self.subTest(formats=formats):
                with self.assertRaises(MusicModelContractError):
                    parse_music_capability_probe(_probe(output_formats=formats))

    def test_executable_output_format_is_rejected_without_callbacks(self):
        _ExecutableString.calls = 0
        with self.assertRaises(MusicModelContractError):
            parse_music_capability_probe(_probe(
                output_formats=["flac", _ExecutableString("wav")],
            ))
        self.assertEqual(_ExecutableString.calls, 0)

    def test_direct_construction_rejects_mutable_or_invalid_probe_fields(self):
        capabilities = parse_music_capability_probe(_probe())
        with self.assertRaises(MusicModelContractError):
            type(capabilities)(
                lyrics=True,
                instrumental=True,
                minimum_duration_seconds=1,
                maximum_duration_seconds=600,
                seed=True,
                max_frames=576_000,
                output_formats=["wav"],
                cancel=True,
                health=True,
                unload=True,
            )


class MusicRequestTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = parse_music_capability_probe(_probe())

    def test_request_retains_every_supplied_field(self):
        source = {
            "lyrics": "Any locally authored lyrics remain opaque.",
            "instrumental": False,
            "duration_seconds": 90.5,
            "seed": 42,
            "max_frames": 86_400,
            "output_format": "flac",
        }
        validated = validate_music_request(source, self.capabilities)
        self.assertEqual(set(validated.to_mapping()), set(source))
        self.assertEqual(validated.to_mapping(), source)

    def test_unknown_or_executable_shaped_fields_fail_closed(self):
        for key, value in (
            ("callback", lambda: None),
            ("import", "adapter.module"),
            ("command", ["python", "adapter.py"]),
            ("executor", object()),
        ):
            with self.subTest(key=key):
                with self.assertRaises(MusicModelContractError):
                    validate_music_request(
                        {"duration_seconds": 10, key: value},
                        self.capabilities,
                    )

    def test_unsupported_fields_fail_before_any_value_is_returned(self):
        no_options = parse_music_capability_probe(_probe(
            lyrics=False,
            instrumental=False,
            seed=False,
            output_formats=["wav"],
        ))
        requests = (
            {"lyrics": "text"},
            {"instrumental": True},
            {"seed": 1},
            {"duration_seconds": 700},
            {"max_frames": 576_001},
            {"output_format": "flac"},
        )
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(UnsupportedMusicRequest):
                    validate_music_request(request, no_options)

    def test_request_rejects_bool_nonfinite_and_scalar_type_confusion(self):
        invalid_requests = (
            {"lyrics": 7},
            {"instrumental": 1},
            {"duration_seconds": True},
            {"duration_seconds": float("nan")},
            {"duration_seconds": float("inf")},
            {"seed": True},
            {"seed": 1.5},
            {"max_frames": True},
            {"max_frames": 1.5},
            {"output_format": 7},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(MusicModelContractError):
                    validate_music_request(request, self.capabilities)

    def test_request_rejects_numeric_bounds(self):
        invalid_requests = (
            {"duration_seconds": 0},
            {"duration_seconds": 601},
            {"seed": MIN_SEED - 1},
            {"seed": MAX_SEED + 1},
            {"max_frames": 0},
            {"max_frames": 576_001},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(MusicModelContractError):
                    validate_music_request(request, self.capabilities)

    def test_request_rejects_huge_integer_as_contract_error(self):
        with self.assertRaises(MusicModelContractError):
            validate_music_request(
                {"duration_seconds": 10**10_000},
                self.capabilities,
            )

    def test_instrumental_and_lyrics_are_mutually_exclusive(self):
        with self.assertRaises(MusicModelContractError):
            validate_music_request(
                {"instrumental": True, "lyrics": "words"},
                self.capabilities,
            )

    def test_empty_request_and_empty_lyrics_are_rejected(self):
        with self.assertRaises(MusicModelContractError):
            validate_music_request({}, self.capabilities)
        with self.assertRaises(MusicModelContractError):
            validate_music_request({"lyrics": ""}, self.capabilities)


class MusicProvenanceTests(unittest.TestCase):
    def test_projection_is_deterministic_immutable_and_content_free(self):
        contract = MusicModelContract(
            identity=parse_music_model_identity(_identity()),
            capabilities=parse_music_capability_probe(_probe()),
        )
        first_request = validate_music_request({
            "lyrics": "private first lyric",
            "duration_seconds": 30,
            "seed": 7,
            "output_format": "wav",
        }, contract.capabilities)
        second_request = validate_music_request({
            "lyrics": "different private lyric",
            "duration_seconds": 30,
            "seed": 7,
            "output_format": "wav",
        }, contract.capabilities)
        first = canonical_music_provenance(contract, first_request)
        second = canonical_music_provenance(contract, second_request)
        self.assertEqual(dict(first), dict(second))
        encoded = json.dumps({
            "schema_version": first["schema_version"],
            "identity": dict(first["identity"]),
            "capability_probe_sha256": first["capability_probe_sha256"],
            "request": dict(first["request"]),
        }, sort_keys=True)
        self.assertNotIn("private first lyric", encoded)
        self.assertNotIn("different private lyric", encoded)
        self.assertNotIn("lyrics", first["request"])
        self.assertIs(first["request"]["lyrics_supplied"], True)
        with self.assertRaises(TypeError):
            first["schema_version"] = 2
        with self.assertRaises(TypeError):
            first["identity"]["exact_revision"] = "git:" + ("1" * 40)
        with self.assertRaises(TypeError):
            first["request"]["lyrics"] = "injected"

    def test_probe_digest_changes_with_capability_contract(self):
        identity = parse_music_model_identity(_identity())
        base_capabilities = parse_music_capability_probe(_probe())
        changed_capabilities = parse_music_capability_probe(_probe(cancel=False))
        base = canonical_music_provenance(
            MusicModelContract(identity, base_capabilities),
            validate_music_request({"duration_seconds": 30}, base_capabilities),
        )
        changed = canonical_music_provenance(
            MusicModelContract(identity, changed_capabilities),
            validate_music_request({"duration_seconds": 30}, changed_capabilities),
        )
        self.assertNotEqual(
            base["capability_probe_sha256"],
            changed["capability_probe_sha256"],
        )

    def test_projection_rejects_request_bound_to_another_probe(self):
        identity = parse_music_model_identity(_identity())
        broad = parse_music_capability_probe(_probe())
        request = validate_music_request({"duration_seconds": 600}, broad)
        narrow_contract = MusicModelContract(
            identity,
            parse_music_capability_probe(_probe(
                duration_seconds={"minimum": 1, "maximum": 10},
            )),
        )
        with self.assertRaises(MusicModelContractError):
            canonical_music_provenance(narrow_contract, request)

    def test_public_constructors_do_not_create_forged_trust_tokens(self):
        identity = parse_music_model_identity(_identity())
        capabilities = parse_music_capability_probe(_probe(lyrics=False))
        contract = MusicModelContract(identity, capabilities)
        request_type = type(validate_music_request({"duration_seconds": 1}, capabilities))
        with self.assertRaises(MusicModelContractError):
            request_type((("callback", lambda: None),), "0" * 64)
        forged = request_type(
            (("lyrics", "not advertised"),),
            canonical_music_provenance(
                contract,
                validate_music_request({"duration_seconds": 1}, capabilities),
            )["capability_probe_sha256"],
        )
        with self.assertRaises(MusicModelContractError):
            canonical_music_provenance(contract, forged)


class MusicLifecycleTests(unittest.TestCase):
    def test_each_documented_transition_is_accepted(self):
        accepted = {
            ("unprobed", "probing"),
            ("probing", "ready"),
            ("probing", "unavailable"),
            ("probing", "failed"),
            ("ready", "working"),
            ("ready", "unavailable"),
            ("ready", "unloading"),
            ("ready", "failed"),
            ("working", "ready"),
            ("working", "unavailable"),
            ("working", "failed"),
            ("unavailable", "probing"),
            ("unavailable", "unloading"),
            ("unloading", "unloaded"),
            ("unloading", "failed"),
            ("unloaded", "probing"),
            ("failed", "probing"),
            ("failed", "unloading"),
        }
        self.assertEqual(
            {
                (previous, current)
                for previous, current_states in LIFECYCLE_TRANSITIONS.items()
                for current in current_states
            },
            accepted,
        )
        for previous, current in accepted:
            with self.subTest(previous=previous, current=current):
                self.assertEqual(
                    validate_music_lifecycle_transition(previous, current),
                    (previous, current),
                )

    def test_unknown_noop_and_impossible_transitions_fail_closed(self):
        for previous, current in (
            ("ready", "ready"),
            ("unprobed", "ready"),
            ("working", "unloaded"),
            ("unloaded", "working"),
            ("unknown", "probing"),
            ("ready", "unknown"),
            (True, "ready"),
        ):
            with self.subTest(previous=previous, current=current):
                with self.assertRaises(MusicModelContractError):
                    validate_music_lifecycle_transition(previous, current)

    def test_state_validation_is_exact_and_case_sensitive(self):
        self.assertEqual(validate_music_lifecycle_state("ready"), "ready")
        for state in ("READY", " ready", "busy", 1, None):
            with self.subTest(state=state):
                with self.assertRaises(MusicModelContractError):
                    validate_music_lifecycle_state(state)


class MusicContractIsolationTests(unittest.TestCase):
    def test_contract_has_only_inert_standard_library_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(
            imported_roots,
            {"__future__", "dataclasses", "hashlib", "json", "math", "types", "typing"},
        )

    def test_contract_contains_no_dynamic_execution_or_runtime_calls(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_calls = {"compile", "eval", "exec", "getattr", "__import__"}
        found = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_calls
        }
        self.assertEqual(found, set())


if __name__ == "__main__":
    unittest.main()
