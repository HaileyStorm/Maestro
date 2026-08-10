"""Regressions for project-scoped reusable reference assets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
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

    def test_atomic_variant_batch_uses_one_commit_and_publishes_all(self):
        self._create_character(asset_id="hero")
        specs = [
            {
                "id": "batch_one",
                "variant_type": "pose",
                "label": "Batch one",
                "outputs": [self._media("batch-one.png", b"one")],
            },
            {
                "id": "batch_two",
                "variant_type": "pose",
                "label": "Batch two",
                "outputs": [self._media("batch-two.png", b"two")],
            },
        ]
        with mock.patch.object(
            self.store, "_write_manifest", wraps=self.store._write_manifest,
        ) as write_manifest:
            variants = self.store.add_variants_atomic(
                "film_1", "default", "hero", specs,
            )
        self.assertEqual(write_manifest.call_count, 1)
        self.assertEqual([item["id"] for item in variants], ["batch_one", "batch_two"])
        reopened = ProjectAssetStore(self.storage, [self.sources])
        self.assertEqual(
            [item["id"] for item in reopened.get_asset(
                "film_1", "default", "hero",
            )["variants"]],
            ["batch_one", "batch_two"],
        )

    def test_atomic_batch_validates_exact_existing_replays_before_copy(self):
        source = self._media("existing-replay.png", b"existing")
        asset = self._create_character(asset_id="hero", variants=[{
            "id": "existing_replay",
            "variant_type": "pose",
            "label": "Existing replay",
            "outputs": [source],
        }])
        expected = asset["variants"][0]
        with mock.patch.object(
            self.store, "_write_manifest", wraps=self.store._write_manifest,
        ) as write_manifest:
            self.assertEqual(
                self.store.add_variants_atomic(
                    "film_1", "default", "hero", [],
                    expected_existing_variants=[expected],
                ),
                [],
            )
        write_manifest.assert_not_called()

        self.store.set_variant_status(
            "film_1", "default", "hero", "existing_replay", "kept",
        )
        with mock.patch.object(
            self.store, "_copy_outputs", wraps=self.store._copy_outputs,
        ) as copy_outputs, self.assertRaisesRegex(
            ValueError, "^existing variant changed: existing_replay$",
        ):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [{
                    "id": "must_not_publish",
                    "variant_type": "pose",
                    "label": "Must not publish",
                    "outputs": [self._media("must-not-publish.png", b"new")],
                }],
                expected_existing_variants=[expected],
            )
        copy_outputs.assert_not_called()
        current = self.store.get_asset("film_1", "default", "hero")
        self.assertEqual(
            [item["id"] for item in current["variants"]],
            ["existing_replay"],
        )

    def test_atomic_batch_rejects_missing_expected_replay_media_before_copy(self):
        source = self._media("replay-media.png", b"existing")
        asset = self._create_character(asset_id="hero", variants=[{
            "id": "existing_replay",
            "variant_type": "pose",
            "label": "Existing replay",
            "outputs": [source],
        }])
        expected = asset["variants"][0]
        copied = Path(self.store.resolve_output_path(
            "film_1", "default", expected["outputs"][0]["relative_path"],
        ))
        copied.unlink()
        with mock.patch.object(
            self.store, "_copy_outputs", wraps=self.store._copy_outputs,
        ) as copy_outputs, mock.patch.object(
            self.store, "_write_manifest", wraps=self.store._write_manifest,
        ) as write_manifest, self.assertRaisesRegex(
            ValueError, "^existing variant media changed: existing_replay$",
        ):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [{
                    "id": "must_not_publish",
                    "variant_type": "pose",
                    "label": "Must not publish",
                    "outputs": [self._media("must-not-copy.png", b"new")],
                }],
                expected_existing_variants=[expected],
            )
        copy_outputs.assert_not_called()
        write_manifest.assert_not_called()
        self.assertEqual(
            [item["id"] for item in self.store.get_asset(
                "film_1", "default", "hero",
            )["variants"]],
            ["existing_replay"],
        )

    def test_publication_guard_serializes_other_store_instances(self):
        self._create_character(asset_id="hero")
        second = ProjectAssetStore(self.storage, [self.sources])
        started = threading.Event()
        finished = threading.Event()

        def mutate():
            started.set()
            second.update_asset(
                "film_1", "default", "hero", {"description": "changed"},
            )
            finished.set()

        with self.store.publication_guard():
            thread = threading.Thread(target=mutate)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(0.05))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())

    def test_reviewed_source_identity_is_verified_during_final_copy(self):
        self._create_character(asset_id="hero")
        source = self._media("reviewed.png", b"reviewed bytes")
        approved = source.stat()
        expected = {
            "device": approved.st_dev,
            "inode": approved.st_ino,
            "size": approved.st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        replacement = self._media("replacement.png", b"replacement bytes")
        os.replace(replacement, source)
        with self.assertRaisesRegex(ValueError, "reviewed source identity changed"):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [{
                    "id": "reviewed_candidate",
                    "variant_type": "reference_pack",
                    "label": "Reviewed candidate",
                    "outputs": [{
                        "source_path": source,
                        "expected_source_identity": expected,
                    }],
                }],
            )
        self.assertEqual(
            self.store.get_asset("film_1", "default", "hero")["variants"],
            [],
        )
        media_root = (
            self.storage / "projects" / "film_1" / "workspaces" / "default"
            / "media" / "hero"
        )
        self.assertFalse((media_root / "reviewed_candidate").exists())

    def test_reviewed_source_identity_is_transient_on_success(self):
        self._create_character(asset_id="hero")
        source = self._media("approved.png", b"approved bytes")
        approved = source.stat()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        variants = self.store.add_variants_atomic(
            "film_1", "default", "hero", [{
                "id": "approved_candidate",
                "variant_type": "reference_pack",
                "label": "Approved candidate",
                "outputs": [{
                    "source_path": source,
                    "expected_source_identity": {
                        "device": approved.st_dev,
                        "inode": approved.st_ino,
                        "size": approved.st_size,
                        "sha256": digest,
                    },
                }],
            }],
        )
        self.assertNotIn("expected_source_identity", variants[0]["outputs"][0])
        manifest = (
            self.storage / "projects" / "film_1" / "project-assets.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn("expected_source_identity", manifest)
        self.assertNotIn(digest, manifest)

    def test_restart_cleanup_removes_only_unreferenced_transaction_media(self):
        source = self._media("kept-reference.png", b"kept")
        asset = self._create_character(asset_id="hero", variants=[{
            "id": "kept_variant",
            "variant_type": "reference_pack",
            "label": "Kept",
            "outputs": [source],
        }])
        kept_relative = asset["variants"][0]["outputs"][0]["relative_path"]
        kept_path = Path(self.store.resolve_output_path(
            "film_1", "default", kept_relative,
        ))
        media_root = kept_path.parents[2]
        orphan = media_root / "hero" / "orphan_variant"
        orphan.mkdir()
        (orphan / "private.png").write_bytes(b"private")
        hidden = media_root / "hero" / ".staged.0123456789abcdef.tmp"
        hidden.mkdir()
        (hidden / "private.png").write_bytes(b"private")
        orphan_asset = media_root / "orphan_asset" / "candidate"
        orphan_asset.mkdir(parents=True)
        (orphan_asset / "private.png").write_bytes(b"private")

        corrupt_project = self.storage / "projects" / "corrupt"
        corrupt_media = (
            corrupt_project / "workspaces" / "default" / "media"
            / "private_asset" / "private_variant"
        )
        corrupt_media.mkdir(parents=True)
        (corrupt_media / "must-remain.png").write_bytes(b"uncertain")
        (corrupt_project / "project-assets.json").write_text(
            "not-json", encoding="utf-8",
        )

        self.assertEqual(self.store.cleanup_unreferenced_media(), 3)
        self.assertTrue(kept_path.is_file())
        self.assertFalse(orphan.exists())
        self.assertFalse(hidden.exists())
        self.assertFalse(orphan_asset.exists())
        self.assertTrue(corrupt_media.is_dir())

    def test_atomic_variant_batch_copy_or_replace_failure_leaves_no_partial_media(self):
        before = self._create_character(asset_id="hero")
        valid = {
            "id": "batch_one",
            "variant_type": "pose",
            "label": "Batch one",
            "outputs": [self._media("batch-one.png", b"one")],
        }
        invalid = {
            "id": "batch_two",
            "variant_type": "pose",
            "label": "Batch two",
            "outputs": [self.sources / "missing.png"],
        }
        with self.assertRaises(ValueError):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [valid, invalid],
            )
        self.assertEqual(
            ProjectAssetStore(self.storage, [self.sources]).get_asset(
                "film_1", "default", "hero",
            ),
            before,
        )

        with mock.patch.object(
            self.store,
            "_write_manifest",
            side_effect=OSError("simulated batch write failure"),
        ), self.assertRaises(OSError):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [valid],
            )
        self.assertEqual(
            ProjectAssetStore(self.storage, [self.sources]).get_asset(
                "film_1", "default", "hero",
            ),
            before,
        )

        manifest = self.storage / "projects" / "film_1" / "project-assets.json"
        real_replace = os.replace

        def fail_manifest_replace(source, destination):
            if Path(destination) == manifest:
                raise OSError("simulated batch replace failure")
            return real_replace(source, destination)

        with mock.patch(
            "services.project_assets.os.replace", side_effect=fail_manifest_replace,
        ), self.assertRaises(OSError):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [valid, {
                    **invalid,
                    "outputs": [self._media("batch-two.png", b"two")],
                }],
            )
        self.assertEqual(
            ProjectAssetStore(self.storage, [self.sources]).get_asset(
                "film_1", "default", "hero",
            ),
            before,
        )
        media_root = manifest.parent / "workspaces" / "default" / "media" / "hero"
        self.assertEqual(list(media_root.glob("batch_*")), [])
        self.assertEqual(list(media_root.glob(".*.tmp")), [])

    def test_atomic_variant_batch_rejects_stable_id_collisions_before_copy(self):
        existing_source = self._media("existing.png", b"existing")
        before = self._create_character(asset_id="hero", variants=[{
            "id": "stable_id",
            "variant_type": "pose",
            "label": "Existing",
            "outputs": [existing_source],
        }])
        with mock.patch.object(
            self.store, "_copy_outputs", wraps=self.store._copy_outputs,
        ) as copy_outputs, self.assertRaises(ValueError):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [{
                    "id": "STABLE_ID",
                    "variant_type": "pose",
                    "label": "Collision",
                    "outputs": [self._media("collision.png", b"collision")],
                }],
            )
        copy_outputs.assert_not_called()
        self.assertEqual(
            ProjectAssetStore(self.storage, [self.sources]).get_asset(
                "film_1", "default", "hero",
            ),
            before,
        )

    def test_atomic_batch_cleanup_fallback_keeps_stable_id_retryable(self):
        self._create_character(asset_id="hero")
        spec = {
            "id": "retryable_id",
            "variant_type": "pose",
            "label": "Retryable",
            "outputs": [self._media("retryable.png", b"retryable")],
        }
        with mock.patch.object(
            self.store,
            "_write_manifest",
            side_effect=OSError("simulated pre-commit failure"),
        ), mock.patch(
            "services.project_assets.shutil.rmtree",
            side_effect=OSError("simulated cleanup helper failure"),
        ), self.assertRaises(OSError):
            self.store.add_variants_atomic(
                "film_1", "default", "hero", [spec],
            )
        media_root = (
            self.storage / "projects" / "film_1" / "workspaces" / "default"
            / "media" / "hero"
        )
        self.assertFalse((media_root / "retryable_id").exists())
        self.assertEqual(list(media_root.glob(".*.tmp")), [])
        retried = self.store.add_variants_atomic(
            "film_1", "default", "hero", [spec],
        )
        self.assertEqual(retried[0]["id"], "retryable_id")

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-only")
    def test_post_replace_directory_fsync_failure_keeps_committed_media(self):
        self._create_character(asset_id="hero")
        source = self._media("committed.png", b"committed")
        real_fsync = os.fsync
        calls = {"count": 0}

        def fail_directory_fsync(descriptor):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated directory fsync failure")
            return real_fsync(descriptor)

        with mock.patch(
            "services.project_assets.os.fsync", side_effect=fail_directory_fsync,
        ):
            variants = self.store.add_variants_atomic(
                "film_1", "default", "hero", [{
                    "id": "committed_variant",
                    "variant_type": "pose",
                    "label": "Committed variant",
                    "outputs": [source],
                }],
            )
        self.assertEqual(calls["count"], 2)
        persisted = ProjectAssetStore(self.storage, [self.sources]).get_variant(
            "film_1", "default", "hero", "committed_variant",
        )
        copied = Path(self.store.resolve_output_path(
            "film_1", "default", persisted["outputs"][0]["relative_path"],
        ))
        self.assertEqual(variants[0]["id"], "committed_variant")
        self.assertEqual(copied.read_bytes(), b"committed")

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

    def test_list_assets_is_read_only_when_absent_and_reloads_persisted_cards(self):
        missing_project = self.storage / "projects" / "unstarted"
        self.assertEqual(self.store.list_assets("unstarted", "default"), [])
        self.assertFalse(missing_project.exists())

        created = self._create_character(asset_id="persisted_card")
        reopened = ProjectAssetStore(self.storage, [self.sources])
        self.assertEqual(reopened.list_assets("film_1", "default"), [created])

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

    def test_invalid_updates_fail_closed(self):
        self._create_character(asset_id="hero")
        with self.assertRaises(ValueError):
            self.store.update_asset(
                "film_1", "default", "hero", {"variants": []},
            )
        with self.assertRaises(ValueError):
            self.store.update_asset(
                "film_1", "default", "hero", {"provenance": "unknown"},
            )

    def test_list_assets_rejects_corrupt_manifest_without_reflecting_content(self):
        self._create_character(asset_id="hero")
        manifest = self.storage / "projects" / "film_1" / "project-assets.json"
        corrupt_content = "private-corrupt-manifest-content"
        manifest.write_text(corrupt_content, encoding="utf-8")
        with self.assertRaises(ProjectAssetPersistenceError) as raised:
            self.store.list_assets("film_1", "default")
        self.assertNotIn(corrupt_content, str(raised.exception))

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
