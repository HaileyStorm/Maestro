import asyncio
import copy
import contextvars
import hashlib
import hmac
import json
import math
import re
import threading
import time
from unittest.mock import patch
import ast
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
LAUNCH_PATH = APP_ROOT / "launch.py"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.output_access import (  # noqa: E402
    can_access_upload,
    public_output_policy,
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
    @staticmethod
    def _load_upload_route():
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            item for item in ast.walk(tree)
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "upload_image"
        )
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "Request": object,
            "UploadFile": object,
            "File": lambda _value: None,
            "HTTPException": _HTTPException,
            "MAX_IMAGE_UPLOAD_BYTES": 1024,
            "_require_upload_content_access": lambda _request: None,
            "write_upload_access_sidecar": write_upload_access_sidecar,
            "public_output_policy": public_output_policy,
            "os": os,
            "uuid": uuid,
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        return namespace["upload_image"]

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

    def test_local_upload_under_volume_symlink_returns_authorizable_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            volume = root / "volume"
            outputs = root / "outputs"
            app.mkdir()
            volume.mkdir()
            outputs.mkdir()
            (app / "uploads").symlink_to(volume, target_is_directory=True)
            previous = os.getcwd()
            os.chdir(app)
            try:
                owner = "a" * 32
                foreign = "b" * 32
                request = self._request(owner)
                request.headers = {}

                class UploadedFile:
                    filename = "source.png"

                    async def read(self):
                        return b"synthetic-source"

                result = asyncio.run(
                    self._load_upload_route()(request, UploadedFile())
                )
                canonical = str(volume / result["filename"])
                alias = str(app / "uploads" / result["filename"])
                self.assertEqual(result["path"], canonical)
                resolver = self._load_resolver(str(outputs))
                self.assertEqual(
                    resolver(request, result["path"], "default"), canonical,
                )
                self.assertIsNone(
                    resolver(self._request(foreign), result["path"], "default")
                )
                self.assertIsNone(resolver(request, alias, "default"))
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


class RetakePolicyAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(LAUNCH_PATH.read_text(encoding="utf-8"))
        names = {"retake_video_endpoint", "_inherit_media_access_policy",
                 "_http_output_policy_from_request", "_JobRegistry",
                 "_output_share_revision", "_require_authorized_output",
                 "_OutputLineageMutationGuard", "_output_lineage_mutation_guard"}
        nodes = [copy.deepcopy(node) for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and node.name in names]
        for node in nodes:
            node.decorator_list = []
        cls.code = compile(ast.Module(body=nodes, type_ignores=[]), str(LAUNCH_PATH), "exec")

    def setUp(self):
        from services.output_access import output_policy_from_request, read_upload_access_sidecar
        from services.search_index import load_media_sidecars

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.source = self.outputs / "source.mp4"
        self.source.write_bytes(b"disposable authorized video")
        self.owner = "a" * 32
        self.registered = []
        self.probed = []
        self.mutate_on_probe = None
        self.ns = {
            "Request": object, "HTTPException": _HTTPException,
            "os": os, "uuid": uuid, "time": time, "threading": threading,
            "re": re, "hmac": hmac, "hashlib": hashlib, "math": math,
            "_output_share_revision_cache": {},
            "_output_share_revision_cache_lock": threading.Lock(),
            "_output_lineage_mutation_registry_lock": threading.Lock(),
            "_output_lineage_mutation_locks": {},
            "_request_remote": contextvars.ContextVar("retake_remote", default=False),
            "_request_session_id": contextvars.ContextVar("retake_session", default=self.owner),
            "output_policy_from_request": output_policy_from_request,
            "read_upload_access_sidecar": read_upload_access_sidecar,
            "load_media_sidecars": load_media_sidecars,
            "_workspace_dir": lambda _workspace: str(self.outputs),
            "_get_active_workspace": lambda: "default",
            "_resolve_authorized_request_media": AuthorizedMediaResolverTests()._load_resolver(str(self.outputs)),
        }

        def project_access(request, workspace, *, permission=None):
            if permission is not None:
                self.assertEqual(permission, "project.generate")
            if not request.state.project_unlocked or workspace != request.state.project_workspace:
                raise _HTTPException(423)
            return str(self.outputs)

        self.ns["_require_project_access"] = project_access
        exec(self.code, self.ns)
        registry = self.ns["_JobRegistry"]()

        def register(job):
            # Use native policy admission; never start a worker or touch a model.
            registry.prepare(job)
            self.registered.append(copy.deepcopy(job))

        self.ns["_queue_recovery_register_and_publish"] = register
        test = self

        class Reader:
            def __init__(self, path):
                test.probed.append(path)
                if test.mutate_on_probe:
                    test.mutate_on_probe()

            def get_avg_fps(self):
                return 24

            def __len__(self):
                return 48

            def __getitem__(self, _index):
                return types.SimpleNamespace(shape=(480, 640, 3))

        decoder = patch.dict(sys.modules, {"decord": types.SimpleNamespace(VideoReader=Reader)})
        decoder.start()
        self.addCleanup(decoder.stop)
        cwd = patch("os.getcwd", return_value=str(self.root))
        cwd.start()
        self.addCleanup(cwd.stop)

    def submit(self, flags=None, *, source=None, session=None, workspace="default", unlocked=True, prompt="retake"):
        body = {"video_path": str(source or self.source), "workspace": workspace,
                "model_type": "ltx2_3", "start_time": 0.25, "end_time": 1.25,
                "prompt": prompt, **(flags or {})}
        request = AuthorizedMediaResolverTests._request(
            session or self.owner, project_unlocked=unlocked,
        )

        async def read_body():
            return copy.deepcopy(body)

        request.json = read_body
        return asyncio.run(self.ns["retake_video_endpoint"](request))

    def set_source_policy(self, private, explicit):
        self.source.with_suffix(".meta.json").write_text(json.dumps({
            "output_filename": self.source.name, "private": private,
            "explicit": explicit, "workspace": "default",
        }))

    def test_source_flags_and_explicit_overrides_reach_native_job_policy(self):
        for private in (False, True):
            for explicit in (False, True):
                self.set_source_policy(private, explicit)
                cases = (
                    ({}, {"private": private, "explicit": explicit}),
                    ({"private_output": None, "explicit_output": None},
                     {"private": private, "explicit": explicit}),
                    ({"private_output": not private}, {"private": not private, "explicit": explicit}),
                    ({"explicit_output": not explicit}, {"private": private, "explicit": not explicit}),
                    ({"private_output": False, "explicit_output": False}, {"private": False, "explicit": False}),
                    ({"private_output": True, "explicit_output": True}, {"private": True, "explicit": True}),
                )
                for flags, expected in cases:
                    with self.subTest(source=(private, explicit), flags=flags):
                        response = self.submit(flags)
                        job = self.registered[-1]
                        self.assertEqual(response["job_id"], job["id"])
                        self.assertEqual(job["access_policy"], expected)
                        self.assertEqual({key: job[key] for key in expected}, expected)
                        self.assertEqual(job["session_id"], self.owner)
                        self.assertEqual(job["params"]["retake_video"], str(self.source))
                        self.assertEqual((job["params"]["retake_start_frame"], job["params"]["retake_end_frame"]), (6, 30))
                        self.assertFalse({"private_output", "explicit_output"} & job["params"].keys())

    def test_owned_upload_inherits_flags_but_foreign_upload_is_not_admitted(self):
        source = self.uploads / "uploaded.mp4"
        source.write_bytes(b"disposable upload")
        write_upload_access_sidecar(str(source), self.owner, private=True)
        self.submit(source=source)
        self.assertEqual(self.registered[-1]["access_policy"], {"private": True, "explicit": False})
        before = len(self.registered)
        self.probed.clear()
        with self.assertRaises(_HTTPException) as raised:
            self.submit(source=source, session="b" * 32)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(len(self.registered), before)
        self.assertEqual(self.probed, [])

    def test_invalid_flags_reject_before_video_probe_or_job_admission(self):
        for flag in ("private_output", "explicit_output"):
            for value in ("false", 1, [], {}):
                with self.subTest(flag=flag, value=value):
                    with self.assertRaises(_HTTPException) as raised:
                        self.submit({flag: value})
                    self.assertEqual(raised.exception.status_code, 400)
                    self.assertIn(flag, raised.exception.detail)
        self.assertEqual(self.registered, [])
        self.assertEqual(self.probed, [])

    def test_locked_cross_project_and_missing_sources_never_admit_jobs(self):
        for kwargs, status in (({"unlocked": False}, 423), ({"workspace": "other"}, 423),
                               ({"source": self.outputs / "missing.mp4"}, 404)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(_HTTPException) as raised:
                    self.submit(**kwargs)
                self.assertEqual(raised.exception.status_code, status)
        self.assertEqual(self.registered, [])
        self.assertEqual(self.probed, [])

    def test_prompt_subject_matter_does_not_set_preview_flags(self):
        self.set_source_policy(False, False)
        for prompt in ("adult romance", "violent battle", "controversial political scene"):
            with self.subTest(prompt=prompt):
                self.submit(prompt=prompt)
                job = self.registered[-1]
                self.assertEqual(job["params"]["prompt"], prompt)
                self.assertEqual(job["access_policy"], {"private": False, "explicit": False})

    def source_revision(self):
        return self.ns["_output_share_revision"](
            str(self.source), str(self.outputs), self.source.name,
        )

    def test_editor_source_revision_accepts_current_bytes_and_rejects_replacement(self):
        self.set_source_policy(True, False)
        revision = self.source_revision()
        self.submit({"expected_source_revision": revision})
        self.assertEqual(len(self.registered), 1)
        self.assertNotIn("expected_source_revision", self.registered[0]["params"])
        # Preserve size and mtime: content identity must still detect replacement.
        original = self.source.stat()
        self.source.write_bytes(b"x" * original.st_size)
        os.utime(self.source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.probed.clear()
        with self.assertRaises(_HTTPException) as raised:
            self.submit({"expected_source_revision": revision})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(self.registered), 1)
        self.assertEqual(self.probed, [])

    def test_editor_revision_change_during_probe_never_registers_job(self):
        for change in ("media", "sidecar"):
            with self.subTest(change=change):
                self.set_source_policy(True, False)
                revision = self.source_revision()
                self.mutate_on_probe = (
                    lambda: self.source.write_bytes(b"changed during probe")
                ) if change == "media" else lambda: self.set_source_policy(False, False)
                with self.assertRaises(_HTTPException) as raised:
                    self.submit({"expected_source_revision": revision})
                self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.registered, [])

    def test_invalid_editor_source_revision_rejects_before_probe(self):
        for revision in (None, 1, {}, "", "sha256:no", "sha256:" + "A" * 64):
            with self.subTest(revision=revision):
                with self.assertRaises(_HTTPException) as raised:
                    self.submit({"expected_source_revision": revision})
                self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.probed, [])
        self.assertEqual(self.registered, [])

    def test_editor_revision_cannot_bind_an_upload_with_matching_output_name(self):
        upload = self.uploads / self.source.name
        upload.write_bytes(b"different authorized upload")
        write_upload_access_sidecar(str(upload), self.owner, private=True)
        with self.assertRaises(_HTTPException) as raised:
            self.submit({"expected_source_revision": self.source_revision()}, source=upload)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.probed, [])
        self.assertEqual(self.registered, [])


    def test_invalid_retake_temporal_controls_reject_before_probe_or_registration(self):
        for field in ("start_time", "end_time", "retake_strength"):
            for value in (None, True, {}, [], "bad", float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(_HTTPException) as raised:
                        self.submit({field: value})
                    self.assertEqual(raised.exception.status_code, 400)
        for value in (-0.1, 1.1):
            with self.subTest(strength=value):
                with self.assertRaises(_HTTPException) as raised:
                    self.submit({"retake_strength": value})
                self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.probed, [])
        self.assertEqual(self.registered, [])

    def test_retake_legacy_whole_source_range_and_numeric_controls_remain_supported(self):
        for end in (0, -1):
            result = self.submit({"start_time": -0.25, "end_time": end, "retake_strength": 0})
            self.assertEqual(result["retake_frames"], "0-48/48")
            self.assertEqual(self.registered[-1]["params"]["retake_strength"], 0)
        result = self.submit({"start_time": "0.25", "end_time": "1.25", "retake_strength": "0.5"})
        self.assertEqual(result["retake_frames"], "6-30/48")
        self.assertEqual(self.registered[-1]["params"]["retake_strength"], 0.5)

    def test_finite_extreme_retake_times_cannot_overflow_frame_conversion(self):
        for start, end in ((-1e308, 1e308), (0.25, 1e308)):
            result = self.submit({"start_time": start, "end_time": end})
            self.assertEqual(result["retake_frames"], f"{0 if start < 0 else 6}-48/48")
        before = len(self.registered)
        with self.assertRaises(_HTTPException) as raised:
            self.submit({"start_time": 1e308, "end_time": 1e308})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(len(self.registered), before)

    def test_invalid_source_timing_cannot_overflow_frame_conversion_or_admit(self):
        for fps in (0, -1, float("nan"), float("inf"), 5e-324):
            with self.subTest(fps=fps):
                with patch.object(sys.modules["decord"].VideoReader, "get_avg_fps", return_value=fps):
                    with self.assertRaises(_HTTPException) as raised:
                        self.submit()
                self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.registered, [])

    def test_retake_decoder_failure_hides_private_path_and_never_admits(self):
        def unreadable():
            raise ValueError("Cannot decode /private/user/video.mp4")
        self.mutate_on_probe = unreadable
        with self.assertRaises(_HTTPException) as raised:
            self.submit()
        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("/private/", raised.exception.detail)
        self.assertEqual(self.registered, [])


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
        for name in ("serve_file", "serve_upload", "serve_audio_upload"):
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


class OrdinaryUploadDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(LAUNCH_PATH.read_text())
        names = {"_upload_retained_input", "_queue_recovery_file_values", "delete_upload", "_upload_registration_guard"}
        cls.nodes = [copy.deepcopy(node) for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        for node in cls.nodes:
            node.decorator_list = []

    def setUp(self):
        from services import upload_usage
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import (QueueRecoveryCoordinator,
            processed_tool_publication_pending, prompt_enhancement_gpu_cleanup_pending)
        from services.queue_recovery_runtime import load_request_manifest
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.project = self.root / "outputs"
        self.project.mkdir()
        self.media = self.uploads / "song.wav"
        self.media.write_bytes(b"ordinary audio")
        self.owner = "a" * 32
        write_upload_access_sidecar(str(self.media), self.owner)
        self.request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_session_id=self.owner))
        self.coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.root / "queue.jsonl"))
        self.ns = dict(os=os, json=json, uuid=uuid, Request=object, HTTPException=_HTTPException,
                       upload_usage=upload_usage, can_access_upload=can_access_upload,
                       _workspace_lifecycle_lock=threading.RLock(),
                       _queue_recovery_coordinator=self.coordinator,
                       processed_tool_publication_pending=processed_tool_publication_pending,
                       prompt_enhancement_gpu_cleanup_pending=prompt_enhancement_gpu_cleanup_pending,
                       _require_upload_content_access=lambda request: None,
                       _list_workspaces=lambda: [{"name": "default", "path": str(self.project)}],
                       load_request_manifest=load_request_manifest,
                       _RECOVERABLE_INPUT_KEYS={"audio_path", "image_paths"},
                       _llm_chat_upload_marker_path=lambda path: path + ".chat-upload.json")
        exec(compile(ast.Module(body=copy.deepcopy(self.nodes), type_ignores=[]), str(LAUNCH_PATH), "exec"), self.ns)
        cwd_patch = patch("os.getcwd", return_value=str(self.root))
        cwd_patch.start()
        self.addCleanup(cwd_patch.stop)

    def delete(self):
        return self.ns["delete_upload"](self.request, self.media.name)

    def manifest(self, job_id="retained"):
        from services.queue_recovery_runtime import atomic_write_request_manifest
        return atomic_write_request_manifest(str(self.project), job_id=job_id, params={},
                                             inputs=[{"path": str(self.media), "field": "audio_path:0"}])

    def test_unused_upload_is_physically_removed_without_derivative_cleanup(self):
        derivative = self.uploads / "song-preview.wav"
        derivative.write_bytes(b"independent")
        self.assertEqual(self.delete(), {"deleted": "song.wav"})
        self.assertFalse(self.media.exists())
        self.assertFalse(Path(str(self.media) + ".access.json").exists())
        self.assertTrue(derivative.exists())
        self.assertFalse(list(self.uploads.glob(".trash*")))

    def test_wrong_session_and_symlink_metadata_fail_closed(self):
        self.request.state.maestro_session_id = "b" * 32
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.status_code, 404)
        self.request.state.maestro_session_id = self.owner
        sidecar = Path(str(self.media) + ".access.json")
        elsewhere = self.root / "outside.json"
        sidecar.replace(elsewhere)
        sidecar.symlink_to(elsewhere)
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.status_code, 404)
        self.assertTrue(self.media.exists())

    def test_live_reader_and_chat_marker_reject_removal(self):
        with self.ns["upload_usage"].reader([str(self.media)]):
            with self.assertRaises(_HTTPException) as caught:
                self.delete()
            self.assertEqual(caught.exception.detail, "This upload is still in use")
        Path(str(self.media) + ".chat-upload.json").write_text("{}")
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(self.media.exists())

    def test_restart_retained_and_orphan_manifest_keep_input(self):
        self.manifest()
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.detail, "This upload is still in use")
        self.assertTrue(self.media.exists())

    def test_corrupt_manifest_does_not_authorize_removal(self):
        pointer = self.manifest()
        (self.project / pointer["path"]).write_text("bad")
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("could not be checked", caught.exception.detail)
        self.assertTrue(self.media.exists())

    def test_completed_director_keeps_original_song_for_rejoin(self):
        (self.project / "_director_pipeline_finished.json").write_text(json.dumps(
            {"status": "completed", "_params_snapshot": {"audio_path": str(self.media)}}))
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.detail, "This upload is still in use")
        self.assertTrue(self.media.exists())

    def test_second_staging_failure_restores_media_and_sidecar(self):
        original = os.replace
        calls = []
        def fail_second(source, destination):
            calls.append((source, destination))
            if len(calls) == 2:
                raise PermissionError("busy")
            return original(source, destination)
        with patch("os.replace", side_effect=fail_second):
            with self.assertRaises(_HTTPException) as caught:
                self.delete()
        self.assertEqual(caught.exception.status_code, 423)
        self.assertEqual(self.media.read_bytes(), b"ordinary audio")
        self.assertTrue(can_access_upload(str(self.media), self.owner))
        self.assertFalse(list(self.uploads.glob(".trash*")))

    def test_registration_and_deletion_serialize_at_one_boundary(self):
        entered = threading.Event()
        finish = threading.Event()
        outcome = []
        def register():
            entered.set()
            finish.wait(2)
            self.manifest()
        worker = threading.Thread(target=self.ns["_upload_registration_guard"](register))
        worker.start()
        self.assertTrue(entered.wait(1))
        def remove():
            try:
                self.delete()
            except _HTTPException as error:
                outcome.append(error.status_code)
        deletion = threading.Thread(target=remove)
        deletion.start()
        self.assertTrue(deletion.is_alive())
        finish.set()
        worker.join(2)
        deletion.join(2)
        self.assertEqual(outcome, [409])
        self.assertTrue(self.media.exists())

    def test_snapshot_read_does_not_restore_or_mutate_live_coordinator(self):
        from services.queue_recovery_adapter import owner_principal_digest, project_instance_digest
        owner = owner_principal_digest(b"test-upload-secret-value", self.owner)
        project = project_instance_digest(b"test-upload-secret-value", "c" * 32)
        pointer = self.manifest("live-job")
        self.coordinator.register_job({"id": "live-job", "status": "running", "workspace": "default"},
                                     owner_digest=owner, project_digest=project, request_manifest=pointer)
        before = copy.deepcopy(self.coordinator._snapshots)
        snapshots, retired = self.coordinator.read_only_snapshot()
        self.assertEqual(snapshots["live-job"]["status"], "running")
        self.assertEqual(self.coordinator._snapshots, before)
        snapshots["live-job"]["status"] = "cancelled"
        self.assertEqual(self.coordinator._snapshots, before)


    def test_retired_ordinary_job_does_not_keep_completed_input(self):
        from services.queue_recovery_adapter import owner_principal_digest, project_instance_digest
        owner = owner_principal_digest(b"test-upload-secret-value", self.owner)
        project = project_instance_digest(b"test-upload-secret-value", "c" * 32)
        pointer = self.manifest("done-job")
        self.coordinator.register_job({"id": "done-job", "status": "completed", "workspace": "default"},
                                     owner_digest=owner, project_digest=project, request_manifest=pointer)
        self.assertEqual(self.delete(), {"deleted": "song.wav"})

    def test_tombstoned_ordinary_job_does_not_keep_input(self):
        from services.queue_recovery_adapter import owner_principal_digest, project_instance_digest
        owner = owner_principal_digest(b"test-upload-secret-value", self.owner)
        project = project_instance_digest(b"test-upload-secret-value", "c" * 32)
        pointer = self.manifest("retired-job")
        self.coordinator.register_job({"id": "retired-job", "status": "completed", "workspace": "default"},
                                     owner_digest=owner, project_digest=project, request_manifest=pointer)
        self.coordinator.tombstone_terminal("retired-job")
        self.assertEqual(self.delete(), {"deleted": "song.wav"})

    def test_canonical_volume_alias_cannot_hide_retained_input(self):
        from services.queue_recovery_runtime import atomic_write_request_manifest
        alias = self.root / "volume-alias"
        alias.symlink_to(self.uploads, target_is_directory=True)
        atomic_write_request_manifest(str(self.project), job_id="alias-job", params={},
                                      inputs=[{"path": str(alias / self.media.name)}])
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.detail, "This upload is still in use")

    def test_media_reclaim_failure_restores_exact_access_pair(self):
        access = Path(str(self.media) + ".access.json")
        before = access.read_bytes()
        original = os.remove
        def fail_media(path, *args, **kwargs):
            if str(path).startswith(str(self.uploads / ".trash_upload_")) and str(path).endswith(self.media.name):
                raise PermissionError("busy")
            return original(path, *args, **kwargs)
        with patch("os.remove", side_effect=fail_media):
            with self.assertRaises(_HTTPException) as caught:
                self.delete()
        self.assertEqual(caught.exception.status_code, 423)
        self.assertEqual(access.read_bytes(), before)
        self.assertEqual(self.media.read_bytes(), b"ordinary audio")
        self.assertFalse(list(self.uploads.glob(".trash*")))

    def test_snapshot_accessor_never_calls_repairing_journal_recover(self):
        with patch.object(self.coordinator.journal, "recover", side_effect=AssertionError("read repaired journal")):
            self.assertEqual(self.coordinator.read_only_snapshot(), ({}, frozenset()))

    def test_unrelated_legacy_director_without_snapshot_does_not_block(self):
        (self.project / "_director_pipeline_legacy.json").write_text(json.dumps({"status": "completed"}))
        self.assertEqual(self.delete(), {"deleted": "song.wav"})


    def test_known_retired_corrupt_manifest_is_ignored_without_prefix_aliases(self):
        from services.queue_recovery_adapter import owner_principal_digest, project_instance_digest
        owner = owner_principal_digest(b"test-upload-secret-value", self.owner)
        project = project_instance_digest(b"test-upload-secret-value", "c" * 32)
        pointer = self.manifest("retired-job")
        self.coordinator.register_job({"id": "retired-job", "status": "completed", "workspace": "default"},
                                     owner_digest=owner, project_digest=project, request_manifest=pointer)
        self.coordinator.tombstone_terminal("retired-job")
        (self.project / pointer["path"]).write_text("bad retired record")
        alias = self.project / ".maestro-recovery" / "retired-job.other.request.json"
        alias.write_text("bad unresolved alias")
        with self.assertRaises(_HTTPException) as caught:
            self.delete()
        self.assertEqual(caught.exception.status_code, 409)
        alias.unlink()
        self.assertEqual(self.delete(), {"deleted": "song.wav"})
