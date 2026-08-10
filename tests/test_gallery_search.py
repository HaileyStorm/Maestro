"""Deterministic, model-free gallery search and artifact filter regressions."""
from __future__ import annotations

import ast
import copy
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_APP_DIR = os.path.join(_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.search_index import (  # noqa: E402
    ArtifactScope,
    SearchIndex,
    artifact_matches_scope,
    classify_gallery_artifacts,
    linked_component_names,
    load_media_sidecars,
)
from services.win_safe_files import (  # noqa: E402
    is_safe_direct_basename,
    is_safe_workspace_name,
    safe_direct_file_under,
    safe_join_under,
)


def _touch_media(workspace: str, name: str, size: int = 4) -> None:
    with open(os.path.join(workspace, name), "wb") as handle:
        handle.write(b"x" * size)


def _write_sidecar(workspace: str, media_name: str, **updates) -> str:
    meta = {
        "output_filename": media_name,
        "job_id": "job-1",
        "generation_mode": "video",
        "params": {
            "prompt": "copper sunrise",
            "negative_prompt": "fog",
            "model_type": "ltx_distilled",
            "seed": 42,
        },
    }
    meta.update(updates)
    path = os.path.join(workspace, os.path.splitext(media_name)[0] + ".meta.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    return path


class GallerySearchTests(unittest.TestCase):
    def test_sidecar_maps_to_exact_extension_when_stems_collide(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "same.png")
            _touch_media(workspace, "same.mp4")
            _write_sidecar(workspace, "same.mp4")

            sidecars = load_media_sidecars(workspace)
            self.assertEqual(set(sidecars), {"same.mp4"})
            self.assertEqual(SearchIndex().search("copper", workspace), {"same.mp4"})

    def test_prompt_model_mode_and_filename_search(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "hero-entrance.mp4")
            _write_sidecar(workspace, "hero-entrance.mp4")
            index = SearchIndex()
            for query in ("copper sunrise", "ltx distilled", "video", "hero entrance"):
                with self.subTest(query=query):
                    self.assertEqual(index.search(query, workspace), {"hero-entrance.mp4"})

    def test_changed_and_deleted_sidecars_refresh_without_manual_invalidation(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "fresh.mp4")
            sidecar = _write_sidecar(workspace, "fresh.mp4")
            index = SearchIndex()
            self.assertEqual(index.search("copper", workspace), {"fresh.mp4"})

            before = os.stat(sidecar).st_mtime_ns
            _write_sidecar(
                workspace,
                "fresh.mp4",
                params={"prompt": "violet horizon", "model_type": "new_model", "seed": 42},
            )
            os.utime(sidecar, ns=(before + 1_000_000_000, before + 1_000_000_000))
            self.assertEqual(index.search("violet", workspace), {"fresh.mp4"})
            self.assertEqual(index.search("copper", workspace), set())

            os.remove(sidecar)
            self.assertEqual(index.search("violet", workspace), set())

    def test_same_filename_is_isolated_between_workspaces(self):
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "first")
            second = os.path.join(root, "second")
            os.mkdir(first)
            os.mkdir(second)
            for workspace, prompt in ((first, "scarlet fox"), (second, "azure whale")):
                _touch_media(workspace, "shared.mp4")
                _write_sidecar(
                    workspace,
                    "shared.mp4",
                    params={"prompt": prompt, "model_type": "model", "seed": 7},
                )
            index = SearchIndex()
            self.assertEqual(index.search("scarlet", first), {"shared.mp4"})
            self.assertEqual(index.search("scarlet", second), set())
            self.assertEqual(index.search("azure", second), {"shared.mp4"})
            self.assertEqual(index.search("azure", first), set())

    def test_structured_filters_compose_from_durable_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "matching.mp4")
            _write_sidecar(
                workspace,
                "matching.mp4",
                created_at=1_788_200_000,
                upload_filenames={"image_refs": ["private-reference-name.png"]},
                params={
                    "prompt": "copper sunrise",
                    "model_type": "minimax_h3_ref2va",
                    "seed": 31415,
                    "activated_loras": ["styles/Cinematic Light.safetensors"],
                },
            )
            _touch_media(workspace, "other.mp4")
            _write_sidecar(
                workspace,
                "other.mp4",
                created_at=1_750_000_000,
                params={
                    "prompt": "copper sunrise",
                    "model_type": "minimax_h3",
                    "seed": 7,
                    "activated_loras": [],
                },
            )

            index = SearchIndex()
            query = (
                'copper model:"h3_ref2va" lora:"cinematic light" '
                "seed:31415 reference:with after:2026-01-01 before:2026-12-31"
            )
            self.assertEqual(index.search(query, workspace), {"matching.mp4"})
            self.assertEqual(index.search("reference:without", workspace), {"other.mp4"})
            self.assertEqual(index.search("model:h3_ref2va seed:7", workspace), set())

    def test_structured_reference_filter_indexes_presence_not_private_paths(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "referenced.mp4")
            _write_sidecar(
                workspace,
                "referenced.mp4",
                upload_filenames={"image_start": "secret-person-name.png"},
            )
            index = SearchIndex()
            self.assertEqual(index.search("reference:with", workspace), {"referenced.mp4"})
            self.assertEqual(index.search("secret-person-name", workspace), set())

    def test_unknown_search_prefix_remains_ordinary_text(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "field-notes.mp4")
            _write_sidecar(workspace, "field-notes.mp4", params={
                "prompt": "camera: handheld field notes",
                "model_type": "model",
                "seed": 7,
            })
            self.assertEqual(
                SearchIndex().search("camera: handheld", workspace),
                {"field-notes.mp4"},
            )

    def test_malformed_scalar_window_prompts_are_ignored_without_breaking_index(self):
        with tempfile.TemporaryDirectory() as workspace:
            _touch_media(workspace, "malformed.mp4")
            _write_sidecar(workspace, "malformed.mp4", params={
                "prompt": "still searchable",
                "model_type": "model",
                "seed": 9,
                "window_prompts": 12345,
            })
            index = SearchIndex()
            self.assertEqual(index.search("still searchable", workspace), {"malformed.mp4"})
            self.assertEqual(index.search("seed:9", workspace), {"malformed.mp4"})

    def test_date_filter_falls_back_to_media_mtime_for_missing_or_bad_sidecar_time(self):
        with tempfile.TemporaryDirectory() as workspace:
            expected = {"missing-time.mp4", "bad-time.mp4", "infinite-time.mp4"}
            for name, created_at in (
                ("missing-time.mp4", None),
                ("bad-time.mp4", "not-a-date"),
                ("infinite-time.mp4", "inf"),
            ):
                _touch_media(workspace, name)
                media_path = os.path.join(workspace, name)
                os.utime(media_path, (1_788_200_000, 1_788_200_000))
                updates = {} if created_at is None else {"created_at": created_at}
                _write_sidecar(workspace, name, **updates)
            index = SearchIndex()
            self.assertEqual(
                index.search("after:2026-08-01 before:2026-09-30", workspace),
                expected,
            )
            self.assertEqual(index.search("before:2025-01-01", workspace), set())

    def test_secondary_video_guides_and_video_source_count_as_references(self):
        with tempfile.TemporaryDirectory() as workspace:
            expected = set()
            for index, key in enumerate(("video_guide2", "video_guide3", "video_source"), start=2):
                name = f"video-reference-{index}.mp4"
                expected.add(name)
                _touch_media(workspace, name)
                _write_sidecar(
                    workspace,
                    name,
                    params={
                        "prompt": "reference coverage",
                        "model_type": "model",
                        "seed": index,
                        key: f"private-{key}.mp4",
                    },
                )
            search = SearchIndex()
            self.assertEqual(search.search("reference:with", workspace), expected)
            self.assertEqual(search.search("private-video", workspace), set())


class ArtifactClassificationTests(unittest.TestCase):
    def setUp(self):
        sliding = {
            "job_id": "job-sliding",
            "params": {
                "seed": 9,
                "video_length": 241,
                "sliding_window_size": 81,
            },
        }
        component = {
            "job_id": "job-group",
            "params": {
                "seed": 1,
                "multi_clip_info": {"group_id": "group-a", "index": 0, "total": 2},
            },
        }
        grouped_final = {
            "job_id": "job-group",
            "params": {
                "seed": 99,
                "multi_clip_info": {"group_id": "group-a", "index": 1, "total": 2},
            },
        }
        self.entries = [
            {"name": "cumulative-a.mp4", "size": 100, "created_at": 1, "meta": sliding},
            {"name": "cumulative-final.mp4", "size": 300, "created_at": 2, "meta": sliding},
            {"name": "clip-a.mp4", "size": 80, "created_at": 3, "meta": component},
            {"name": "joined_multiclip.mp4", "size": 400, "created_at": 4, "meta": grouped_final},
            {"name": "render_tmp.mp4", "size": 10, "created_at": 5, "meta": sliding},
        ]
        self.classes = classify_gallery_artifacts(self.entries)

    def test_multi_window_and_component_classes_are_explicit(self):
        self.assertEqual(self.classes["cumulative-a.mp4"], "window")
        self.assertEqual(self.classes["cumulative-final.mp4"], "final")
        self.assertEqual(self.classes["clip-a.mp4"], "component")
        self.assertEqual(self.classes["joined_multiclip.mp4"], "final")
        self.assertEqual(self.classes["render_tmp.mp4"], "temporary")

    def test_modern_h3_producer_roles_override_stale_or_missing_classes(self):
        success = classify_gallery_artifacts([
            {
                "name": "segment-a.mp4", "meta": {
                    "producer_unit_kind": "h3_segment",
                    "artifact_class": "final",
                },
            },
            {
                "name": "segment-b.mp4", "meta": {
                    "producer_unit_kind": "h3_segment",
                },
            },
            {
                "name": "joined.mp4", "meta": {
                    "producer_unit_kind": "h3_concat",
                    "artifact_class": "component",
                },
            },
        ])
        self.assertEqual(
            {name for name, role in success.items() if role == "final"},
            {"joined.mp4"},
        )
        self.assertEqual(success["segment-a.mp4"], "component")
        self.assertEqual(success["segment-b.mp4"], "component")

        failed_concat = classify_gallery_artifacts([
            {
                "name": "segment-a.mp4", "meta": {
                    "producer_unit_kind": "h3_segment",
                    "artifact_class": "final",
                },
            },
            {
                "name": "segment-b.mp4", "meta": {
                    "producer_unit_kind": "h3_segment",
                },
            },
        ])
        self.assertNotIn("final", failed_concat.values())

    def test_h3_delivery_replacement_is_the_only_final(self):
        classes = classify_gallery_artifacts([
            {
                "name": "native-join.mp4", "meta": {
                    "producer_unit_kind": "h3_concat",
                    "artifact_class": "temporary",
                    "delivery_native_source": True,
                },
            },
            {
                "name": "delivery.mp4", "meta": {
                    "producer_unit_kind": "h3_delivery",
                },
            },
        ])
        self.assertEqual(classes["native-join.mp4"], "temporary")
        self.assertEqual(classes["delivery.mp4"], "final")
        self.assertEqual(
            {name for name, role in classes.items() if role == "final"},
            {"delivery.mp4"},
        )

    def test_default_all_and_components_scopes_never_discard_artifacts(self):
        finals = {name for name, cls in self.classes.items() if artifact_matches_scope(cls, ArtifactScope.FINAL)}
        all_files = {name for name, cls in self.classes.items() if artifact_matches_scope(cls, ArtifactScope.ALL)}
        components = {name for name, cls in self.classes.items() if artifact_matches_scope(cls, ArtifactScope.COMPONENTS)}
        self.assertEqual(finals, {"cumulative-final.mp4", "joined_multiclip.mp4"})
        self.assertEqual(all_files, set(self.classes))
        self.assertEqual(components, set(self.classes) - finals)

    def test_exact_non_final_scopes_are_independently_selectable(self):
        for scope, expected in (
            (ArtifactScope.COMPONENT, {"clip-a.mp4"}),
            (ArtifactScope.WINDOW, {"cumulative-a.mp4"}),
            (ArtifactScope.TEMPORARY, {"render_tmp.mp4"}),
        ):
            with self.subTest(scope=scope):
                self.assertEqual(
                    {
                        name
                        for name, artifact_class in self.classes.items()
                        if artifact_matches_scope(artifact_class, scope)
                    },
                    expected,
                )

    def test_explicit_window_filename_cannot_be_promoted_to_final(self):
        sliding = {
            "job_id": "job-window-names",
            "params": {"seed": 12, "video_length": 241, "sliding_window_size": 81},
        }
        classes = classify_gallery_artifacts([
            {"name": "render-window-1.mp4", "size": 10, "created_at": 1, "meta": sliding},
            {"name": "render-window-2.mp4", "size": 20, "created_at": 2, "meta": sliding},
            {"name": "render-final.mp4", "size": 30, "created_at": 3, "meta": sliding},
        ])
        self.assertEqual(classes["render-window-1.mp4"], "window")
        self.assertEqual(classes["render-window-2.mp4"], "window")
        self.assertEqual(classes["render-final.mp4"], "final")

    def test_cleanup_requires_matching_sidecar_lineage_not_filename(self):
        sidecars = {entry["name"]: entry["meta"] for entry in self.entries}
        sidecars["cumulative-a.mp4"] = dict(
            sidecars["cumulative-a.mp4"], artifact_class="window",
        )
        sidecars["cumulative-final.mp4"] = {
            "job_id": "job-sliding",
            "params": {"seed": 9, "video_length": 241, "sliding_window_size": 81},
        }
        sidecars["cumulative-lookalike-window-9.mp4"] = {
            "job_id": "other-job",
            "params": {"seed": 9},
        }
        classes = dict(self.classes, **{"cumulative-lookalike-window-9.mp4": "window"})
        self.assertEqual(
            linked_component_names("cumulative-final.mp4", sidecars, classes),
            ["cumulative-a.mp4"],
        )

    def test_cleanup_never_trusts_filename_only_nonfinal_classification(self):
        final = {"job_id": "job-safe", "params": {"seed": 4}}
        sliding = {
            "job_id": "job-safe",
            "params": {"seed": 4, "video_length": 241, "sliding_window_size": 81},
        }
        sidecars = {
            "final.mp4": final,
            "legitimate-stitched.mp4": dict(final),
            "legitimate-window-1.mp4": sliding,
            "explicit-temp.mp4": dict(final, artifact_class="temporary"),
        }
        classes = {
            "final.mp4": "final",
            "legitimate-stitched.mp4": "temporary",
            "legitimate-window-1.mp4": "window",
            "explicit-temp.mp4": "temporary",
        }
        self.assertEqual(
            linked_component_names("final.mp4", sidecars, classes),
            ["explicit-temp.mp4"],
        )


class GalleryApiUiContractTests(unittest.TestCase):
    def test_virtual_upload_gallery_never_enters_project_favorites_storage(self):
        launch_path = Path(_APP_DIR) / "launch.py"
        source = launch_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(launch_path))
        nodes = []
        for name in ("list_favorites", "toggle_favorite"):
            node = next(
                item for item in tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            )
            selected = copy.deepcopy(node)
            selected.decorator_list = []
            nodes.append(selected)
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        project_calls = []

        def project_lookup(*args):
            project_calls.append(args)
            raise AssertionError("virtual uploads must not resolve a project")

        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "_get_active_workspace": lambda: "default",
            "_require_project_access": project_lookup,
            "_require_authorized_output": project_lookup,
            "load_media_sidecars": project_lookup,
        }
        exec(compile(module, str(launch_path), "exec"), namespace)
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id="owner"),
        )
        self.assertEqual(
            namespace["list_favorites"](request, "__uploads__"),
            {"favorites": []},
        )
        with self.assertRaises(FakeHTTPException) as raised:
            namespace["toggle_favorite"](
                request, "reference.png", "__uploads__",
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(project_calls, [])

        listing = source[
            source.index('@api.get("/api/v1/outputs")'):
            source.index('@api.get("/api/v1/file/{filename:path}")')
        ]
        self.assertIn('selected_workspace == "__uploads__"', listing)
        self.assertIn("else _load_favorites(selected_workspace)", listing)

    def test_output_delete_names_are_direct_cross_platform_basenames(self):
        for name in ("render.mp4", "clip window 1.webm", "éclair.png"):
            self.assertTrue(is_safe_direct_basename(name), name)
        for name in (
            "", ".", "..", "../victim.mp4", "folder/victim.mp4",
            r"..\victim.mp4", r"C:\outside\victim.mp4", r"folder\victim.mp4",
        ):
            self.assertFalse(is_safe_direct_basename(name), name)

    def test_workspace_names_and_paths_cannot_rebase_output_root(self):
        for name in ("default", "project-7", "Project_Seven", "9"):
            self.assertTrue(is_safe_workspace_name(name), name)
        for name in ("", ".", "..", "../outside", r"..\outside", "/tmp", r"C:\tmp", "a/b"):
            self.assertFalse(is_safe_workspace_name(name), name)
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(safe_join_under(root, "..", "outside.mp4"))

    def test_destructive_direct_path_refuses_symlink_alias(self):
        with tempfile.TemporaryDirectory() as root:
            real = os.path.join(root, "real.mp4")
            alias = os.path.join(root, "alias.mp4")
            _touch_media(root, "real.mp4")
            try:
                os.symlink(real, alias)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertIsNone(safe_direct_file_under(root, "alias.mp4"))
            self.assertEqual(safe_direct_file_under(root, "real.mp4"), real)

    def test_backend_uses_explicit_default_scope_and_contained_cleanup(self):
        with open(os.path.join(_APP_DIR, "launch.py"), "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("artifact_scope: ArtifactScope = ArtifactScope.FINAL", source)
        workspace_helper = source[
            source.index("def _workspace_dir"):source.index("def _workspace_file_count")
        ]
        self.assertIn("is_safe_workspace_name", workspace_helper)
        self.assertIn("safe_join_under(base, ws)", workspace_helper)
        self.assertIn('@api.delete("/api/v1/outputs/{name:path}/components")', source)
        cleanup = source[
            source.index("def _plan_output_component_cleanup"):
            source.index('@api.delete("/api/v1/outputs/{name:path}/components")')
        ]
        self.assertIn("safe_direct_file_under", cleanup)
        self.assertIn("linked_component_names", cleanup)
        self.assertIn("delete_components: bool = False", source)
        self.assertIn("workspace: str | None = None", source)
        delete_route = source.index('@api.delete("/api/v1/outputs/{name}")')
        cascade = source[delete_route:source.index('@api.post("/api/v1/upload")')]
        self.assertIn("filepath = safe_direct_file_under(out_dir, name)", cascade)
        self.assertIn("not is_safe_direct_basename(name)", cascade)
        self.assertIn('detail="Invalid output name"', cascade)
        self.assertIn("_delete_frozen_output_names(", cascade)
        self.assertIn("_output_lineage_mutation_guard(out_dir)", cascade)
        self.assertNotIn("safe_delete(filepath)", cascade)

    def test_producer_never_promotes_filename_hint_to_cleanup_authority(self):
        with open(os.path.join(_APP_DIR, "launch.py"), "r", encoding="utf-8") as handle:
            source = handle.read()
        refresh = source[source.index("# Persist the producer's current artifact view"):source.index("is_multiclip =", source.index("# Persist the producer's current artifact view"))]
        self.assertIn('meta.pop("artifact_class", None)', refresh)
        self.assertIn("_queue_recovery_expected_artifact_role", refresh)
        self.assertIn('elif producer_kind:', refresh)
        self.assertIn('meta.get("producer_temporary") is True', refresh)
        self.assertNotIn("classify_gallery_artifacts", refresh)

    def test_ui_exposes_scope_enum_and_cleanup_action(self):
        with open(os.path.join(_ROOT, "ui", "src", "api", "client.ts"), "r", encoding="utf-8") as handle:
            client = handle.read()
        with open(os.path.join(_ROOT, "ui", "src", "components", "MainContent", "TabFilter.tsx"), "r", encoding="utf-8") as handle:
            tabs = handle.read()
        with open(os.path.join(_ROOT, "ui", "src", "components", "MainContent", "MediaFeedItem.tsx"), "r", encoding="utf-8") as handle:
            item = handle.read()
        self.assertIn("params.set('artifact_scope'", client)
        for label in ("Finals", "All", "Components", "Windows", "Temporary"):
            self.assertIn(f"label: '{label}'", tabs)
        self.assertIn("deleteOutputComponents(file.name, file.workspace)", item)
        self.assertIn("params.set('delete_components', 'true')", client)
        self.assertIn("params.set('workspace', workspace)", client)
        self.assertIn("OutputCleanupResult", client)
        self.assertIn("Cleanup incomplete", item)

    def test_ui_async_operations_keep_exact_output_identity(self):
        with open(os.path.join(_ROOT, "ui", "src", "stores", "useStore.ts"), "r", encoding="utf-8") as handle:
            store = handle.read()
        with open(os.path.join(_ROOT, "ui", "src", "components", "MainContent", "MediaFeedItem.tsx"), "r", encoding="utf-8") as handle:
            item = handle.read()
        with open(os.path.join(_ROOT, "ui", "src", "components", "MainContent", "TabFilter.tsx"), "r", encoding="utf-8") as handle:
            tabs = handle.read()
        self.assertIn("await deleteOutput(file.name, file.workspace)", item)
        self.assertNotIn("deleteTimerRef", item)
        self.assertIn("selectedOutputMetaName", store)
        self.assertIn("_metadataRequestGeneration", store)
        self.assertGreaterEqual(store.count("++_outputsRequestGeneration"), 3)
        self.assertIn("await get().loadOutputs()", store)
        self.assertIn("const identity = privatePreviewIdentity(file.workspace, file.name, file.revision)", (
            Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx"
        ).read_text(encoding="utf-8"))
        self.assertIn("key={identity}", (
            Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx"
        ).read_text(encoding="utf-8"))
        self.assertIn("clearTimeout(debounceRef.current)", tabs)
        self.assertIn("setSearchQuery('')", tabs)

    def test_metadata_filters_are_composed_before_output_pagination(self):
        launch = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        route = launch[
            launch.index('@api.get("/api/v1/outputs")'):
            launch.index('@api.get("/api/v1/file/{filename:path}")')
        ]
        self.assertLess(route.index("sidecar_cache = load_media_sidecars"), route.index("if search:"))
        self.assertLess(route.index("if search:"), route.index("total = len(files)"))
        self.assertLess(route.index("total = len(files)"), route.index("files = files[offset:offset + limit]"))

        tabs = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "TabFilter.tsx").read_text(encoding="utf-8")
        client = (Path(_ROOT) / "ui" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        for label in ("Model contains", "LoRA contains", "Seed", "References", "From date", "Through date"):
            self.assertIn(label, tabs)
        self.assertIn("buildOutputSearchQuery", tabs)
        self.assertIn("OUTPUT_SEARCH_FIELDS", client)

    def test_filter_groups_wrap_whole_and_compact_from_available_width(self):
        tabs = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "TabFilter.tsx").read_text(encoding="utf-8")
        main = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx").read_text(encoding="utf-8")

        self.assertIn("basis-[42rem] flex-wrap", tabs)
        self.assertIn("new ResizeObserver(updateLayout)", tabs)
        self.assertIn("getBoundingClientRect().width < 760", tabs)
        self.assertIn("shortLabel: 'Edits'", tabs)
        self.assertNotIn("overflow-x-auto", tabs)
        self.assertIn("items-start justify-between", main)

    def test_cards_show_lora_and_semantic_reference_provenance(self):
        item = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MediaFeedItem.tsx").read_text(encoding="utf-8")
        self.assertIn("activatedLoras", item)
        self.assertIn("loras_multipliers", item)
        self.assertIn("Semantic references:", item)
        self.assertIn("uploadFilenames?.image_refs", item)
        for key in ("video_guide2", "video_guide3", "video_source"):
            self.assertIn(f"params?.{key}", item)
            self.assertIn(f"uploadFilenames?.{key}", item)
        provenance = item[item.index("const activatedLoras"):item.index("const selectionKey")]
        self.assertNotIn("uploadFilenames?.image_start", provenance)

    def test_failed_card_logs_are_inline_and_backend_ids_are_guarded(self):
        main = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx").read_text(encoding="utf-8")
        client = (Path(_ROOT) / "ui" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        self.assertIn("Generation failed before a server job was created", main)
        self.assertIn("Show recorded events", main)
        self.assertIn("Load job event history", main)
        self.assertIn("if (!api.isBackendJobId(job.id)) return", main)
        self.assertIn("if (!isBackendJobId(jobId))", client)
        self.assertIn("const res = await fetch(`${BASE}/api/v1/jobs/", client)
        self.assertLess(client.index("if (!isBackendJobId(jobId))"), client.index("const res = await fetch(`${BASE}/api/v1/jobs/"))

    def test_gallery_selection_is_cleared_atomically_with_visible_scope_changes(self):
        store = (Path(_ROOT) / "ui" / "src" / "stores" / "useStore.ts").read_text(encoding="utf-8")
        switch_scope = store[store.index("switchWorkspace: async"):store.index("unlockWorkspace: async")]
        self.assertGreaterEqual(switch_scope.count("selectedOutputKeys: []"), 3)

        delete_start = store.index("deleteWorkspace: async")
        delete_scope = store[delete_start:store.index("storageDashboardOpen:", delete_start)]
        self.assertIn("selectedOutputKeys: []", delete_scope)

        for start, end in (
            ("setMediaFilter: (f)", "setOutputArtifactScope:"),
            ("setOutputArtifactScope: (scope)", "setOutputSearchQuery:"),
            ("setOutputSearchQuery: (q)", "filteredOutputs:"),
        ):
            action = store[store.index(start):store.index(end, store.index(start))]
            self.assertIn("selectedOutputKeys: []", action)

        main = (Path(_ROOT) / "ui" / "src" / "components" / "MainContent" / "MainContent.tsx").read_text(encoding="utf-8")
        self.assertNotIn("clearOutputSelection = useStore", main)
        self.assertIn("galleryScopeKey", main)
        self.assertIn("feedEl.scrollTo({ top: 0, behavior: 'auto' })", main)
        self.assertIn("viewportAnchor", main)
        self.assertIn("intraItemOffset", main)


if __name__ == "__main__":
    unittest.main()
