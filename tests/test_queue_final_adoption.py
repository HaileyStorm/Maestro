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
    stage_quarantined_h3_segment_for_final_adoption,
    validate_h3_segment_staging_name_budget,
    _h3_staging_names,
)
from services.queue_recovery_runtime import (
    QueueRecoveryRuntimeError,
    artifact_descriptor,
    recovery_unit_id,
)
from services.h3_audio_safety import DEFAULT_TARGET_DBTP, POLICY_VERSION


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

    def _single_source_prefix_job(
        self, job_id: str, *, concat_prefix: dict | None = None,
    ) -> dict:
        prefix = {
            "version": 1,
            "input_field": "video_source:0",
            "source_native_frames": 125,
            "retained_frames": 120,
            "output_fps": 24,
            "fit": "contain",
            "conditioning": "last_frame",
            "audio_policy": "preserve_source_then_generated",
            "sha256": hashlib.sha256(b"source").hexdigest(),
            "size": 6,
        }
        component_name = f"{job_id}-generated.mp4"
        component_data = b"generated-segment"
        segment_settings = {
            "discard_prefix_frames": 0,
            "trim_tail_frames": 0,
            "source_prefix": prefix,
        }
        segment = self._base_meta(
            job_id=job_id, kind="h3_segment", variant=0, index=0,
            output_index=0, output_total=1, dependencies=[],
            settings=segment_settings, artifacts=[component_name],
        )
        segment["private"] = False
        self._quarantined_pair(component_name, component_data, segment)
        final_name = f"{job_id}-final.mp4"
        final = self._base_meta(
            job_id=job_id, kind="h3_concat", variant=0, index=0,
            output_index=0, output_total=1,
            dependencies=[segment["producer_unit_id"]],
            settings={
                "clip_start_frames": [0],
                "clip_tail_frames": [0],
                "component_hashes": [
                    hashlib.sha256(component_data).hexdigest(),
                ],
                "source_prefix": (
                    prefix if concat_prefix is None else concat_prefix
                ),
            },
            artifacts=[final_name],
        )
        final["private"] = False
        self._quarantined_pair(final_name, b"source-plus-generated", final)
        return {"component": component_name, "final": final_name}

    def _h3_final_adoption_fixture(
        self,
        *,
        job_id: str = "job-stage-h3",
        final_payload: bytes = b"sealed-final-media",
        quarantined_final_payload: bytes | None = None,
        segment_name: str | None = None,
    ) -> dict:
        prefix = {
            "version": 1,
            "input_field": "video_source:0",
            "source_native_frames": 125,
            "retained_frames": 120,
            "output_fps": 24,
            "fit": "contain",
            "conditioning": "last_frame",
            "audio_policy": "preserve_source_then_generated",
            "sha256": hashlib.sha256(b"source").hexdigest(),
            "size": 6,
        }
        job = {
            "id": job_id,
            "workspace": self.workspace,
            "private": True,
            "explicit": False,
            "access_policy": {"private": True, "explicit": False},
        }
        segment_name = segment_name or f"{job_id}-generated.mp4"
        segment_payload = b"source-prefix-generated-segment"
        segment_settings = {
            "discard_prefix_frames": 0,
            "trim_tail_frames": 0,
            "source_prefix": prefix,
        }
        segment_meta = self._base_meta(
            job_id=job_id,
            kind="h3_segment",
            variant=0,
            index=0,
            output_index=0,
            output_total=1,
            dependencies=[],
            settings=segment_settings,
            artifacts=[segment_name],
        )
        segment_meta["explicit"] = False
        segment_media = self.project / segment_name
        segment_sidecar = self.project / f"{Path(segment_name).stem}.meta.json"
        segment_media.write_bytes(segment_payload)
        segment_meta.update({
            "output_filename": segment_name,
            "producer_media_sha256": hashlib.sha256(segment_payload).hexdigest(),
            "producer_media_size": len(segment_payload),
        })
        segment_sidecar.write_text(
            json.dumps(segment_meta, sort_keys=True), encoding="utf-8",
        )
        segment_descriptor = artifact_descriptor(
            self.project,
            basename=segment_name,
            sidecar_basename=segment_sidecar.name,
            producer_unit_id=segment_meta["producer_unit_id"],
        )
        segment_unit = {
            "artifacts": [segment_descriptor],
            "dependencies": [],
            "index": 0,
            "kind": "h3_segment",
            "settings": segment_settings,
            "state": "completed",
            "unit_id": segment_meta["producer_unit_id"],
            "variant": 0,
        }

        final_settings = {
            "clip_start_frames": [0],
            "clip_tail_frames": [0],
            "component_hashes": [hashlib.sha256(segment_payload).hexdigest()],
            "h3_audio_true_peak_policy": {
                "policy_version": POLICY_VERSION,
                "target_dbtp": DEFAULT_TARGET_DBTP,
            },
            "source_prefix": prefix,
        }
        final_name = f"{job_id}-final.mp4"
        final_meta = self._base_meta(
            job_id=job_id,
            kind="h3_concat",
            variant=0,
            index=0,
            output_index=0,
            output_total=1,
            dependencies=[segment_unit["unit_id"]],
            settings=final_settings,
            artifacts=[final_name],
        )
        final_meta["explicit"] = False
        final_sidecar = self.project / f"{Path(final_name).stem}.meta.json"
        final_media = self.project / final_name
        final_media.write_bytes(final_payload)
        final_meta.update({
            "output_filename": final_name,
            "producer_media_sha256": hashlib.sha256(final_payload).hexdigest(),
            "producer_media_size": len(final_payload),
        })
        final_sidecar.write_text(json.dumps(final_meta, sort_keys=True), encoding="utf-8")
        final_descriptor = artifact_descriptor(
            self.project,
            basename=final_name,
            sidecar_basename=final_sidecar.name,
            producer_unit_id=final_meta["producer_unit_id"],
        )
        final_unit = {
            "artifacts": [final_descriptor],
            "attestation": {
                "h3_audio_true_peak": {
                    "policy_version": POLICY_VERSION,
                    "target_dbtp": DEFAULT_TARGET_DBTP,
                    "verified": True,
                },
            },
            "dependencies": [segment_unit["unit_id"]],
            "index": 0,
            "kind": "h3_concat",
            "settings": final_settings,
            "state": "completed",
            "unit_id": final_meta["producer_unit_id"],
            "variant": 0,
        }
        final_media.unlink()
        final_sidecar.unlink()
        quarantined_meta = dict(final_meta)
        quarantined_meta["later_re_attestation_marker"] = "updated"
        quarantined = self._quarantined_pair(
            final_name,
            final_payload if quarantined_final_payload is None
            else quarantined_final_payload,
            quarantined_meta,
        )
        return {
            "final_descriptor": final_descriptor,
            "final_meta": final_meta,
            "final_unit": final_unit,
            "job": job,
            "job_id": job_id,
            "quarantined_final": quarantined,
            "segment_descriptor": segment_descriptor,
            "segment_meta": segment_meta,
            "segment_name": segment_name,
            "segment_payload": segment_payload,
            "segment_unit": segment_unit,
        }

    def _stage_fixture(self, fixture: dict) -> dict:
        return stage_quarantined_h3_segment_for_final_adoption(
            self.project,
            workspace=self.workspace,
            job=fixture["job"],
            segment_unit=fixture["segment_unit"],
            final_unit=fixture["final_unit"],
        )

    def _delivery_job(
        self, job_id: str, total: int, *, wrong_native_hash: bool = False,
        reversed_native_hashes: bool = False,
        parent_kind: str = "h3_concat", missing_native_hashes: bool = False,
        multiartifact_parent: bool = False, duplicate_native_hash: bool = False,
    ) -> dict:
        dependencies = []
        components = []
        native_hashes = []
        multiartifact_outputs = []
        for segment in range(total):
            output = f"{job_id}-component-{segment}.mp4"
            settings = {
                "discard_prefix_frames": 0,
                "segment": segment,
                "trim_tail_frames": 0,
            }
            meta = self._base_meta(
                job_id=job_id,
                kind="h3_segment",
                variant=segment,
                index=0,
                output_index=segment,
                output_total=total,
                dependencies=[],
                settings=settings,
                artifacts=[output],
            )
            continuation_sha = hashlib.sha256(
                f"continuation:{job_id}:{segment}".encode()
            ).hexdigest()
            meta["producer_unit_continuation"] = {
                "basename": f"{job_id}-s{segment}-continuation.png",
                "dependency": meta["producer_unit_id"],
                "mode": "last_frame",
                "sha256": continuation_sha,
                "size": 123,
                "storage": "recovery_staging",
            }
            components.append(self._quarantined_pair(output, output.encode(), meta))
            component_hash = hashlib.sha256(output.encode()).hexdigest()
            segment_unit_id = meta["producer_unit_id"]
            if parent_kind == "h3_segment":
                dependencies.append(segment_unit_id)
                native_hashes.append(component_hash)
                continue
            native_output = f"{job_id}-native-{segment}.mp4"
            if multiartifact_parent:
                multiartifact_outputs.append(native_output)
                native_hashes.append(hashlib.sha256(native_output.encode()).hexdigest())
                continue
            parent_dependencies = (
                [segment_unit_id] if parent_kind == "h3_concat" else []
            )
            parent_settings = (
                {
                    "clip_start_frames": [0],
                    "clip_tail_frames": [0],
                    "component_hashes": [component_hash],
                }
                if parent_kind == "h3_concat" else {}
            )
            parent_meta = self._base_meta(
                job_id=job_id,
                kind=parent_kind,
                variant=segment if parent_kind == "h3_concat" else 0,
                index=0 if parent_kind == "h3_concat" else segment,
                output_index=segment,
                output_total=total,
                dependencies=parent_dependencies,
                settings=parent_settings,
                artifacts=[native_output],
            )
            components.append(self._quarantined_pair(
                native_output, native_output.encode(), parent_meta,
            ))
            dependencies.append(parent_meta["producer_unit_id"])
            native_hashes.append(hashlib.sha256(native_output.encode()).hexdigest())
        if multiartifact_parent:
            for output_index, native_output in enumerate(multiartifact_outputs):
                parent_meta = self._base_meta(
                    job_id=job_id,
                    kind="ordinary_repeat",
                    variant=0,
                    index=0,
                    output_index=output_index,
                    output_total=total,
                    dependencies=[],
                    settings={},
                    artifacts=multiartifact_outputs,
                )
                components.append(self._quarantined_pair(
                    native_output, native_output.encode(), parent_meta,
                ))
                dependencies.append(parent_meta["producer_unit_id"])
        names = [f"{job_id}-delivery-{index}.mp4" for index in range(total)]
        if wrong_native_hash:
            native_hashes[-1] = hashlib.sha256(b"different native").hexdigest()
        if reversed_native_hashes:
            native_hashes.reverse()
        if duplicate_native_hash:
            native_hashes[-1] = native_hashes[0]
        settings = {"delivery": True}
        if not missing_native_hashes:
            settings["native_hashes"] = native_hashes
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

    def test_stages_sealed_h3_segment_as_an_idempotent_independent_pair(self):
        fixture = self._h3_final_adoption_fixture()
        result = self._stage_fixture(fixture)

        staged_media = self.quarantine / result["segment_media_basename"]
        staged_sidecar = self.quarantine / result["segment_sidecar_basename"]
        source_media = self.project / fixture["segment_name"]
        source_sidecar = self.project / fixture["segment_descriptor"]["sidecar_basename"]
        self.assertFalse(result["idempotent"])
        self.assertEqual(staged_media.read_bytes(), fixture["segment_payload"])
        self.assertEqual(staged_sidecar.read_bytes(), source_sidecar.read_bytes())
        for staged, source in ((staged_media, source_media), (staged_sidecar, source_sidecar)):
            info = os.lstat(staged)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_mode & 0o777, 0o600)
            self.assertNotEqual(
                (info.st_dev, info.st_ino),
                (os.stat(source).st_dev, os.stat(source).st_ino),
            )
        actual_sidecar_sha = hashlib.sha256(
            fixture["quarantined_final"][1].read_bytes()
        ).hexdigest()
        self.assertEqual(result["final_sidecar_sha256"], actual_sidecar_sha)
        self.assertFalse(result["final_sidecar_seal_match"])
        self.assertEqual(
            result["final_media_sha256"], fixture["final_descriptor"]["sha256"],
        )

        repeated = self._stage_fixture(fixture)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(
            repeated["segment_media_basename"], result["segment_media_basename"],
        )
        self.assertEqual(
            repeated["segment_sidecar_basename"], result["segment_sidecar_basename"],
        )

    def test_rejects_quarantined_final_with_wrong_media_digest(self):
        fixture = self._h3_final_adoption_fixture(
            quarantined_final_payload=b"not-the-sealed-final",
        )
        with self.assertRaises(QueueRecoveryRuntimeError):
            self._stage_fixture(fixture)
        staged_media, staged_sidecar = _h3_staging_names(
            self.workspace, fixture["job_id"], fixture["segment_unit"],
        )
        self.assertFalse((self.quarantine / staged_media).exists())
        self.assertFalse((self.quarantine / staged_sidecar).exists())

    def test_rejects_quarantined_final_with_wrong_producer_graph(self):
        fixture = self._h3_final_adoption_fixture()
        final_sidecar = fixture["quarantined_final"][1]
        meta = json.loads(final_sidecar.read_text(encoding="utf-8"))
        dependencies = ["unit:v1:" + "f" * 64]
        settings = dict(meta["producer_unit_settings"])
        settings["component_hashes"] = ["0" * 64]
        meta["producer_unit_dependencies"] = dependencies
        meta["producer_unit_settings"] = settings
        meta["producer_unit_id"] = recovery_unit_id(
            fixture["job_id"], "h3_concat", variant=0, index=0,
            dependencies=dependencies, settings=settings,
        )
        final_sidecar.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")

        with self.assertRaises(QueueRecoveryRuntimeError):
            self._stage_fixture(fixture)
        staged_media, staged_sidecar = _h3_staging_names(
            self.workspace, fixture["job_id"], fixture["segment_unit"],
        )
        self.assertFalse((self.quarantine / staged_media).exists())
        self.assertFalse((self.quarantine / staged_sidecar).exists())

    def test_rejects_changed_root_segment_before_staging(self):
        fixture = self._h3_final_adoption_fixture()
        (self.project / fixture["segment_name"]).write_bytes(b"changed-segment")
        with self.assertRaises(QueueRecoveryRuntimeError):
            self._stage_fixture(fixture)
        staged_media, staged_sidecar = _h3_staging_names(
            self.workspace, fixture["job_id"], fixture["segment_unit"],
        )
        self.assertFalse((self.quarantine / staged_media).exists())
        self.assertFalse((self.quarantine / staged_sidecar).exists())

    def test_long_segment_name_is_rejected_by_read_only_preflight(self):
        fixture = self._h3_final_adoption_fixture(
            job_id="job-stage-long-name",
            segment_name=("s" * 230) + ".mp4",
        )
        staged_media, staged_sidecar = _h3_staging_names(
            self.workspace, fixture["job_id"], fixture["segment_unit"],
        )
        name_max = (
            int(os.pathconf(self.quarantine, "PC_NAME_MAX"))
            if hasattr(os, "pathconf") else 255
        )
        self.assertGreater(
            max(len(os.fsencode(staged_media)), len(os.fsencode(staged_sidecar))),
            name_max,
        )
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "H3 segment names exceed the quarantine filesystem limit",
        ):
            validate_h3_segment_staging_name_budget(
                self.project,
                workspace=self.workspace,
                job=fixture["job"],
                segment_unit=fixture["segment_unit"],
            )
        with self.assertRaisesRegex(
            QueueRecoveryRuntimeError,
            "H3 segment names exceed the quarantine filesystem limit",
        ):
            self._stage_fixture(fixture)
        quarantine_names = {path.name for path in self.quarantine.iterdir()}
        self.assertNotIn(staged_media, quarantine_names)
        self.assertNotIn(staged_sidecar, quarantine_names)

    def test_collision_partial_publish_and_crash_resume_are_safe(self):
        resume = self._h3_final_adoption_fixture(job_id="job-stage-resume")
        resume_media, resume_sidecar = _h3_staging_names(
            self.workspace, resume["job_id"], resume["segment_unit"],
        )
        partial_media = self.quarantine / resume_media
        partial_media.write_bytes(resume["segment_payload"])
        os.chmod(partial_media, 0o600)
        resumed = self._stage_fixture(resume)
        self.assertFalse(resumed["idempotent"])
        self.assertEqual((self.quarantine / resume_sidecar).read_bytes(), (
            self.project / resume["segment_descriptor"]["sidecar_basename"]
        ).read_bytes())

        collision = self._h3_final_adoption_fixture(job_id="job-stage-collision")
        collision_media, collision_sidecar = _h3_staging_names(
            self.workspace, collision["job_id"], collision["segment_unit"],
        )
        occupied = self.quarantine / collision_media
        occupied.write_bytes(b"foreign collision")
        os.chmod(occupied, 0o600)
        with self.assertRaises(QueueRecoveryRuntimeError):
            self._stage_fixture(collision)
        self.assertEqual(occupied.read_bytes(), b"foreign collision")
        self.assertFalse((self.quarantine / collision_sidecar).exists())

        partial = self._h3_final_adoption_fixture(job_id="job-stage-partial")
        partial_media, partial_sidecar = _h3_staging_names(
            self.workspace, partial["job_id"], partial["segment_unit"],
        )
        real_link = os.link
        link_calls = 0

        def fail_second_link(source, destination, *args, **kwargs):
            nonlocal link_calls
            link_calls += 1
            if link_calls == 2:
                raise OSError("synthetic partial publish")
            return real_link(source, destination, *args, **kwargs)

        with mock.patch(
            "services.queue_recovery_final_adoption.os.link",
            side_effect=fail_second_link,
        ):
            with self.assertRaises(QueueRecoveryRuntimeError):
                self._stage_fixture(partial)
        self.assertEqual(link_calls, 2)
        self.assertFalse((self.quarantine / partial_media).exists())
        self.assertFalse((self.quarantine / partial_sidecar).exists())
        self.assertEqual(list(self.quarantine.glob(".*.tmp")), [])

    def test_adopts_complete_ordinary_repeat_without_h3_concat_fields(self):
        output = "unit-ordinary-final-t0-r0-w1.mp4"
        payload = b"complete-ordinary-final"
        unit_id = recovery_unit_id(
            "job-ordinary",
            "ordinary_repeat",
            variant=0,
            index=0,
            dependencies=[],
            settings={},
        )
        meta = {
            "artifact_class": "final",
            "job_id": "job-ordinary",
            "params": {},
            "private": False,
            "producer_artifact_class": "final",
            "producer_unit_artifact_names": [output],
            "producer_unit_dependencies": None,
            "producer_unit_id": unit_id,
            "producer_unit_index": 0,
            "producer_unit_kind": "ordinary_repeat",
            "producer_unit_settings": None,
            "producer_unit_variant": 0,
            "workspace": self.workspace,
        }
        media, sidecar = self._quarantined_pair(output, payload, meta)

        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(summary["declared_groups"], 1)
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertEqual(summary["missing_groups"], 0)
        self.assertTrue((self.project / output).is_file())
        self.assertEqual((self.project / output).read_bytes(), payload)
        self.assertTrue((self.project / f"{Path(output).stem}.meta.json").is_file())
        self.assertEqual(media.name.split("-", 1)[1], output)

    def test_adopts_complete_public_h3_concat(self):
        fixture = self._concat_job("job-public-h3", 1)
        for _media, sidecar in fixture["components"] + fixture["finals"]:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            meta["private"] = False
            sidecar.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")

        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )

        self.assertEqual(summary["declared_groups"], 1)
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertEqual(summary["quarantined_groups"], 0)
        self.assertTrue((self.project / "job-public-h3-v0-final.mp4").is_file())

    def test_adopts_one_generated_segment_only_with_bound_source_prefix(self):
        good = self._single_source_prefix_job("job-source-one")
        summary = adopt_quarantined_final_groups(
            self.project, workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)
        self.assertTrue((self.project / good["final"]).is_file())
        self.assertTrue((self.project / good["component"]).is_file())

    def test_rejects_one_segment_when_concat_prefix_differs(self):
        self._single_source_prefix_job(
            "job-source-mismatch",
            concat_prefix={
                "version": 1, "input_field": "video_source:0",
                "source_native_frames": 125, "retained_frames": 120,
                "output_fps": 24, "fit": "contain",
                "conditioning": "last_frame",
                "audio_policy": "preserve_source_then_generated",
                "sha256": "0" * 64, "size": 6,
            },
        )
        summary = adopt_quarantined_final_groups(
            self.project, workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)
        self.assertFalse(
            (self.project / "job-source-mismatch-final.mp4").exists()
        )

    def test_adopts_exact_four_plus_one_with_attested_components(self):
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
        self.assertEqual(len(list(self.quarantine.glob("*"))), 8)
        for pair in first["components"] + second["components"]:
            output = pair[0].name.split("-", 1)[1]
            self.assertTrue((self.project / output).is_file())
            self.assertTrue((self.project / f"{Path(output).stem}.meta.json").is_file())
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

    def test_adopted_final_receipt_restores_late_quarantined_components(self):
        fixture = self._concat_job("job-late-clips", 1)
        first = adopt_quarantined_final_groups(
            self.project, workspace=self.workspace,
        )
        self.assertEqual(first["adopted_groups"], 1)
        for media, sidecar in fixture["components"]:
            for source in (media, sidecar):
                destination = self.project / source.name.split("-", 1)[1]
                os.replace(destination, source)
        shutil.rmtree(self.project / ".maestro-recovery" / "component-adoption")

        replay = adopt_quarantined_final_groups(
            self.project, workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 1)
        for media, sidecar in fixture["components"]:
            for source in (media, sidecar):
                self.assertFalse(source.exists())
                self.assertTrue((self.project / source.name.split("-", 1)[1]).is_file())

    def test_component_publication_crash_rolls_back_and_retries(self):
        fixture = self._concat_job("job-clip-crash", 1)
        adopt_quarantined_final_groups(self.project, workspace=self.workspace)
        for media, sidecar in fixture["components"]:
            for source in (media, sidecar):
                os.replace(self.project / source.name.split("-", 1)[1], source)
        shutil.rmtree(self.project / ".maestro-recovery" / "component-adoption")
        from services import queue_recovery_final_adoption as module
        publish = module._publish_one
        interrupted = False

        def crash_once(source, destination, **kwargs):
            nonlocal interrupted
            if source.name.endswith("-s0.mp4") and not interrupted:
                interrupted = True
                raise RuntimeError("simulated clip publication crash")
            return publish(source, destination, **kwargs)

        with mock.patch.object(module, "_publish_one", side_effect=crash_once):
            with self.assertRaisesRegex(RuntimeError, "simulated clip publication crash"):
                adopt_quarantined_final_groups(
                    self.project, workspace=self.workspace,
                )
        for media, sidecar in fixture["components"]:
            self.assertTrue(media.exists())
            self.assertTrue(sidecar.exists())
        replay = adopt_quarantined_final_groups(
            self.project, workspace=self.workspace,
        )
        self.assertEqual(replay["adopted_groups"], 1)
        for media, sidecar in fixture["components"]:
            self.assertTrue((self.project / media.name.split("-", 1)[1]).is_file())
            self.assertTrue((self.project / sidecar.name.split("-", 1)[1]).is_file())

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

    def test_delivery_group_rejects_native_hash_not_sealed_by_parent(self):
        self._delivery_job("job-delivery-parent-mismatch", 2, wrong_native_hash=True)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)
        self.assertFalse(list(self.project.glob("job-delivery-parent-mismatch-delivery-*.mp4")))

    def test_delivery_group_rejects_reordered_native_hashes(self):
        self._delivery_job("job-delivery-parent-order", 2, reversed_native_hashes=True)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)

    def test_delivery_group_rejects_component_parent(self):
        self._delivery_job("job-delivery-component", 2, parent_kind="h3_segment")
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)

    def test_delivery_group_accepts_ordinary_final_parent(self):
        self._delivery_job("job-delivery-ordinary", 2, parent_kind="ordinary_repeat")
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)

    def test_delivery_group_requires_native_hashes(self):
        self._delivery_job("job-delivery-unbound", 2, missing_native_hashes=True)
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)

    def test_delivery_group_consumes_each_multiartifact_parent_hash_once(self):
        self._delivery_job(
            "job-delivery-multi-parent", 2,
            parent_kind="ordinary_repeat", multiartifact_parent=True,
        )
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 1)

    def test_delivery_group_rejects_reused_multiartifact_parent_hash(self):
        self._delivery_job(
            "job-delivery-reused-parent", 2,
            parent_kind="ordinary_repeat", multiartifact_parent=True,
            duplicate_native_hash=True,
        )
        summary = adopt_quarantined_final_groups(
            self.project,
            workspace=self.workspace,
        )
        self.assertEqual(summary["adopted_groups"], 0)

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
