"""Focused trust-boundary tests for Character Sheet reference anchors."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services.character_sheet_anchor import (  # noqa: E402
    CharacterSheetAnchorError,
    create_reference_pack_anchor_proof,
    resolve_character_sheet_anchor,
)
from services.project_assets import ProjectAssetStore  # noqa: E402


_KEY = b"test-only-character-sheet-anchor-key-32-bytes"
_FLUX_MODELS = {"flux2_klein_4b", "flux2_klein_9b"}


class CharacterSheetAnchorResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.storage = self.base / "library"
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.store = ProjectAssetStore(self.storage, [self.sources])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _publish(
        self,
        *,
        project_id="film_1",
        proof_project_id=None,
        status="kept",
        model_id="flux2_klein_9b",
        variant_provenance_kind="generated",
        variant_metadata_updates=None,
        output_metadata_updates=None,
    ):
        asset_id = "hero_1"
        variant_id = "job_1_pack_1"
        job_id = "job_1"
        basename = "hero-front.png"
        payload = b"generated reference bytes"
        source = self.sources / basename
        source.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        proof = create_reference_pack_anchor_proof(
            _KEY,
            project_id=proof_project_id or project_id,
            workspace_id="main",
            asset_id=asset_id,
            variant_id=variant_id,
            output_index=0,
            basename=basename,
            source_model_id=model_id,
            source_sha256=digest,
            job_id=job_id,
        )
        variant_metadata = {
            "reference_pack": {
                "model": model_id,
                "generation_model": model_id,
                "provenance": {
                    "service": "reference_sheets",
                    "version": "reference-pack-v2",
                },
            },
            "job": {"id": job_id, "generation_model": model_id},
        }
        variant_metadata.update(variant_metadata_updates or {})
        output_reference = {
            "model": model_id,
            "role": "front_full_body",
            "provenance": {"strategy": "draft_one_shot", "version": "v1"},
            "anchor_provenance": proof,
        }
        output_metadata = {
            "reference_pack": output_reference,
            "lineage": {"parent_job_id": job_id},
        }
        output_metadata.update(output_metadata_updates or {})
        asset = self.store.create_asset(
            project_id,
            "main",
            asset_id=asset_id,
            name="Hero",
            asset_type="character",
            provenance={"kind": "typed", "details": {"author": "test"}},
            variants=[{
                "id": variant_id,
                "variant_type": "reference_pack",
                "label": "Candidate 1",
                "status": status,
                "provenance": {
                    "kind": variant_provenance_kind,
                    "details": {
                        "service": "reference_sheets",
                        "version": "reference-pack-v2",
                        "job_id": job_id,
                    },
                },
                "metadata": variant_metadata,
                "outputs": [{
                    "source_path": source,
                    "label": "Front",
                    "metadata": output_metadata,
                }],
            }],
        )
        variant = asset["variants"][0]
        output = variant["outputs"][0]
        return {
            "project_id": project_id,
            "asset_id": asset_id,
            "variant_id": variant_id,
            "output_id": output["id"],
            "output_path": Path(self.store.resolve_output_path(
                project_id, "main", output["relative_path"],
            )),
            "digest": digest,
        }

    @staticmethod
    def _is_flux(model_id):
        return model_id in _FLUX_MODELS

    def _resolve(self, published, **updates):
        request = {
            "project_id": published["project_id"],
            "workspace_id": "main",
            "asset_id": published["asset_id"],
            "variant_id": published["variant_id"],
            "output_id": published["output_id"],
            "is_verified_flux_model": self._is_flux,
        }
        request.update(updates)
        return resolve_character_sheet_anchor(self.store, _KEY, **request)

    def _read_manifest(self, project_id="film_1"):
        path = self.storage / "projects" / project_id / "project-assets.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _selected_output(manifest, asset_id="hero_1", variant_id="job_1_pack_1"):
        workspace = manifest["workspaces"]["main"]
        asset = next(item for item in workspace["assets"] if item["id"] == asset_id)
        variant = next(item for item in asset["variants"] if item["id"] == variant_id)
        return variant["outputs"][0]

    def test_generated_kept_flux_output_resolves_exact_schema_v3_anchor(self):
        published = self._publish()

        resolved = self._resolve(published)

        self.assertEqual(resolved["anchor"], {
            "schema_version": 3,
            "project_id": "film_1",
            "anchor_id": published["output_id"],
            "kind": "generated",
            "sha256": published["digest"],
            "source_model_id": "flux2_klein_9b",
            "source_model_family": "flux",
        })
        self.assertEqual(Path(resolved["source_path"]), published["output_path"])

    def test_wrong_project_binding_and_non_main_workspace_are_rejected(self):
        published = self._publish(project_id="other_1", proof_project_id="film_1")
        with self.assertRaisesRegex(CharacterSheetAnchorError, "proof_invalid"):
            self._resolve(published)

        local = self._publish()
        with self.assertRaisesRegex(CharacterSheetAnchorError, "workspace_invalid"):
            self._resolve(local, workspace_id="default")

    def test_unkept_imported_and_non_flux_outputs_are_rejected(self):
        cases = (
            (
                {"project_id": "film_candidate", "status": "candidate"},
                "variant_not_kept_reference_pack",
            ),
            (
                {"project_id": "film_imported", "variant_provenance_kind": "imported"},
                "not_generated",
            ),
            (
                {
                    "project_id": "film_nonflux",
                    "model_id": "qwen_image_edit_2511_20B_fp8_lightning_4step",
                },
                "source_model_not_flux",
            ),
        )
        for options, reason in cases:
            with self.subTest(reason=reason):
                published = self._publish(**options)
                with self.assertRaisesRegex(CharacterSheetAnchorError, reason):
                    self._resolve(published)

    def test_missing_proof_and_tampered_proof_are_rejected(self):
        published = self._publish()
        path, manifest = self._read_manifest()
        output = self._selected_output(manifest)
        output["metadata"]["reference_pack"].pop("anchor_provenance")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.store = ProjectAssetStore(self.storage, [self.sources])
        with self.assertRaisesRegex(CharacterSheetAnchorError, "proof_missing"):
            self._resolve(published)

        published = self._publish(project_id="film_2")
        path, manifest = self._read_manifest("film_2")
        output = self._selected_output(manifest)
        output["metadata"]["reference_pack"]["anchor_provenance"]["signature"] = "f" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.store = ProjectAssetStore(self.storage, [self.sources])
        with self.assertRaisesRegex(CharacterSheetAnchorError, "proof_invalid"):
            self._resolve(published)

    def test_symlink_and_changed_bytes_are_rejected(self):
        published = self._publish()
        target = published["output_path"].with_name("other.png")
        target.write_bytes(b"generated reference bytes")
        published["output_path"].unlink()
        published["output_path"].symlink_to(target)
        with self.assertRaises(CharacterSheetAnchorError):
            self._resolve(published)

        published = self._publish(project_id="film_links")
        os.link(
            published["output_path"],
            published["output_path"].with_name("hardlink.png"),
        )
        with self.assertRaisesRegex(CharacterSheetAnchorError, "source_file_invalid"):
            self._resolve(published)

        published = self._publish(project_id="film_2")
        published["output_path"].write_bytes(b"changed after publication")
        with self.assertRaisesRegex(CharacterSheetAnchorError, "source_digest_mismatch"):
            self._resolve(published)

    def test_conflicting_model_claims_and_duplicate_output_ids_are_rejected(self):
        published = self._publish(variant_metadata_updates={
            "reference_pack": {
                "model": "flux2_klein_4b",
                "generation_model": "flux2_klein_4b",
                "provenance": {
                    "service": "reference_sheets",
                    "version": "reference-pack-v2",
                },
            },
        })
        with self.assertRaisesRegex(CharacterSheetAnchorError, "model_claim_mismatch"):
            self._resolve(published)

        published = self._publish(project_id="film_2")
        path, manifest = self._read_manifest("film_2")
        variant = manifest["workspaces"]["main"]["assets"][0]["variants"][0]
        duplicate = copy.deepcopy(variant["outputs"][0])
        duplicate["relative_path"] = "media/hero_1/job_1_pack_1/other.png"
        duplicate["filename"] = "other.png"
        variant["outputs"].append(duplicate)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.store = ProjectAssetStore(self.storage, [self.sources])
        with self.assertRaisesRegex(CharacterSheetAnchorError, "output_ambiguous"):
            self._resolve(published)

    def test_registry_callback_is_required_and_must_confirm_native_flux(self):
        published = self._publish()
        with self.assertRaisesRegex(CharacterSheetAnchorError, "model_registry_unavailable"):
            self._resolve(published, is_verified_flux_model=None)
        with self.assertRaisesRegex(CharacterSheetAnchorError, "source_model_not_flux"):
            self._resolve(published, is_verified_flux_model=lambda _model: False)


if __name__ == "__main__":
    unittest.main()
