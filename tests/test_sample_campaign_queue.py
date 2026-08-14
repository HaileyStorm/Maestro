from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.sample_campaign import (  # noqa: E402
    CampaignArm,
    InterventionDelta,
    build_arm_manifest,
    build_pair_manifest,
    pair_manifest_digest,
)
from services.sample_campaign_queue import (  # noqa: E402
    MAX_PUBLIC_PAIRS,
    exact_reciprocal_sample_pairs,
    project_sample_campaign_queue,
)


PRIVATE_PROMPT = "PRIVATE_PROMPT_SENTINEL a dancer crosses a room"
PRIVATE_PATH = "/private/reference.png"
PRIVATE_FINGERPRINT = "a" * 64


def make_pair(
    *,
    pair_id: str = "pair-1",
    case_id: str = "case-1",
    seed: int = 42,
):
    settings = {
        "steps": 20,
        "resolution": {"width": 1280, "height": 720},
        "fps": 24,
    }

    def arm(kind: CampaignArm, interventions):
        return build_arm_manifest(
            arm=kind,
            raw_prompt=PRIVATE_PROMPT,
            private_input_paths=(PRIVATE_PATH,),
            input_fingerprints=(PRIVATE_FINGERPRINT,),
            model_revision="model-revision-1",
            settings=settings,
            seed=seed,
            output_index=0,
            interventions=interventions,
        )

    return build_pair_manifest(
        pair_id=pair_id,
        case_id=case_id,
        maestro=arm(CampaignArm.MAESTRO, ("maestro.reference_lock",)),
        control=arm(CampaignArm.CONTROL, ()),
        intervention_delta=InterventionDelta(
            maestro_only=("maestro.reference_lock",),
        ),
    )


def make_jobs(pair=None, *, state: str = "held", workspace: str = "project-a"):
    pair = pair or make_pair()
    digest = pair_manifest_digest(pair)
    jobs = []
    for arm, peer in (("maestro", "control"), ("control", "maestro")):
        job_id = f"sample-{pair.pair_id}-{arm}"
        peer_id = f"sample-{pair.pair_id}-{peer}"
        job = {
            "id": job_id,
            "kind": "sample_campaign_generation",
            "status": "queued",
            "workspace": workspace,
            "_recovery_owner_digest": "owner:v1:" + "b" * 64,
            "_recovery_project_digest": "project:v1:" + "c" * 64,
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": True,
            "recovery_state": "sample_campaign_held",
            "resource_state": "queued",
            "progress": 0,
            "output_files": [],
            "recovery_cursor": {"sample_campaign": {
                "schema": 1,
                "pair_id": pair.pair_id,
                "pair_manifest_digest": digest,
                "arm": arm,
                "peer_job_id": peer_id,
            }},
        }
        jobs.append(job)
    if state == "running_maestro":
        jobs[0].update({
            "status": "running",
            "queue_held": False,
            "recovery_state": "sample_campaign_released",
            "resource_state": "running",
            "progress": 31,
        })
    elif state == "control_held":
        jobs[0].update({
            "status": "completed",
            "queue_held": False,
            "recovery_state": "terminal",
            "resource_state": "released",
            "progress": 100,
            "output_files": ["private-maestro.mp4"],
        })
    elif state == "completed":
        for job in jobs:
            job.update({
                "status": "completed",
                "queue_held": False,
                "recovery_state": "terminal",
                "resource_state": "released",
                "progress": 100,
                "output_files": [f"private-{job['id']}.mp4"],
            })
    return jobs


class SampleCampaignQueueTests(unittest.TestCase):
    def test_projects_exact_held_pair_without_private_content(self):
        pair = make_pair()
        payload = project_sample_campaign_queue(
            make_jobs(pair), load_pair_manifest=lambda _job: pair,
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["pairs"]), 1)
        item = payload["pairs"][0]
        self.assertEqual(item["queue_state"], "held")
        self.assertEqual(
            [arm["arm"] for arm in item["arms"]],
            ["maestro", "control"],
        )
        self.assertEqual(
            item["pair"]["evaluation"]["evidence_class"],
            "manifest_only",
        )
        rendered = json.dumps(payload, sort_keys=True)
        for private in (
            PRIVATE_PROMPT,
            PRIVATE_PATH,
            PRIVATE_FINGERPRINT,
            pair_manifest_digest(pair),
            "output_files",
            "pair_manifest_digest",
            "owner:v1:",
            "project:v1:",
        ):
            self.assertNotIn(private, rendered)

    def test_derives_running_held_and_outputs_unbound_states(self):
        pair = make_pair()
        for state, expected in (
            ("running_maestro", "running_arm"),
            ("control_held", "held"),
            ("completed", "outputs_unbound"),
        ):
            with self.subTest(state=state):
                payload = project_sample_campaign_queue(
                    make_jobs(pair, state=state),
                    load_pair_manifest=lambda _job: pair,
                )
                item = payload["pairs"][0]
                self.assertEqual(item["queue_state"], expected)
                self.assertEqual(
                    item["pair"]["evaluation"]["evidence_class"],
                    "manifest_only",
                )
                self.assertNotEqual(item["queue_state"], "awaiting_review")
        completed = project_sample_campaign_queue(
            make_jobs(pair, state="completed"),
            load_pair_manifest=lambda _job: pair,
        )["pairs"][0]
        self.assertTrue(all(arm["output_available"] for arm in completed["arms"]))
        self.assertEqual([arm["output_count"] for arm in completed["arms"]], [1, 1])

    def test_drops_incomplete_nonreciprocal_duplicate_and_cross_identity_groups(self):
        pair = make_pair()
        jobs = make_jobs(pair)
        self.assertEqual(len(exact_reciprocal_sample_pairs(jobs)), 1)
        variants = []
        variants.append(jobs[:1])
        nonreciprocal = copy.deepcopy(jobs)
        nonreciprocal[1]["recovery_cursor"]["sample_campaign"]["peer_job_id"] = "other"
        variants.append(nonreciprocal)
        duplicate = copy.deepcopy(jobs)
        duplicate[1]["recovery_cursor"]["sample_campaign"]["arm"] = "maestro"
        variants.append(duplicate)
        cross_owner = copy.deepcopy(jobs)
        cross_owner[1]["_recovery_owner_digest"] = "owner:v1:" + "d" * 64
        variants.append(cross_owner)
        cross_project = copy.deepcopy(jobs)
        cross_project[1]["workspace"] = "project-b"
        variants.append(cross_project)
        for candidate in variants:
            with self.subTest(candidate=candidate):
                self.assertEqual(exact_reciprocal_sample_pairs(candidate), ())
                self.assertEqual(
                    project_sample_campaign_queue(
                        candidate, load_pair_manifest=lambda _job: pair,
                    )["pairs"],
                    [],
                )

    def test_drops_manifest_mismatch_loader_failure_but_blocks_bad_lifecycle(self):
        pair = make_pair()
        jobs = make_jobs(pair)
        other = make_pair(pair_id="pair-2")
        for loader in (
            lambda _job: other,
            lambda _job: (_ for _ in ()).throw(OSError("sealed read failed")),
        ):
            with self.subTest(loader=loader):
                self.assertEqual(
                    project_sample_campaign_queue(
                        jobs, load_pair_manifest=loader,
                    )["pairs"],
                    [],
                )
        malformed = copy.deepcopy(jobs)
        malformed[0]["progress"] = float("nan")
        projected = project_sample_campaign_queue(
            malformed, load_pair_manifest=lambda _job: pair,
        )
        self.assertEqual(projected["pairs"][0]["arms"][0]["progress"], 0)
        missing_output = make_jobs(pair, state="completed")
        missing_output[1]["output_files"] = []
        projected = project_sample_campaign_queue(
            missing_output, load_pair_manifest=lambda _job: pair,
        )
        self.assertEqual(projected["pairs"][0]["queue_state"], "blocked")
        self.assertEqual(projected["pairs"][0]["arms"][1]["status"], "failed")

    def test_maps_terminal_failure_cancel_and_blocked_state_without_error_text(self):
        pair = make_pair()
        for raw_status in ("failed", "cancelled", "error", "blocked"):
            jobs = make_jobs(pair)
            jobs[0].update({
                "status": raw_status,
                "queue_held": False,
                "recovery_state": "terminal",
                "resource_state": "released",
                "error": "PRIVATE_ERROR_SENTINEL /private/error.log",
            })
            with self.subTest(raw_status=raw_status):
                payload = project_sample_campaign_queue(
                    jobs, load_pair_manifest=lambda _job: pair,
                )
                item = payload["pairs"][0]
                self.assertEqual(item["queue_state"], "blocked")
                self.assertEqual(item["arms"][0]["status"], "failed")
                self.assertNotIn("PRIVATE_ERROR_SENTINEL", json.dumps(payload))
        control_failed_first = make_jobs(pair)
        control_failed_first[1].update({
            "status": "failed",
            "queue_held": False,
            "recovery_state": "terminal",
            "resource_state": "released",
        })
        projected = project_sample_campaign_queue(
            control_failed_first, load_pair_manifest=lambda _job: pair,
        )
        self.assertEqual(projected["pairs"][0]["queue_state"], "blocked")
        self.assertEqual(projected["pairs"][0]["arms"][1]["status"], "failed")

    def test_drops_impossible_cross_arm_lifecycle_states(self):
        pair = make_pair()
        both_running = make_jobs(pair, state="running_maestro")
        both_running[1].update({
            "status": "running",
            "queue_held": False,
            "recovery_state": "sample_campaign_released",
            "resource_state": "running",
        })
        control_first = make_jobs(pair)
        control_first[1].update({
            "status": "completed",
            "queue_held": False,
            "recovery_state": "terminal",
            "resource_state": "released",
            "output_files": ["private-control.mp4"],
        })
        for jobs in (both_running, control_first):
            with self.subTest(jobs=jobs):
                self.assertEqual(
                    project_sample_campaign_queue(
                        jobs, load_pair_manifest=lambda _job: pair,
                    )["pairs"],
                    [],
                )

    def test_drops_conflicting_digests_for_one_public_pair_id(self):
        first = make_pair(pair_id="pair-shared", case_id="case-a")
        second = make_pair(pair_id="pair-shared", case_id="case-b")
        first_jobs = make_jobs(first)
        second_jobs = make_jobs(second)
        for job in second_jobs:
            arm = job["recovery_cursor"]["sample_campaign"]["arm"]
            peer = "control" if arm == "maestro" else "maestro"
            job["id"] = f"sample-pair-shared-{arm}-other"
            job["recovery_cursor"]["sample_campaign"]["peer_job_id"] = (
                f"sample-pair-shared-{peer}-other"
            )
        by_digest = {
            pair_manifest_digest(first): first,
            pair_manifest_digest(second): second,
        }
        payload = project_sample_campaign_queue(
            first_jobs + second_jobs,
            load_pair_manifest=lambda job: by_digest[
                job["recovery_cursor"]["sample_campaign"]
                ["pair_manifest_digest"]
            ],
        )
        self.assertEqual(payload["pairs"], [])
        payload = project_sample_campaign_queue(
            first_jobs + second_jobs[:1],
            load_pair_manifest=lambda job: by_digest[
                job["recovery_cursor"]["sample_campaign"]
                ["pair_manifest_digest"]
            ],
        )
        self.assertEqual(payload["pairs"], [])

    def test_uses_durable_identities_and_caps_sorted_pairs(self):
        jobs = make_jobs()
        for job in jobs:
            job["owner_principal"] = job.pop("_recovery_owner_digest")
            job["project_instance"] = job.pop("_recovery_project_digest")
        self.assertEqual(len(exact_reciprocal_sample_pairs(jobs)), 1)

        all_jobs = []
        pairs = {}
        for index in range(MAX_PUBLIC_PAIRS + 4):
            pair = make_pair(
                pair_id=f"pair-{index:03d}", case_id=f"case-{index:03d}",
            )
            pairs[pair.pair_id] = pair
            all_jobs.extend(make_jobs(pair))
        payload = project_sample_campaign_queue(
            all_jobs,
            load_pair_manifest=lambda job: pairs[
                job["recovery_cursor"]["sample_campaign"]["pair_id"]
            ],
        )
        self.assertEqual(len(payload["pairs"]), MAX_PUBLIC_PAIRS)
        self.assertEqual(payload["pairs"][0]["pair"]["pair_id"], "pair-000")
        self.assertEqual(payload["pairs"][-1]["pair"]["pair_id"], "pair-099")

    def test_seed_is_canonical_uint64_decimal_string_and_malformed_drops(self):
        pair = make_pair(seed=2**64 - 1)
        payload = project_sample_campaign_queue(
            make_jobs(pair), load_pair_manifest=lambda _job: pair,
        )
        seed = payload["pairs"][0]["pair"]["shared_generation"]["seed"]
        self.assertEqual(seed, "18446744073709551615")
        self.assertEqual(
            json.loads(json.dumps(payload))["pairs"][0]["pair"]
            ["shared_generation"]["seed"],
            "18446744073709551615",
        )

        from services import sample_campaign_queue as queue_module

        valid_projection = queue_module.public_pair_projection(pair)
        for malformed in (2**64, -1, "18446744073709551615", "01"):
            projection = copy.deepcopy(valid_projection)
            projection["shared_generation"]["seed"] = malformed
            with self.subTest(malformed=malformed), mock.patch.object(
                queue_module, "public_pair_projection", return_value=projection,
            ):
                self.assertEqual(
                    project_sample_campaign_queue(
                        make_jobs(pair), load_pair_manifest=lambda _job: pair,
                    )["pairs"],
                    [],
                )


if __name__ == "__main__":
    unittest.main()
