from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from services.character_sheet_executor import (
    PANEL_ROLES,
    CharacterSheetExecutorError,
    build_quad_job_identity,
    build_quad_runtime_snapshot,
    create_quad_publication_proof,
    crop_quad_sheet,
    validate_quad_job_identity,
    verify_quad_publication_proof,
)
from services.character_sheet_quad import (
    QUAD_FLUX_BASE_MODEL,
    QUAD_FLUX_LORA_SHA256,
)
from services.character_sheet_anchor import (
    create_reference_pack_anchor_proof,
    resolve_character_sheet_anchor,
)
from services.host_terms import (
    BFL_FLUX2_REVIEW_TERM,
    CHARACTER_SHEET_QUAD_CREATOR_TERM,
)
from services.project_assets import ProjectAssetStore


KEY = b"character-sheet-executor-tests-key-2026"
PROJECT_ID = "project_01"
ASSET_ID = "asset_01"
JOB_ID = "sheet_job_01"
ANCHOR = {
    "schema_version": 3,
    "project_id": PROJECT_ID,
    "anchor_id": "anchor_output_01",
    "kind": "generated",
    "sha256": "a" * 64,
    "source_model_id": "flux2_klein_9b",
    "source_model_family": "flux",
}
TERM_STATUSES = [
    {"term": term, "version": 1, "accepted": True}
    for term in (CHARACTER_SHEET_QUAD_CREATOR_TERM, BFL_FLUX2_REVIEW_TERM)
]
SCHEDULE = {"model": QUAD_FLUX_BASE_MODEL, "steps": 28, "guidance": 4.0}


def runtime_snapshot(**overrides):
    values = {
        "model_id": QUAD_FLUX_BASE_MODEL,
        "model_family": "flux",
        "model_artifact_commitment": "b" * 64,
        "lora_sha256": QUAD_FLUX_LORA_SHA256,
        "term_statuses": [dict(item) for item in TERM_STATUSES],
        "schedule": dict(SCHEDULE),
    }
    values.update(overrides)
    return build_quad_runtime_snapshot(**values)


def job_identity(**overrides):
    values = {
        "project_id": PROJECT_ID,
        "workspace_id": "main",
        "job_id": JOB_ID,
        "asset_id": ASSET_ID,
        "anchor": dict(ANCHOR),
        "anchor_variant_id": "anchor_pack_01",
        "resources": runtime_snapshot(),
        "seed": 42,
    }
    values.update(overrides)
    return build_quad_job_identity(KEY, **values)


class CharacterSheetExecutorTests(unittest.TestCase):
    def test_runtime_snapshot_binds_native_model_lora_terms_and_schedule(self):
        snapshot = runtime_snapshot()
        self.assertEqual(snapshot["base_model_id"], QUAD_FLUX_BASE_MODEL)
        self.assertEqual(snapshot["lora"]["sha256"], QUAD_FLUX_LORA_SHA256)
        self.assertEqual(
            [item["term"] for item in snapshot["terms"]["statuses"]],
            [CHARACTER_SHEET_QUAD_CREATOR_TERM, BFL_FLUX2_REVIEW_TERM],
        )
        self.assertEqual(snapshot["schedule"]["steps"], 28)

    def test_runtime_snapshot_rejects_changed_model_lora_terms_and_schedule(self):
        for overrides in (
            {"model_id": "not_a_flux_model"},
            {"model_family": "sdxl"},
            {"lora_sha256": "c" * 64},
            {"term_statuses": [dict(TERM_STATUSES[1]), dict(TERM_STATUSES[0])]},
            {"term_statuses": [
                {**TERM_STATUSES[0], "accepted": False}, TERM_STATUSES[1],
            ]},
            {"schedule": {**SCHEDULE, "steps": True}},
            {"schedule": {**SCHEDULE, "guidance": float("nan")}},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(
                CharacterSheetExecutorError,
            ):
                runtime_snapshot(**overrides)

    def test_identity_binds_scope_anchor_role_order_and_resource_snapshot(self):
        identity = job_identity()
        self.assertEqual(identity["role_outputs"][0]["role"], "face_closeup")
        self.assertEqual(
            [item["role"] for item in identity["role_outputs"]], list(PANEL_ROLES),
        )
        self.assertEqual(identity["variant_id"], f"{JOB_ID}_pack_1")
        self.assertEqual(identity["generation_job_id"], f"{JOB_ID}_quad")
        self.assertEqual(
            validate_quad_job_identity(
                KEY, identity, expected_project_id=PROJECT_ID,
                expected_asset_id=ASSET_ID, expected_job_id=JOB_ID,
                current_resources=runtime_snapshot(),
            )["seed"],
            42,
        )
        changed = {**identity, "role_outputs": [dict(item) for item in identity["role_outputs"]]}
        changed["role_outputs"][0]["role"] = "front_full_body"
        with self.assertRaises(CharacterSheetExecutorError):
            validate_quad_job_identity(KEY, changed)
        with self.assertRaises(CharacterSheetExecutorError):
            validate_quad_job_identity(
                KEY, identity, expected_project_id="another_project",
            )
        with self.assertRaises(CharacterSheetExecutorError):
            validate_quad_job_identity(
                KEY, identity, current_resources=runtime_snapshot(
                    model_artifact_commitment="d" * 64,
                ),
            )
        with self.assertRaises(CharacterSheetExecutorError):
            job_identity(workspace_id="secondary")
        with self.assertRaises(CharacterSheetExecutorError):
            job_identity(anchor={**ANCHOR, "project_id": "another_project"})

    def test_published_panel_hashes_are_sealed_in_fixed_role_order(self):
        identity = job_identity()
        panel_digests = [
            {"role": role, "output_index": index, "sha256": str(index + 1) * 64}
            for index, role in enumerate(PANEL_ROLES)
        ]
        proof = create_quad_publication_proof(
            KEY, identity=identity, panel_digests=panel_digests,
        )
        self.assertTrue(verify_quad_publication_proof(
            KEY, proof, identity=identity, panel_digests=panel_digests,
        ))
        changed = [dict(item) for item in panel_digests]
        changed[2]["sha256"] = "f" * 64
        self.assertFalse(verify_quad_publication_proof(
            KEY, proof, identity=identity, panel_digests=changed,
        ))
        changed_order = [dict(item) for item in panel_digests]
        changed_order[0], changed_order[1] = changed_order[1], changed_order[0]
        with self.assertRaises(CharacterSheetExecutorError):
            create_quad_publication_proof(
                KEY, identity=identity, panel_digests=changed_order,
            )

    def test_crop_quad_sheet_emits_four_exact_role_images_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
            image = Image.new("RGB", (1024, 1024))
            for color, (x, y) in zip(colors, ((0, 0), (512, 0), (0, 512), (512, 512)), strict=True):
                image.paste(color, (x, y, x + 512, y + 512))
            source = root / "quad.png"
            image.save(source)
            outputs = crop_quad_sheet(KEY, source, root, identity=job_identity())
            self.assertEqual([item["role"] for item in outputs], list(PANEL_ROLES))
            for output, expected_color in zip(outputs, colors, strict=True):
                path = Path(output["path"])
                with Image.open(path) as panel:
                    self.assertEqual(panel.size, (512, 512))
                    self.assertEqual(panel.getpixel((10, 10)), expected_color)
                self.assertEqual(
                    output["sha256"], hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_crop_quad_sheet_rejects_symlink_and_wrong_canvas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.png"
            Image.new("RGB", (1024, 1024), "white").save(outside)
            source_link = root / "linked.png"
            source_link.symlink_to(outside)
            with self.assertRaises(CharacterSheetExecutorError):
                crop_quad_sheet(KEY, source_link, root, identity=job_identity())

            wrong_size = root / "wrong.png"
            Image.new("RGB", (512, 512), "white").save(wrong_size)
            with self.assertRaises(CharacterSheetExecutorError):
                crop_quad_sheet(KEY, wrong_size, root, identity=job_identity())

    def test_atomic_project_asset_publication_preserves_four_anchor_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "outputs"
            source_root.mkdir()
            image = Image.new("RGB", (1024, 1024), "white")
            source = source_root / "quad.png"
            image.save(source)
            identity = job_identity()
            panels = crop_quad_sheet(KEY, source, source_root, identity=identity)
            panel_digests = [
                {"role": item["role"], "output_index": item["output_index"], "sha256": item["sha256"]}
                for item in panels
            ]
            publication_proof = create_quad_publication_proof(
                KEY, identity=identity, panel_digests=panel_digests,
            )
            anchor_key = b"character-sheet-anchor-tests-key-2026"
            outputs = []
            for panel in panels:
                path = Path(panel["path"])
                file_stat = os.stat(path)
                anchor_proof = create_reference_pack_anchor_proof(
                    anchor_key,
                    project_id=PROJECT_ID,
                    workspace_id="main",
                    asset_id=ASSET_ID,
                    variant_id=identity["variant_id"],
                    output_index=panel["output_index"],
                    basename=panel["basename"],
                    source_model_id=QUAD_FLUX_BASE_MODEL,
                    source_sha256=panel["sha256"],
                    job_id=JOB_ID,
                )
                outputs.append({
                    "source_path": str(path),
                    "label": panel["role"],
                    "expected_source_identity": {
                        "device": file_stat.st_dev,
                        "inode": file_stat.st_ino,
                        "size": file_stat.st_size,
                        "sha256": panel["sha256"],
                    },
                    "metadata": {
                        "private": True,
                        "explicit": False,
                        "reference_pack": {
                            "model": QUAD_FLUX_BASE_MODEL,
                            "generation_model": QUAD_FLUX_BASE_MODEL,
                            "role": panel["role"],
                            "output_index": panel["output_index"],
                            "anchor_provenance": anchor_proof,
                        },
                        "lineage": {"parent_job_id": JOB_ID},
                    },
                })
            store = ProjectAssetStore(
                root / "asset-store", allowed_source_roots=[source_root],
            )
            store.create_asset(
                PROJECT_ID,
                "main",
                asset_id=ASSET_ID,
                name="Example character",
                asset_type="character",
                provenance="typed",
            )
            store.add_variants_atomic(
                PROJECT_ID,
                "main",
                ASSET_ID,
                [{
                    "id": identity["variant_id"],
                    "variant_type": "reference_pack",
                    "label": "Quad character sheet",
                    "outputs": outputs,
                    "provenance": {
                        "kind": "generated",
                        "details": {"service": "reference_sheets", "job_id": JOB_ID},
                    },
                    "status": "candidate",
                    "metadata": {
                        "reference_pack": {
                            "generation_model": QUAD_FLUX_BASE_MODEL,
                            "identity_seal": identity["identity_seal"],
                            "publication_proof": publication_proof,
                        },
                        "job": {
                            "id": JOB_ID,
                            "generation_model": QUAD_FLUX_BASE_MODEL,
                        },
                    },
                }],
            )
            store.keep_variant(PROJECT_ID, "main", ASSET_ID, identity["variant_id"])
            asset = store.get_asset(PROJECT_ID, "main", ASSET_ID)
            variant = asset["variants"][0]
            resolved_hashes = []
            for index, output in enumerate(variant["outputs"]):
                resolved = resolve_character_sheet_anchor(
                    store,
                    anchor_key,
                    project_id=PROJECT_ID,
                    workspace_id="main",
                    asset_id=ASSET_ID,
                    variant_id=identity["variant_id"],
                    output_id=output["id"],
                    is_verified_flux_model=lambda model_id: model_id == QUAD_FLUX_BASE_MODEL,
                )
                resolved_hashes.append({
                    "role": PANEL_ROLES[index],
                    "output_index": index,
                    "sha256": resolved["anchor"]["sha256"],
                })
                self.assertEqual(resolved["anchor"]["source_model_family"], "flux")
            self.assertTrue(verify_quad_publication_proof(
                KEY,
                publication_proof,
                identity=identity,
                panel_digests=resolved_hashes,
            ))


if __name__ == "__main__":
    unittest.main()
