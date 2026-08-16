"""CPU-only regressions for producer-attested quarantine adoption."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock
import uuid

from services.queue_recovery_final_adoption import (
    adopt_quarantined_final_groups,
)
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    recovery_unit_id,
)


class QueueFinalAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.quarantine = self.project / ".maestro-recovery" / "quarantine"
        self.quarantine.mkdir(parents=True, mode=0o700)
        os.chmod(self.project / ".maestro-recovery", 0o700)
        os.chmod(self.quarantine, 0o700)
        self.workspace = "project"

    def tearDown(self):
        self.temporary.cleanup()

    def _quarantined_pair(self, output: str, payload: bytes, meta: dict) -> tuple[Path, Path]:
        media = self.quarantine / f"{uuid.uuid4().hex}-{output}"
        sidecar = self.quarantine / (
            f"{uuid.uuid4().hex}-{Path(output).stem}.meta.json"
        )
        media.write_bytes(payload)
        sealed = dict(meta)
        sealed.update({
            "output_filename": output,
            "producer_media_sha256": hashlib.sha256(payload).hexdigest(),
            "producer_media_size": len(payload),
        })
        sidecar.write_text(json.dumps(sealed, sort_keys=True), encoding="utf-8")
        return media, sidecar

    def _base_meta(
        self,
        *,
        job_id: str,
        kind: str,
        variant: int,
        index: int,
        output_index: int,
        output_total: int,
        dependencies: list[str],
        settings: dict,
        artifacts: list[str],
    ) -> dict:
        unit_id = recovery_unit_id(
            job_id,
            kind,
            variant=variant,
            index=index,
            dependencies=dependencies,
            settings=settings,
        )
        role = "component" if kind == "h3_segment" else "final"
        return {
            "artifact_class": role,
            "job_id": job_id,
            "params": {
                "multi_clip_info": {
                    "output_index": output_index,
                    "output_total": output_total,
                },
            },
            "private": True,
            "producer_artifact_class": role,
            "producer_unit_artifact_names": artifacts,
            "producer_unit_dependencies": dependencies,
            "producer_unit_id": unit_id,
            "producer_unit_index": index,
            "producer_unit_kind": kind,
            "producer_unit_settings": settings,
            "producer_unit_variant": variant,
            "workspace": self.workspace,
        }

    def _concat_job(self, job_id: str, total: int, *, tag: str = "") -> dict:
        finals = []
        components = []
        for variant in range(total):
            dependencies = []
            component_hashes = []
            previous_continuation_sha = ""
            for segment in range(6):
                output = f"{job_id}{tag}-v{variant}-s{segment}.mp4"
                payload = f"component:{job_id}:{variant}:{segment}".encode()
                settings = {
                    "discard_prefix_frames": 0,
                    "segment": segment,
                    "tag": tag,
                    "trim_tail_frames": 0,
                }
                prior = list(dependencies[-1:])
                if prior:
                    settings.update({
                        "predecessor_artifact_hashes": [component_hashes[-1]],
                        "predecessor_continuation_sha256": previous_continuation_sha,
                    })
                meta = self._base_meta(
                    job_id=job_id,
                    kind="h3_segment",
                    variant=variant,
                    index=segment,
                    output_index=variant,
                    output_total=total,
                    dependencies=prior,
                    settings=settings,
                    artifacts=[output],
                )
                previous_continuation_sha = hashlib.sha256(
                    f"continuation:{job_id}:{tag}:{variant}:{segment}".encode()
                ).hexdigest()
                meta["producer_unit_continuation"] = {
                    "basename": f"{job_id}{tag}-v{variant}-s{segment}-continuation.png",
                    "dependency": meta["producer_unit_id"],
                    "mode": "last_frame",
                    "sha256": previous_continuation_sha,
                    "size": 123,
                    "storage": "recovery_staging",
                }
                components.append(self._quarantined_pair(
                    output,
                    payload,
                    meta,
                ))
                dependencies.append(meta["producer_unit_id"])
                component_hashes.append(hashlib.sha256(payload).hexdigest())
            final_output = f"{job_id}{tag}-v{variant}-final.mp4"
            final_meta = self._base_meta(
                job_id=job_id,
                kind="h3_concat",
                variant=variant,
                index=0,
                output_index=variant,
                output_total=total,
                dependencies=dependencies,
                settings={
                    "clip_start_frames": [0] * 6,
                    "clip_tail_frames": [0] * 6,
                    "component_hashes": component_hashes,
                    "concat": True,
                    "tag": tag,
                },
                artifacts=[final_output],
            )
            finals.append(self._quarantined_pair(
                final_output,
                f"final:{job_id}:{variant}".encode(),
                final_meta,
            ))
        return {"components": components, "finals": finals}

    def _delivery_job(self, job_id: str, total: int) -> dict:
        dependencies = []
        components = []
        previous_hash = ""
        previous_continuation_sha = ""
        for segment in range(2):
            output = f"{job_id}-component-{segment}.mp4"
            settings = {
                "discard_prefix_frames": 0,
                "segment": segment,
                "trim_tail_frames": 0,
            }
            if dependencies:
                settings.update({
                    "predecessor_artifact_hashes": [previous_hash],
                    "predecessor_continuation_sha256": previous_continuation_sha,
                })
            meta = self._base_meta(
                job_id=job_id,
                kind="h3_segment",
                variant=0,
                index=segment,
                output_index=0,
                output_total=total,
                dependencies=list(dependencies[-1:]),
                settings=settings,
                artifacts=[output],
            )
            previous_continuation_sha = hashlib.sha256(
                f"continuation:{job_id}:{segment}".encode()
            ).hexdigest()
            meta["producer_unit_continuation"] = {
                "basename": f"{job_id}-s{segment}-continuation.png",
                "dependency": meta["producer_unit_id"],
                "mode": "last_frame",
                "sha256": previous_continuation_sha,
                "size": 123,
                "storage": "recovery_staging",
            }
            components.append(self._quarantined_pair(output, output.encode(), meta))
            dependencies.append(meta["producer_unit_id"])
            previous_hash = hashlib.sha256(output.encode()).hexdigest()
        names = [f"{job_id}-delivery-{index}.mp4" for index in range(total)]
        settings = {"delivery": True}
        unit_id = recovery_unit_id(
            job_id,
            "h3_delivery",
            variant=0,
            index=0,
            dependencies=dependencies,
            settings=settings,
        )
        finals = []
        for output_index, output in enumerate(names):
            meta = self._base_meta(
                job_id=job_id,
                kind="h3_delivery",
                variant=0,
                index=0,
                output_index=output_index,
                output_total=total,
                dependencies=dependencies,
                settings=settings,
                artifacts=names,
            )
            self.assertEqual(meta["producer_unit_id"], unit_id)
            finals.append(self._quarantined_pair(output, output.encode(), meta))
        return {"components": components, "finals": finals}

    def _copy_final_destinations(self, fixture: dict) -> list[tuple[Path, Path]]:
        copied = []
        for source_media, source_sidecar in fixture["finals"]:
            media_name = source_media.name.split("-", 1)[1]
            sidecar_name = source_sidecar.name.split("-", 1)[1]
            destination_media = self.project / media_name
            destination_sidecar = self.project / sidecar_name
            shutil.copyfile(source_media, destination_media)
            shutil.copyfile(source_sidecar, destination_sidecar)
            copied.append((destination_media, destination_sidecar))
        return copied

    def test_adopts_exact_four_plus_one_and_keeps_components_quarantined(self):
        first = self._concat_job("job-four", 4)
        second = self._concat_job("job-one", 1)
        for orphan in range(4):
            output = f"orphan-{orphan}.mp4"
            meta = self._base_meta(
                job_id="orphan-job",
                kind="h3_segment",
                variant=0,
                index=orphan,
                output_index=0,
                output_total=1,
                dependencies=[],
                settings={"orphan": orphan},
                artifacts=[output],
            )
            self._quarantined_pair(output, output.encode(), meta)
        unrelated = {
            "unrelated-a.mp4": b"unrelated-a",
            "unrelated-b.mp4": b"unrelated-b",
        }
        for name, payload in unrelated.items():
            (self.project / name).write_bytes(payload)

        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(summary["declared_groups"], 2)
        self.assertEqual(summary["adopted_groups"], 2)
        self.assertEqual(summary["missing_groups"], 0)
        self.assertEqual(summary["quarantined_groups"], 0)
        self.assertEqual(sum(job["adopted"] for job in summary["jobs"]), 5)
        for pair in first["finals"] + second["finals"]:
            output = pair[0].name.split("-", 1)[1]
            self.assertTrue((self.project / output).is_file())
            self.assertTrue((self.project / f"{Path(output).stem}.meta.json").is_file())
        self.assertEqual(
            len(list(self.quarantine.glob("*"))),
            34 * 2,
        )
        for name, payload in unrelated.items():
            self.assertEqual((self.project / name).read_bytes(), payload)

        receipts = sorted((
            self.project / ".maestro-recovery" / "final-adoption" / "receipts"
        ).glob("*.json"))
        before = {path.name: path.read_bytes() for path in receipts}
        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 2)
        self.assertEqual(before, {path.name: path.read_bytes() for path in receipts})

    def test_all_five_preexisting_exact_copies_adopt_without_consuming_sources(self):
        first = self._concat_job("job-copy-four", 4)
        second = self._concat_job("job-copy-one", 1)
        copied = self._copy_final_destinations(first) + self._copy_final_destinations(second)
        source_bytes = {
            path: path.read_bytes()
            for fixture in (first, second)
            for pair in fixture["finals"]
            for path in pair
        }
        destination_bytes = {
            path: path.read_bytes()
            for pair in copied
            for path in pair
        }

        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(summary["adopted_groups"], 2)
        self.assertEqual(sum(job["adopted"] for job in summary["jobs"]), 5)
        self.assertTrue(all(path.exists() for path in source_bytes))
        self.assertEqual(source_bytes, {path: path.read_bytes() for path in source_bytes})
        self.assertEqual(
            destination_bytes,
            {path: path.read_bytes() for path in destination_bytes},
        )
        for fixture, destinations in ((first, copied[:4]), (second, copied[4:])):
            for (source_media, source_sidecar), (dest_media, dest_sidecar) in zip(
                fixture["finals"], destinations,
            ):
                self.assertNotEqual(source_media.stat().st_ino, dest_media.stat().st_ino)
                self.assertNotEqual(source_sidecar.stat().st_ino, dest_sidecar.stat().st_ino)
        receipts = sorted((
            self.project / ".maestro-recovery" / "final-adoption" / "receipts"
        ).glob("*.json"))
        self.assertEqual(len(receipts), 2)
        receipt_bytes = {path.name: path.read_bytes() for path in receipts}
        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 2)
        self.assertEqual(receipt_bytes, {path.name: path.read_bytes() for path in receipts})
        self.assertTrue(all(path.exists() for path in source_bytes))

    def test_preexisting_mixed_exact_and_different_group_fails_closed(self):
        fixture = self._concat_job("job-copy-mixed", 2)
        copied = self._copy_final_destinations(fixture)
        copied[1][0].write_bytes(b"different-final")
        before = {path: path.read_bytes() for pair in copied for path in pair}
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "destination collision",
        ):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
            )
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))

    def test_preexisting_one_sided_pair_fails_closed(self):
        fixture = self._concat_job("job-copy-one-sided", 1)
        source_media, source_sidecar = fixture["finals"][0]
        destination_media = self.project / source_media.name.split("-", 1)[1]
        shutil.copyfile(source_media, destination_media)
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "destination collision",
        ):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
            )
        self.assertTrue(source_media.exists())
        self.assertTrue(source_sidecar.exists())
        self.assertTrue(destination_media.exists())
        self.assertFalse(
            (self.project / source_sidecar.name.split("-", 1)[1]).exists()
        )

    def test_preexisting_pending_plan_replays_without_touching_either_copy(self):
        fixture = self._concat_job("job-copy-pending", 1)
        copied = self._copy_final_destinations(fixture)

        def fail(kind: str, _index: int) -> None:
            if kind == "preexisting_validated":
                raise RuntimeError("simulated receipt crash")

        with self.assertRaisesRegex(RuntimeError, "simulated receipt crash"):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
                _publish_hook=fail,
            )
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))
        self.assertTrue(all(path.exists() for pair in copied for path in pair))
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))
        self.assertTrue(all(path.exists() for pair in copied for path in pair))

    def test_pending_preexisting_without_quarantine_fails_closed(self):
        fixture = self._concat_job("job-copy-pending-purged", 1)
        self._copy_final_destinations(fixture)

        def fail(kind: str, _index: int) -> None:
            if kind == "preexisting_validated":
                raise RuntimeError("simulated receipt crash")

        with self.assertRaisesRegex(RuntimeError, "simulated receipt crash"):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
                _publish_hook=fail,
            )
        shutil.rmtree(self.quarantine)

        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "quarantine is unavailable for a pending plan",
        ):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
            )

    def test_committed_preexisting_missing_destination_reports_missing(self):
        fixture = self._concat_job("job-copy-committed-missing", 1)
        copied = self._copy_final_destinations(fixture)
        first = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(first["adopted_groups"], 1)
        copied[0][0].unlink()

        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(replay["adopted_groups"], 0)
        self.assertEqual(replay["missing_groups"], 1)
        self.assertEqual(replay["jobs"][0]["state"], "missing")
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))
        self.assertFalse(copied[0][0].exists())
        self.assertTrue(copied[0][1].exists())

    def test_committed_preexisting_replays_after_quarantine_purge(self):
        fixture = self._concat_job("job-copy-committed-purged", 1)
        copied = self._copy_final_destinations(fixture)
        first = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(first["adopted_groups"], 1)
        shutil.rmtree(self.quarantine)

        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(replay["adopted_groups"], 1)
        self.assertEqual(replay["jobs"][0]["state"], "adopted")
        self.assertTrue(all(path.exists() for pair in copied for path in pair))

    def test_committed_preexisting_missing_destination_after_purge_reports_missing(self):
        fixture = self._concat_job("job-copy-committed-purged-missing", 1)
        copied = self._copy_final_destinations(fixture)
        adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        copied[0][0].unlink()
        shutil.rmtree(self.quarantine)

        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(replay["adopted_groups"], 0)
        self.assertEqual(replay["missing_groups"], 1)
        self.assertEqual(replay["jobs"][0]["state"], "missing")
        self.assertFalse(copied[0][0].exists())
        self.assertTrue(copied[0][1].exists())

    def test_committed_preexisting_changed_or_symlink_destination_reports_missing(self):
        for mutation in ("different", "symlink"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    original_project = self.project
                    original_quarantine = self.quarantine
                    self.project = Path(temporary) / "project"
                    self.quarantine = (
                        self.project / ".maestro-recovery" / "quarantine"
                    )
                    self.quarantine.mkdir(parents=True, mode=0o700)
                    os.chmod(self.project / ".maestro-recovery", 0o700)
                    os.chmod(self.quarantine, 0o700)
                    try:
                        fixture = self._concat_job(f"job-copy-{mutation}", 1)
                        copied = self._copy_final_destinations(fixture)
                        adopt_quarantined_final_groups(
                            self.project,
                            workspace=self.workspace,
                        )
                        if mutation == "different":
                            copied[0][0].write_bytes(b"changed-public-final")
                        else:
                            outside = self.project / "outside.mp4"
                            outside.write_bytes(b"outside")
                            copied[0][0].unlink()
                            copied[0][0].symlink_to(outside)
                        replay = adopt_quarantined_final_groups(
                            self.project,
                            workspace=self.workspace,
                        )
                        self.assertEqual(replay["missing_groups"], 1)
                        self.assertEqual(replay["jobs"][0]["state"], "missing")
                        self.assertTrue(
                            all(path.exists() for pair in fixture["finals"] for path in pair)
                        )
                        if mutation == "different":
                            self.assertEqual(
                                copied[0][0].read_bytes(),
                                b"changed-public-final",
                            )
                        else:
                            self.assertTrue(copied[0][0].is_symlink())
                            self.assertEqual(outside.read_bytes(), b"outside")
                    finally:
                        self.project = original_project
                        self.quarantine = original_quarantine

    def test_incomplete_group_stays_quarantined(self):
        fixture = self._concat_job("job-incomplete", 4)
        for path in fixture["finals"][-1]:
            path.unlink()
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)
        self.assertEqual(summary["quarantined_groups"], 1)
        self.assertEqual(summary["jobs"][0]["missing"], 1)
        self.assertFalse(any(self.project.glob("job-incomplete-*-final.mp4")))

    def test_tampered_dependency_blocks_final_group(self):
        fixture = self._concat_job("job-dependency", 1)
        fixture["components"][2][0].write_bytes(b"tampered")
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)
        self.assertTrue(fixture["finals"][0][0].exists())

    def test_symlink_and_tampered_final_are_rejected(self):
        fixture = self._concat_job("job-tamper", 1)
        outside = self.project / "outside.mp4"
        outside.write_bytes(b"outside")
        fixture["finals"][0][0].unlink()
        fixture["finals"][0][0].symlink_to(outside)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)
        self.assertGreaterEqual(summary["rejected_artifacts"], 1)
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_collision_never_overwrites(self):
        fixture = self._concat_job("job-collision", 1)
        destination = self.project / "job-collision-v0-final.mp4"
        destination.write_bytes(b"keep-me")
        with self.assertRaises(QueueRecoveryRuntimeError):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
            )
        self.assertEqual(destination.read_bytes(), b"keep-me")
        self.assertTrue(fixture["finals"][0][0].exists())

    def test_late_collision_does_not_prevent_other_items_from_rolling_back(self):
        fixture = self._concat_job("job-late-collision", 4)
        collision = self.project / "job-late-collision-v3-final.meta.json"

        def collide(kind: str, index: int) -> None:
            if kind == "sidecar" and index == 1:
                collision.write_bytes(b"foreign-sidecar")
                raise RuntimeError("injected late collision")

        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "rollback retained conflicting evidence",
        ):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
                _publish_hook=collide,
            )
        self.assertEqual(collision.read_bytes(), b"foreign-sidecar")
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))
        self.assertFalse((self.project / "job-late-collision-v0-final.meta.json").exists())
        self.assertFalse((self.project / "job-late-collision-v1-final.meta.json").exists())

    def test_one_job_binding_rejects_a_second_complete_final_set(self):
        self._concat_job("job-one-binding", 1)
        first = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(first["adopted_groups"], 1)

        second_fixture = self._concat_job(
            "job-one-binding",
            1,
            tag="-replacement",
        )
        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(replay["declared_groups"], 1)
        self.assertEqual(replay["adopted_groups"], 1)
        self.assertEqual(len(replay["jobs"]), 1)
        self.assertTrue(all(path.exists() for pair in second_fixture["finals"] for path in pair))
        self.assertFalse(
            (self.project / "job-one-binding-replacement-v0-final.mp4").exists()
        )

    def test_mid_publish_failure_rolls_back_whole_group(self):
        fixture = self._concat_job("job-rollback", 4)

        def fail(kind: str, index: int) -> None:
            if kind == "sidecar" and index == 2:
                raise RuntimeError("injected crash")

        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
                _publish_hook=fail,
            )
        self.assertFalse(any(self.project.glob("job-rollback-*-final.mp4")))
        self.assertFalse(any(self.project.glob("job-rollback-*-final.meta.json")))
        self.assertTrue(all(path.exists() for pair in fixture["finals"] for path in pair))

        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)

    def test_restart_reconciles_pending_plan_after_abrupt_rollback_loss(self):
        self._concat_job("job-restart", 4)

        def fail(kind: str, index: int) -> None:
            if kind == "sidecar_linked" and index == 0:
                raise RuntimeError("simulated process loss")

        with mock.patch(
            "services.queue_recovery_final_adoption._rollback",
            side_effect=RuntimeError("rollback process also stopped"),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback process also stopped"):
                adopt_quarantined_final_groups(
                    self.project,
                    workspace=self.workspace,
                    _publish_hook=fail,
                )
        adoption = self.project / ".maestro-recovery" / "final-adoption"
        self.assertEqual(len(list((adoption / "plans").glob("*.json"))), 1)
        self.assertEqual(len(list((adoption / "receipts").glob("*.json"))), 0)
        source_sidecar = next(
            path for path in self.quarantine.glob("*-job-restart-v0-final.meta.json")
        )
        public_sidecar = self.project / "job-restart-v0-final.meta.json"
        source_info = source_sidecar.stat()
        public_info = public_sidecar.stat()
        self.assertEqual(
            (source_info.st_dev, source_info.st_ino),
            (public_info.st_dev, public_info.st_ino),
        )
        self.assertEqual(source_info.st_nlink, 2)
        self.assertEqual(public_info.st_nlink, 2)

        # A new process/startup sees the immutable pending plan first, restores
        # quarantine, and can then publish the exact same deterministic plan.
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertEqual(len(list((adoption / "plans").glob("*.json"))), 1)
        self.assertEqual(len(list((adoption / "receipts").glob("*.json"))), 1)

        # Generic journal/staging retirement cannot revoke an adoption receipt.
        staging = self.project / ".maestro-recovery" / "staging"
        staging.mkdir(mode=0o700)
        staging.rmdir()
        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 1)

    def test_concurrent_adopters_publish_exactly_once(self):
        self._concat_job("job-concurrent", 4)
        summaries = []
        failures = []

        def run() -> None:
            try:
                summaries.append(adopt_quarantined_final_groups(
                    self.project,
                    workspace=self.workspace,
                ))
            except BaseException as error:  # pragma: no cover - asserted below
                failures.append(error)

        threads = [threading.Thread(target=run) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(failures)
        self.assertEqual(len(summaries), 6)
        self.assertTrue(all(item["adopted_groups"] == 1 for item in summaries))
        self.assertEqual(len(list(self.project.glob("job-concurrent-*-final.mp4"))), 4)
        self.assertEqual(len(list(self.project.glob("job-concurrent-*-final.meta.json"))), 4)

    def test_delivery_group_is_adopted_only_as_one_exact_artifact_set(self):
        self._delivery_job("job-delivery", 3)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertEqual(summary["jobs"][0]["adopted"], 3)
        self.assertEqual(len(list(self.project.glob("job-delivery-delivery-*.mp4"))), 3)

    def test_private_directory_and_workspace_are_fail_closed(self):
        self._concat_job("job-private", 1)
        os.chmod(self.quarantine, 0o755)
        with self.assertRaises(QueueRecoveryRuntimeError):
            adopt_quarantined_final_groups(
                self.project,
                workspace=self.workspace,
            )
        os.chmod(self.quarantine, 0o700)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace="different-project",
        )
        self.assertEqual(summary["adopted_groups"], 0)

    def test_missing_published_file_is_reported_without_republication(self):
        self._concat_job("job-missing", 1)
        first = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(first["adopted_groups"], 1)
        (self.project / "job-missing-v0-final.mp4").unlink()
        replay = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 0)
        self.assertEqual(replay["missing_groups"], 1)
        self.assertEqual(replay["jobs"][0]["state"], "missing")


if __name__ == "__main__":
    unittest.main()
