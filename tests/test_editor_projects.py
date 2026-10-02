"""Project isolation and durability for the first Continuum Editor slice."""

from __future__ import annotations

import os
import ast
import asyncio
import copy
import errno
import hashlib
import hmac
import json
import multiprocessing
import re
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from fastapi import HTTPException


_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import editor_projects as editor_project_service  # noqa: E402
from services.editor_projects import (  # noqa: E402
    EditorProjectError,
    apply_output_video_trim,
    append_output_video_clip,
    editor_sequence_clips,
    create_editor_project,
    create_output_video_timeline,
    list_editor_projects,
    load_editor_project,
    resolve_editor_asset,
    save_editor_project,
)


def _competing_editor_save(root, project, barrier, results, index):
    candidate = dict(project, name=f"Concurrent edit {index}")
    barrier.wait(timeout=15)
    try:
        saved = save_editor_project(root, "scene", candidate, expected_revision=1)
        results.put(("saved", saved["revision"], index))
    except EditorProjectError:
        results.put(("conflict", None, index))


class TestEditorProjectFoundation(unittest.TestCase):
    def test_image_pinned_identity_timing_appearance_roundtrip_and_removal(self):
        from services.editor_projects import add_output_image_layer, editor_image_layer
        self._workspace("images")
        current = create_output_video_timeline(workspace="images", output_name="source.mp4", output_revision="video",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        current = add_output_image_layer(current, output_name="logo.png", output_revision="image",
            media={"type": "image", "width": 32, "height": 16})
        current = save_editor_project(self.outputs, "images", current, expected_revision=0)
        proposed = copy.deepcopy(current)
        proposed["assets"]["source-image"]["output_id"] = "foreign.png"
        track = next(t for t in proposed["tracks"] if t["id"] == "images-main")
        track["items"][0].update(start=1, duration=2, size=0.5, opacity=0.25, position="top")
        updated = apply_output_video_trim(current, proposed)
        self.assertEqual(updated["assets"], current["assets"])
        self.assertEqual(updated["tracks"][:3], current["tracks"][:3])
        saved = save_editor_project(self.outputs, "images", updated, expected_revision=1)
        self.assertEqual(editor_image_layer(load_editor_project(self.outputs, "images", saved["id"]), require_fit=True), editor_image_layer(updated))
        for change in ({"size": 1.01}, {"opacity": True}, {"duration": float("nan")}, {"position": "left"},
                       {"start": -1}, {"source_in": 1}, {"asset_id": "source-video"}, {"transform": {"x": 2}}):
            broken = copy.deepcopy(saved); next(t for t in broken["tracks"] if t["id"] == "images-main")["items"][0].update(change)
            with self.subTest(change=change), self.assertRaises(EditorProjectError): apply_output_video_trim(saved, broken)
        for change in ({"start": 3}, {"duration": 1 / 240}):
            late = copy.deepcopy(saved); next(t for t in late["tracks"] if t["id"] == "images-main")["items"][0].update(change)
            late = apply_output_video_trim(saved, late)
            self.assertIsNotNone(editor_image_layer(late))
            with self.assertRaises(EditorProjectError): editor_image_layer(late, require_fit=True)
        unknown_fps = copy.deepcopy(saved)
        unknown_fps["assets"]["source-video"]["fps"] = 0
        next(t for t in unknown_fps["tracks"] if t["id"] == "images-main")["items"][0]["duration"] = 0.25
        self.assertEqual(editor_image_layer(unknown_fps, require_fit=True)["duration"], 0.25)
        removed = copy.deepcopy(saved); next(t for t in removed["tracks"] if t["id"] == "images-main")["items"] = []
        removed = apply_output_video_trim(saved, removed)
        self.assertIsNone(editor_image_layer(removed)); self.assertNotIn("source-image", removed["assets"])
        self.assertEqual(len(editor_sequence_clips(removed)), 1)

    def test_eight_image_rows_overlap_identity_roundtrip_and_independent_removal(self):
        from services.editor_projects import add_output_image_layer, editor_image_layers
        self._workspace("images")
        current = create_output_video_timeline(workspace="images", output_name="source.mp4", output_revision="video",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        for _ in range(8):
            current = add_output_image_layer(current, output_name="same.png", output_revision="image",
                media={"type": "image", "width": 32, "height": 16})
        layers = editor_image_layers(current, require_fit=True)
        self.assertEqual(len({item["id"] for item in layers}), 8)
        self.assertEqual(len({item["asset_id"] for item in layers}), 8)
        with self.assertRaises(EditorProjectError):
            add_output_image_layer(current, output_name="ninth.png", output_revision="image", media={"type": "image", "width": 32, "height": 16})
        proposed = copy.deepcopy(current)
        rows = next(track for track in proposed["tracks"] if track["id"] == "images-main")["items"]
        rows.reverse()
        rows[0].update(start=1, duration=2, opacity=0)
        removed = rows.pop(3)
        updated = apply_output_video_trim(current, proposed)
        self.assertNotIn(removed["asset_id"], updated["assets"])
        self.assertEqual(len(editor_sequence_clips(updated)), 1)
        saved = save_editor_project(self.outputs, "images", updated, expected_revision=0)
        self.assertEqual(editor_image_layers(load_editor_project(self.outputs, "images", saved["id"])), editor_image_layers(updated))
        for key, value in (("id", "forged"), ("asset_id", layers[0]["asset_id"])):
            forged = copy.deepcopy(saved)
            next(track for track in forged["tracks"] if track["id"] == "images-main")["items"][1][key] = value
            with self.assertRaises(EditorProjectError): apply_output_video_trim(saved, forged)
        late = copy.deepcopy(saved)
        next(track for track in late["tracks"] if track["id"] == "images-main")["items"][-1]["start"] = 4
        with self.assertRaises(EditorProjectError): editor_image_layers(late, require_fit=True)

    def test_still_decoder_rejects_animation_and_container_and_applies_orientation(self):
        from services.editor_projects import inspect_editor_still
        from PIL import Image
        path = Path(self.outputs) / "still.png"
        Image.new("RGBA", (12, 8), (255, 0, 0, 128)).save(path)
        self.assertEqual(inspect_editor_still(path)["width"], 12)
        Image.new("RGBA", (12, 8), "red").save(path, save_all=True, append_images=[Image.new("RGBA", (12, 8), "blue")], duration=100)
        with self.assertRaises(EditorProjectError): inspect_editor_still(path)
        jpeg = path.with_suffix(".jpg")
        exif = Image.Exif(); exif[274] = 6
        Image.new("RGB", (12, 8), "red").save(jpeg, exif=exif)
        self.assertEqual((inspect_editor_still(jpeg)["width"], inspect_editor_still(jpeg)["height"]), (8, 12))

    def test_audio_layer_round_trip_trim_offset_gain_mute_and_closed_identity(self):
        from services.editor_projects import add_output_audio_layer, editor_audio_layer
        self._workspace("audio")
        current = create_output_video_timeline(workspace="audio", output_name="source.mp4", output_revision="video",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        current = add_output_audio_layer(current, output_name="score.wav", output_revision="audio",
            media={"type": "audio", "duration": 8, "has_audio": True})
        current = save_editor_project(self.outputs, "audio", current, expected_revision=0)
        proposed = copy.deepcopy(current)
        proposed["assets"]["source-audio"]["output_id"] = "foreign.wav"
        item = proposed["tracks"][1]["items"][0]
        item.update(source_in=2, duration=1.5, start=1, volume=0.25, muted=True)
        updated = apply_output_video_trim(current, proposed)
        self.assertEqual(updated["assets"], current["assets"])
        self.assertEqual(editor_audio_layer(updated, require_fit=True), {
            "id": "audio-layer", "asset_id": "source-audio", "source_in": 2,
            "start": 1, "duration": 1.5, "volume": 0.25, "muted": True})
        saved = save_editor_project(self.outputs, "audio", updated, expected_revision=1)
        self.assertEqual(editor_audio_layer(load_editor_project(self.outputs, "audio", saved["id"])), editor_audio_layer(updated))
        self.assertEqual(len(editor_sequence_clips(saved)), 1)
        for change in ({"volume": 1.01}, {"volume": True}, {"duration": float("nan")}, {"source_in": 7},
                       {"start": -1}, {"muted": 1}, {"asset_id": "source-video"}, {"fade_in": 0.2}):
            broken = copy.deepcopy(saved); broken["tracks"][1]["items"][0].update(change)
            with self.subTest(change=change), self.assertRaises(EditorProjectError):
                apply_output_video_trim(saved, broken)
        late = copy.deepcopy(saved); late["tracks"][1]["items"][0]["start"] = 3
        late = apply_output_video_trim(saved, late)
        self.assertIsNotNone(editor_audio_layer(late))
        with self.assertRaises(EditorProjectError): editor_audio_layer(late, require_fit=True)
        with self.assertRaises(EditorProjectError):
            add_output_audio_layer(saved, output_name="second.wav", output_revision="second", media={"type": "audio", "duration": 1, "has_audio": True})
        removed = copy.deepcopy(saved); removed["tracks"][1]["items"] = []
        removed = apply_output_video_trim(saved, removed)
        self.assertIsNone(editor_audio_layer(removed)); self.assertNotIn("source-audio", removed["assets"])
        self.assertEqual(len(editor_sequence_clips(removed)), 1)

    def test_audio_probe_uses_stream_duration_when_container_duration_absent(self):
        path = os.path.join(self.temp.name, "sound.wav")
        Path(path).write_bytes(b"audio")
        with mock.patch("services.editor_projects.subprocess.run", return_value=types.SimpleNamespace(returncode=0,
                stdout=json.dumps({"streams": [{"codec_type": "audio", "duration": "2.5", "channels": 2, "sample_rate": "48000"}]}))):
            media = editor_project_service.probe_media(path)
        self.assertEqual(media["duration"], 2.5)
        self.assertEqual(media["type"], "audio")

    def test_titles_overlap_round_trip_and_keep_absolute_times_through_trim(self):
        from services.editor_projects import editor_text_layers
        self._workspace("titles")
        project = create_output_video_timeline(workspace="titles", output_name="source.mp4", output_revision="source",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        proposed = copy.deepcopy(project)
        proposed["tracks"][2]["items"] = [
            {"id": "one", "text": "Adult fiction: blood & love [v]; 'literal'", "start": 0.5, "duration": 2, "position": "top"},
            {"id": "two", "text": "Line one\nLine two", "start": 1, "duration": 2, "position": "bottom"},
        ]
        updated = apply_output_video_trim(project, proposed)
        saved = save_editor_project(self.outputs, "titles", updated, expected_revision=0)
        reopened = load_editor_project(self.outputs, "titles", saved["id"])
        self.assertEqual(editor_text_layers(reopened, require_fit=True), proposed["tracks"][2]["items"])
        self.assertEqual(len(editor_sequence_clips(reopened)), 1)
        trimmed = copy.deepcopy(reopened)
        trimmed["tracks"][0]["items"][0]["duration"] = 1
        short = apply_output_video_trim(reopened, trimmed)
        self.assertEqual(editor_text_layers(short), proposed["tracks"][2]["items"])
        with self.assertRaises(EditorProjectError):
            editor_text_layers(short, require_fit=True)
        removed = copy.deepcopy(short)
        removed["tracks"][2]["items"] = []
        self.assertEqual(editor_text_layers(apply_output_video_trim(short, removed), require_fit=True), [])

    def test_titles_reject_invalid_shapes_and_unsupported_effects(self):
        project = create_output_video_timeline(workspace="scene", output_name="source.mp4", output_revision="source",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        title = {"id": "one", "text": "TITLE", "start": 0, "duration": 2, "position": "center"}
        for change in ({"text": "x" * 161}, {"text": "a\nb\nc\nd"}, {"text": "\x00"},
                       {"start": float("nan")}, {"duration": True}, {"position": "../path"},
                       {"fade_in": 1}, {"style": {"font_family": "/host/font.ttf"}}, {"opacity": 0.5}):
            proposed = copy.deepcopy(project)
            proposed["tracks"][2]["items"] = [{**title, **change}]
            with self.subTest(change=change), self.assertRaises(EditorProjectError):
                apply_output_video_trim(project, proposed)

    def test_subframe_title_can_be_saved_but_cannot_be_exported(self):
        from services.editor_projects import editor_text_layers
        project = create_output_video_timeline(workspace="scene", output_name="source.mp4", output_revision="source",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        proposed = copy.deepcopy(project)
        proposed["tracks"][2]["items"] = [{"id": "short", "text": "TITLE", "start": 0.01,
            "duration": 0.01, "position": "center"}]
        updated = apply_output_video_trim(project, proposed)
        self.assertEqual(len(editor_text_layers(updated)), 1)
        with self.assertRaisesRegex(EditorProjectError, "one video frame"):
            editor_text_layers(updated, require_fit=True)
        proposed["tracks"][2]["items"][0]["duration"] = 1 / 24
        self.assertEqual(len(editor_text_layers(apply_output_video_trim(project, proposed), require_fit=True)), 1)

    def test_malformed_proposed_tracks_return_a_project_error(self):
        project = create_output_video_timeline(workspace="scene", output_name="source.mp4", output_revision="source",
            media={"type": "video", "duration": 4, "width": 128, "height": 72, "fps": 24})
        for tracks in (None, {}, [None]):
            with self.subTest(tracks=tracks), self.assertRaises(EditorProjectError):
                apply_output_video_trim(project, {**project, "tracks": tracks})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outputs = os.path.join(self.temp.name, "outputs")
        self.uploads = os.path.join(self.temp.name, "uploads")
        for directory in (self.outputs, self.uploads):
            os.mkdir(directory)

    def _workspace(self, name: str) -> str:
        path = os.path.join(self.outputs, name)
        os.mkdir(path)
        return path

    def test_output_timeline_reopens_by_exact_revision_and_saves_only_trim(self):
        self._workspace("scene")
        media = {"type": "video", "duration": 20.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}
        first = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="a-b.c-d", media=media,
        )
        same = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="a-b.c-d", media=media,
        )
        changed = create_output_video_timeline(
            workspace="scene", output_name="clip.mp4", output_revision="new-revision", media=media,
        )
        self.assertEqual(first["id"], same["id"])
        self.assertNotEqual(first["id"], changed["id"])
        first = save_editor_project(self.outputs, "scene", first, expected_revision=0)
        proposed = dict(first, name="Forged name", assets={"source-video": {"path": "/private"}})
        proposed["tracks"] = [{
            "id": "video-main", "items": [{"source_in": 2.0, "duration": 11.0}],
        }]
        updated = apply_output_video_trim(first, proposed)
        self.assertEqual((updated["name"], updated["assets"]), (first["name"], first["assets"]))
        self.assertEqual(updated["tracks"][0]["items"][0]["source_in"], 2.0)
        self.assertEqual(updated["tracks"][0]["items"][0]["duration"], 11.0)
        with self.assertRaisesRegex(EditorProjectError, "valid source range"):
            apply_output_video_trim(first, dict(proposed, tracks=[{
                "id": "video-main", "items": [{"source_in": 19.0, "duration": 4.0}],
            }]))

    def test_sequence_reorder_trim_keeps_server_assets_and_rejects_identity_changes(self):
        self._workspace("scene")
        media = {"type": "video", "duration": 3.0, "width": 127, "height": 71, "fps": 24}
        first = create_output_video_timeline(workspace="scene", output_name="first.mp4", output_revision="first", media=media)
        sequence = append_output_video_clip(first, output_name="second.mp4", output_revision="second", media=media)
        self.assertEqual((sequence["canvas"]["width"], sequence["canvas"]["height"]), (128, 72))
        proposed = copy.deepcopy(sequence)
        proposed["tracks"][0]["items"].reverse()
        proposed["tracks"][0]["items"][0].update(source_in=0.5, duration=1.25, start=999, asset_id="forged")
        updated = apply_output_video_trim(sequence, proposed)
        pairs = editor_sequence_clips(updated)
        self.assertEqual([asset["output_id"] for asset, _ in pairs], ["second.mp4", "first.mp4"])
        self.assertEqual([clip["start"] for _, clip in pairs], [0, 1.25])
        self.assertEqual(updated["assets"], sequence["assets"])
        saved = save_editor_project(self.outputs, "scene", updated, expected_revision=0)
        self.assertEqual(len(editor_sequence_clips(load_editor_project(self.outputs, "scene", saved["id"]))), 2)
        for mode in ("duplicate", "empty", "unknown"):
            invalid = copy.deepcopy(proposed)
            items = invalid["tracks"][0]["items"]
            if mode == "duplicate":
                items[1]["id"] = items[0]["id"]
            elif mode == "empty":
                items.clear()
            else:
                items[0]["id"] = "unknown"
            with self.subTest(mode=mode), self.assertRaises(EditorProjectError):
                apply_output_video_trim(sequence, invalid)
        with self.assertRaisesRegex(EditorProjectError, "already"):
            append_output_video_clip(sequence, output_name="second.mp4", output_revision="second", media=media)
        for index in range(6):
            sequence = append_output_video_clip(sequence, output_name=f"more-{index}.mp4", output_revision="revision", media=media)
        with self.assertRaisesRegex(EditorProjectError, "eight"):
            append_output_video_clip(sequence, output_name="overflow.mp4", output_revision="revision", media=media)

    def test_remove_original_and_appended_clips_preserves_anchor_layers_and_cas(self):
        from services.editor_projects import add_output_audio_layer, add_output_image_layer, editor_audio_layer, editor_image_layer, editor_text_layers
        self._workspace("scene")
        media = {"type": "video", "duration": 3, "width": 128, "height": 72, "fps": 24}
        first = create_output_video_timeline(workspace="scene", output_name="first.mp4", output_revision="first", media=media)
        first.pop("opening_source")  # Legacy draft upgrades on its first save.
        current = append_output_video_clip(first, output_name="second.mp4", output_revision="second", media=media)
        current = append_output_video_clip(current, output_name="third.mp4", output_revision="third", media=media)
        current["tracks"][2]["items"] = [{"id": "title", "text": "Kept", "start": 5, "duration": 1, "position": "top"}]
        current = add_output_audio_layer(current, output_name="sound.wav", output_revision="audio", media={"type":"audio", "duration":10, "has_audio":True})
        current = add_output_image_layer(current, output_name="still.png", output_revision="image", media={"type":"image", "width":16, "height":8, "has_audio":False})
        original_layers = (editor_text_layers(current), editor_audio_layer(current), editor_image_layer(current))
        proposed = copy.deepcopy(current)
        proposed["tracks"][0]["items"].pop(0)
        proposed["opening_source"] = {"output_id":"forged"}
        remaining = apply_output_video_trim(current, proposed)
        self.assertEqual([asset["output_id"] for asset, _ in editor_sequence_clips(remaining)], ["second.mp4", "third.mp4"])
        self.assertNotIn("source-video", remaining["assets"])
        self.assertEqual(remaining["opening_source"]["output_id"], "first.mp4")
        self.assertEqual(remaining["canvas"], current["canvas"])
        self.assertEqual((editor_text_layers(remaining), editor_audio_layer(remaining), editor_image_layer(remaining)), original_layers)
        saved = save_editor_project(self.outputs, "scene", remaining, expected_revision=0)
        proposed = copy.deepcopy(saved); proposed["tracks"][0]["items"].pop()
        last = apply_output_video_trim(saved, proposed)
        self.assertEqual(last["opening_source"], remaining["opening_source"])
        self.assertEqual([clip["start"] for _, clip in editor_sequence_clips(last)], [0])
        with self.assertRaises(EditorProjectError):
            editor_image_layer(last, require_fit=True)
        save_editor_project(self.outputs, "scene", last, expected_revision=saved["revision"])
        with self.assertRaises(EditorProjectError):
            save_editor_project(self.outputs, "scene", remaining, expected_revision=saved["revision"])
        proposed["tracks"].append(copy.deepcopy(proposed["tracks"][0]))
        with self.assertRaises(EditorProjectError):
            apply_output_video_trim(saved, proposed)

    def test_round_trip_and_stale_autosave_cannot_overwrite_newer_edit(self):
        self._workspace("scene-a")
        project = create_editor_project(name="My cut", workspace="scene-a")
        created = save_editor_project(self.outputs, "scene-a", project, expected_revision=0)
        self.assertEqual(created["revision"], 1)
        stale = dict(created)
        changed = dict(created, name="Second edit")
        saved = save_editor_project(self.outputs, "scene-a", changed, expected_revision=1)
        self.assertEqual(saved["revision"], 2)
        with self.assertRaisesRegex(EditorProjectError, "reload"):
            save_editor_project(self.outputs, "scene-a", stale, expected_revision=1)
        loaded = load_editor_project(self.outputs, "scene-a", created["id"])
        self.assertEqual((loaded["name"], loaded["revision"]), ("Second edit", 2))
        self.assertEqual(list_editor_projects(self.outputs, "scene-a")[0]["name"], "Second edit")
        self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(
            os.path.join(self.outputs, "scene-a", ".maestro_editor")
        )))

    def test_simultaneous_process_saves_accept_only_one_revision(self):
        self._workspace("scene")
        first = save_editor_project(
            self.outputs, "scene", create_editor_project(workspace="scene"),
            expected_revision=0,
        )
        context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
        barrier = context.Barrier(8)
        results = context.Queue()
        processes = [
            context.Process(
                target=_competing_editor_save,
                args=(self.outputs, first, barrier, results, index),
            )
            for index in range(8)
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
            self.assertEqual([process.exitcode for process in processes], [0] * 8)
            outcomes = [results.get(timeout=2) for _ in processes]
            winners = [item for item in outcomes if item[0] == "saved"]
            self.assertEqual(len(winners), 1, outcomes)
            self.assertEqual([item[0] for item in outcomes].count("conflict"), 7)
            self.assertEqual(winners[0][1], 2)
            final = load_editor_project(self.outputs, "scene", first["id"])
            self.assertEqual((final["revision"], final["name"]), (
                2, f"Concurrent edit {winners[0][2]}",
            ))
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(2)
            results.close()
            results.join_thread()

    def test_windows_lock_retries_temporary_contention(self):
        self._workspace("scene")
        operations = []
        windows_lock = types.SimpleNamespace(LK_LOCK=1, LK_UNLCK=2)

        def locking(_descriptor, operation, _length):
            operations.append(operation)
            if operations == [windows_lock.LK_LOCK]:
                raise OSError(errno.EACCES, "another save holds the lock")

        windows_lock.locking = locking
        with mock.patch.dict(sys.modules, {"msvcrt": windows_lock}):
            with mock.patch.object(editor_project_service.os, "name", "nt"):
                saved = save_editor_project(
                    self.outputs, "scene", create_editor_project(workspace="scene"),
                    expected_revision=0,
                )
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(operations, [
            windows_lock.LK_LOCK, windows_lock.LK_LOCK, windows_lock.LK_UNLCK,
        ])

    def test_project_switch_rejects_stale_document_and_source(self):
        source_a = self._workspace("scene-a")
        source_b = self._workspace("scene-b")
        media_a = os.path.join(source_a, "clip.mp4")
        with open(media_a, "wb") as handle:
            handle.write(b"clip")
        with open(os.path.join(source_b, "clip.mp4"), "wb") as handle:
            handle.write(b"different clip")
        project = create_editor_project(workspace="scene-a")
        with self.assertRaisesRegex(EditorProjectError, "different workspace"):
            save_editor_project(self.outputs, "scene-b", project, expected_revision=0)
        with self.assertRaisesRegex(EditorProjectError, "different workspace"):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output", "workspace": "scene-a"},
                save_root=self.outputs, workspace="scene-b", uploads_root=self.uploads,
            )
        with self.assertRaises(EditorProjectError):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output", "path": media_a},
                save_root=self.outputs, workspace="scene-b", uploads_root=self.uploads,
            )
        self.assertEqual(resolve_editor_asset(
            {"name": "clip.mp4", "origin": "output", "workspace": "scene-a"},
            save_root=self.outputs, workspace="scene-a", uploads_root=self.uploads,
        ), media_a)

    def test_saved_asset_uses_opaque_output_id_without_client_host_path(self):
        self._workspace("scene")
        project = create_editor_project(workspace="scene")
        project["assets"]["clip"] = {
            "id": "clip", "name": "clip.mp4", "type": "video",
            "origin": "output", "workspace": "scene", "output_id": "unit-clip",
            "output_revision": "sha256:abc123",
            "path": "/private/other-user/clip.mp4", "url": "https://example.invalid/secret",
        }
        saved = save_editor_project(self.outputs, "scene", project, expected_revision=0)
        self.assertEqual(saved["assets"]["clip"]["output_id"], "unit-clip")
        self.assertEqual(saved["assets"]["clip"]["output_revision"], "sha256:abc123")
        self.assertNotIn("path", saved["assets"]["clip"])
        self.assertNotIn("url", saved["assets"]["clip"])
        self.assertEqual(
            load_editor_project(self.outputs, "scene", saved["id"])["assets"]["clip"],
            saved["assets"]["clip"],
        )

    def test_links_and_global_upload_fallback_do_not_cross_project_boundary(self):
        scene = self._workspace("scene")
        outside = os.path.join(self.temp.name, "private.mp4")
        with open(outside, "wb") as handle:
            handle.write(b"private")
        os.symlink(outside, os.path.join(scene, "clip.mp4"))
        with self.assertRaises(EditorProjectError):
            resolve_editor_asset(
                {"name": "clip.mp4", "origin": "output"},
                save_root=self.outputs, workspace="scene", uploads_root=self.uploads,
            )
        with open(os.path.join(self.uploads, "private.wav"), "wb") as handle:
            handle.write(b"private")
        with self.assertRaisesRegex(EditorProjectError, "Upload import"):
            resolve_editor_asset(
                {"name": "private.wav", "origin": "upload"},
                save_root=self.outputs, workspace="scene", uploads_root=self.uploads,
            )
        os.symlink(self.temp.name, os.path.join(self.outputs, "linked"))
        with self.assertRaises(EditorProjectError):
            save_editor_project(
                self.outputs, "linked", create_editor_project(workspace="linked"),
                expected_revision=0,
            )

    def test_project_file_symlink_cannot_be_read_or_followed_on_save(self):
        scene = self._workspace("scene")
        directory = os.path.join(scene, ".maestro_editor")
        os.mkdir(directory)
        outside = os.path.join(self.temp.name, "private.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write('{"name":"private"}')
        project = create_editor_project(workspace="scene")
        os.symlink(outside, os.path.join(directory, project["id"] + ".json"))
        with self.assertRaises(EditorProjectError):
            load_editor_project(self.outputs, "scene", project["id"])
        with self.assertRaises(EditorProjectError):
            save_editor_project(self.outputs, "scene", project, expected_revision=0)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])


class _EditorRequest:
    def __init__(self, body):
        self.body = body

    async def stream(self):
        yield json.dumps(self.body).encode("utf-8")


class TestEditorProjectRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outputs = os.path.join(self.temp.name, "outputs")
        self.scene = os.path.join(self.outputs, "scene")
        os.makedirs(self.scene)
        self.clip = os.path.join(self.scene, "clip.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"fixture")
        self.revision = "a-b.c-d"
        self.private = False
        self.permissions = []
        launch_path = Path(__file__).resolve().parents[1] / "app" / "launch.py"
        wanted = {
            "_editor_request_body", "_editor_save_root",
            "_editor_require_current_source", "open_output_editor_project",
            "save_output_editor_project", "serve_file",
        }
        selected = []
        for node in ast.parse(launch_path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
                cloned = copy.deepcopy(node)
                cloned.decorator_list = []
                selected.append(cloned)
        self.assertEqual({node.name for node in selected}, wanted)
        module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))

        def authorize(_request, project, *, existing_only=False, permission=None):
            self.permissions.append((project, existing_only, permission))
            if project != "scene":
                raise HTTPException(status_code=403, detail="Project access denied")
            return self.scene

        def output(_request, project, name):
            authorize(_request, project, existing_only=True, permission="project.mutate")
            if name != "clip.mp4":
                raise HTTPException(status_code=404, detail="Output not found")
            return self.scene, self.clip, {"private": self.private}

        namespace = {
            "Request": object,
            "HTTPException": HTTPException,
            "json": json,
            "hmac": hmac,
            "re": re,
            "os": os,
            "subprocess": subprocess,
            "wgp": types.SimpleNamespace(server_config={"save_path": self.outputs}),
            "_reserve_workspace_operations": lambda _project: nullcontext(),
            "_require_project_access": authorize,
            "_require_authorized_output": output,
            "_output_revision": lambda *_args: self.revision,
            "_output_share_revision": lambda *_args: "sha256:" + hashlib.sha256(
                Path(self.clip).read_bytes() + (b"private" if self.private else b""),
            ).hexdigest(),
            "_request_project_workspace": lambda _request, workspace: workspace,
            "public_output_policy": lambda sidecar: {"private": bool(sidecar.get("private"))},
            "_GALLERY_MEDIA_EXTENSIONS": {".mp4"},
        }
        exec(compile(module, str(launch_path), "exec"), namespace)
        self.routes = namespace

    def test_reopen_original_gallery_video_after_its_clip_is_removed(self):
        media = {"type":"video", "duration":3, "width":128, "height":72, "fps":24}
        request = _EditorRequest({"output_name":"clip.mp4", "output_revision":self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        second = Path(self.scene) / "second.mp4"; second.write_bytes(b"second")
        revision = "sha256:" + hashlib.sha256(second.read_bytes()).hexdigest()
        sequence = append_output_video_clip(opened, output_name=second.name, output_revision=revision, media=media)
        sequence = save_editor_project(self.outputs, "scene", sequence, expected_revision=opened["revision"])
        self.routes["_require_authorized_output"] = lambda _r, _p, name: (self.scene, str(Path(self.scene)/name), {"private":False})
        self.routes["_output_share_revision"] = lambda path, *_a: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        proposed = copy.deepcopy(sequence); proposed["tracks"][0]["items"].pop(0)
        proposed["opening_source"] = {"output_id":"forged.mp4"}
        saved = asyncio.run(self.routes["save_output_editor_project"]("scene", sequence["id"],
            _EditorRequest({"project":proposed,"expected_revision":sequence["revision"]})))["project"]
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            reopened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        self.assertEqual((reopened["id"],reopened["revision"]), (saved["id"],saved["revision"]))
        self.assertNotIn("source-video", reopened["assets"])
        self.assertEqual(reopened["opening_source"], opened["opening_source"])
        self.assertEqual([asset["output_id"] for asset,_ in editor_sequence_clips(reopened)], [second.name])
        self.assertTrue(Path(self.clip).exists())

    def test_open_replay_trim_and_stale_or_foreign_mutation(self):
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value=media):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
            replay = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        self.assertEqual((opened["id"], opened["revision"]), (replay["id"], replay["revision"]))
        self.assertEqual(opened["revision"], 1)
        self.assertEqual(opened["assets"]["source-video"]["output_revision"],
                         "sha256:" + hashlib.sha256(b"fixture").hexdigest())
        self.assertFalse(opened["assets"]["source-video"]["private"])
        proposed = copy.deepcopy(opened)
        proposed["name"] = "Forged title"
        proposed["assets"]["source-video"]["name"] = "other.mp4"
        clip = proposed["tracks"][0]["items"][0]
        clip.update(source_in=2.0, duration=6.0)
        save_request = _EditorRequest({"project": proposed, "expected_revision": 1})
        saved = asyncio.run(self.routes["save_output_editor_project"]("scene", opened["id"], save_request))["project"]
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["name"], opened["name"])
        self.assertEqual(saved["assets"]["source-video"]["name"], "clip.mp4")
        self.assertEqual(saved["tracks"][0]["items"][0]["source_in"], 2.0)
        with self.assertRaises(HTTPException) as stale:
            asyncio.run(self.routes["save_output_editor_project"]("scene", opened["id"], save_request))
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(self.routes["open_output_editor_project"]("other", request))
        self.assertEqual(foreign.exception.status_code, 403)
        with open(self.clip, "wb") as handle:
            handle.write(b"changed")
        with self.assertRaises(HTTPException) as changed:
            asyncio.run(self.routes["save_output_editor_project"](
                "scene", saved["id"], _EditorRequest({"project": saved, "expected_revision": 2}),
            ))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertTrue(all(permission == "project.mutate" for _, _, permission in self.permissions))

    def test_invalid_video_is_uneditable_not_a_stale_conflict(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value={
            "type": "video", "duration": 0,
        }):
            with self.assertRaises(HTTPException) as invalid:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(invalid.exception.status_code, 422)

    def test_replacement_during_probe_cannot_create_draft_with_stale_media_or_privacy(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }

        def replace_during_probe(_path):
            self.private = True
            with open(self.clip, "wb") as handle:
                handle.write(b"changed")
            return media

        with mock.patch("services.editor_projects.probe_media", side_effect=replace_during_probe):
            with self.assertRaises(HTTPException) as changed:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])

    def test_privacy_change_during_probe_does_not_create_draft(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})

        def change_privacy(_path):
            self.private = True
            return {
                "type": "video", "duration": 12.0, "width": 1280,
                "height": 720, "fps": 30.0, "has_audio": True,
            }

        with mock.patch("services.editor_projects.probe_media", side_effect=change_privacy):
            with self.assertRaises(HTTPException) as changed:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
        self.assertEqual(changed.exception.status_code, 409)
        self.assertEqual(list_editor_projects(self.outputs, "scene"), [])

    def test_changed_while_hashing_is_a_stale_source_conflict(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        media = {
            "type": "video", "duration": 12.0, "width": 1280,
            "height": 720, "fps": 30.0, "has_audio": True,
        }
        real_revision = self.routes["_output_share_revision"]

        def interrupted_hash(*_args):
            raise OSError("Output changed while reading")

        try:
            self.routes["_output_share_revision"] = interrupted_hash
            with self.assertRaises(HTTPException) as opening:
                asyncio.run(self.routes["open_output_editor_project"]("scene", request))
            self.assertEqual(opening.exception.status_code, 409)
        finally:
            self.routes["_output_share_revision"] = real_revision

        with mock.patch("services.editor_projects.probe_media", return_value=media):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        try:
            self.routes["_output_share_revision"] = interrupted_hash
            with self.assertRaises(HTTPException) as saving:
                asyncio.run(self.routes["save_output_editor_project"](
                    "scene", opened["id"], _EditorRequest({
                        "project": opened, "expected_revision": opened["revision"],
                    }),
                ))
            self.assertEqual(saving.exception.status_code, 409)
            with self.assertRaises(HTTPException) as preview:
                self.routes["serve_file"](
                    request, "clip.mp4", workspace="scene",
                    content_revision=opened["assets"]["source-video"]["output_revision"],
                )
            self.assertEqual(preview.exception.status_code, 409)
        finally:
            self.routes["_output_share_revision"] = real_revision

    def test_preview_rejects_same_name_replacement_even_when_gallery_token_is_unchanged(self):
        request = _EditorRequest({"output_name": "clip.mp4", "output_revision": self.revision})
        with mock.patch("services.editor_projects.probe_media", return_value={
            "type": "video", "duration": 12.0, "width": 1280, "height": 720,
            "fps": 30.0, "has_audio": True,
        }):
            opened = asyncio.run(self.routes["open_output_editor_project"]("scene", request))["project"]
        original_revision = opened["assets"]["source-video"]["output_revision"]
        self.private = True
        with open(self.clip, "wb") as handle:
            handle.write(b"replace")
        with self.assertRaises(HTTPException) as stale_preview:
            self.routes["serve_file"](
                request, "clip.mp4", workspace="scene", content_revision=original_revision,
            )
        self.assertEqual(stale_preview.exception.status_code, 409)
        with self.assertRaises(HTTPException) as stale_save:
            asyncio.run(self.routes["save_output_editor_project"](
                "scene", opened["id"], _EditorRequest({
                    "project": opened, "expected_revision": opened["revision"],
                }),
            ))
        self.assertEqual(stale_save.exception.status_code, 409)

    def test_oversized_editor_payload_is_rejected_before_parse(self):
        with self.assertRaises(HTTPException) as oversized:
            asyncio.run(self.routes["_editor_request_body"](_EditorRequest({"blob": "x" * 70000})))
        self.assertEqual(oversized.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
