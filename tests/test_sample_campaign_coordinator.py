"""Model-free atomic submission coverage for comparative sample pairs."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.queue_recovery_runtime import (  # noqa: E402
    discover_request_manifest_pointers,
    load_request_manifest,
    remove_request_manifest,
    write_sealed_request_manifest,
)
from services.queue_recovery import QueueRecoveryJournal  # noqa: E402
from services.queue_recovery_adapter import QueueRecoveryCoordinator  # noqa: E402
from services.job_lifecycle import DurableTransition  # noqa: E402
from services.sample_campaign import (  # noqa: E402
    CampaignArm,
    InterventionDelta,
    build_arm_manifest,
    build_pair_manifest,
    pair_manifest_digest,
)
from services.sample_campaign_coordinator import (  # noqa: E402
    HeldSamplePairCoordinator,
    SampleArmSubmission,
    SampleCampaignSubmissionError,
    parse_private_pair_manifest,
    sample_arm_job_id,
    validate_private_arm_request,
)


PRIVATE_PROMPT = "PRIVATE_SAMPLE_PROMPT"
PRIVATE_PATH = "/private/reference.png"
FINGERPRINT = "a" * 64
OWNER = "owner:v1:" + "b" * 64
PROJECT = "project:v1:" + "c" * 64
SETTINGS = {
    "steps": 20,
    "resolution": {"width": 640, "height": 384},
    "fps": 24,
    "model_type": "runtime-model-1",
    "seed": 42,
}


def _arm(arm: CampaignArm):
    return build_arm_manifest(
        arm=arm,
        raw_prompt=PRIVATE_PROMPT,
        private_input_paths=(PRIVATE_PATH,),
        input_fingerprints=(FINGERPRINT,),
        model_revision="model-revision-1",
        settings=SETTINGS,
        seed=42,
        output_index=0,
        interventions=("maestro.reference_lock",) if arm is CampaignArm.MAESTRO else (),
    )


def _pair():
    return build_pair_manifest(
        pair_id="reference-lock-1",
        case_id="reference-lock",
        maestro=_arm(CampaignArm.MAESTRO),
        control=_arm(CampaignArm.CONTROL),
        intervention_delta=InterventionDelta(
            maestro_only=("maestro.reference_lock",),
        ),
    )


def _job(job_id: str):
    return {
        "id": job_id,
        "kind": "sample_campaign_generation",
        "status": "queued",
        "created_at": 1,
        "params": {
            "prompt": PRIVATE_PROMPT,
            "model_type": "runtime-model-1",
            "seed": 42,
            **copy.deepcopy(SETTINGS),
        },
        "prompt_preview": PRIVATE_PROMPT,
    }


def _submission(directory: str, arm: CampaignArm, job_id: str):
    pair = _pair()
    manifest = pair.maestro if arm is CampaignArm.MAESTRO else pair.control
    return SampleArmSubmission(
        arm=arm,
        manifest=manifest,
        job=_job(job_id),
        project_directory=directory,
        owner_digest=OWNER,
        project_digest=PROJECT,
        request_inputs=({"field": "image_start:0", "scope": "synthetic"},),
        generation_settings=copy.deepcopy(SETTINGS),
    )


def _job_id(arm: CampaignArm) -> str:
    return sample_arm_job_id(pair_manifest_digest(_pair()), arm)


class _Harness:
    def __init__(self, directory: str):
        self.directory = directory
        self.events = []
        self.durable = {}
        self.visible = {}
        self.fail_manifest_at = 0
        self.fail_register = False
        self.fail_publish_at = 0
        self.fail_rollback = False
        self.manifest_calls = 0

    def write(self, *args, **kwargs):
        self.manifest_calls += 1
        self.events.append(f"manifest:{self.manifest_calls}")
        if self.fail_manifest_at == self.manifest_calls:
            raise RuntimeError("manifest failed")
        return write_sealed_request_manifest(*args, **kwargs)

    def register(self, registrations, *, global_state):
        self.events.append("register")
        if self.fail_register:
            raise RuntimeError("journal failed")
        self.durable = {
            job["id"]: (copy.deepcopy(job), dict(pointer))
            for job, _owner, _project, pointer in registrations
        }
        self.global_state = copy.deepcopy(global_state)

    def rollback(self, job_ids):
        self.events.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        for job_id in job_ids:
            self.durable.pop(job_id, None)

    def publish(self, entries):
        self.events.append("publish")
        staged = {}
        for index, (job_id, job) in enumerate(entries, 1):
            if self.fail_publish_at == index:
                raise RuntimeError("publication failed")
            staged[job_id] = job
        self.visible.update(staged)

    def coordinator(self):
        return HeldSamplePairCoordinator(
            write_manifest=self.write,
            remove_manifest=remove_request_manifest,
            register_jobs_atomic=self.register,
            rollback_jobs_atomic=self.rollback,
            publish_jobs_atomic=self.publish,
            global_state_for_jobs=lambda jobs: {
                "queue_order": [job["id"] for job in jobs],
            },
        )


class HeldSamplePairCoordinatorTests(unittest.TestCase):
    def _submit(self, harness: _Harness):
        return harness.coordinator().submit(
            _pair(),
            (
                _submission(
                    harness.directory, CampaignArm.MAESTRO,
                    _job_id(CampaignArm.MAESTRO),
                ),
                _submission(harness.directory, CampaignArm.CONTROL, _job_id(CampaignArm.CONTROL)),
            ),
        )

    def test_success_writes_both_private_manifests_before_one_commit_and_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory)
            response = self._submit(harness)

            self.assertEqual(
                harness.events,
                ["manifest:1", "manifest:2", "register", "publish"],
            )
            expected_ids = {
                _job_id(CampaignArm.MAESTRO),
                _job_id(CampaignArm.CONTROL),
            }
            self.assertEqual(set(harness.durable), expected_ids)
            self.assertEqual(set(harness.visible), set(harness.durable))
            for job_id, (job, pointer) in harness.durable.items():
                self.assertEqual(job["queue_class"], "background_sample")
                self.assertEqual(job["queue_priority"], -1000)
                self.assertTrue(job["queue_held"])
                self.assertEqual(job["prompt_preview"], "")
                self.assertNotIn("_sample_campaign_private", job["params"])
                manifest = load_request_manifest(
                    directory, pointer, expected_job_id=job_id,
                )
                self.assertEqual(manifest["params"]["prompt"], PRIVATE_PROMPT)
                private = manifest["params"]["_sample_campaign_private"]
                self.assertEqual(
                    private["linkage"], job["recovery_cursor"]["sample_campaign"],
                )
                self.assertEqual(
                    parse_private_pair_manifest(private["pair_manifest"]), _pair(),
                )
                self.assertEqual(
                    os.stat(Path(directory) / pointer["path"]).st_mode & 0o777,
                    0o600,
                )
            rendered = repr(response)
            self.assertNotIn(PRIVATE_PROMPT, rendered)
            self.assertNotIn(PRIVATE_PATH, rendered)
            self.assertNotIn(FINGERPRINT, rendered)
            self.assertEqual(response["status"], "held")

    def test_precommit_manifest_or_journal_failure_leaves_no_evidence_or_visibility(self):
        for failure in ("manifest", "journal"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                harness = _Harness(directory)
                harness.fail_manifest_at = 2 if failure == "manifest" else 0
                harness.fail_register = failure == "journal"
                with self.assertRaises(RuntimeError):
                    self._submit(harness)
                self.assertEqual(harness.durable, {})
                self.assertEqual(harness.visible, {})
                self.assertEqual(discover_request_manifest_pointers(directory), [])

    def test_precommit_cleanup_removes_only_manifests_created_by_this_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            prior = write_sealed_request_manifest(
                directory,
                job_id=_job_id(CampaignArm.MAESTRO),
                params={"prompt": "prior private request"},
                inputs=(),
            )
            harness = _Harness(directory)
            harness.fail_register = True
            with self.assertRaisesRegex(RuntimeError, "journal"):
                self._submit(harness)
            discovered = discover_request_manifest_pointers(directory)
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["pointer"], prior)

    def test_publication_failure_rolls_back_both_before_removing_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory)
            harness.fail_publish_at = 2
            with self.assertRaisesRegex(RuntimeError, "publication"):
                self._submit(harness)
            self.assertEqual(harness.events[-2:], ["publish", "rollback"])
            self.assertEqual(harness.durable, {})
            self.assertEqual(harness.visible, {})
            self.assertEqual(discover_request_manifest_pointers(directory), [])

    def test_failed_durable_rollback_preserves_both_manifests_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory)
            harness.fail_publish_at = 1
            harness.fail_rollback = True
            with self.assertRaisesRegex(RuntimeError, "publication"):
                self._submit(harness)
            self.assertEqual(set(harness.durable), {
                _job_id(CampaignArm.MAESTRO),
                _job_id(CampaignArm.CONTROL),
            })
            self.assertEqual(harness.visible, {})
            self.assertEqual(
                {item["job_id"] for item in discover_request_manifest_pointers(directory)},
                {_job_id(CampaignArm.MAESTRO), _job_id(CampaignArm.CONTROL)},
            )

    def test_real_journal_publication_failure_tombstones_both_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            durable = QueueRecoveryCoordinator(journal)
            visible = {}

            def publish(entries):
                for index, (job_id, job) in enumerate(entries):
                    if index == 1:
                        raise RuntimeError("injected second publication failure")
                    # Stage privately like the launch registry; the batch is
                    # not made visible until every entry passes.
                    _ = (job_id, job)

            coordinator = HeldSamplePairCoordinator(
                write_manifest=write_sealed_request_manifest,
                remove_manifest=remove_request_manifest,
                register_jobs_atomic=durable.register_jobs_atomic,
                rollback_jobs_atomic=lambda job_ids: durable.prospective_transition(
                    DurableTransition(
                        name="sample_pair_test_rollback",
                        tombstones=tuple(job_ids),
                        global_state={
                            "paused": False,
                            "pause_after_current": False,
                            "manual_order_sequence": 0,
                            "queue_order": [],
                        },
                    )
                ),
                publish_jobs_atomic=publish,
                global_state_for_jobs=lambda jobs: {
                    "paused": False,
                    "pause_after_current": False,
                    "manual_order_sequence": 0,
                    "queue_order": [job["id"] for job in jobs],
                },
            )
            with self.assertRaisesRegex(RuntimeError, "second publication"):
                coordinator.submit(
                    _pair(),
                    (
                        _submission(
                            directory, CampaignArm.MAESTRO,
                            _job_id(CampaignArm.MAESTRO),
                        ),
                        _submission(
                            directory, CampaignArm.CONTROL,
                            _job_id(CampaignArm.CONTROL),
                        ),
                    ),
                )
            self.assertEqual(visible, {})
            self.assertEqual(QueueRecoveryCoordinator(journal).restore().jobs, {})
            self.assertEqual(discover_request_manifest_pointers(directory), [])

    def test_deterministic_ids_fence_duplicate_pair_without_harming_first(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            durable = QueueRecoveryCoordinator(journal)
            visible = {}
            coordinator = HeldSamplePairCoordinator(
                write_manifest=write_sealed_request_manifest,
                remove_manifest=remove_request_manifest,
                register_jobs_atomic=durable.register_jobs_atomic,
                rollback_jobs_atomic=lambda _job_ids: None,
                publish_jobs_atomic=lambda entries: visible.update(entries),
                global_state_for_jobs=lambda jobs: {
                    "paused": False,
                    "pause_after_current": False,
                    "manual_order_sequence": 0,
                    "queue_order": [job["id"] for job in jobs],
                },
            )
            submissions = (
                _submission(
                    directory, CampaignArm.MAESTRO,
                    _job_id(CampaignArm.MAESTRO),
                ),
                _submission(
                    directory, CampaignArm.CONTROL,
                    _job_id(CampaignArm.CONTROL),
                ),
            )
            coordinator.submit(_pair(), submissions)
            first_pointers = discover_request_manifest_pointers(directory)
            with self.assertRaises(Exception):
                coordinator.submit(_pair(), submissions)
            restored = QueueRecoveryCoordinator(journal).restore().jobs
            self.assertEqual(set(restored), set(visible))
            self.assertEqual(
                discover_request_manifest_pointers(directory), first_pointers,
            )

    def test_duplicate_arm_or_job_id_fails_before_manifest_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory)
            duplicate_arm = _submission(
                directory, CampaignArm.MAESTRO, _job_id(CampaignArm.CONTROL),
            )
            with self.assertRaises(SampleCampaignSubmissionError):
                harness.coordinator().submit(
                    _pair(),
                    (
                        _submission(
                            directory, CampaignArm.MAESTRO,
                            _job_id(CampaignArm.MAESTRO),
                        ),
                        duplicate_arm,
                    ),
                )
            self.assertEqual(harness.manifest_calls, 0)

    def test_arm_generation_settings_must_match_in_full(self):
        with tempfile.TemporaryDirectory() as directory:
            harness = _Harness(directory)
            maestro = _submission(
                directory, CampaignArm.MAESTRO,
                _job_id(CampaignArm.MAESTRO),
            )
            control = _submission(
                directory, CampaignArm.CONTROL,
                _job_id(CampaignArm.CONTROL),
            )
            control = SampleArmSubmission(
                arm=control.arm,
                manifest=control.manifest,
                job=control.job,
                project_directory=control.project_directory,
                owner_digest=control.owner_digest,
                project_digest=control.project_digest,
                request_inputs=control.request_inputs,
                generation_settings={**control.generation_settings, "fps": 25},
            )
            with self.assertRaisesRegex(
                SampleCampaignSubmissionError, "must match exactly",
            ):
                harness.coordinator().submit(_pair(), (maestro, control))
            self.assertEqual(harness.manifest_calls, 0)

    def test_private_parser_and_authorized_request_checks_fail_closed(self):
        pair = _pair()
        raw = {
            "schema_version": pair.schema_version,
            "pair_id": pair.pair_id,
            "case_id": pair.case_id,
            "intervention_delta": {
                "maestro_only": list(pair.intervention_delta.maestro_only),
                "control_only": list(pair.intervention_delta.control_only),
            },
        }
        for name, manifest in (("maestro", pair.maestro), ("control", pair.control)):
            raw[name] = {
                "arm": manifest.arm.value,
                "raw_prompt": manifest.raw_prompt,
                "prompt_digest": manifest.prompt_digest,
                "private_input_paths": list(manifest.private_input_paths),
                "input_fingerprints": list(manifest.input_fingerprints),
                "input_digest": manifest.input_digest,
                "model_revision": manifest.model_revision,
                "settings": manifest.settings.to_dict(),
                "seed": manifest.seed,
                "output_index": manifest.output_index,
                "interventions": list(manifest.interventions),
            }
        parsed = parse_private_pair_manifest(raw)
        self.assertEqual(parsed, pair)
        params = _job("sample")["params"]
        validate_private_arm_request(
            pair.maestro,
            params,
            server_model_revision="model-revision-1",
            generation_settings=SETTINGS,
            authorized_input_paths=(PRIVATE_PATH,),
            input_fingerprints=(FINGERPRINT,),
            output_index=0,
        )
        for change in (
            {"prompt": "changed"},
            {"model_type": "changed"},
            {"seed": 43},
            {"fps": 25},
        ):
            with self.subTest(change=change), self.assertRaises(
                SampleCampaignSubmissionError,
            ):
                validate_private_arm_request(
                    pair.maestro,
                    {**params, **change},
                    server_model_revision=(
                        "changed" if "model_type" in change
                        else "model-revision-1"
                    ),
                    generation_settings={
                        key: value
                        for key, value in {**params, **change}.items()
                        if key != "prompt"
                    },
                    authorized_input_paths=(PRIVATE_PATH,),
                    input_fingerprints=(FINGERPRINT,),
                    output_index=0,
                )


if __name__ == "__main__":
    unittest.main()
