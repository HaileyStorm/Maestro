"""CPU-only contracts for inert MiniMax H3 Fun ControlNet Union plans."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import unittest

from services.h3_control_plan import (
    H3_CONTROL_BLOCKS,
    H3_CONTROL_KINDS,
    H3_CONTROL_SOURCE_REVISION,
    H3ControlPlanError,
    canonical_h3_control_plan,
    plan_h3_control_request,
    validate_h3_control_plan,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _plan(kind: str = "canny", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "control_kind": kind,
        "source_sha256": _digest("control-video"),
        "source_width": 1920,
        "source_height": 1080,
        "source_frame_count": 400,
        "target_width": 960,
        "target_height": 544,
        "strength": 0.75,
        "mask_sha256": _digest("mask") if kind == "inpaint" else None,
    }
    values.update(overrides)
    return plan_h3_control_request(**values)


def _reseal(plan: dict[str, object]) -> None:
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    plan["plan_sha256"] = "sha256:" + hashlib.sha256(encoded).hexdigest()


class H3ControlPlanTests(unittest.TestCase):
    def test_all_six_exact_control_kinds_build_one_v1_control(self) -> None:
        self.assertEqual(
            H3_CONTROL_KINDS,
            frozenset({"canny", "depth", "hed", "mlsd", "pose", "inpaint"}),
        )
        for kind in sorted(H3_CONTROL_KINDS):
            with self.subTest(kind=kind):
                plan = _plan(kind)
                self.assertNotIn("controls", plan)
                self.assertEqual(plan["control"]["version"], 1)
                self.assertEqual(plan["control"]["kind"], kind)
                self.assertEqual(
                    plan["control"]["mask_sha256"],
                    _digest("mask") if kind == "inpaint" else None,
                )

        for invalid in ("Canny", "softedge", "video", "", 1, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3ControlPlanError):
                    _plan(invalid)  # type: ignore[arg-type]

    def test_inpaint_requires_one_mask_and_other_kinds_forbid_it(self) -> None:
        with self.assertRaisesRegex(H3ControlPlanError, "sha256-prefixed"):
            _plan("inpaint", mask_sha256=None)
        with self.assertRaisesRegex(H3ControlPlanError, "only valid for inpaint"):
            _plan("depth", mask_sha256=_digest("mask"))
        with self.assertRaises(H3ControlPlanError):
            _plan("inpaint", mask_sha256="mask.png")

    def test_source_frames_floor_to_17n_plus_5_and_cap_at_345(self) -> None:
        for source, expected in (
            (5, 5),
            (21, 5),
            (22, 22),
            (38, 22),
            (39, 39),
            (344, 328),
            (345, 345),
            (346, 345),
            (10_000, 345),
        ):
            with self.subTest(source=source):
                geometry = _plan(source_frame_count=source)["geometry"]
                self.assertEqual(geometry["source_frame_count"], source)
                self.assertEqual(geometry["frame_count"], expected)
                self.assertEqual((expected - 5) % 17, 0)
                self.assertLessEqual(expected, 345)
        for invalid in (4, True, 5.0, "5"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3ControlPlanError):
                    _plan(source_frame_count=invalid)

    def test_aspect_fit_is_deterministic_contained_and_multiple_of_32(self) -> None:
        cases = (
            ((1920, 1080, 960, 544), (960, 544)),
            ((1080, 1920, 960, 544), (320, 544)),
            ((1024, 1024, 960, 544), (544, 544)),
            ((1920, 800, 960, 544), (960, 416)),
        )
        for (source_w, source_h, bound_w, bound_h), expected in cases:
            with self.subTest(source=(source_w, source_h)):
                geometry = _plan(
                    source_width=source_w,
                    source_height=source_h,
                    target_width=bound_w,
                    target_height=bound_h,
                )["geometry"]
                self.assertEqual((geometry["width"], geometry["height"]), expected)
                self.assertEqual(geometry["width"] % 32, 0)
                self.assertEqual(geometry["height"] % 32, 0)
                self.assertLessEqual(geometry["width"], bound_w)
                self.assertLessEqual(geometry["height"], bound_h)

        for field in ("target_width", "target_height"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(H3ControlPlanError, "multiple of 32"):
                    _plan(**{field: 1000})
        with self.assertRaisesRegex(H3ControlPlanError, "32-pixel minimum"):
            _plan(source_width=10_000, source_height=1, target_width=32, target_height=32)

    def test_strength_is_finite_bounded_and_canonical_float(self) -> None:
        for value, expected in ((0, 0.0), (1, 1.0), (0.25, 0.25)):
            with self.subTest(value=value):
                strength = _plan(strength=value)["control"]["strength"]
                self.assertIs(type(strength), float)
                self.assertEqual(strength, expected)
        for invalid in (-0.01, 1.01, math.nan, math.inf, -math.inf, True, "0.5"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(H3ControlPlanError):
                    _plan(strength=invalid)

    def test_fixed_h3_union_contract_is_inert_and_audio_neutral(self) -> None:
        plan = _plan()
        self.assertEqual(plan["base_family"], "minimax_h3")
        self.assertEqual(plan["fps"], 24)
        self.assertIs(plan["execution_available"], False)
        self.assertIs(plan["automatic_fallback"], False)
        self.assertEqual(plan["control"]["guidance_scale"], 1.0)
        self.assertEqual(plan["control"]["control_blocks"], list(H3_CONTROL_BLOCKS))
        self.assertEqual(plan["control"]["control_in_dim"], 49)
        self.assertIs(plan["control"]["control_apply_audio"], False)
        self.assertEqual(
            plan["implementation"]["revision"], H3_CONTROL_SOURCE_REVISION
        )

        for kwargs in (
            {"base_family": "minimax_h3_ref2va"},
            {"fps": 25},
            {"fps": 24.0},
            {"execution_available": True},
            {"automatic_fallback": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(H3ControlPlanError):
                    _plan(**kwargs)

    def test_canonical_digest_is_deterministic_and_tamper_evident(self) -> None:
        first = _plan()
        second = _plan()
        self.assertEqual(canonical_h3_control_plan(first), canonical_h3_control_plan(second))
        self.assertEqual(validate_h3_control_plan(first), first)

        mutations = (
            lambda value: value.__setitem__("execution_available", True),
            lambda value: value.__setitem__("automatic_fallback", True),
            lambda value: value.__setitem__("base_family", "other"),
            lambda value: value.__setitem__("fps", 25),
            lambda value: value["geometry"].__setitem__("frame_count", 344),
            lambda value: value["geometry"].__setitem__("width", 928),
            lambda value: value["control"].__setitem__("guidance_scale", 2.0),
            lambda value: value["control"].__setitem__("control_blocks", [0, 20, 40]),
            lambda value: value["control"].__setitem__("control_in_dim", 48),
            lambda value: value["control"].__setitem__("control_apply_audio", True),
            lambda value: value.__setitem__("prompt", "private"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(first)
            mutate(changed)
            _reseal(changed)
            with self.subTest(changed=changed):
                with self.assertRaises(H3ControlPlanError):
                    validate_h3_control_plan(changed)

        digest_only = copy.deepcopy(first)
        digest_only["plan_sha256"] = _digest("tampered")
        with self.assertRaisesRegex(H3ControlPlanError, "digest drifted"):
            validate_h3_control_plan(digest_only)

    def test_plan_contains_commitments_and_geometry_not_runtime_content(self) -> None:
        encoded = canonical_h3_control_plan(_plan("inpaint")).decode("ascii")
        for forbidden in ("path", "prompt", "tensor", "torch", "runtime", "content"):
            self.assertNotIn(forbidden, encoded.lower())

        import services.h3_control_plan as module

        self.assertFalse(hasattr(module, "execute_h3_control_plan"))
        self.assertFalse(hasattr(module, "load_h3_control_model"))

    def test_plain_json_boundary_rejects_scalar_subclasses_cycles_and_extras(self) -> None:
        class EqualityString(str):
            def __eq__(self, other: object) -> bool:
                return True

            __hash__ = str.__hash__

        plan = _plan()
        hostile = copy.deepcopy(plan)
        hostile["control"]["kind"] = EqualityString("wrong")
        _reseal(hostile)
        with self.assertRaisesRegex(H3ControlPlanError, "exact plain JSON"):
            validate_h3_control_plan(hostile)

        cyclic: list[object] = []
        cyclic.append(cyclic)
        plan["control"]["control_blocks"] = cyclic
        with self.assertRaisesRegex(H3ControlPlanError, "JSON cycle"):
            validate_h3_control_plan(plan)


if __name__ == "__main__":
    unittest.main()
