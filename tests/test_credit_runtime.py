"""Pure contracts for the disabled-by-default runtime credit foundation."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.credit_runtime import (
    CreditRuntimeError,
    CreditRuntimePolicy,
    CreditTransitionConflict,
    public_credit_projection,
    quote_reservation,
    reserve_quote,
    revalidate_reservation,
    transition_reservation,
)

STANDARD_CAPABILITY = {
    "capability_id": "standard_creator_v1",
    "support_priority_eligible": True,
    "marker": "standard_support_priority_policy",
}
EXCLUDED_CAPABILITY = {
    "capability_id": "krea2_moody_mix_v7_fp8",
    "support_priority_eligible": False,
    "marker": "creator_terms_exclude_support_priority",
    "creator_term": "civitai_2731187_3209007_creator_terms",
}


def source(
    kind: str,
    event_id: str | None,
    units: int,
    *,
    granted: int | None = None,
    expires_at: str | None = None,
    status: str = "active",
    refund_state: str = "none",
) -> dict:
    return {
        "source": kind,
        "source_event_id": event_id,
        "granted_allowance": units if granted is None else granted,
        "effective_allowance": units,
        "expires_at": expires_at,
        "status": status,
        "refund_state": refund_state,
    }


def snapshot(*sources: dict, as_of: str = "2026-08-11T10:00:00Z") -> dict:
    return {
        "state": "recorded_not_enforced",
        "enforcement_enabled": False,
        "unit": "compute_seconds",
        "as_of": as_of,
        "effective_allowance": sum(item["effective_allowance"] for item in sources),
        "sources": list(sources),
    }


FREE_ZERO = source(
    "free", None, 0, status="inactive", refund_state="not_applicable",
)


class CreditRuntimeTests(unittest.TestCase):
    def quote(self, allowance: dict, **updates):
        arguments = {
            "realm": "hosted",
            "requested_units": 20,
            "recorded_allowance": allowance,
            "capability_priority": STANDARD_CAPABILITY,
        }
        arguments.update(updates)
        return quote_reservation(**arguments)

    def funded_snapshot(self):
        return snapshot(
            FREE_ZERO,
            source(
                "one_time_support", "evt_source_later", 20,
                expires_at="2026-08-11T12:00:00Z",
            ),
            source(
                "recurring_support", "evt_source_sooner", 10,
                expires_at="2026-08-11T11:00:00Z",
            ),
        )

    def enabled_quote(self):
        return self.quote(
            self.funded_snapshot(),
            requested_units=25,
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )

    def test_local_and_lan_are_unmetered_by_explicit_realm(self):
        for realm in ("local", "lan"):
            with self.subTest(realm=realm):
                quote = self.quote(
                    self.funded_snapshot(),
                    realm=realm,
                    policy=CreditRuntimePolicy(enforcement_enabled=True),
                )
                self.assertTrue(quote.submission_allowed)
                self.assertEqual(quote.decision, "unmetered_realm")
                self.assertFalse(quote.metering_applied)
                self.assertFalse(quote.priority_boost)
                self.assertFalse(quote.reservation_required)
                self.assertEqual(quote.allocations, ())

        with self.assertRaises(CreditRuntimeError):
            self.quote(self.funded_snapshot(), realm="remote")

    def test_hosted_zero_allowance_is_baseline_and_default_is_disabled(self):
        default = self.quote(snapshot(FREE_ZERO))
        explicitly_enabled = self.quote(
            snapshot(FREE_ZERO),
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        for quote in (default, explicitly_enabled):
            self.assertTrue(quote.submission_allowed)
            self.assertEqual(quote.decision, "hosted_baseline")
            self.assertFalse(quote.priority_boost)
            self.assertFalse(quote.reservation_required)
            self.assertEqual(quote.reserved_units, 0)
        self.assertFalse(default.policy_enforcement_enabled)
        self.assertFalse(default.metering_applied)
        self.assertTrue(explicitly_enabled.metering_applied)

    def test_partial_refunded_and_expired_hosted_allowance_never_denies(self):
        cases = {
            "partial": snapshot(source(
                "one_time_support", "evt_partial_0001", 10,
                granted=20, refund_state="partial",
            )),
            "refunded": snapshot(source(
                "one_time_support", "evt_refunded_0001", 0,
                granted=20, status="refunded", refund_state="full",
            )),
            "expired": snapshot(source(
                "recurring_support", "evt_expired_0001", 0,
                granted=20, expires_at="2026-08-11T09:00:00Z",
                status="expired",
            )),
        }
        for label, allowance in cases.items():
            with self.subTest(label=label):
                quote = self.quote(
                    allowance,
                    policy=CreditRuntimePolicy(enforcement_enabled=True),
                )
                self.assertTrue(quote.submission_allowed)
                self.assertTrue(quote.metering_applied)
                self.assertEqual(quote.decision, "hosted_baseline")
                self.assertFalse(quote.priority_boost)
                self.assertFalse(quote.reservation_required)
                self.assertEqual(quote.reserved_units, 0)
                self.assertEqual(quote.allocations, ())

    def test_exact_capability_exclusion_neutralizes_boost_not_submission(self):
        excluded = self.quote(
            self.funded_snapshot(),
            capability_priority=EXCLUDED_CAPABILITY,
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        self.assertTrue(excluded.submission_allowed)
        self.assertEqual(excluded.decision, "capability_excluded")
        self.assertFalse(excluded.priority_boost)
        self.assertEqual(excluded.allocations, ())

        similarly_named = self.quote(
            self.funded_snapshot(),
            capability_priority={
                **STANDARD_CAPABILITY,
                "capability_id": "moody_named_but_explicitly_eligible",
            },
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        self.assertTrue(similarly_named.priority_boost)

    def test_source_distinct_quote_is_deterministic_and_earliest_expiry_first(self):
        original = self.enabled_quote()
        allowance = self.funded_snapshot()
        allowance["sources"] = list(reversed(allowance["sources"]))
        reordered = self.quote(
            allowance,
            requested_units=25,
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        self.assertEqual(original.allocations, reordered.allocations)
        self.assertEqual(
            [(item.source_event_id, item.units) for item in original.allocations],
            [("evt_source_sooner", 10), ("evt_source_later", 15)],
        )

    def test_reservation_and_terminal_retries_are_idempotent(self):
        quote = self.enabled_quote()
        reserved = reserve_quote(quote, reservation_id="reservation_retry_0001")
        self.assertIs(
            reserve_quote(
                quote,
                reservation_id="reservation_retry_0001",
                current=reserved,
            ),
            reserved,
        )
        consumed = transition_reservation(reserved, "consume")
        self.assertEqual(consumed.status, "consumed")
        self.assertIs(transition_reservation(consumed, "consume"), consumed)
        self.assertIs(transition_reservation(consumed, "release"), consumed)

        released = transition_reservation(reserved, "release")
        self.assertFalse(
            public_credit_projection(quote, reservation=released)["priority_boost"],
        )

        different = self.quote(
            self.funded_snapshot(),
            requested_units=20,
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        with self.assertRaises(CreditTransitionConflict):
            reserve_quote(
                different,
                reservation_id="reservation_retry_0001",
                current=reserved,
            )
        with self.assertRaises(CreditRuntimeError):
            reserve_quote(
                quote,
                reservation_id="reservation_retry_0001",
                current=replace(reserved, revision=99),
            )
        corrupted = replace(reserved, status="bogus", revision=None)
        with self.assertRaises(CreditRuntimeError):
            reserve_quote(
                quote,
                reservation_id="reservation_retry_0001",
                current=corrupted,
            )
        with self.assertRaises(CreditRuntimeError):
            transition_reservation(corrupted, "consume")
        for poisoned in (
            replace(reserved, schema_version=True),
            replace(reserved, revision=True),
            replace(reserved, status=[]),
        ):
            with self.subTest(poisoned=poisoned), self.assertRaises(
                CreditRuntimeError,
            ):
                reserve_quote(
                    quote,
                    reservation_id="reservation_retry_0001",
                    current=poisoned,
                )
        with self.assertRaises(CreditRuntimeError):
            public_credit_projection(
                quote,
                reservation=replace(reserved, reserved_units=999),
            )

    def test_refund_or_expiry_downgrades_boost_but_never_submission(self):
        quote = self.enabled_quote()
        reserved = reserve_quote(quote, reservation_id="reservation_refund_0001")
        changed = snapshot(
            FREE_ZERO,
            source(
                "one_time_support", "evt_source_later", 0,
                granted=20,
                expires_at="2026-08-11T12:00:00Z",
                status="refunded",
                refund_state="full",
            ),
            source(
                "recurring_support", "evt_source_sooner", 0,
                granted=10,
                expires_at="2026-08-11T11:00:00Z",
                status="expired",
            ),
            as_of="2026-08-11T11:30:00Z",
        )
        result = revalidate_reservation(reserved, changed)
        self.assertEqual(result.state, "downgraded")
        self.assertTrue(result.submission_allowed)
        self.assertFalse(result.priority_boost_retained)
        self.assertEqual(result.available_reserved_units, 0)
        self.assertTrue(result.release_recommended)

        other_quote = self.quote(
            self.funded_snapshot(),
            requested_units=20,
            policy=CreditRuntimePolicy(enforcement_enabled=True),
        )
        other_reserved = reserve_quote(
            other_quote, reservation_id="reservation_refund_0002",
        )
        with self.assertRaises(CreditRuntimeError):
            public_credit_projection(
                other_quote,
                reservation=other_reserved,
                revalidation=result,
            )

    def test_concurrent_logical_consume_release_is_order_deterministic(self):
        reserved = reserve_quote(
            self.enabled_quote(), reservation_id="reservation_race_0001",
        )
        results = []
        for actions in permutations(("consume", "release")):
            state = reserved
            for action in actions:
                state = transition_reservation(state, action)
            results.append(state)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].status, "consumed")
        self.assertEqual(results[0].revision, 3)

    def test_inputs_are_strict_and_public_projection_is_content_free(self):
        bad_allowance = self.funded_snapshot()
        bad_allowance["private_text"] = "must not cross the schema"
        with self.assertRaises(CreditRuntimeError):
            self.quote(bad_allowance)

        bad_source = self.funded_snapshot()
        bad_source["sources"][1]["provider"] = "private-provider"
        with self.assertRaises(CreditRuntimeError):
            self.quote(bad_source)

        with self.assertRaises(CreditRuntimeError):
            self.quote(
                self.funded_snapshot(),
                capability_priority={**STANDARD_CAPABILITY, "unexpected": True},
            )
        with self.assertRaises(CreditRuntimeError):
            self.quote(
                self.funded_snapshot(),
                capability_priority={
                    **EXCLUDED_CAPABILITY,
                    "marker": "standard_support_priority_policy",
                },
            )

        inconsistent_refund = self.funded_snapshot()
        inconsistent_refund["sources"][1].update({
            "status": "refunded",
            "refund_state": "full",
        })
        with self.assertRaises(CreditRuntimeError):
            self.quote(inconsistent_refund)

        malformed_source = self.funded_snapshot()
        malformed_source["sources"][1]["source"] = []
        malformed_status = self.funded_snapshot()
        malformed_status["sources"][1]["status"] = []
        for updates in (
            {"realm": []},
            {"recorded_allowance": malformed_source},
            {"recorded_allowance": malformed_status},
            {"capability_priority": {**STANDARD_CAPABILITY, "marker": []}},
        ):
            with self.subTest(updates=tuple(updates)), self.assertRaises(
                CreditRuntimeError,
            ):
                self.quote(self.funded_snapshot(), **updates)

        quote = self.enabled_quote()
        reserved = reserve_quote(quote, reservation_id="reservation_private_0001")
        public = public_credit_projection(quote, reservation=reserved)
        serialized = json.dumps(public, sort_keys=True)
        for private_value in (
            "evt_source_later",
            "evt_source_sooner",
            "reservation_private_0001",
            "standard_creator_v1",
            "creator_terms",
        ):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(set(public), {
            "schema_version", "realm", "submission_allowed", "decision",
            "policy_enforcement_enabled", "metering_applied", "unit",
            "requested_units", "priority_boost", "reservation", "revalidation",
        })


if __name__ == "__main__":
    unittest.main()
