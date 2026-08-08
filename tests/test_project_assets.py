"""Regressions for project-scoped reusable reference assets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services.project_assets import (  # noqa: E402
    ProjectAssetNotFoundError,
    ProjectAssetPersistenceError,
    ProjectAssetStore,
)


class ProjectAssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.storage = self.base / "library"
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.store = ProjectAssetStore(self.storage, [self.sources])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _media(self, name: str, payload: bytes = b"reference") -> Path:
        path = self.sources / name
        path.write_bytes(payload)
        return path

    def _create_character(self, **updates):
        fields = {
            "name": "Mara Voss",
            "asset_type": "character",
            "description": "A patient deep-space cartographer.",
            "tags": ["lead", "space", "Lead"],
            "provenance": {"kind": "typed", "details": {"author": "user"}},
            "metadata": {"accent": "soft"},
        }
        fields.update(updates)
        return self.store.create_asset("film_1", "default", **fields)

    def test_card_crud_persists_descriptions_tags_types_and_provenance(self):
        card = self._create_character(asset_id="hero_1")
        self.assertEqual(card["id"], "hero_1")
        self.assertEqual(card["asset_type"], "character")
        self.assertEqual(card["tags"], ["lead", "space"])
        self.assertEqual(card["provenance"]["kind"], "typed")
        self.assertEqual(card["variants"], [])

        updated = self.store.update_asset(
            "film_1",
            "default",
            "hero_1",
            {
                "description": "Updated description",
                "tags": ["hero", "continuity"],
                "asset_type": "cast member",
                "provenance": "imported",
            },
        )
        self.assertEqual(updated["description"], "Updated description")
        self.assertEqual(updated["asset_type"], "cast member")
        self.assertEqual(updated["provenance"], {"kind": "imported", "details": {}})

        reopened = ProjectAssetStore(self.storage, [self.sources])
        self.assertEqual(reopened.get_asset("film_1", "default", "hero_1"), updated)
        self.assertEqual(reopened.list_assets("film_1", "default", tags=["HERO"]), [updated])
        self.assertTrue(reopened.delete_asset("film_1", "default", "hero_1"))
        self.assertFalse(reopened.delete_asset("film_1", "default", "hero_1"))
        with self.assertRaises(ProjectAssetNotFoundError):
            reopened.get_asset("film_1", "default", "hero_1")

    def test_variants_copy_one_or_more_outputs_and_persist_only_relative_paths(self):
        front = self._media("mara-front.png", b"front")
        profile = self._media("mara-profile.png", b"profile")
        card = self._create_character(
            asset_id="hero",
            variants=[{
                "id": "pose_turnaround",
                "variant_type": "pose",
                "label": "Turnaround",
                "status": "candidate",
                "provenance": {"kind": "generated", "details": {"job_id": "job-7"}},
                "outputs": [
                    front,
                    {"source_path": profile, "label": "right profile", "metadata": {"seed": 42}},
                ],
            }],
        )

        variant = card["variants"][0]
        self.assertEqual(len(variant["outputs"]), 2)
        self.assertEqual(variant["variant_type"], "pose")
        for output, expected in zip(variant["outputs"], (b"front", b"profile")):
            relative = output["relative_path"]
            self.assertFalse(os.path.isabs(relative))
            copied = Path(self.store.resolve_output_path("film_1", "default", relative))
            self.assertEqual(copied.read_bytes(), expected)

        front.unlink()
        profile.unlink()
        reopened = ProjectAssetStore(self.storage, [self.sources])
        saved = reopened.get_asset("film_1", "default", "hero")
        self.assertEqual(len(saved["variants"][0]["outputs"]), 2)
        manifest_text = (
            self.storage / "projects" / "film_1" / "project-assets.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn(str(self.sources), manifest_text)
        self.assertNotIn("source_path", manifest_text)

    def test_blender_semantic_mapping_roundtrips_atomically_with_variant_provenance(self):
        video = self._media("blocking-reference.mp4", b"full-rate-video")
        semantic_mapping = {
            "legend": [{
                "object_name": "LeadGuide",
                "primitive": "sphere",
                "color": [0.8, 0.1, 0.2, 1.0],
                "subject": "lead dancer",
                "action": "turns toward camera",
            }],
            "conditioned_prompt": "The red sphere drives the lead dancer's turn.",
        }
        created = self.store.create_asset(
            "film_1",
            "default",
            asset_id="blender_motion",
            name="Director motion control",
            asset_type="setting",
            provenance={"kind": "generated", "details": {"tool": "blender_mcp"}},
            metadata={"semantic_mapping": semantic_mapping},
            variants=[{
                "id": "full_video",
                "variant_type": "blender_video",
                "label": "Full-rate control",
                "outputs": [{"source_path": video, "metadata": {"full_fps_reference": True}}],
                "provenance": {"kind": "generated", "details": {"lineage": "run-1"}},
                "metadata": {
                    "semantic_mapping": semantic_mapping,
                    "conditioned_prompt": semantic_mapping["conditioned_prompt"],
                },
            }],
        )
        semantic_mapping["legend"][0]["subject"] = "mutated caller value"

        reopened = ProjectAssetStore(self.storage, [self.sources])
        saved = reopened.get_asset("film_1", "default", created["id"])
        variant = saved["variants"][0]
        self.assertEqual(
            variant["metadata"]["semantic_mapping"]["legend"][0]["subject"],
            "lead dancer",
        )
        self.assertEqual(
            variant["provenance"],
            {"kind": "generated", "details": {"lineage": "run-1"}},
        )
        copied = Path(reopened.resolve_output_path(
            "film_1", "default", variant["outputs"][0]["relative_path"],
        ))
        self.assertEqual(copied.read_bytes(), b"full-rate-video")

    def test_add_keep_reject_filter_get_and_delete_variant(self):
        card = self._create_character(asset_id="hero")
        first = self.store.add_variant(
            "film_1", "default", card["id"],
            variant_id="winter",
            variant_type="outfit",
            label="Winter coat",
            outputs=[self._media("winter.png")],
        )
        second = self.store.add_variant(
            "film_1", "default", card["id"],
            variant_id="summer",
            variant_type="outfit",
            label="Summer coat",
            outputs=[self._media("summer.png")],
            provenance="imported",
        )

        self.assertEqual(self.store.keep_variant("film_1", "default", "hero", first["id"])["status"], "kept")
        self.assertEqual(self.store.reject_variant("film_1", "default", "hero", second["id"])["status"], "rejected")
        self.assertEqual(
            [asset["id"] for asset in self.store.list_assets(
                "film_1", "default", variant_status="kept",
            )],
            ["hero"],
        )
        self.assertEqual(
            self.store.get_variant("film_1", "default", "hero", "summer")["status"],
            "rejected",
        )

        relative = self.store.get_variant(
            "film_1", "default", "hero", "summer",
        )["outputs"][0]["relative_path"]
        copied = Path(self.store.resolve_output_path("film_1", "default", relative))
        self.assertTrue(self.store.delete_variant("film_1", "default", "hero", "summer"))
        self.assertFalse(copied.exists())
        self.assertFalse(self.store.delete_variant("film_1", "default", "hero", "summer"))

    def test_project_and_workspace_scopes_are_isolated(self):
        for project, workspace, name in (
            ("film_a", "default", "A default"),
            ("film_a", "second", "A second"),
            ("film_b", "default", "B default"),
        ):
            self.store.create_asset(
                project,
                workspace,
                asset_id="shared_id",
                name=name,
                asset_type="location",
            )
        self.assertEqual(
            self.store.get_asset("film_a", "default", "shared_id")["name"],
            "A default",
        )
        self.assertEqual(
            self.store.get_asset("film_a", "second", "shared_id")["name"],
            "A second",
        )
        self.assertEqual(
            self.store.get_asset("film_b", "default", "shared_id")["name"],
            "B default",
        )

    def test_delete_project_removes_manifest_and_all_copied_reference_media(self):
        source = self._media("old-private-reference.mp4", b"video")
        self.store.create_asset(
            "film_a", "default", asset_id="old_ref",
            name="Old private reference", asset_type="setting",
            variants=[{
                "id": "full_video",
                "variant_type": "video",
                "label": "Full render",
                "outputs": [source],
            }],
        )
        old_root = self.storage / "projects" / "film_a"
        self.assertTrue(old_root.is_dir())
        self.assertTrue(self.store.delete_project("film_a"))
        self.assertFalse(old_root.exists())
        self.assertFalse(self.store.delete_project("film_a"))
        self.assertEqual(self.store.list_assets("film_a", "default"), [])

    def test_safe_ids_source_allowlist_and_relative_path_resolution(self):
        for project_id, workspace_id in (
            ("../escape", "default"),
            ("film", "../escape"),
            ("C:drive", "default"),
            ("CON", "default"),
        ):
            with self.subTest(project=project_id, workspace=workspace_id):
                with self.assertRaises(ValueError):
                    self.store.list_assets(project_id, workspace_id)

        card = self._create_character(asset_id="hero")
        outside = self.base / "outside.png"
        outside.write_bytes(b"outside")
        with self.assertRaises(ValueError):
            self.store.add_variant(
                "film_1", "default", card["id"],
                variant_type="pose", label="unsafe", outputs=[outside],
            )
        for path in ("/tmp/file.png", "../file.png", "media/../../file.png", "other/file.png"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.store.resolve_output_path("film_1", "default", path)

    @unittest.skipIf(not hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_sources_are_rejected(self):
        target = self._media("target.png")
        link = self.sources / "link.png"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        card = self._create_character(asset_id="hero")
        with self.assertRaises(ValueError):
            self.store.add_variant(
                "film_1", "default", card["id"],
                variant_type="pose", label="link", outputs=[link],
            )

    def test_variant_requires_outputs_and_rejects_duplicate_basenames(self):
        card = self._create_character(asset_id="hero")
        with self.assertRaises(ValueError):
            self.store.add_variant(
                "film_1", "default", card["id"],
                variant_type="pose", label="empty", outputs=[],
            )

        left = self.sources / "left"
        right = self.sources / "right"
        left.mkdir()
        right.mkdir()
        (left / "same.png").write_bytes(b"left")
        (right / "same.png").write_bytes(b"right")
        store = ProjectAssetStore(self.storage, [self.sources])
        with self.assertRaises(ValueError):
            store.add_variant(
                "film_1", "default", card["id"],
                variant_id="duplicates",
                variant_type="pose",
                label="duplicates",
                outputs=[left / "same.png", right / "same.png"],
            )
        self.assertFalse(
            self.storage.joinpath(
                "projects", "film_1", "workspaces", "default", "media", "hero", "duplicates",
            ).exists()
        )

        with self.assertRaises(ValueError):
            store.add_variant(
                "film_1", "default", card["id"],
                variant_id="invalid_label",
                variant_type="pose",
                label="",
                outputs=[left / "same.png"],
            )
        self.assertFalse(
            self.storage.joinpath(
                "projects", "film_1", "workspaces", "default", "media", "hero", "invalid_label",
            ).exists()
        )

    def test_password_metadata_is_opaque_project_metadata_not_authentication(self):
        metadata = {
            "enabled": True,
            "provider": "external-auth",
            "key_id": "project-key-7",
            "policy": {"version": 2},
        }
        returned = self.store.set_password_metadata("film_1", metadata)
        metadata["policy"]["version"] = 99
        self.assertEqual(returned["policy"]["version"], 2)
        self.assertEqual(self.store.get_password_metadata("film_1")["provider"], "external-auth")
        self.assertIsNone(self.store.get_password_metadata("film_2"))
        self.assertIsNone(self.store.set_password_metadata("film_1", None))
        self.assertIsNone(self.store.get_password_metadata("film_1"))

    def test_atomic_replace_failure_preserves_previous_manifest(self):
        self._create_character(asset_id="hero")
        manifest = self.storage / "projects" / "film_1" / "project-assets.json"
        before = manifest.read_bytes()
        real_replace = os.replace

        def fail_manifest_replace(source, destination):
            if Path(destination) == manifest:
                raise OSError("simulated replace failure")
            return real_replace(source, destination)

        with mock.patch("services.project_assets.os.replace", side_effect=fail_manifest_replace):
            with self.assertRaises(OSError):
                self.store.update_asset(
                    "film_1", "default", "hero", {"description": "must not persist"},
                )
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(
            ProjectAssetStore(self.storage, [self.sources]).get_asset(
                "film_1", "default", "hero",
            )["description"],
            "A patient deep-space cartographer.",
        )
        self.assertEqual(list(manifest.parent.glob(".project-assets-*.tmp")), [])

    def test_invalid_updates_and_corrupt_manifests_fail_closed(self):
        self._create_character(asset_id="hero")
        with self.assertRaises(ValueError):
            self.store.update_asset(
                "film_1", "default", "hero", {"variants": []},
            )
        with self.assertRaises(ValueError):
            self.store.update_asset(
                "film_1", "default", "hero", {"provenance": "unknown"},
            )

        manifest = self.storage / "projects" / "film_1" / "project-assets.json"
        manifest.write_text("not-json", encoding="utf-8")
        with self.assertRaises(ProjectAssetPersistenceError):
            self.store.list_assets("film_1", "default")

    def test_deleting_asset_removes_its_copied_media_after_manifest_publish(self):
        source = self._media("prop.png")
        card = self.store.create_asset(
            "film_1",
            "default",
            asset_id="prop",
            name="Antique compass",
            asset_type="item",
            variants=[{
                "id": "aged",
                "variant_type": "style",
                "label": "Aged brass",
                "outputs": [source],
            }],
        )
        copied = Path(self.store.resolve_output_path(
            "film_1", "default", card["variants"][0]["outputs"][0]["relative_path"],
        ))
        self.assertTrue(copied.exists())
        self.assertTrue(self.store.delete_asset("film_1", "default", "prop"))
        self.assertFalse(copied.exists())


if __name__ == "__main__":
    unittest.main()
