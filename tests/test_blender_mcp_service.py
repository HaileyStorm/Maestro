"""Fully offline tests for the bounded official Blender MCP integration."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from app.services.blender_mcp_service import (
    ALLOWED_TOOLS,
    ANIMATE_KEYFRAMES,
    EXECUTE_BLENDER_CODE,
    GET_OBJECTS_SUMMARY,
    INSPECT_SCENE,
    PINNED_INSTALL,
    PUBLIC_TOOL_SCHEMAS,
    PUBLIC_TOOLS,
    RENDER_ANIMATION,
    RENDER_PREVIEW,
    RENDER_THUMBNAIL_TO_PATH,
    SCENE_CREATE,
    UPSTREAM_TOOL_ALLOWLIST,
    BlenderMCPCancelled,
    BlenderMCPClient,
    BlenderMCPLimits,
    BlenderMCPSecurityError,
    BlenderMCPService,
    BlenderMCPToolError,
    BlenderMCPValidationError,
    attest_blender_executable,
    attest_mcp_checkout,
    discover_blender_runtimes,
    _encode_png_sequence_to_mp4,
    provision_discovered_blender_runtime,
    quarantine_invalid_mcp_checkout,
    read_blender_runtime_info,
    reuse_compatible_mcp_checkout,
)
from app.services.blender_mcp_transport import StdioBlenderMCPClient

_FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42offline-video"


def _render_path_from_code(code: str) -> Path:
    match = re.search(r"^scene\.render\.filepath = (.+)$", code, re.MULTILINE)
    if match is None:
        raise AssertionError("render_animation code has no output path")
    return Path(ast.literal_eval(match.group(1)))


def _attestation(scratch: Path) -> dict[str, Any]:
    return {
        "repository": PINNED_INSTALL.repository,
        "tag": PINNED_INSTALL.tag,
        "revision": PINNED_INSTALL.revision,
        "package_version": PINNED_INSTALL.package_version,
        "license": PINNED_INSTALL.license,
        "transport": "stdio",
        "bridge_host": "localhost",
        "bridge_port": 9876,
        "blender_version": "5.1.0",
        "server_name": "blender-mcp",
        "tools": sorted(UPSTREAM_TOOL_ALLOWLIST),
        "probe_tool": GET_OBJECTS_SUMMARY,
        "probe_ok": True,
        "scratch_root": str(scratch.resolve()),
    }


class FakeClient(BlenderMCPClient):
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.security = _attestation(scratch)
        self.connect_count = 0
        self.close_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: dict[str, int] = {}
        self.response_override: Any = None

    def connect(self, *, cancelled: Any = None) -> dict[str, Any]:
        del cancelled
        self.connect_count += 1
        return dict(self.security)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        cancelled: Any = None,
    ) -> dict[str, Any]:
        del cancelled
        self.calls.append((name, dict(arguments)))
        remaining = self.failures.get(name, 0)
        if remaining:
            self.failures[name] = remaining - 1
            raise RuntimeError(f"forced {name} failure")
        if self.response_override is not None:
            return self.response_override
        if name == RENDER_THUMBNAIL_TO_PATH:
            path = self.scratch / arguments["output_path"]
            path.write_bytes(b"\x89PNG\r\n\x1a\noffline-preview")
            return {"status": "ok", "result": {"status": "ok", "filepath": str(path)}}
        if name == GET_OBJECTS_SUMMARY:
            return {"status": "ok", "result": {"status": "ok", "collections": []}}
        if name == "get_object_detail_summary":
            return {
                "status": "ok",
                "result": {"status": "ok", "name": arguments["name"], "type": "MESH"},
            }
        if name == EXECUTE_BLENDER_CODE:
            code = arguments["code"]
            if "# Maestro deterministic render_animation v1" in code:
                prefix = _render_path_from_code(code)
                start = int(re.search(r"^scene\.frame_start = (\d+)$", code, re.MULTILINE).group(1))
                end = int(re.search(r"^scene\.frame_end = (\d+)$", code, re.MULTILINE).group(1))
                for frame in range(start, end + 1):
                    Path(f"{prefix}{frame:04d}.png").write_bytes(
                        b"\x89PNG\r\n\x1a\noffline-frame"
                    )
            return {"status": "ok", "result": {"status": "ok"}}
        raise AssertionError(f"unexpected upstream call: {name}")

    def close(self) -> None:
        self.close_count += 1


def _scene() -> dict[str, Any]:
    return {
        "clear_scene": True,
        "objects": [
            {
                "name": "HeroCube",
                "primitive": "cube",
                "location": [0, 0, 1],
                "rotation_degrees": [0, 0, 45],
                "scale": [1, 2, 1],
                "material": {
                    "name": "HeroBlue",
                    "color": [0.1, 0.2, 0.9, 1.0],
                },
            }
        ],
    }


def _animation() -> dict[str, Any]:
    return {
        "frame_start": 1,
        "frame_end": 21,
        "objects": [
            {
                "name": "HeroCube",
                "keyframes": [
                    {"frame": 1, "location": [0, 0, 1], "interpolation": "LINEAR"},
                    {"frame": 21, "location": [4, 0, 1], "interpolation": "LINEAR"},
                ],
            }
        ],
    }


class TestBlenderMCPService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.scratch = self.root / "mock_blender_scratch"
        self.scratch.mkdir()
        self.client = FakeClient(self.scratch)
        self.encoder = mock.patch(
            "app.services.blender_mcp_service._encode_png_sequence_to_mp4",
            side_effect=lambda _prefix, output, **_kwargs: output.write_bytes(_FAKE_MP4),
        )
        self.encoder.start()
        self.addCleanup(self.encoder.stop)
        self.service = BlenderMCPService(
            self.client,
            self.root,
            sleeper=lambda _seconds: None,
        )

    def test_official_install_metadata_is_exact(self):
        self.assertEqual(PINNED_INSTALL.repository, "https://projects.blender.org/lab/blender_mcp.git")
        self.assertEqual(PINNED_INSTALL.tag, "v1.0.0")
        self.assertEqual(PINNED_INSTALL.revision, "03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4")
        self.assertEqual(PINNED_INSTALL.package_version, "1.0.0")
        self.assertEqual(PINNED_INSTALL.license, "GPL-3.0-or-later")
        self.assertEqual(PINNED_INSTALL.blender_min_version, "5.1.0")

    def _fake_blender(self, version: str = "5.2.1") -> Path:
        binary = self.root / "pinokio" / "Blender" / "blender"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(f"#!/bin/sh\nprintf 'Blender {version}\\n'\n", encoding="utf-8")
        binary.chmod(0o755)
        return binary

    def _fake_mcp_checkout(self) -> Path:
        checkout = self.root / "pinokio" / "api" / "Blender.git" / "app" / "services" / "blender_mcp"
        (checkout / "mcp" / "blmcp").mkdir(parents=True)
        (checkout / "mcp" / "blmcp" / "__init__.py").write_text("", encoding="utf-8")
        (checkout / "addon" / "blender_mcp_addon").mkdir(parents=True)
        (checkout / "addon" / "blender_mcp_addon" / "blender_manifest.toml").write_text(
            "schema_version = '1.0.0'\n", encoding="utf-8"
        )
        return checkout

    def test_blender_runtime_discovery_attests_version_hash_and_permissions(self):
        binary = self._fake_blender()
        runtimes = discover_blender_runtimes([self.root / "pinokio"])
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0]["binary"], str(binary.resolve()))
        self.assertEqual(runtimes[0]["version"], "5.2.1")
        self.assertRegex(runtimes[0]["executable_sha256"], r"^[0-9a-f]{64}$")

        binary.chmod(0o777)
        with self.assertRaises(BlenderMCPSecurityError):
            attest_blender_executable(binary)

    def test_blender_runtime_rejects_old_version_and_tampered_marker(self):
        old_binary = self._fake_blender("5.0.9")
        with self.assertRaises(BlenderMCPValidationError):
            attest_blender_executable(old_binary)

        binary = self._fake_blender("5.1.3")
        executable = attest_blender_executable(binary)
        marker = self.root / "tools" / "blender" / "runtime.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps(
                {
                    **executable,
                    "source": "external",
                    "transport": "stdio",
                    "bridge_host": "localhost",
                    "bridge_port": 9876,
                    "mcp_revision": PINNED_INSTALL.revision,
                    "user_home": str(marker.parent / "home"),
                }
            ),
            encoding="utf-8",
        )
        status = read_blender_runtime_info(marker)
        self.assertTrue(status["external"])
        self.assertEqual(status["source"], "external")
        self.assertEqual(status["transport"], "stdio")

        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["transport"] = "http"
        marker.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(read_blender_runtime_info(marker), {})

        payload["transport"] = "stdio"
        payload["user_home"] = str(self.root.parent / "outside")
        marker.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(read_blender_runtime_info(marker), {})

    def test_exact_official_mcp_checkout_is_attested_and_marked_for_reuse(self):
        checkout = self._fake_mcp_checkout()
        answers = {
            ("rev-parse", "HEAD"): PINNED_INSTALL.revision,
            ("describe", "--tags", "--exact-match", "HEAD"): PINNED_INSTALL.tag,
            ("remote", "get-url", "origin"): PINNED_INSTALL.repository,
            ("status", "--porcelain", "--untracked-files=no"): "",
        }
        with mock.patch(
            "app.services.blender_mcp_service._git_output",
            side_effect=lambda _checkout, *arguments: answers[arguments],
        ):
            facts = attest_mcp_checkout(checkout)
            reused = reuse_compatible_mcp_checkout(checkout)
        self.assertEqual(facts["transport"], "stdio")
        self.assertEqual(reused, checkout.resolve())
        marker = json.loads((checkout / ".maestro-attested").read_text(encoding="utf-8"))
        self.assertEqual(marker["revision"], PINNED_INSTALL.revision)
        self.assertEqual(marker["repository"], PINNED_INSTALL.repository)

    def test_compatible_pinokio_checkout_is_cloned_locally_before_network_fallback(self):
        source = self._fake_mcp_checkout()
        target = self.root / "maestro" / "services" / "blender_mcp"
        facts = {
            "repository": PINNED_INSTALL.repository,
            "tag": PINNED_INSTALL.tag,
            "revision": PINNED_INSTALL.revision,
            "package_version": PINNED_INSTALL.package_version,
            "transport": PINNED_INSTALL.transport,
            "checkout": str(source),
        }
        clone_commands: list[list[str]] = []

        def clone(command: list[str], **_kwargs: Any) -> SimpleNamespace:
            clone_commands.append(command)
            shutil.copytree(source, Path(command[-1]))
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with (
            mock.patch(
                "app.services.blender_mcp_service.discover_compatible_mcp_checkouts",
                return_value=[facts],
            ),
            mock.patch(
                "app.services.blender_mcp_service.attest_mcp_checkout",
                return_value=facts,
            ),
            mock.patch("app.services.blender_mcp_service.subprocess.run", side_effect=clone),
            mock.patch("app.services.blender_mcp_service._git_output") as git_output,
        ):
            reused = reuse_compatible_mcp_checkout(target)
        self.assertEqual(reused, target.resolve())
        self.assertTrue((target / "mcp" / "blmcp" / "__init__.py").is_file())
        self.assertEqual(len(clone_commands), 1)
        self.assertIn("--local", clone_commands[0])
        self.assertIn("--no-hardlinks", clone_commands[0])
        git_output.assert_any_call(
            mock.ANY, "remote", "set-url", "origin", PINNED_INSTALL.repository
        )
        self.assertEqual(
            json.loads((target / ".maestro-attested").read_text(encoding="utf-8"))["transport"],
            "stdio",
        )

    def test_incomplete_mcp_checkout_is_quarantined_for_retryable_repair(self):
        target = self.root / "services" / "blender_mcp"
        target.mkdir(parents=True)
        partial = target / "partial-download"
        partial.write_bytes(b"preserve until replacement is verified")

        quarantine = quarantine_invalid_mcp_checkout(target)

        self.assertIsNotNone(quarantine)
        self.assertFalse(target.exists())
        self.assertEqual(
            (quarantine / "partial-download").read_bytes(),
            b"preserve until replacement is verified",
        )
        self.assertRegex(quarantine.name, r"^\.blender_mcp\.invalid-[0-9a-f]{32}$")

    def test_discovered_runtime_provisioning_uses_only_fixed_extension_commands(self):
        checkout = self._fake_mcp_checkout()
        binary = self._fake_blender("5.1.4")
        executable = attest_blender_executable(binary)
        marker = self.root / "tools" / "blender" / "runtime.json"
        commands: list[list[str]] = []

        def run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
            commands.append(command)
            if command[-1:] == ["--version"]:
                return SimpleNamespace(stdout="Blender 5.1.4\n", stderr="", returncode=0)
            output = next((item.split("=", 1)[1] for item in command if item.startswith("--output-dir=")), None)
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                (Path(output) / "mcp-1.0.0.zip").write_bytes(b"extension")
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        with (
            mock.patch("app.services.blender_mcp_service.attest_mcp_checkout"),
            mock.patch(
                "app.services.blender_mcp_service.discover_blender_runtimes",
                return_value=[executable],
            ),
            mock.patch("app.services.blender_mcp_service.subprocess.run", side_effect=run),
        ):
            status = provision_discovered_blender_runtime(checkout, marker)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertTrue(status["external"])
        self.assertEqual(status["transport"], "stdio")
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[-1], [str(binary.resolve()), "--version"])
        self.assertNotIn("--python-expr", " ".join(part for command in commands for part in command))
        self.assertNotIn("--python", " ".join(part for command in commands for part in command))

    def test_public_surface_is_exact_and_schemas_are_closed(self):
        self.assertEqual(
            PUBLIC_TOOLS,
            {
                SCENE_CREATE,
                ANIMATE_KEYFRAMES,
                RENDER_PREVIEW,
                RENDER_ANIMATION,
                INSPECT_SCENE,
            },
        )
        self.assertEqual(ALLOWED_TOOLS, PUBLIC_TOOLS)
        self.assertEqual(set(PUBLIC_TOOL_SCHEMAS), PUBLIC_TOOLS)
        self.assertTrue(all(schema["additionalProperties"] is False for schema in PUBLIC_TOOL_SCHEMAS.values()))
        self.assertEqual(len(UPSTREAM_TOOL_ALLOWLIST), 26)
        self.assertIn(EXECUTE_BLENDER_CODE, UPSTREAM_TOOL_ALLOWLIST)
        keyframe_schema = PUBLIC_TOOL_SCHEMAS[ANIMATE_KEYFRAMES]["properties"]["objects"]["items"]["properties"]["keyframes"]["items"]
        self.assertEqual(len(keyframe_schema["anyOf"]), 3)
        scale_items = PUBLIC_TOOL_SCHEMAS[SCENE_CREATE]["$defs"]["positiveVector3"]["items"]
        self.assertEqual(scale_items, {"type": "number", "minimum": 0.0001, "maximum": 10000})
        render_schema = PUBLIC_TOOL_SCHEMAS[RENDER_PREVIEW]
        self.assertIn("[Pp][Nn][Gg]", render_schema["properties"]["output_path"]["pattern"])
        self.assertEqual(render_schema["not"], {"required": ["frame", "frames"]})
        self.assertEqual(render_schema["properties"]["frame"]["maximum"], 1_000_000)
        self.assertEqual(render_schema["properties"]["frames"]["minItems"], 2)
        self.assertEqual(render_schema["properties"]["frames"]["maxItems"], 32)
        self.assertTrue(render_schema["properties"]["frames"]["uniqueItems"])
        animation_schema = PUBLIC_TOOL_SCHEMAS[RENDER_ANIMATION]
        self.assertEqual(
            animation_schema["required"],
            ["output_path", "frame_start", "frame_end", "fps"],
        )
        self.assertIn("[Mm][Pp]4", animation_schema["properties"]["output_path"]["pattern"])
        self.assertEqual(animation_schema["properties"]["fps"]["minimum"], 1)
        self.assertEqual(animation_schema["properties"]["fps"]["maximum"], 240)
        self.assertFalse(animation_schema["properties"]["overwrite"]["default"])

    def test_client_is_lazy_and_connection_attestation_is_cached(self):
        self.assertEqual(self.client.connect_count, 0)
        self.service.inspect_scene({})
        self.service.inspect_scene({})
        self.assertEqual(self.client.connect_count, 1)
        self.assertEqual(self.service.attestation["server_name"], "blender-mcp")
        self.service.close()
        self.assertEqual(self.client.close_count, 1)

    def test_each_project_activates_a_distinct_fixed_hidden_blend_scene(self):
        other_root = self.root / "other-project"
        other_root.mkdir()
        first_client = FakeClient(self.scratch)
        second_client = FakeClient(self.scratch)
        first = BlenderMCPService(first_client, self.root)
        second = BlenderMCPService(second_client, other_root)

        first.inspect_scene({})
        second.inspect_scene({})

        first_path = self.root / ".maestro_blender_scene.blend"
        second_path = other_root / ".maestro_blender_scene.blend"
        self.assertEqual(first.scene_path, first_path)
        self.assertEqual(second.scene_path, second_path)
        self.assertNotEqual(first.scene_path, second.scene_path)
        first_code = first_client.calls[0][1]["code"]
        second_code = second_client.calls[0][1]["code"]
        self.assertIn("# Maestro deterministic activate_project_scene v1", first_code)
        self.assertIn("bpy.ops.wm.open_mainfile", first_code)
        self.assertIn("bpy.ops.wm.read_homefile(use_empty=True)", first_code)
        self.assertIn("bpy.ops.wm.save_as_mainfile", first_code)
        self.assertIn(repr(str(first_path)), first_code)
        self.assertIn(repr(str(second_path)), second_code)
        self.assertNotIn(str(second_path), first_code)
        self.assertNotIn(str(first_path), second_code)

    def test_project_scene_path_rejects_a_symlink(self):
        outside = self.root / "outside.blend"
        outside.write_bytes(b"BLENDER")
        linked_root = self.root / "linked-project"
        linked_root.mkdir()
        (linked_root / ".maestro_blender_scene.blend").symlink_to(outside)
        with self.assertRaises(BlenderMCPSecurityError):
            BlenderMCPService(FakeClient(self.scratch), linked_root)

    def test_attestation_fails_closed_without_calling_tools(self):
        mismatches = {
            "repository": "https://example.invalid/wrong.git",
            "tag": "v1.0.1",
            "revision": "0" * 40,
            "transport": "http",
            "bridge_host": "0.0.0.0",
            "bridge_port": 8000,
            "blender_version": "5.0.9",
            "server_name": "not-blender",
            "probe_ok": False,
        }
        for key, value in mismatches.items():
            with self.subTest(key=key):
                client = FakeClient(self.scratch)
                client.security[key] = value
                service = BlenderMCPService(client, self.root)
                with self.assertRaises(BlenderMCPSecurityError):
                    service.inspect_scene({})
                self.assertEqual(client.calls, [])

        client = FakeClient(self.scratch)
        client.security["tools"] = [GET_OBJECTS_SUMMARY]
        with self.assertRaises(BlenderMCPSecurityError):
            BlenderMCPService(client, self.root).inspect_scene({})
        self.assertEqual(client.calls, [])

    def test_arbitrary_tools_and_caller_code_are_never_public(self):
        with self.assertRaises(BlenderMCPSecurityError):
            self.service.invoke(EXECUTE_BLENDER_CODE, {"code": "import os"})
        with self.assertRaises(BlenderMCPValidationError):
            self.service.scene_create({"objects": [], "code": "import os"})
        malicious = _scene()
        malicious["objects"][0]["name"] = "Cube'); __import__('os').system('x') #"
        with self.assertRaises(BlenderMCPValidationError):
            self.service.scene_create(malicious)
        self.assertEqual(self.client.calls, [])

    def test_scene_create_uses_only_controlled_deterministic_code(self):
        result = self.service.scene_create(_scene())
        self.assertEqual(result["created"], ["HeroCube"])
        self.assertEqual(
            [name for name, _args in self.client.calls],
            [
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
            ],
        )
        self.assertIn(
            "# Maestro deterministic activate_project_scene v1",
            self.client.calls[0][1]["code"],
        )
        code = self.client.calls[1][1]["code"]
        self.assertIn("# Maestro deterministic scene_create v1", code)
        self.assertIn('obj.name = item["name"]', code)
        self.assertNotIn("__import__", code)
        self.assertNotIn("open(", code)
        self.assertIn(
            "# Maestro deterministic save_project_scene v1",
            self.client.calls[3][1]["code"],
        )
        setup_code = self.client.calls[2][1]["code"]
        self.assertIn("# Maestro deterministic render_scene_setup v1", setup_code)
        self.assertIn('if scene.camera is None:', setup_code)
        self.assertIn('if cameras:', setup_code)
        self.assertIn('if not lights:', setup_code)
        self.assertIn('camera.location = (8.0, -8.0, 6.0)', setup_code)
        self.assertIn('light_data.energy = 1200.0', setup_code)
        self.assertIn('scene.camera = cameras[0]', setup_code)
        self.assertNotIn("open(", setup_code)

    def test_scene_create_validates_all_values_before_connecting(self):
        cases = []
        bad = _scene()
        bad["objects"][0]["primitive"] = "arbitrary"
        cases.append(bad)
        bad = _scene()
        bad["objects"][0]["scale"] = [1, 0, 1]
        cases.append(bad)
        bad = _scene()
        bad["objects"][0]["material"]["color"] = [1, 0, 0]
        cases.append(bad)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(BlenderMCPValidationError):
                self.service.scene_create(value)

        conflicting = _scene()
        second = dict(conflicting["objects"][0])
        second["name"] = "SecondCube"
        second["material"] = {"name": "HeroBlue", "color": [1, 0, 0, 1]}
        conflicting["objects"].append(second)
        with self.assertRaises(BlenderMCPValidationError):
            self.service.scene_create(conflicting)
        self.assertEqual(self.client.connect_count, 0)

    def test_animation_is_bounded_and_compiles_controlled_code(self):
        result = self.service.animate_keyframes(_animation())
        self.assertEqual(result["frame_range"], {"start": 1, "end": 21})
        code = self.client.calls[1][1]["code"]
        self.assertIn("# Maestro deterministic animate_keyframes v1", code)
        self.assertIn("obj.keyframe_insert", code)
        self.assertIn("action.fcurve_ensure_for_datablock", code)
        self.assertNotIn("action.fcurves", code)
        self.assertIn(
            "# Maestro deterministic save_project_scene v1",
            self.client.calls[2][1]["code"],
        )

        too_long = _animation()
        too_long["frame_end"] = 7201
        with self.assertRaises(BlenderMCPValidationError):
            BlenderMCPService(FakeClient(self.scratch), self.root).animate_keyframes(too_long)
        duplicate = _animation()
        duplicate["objects"][0]["keyframes"][1]["frame"] = 1
        with self.assertRaises(BlenderMCPValidationError):
            BlenderMCPService(FakeClient(self.scratch), self.root).animate_keyframes(duplicate)

    def test_inspection_activates_scene_then_uses_bounded_summary_retries(self):
        self.client.failures[GET_OBJECTS_SUMMARY] = 2
        result = self.service.inspect_scene({"objects": ["HeroCube"]})
        self.assertEqual(result["objects"]["HeroCube"]["type"], "MESH")
        tools = [name for name, _args in self.client.calls]
        self.assertEqual(tools[0], EXECUTE_BLENDER_CODE)
        self.assertEqual(tools.count(GET_OBJECTS_SUMMARY), 3)
        self.assertEqual(tools[-1], "get_object_detail_summary")

    def test_cancellation_stops_before_connection_or_next_detail(self):
        with self.assertRaises(BlenderMCPCancelled):
            self.service.inspect_scene({}, cancelled=lambda: True)
        self.assertEqual(self.client.connect_count, 0)

        checks = iter([False, False, True])
        service = BlenderMCPService(FakeClient(self.scratch), self.root)
        with self.assertRaises(BlenderMCPCancelled):
            service.inspect_scene(
                {"objects": ["HeroCube", "SecondCube"]},
                cancelled=lambda: next(checks, True),
            )

    def test_render_preview_copies_trusted_scratch_result_into_project(self):
        result = self.service.render_preview({"output_path": "previews/hero.png"})
        destination = self.root / "previews" / "hero.png"
        self.assertEqual(result["output_path"], str(destination.resolve()))
        self.assertEqual(destination.read_bytes(), b"\x89PNG\r\n\x1a\noffline-preview")
        tool, arguments = self.client.calls[0]
        self.assertEqual(tool, EXECUTE_BLENDER_CODE)
        self.assertIn("activate_project_scene v1", arguments["code"])
        tool, arguments = self.client.calls[1]
        self.assertEqual(tool, EXECUTE_BLENDER_CODE)
        self.assertIn("render_scene_setup v1", arguments["code"])
        tool, arguments = self.client.calls[2]
        self.assertEqual(tool, RENDER_THUMBNAIL_TO_PATH)
        self.assertRegex(arguments["output_path"], r"^maestro_[0-9a-f]{32}\.png$")
        self.assertNotIn(str(self.root), arguments["output_path"])

    def test_render_preview_single_frame_uses_constant_owned_code(self):
        result = self.service.render_preview(
            {"output_path": "previews/hero.png", "frame": 12}
        )

        destination = self.root / "previews" / "hero.png"
        self.assertEqual(
            result,
            {"status": "ok", "output_path": str(destination), "frame": 12},
        )
        self.assertEqual(
            [name for name, _arguments in self.client.calls],
            [
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                RENDER_THUMBNAIL_TO_PATH,
            ],
        )
        self.assertIn("render_scene_setup v1", self.client.calls[1][1]["code"])
        code = self.client.calls[2][1]["code"]
        self.assertIn("# Maestro deterministic render_preview frame v1", code)
        self.assertIn("scene.frame_set(12)", code)
        self.assertNotIn("json", code)
        self.assertTrue(destination.is_file())

    def test_render_preview_multiple_frames_normalizes_order_and_names_outputs(self):
        result = self.service.render_preview(
            {"output_path": "previews/hero.png", "frames": [12, 1, 6]}
        )

        expected = [
            {"frame": frame, "output_path": str(self.root / "previews" / name)}
            for frame, name in (
                (1, "hero_f000001.png"),
                (6, "hero_f000006.png"),
                (12, "hero_f000012.png"),
            )
        ]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["outputs"], expected)
        self.assertEqual(
            result["output_paths"],
            [item["output_path"] for item in expected],
        )
        self.assertEqual(
            [name for name, _arguments in self.client.calls],
            [
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                EXECUTE_BLENDER_CODE,
                RENDER_THUMBNAIL_TO_PATH,
                EXECUTE_BLENDER_CODE,
                RENDER_THUMBNAIL_TO_PATH,
                EXECUTE_BLENDER_CODE,
                RENDER_THUMBNAIL_TO_PATH,
            ],
        )
        self.assertIn("render_scene_setup v1", self.client.calls[1][1]["code"])
        frame_codes = self.client.calls[2::2]
        for expected_frame, (_tool, arguments) in zip((1, 6, 12), frame_codes, strict=True):
            self.assertIn(f"scene.frame_set({expected_frame})", arguments["code"])
        self.assertTrue(all(Path(item["output_path"]).is_file() for item in expected))

    def test_render_preview_frame_selection_is_bounded_and_preflighted(self):
        invalid = [
            {"frame": 1, "frames": [1, 2]},
            {"frame": True},
            {"frame": -1},
            {"frame": 1_000_001},
            {"frames": [1]},
            {"frames": list(range(33))},
            {"frames": [1, 1]},
            {"frames": [1, False]},
            {"frames": [1, 1_000_001]},
        ]
        for selection in invalid:
            with self.subTest(selection=selection):
                client = FakeClient(self.scratch)
                service = BlenderMCPService(client, self.root)
                with self.assertRaises(BlenderMCPValidationError):
                    service.render_preview(
                        {"output_path": "previews/hero.png", **selection}
                    )
                self.assertEqual(client.connect_count, 0)
                self.assertEqual(client.calls, [])

        occupied = self.root / "previews" / "hero_f000002.png"
        occupied.parent.mkdir()
        occupied.write_bytes(b"keep")
        client = FakeClient(self.scratch)
        with self.assertRaises(BlenderMCPValidationError):
            BlenderMCPService(client, self.root).render_preview(
                {"output_path": "previews/hero.png", "frames": [1, 2]}
            )
        self.assertEqual(occupied.read_bytes(), b"keep")
        self.assertEqual(client.connect_count, 0)
        self.assertEqual(client.calls, [])

    def test_render_preview_rejects_escape_extension_and_overwrite(self):
        outside = self.root.parent / "escape.png"
        with self.assertRaises(BlenderMCPSecurityError):
            self.service.render_preview({"output_path": str(outside)})
        with self.assertRaises(BlenderMCPValidationError):
            self.service.render_preview({"output_path": "preview.py"})
        with self.assertRaises(BlenderMCPValidationError):
            self.service.render_preview({"output_path": "preview.jpg"})
        existing = self.root / "existing.png"
        existing.write_bytes(b"keep")
        with self.assertRaises(BlenderMCPValidationError):
            self.service.render_preview({"output_path": existing})
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(self.client.calls, [])

    def test_render_preview_rejects_non_png_and_destination_race(self):
        class BadImageClient(FakeClient):
            def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
                response = super().call_tool(name, arguments, **kwargs)
                if name == RENDER_THUMBNAIL_TO_PATH:
                    Path(response["result"]["filepath"]).write_bytes(b"not-a-png")
                return response

        bad_destination = self.root / "bad.png"
        with self.assertRaises(BlenderMCPToolError):
            BlenderMCPService(BadImageClient(self.scratch), self.root).render_preview(
                {"output_path": bad_destination}
            )
        self.assertFalse(bad_destination.exists())

        class RacingClient(FakeClient):
            def call_tool(self, name: str, arguments: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
                response = super().call_tool(name, arguments, **kwargs)
                if name == RENDER_THUMBNAIL_TO_PATH:
                    (self.scratch.parent / "race.png").write_bytes(b"other-writer")
                return response

        race_destination = self.root / "race.png"
        with self.assertRaises(BlenderMCPValidationError):
            BlenderMCPService(RacingClient(self.scratch), self.root).render_preview(
                {"output_path": race_destination}
            )
        self.assertEqual(race_destination.read_bytes(), b"other-writer")

    def test_render_animation_renders_exact_full_range_to_attested_scratch(self):
        result = self.service.invoke(
            RENDER_ANIMATION,
            {
                "output_path": "renders/hero.mp4",
                "frame_start": 3,
                "frame_end": 48,
                "fps": 24,
            },
        )

        destination = self.root / "renders" / "hero.mp4"
        self.assertEqual(
            result,
            {
                "status": "ok",
                "output_path": str(destination),
                "frame_range": {"start": 3, "end": 48},
                "fps": 24,
            },
        )
        self.assertEqual(destination.read_bytes(), _FAKE_MP4)
        self.assertEqual(
            [name for name, _arguments in self.client.calls],
            [EXECUTE_BLENDER_CODE, EXECUTE_BLENDER_CODE, EXECUTE_BLENDER_CODE],
        )
        activation_code = self.client.calls[0][1]["code"]
        self.assertIn("# Maestro deterministic activate_project_scene v1", activation_code)
        self.assertIn(str(self.root / ".maestro_blender_scene.blend"), activation_code)
        self.assertIn("render_scene_setup v1", self.client.calls[1][1]["code"])
        code = self.client.calls[2][1]["code"]
        self.assertIn("# Maestro deterministic render_animation v1", code)
        self.assertIn("scene.frame_start = 3", code)
        self.assertIn("scene.frame_end = 48", code)
        self.assertIn("scene.render.fps = 24", code)
        self.assertIn("scene.render.fps_base = 1.0", code)
        self.assertIn('scene.render.image_settings.file_format = "PNG"', code)
        self.assertIn("bpy.ops.render.render(animation=True)", code)
        frame_prefix = _render_path_from_code(code)
        self.assertEqual(frame_prefix.parent.parent, self.scratch)
        self.assertRegex(frame_prefix.parent.name, r"^maestro_frames_[0-9a-f]{32}$")
        self.assertEqual(frame_prefix.name, "frame_")
        self.assertNotIn(str(destination), code)

    def test_managed_ffmpeg_encodes_a_valid_full_rate_png_sequence(self):
        from PIL import Image

        frame_directory = self.scratch / "blender_mcp"
        frame_directory.mkdir()
        prefix = frame_directory / "frame_"
        for frame, color in ((1, (255, 0, 0)), (2, (0, 0, 255))):
            Image.new("RGB", (16, 16), color).save(f"{prefix}{frame:04d}.png")
        output = self.scratch / "encoded.mp4"
        _encode_png_sequence_to_mp4(
            prefix,
            output,
            frame_start=1,
            frame_end=2,
            fps=24,
            max_input_bytes=1024 * 1024,
        )
        header = output.read_bytes()[:16]
        self.assertGreaterEqual(len(header), 12)
        self.assertEqual(header[4:8], b"ftyp")

    def test_render_animation_validates_path_range_fps_and_caller_fields(self):
        invalid = [
            {"output_path": "renders/hero.mov", "frame_start": 1, "frame_end": 2, "fps": 24},
            {"output_path": "renders/hero.mp4", "frame_start": -1, "frame_end": 2, "fps": 24},
            {"output_path": "renders/hero.mp4", "frame_start": 2, "frame_end": 1, "fps": 24},
            {"output_path": "renders/hero.mp4", "frame_start": 0, "frame_end": 7200, "fps": 24},
            {"output_path": "renders/hero.mp4", "frame_start": 1, "frame_end": 2, "fps": 0},
            {"output_path": "renders/hero.mp4", "frame_start": 1, "frame_end": 2, "fps": 241},
            {"output_path": "renders/hero.mp4", "frame_start": 1, "frame_end": 2, "fps": True},
            {"output_path": "renders/hero.mp4", "frame_start": 1, "frame_end": 2, "fps": 24, "code": "import os"},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                client = FakeClient(self.scratch)
                service = BlenderMCPService(client, self.root)
                with self.assertRaises(BlenderMCPValidationError):
                    service.render_animation(arguments)
                self.assertEqual(client.connect_count, 0)
                self.assertEqual(client.calls, [])

        outside = self.root.parent / "escape.mp4"
        with self.assertRaises(BlenderMCPSecurityError):
            self.service.render_animation(
                {
                    "output_path": outside,
                    "frame_start": 1,
                    "frame_end": 2,
                    "fps": 24,
                }
            )
        self.assertEqual(self.client.calls, [])

    def test_render_animation_honors_cancellation_and_overwrite_policy(self):
        arguments = {
            "output_path": "renders/hero.mp4",
            "frame_start": 1,
            "frame_end": 2,
            "fps": 24,
        }
        with self.assertRaises(BlenderMCPCancelled):
            self.service.render_animation(arguments, cancelled=lambda: True)
        self.assertEqual(self.client.connect_count, 0)

        destination = self.root / "renders" / "hero.mp4"
        destination.parent.mkdir()
        destination.write_bytes(b"keep")
        client = FakeClient(self.scratch)
        with self.assertRaises(BlenderMCPValidationError):
            BlenderMCPService(client, self.root).render_animation(arguments)
        self.assertEqual(destination.read_bytes(), b"keep")
        self.assertEqual(client.calls, [])

        overwrite_client = FakeClient(self.scratch)
        BlenderMCPService(overwrite_client, self.root).render_animation(
            {**arguments, "overwrite": True}
        )
        self.assertEqual(destination.read_bytes(), _FAKE_MP4)

        destination.unlink()
        checks = iter([False, False, False, False, True])
        cancelled_client = FakeClient(self.scratch)
        with self.assertRaises(BlenderMCPCancelled):
            BlenderMCPService(cancelled_client, self.root).render_animation(
                arguments,
                cancelled=lambda: next(checks, True),
            )
        self.assertEqual(
            [name for name, _arguments in cancelled_client.calls],
            [EXECUTE_BLENDER_CODE, EXECUTE_BLENDER_CODE],
        )
        self.assertFalse(destination.exists())

    def test_render_animation_rejects_invalid_oversized_and_racing_outputs(self):
        arguments = {
            "output_path": "renders/hero.mp4",
            "frame_start": 1,
            "frame_end": 2,
            "fps": 24,
        }

        destination = self.root / "renders" / "hero.mp4"
        with (
            mock.patch(
                "app.services.blender_mcp_service._encode_png_sequence_to_mp4",
                side_effect=lambda _prefix, output, **_kwargs: output.write_bytes(b"not-an-mp4"),
            ),
            self.assertRaises(BlenderMCPToolError),
        ):
            BlenderMCPService(FakeClient(self.scratch), self.root).render_animation(arguments)
        self.assertFalse(destination.exists())

        with self.assertRaises(BlenderMCPToolError):
            BlenderMCPService(
                FakeClient(self.scratch),
                self.root,
                limits=BlenderMCPLimits(max_video_bytes=len(_FAKE_MP4) - 1),
            ).render_animation(arguments)
        self.assertFalse(destination.exists())

        def racing_encoder(_prefix, output, **_kwargs):
            output.write_bytes(_FAKE_MP4)
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(b"other-writer")

        with (
            mock.patch(
                "app.services.blender_mcp_service._encode_png_sequence_to_mp4",
                side_effect=racing_encoder,
            ),
            self.assertRaises(BlenderMCPValidationError),
        ):
            BlenderMCPService(FakeClient(self.scratch), self.root).render_animation(arguments)
        self.assertEqual(destination.read_bytes(), b"other-writer")

    def test_invalid_and_oversized_responses_are_rejected(self):
        self.client.response_override = "not-json"
        with self.assertRaises(BlenderMCPToolError):
            self.service.inspect_scene({})

        client = FakeClient(self.scratch)
        client.response_override = {"payload": "x" * 100}
        service = BlenderMCPService(
            client,
            self.root,
            limits=BlenderMCPLimits(max_response_bytes=20),
        )
        with self.assertRaises(BlenderMCPToolError):
            service.inspect_scene({})

    def test_stdio_transport_construction_is_lazy_and_sdk_free(self):
        client = StdioBlenderMCPClient(
            checkout_root=self.root / "not-yet-installed",
            blender_version="5.1.0",
        )
        self.assertIsNone(client._thread)
        self.assertIsNone(client._attestation)

    def test_stdio_transport_attests_blender_reported_scratch_root(self):
        scratch = self.root / "blender_mcp"
        scratch.mkdir(mode=0o700)
        payload = {
            "structuredContent": {
                "result": {
                    "status": "ok",
                    "scratch_root": str(scratch),
                }
            }
        }
        self.assertEqual(
            StdioBlenderMCPClient._attest_scratch_root(payload),
            scratch.resolve(),
        )
        for invalid in (
            {"structuredContent": {"result": {"status": "error"}}},
            {"structuredContent": {"result": {"status": "ok", "scratch_root": "relative"}}},
            {"structuredContent": {"result": {"status": "ok", "scratch_root": str(self.root)}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(BlenderMCPSecurityError):
                StdioBlenderMCPClient._attest_scratch_root(invalid)

    def test_stdio_transport_lifecycle_owns_contexts_and_restricts_environment(self):
        task_ids: list[tuple[str, int]] = []
        captured: dict[str, Any] = {}
        behavior = {"delay_initialize": False}

        class AsyncContext:
            def __init__(self, value: Any, label: str) -> None:
                self.value = value
                self.label = label

            async def __aenter__(self) -> Any:
                task_ids.append((f"enter-{self.label}", id(__import__("asyncio").current_task())))
                return self.value

            async def __aexit__(self, *_args: object) -> None:
                task_ids.append((f"exit-{self.label}", id(__import__("asyncio").current_task())))

        class FakeSession:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> Any:
                task_ids.append(("enter-session", id(__import__("asyncio").current_task())))
                return self

            async def __aexit__(self, *_args: object) -> None:
                task_ids.append(("exit-session", id(__import__("asyncio").current_task())))

            async def initialize(self) -> Any:
                if behavior["delay_initialize"]:
                    await __import__("asyncio").sleep(10)
                return SimpleNamespace(serverInfo=SimpleNamespace(name="blender-mcp"))

            async def list_tools(self) -> Any:
                return SimpleNamespace(
                    tools=[SimpleNamespace(name=name) for name in UPSTREAM_TOOL_ALLOWLIST]
                )

            async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
                if arguments.get("delay"):
                    await __import__("asyncio").sleep(10)
                return SimpleNamespace(
                    isError=False,
                    structuredContent={"result": {"status": "ok", "name": name, "arguments": arguments}},
                )

        def parameters(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        fake_mcp = SimpleNamespace(
            ClientSession=FakeSession,
            StdioServerParameters=parameters,
        )
        fake_stdio = SimpleNamespace(
            get_default_environment=lambda: {"PATH": "/safe/bin"},
            stdio_client=lambda _params: AsyncContext((object(), object()), "stdio"),
        )

        client = StdioBlenderMCPClient(
            checkout_root=self.root,
            blender_version="5.1.0",
            scratch_root=self.scratch,
        )
        with (
            mock.patch.object(client, "_validate_launcher_facts"),
            mock.patch.object(client, "_verify_checkout"),
            mock.patch(
                "app.services.blender_mcp_transport.importlib.import_module",
                side_effect=lambda name: fake_mcp if name == "mcp" else fake_stdio,
            ),
            mock.patch.dict(os.environ, {"MAESTRO_SECRET_TOKEN": "do-not-forward"}),
        ):
            attestation = client.connect()
            response = client.call_tool(GET_OBJECTS_SUMMARY, {})
            checks = iter([False, False, True])
            with self.assertRaises(BlenderMCPCancelled):
                client.call_tool(
                    GET_OBJECTS_SUMMARY,
                    {"delay": True},
                    cancelled=lambda: next(checks, True),
                )
            client.close()
            first_owner_tasks = {task_id for _label, task_id in task_ids}

            behavior["delay_initialize"] = True
            cold_client = StdioBlenderMCPClient(
                checkout_root=self.root,
                blender_version="5.1.0",
                scratch_root=self.scratch,
            )
            with (
                mock.patch.object(cold_client, "_validate_launcher_facts"),
                mock.patch.object(cold_client, "_verify_checkout"),
            ):
                cold_checks = iter([False, False, True])
                with self.assertRaises(BlenderMCPCancelled):
                    cold_client.connect(cancelled=lambda: next(cold_checks, True))
            self.assertTrue(
                cold_client._thread is None or not cold_client._thread.is_alive()
            )

        self.assertTrue(attestation["probe_ok"])
        self.assertIn("structuredContent", response)
        self.assertEqual(captured["env"]["PATH"], "/safe/bin")
        self.assertEqual(captured["env"]["PYTHONPATH"], str(self.root / "mcp"))
        self.assertNotIn("MAESTRO_SECRET_TOKEN", captured["env"])
        self.assertEqual(len(first_owner_tasks), 1)


if __name__ == "__main__":
    unittest.main()
