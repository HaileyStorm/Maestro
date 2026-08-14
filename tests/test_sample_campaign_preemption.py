import threading
import unittest
from dataclasses import dataclass

from services.sample_campaign_preemption import (
    MINIMUM_POLL_INTERVAL_SECONDS,
    SampleCampaignPreemptionCoordinator,
)


@dataclass
class _Significance:
    known: bool
    significant: bool


@dataclass
class _Result:
    changed: bool


def _sample():
    return {
        "id": "sample-opaque",
        "kind": "sample_campaign_generation",
        "queue_class": "background_sample",
        "queue_priority": -1000,
        "status": "running",
        "queue_held": False,
        "resource_state": "running",
        "resource_intent": "generation",
        "resource_execution": "standard",
        "preemption_mode": "none",
        "execution_attempt": 4,
    }


class SampleCampaignPreemptionTests(unittest.TestCase):
    def _coordinator(self, **overrides):
        sample = overrides.pop("sample", _sample())
        calls = overrides.pop("calls", [])

        def request(job, **kwargs):
            calls.append((job, kwargs))
            return _Result(True)

        coordinator = SampleCampaignPreemptionCoordinator(
            jobs=overrides.pop("jobs", lambda: (sample,)),
            active_states=overrides.pop(
                "active_states", lambda: {sample["id"]: {"abort": False}},
            ),
            urgent_ordinary_work_present=overrides.pop("urgent", lambda: False),
            capture_foreign_significance=overrides.pop(
                "capture", lambda: _Significance(True, False),
            ),
            request_preemption=overrides.pop("request", request),
            **overrides,
        )
        return coordinator, calls

    def test_significant_external_work_requests_exact_attempt(self):
        coordinator, calls = self._coordinator(
            capture=lambda: _Significance(True, True),
        )
        decision = coordinator.poll_once()
        self.assertTrue(decision.requested)
        self.assertEqual(decision.reason, "significant_external_gpu_work")
        self.assertEqual(calls[0][1], {
            "job_id": "sample-opaque", "expected_execution_attempt": 4,
        })

    def test_urgent_ordinary_work_does_not_need_gpu_telemetry(self):
        coordinator, calls = self._coordinator(
            urgent=lambda: True,
            capture=lambda: self.fail("urgent work should short-circuit telemetry"),
        )
        self.assertEqual(coordinator.poll_once().reason, "urgent_ordinary_work")
        self.assertEqual(len(calls), 1)

    def test_unknown_or_aggregate_busy_never_requests(self):
        for significance in (
            _Significance(False, False), _Significance(True, False),
        ):
            with self.subTest(significance=significance):
                coordinator, calls = self._coordinator(
                    capture=lambda value=significance: value,
                )
                self.assertFalse(coordinator.poll_once().requested)
                self.assertEqual(calls, [])

    def test_user_job_and_unregistered_or_ambiguous_sample_are_untouched(self):
        sample = _sample()
        user = {**sample, "id": "user", "kind": "studio_generation",
                "queue_class": "user"}
        for jobs, states in (
            ((user,), {"user": {"abort": False}}),
            ((sample,), {}),
            ((sample, {**sample, "id": "sample-two"}), {
                "sample-opaque": {"abort": False},
                "sample-two": {"abort": False},
            }),
        ):
            with self.subTest(jobs=len(jobs)):
                coordinator, calls = self._coordinator(
                    sample=sample,
                    jobs=lambda value=jobs: value,
                    active_states=lambda value=states: value,
                    urgent=lambda: True,
                )
                self.assertFalse(coordinator.poll_once().requested)
                self.assertEqual(calls, [])

    def test_persistence_failure_result_or_exception_never_claims_request(self):
        for request in (
            lambda *_args, **_kwargs: _Result(False),
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")),
        ):
            coordinator, _calls = self._coordinator(
                urgent=lambda: True, request=request,
            )
            self.assertFalse(coordinator.poll_once().requested)

    def test_cadence_is_at_least_fifteen_seconds(self):
        with self.assertRaisesRegex(ValueError, "too short"):
            self._coordinator(poll_interval_seconds=14.999)
        coordinator, _calls = self._coordinator()
        self.assertEqual(
            coordinator.poll_interval_seconds, MINIMUM_POLL_INTERVAL_SECONDS,
        )

    def test_run_waits_between_polls_without_busy_loop(self):
        stop = threading.Event()
        waits = []
        polls = []
        now = [0.0]

        class _Stop:
            def is_set(self):
                return len(polls) >= 2

            def wait(self, delay):
                waits.append(delay)
                now[0] += delay
                return False

        coordinator, _calls = self._coordinator(
            monotonic_clock=lambda: now[0],
        )
        coordinator.poll_once = lambda: polls.append(True)
        coordinator.run(_Stop())
        self.assertEqual(len(polls), 2)
        self.assertGreaterEqual(waits[1], 15.0)


if __name__ == "__main__":
    unittest.main()
