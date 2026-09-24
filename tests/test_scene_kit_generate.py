"""Model-free regressions for identity-bound Scene Kit image references."""
from __future__ import annotations

import ast
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

from fastapi import HTTPException
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.project_assets import ProjectAssetStore  # noqa: E402
from services.queue_recovery_runtime import (  # noqa: E402
    atomic_write_request_manifest,
    load_request_manifest,
)
from services.output_access import (  # noqa: E402
    can_access_upload,
    upload_access_sidecar_path,
)


LAUNCH = ROOT / "app" / "launch.py"
SESSION = "a" * 32


def _load_helpers():
    tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
    names = {
        "_normalize_project_asset_ref_descriptors",
        "_generation_image_ref_count",
        "_project_asset_apply_outputs",
        "_cleanup_project_asset_ref_snapshots",
        "_cleanup_terminal_project_asset_snapshots",
        "_snapshot_project_asset_refs",
        "_revalidate_generation_asset_ref_scope",
        "_can_access_project_asset_variant",
        "_require_project_asset_media_access",
        "_require_remote_visible_models",
        "_prepare_generation_sidecar_params",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "Request": object,
        "HTTPException": HTTPException,
        "hashlib": hashlib,
        "hmac": hmac,
        "os": os,
        "uuid": uuid,
        "_PROJECT_ASSET_REF_MAX_DESCRIPTORS": 32,
        "_PROJECT_ASSET_REF_MAX_OUTPUTS": 32,
        "MAX_IMAGE_UPLOAD_BYTES": 500 * 1024 * 1024,
        "re": re,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(LAUNCH), "exec"), namespace)
    return namespace


class SceneKitGenerateReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.store = ProjectAssetStore(self.base / "asset-store", [self.sources])
        self.helpers = _load_helpers()
        self.helpers["_app_dir"] = str(self.base)
        self.helpers["_project_asset_store"] = lambda: self.store
        self.helpers["_require_upload_content_access"] = lambda _request: None
        self.helpers["_queue_recovery_existing_project_identity"] = lambda _path: "project-identity"
        self.helpers["_remote_visible_model_ids"] = lambda _request: frozenset({"minimax_h3"})
        self.request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id=SESSION),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _png(self, name: str, color: str = "red") -> Path:
        path = self.sources / name
        Image.new("RGB", (12, 8), color=color).save(path, format="PNG")
        return path

    def _asset(
        self, *, asset_type="character", status="kept", outputs=None,
        variant_type="pose", metadata=None, asset_id="hero_1",
        variant_id="turnaround_1",
    ):
        if outputs is None:
            outputs = [{"source_path": self._png("front.png"), "label": "Front view"}]
        return self.store.create_asset(
            "film_1",
            "main",
            asset_id=asset_id,
            name="Mara Voss",
            asset_type=asset_type,
            variants=[{
                "id": variant_id,
                "variant_type": variant_type,
                "label": "Turnaround",
                "status": status,
                "metadata": metadata or {},
                "outputs": outputs,
            }],
        )

    def _descriptor(self, asset=None, *, output_ids=None, **extra):
        asset = asset or self.store.get_asset("film_1", "main", "hero_1")
        variant = asset["variants"][0]
        descriptor = {
            "asset_id": asset["id"],
            "variant_id": variant["id"],
            "output_ids": output_ids or [
                item["id"] for item in self.helpers["_project_asset_apply_outputs"](variant)
            ],
        }
        descriptor.update(extra)
        return descriptor

    def _snapshot(self, descriptors, *, direct_refs=None, model_def=None):
        return self.helpers["_snapshot_project_asset_refs"](
            self.request,
            "film_1",
            self.helpers["_normalize_project_asset_ref_descriptors"](descriptors),
            job_id="b" * 32,
            direct_refs=direct_refs or [],
            model_def=model_def or {},
            edit_source_present=False,
        )

    def test_server_resolves_identity_and_stages_ordered_session_private_bytes(self):
        asset = self._asset(outputs=[
            {"source_path": self._png("front.png"), "label": "Front view"},
            {"source_path": self._png("profile.png", "blue"), "label": "Left profile"},
        ], variant_type="reference_pack")
        submitted = self._descriptor(
            asset,
            path="/client/forged.png",
            label="Forged label",
            kind="location",
        )
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            paths, provenance, path_provenance = self._snapshot([submitted])

        self.assertEqual(len(paths), 2)
        self.assertEqual([Path(path).read_bytes() for path in paths], [
            self._png_bytes("front.png"), self._png_bytes("profile.png"),
        ])
        self.assertTrue(all(can_access_upload(path, SESSION) for path in paths))
        self.assertEqual(provenance, [{
            "asset_id": "hero_1",
            "asset_label": "Mara Voss",
            "variant_id": "turnaround_1",
            "variant_label": "Turnaround",
            "outputs": [
                {"output_id": asset["variants"][0]["outputs"][0]["id"], "label": "Front view"},
                {"output_id": asset["variants"][0]["outputs"][1]["id"], "label": "Left profile"},
            ],
        }])
        self.assertNotIn("Forged label", repr(provenance))
        self.assertNotIn("/client/forged.png", repr(provenance))
        self.assertEqual(
            [item["relative_path"] for item in path_provenance[0]["outputs"]],
            [item["relative_path"] for item in asset["variants"][0]["outputs"]],
        )
        self.assertEqual(
            [item["staged_path"] for item in path_provenance[0]["outputs"]],
            paths,
        )
        staged_bytes = [Path(path).read_bytes() for path in paths]
        for output in asset["variants"][0]["outputs"]:
            source = Path(self.store.resolve_output_path(
                "film_1", "main", output["relative_path"],
            ))
            Image.new("RGB", (12, 8), color="green").save(source, format="PNG")
        self.assertEqual([Path(path).read_bytes() for path in paths], staged_bytes)
        self.assertEqual(list((self.base / "uploads").glob("*.png")), [])

    def _png_bytes(self, name: str) -> bytes:
        output = self.store.get_asset("film_1", "main", "hero_1")["variants"][0]["outputs"]
        index = 0 if name == "front.png" else 1
        source = Path(self.store.resolve_output_path("film_1", "main", output[index]["relative_path"]))
        return source.read_bytes()

    def test_existing_upload_volume_symlink_stages_into_canonical_private_root(self):
        asset = self._asset()
        upload_volume = self.base / "upload-volume"
        upload_volume.mkdir()
        (self.base / "uploads").symlink_to(upload_volume, target_is_directory=True)

        paths, _provenance, _path_provenance = self._snapshot([self._descriptor(asset)])

        self.assertEqual(len(paths), 1)
        self.assertTrue(Path(paths[0]).is_relative_to(upload_volume))
        self.assertTrue(can_access_upload(paths[0], SESSION))
        self.assertEqual(Path(paths[0]).read_bytes(), self._png("front.png").read_bytes())

    def test_exact_output_order_and_kept_state_are_required_before_copy(self):
        asset = self._asset(outputs=[
            {"source_path": self._png("front.png"), "label": "Front"},
            {"source_path": self._png("profile.png"), "label": "Profile"},
        ], variant_type="reference_pack")
        descriptor = self._descriptor(asset, output_ids=[
            asset["variants"][0]["outputs"][1]["id"],
            asset["variants"][0]["outputs"][0]["id"],
        ])
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            with self.assertRaises(HTTPException) as mismatch:
                self._snapshot([descriptor])
            self.assertEqual(mismatch.exception.status_code, 409)
            self.assertEqual(list((self.base / "uploads").glob("project-ref-*")), [])
            self.store.set_variant_status("film_1", "main", "hero_1", "turnaround_1", "candidate")
            with self.assertRaises(HTTPException) as not_kept:
                self._snapshot([self._descriptor(asset)])
            self.assertEqual(not_kept.exception.status_code, 409)
            self.assertEqual(list((self.base / "uploads").glob("project-ref-*")), [])

    def test_reference_pack_order_and_reference_sheet_primary_match_studio(self):
        packed = self._asset(
            asset_id="packed_1",
            variant_id="pack_1",
            variant_type="reference_pack",
            outputs=[
                {
                    "source_path": self._png("pack-second.png", "blue"),
                    "label": "Second role",
                    "metadata": {"reference_pack": {"index": 2}},
                },
                {
                    "source_path": self._png("pack-first.png", "red"),
                    "label": "First role",
                    "metadata": {"reference_pack": {"index": 1}},
                },
            ],
        )
        pack_outputs = packed["variants"][0]["outputs"]
        ordered_ids = [pack_outputs[1]["id"], pack_outputs[0]["id"]]
        pack_descriptor = self._descriptor(packed, output_ids=ordered_ids)
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            pack_paths, pack_provenance, pack_path_rows = self._snapshot([pack_descriptor])
        self.assertEqual(
            [row["output_id"] for row in pack_provenance[0]["outputs"]],
            ordered_ids,
        )
        self.assertEqual(
            [row["output_id"] for row in pack_path_rows[0]["outputs"]],
            ordered_ids,
        )
        self.assertEqual(
            [Path(path).read_bytes() for path in pack_paths],
            [
                Path(self.store.resolve_output_path("film_1", "main", pack_outputs[1]["relative_path"])).read_bytes(),
                Path(self.store.resolve_output_path("film_1", "main", pack_outputs[0]["relative_path"])).read_bytes(),
            ],
        )

        sheet_asset = self._asset(
            asset_id="sheet_1",
            variant_id="sheet_variant_1",
            variant_type="reference_sheet",
            outputs=[
                {
                    "source_path": self._png("component.png", "blue"),
                    "label": "Component crop",
                    "metadata": {"reference_sheet": {"role": "component"}},
                },
                {
                    "source_path": self._png("sheet.png", "red"),
                    "label": "Complete sheet",
                    "metadata": {"reference_sheet": {"role": "sheet"}},
                },
            ],
        )
        selected = sheet_asset["variants"][0]["outputs"][1]
        sheet_descriptor = self._descriptor(sheet_asset, output_ids=[selected["id"]])
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            sheet_paths, sheet_provenance, sheet_path_rows = self._snapshot([sheet_descriptor])
        self.assertEqual(len(sheet_paths), 1)
        self.assertEqual(sheet_provenance[0]["outputs"][0]["output_id"], selected["id"])
        self.assertEqual(sheet_path_rows[0]["outputs"][0]["relative_path"], selected["relative_path"])

    def test_permission_revocation_after_copy_cleans_the_staged_upload(self):
        asset = self._asset()
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            paths, _identity, _path_provenance = self._snapshot([self._descriptor(asset)])
        self.assertTrue(can_access_upload(paths[0], SESSION))

        self.helpers["_require_project_access"] = mock.Mock(
            side_effect=HTTPException(status_code=404, detail="Project not found"),
        )
        with self.assertRaises(HTTPException):
            self.helpers["_revalidate_generation_asset_ref_scope"](
                self.request,
                "film_1",
                str(self.base / "project-output"),
                "project-identity",
                SESSION,
                paths,
            )
        self.assertFalse(Path(paths[0]).exists())
        self.assertFalse(Path(upload_access_sidecar_path(paths[0])).exists())

    def test_asset_kind_session_review_and_model_reference_limits_fail_closed(self):
        style = self._asset(asset_type="style", asset_id="style_1")
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            with self.assertRaises(HTTPException) as invalid_kind:
                self._snapshot([self._descriptor(style)])
            self.assertEqual(invalid_kind.exception.status_code, 409)

        private = self._asset(
            asset_type="character",
            status="candidate",
            variant_type="blender_video",
            asset_id="private_1",
            variant_id="private_variant_1",
            metadata={
                "tool": "blender_mcp",
                "review_owner_session_hash": hashlib.sha256(b"another-session").hexdigest(),
            },
        )
        with self.assertRaises(HTTPException) as private_denied:
            self._snapshot([self._descriptor(private)])
        self.assertEqual(private_denied.exception.status_code, 404)

        character = self._asset(asset_type="character", asset_id="character_1")
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            with self.assertRaises(HTTPException) as too_many:
                self._snapshot(
                    [self._descriptor(character)],
                    direct_refs=["existing.png"],
                    model_def={"max_image_refs": 1},
                )
            self.assertEqual(too_many.exception.status_code, 400)
            self.assertEqual(list((self.base / "uploads").glob("project-ref-*")), [])

    def test_partial_copy_failure_removes_all_snapshots_and_sidecars(self):
        asset = self._asset(outputs=[
            {"source_path": self._png("front.png"), "label": "Front"},
            {"source_path": self._png("profile.png"), "label": "Profile"},
        ], variant_type="reference_pack")
        from services import output_access
        original = output_access.write_upload_access_sidecar
        calls = 0

        def fail_second(path, owner_session_id, *, private=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected sidecar failure")
            return original(path, owner_session_id, private=private)

        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            with mock.patch.object(output_access, "write_upload_access_sidecar", side_effect=fail_second):
                with self.assertRaises(HTTPException):
                    self._snapshot([self._descriptor(asset)])

        upload_files = list((self.base / "uploads").rglob("reference-*"))
        self.assertEqual(upload_files, [])
        self.assertEqual(list((self.base / "uploads").rglob("*.access.json")), [])

    def test_private_recovery_manifest_keeps_server_identity_with_pinned_paths(self):
        asset = self._asset()
        with mock.patch.object(os, "getcwd", return_value=str(self.base)):
            paths, provenance, path_provenance = self._snapshot([self._descriptor(asset)])
        params = {
            "image_refs": paths + ["existing-direct-reference.png"],
            "_project_asset_ref_provenance": provenance,
            "_project_asset_ref_paths": path_provenance,
        }
        project_dir = self.base / "project-output"
        project_dir.mkdir()
        pointer = atomic_write_request_manifest(
            project_dir,
            job_id="scene-kit-job-1",
            params=params,
            inputs=[],
        )
        recovered = load_request_manifest(
            project_dir,
            pointer,
            expected_job_id="scene-kit-job-1",
        )
        self.assertEqual(recovered["params"]["image_refs"], params["image_refs"])
        self.assertEqual(
            recovered["params"]["_project_asset_ref_provenance"], provenance,
        )
        self.assertEqual(
            recovered["params"]["_project_asset_ref_paths"], path_provenance,
        )
        self.assertTrue(can_access_upload(paths[0], SESSION))
        self.assertTrue(Path(upload_access_sidecar_path(paths[0])).is_file())

    def test_terminal_snapshot_cleanup_preserves_retryable_failures(self):
        asset = self._asset()
        paths, provenance, path_provenance = self._snapshot([
            self._descriptor(asset),
        ])
        job = {
            "id": "b" * 32,
            "status": "failed",
            "session_id": SESSION,
            "params": {"_project_asset_ref_paths": path_provenance},
        }
        cleanup = self.helpers["_cleanup_terminal_project_asset_snapshots"]
        self.assertFalse(cleanup(job))
        self.assertTrue(Path(paths[0]).is_file())
        self.assertTrue(Path(upload_access_sidecar_path(paths[0])).is_file())

        job["status"] = "completed"
        self.assertTrue(cleanup(job))
        self.assertFalse(Path(paths[0]).exists())
        self.assertFalse(Path(upload_access_sidecar_path(paths[0])).exists())
        self.assertFalse(Path(paths[0]).parent.exists())

    def test_route_and_worker_keep_identity_server_owned_and_out_of_wgp(self):
        source = LAUNCH.read_text(encoding="utf-8")
        generate = source[
            source.index('async def generate(request: Request):'):
            source.index('@api.post("/api/v1/retake")')
        ]
        tree = ast.parse(source)
        worker_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_generation"
        )
        source_lines = source.splitlines()
        worker = "\n".join(source_lines[worker_node.lineno - 1:worker_node.end_lineno])
        preparation_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_run_generation_preparation"
        )
        preparation = "\n".join(
            source_lines[preparation_node.lineno - 1:preparation_node.end_lineno]
        )
        self.assertIn('body.pop("project_asset_refs", None)', generate)
        self.assertIn('if submitted_project_asset_refs else []', generate)
        self.assertIn('body["_project_asset_ref_provenance"] = project_asset_provenance', generate)
        self.assertIn('body["_project_asset_ref_paths"] = project_asset_paths', generate)
        self.assertIn('raw_params.pop("_project_asset_ref_provenance", None)', worker)
        self.assertIn('raw_params.pop("_project_asset_ref_paths", None)', worker)
        self.assertIn('"_project_asset_ref_provenance", None,', preparation)
        self.assertIn('"_project_asset_ref_paths", None,', preparation)
        self.assertIn('manifest_params["_project_asset_ref_provenance"] =', preparation)
        self.assertIn('manifest_params["_project_asset_ref_paths"] =', preparation)
        approval_node = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_approve_waiting_generation_plan"
        )
        approval = "\n".join(
            source_lines[approval_node.lineno - 1:approval_node.end_lineno]
        )
        self.assertIn('prepared_params["_project_asset_ref_provenance"] =', approval)
        self.assertIn('prepared_params["_project_asset_ref_paths"] =', approval)

    def test_remote_model_admission_ignores_absent_optional_editor_model(self):
        remote_request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=True),
        )
        require_visible = self.helpers["_require_remote_visible_models"]
        require_visible(remote_request, ["minimax_h3", None, ""])
        with self.assertRaises(HTTPException) as hidden:
            require_visible(remote_request, ["minimax_h3", "hidden-model"])
        self.assertEqual(hidden.exception.status_code, 404)

    def test_output_sidecar_params_drop_private_scene_kit_provenance(self):
        prepare = self.helpers["_prepare_generation_sidecar_params"]
        _filenames, sidecar_params = prepare({
            "prompt": "A scene",
            "_project_asset_ref_provenance": [{
                "asset_id": "internal-asset-id",
                "asset_label": "Private card label",
            }],
            "_project_asset_ref_paths": [{
                "relative_path": "media/private-asset/private-variant/image.png",
                "staged_path": "/private/uploads/reference-private.png",
            }],
        })
        self.assertEqual(sidecar_params, {"prompt": "A scene"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
