"""Pure contract tests for explicit H3 multi-speaker turn planning."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import sys
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.director.h3_turn_plan import (  # noqa: E402
    H3_LEGAL_BLOCKED,
    H3_TURN_PLAN_MODE,
    H3_WRITTEN_AUTHORIZATION_VERIFIED,
    H3TurnPlanError,
    build_h3_turn_plan,
    canonical_h3_turn_plan_json,
    is_h3_turn_plan_mode,
    normalize_h3_turn_plan,
    public_h3_turn_plan_projection,
    replay_h3_turn_plan,
    validate_h3_turn_plan,
)


_AUDIO_SHA256 = hashlib.sha256(b"synthetic dialogue master").hexdigest()


class H3TurnPlanTests(unittest.TestCase):
    def _build(self, turns=None, *, total_frames=120, audio_sha256=_AUDIO_SHA256):
        return build_h3_turn_plan(
            mode=H3_TURN_PLAN_MODE,
            legal_authorization={
                "state": H3_LEGAL_BLOCKED,
                "territory": "US",
            },
            dialogue_master={
                "audio_sha256": audio_sha256,
                "audio_format": "WAV",
                "sample_rate_hz": 48_000,
                "channels": 2,
                "duration_frames": 144,
            },
            speakers=[
                {"speaker_id": "speaker.alice", "subject_id": "subject.alice"},
                {"speaker_id": "speaker.bob", "subject_id": "subject.bob"},
                {"speaker_id": "speaker.lee", "subject_id": "subject.lee"},
            ],
            turns=turns
            or [
                {
                    "speaker_id": "speaker.alice",
                    "text": "Keep the exact first line.",
                    "start_frame": 12,
                    "end_frame": 36,
                    "source_audio_start_frame": 6,
                    "source_audio_end_frame": 30,
                    "visible_subject_ids": ["subject.alice", "subject.bob"],
                },
                {
                    "speaker_id": "speaker.bob",
                    "text": "And preserve this reply exactly.",
                    "start_frame": 48,
                    "end_frame": 72,
                    "source_audio_start_frame": 42,
                    "source_audio_end_frame": 66,
                    "visible_subject_ids": [
                        "subject.alice",
                        "subject.bob",
                        "subject.lee",
                    ],
                },
            ],
            total_frames=total_frames,
        )

    def test_canonical_roundtrip_and_tamper_rejection(self):
        plan = self._build()
        payload = canonical_h3_turn_plan_json(plan)

        normalized = normalize_h3_turn_plan(plan)
        self.assertNotIn("plan_seal", normalized)
        self.assertEqual(normalized["turns"][0]["text_sha256"], hashlib.sha256(
            normalized["turns"][0]["text"].encode("utf-8")
        ).hexdigest())
        self.assertEqual(replay_h3_turn_plan(payload), plan)
        self.assertEqual(canonical_h3_turn_plan_json(replay_h3_turn_plan(payload)), payload)
        self.assertEqual(validate_h3_turn_plan(copy.deepcopy(plan)), plan)

        for mutate in (
            lambda value: value["turns"][0].__setitem__("text", "Changed"),
            lambda value: value["turns"][0].__setitem__("start_seconds", 0.51),
            lambda value: value["turns"][1]["continuity"].__setitem__(
                "seam_id", "h3seam1_" + "0" * 64
            ),
        ):
            altered = copy.deepcopy(plan)
            mutate(altered)
            with self.subTest(altered=altered["turns"][0]["text"]):
                with self.assertRaises(H3TurnPlanError):
                    validate_h3_turn_plan(altered)

        with self.assertRaisesRegex(H3TurnPlanError, "canonical"):
            replay_h3_turn_plan(json.dumps(plan, indent=2))
        duplicated = payload.replace(
            '"mode":"m3_turn_conditioned"',
            '"mode":"m3_turn_conditioned","mode":"m3_turn_conditioned"',
            1,
        )
        with self.assertRaisesRegex(H3TurnPlanError, "repeats key"):
            replay_h3_turn_plan(duplicated)

    def test_speaker_changes_same_speaker_adjacency_and_silent_listeners(self):
        turns = [
            {
                "speaker_id": "speaker.alice",
                "text": "First.",
                "start_frame": 0,
                "end_frame": 12,
                "source_audio_start_frame": 0,
                "source_audio_end_frame": 12,
                "visible_subject_ids": ["subject.alice", "subject.bob"],
            },
            {
                "speaker_id": "speaker.alice",
                "text": "Still Alice after a pause.",
                "start_frame": 18,
                "end_frame": 30,
                "source_audio_start_frame": 18,
                "source_audio_end_frame": 30,
                "visible_subject_ids": [
                    "subject.alice",
                    "subject.bob",
                    "subject.lee",
                ],
            },
            {
                "speaker_id": "speaker.bob",
                "text": "Now Bob.",
                "start_frame": 30,
                "end_frame": 42,
                "source_audio_start_frame": 30,
                "source_audio_end_frame": 42,
                "visible_subject_ids": ["subject.alice", "subject.bob"],
            },
        ]
        plan = self._build(turns, total_frames=48)

        self.assertEqual([turn["speaker_id"] for turn in plan["turns"]], [
            "speaker.alice",
            "speaker.alice",
            "speaker.bob",
        ])
        for turn in plan["turns"]:
            conditioning = turn["conditioning"]
            self.assertEqual(conditioning["kind"], "sole_speaker")
            self.assertEqual(
                conditioning["sole_conditioned_speaker_id"], turn["speaker_id"]
            )
            self.assertNotIn(
                turn["speaker_subject_id"], conditioning["visible_silent_subject_ids"]
            )
            self.assertEqual(
                set(conditioning["visible_silent_subject_ids"]),
                set(conditioning["visible_subject_ids"]) - {turn["speaker_subject_id"]},
            )
        self.assertEqual(plan["turns"][1]["pause_before_frames"], 6)
        self.assertEqual(plan["turns"][1]["gap_after_frames"], 0)

    def test_overlap_and_ambiguous_speaker_mapping_fail_closed(self):
        turns = [
            {
                "speaker_id": "speaker.alice",
                "text": "One.",
                "start_frame": 0,
                "end_frame": 24,
                "source_audio_start_frame": 0,
                "source_audio_end_frame": 24,
                "visible_subject_ids": ["subject.alice", "subject.bob"],
            },
            {
                "speaker_id": "speaker.bob",
                "text": "Unsupported simultaneous interruption.",
                "start_frame": 23,
                "end_frame": 35,
                "source_audio_start_frame": 24,
                "source_audio_end_frame": 36,
                "visible_subject_ids": ["subject.alice", "subject.bob"],
            },
        ]
        with self.assertRaisesRegex(H3TurnPlanError, "one voice at a time"):
            self._build(turns)

        with self.assertRaisesRegex(H3TurnPlanError, "multiple speakers"):
            build_h3_turn_plan(
                mode=H3_TURN_PLAN_MODE,
                legal_authorization={
                    "state": H3_LEGAL_BLOCKED,
                    "territory": "US",
                },
                dialogue_master={
                    "audio_sha256": _AUDIO_SHA256,
                    "audio_format": "wav",
                    "sample_rate_hz": 48_000,
                    "channels": 1,
                    "duration_frames": 24,
                },
                speakers=[
                    {"speaker_id": "a", "subject_id": "same"},
                    {"speaker_id": "b", "subject_id": "same"},
                ],
                turns=[turns[0]],
                total_frames=24,
            )

    def test_frame_authority_rounding_and_total_conservation(self):
        plan = self._build(
            [
                {
                    "speaker_id": "speaker.alice",
                    "text": "Frame one through seven.",
                    "start_frame": 1,
                    "end_frame": 7,
                    "source_audio_start_frame": 2,
                    "source_audio_end_frame": 8,
                    "visible_subject_ids": ["subject.alice"],
                },
                {
                    "speaker_id": "speaker.bob",
                    "text": "Frame eleven through nineteen.",
                    "start_frame": 11,
                    "end_frame": 19,
                    "source_audio_start_frame": 12,
                    "source_audio_end_frame": 20,
                    "visible_subject_ids": ["subject.bob"],
                },
            ],
            total_frames=25,
        )
        first, second = plan["turns"]
        self.assertEqual(first["start_seconds"], round(1 / 24, 9))
        self.assertEqual(second["end_seconds"], round(19 / 24, 9))
        self.assertTrue(all(
            math_value == math_value and abs(math_value) != float("inf")
            for math_value in (
                first["start_seconds"],
                first["end_seconds"],
                second["start_seconds"],
                second["end_seconds"],
            )
        ))
        occupied = sum(turn["end_frame"] - turn["start_frame"] for turn in plan["turns"])
        gaps = plan["turns"][0]["pause_before_frames"] + sum(
            turn["gap_after_frames"] for turn in plan["turns"]
        )
        self.assertEqual(occupied + gaps, plan["total_frames"])

    def test_public_projection_contains_no_private_text_or_audio_fingerprint(self):
        plan = self._build()
        projection = public_h3_turn_plan_projection(plan)
        encoded = json.dumps(projection, sort_keys=True)

        self.assertNotIn("Keep the exact first line", encoded)
        self.assertNotIn("preserve this reply", encoded)
        self.assertNotIn(_AUDIO_SHA256, encoded)
        self.assertNotIn("text_sha256", encoded)
        self.assertNotIn("plan_seal", projection)
        self.assertRegex(projection["projection_seal"], r"^[0-9a-f]{64}$")

        changed_text_turns = [
            {
                "speaker_id": "speaker.alice",
                "text": "A completely different private first line.",
                "start_frame": 12,
                "end_frame": 36,
                "source_audio_start_frame": 6,
                "source_audio_end_frame": 30,
                "visible_subject_ids": ["subject.alice", "subject.bob"],
            },
            {
                "speaker_id": "speaker.bob",
                "text": "Different private reply.",
                "start_frame": 48,
                "end_frame": 72,
                "source_audio_start_frame": 42,
                "source_audio_end_frame": 66,
                "visible_subject_ids": [
                    "subject.alice",
                    "subject.bob",
                    "subject.lee",
                ],
            },
        ]
        changed_private = self._build(
            changed_text_turns,
            audio_sha256=hashlib.sha256(b"different private master").hexdigest(),
        )
        self.assertEqual(
            public_h3_turn_plan_projection(changed_private), projection
        )

    def test_private_turn_identity_binds_master_subjects_and_visibility(self):
        baseline = self._build()
        changed_master = self._build(
            audio_sha256=hashlib.sha256(b"different master").hexdigest()
        )
        changed_visibility_turns = [
            {
                "speaker_id": "speaker.alice",
                "text": "Keep the exact first line.",
                "start_frame": 12,
                "end_frame": 36,
                "source_audio_start_frame": 6,
                "source_audio_end_frame": 30,
                "visible_subject_ids": [
                    "subject.alice",
                    "subject.bob",
                    "subject.lee",
                ],
            },
            {
                "speaker_id": "speaker.bob",
                "text": "And preserve this reply exactly.",
                "start_frame": 48,
                "end_frame": 72,
                "source_audio_start_frame": 42,
                "source_audio_end_frame": 66,
                "visible_subject_ids": [
                    "subject.alice",
                    "subject.bob",
                    "subject.lee",
                ],
            },
        ]
        changed_visibility = self._build(changed_visibility_turns)
        self.assertNotEqual(
            baseline["turns"][0]["turn_id"], changed_master["turns"][0]["turn_id"]
        )
        self.assertNotEqual(
            baseline["turns"][0]["turn_id"],
            changed_visibility["turns"][0]["turn_id"],
        )
        self.assertNotEqual(
            baseline["turns"][0]["continuity"]["seam_id"],
            changed_master["turns"][0]["continuity"]["seam_id"],
        )

    def test_legal_authorization_is_explicit_and_fail_closed(self):
        blocked = self._build()
        self.assertEqual(
            blocked["legal_authorization"],
            {
                "state": H3_LEGAL_BLOCKED,
                "territory": "US",
                "runtime_allowed": False,
                "authorization_scope": "h3_local_inference",
            },
        )
        self.assertFalse(
            public_h3_turn_plan_projection(blocked)["legal_authorization"][
                "runtime_allowed"
            ]
        )

        raw = normalize_h3_turn_plan(blocked)
        raw["legal_authorization"] = {
            "state": H3_WRITTEN_AUTHORIZATION_VERIFIED,
            "territory": "authorized.test",
            "authorization_evidence_sha256": hashlib.sha256(
                b"synthetic separately reviewed authorization fixture"
            ).hexdigest(),
        }
        verified = build_h3_turn_plan(
            mode=H3_TURN_PLAN_MODE,
            legal_authorization=raw["legal_authorization"],
            dialogue_master=raw["dialogue_master"],
            speakers=raw["speakers"],
            turns=raw["turns"],
            total_frames=raw["total_frames"],
        )
        self.assertTrue(verified["legal_authorization"]["runtime_allowed"])

        for authorization in (
            None,
            {"state": "unknown", "territory": "US"},
            {"state": H3_LEGAL_BLOCKED, "territory": "US", "runtime_allowed": True},
            {
                "state": H3_WRITTEN_AUTHORIZATION_VERIFIED,
                "territory": "US",
            },
        ):
            with self.subTest(authorization=authorization):
                source = normalize_h3_turn_plan(blocked)
                source["legal_authorization"] = authorization
                with self.assertRaises(H3TurnPlanError):
                    normalize_h3_turn_plan(source)

    def test_conflicting_timing_authority_and_oversized_replay_are_rejected(self):
        plan = self._build()
        source = normalize_h3_turn_plan(plan)
        source["dialogue_master"]["fps"] = 25
        with self.assertRaisesRegex(H3TurnPlanError, "exactly 24"):
            normalize_h3_turn_plan(source)

        mixed = normalize_h3_turn_plan(plan)
        mixed["turns"][0]["source_audio_start_frame"] = 7
        with self.assertRaisesRegex(H3TurnPlanError, "mix flat and nested"):
            normalize_h3_turn_plan(mixed)

        contradictory_total = normalize_h3_turn_plan(plan)
        contradictory_total["total_seconds"] += 0.001
        with self.assertRaisesRegex(H3TurnPlanError, "total_seconds"):
            normalize_h3_turn_plan(contradictory_total)

        with self.assertRaisesRegex(H3TurnPlanError, "payload limit"):
            replay_h3_turn_plan(b" " * (3_145_728 + 1))

    def test_maximum_escaped_dialogue_still_roundtrips_within_replay_bound(self):
        text = '"' * 262_144
        turns = [
            {
                "speaker_id": "speaker.alice",
                "text": text,
                "start_frame": index,
                "end_frame": index + 1,
                "source_audio_start_frame": index,
                "source_audio_end_frame": index + 1,
                "visible_subject_ids": ["subject.alice"],
            }
            for index in range(4)
        ]
        plan = self._build(turns, total_frames=4)
        payload = canonical_h3_turn_plan_json(plan)
        self.assertGreater(len(payload.encode("utf-8")), 2_000_000)
        self.assertEqual(replay_h3_turn_plan(payload), plan)

    def test_large_visibility_matrix_fails_before_creating_unreplayable_seal(self):
        suffix = "x" * 96
        speakers = [
            {
                "speaker_id": f"spk.{index:03d}.{suffix}",
                "subject_id": f"sub.{index:03d}.{suffix}",
            }
            for index in range(128)
        ]
        visible = [speaker["subject_id"] for speaker in speakers]
        turns = [
            {
                "speaker_id": speakers[index]["speaker_id"],
                "text": f"Turn {index}.",
                "start_frame": index,
                "end_frame": index + 1,
                "source_audio_start_frame": index,
                "source_audio_end_frame": index + 1,
                "visible_subject_ids": visible,
            }
            for index in range(128)
        ]
        with self.assertRaisesRegex(H3TurnPlanError, "replay payload limit"):
            build_h3_turn_plan(
                mode=H3_TURN_PLAN_MODE,
                legal_authorization={
                    "state": H3_LEGAL_BLOCKED,
                    "territory": "US",
                },
                dialogue_master={
                    "audio_sha256": _AUDIO_SHA256,
                    "audio_format": "wav",
                    "sample_rate_hz": 48_000,
                    "channels": 2,
                    "duration_frames": 144,
                },
                speakers=speakers,
                turns=turns,
                total_frames=128,
            )

    def test_source_audio_overlap_and_duration_mismatch_are_rejected(self):
        turns = [
            {
                "speaker_id": "speaker.alice",
                "text": "First.",
                "start_frame": 0,
                "end_frame": 12,
                "source_audio_start_frame": 0,
                "source_audio_end_frame": 12,
                "visible_subject_ids": ["subject.alice"],
            },
            {
                "speaker_id": "speaker.bob",
                "text": "Second.",
                "start_frame": 12,
                "end_frame": 24,
                "source_audio_start_frame": 11,
                "source_audio_end_frame": 23,
                "visible_subject_ids": ["subject.bob"],
            },
        ]
        with self.assertRaisesRegex(H3TurnPlanError, "overlaps prior"):
            self._build(turns, total_frames=24)

        turns[1]["source_audio_start_frame"] = 12
        turns[1]["source_audio_end_frame"] = 25
        with self.assertRaisesRegex(H3TurnPlanError, "equal frame counts"):
            self._build(turns, total_frames=24)

    def test_mode_is_explicit_and_legacy_paths_remain_outside_this_contract(self):
        self.assertTrue(is_h3_turn_plan_mode(H3_TURN_PLAN_MODE))
        self.assertFalse(is_h3_turn_plan_mode(None))
        self.assertFalse(is_h3_turn_plan_mode("single_speaker"))
        self.assertFalse(is_h3_turn_plan_mode("legacy_alternating"))
        with self.assertRaisesRegex(H3TurnPlanError, "legacy modes pass through"):
            build_h3_turn_plan(
                mode="legacy_alternating",
                legal_authorization={
                    "state": H3_LEGAL_BLOCKED,
                    "territory": "US",
                },
                dialogue_master={},
                speakers=[],
                turns=[],
                total_frames=1,
            )

    def test_contract_has_no_project_runtime_or_executable_dependency_imports(self):
        source_path = os.path.join(
            _APP_DIR, "services", "director", "h3_turn_plan.py"
        )
        with open(source_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertLessEqual(
            imported_roots,
            {"__future__", "collections", "hashlib", "json", "math", "re", "typing"},
        )
        self.assertFalse(imported_roots & {"torch", "subprocess", "services", "launch"})


if __name__ == "__main__":
    unittest.main()
