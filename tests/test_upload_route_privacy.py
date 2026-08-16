import ast
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
LAUNCH_PATH = APP_ROOT / "launch.py"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.output_access import (  # noqa: E402
    can_access_upload,
    write_upload_access_sidecar,
)


def _function_source(name: str) -> str:
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(nodes) != 1:
        raise AssertionError(f"Expected one function named {name}, found {len(nodes)}")
    return ast.get_source_segment(source, nodes[0]) or ""


class _HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AuthorizedMediaResolverTests(unittest.TestCase):
    def _load_resolver(self, output_root: str):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in ast.walk(tree)
            if isinstance(item, ast.FunctionDef)
            and item.name == "_resolve_authorized_request_media"
        )
        node.body = [
            statement for statement in node.body
            if not isinstance(statement, ast.ImportFrom)
        ]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)

        def require_output(request, workspace, name):
            path = os.path.join(output_root, name)
            if not os.path.isfile(path):
                raise _HTTPException(404)
            if (
                not getattr(request.state, "project_unlocked", True)
                or workspace != getattr(request.state, "project_workspace", "default")
            ):
                raise _HTTPException(423)
            return output_root, path, {"private": True}

        def request_project_workspace(request, workspace):
            if getattr(request.state, "maestro_remote", False) and not workspace:
                raise _HTTPException(400)
            return workspace or "default"

        def is_safe_direct_basename(name):
            return (
                isinstance(name, str)
                and name not in {"", ".", ".."}
                and "/" not in name
                and "\\" not in name
                and os.path.basename(name) == name
            )

        def safe_direct_file_under(base, name):
            if not is_safe_direct_basename(name):
                return None
            base_real = os.path.realpath(base)
            candidate = os.path.abspath(os.path.join(base_real, name))
            if os.path.dirname(candidate) != base_real or os.path.islink(candidate):
                return None
            return candidate

        namespace = {
            "Request": object,
            "HTTPException": _HTTPException,
            "os": os,
            "can_access_upload": can_access_upload,
            "is_safe_direct_basename": is_safe_direct_basename,
            "safe_direct_file_under": safe_direct_file_under,
            "_get_active_workspace": lambda: "default",
            "_request_project_workspace": request_project_workspace,
            "_require_authorized_output": require_output,
            "_require_upload_content_access": lambda _request: None,
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        return namespace["_resolve_authorized_request_media"]

    @staticmethod
    def _load_generation_authorizer(resolver):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in ast.walk(tree)
            if isinstance(item, ast.FunctionDef)
            and item.name == "_authorize_generation_media_inputs"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "Request": object,
            "HTTPException": _HTTPException,
            "_GENERATION_MEDIA_INPUTS": ("image_start", "image_refs"),
            "_resolve_authorized_request_media": resolver,
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        return namespace["_authorize_generation_media_inputs"]

    @staticmethod
    def _request(
        session_id: str,
        *,
        remote: bool = False,
        project_unlocked: bool = True,
        project_workspace: str = "default",
    ):
        return types.SimpleNamespace(
            state=types.SimpleNamespace(
                maestro_session_id=session_id,
                maestro_remote=remote,
                project_unlocked=project_unlocked,
                project_workspace=project_workspace,
            )
        )

    def test_uploaded_generation_media_matrix_preserves_session_ownership(self):
        """Exercise the upload-sidecar -> generation-authorizer boundary.

        Local uploads are returned as absolute paths, while remote uploads are
        returned as bare names. Both must resolve for the session that uploaded
        them, and neither shape may weaken the remote session boundary.
        """
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = os.path.join(directory, "uploads")
                outputs = os.path.join(directory, "outputs")
                os.makedirs(os.path.join(uploads, "audio"))
                os.makedirs(outputs)
                resolver = self._load_resolver(outputs)
                authorize = self._load_generation_authorizer(resolver)
                owner = "a" * 32
                foreign = "b" * 32

                uploaded = os.path.join(uploads, "reference.png")
                Path(uploaded).write_bytes(b"reference")
                write_upload_access_sidecar(uploaded, owner, private=True)

                cases = (
                    (False, "image_start", uploaded),
                    (False, "image_refs", [uploaded]),
                    (True, "image_start", "reference.png"),
                    (True, "image_refs", ["reference.png"]),
                )
                for remote, field, supplied in cases:
                    with self.subTest(remote=remote, field=field, owner=True):
                        body = {field: supplied}
                        authorize(
                            self._request(owner, remote=remote), body, "default",
                        )
                        expected = [uploaded] if isinstance(supplied, list) else uploaded
                        self.assertEqual(body[field], expected)

                    with self.subTest(remote=remote, field=field, owner=False):
                        with self.assertRaises(_HTTPException) as raised:
                            authorize(
                                self._request(foreign, remote=remote),
                                {field: supplied},
                                "default",
                            )
                        self.assertEqual(raised.exception.status_code, 404)
                        self.assertEqual(
                            raised.exception.detail,
                            f"Unauthorized media: {field}",
                        )
            finally:
                os.chdir(previous)

    def test_matching_upload_is_decided_before_project_or_same_named_output(self):
        """A remote basename is an upload capability, not a project path.

        Project lookup may be locked or unavailable for first-load callers;
        both an allowed upload and a denied same-name upload must be decided
        from the upload sidecar without falling through to project media.
        """
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = os.path.join(directory, "uploads")
                outputs = os.path.join(directory, "outputs")
                os.makedirs(os.path.join(uploads, "audio"))
                os.makedirs(outputs)
                resolver = self._load_resolver(outputs)
                owner = "a" * 32
                foreign = "b" * 32
                upload = os.path.join(uploads, "reference.png")
                Path(upload).write_bytes(b"upload")
                Path(outputs, "reference.png").write_bytes(b"output")
                write_upload_access_sidecar(upload, owner, private=True)

                project_lookups = []

                def locked_project(*args):
                    project_lookups.append(args)
                    raise _HTTPException(423, "project locked")

                resolver.__globals__["_require_authorized_output"] = locked_project
                self.assertEqual(
                    resolver(self._request(owner, remote=True), "reference.png", "missing"),
                    upload,
                )
                self.assertIsNone(
                    resolver(self._request(foreign, remote=True), "reference.png", "missing")
                )
                self.assertEqual(project_lookups, [])
            finally:
                os.chdir(previous)

    def test_uploads_remain_session_owned_regardless_of_blur_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = os.path.join(directory, "uploads")
                audio = os.path.join(uploads, "audio")
                outputs = os.path.join(directory, "outputs")
                os.makedirs(audio)
                os.makedirs(outputs)
                resolver = self._load_resolver(outputs)
                owner = "a" * 32
                foreign = "b" * 32

                private_path = os.path.join(uploads, "private.png")
                public_path = os.path.join(audio, "public.wav")
                Path(private_path).write_bytes(b"private")
                Path(public_path).write_bytes(b"public")
                write_upload_access_sidecar(private_path, owner)
                write_upload_access_sidecar(public_path, owner, private=False)

                self.assertEqual(
                    resolver(self._request(owner), private_path, "default"),
                    private_path,
                )
                self.assertIsNone(
                    resolver(self._request(foreign), private_path, "default")
                )
                self.assertIsNone(
                    resolver(self._request(foreign), public_path, "default")
                )
            finally:
                os.chdir(previous)

    def test_missing_metadata_traversal_symlink_and_arbitrary_absolute_are_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                uploads = os.path.join(directory, "uploads")
                outputs = os.path.join(directory, "outputs")
                os.makedirs(os.path.join(uploads, "audio"))
                os.makedirs(outputs)
                resolver = self._load_resolver(outputs)
                owner = "a" * 32
                request = self._request(owner)

                missing_sidecar = os.path.join(uploads, "legacy.png")
                outside = os.path.join(directory, "outside.png")
                Path(missing_sidecar).write_bytes(b"legacy")
                Path(outside).write_bytes(b"outside")
                self.assertIsNone(resolver(request, missing_sidecar, "default"))
                self.assertIsNone(resolver(request, outside, "default"))
                self.assertIsNone(
                    resolver(request, os.path.join(uploads, "..", "outside.png"), "default")
                )

                if hasattr(os, "symlink"):
                    alias = os.path.join(uploads, "alias.png")
                    try:
                        os.symlink(outside, alias)
                    except OSError:
                        pass
                    else:
                        write_upload_access_sidecar(alias, owner)
                        self.assertIsNone(resolver(request, alias, "default"))
            finally:
                os.chdir(previous)

    def test_private_project_output_is_shared_by_unlocked_project_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            uploads = os.path.join(directory, "uploads")
            outputs = os.path.join(directory, "outputs")
            os.makedirs(os.path.join(uploads, "audio"))
            os.makedirs(outputs)
            output = os.path.join(outputs, "result.mp4")
            Path(output).write_bytes(b"output")
            resolver = self._load_resolver(outputs)
            owner = "a" * 32
            foreign = "b" * 32
            self.assertEqual(
                resolver(
                    self._request(owner),
                    output,
                    "default",
                ),
                output,
            )
            self.assertEqual(
                resolver(
                    self._request(foreign),
                    output,
                    "default",
                ),
                output,
            )
            with self.assertRaises(_HTTPException) as raised:
                resolver(
                    self._request(foreign, project_unlocked=False),
                    output,
                    "default",
                )
            self.assertEqual(raised.exception.status_code, 423)
            with self.assertRaises(_HTTPException) as raised:
                resolver(self._request(foreign), output, "other")
            self.assertEqual(raised.exception.status_code, 423)


class UploadRouteSourceContractTests(unittest.TestCase):
    def test_upload_content_requires_account_only_after_complete_cutover(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_require_upload_content_access"
        )
        node.decorator_list = []
        calls = []
        state = {"enforced": False}
        namespace = {
            "Request": object,
            "_account_project_access_state": lambda: dict(state),
            "_require_account_store": lambda _request: calls.append("store"),
            "_require_account_principal": lambda _request: calls.append("principal"),
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(LAUNCH_PATH), "exec"),
            namespace,
        )
        request = object()

        namespace["_require_upload_content_access"](request)
        self.assertEqual(calls, [])

        state["enforced"] = True
        namespace["_require_upload_content_access"](request)
        self.assertEqual(calls, ["store", "principal"])

    def test_every_upload_entry_point_uses_the_account_cutover_gate(self):
        for name in (
            "list_outputs",
            "serve_file",
            "_resolve_authorized_request_media",
            "upload_image",
            "upload_audio",
            "reconcile_llm_chat_upload_request",
        ):
            with self.subTest(name=name):
                self.assertIn(
                    "_require_upload_content_access(request)",
                    _function_source(name),
                )

        no_store = _function_source("_recovery_response_requires_no_store")
        for private_path in (
            'path == "/api/v1/workspaces"',
            'path == "/api/v1/outputs"',
            'path.startswith("/api/v1/file/")',
            'path.startswith("/api/v1/upload")',
        ):
            self.assertIn(private_path, no_store)

    def test_malformed_managed_output_is_hidden_after_project_authorization(self):
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_require_authorized_output", "list_favorites"}
        ]
        for node in nodes:
            node.decorator_list = []
        namespace = {
            "Request": object,
            "HTTPException": _HTTPException,
            "os": os,
        }
        exec(
            compile(ast.Module(body=nodes, type_ignores=[]), str(LAUNCH_PATH), "exec"),
            namespace,
        )

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "managed.mp4").write_bytes(b"media")
            Path(directory, "managed.meta.json").write_text("{broken", encoding="utf-8")
            namespace.update({
                "_request_project_workspace": lambda _request, workspace: workspace,
                "_require_project_access": lambda *_args, **_kwargs: directory,
                "load_media_sidecars": lambda *_args, **_kwargs: {},
                "_load_favorites": lambda _workspace: {"managed.mp4"},
            })
            request = types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_session_id="a" * 32),
            )

            with self.assertRaises(_HTTPException) as raised:
                namespace["_require_authorized_output"](
                    request, "project", "managed.mp4",
                )
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(
                namespace["list_favorites"](request, "project"),
                {"favorites": []},
            )

    def test_uploads_stamp_final_path_and_expose_only_public_policy(self):
        transcode_markers = {
            "upload_audio": "if needs_transcode",
            "upload_image": 'if ext in (".mp3", ".m4a", ".aac")',
        }
        for name, marker in transcode_markers.items():
            source = _function_source(name)
            self.assertIn("write_upload_access_sidecar", source)
            self.assertIn("request.state.maestro_session_id", source)
            self.assertIn("private: bool = True", source)
            self.assertIn("public_output_policy(access)", source)
            self.assertGreater(
                source.index("write_upload_access_sidecar"),
                source.index(marker),
            )

    def test_upload_list_and_serve_routes_fail_closed(self):
        listing = _function_source("list_outputs")
        self.assertIn("can_access_upload(entry[1], session_id)", listing)
        self.assertIn("read_upload_access_sidecar", listing)
        self.assertIn("public_output_policy(cached)", listing)
        self.assertIn("can_access_upload(filepath", _function_source("serve_file"))
        for name in ("serve_upload", "serve_audio_upload"):
            self.assertIn(
                "_resolve_authorized_request_media",
                _function_source(name),
            )

    def test_output_file_and_metadata_routes_share_fail_closed_authorizer(self):
        authorizer = _function_source("_require_authorized_output")
        self.assertIn("is_safe_direct_basename(name)", authorizer)
        self.assertIn("_require_project_access(request, workspace)", authorizer)
        self.assertIn("expected_sidecar", authorizer)
        self.assertIn("os.path.isfile(expected_sidecar) and sidecar is None", authorizer)
        self.assertNotIn("can_access_output", authorizer)
        listing = _function_source("list_outputs")
        self.assertIn("sidecar_cache.get(entry[0]) is None", listing)
        for name in ("serve_file", "get_output_metadata"):
            with self.subTest(name=name):
                self.assertIn(
                    "_require_authorized_output(",
                    _function_source(name),
                )

    def test_sfx_outputs_publish_atomic_policy_sidecars_for_audio_and_video(self):
        source = _function_source("_run_sfx_generation")
        self.assertIn("if ext not in GENERATED_MEDIA_EXTENSIONS", source)
        self.assertIn('"generation_mode": "video" if ext in', source)
        self.assertIn("stamp_sidecar_policy(", source)
        self.assertIn("job.get(\"access_policy\")", source)
        self.assertIn("job.get(\"workspace\")", source)
        self.assertIn("os.fsync(f.fileno())", source)
        self.assertIn("os.replace(temp_meta, meta_path)", source)
        self.assertIn("Failed to publish protected SFX metadata", source)

    def test_queue_mutation_and_log_routes_are_owner_scoped(self):
        count = _function_source("set_job_output_count")
        self.assertIn(
            "_require_generic_queue_control_job(job_id, request)", count,
        )
        generic_guard = _function_source("_require_generic_queue_control_job")
        self.assertIn("_require_owned_job(job_id, request)", generic_guard)
        self.assertIn("_queue_recovery_is_blocked(job)", generic_guard)
        log = _function_source("get_job_log")
        self.assertIn("_require_owned_job(job_id, request)", log)
        self.assertIn("count < 1 or count > 25", count)
        self.assertIn("update_requested_outputs(", count)
        self.assertIn("min(250, limit)", log)
        self.assertIn("job_events(", log)
        status = _function_source("get_status")
        self.assertIn("if not _job_owned_by_request(job, request)", status)
        self.assertIn("j = snapshot_job(job)", status)
        self.assertIn('"events": job_events(job, 100)', status)
        self.assertNotIn("job_events(_jobs[job_id]", status)
        self.assertIn('"events": job_events(job, 100)', _function_source("list_jobs"))

    def test_high_risk_consumers_use_central_authorizer(self):
        consumers = (
            "llm_describe_image",
            "mix_audio",
            "analyze_audio",
            "retake_video_endpoint",
            "extract_frames_endpoint",
            "edit_anything_endpoint",
            "repaint_endpoint",
            "recast_endpoint",
            "outpaint_endpoint",
            "blend_endpoint",
            "segment_preview_endpoint",
            "inpaint_endpoint",
            "tools_upscale",
            "tools_revoice",
        )
        for name in consumers:
            with self.subTest(name=name):
                source = _function_source(name)
                self.assertTrue(
                    "_resolve_authorized_request_media" in source
                    or "_resolve_recast_media" in source,
                    source,
                )

    def test_project_asset_variants_use_store_source_path_contract(self):
        imported = _function_source("add_project_asset_variant")
        generated = _function_source("_attach_project_reference_result")
        self.assertIn('"source_path": path', imported)
        self.assertIn('"metadata": inherited', imported)
        self.assertIn('"source_path": str(artifact.path)', generated)
        self.assertIn(
            "tuple(item.role for item in artifacts) != expected_output_roles",
            generated,
        )
        self.assertIn(
            "[item.index for item in artifacts] != list(range(len(artifacts)))",
            generated,
        )
        self.assertIn("for artifact in artifacts:", generated)
        self.assertIn("result.plan.sheets[artifact.index].label", generated)


if __name__ == "__main__":
    unittest.main()
