import ast
import base64
import builtins
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock
from pathlib import Path

from fastapi import HTTPException


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.output_access import (  # noqa: E402
    MIN_PROJECT_PASSWORD_LENGTH,
    ProjectAccessManager,
    ProjectUnlockRateLimiter,
    can_access_upload,
    decode_session_cookie,
    encode_session_cookie,
    load_or_create_session_secret,
    output_policy_from_request,
    public_output_policy,
    read_upload_access_sidecar,
    stamp_sidecar_policy,
    upload_access_sidecar_path,
    write_upload_access_sidecar,
)
from services.search_index import (  # noqa: E402
    artifact_lineage,
    classify_gallery_artifacts,
    linked_component_names,
    load_media_sidecars,
)


def _load_launch_functions(*names):
    source = (APP_ROOT / "launch.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_ROOT / "launch.py"))
    wanted = set(names)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in wanted
    ]
    namespace = {
        "HTTPException": HTTPException,
        "Request": object,
        "json": json,
        "os": os,
        "threading": threading,
        "time": time,
        "uuid": uuid,
        "artifact_lineage": artifact_lineage,
        "classify_gallery_artifacts": classify_gallery_artifacts,
        "linked_component_names": linked_component_names,
        "load_media_sidecars": load_media_sidecars,
        "_GALLERY_MEDIA_EXTENSIONS": {".mp4", ".png", ".webm", ".mkv", ".mov"},
        "_output_lineage_mutation_registry_lock": threading.Lock(),
        "_output_lineage_mutation_locks": {},
        "_workspace_lifecycle_lock": threading.RLock(),
        "_workspaces_deleting": set(),
        "_workspace_operations": {},
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(APP_ROOT / "launch.py"), "exec"),
        namespace,
    )
    return namespace


class SessionCookieTests(unittest.TestCase):
    def test_signed_cookie_round_trip_and_tamper_rejection(self):
        secret = b"s" * 32
        session_id = "a" * 32
        cookie = encode_session_cookie(session_id, secret)
        self.assertEqual(decode_session_cookie(cookie, secret), session_id)
        self.assertIsNone(decode_session_cookie(cookie[:-1] + "0", secret))
        self.assertIsNone(decode_session_cookie("a" * 32, secret))

    def test_secret_is_stable_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "session-secret")
            first = load_or_create_session_secret(path)
            second = load_or_create_session_secret(path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            if os.name != "nt":
                self.assertEqual(os.stat(path).st_mode & 0o077, 0)

    def test_short_existing_secret_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "session-secret")
            Path(path).write_bytes(b"short")
            with self.assertRaisesRegex(RuntimeError, "shorter than 256 bits"):
                load_or_create_session_secret(path)
            self.assertEqual(Path(path).read_bytes(), b"short")

    def test_concurrent_secret_creation_returns_one_fully_written_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "session-secret")
            start = threading.Barrier(8)
            values = []
            errors = []

            def create():
                try:
                    start.wait(2)
                    values.append(load_or_create_session_secret(path))
                except Exception as error:  # pragma: no cover - asserted below
                    errors.append(error)

            threads = [threading.Thread(target=create) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)

            self.assertEqual(errors, [])
            self.assertEqual(len(values), 8)
            self.assertEqual(len(set(values)), 1)
            self.assertGreaterEqual(len(values[0]), 32)
            self.assertEqual(Path(path).read_bytes(), values[0])


class OutputPolicyTests(unittest.TestCase):
    def test_host_consent_does_not_mark_an_ordinary_omitted_job_explicit(self):
        params = {"prompt": "x"}
        policy = output_policy_from_request(
            params, explicit_enabled=True, owner_session_id="a" * 32,
        )
        self.assertEqual(
            policy,
            {"private": False, "explicit": False},
        )

    def test_legacy_mature_hint_does_not_override_caller_policy(self):
        for params in ({}, {"explicit_output": False}, {
            "explicit_output": False, "private_output": False,
        }):
            with self.subTest(params=params):
                policy = output_policy_from_request(
                    dict(params),
                    mature_output=True,
                    owner_session_id="a" * 32,
                )
                self.assertEqual(
                    policy,
                    {"private": False, "explicit": False},
                )

    def test_explicit_public_override_is_honored(self):
        override = {"private_output": False, "explicit_output": True}
        policy = output_policy_from_request(
            override, mature_output=True, owner_session_id="a" * 32,
        )
        self.assertFalse(policy["private"])
        self.assertTrue(policy["explicit"])
        self.assertNotIn("private_output", override)
        self.assertNotIn("explicit_output", override)

    def test_policy_flags_reject_string_boolean_coercion(self):
        for key in ("private_output", "explicit_output"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    output_policy_from_request(
                        {key: "false"},
                        explicit_enabled=False,
                        owner_session_id="a" * 32,
                    )

    def test_private_sidecar_is_shared_blur_metadata_and_strips_legacy_owner(self):
        sidecar = {"params": {"prompt": "private"}}
        stamp_sidecar_policy(
            sidecar,
            {"private": True, "explicit": True, "owner_session_id": "a" * 32},
            workspace="project-a",
        )
        self.assertNotIn("owner_session_id", sidecar)
        self.assertEqual(
            public_output_policy(sidecar), {"private": True, "explicit": True},
        )
        self.assertEqual(sidecar["workspace"], "project-a")

    def test_generated_output_missing_metadata_has_public_presentation_defaults(self):
        self.assertEqual(
            public_output_policy(None), {"private": False, "explicit": False},
        )
        self.assertEqual(
            public_output_policy({"params": {}}),
            {"private": False, "explicit": False},
        )


class UploadAccessTests(unittest.TestCase):
    def _media(self, directory, name="upload.mp4"):
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b"media")
        return path

    def test_stamp_read_and_same_session_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            media = self._media(directory)
            owner = "a" * 32
            stamped = write_upload_access_sidecar(media, owner)

            self.assertEqual(
                stamped,
                {
                    "version": 1,
                    "kind": "upload",
                    "private": True,
                    "owner_session_id": owner,
                },
            )
            self.assertEqual(read_upload_access_sidecar(media), stamped)
            self.assertTrue(can_access_upload(media, owner))
            if os.name != "nt":
                self.assertEqual(
                    os.stat(upload_access_sidecar_path(media)).st_mode & 0o077,
                    0,
                )

    def test_foreign_missing_and_malformed_sidecars_are_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            media = self._media(directory)
            owner = "a" * 32
            foreign = "b" * 32

            self.assertFalse(can_access_upload(media, owner))
            sidecar = upload_access_sidecar_path(media)
            malformed_values = [
                "not json",
                json.dumps([]),
                json.dumps({
                    "version": 1,
                    "kind": "output",
                    "private": True,
                    "owner_session_id": owner,
                }),
                json.dumps({
                    "version": 1,
                    "kind": "upload",
                    "private": "yes",
                    "owner_session_id": owner,
                }),
                json.dumps({
                    "version": 1,
                    "kind": "upload",
                    "private": True,
                    "owner_session_id": "not-a-session",
                }),
            ]
            for value in malformed_values:
                with self.subTest(value=value):
                    with open(sidecar, "w", encoding="utf-8") as handle:
                        handle.write(value)
                    self.assertIsNone(read_upload_access_sidecar(media))
                    self.assertFalse(can_access_upload(media, owner))

            write_upload_access_sidecar(media, owner)
            self.assertFalse(can_access_upload(media, foreign))
            self.assertFalse(can_access_upload(media, ""))

    def test_unblurred_upload_remains_session_owned_and_redacts_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            media = self._media(directory)
            metadata = write_upload_access_sidecar(
                media, "a" * 32, private=False,
            )

            self.assertTrue(can_access_upload(media, "a" * 32))
            self.assertFalse(can_access_upload(media, "b" * 32))
            self.assertFalse(can_access_upload(media, ""))
            self.assertEqual(
                public_output_policy(metadata),
                {"private": False, "explicit": False},
            )
            self.assertNotIn(
                "owner_session_id", public_output_policy(metadata),
            )

    def test_sidecar_name_is_extension_specific_and_unsafe_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            video = self._media(directory, "same.mp4")
            audio = self._media(directory, "same.wav")
            self.assertEqual(
                upload_access_sidecar_path(video), f"{video}.access.json",
            )
            self.assertNotEqual(
                upload_access_sidecar_path(video),
                upload_access_sidecar_path(audio),
            )

            unsafe = [
                "",
                os.path.join(directory, "..", "escape.mp4"),
                os.path.join(directory, ".hidden.mp4"),
                f"{video}.access.json",
            ]
            for path in unsafe:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        upload_access_sidecar_path(path)
                    self.assertFalse(can_access_upload(path, "a" * 32))

    def test_invalid_write_metadata_is_rejected_without_a_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            media = self._media(directory)
            for owner, private in (("short", True), ("a" * 32, 1)):
                with self.subTest(owner=owner, private=private):
                    with self.assertRaises(ValueError):
                        write_upload_access_sidecar(
                            media, owner, private=private,
                        )
                    self.assertFalse(
                        os.path.exists(upload_access_sidecar_path(media)),
                    )


class ProjectPasswordTests(unittest.TestCase):
    @staticmethod
    def _durable_manager(root, clock=lambda: 1_000.0):
        return ProjectAccessManager(
            os.path.join(root, "grants.json"), b"g" * 32, clock=clock,
        )

    def test_new_passwords_have_a_strong_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectAccessManager()
            with self.assertRaisesRegex(ValueError, str(MIN_PROJECT_PASSWORD_LENGTH)):
                manager.set_password(
                    "project", directory, "a" * 32,
                    "x" * (MIN_PROJECT_PASSWORD_LENGTH - 1),
                )

    def test_remote_unlock_limiter_backs_off_per_session_and_project(self):
        now = [100.0]
        limiter = ProjectUnlockRateLimiter(clock=lambda: now[0])
        for _ in range(4):
            self.assertEqual(limiter.record_failure("project", "session-a"), 0)
        self.assertEqual(limiter.record_failure("project", "session-a"), 1)
        self.assertEqual(limiter.retry_after("project", "session-a"), 1)
        now[0] += 1
        self.assertEqual(limiter.retry_after("project", "session-a"), 0)
        self.assertEqual(limiter.record_failure("project", "session-a"), 2)
        # Other sessions share the project-level failure budget, preventing
        # cookie rotation from creating unlimited online guesses.
        for _ in range(6):
            limiter.record_failure("project", "session-b")
        self.assertGreater(limiter.retry_after("project", "session-c"), 0)
        limiter.record_success("project", "session-a")
        self.assertEqual(limiter.retry_after("project", "session-c"), 0)

    def test_unlock_is_session_scoped_and_files_are_not_encrypted(self):
        with tempfile.TemporaryDirectory() as directory:
            media = os.path.join(directory, "output.mp4")
            with open(media, "wb") as handle:
                handle.write(b"ordinary-local-media")
            manager = ProjectAccessManager()
            owner = "a" * 32
            other = "b" * 32
            status = manager.set_password("project", directory, owner, "correct horse")
            self.assertTrue(status.protected)
            self.assertTrue(status.unlocked)
            self.assertFalse(manager.require("project", directory, other))
            self.assertFalse(manager.unlock("project", directory, other, "wrong"))
            self.assertTrue(manager.unlock("project", directory, other, "correct horse"))
            with open(media, "rb") as handle:
                self.assertEqual(handle.read(), b"ordinary-local-media")
            with open(manager.metadata_path(directory), "r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            self.assertNotIn("correct horse", json.dumps(metadata))
            self.assertFalse(metadata["encrypted"])
            manager.lock("project", other)
            self.assertFalse(manager.require("project", directory, other))

    def test_two_unlocked_sessions_share_private_outputs_but_not_other_projects(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manager = ProjectAccessManager()
            session_a = "a" * 32
            session_b = "b" * 32
            manager.set_password("first", first, session_a, "first password")
            manager.set_password("second", second, session_a, "second password")
            self.assertTrue(manager.unlock("first", first, session_b, "first password"))

            legacy_private = {
                "private": True,
                "explicit": True,
                "owner_session_id": session_a,
            }
            for session in (session_a, session_b):
                self.assertTrue(manager.require("first", first, session))
            self.assertEqual(
                public_output_policy(legacy_private),
                {"private": True, "explicit": True},
            )
            self.assertFalse(manager.require("second", second, session_b))

    def test_clearing_password_removes_access_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectAccessManager()
            manager.set_password("project", directory, "a" * 32, "long password")
            status = manager.set_password("project", directory, "a" * 32, "")
            self.assertFalse(status.protected)
            self.assertTrue(status.unlocked)
            self.assertFalse(os.path.exists(manager.metadata_path(directory)))

    def test_malformed_lock_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectAccessManager()
            path = manager.metadata_path(directory)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            status = manager.status("project", directory, "a" * 32)
            self.assertTrue(status.protected)
            self.assertFalse(status.unlocked)
            self.assertFalse(
                manager.unlock("project", directory, "a" * 32, "anything")
            )
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": 1,
                    "algorithm": "pbkdf2-sha256",
                    "iterations": 10**12,
                    "salt": base64.b64encode(b"s" * 16).decode("ascii"),
                    "password_hash": base64.b64encode(b"h" * 32).decode("ascii"),
                }, handle)
            self.assertFalse(
                manager.unlock("project", directory, "a" * 32, "anything")
            )

    def test_password_change_revokes_other_unlocked_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectAccessManager()
            owner = "a" * 32
            other = "b" * 32
            manager.set_password("project", directory, owner, "first password")
            self.assertTrue(
                manager.unlock("project", directory, other, "first password")
            )
            self.assertTrue(manager.require("project", directory, other))
            manager.set_password("project", directory, owner, "second password")
            self.assertFalse(manager.require("project", directory, other))
            self.assertFalse(
                manager.unlock("project", directory, other, "first password")
            )
            self.assertTrue(
                manager.unlock("project", directory, other, "second password")
            )

    def test_device_grants_survive_restart_for_multiple_projects_without_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "first")
            second = os.path.join(root, "second")
            os.makedirs(first)
            os.makedirs(second)
            session = "a" * 32
            manager = self._durable_manager(root)
            manager.set_password(
                "first", first, session, "first password", "device", False,
            )
            manager.set_password(
                "second", second, session, "second password", "device", False,
            )

            restarted = self._durable_manager(root)
            self.assertTrue(restarted.require("first", first, session))
            self.assertTrue(restarted.require("second", second, session))
            self.assertFalse(restarted.require("first", first, "b" * 32))

            grants_path = os.path.join(root, "grants.json")
            with open(grants_path, "r", encoding="utf-8") as handle:
                serialized = handle.read()
                payload = json.loads(serialized)
            self.assertNotIn(session, serialized)
            self.assertNotIn("first password", serialized)
            self.assertNotIn("second password", serialized)
            self.assertNotIn("password", serialized.lower())
            self.assertEqual(len(payload["grants"]), 2)
            self.assertTrue(all(
                record["principal_digest"].startswith("principal:v1:")
                and record["project_instance_digest"].startswith("project:v1:")
                and record["credential_revision"].startswith("credential:v1:")
                and record["access_class"] == "local"
                for record in payload["grants"]
            ))
            if os.name != "nt":
                self.assertEqual(os.stat(grants_path).st_mode & 0o777, 0o600)

    def test_local_and_remote_grants_are_separate_and_lock_scopes_are_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "first")
            second = os.path.join(root, "second")
            os.makedirs(first)
            os.makedirs(second)
            session = "a" * 32
            manager = self._durable_manager(root)
            manager.set_password(
                "first", first, session, "first password", "device", False,
            )
            manager.set_password(
                "second", second, session, "second password", "device", False,
            )
            self.assertFalse(manager.require("first", first, session, True))
            self.assertTrue(manager.unlock(
                "first", first, session, "first password", "device", True,
            ))
            self.assertTrue(manager.require("first", first, session, True))

            self.assertEqual(manager.lock("first", session, False), 1)
            self.assertFalse(manager.require("first", first, session, False))
            self.assertTrue(manager.require("first", first, session, True))
            self.assertTrue(manager.require("second", second, session, False))
            self.assertEqual(manager.lock_all(session, False), 1)
            self.assertFalse(manager.require("second", second, session, False))
            self.assertTrue(manager.require("first", first, session, True))

    def test_status_polls_do_not_extend_idle_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "project")
            os.makedirs(project)
            now = [1_000.0]
            manager = self._durable_manager(root, clock=lambda: now[0])
            status = manager.set_password(
                "project", project, "a" * 32, "long password", "device", False,
            )
            original_idle = status.unlock_idle_expires_at
            self.assertEqual(status.unlock_expires_at, 1_000.0 + 30 * 24 * 60 * 60)
            self.assertEqual(original_idle, 1_000.0 + 7 * 24 * 60 * 60)
            grants_path = os.path.join(root, "grants.json")
            with open(grants_path, "rb") as handle:
                original_bytes = handle.read()

            now[0] += 60 * 60
            for _ in range(20):
                polled = manager.status("project", project, "a" * 32, False)
                self.assertEqual(polled.unlock_idle_expires_at, original_idle)
            with open(grants_path, "rb") as handle:
                self.assertEqual(handle.read(), original_bytes)

            now[0] = float(original_idle) + 0.001
            self.assertFalse(manager.require("project", project, "a" * 32))

    def test_real_authorized_activity_slides_idle_but_never_absolute_expiry(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "project")
            os.makedirs(project)
            now = [1_000.0]
            manager = self._durable_manager(root, clock=lambda: now[0])
            initial = manager.set_password(
                "project", project, "a" * 32, "long password", "device", False,
            )
            absolute = float(initial.unlock_expires_at)
            first_idle = float(initial.unlock_idle_expires_at)

            now[0] = first_idle - 60
            self.assertTrue(manager.require("project", project, "a" * 32))
            refreshed = manager.status("project", project, "a" * 32)
            self.assertGreater(refreshed.unlock_idle_expires_at, first_idle)
            self.assertEqual(refreshed.unlock_expires_at, absolute)

            poll_idle = refreshed.unlock_idle_expires_at
            now[0] += 30
            self.assertEqual(
                manager.status(
                    "project", project, "a" * 32,
                ).unlock_idle_expires_at,
                poll_idle,
            )
            current_idle = float(poll_idle)
            while current_idle < absolute:
                now[0] = current_idle - 60
                self.assertTrue(manager.require("project", project, "a" * 32))
                current_idle = float(manager.status(
                    "project", project, "a" * 32,
                ).unlock_idle_expires_at)
            self.assertEqual(current_idle, absolute)
            now[0] = absolute
            self.assertFalse(manager.require("project", project, "a" * 32))

    @unittest.skipIf(os.name == "nt", "directory fsync is POSIX-specific")
    def test_grant_store_fsyncs_file_then_replace_then_parent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._durable_manager(root)
            events = []
            real_fsync = os.fsync
            real_replace = os.replace

            def tracked_fsync(descriptor):
                events.append(
                    "dir-fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode)
                    else "file-fsync"
                )
                return real_fsync(descriptor)

            def tracked_replace(source, destination):
                events.append("replace")
                return real_replace(source, destination)

            with mock.patch.object(os, "fsync", side_effect=tracked_fsync), mock.patch.object(
                os, "replace", side_effect=tracked_replace,
            ):
                manager._save_device_grants([])

            self.assertLess(events.index("file-fsync"), events.index("replace"))
            self.assertLess(events.index("replace"), events.index("dir-fsync"))

    def test_corrupt_grant_store_fails_locked_until_password_is_proved_again(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "project")
            os.makedirs(project)
            session = "a" * 32
            manager = self._durable_manager(root)
            manager.set_password(
                "project", project, session, "long password", "device", False,
            )
            with open(os.path.join(root, "grants.json"), "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            self.assertFalse(manager.require("project", project, session))
            self.assertTrue(manager.unlock(
                "project", project, session, "long password", "device", False,
            ))
            self.assertTrue(manager.require("project", project, session))

    def test_grant_record_hmac_prevents_local_to_remote_store_reclassification(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "project")
            os.makedirs(project)
            session = "a" * 32
            manager = self._durable_manager(root)
            manager.set_password(
                "project", project, session, "long password", "device", False,
            )
            grants_path = os.path.join(root, "grants.json")
            with open(grants_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["grants"][0]["access_class"] = "remote"
            with open(grants_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            self.assertFalse(manager.require("project", project, session, True))
            self.assertFalse(manager.require("project", project, session, False))

    def test_project_instance_and_password_revisions_fence_stale_device_grants(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "project")
            os.makedirs(project)
            session = "a" * 32
            manager = self._durable_manager(root)
            manager.set_password(
                "project", project, session, "first password", "device", False,
            )
            marker = os.path.join(project, ".maestro-project-instance")
            os.remove(marker)
            self.assertFalse(manager.require("project", project, session))
            self.assertFalse(os.path.exists(marker))
            self.assertTrue(manager.unlock(
                "project", project, session, "first password", "device", False,
            ))
            self.assertTrue(os.path.isfile(marker))

            manager.set_password(
                "project", project, session, "second password", "device", False,
            )
            self.assertFalse(manager.unlock(
                "project", project, "b" * 32, "first password", "device", False,
            ))
            manager.set_password("project", project, session, "")
            self.assertFalse(manager.status(
                "project", project, session,
            ).protected)

    def test_unknown_remember_policy_is_rejected_without_authorizing(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ProjectAccessManager()
            with self.assertRaisesRegex(ValueError, "remember"):
                manager.set_password(
                    "project", directory, "a" * 32, "long password", "forever",
                )
            self.assertFalse(os.path.exists(manager.metadata_path(directory)))


class RemoteProjectScopeTests(unittest.TestCase):
    def test_remote_routes_require_explicit_workspace_without_global_fallback(self):
        namespace = _load_launch_functions("_request_project_workspace")
        namespace["_get_active_workspace"] = lambda: "host-global"
        resolve = namespace["_request_project_workspace"]

        remote = type("Remote", (), {
            "state": type("State", (), {"maestro_remote": True})(),
        })()
        local = type("Local", (), {
            "state": type("State", (), {"maestro_remote": False})(),
        })()
        with self.assertRaises(HTTPException) as raised:
            resolve(remote, "")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(resolve(remote, "project-a"), "project-a")
        self.assertEqual(resolve(local, ""), "host-global")


class WorkspaceOperationReservationTests(unittest.TestCase):
    def setUp(self):
        self.namespace = _load_launch_functions(
            "_require_workspace_not_deleting",
            "_WorkspaceOperationReservation",
            "_reserve_workspace_operations",
        )
        self.namespace["_existing_workspace_dir"] = lambda workspace: workspace

    def test_multi_project_reservation_is_atomic_and_blocks_delete_admission(self):
        reserve = self.namespace["_reserve_workspace_operations"]
        operations = self.namespace["_workspace_operations"]
        lifecycle_lock = self.namespace["_workspace_lifecycle_lock"]

        entered = threading.Event()
        release = threading.Event()
        delete_blocked = threading.Event()

        def operation():
            with reserve("source", "target"):
                entered.set()
                release.wait(2)

        def delete_admission():
            entered.wait(2)
            with lifecycle_lock:
                if operations.get("source", 0) or operations.get("target", 0):
                    delete_blocked.set()

        operation_thread = threading.Thread(target=operation)
        delete_thread = threading.Thread(target=delete_admission)
        operation_thread.start()
        delete_thread.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(delete_blocked.wait(1))
        self.assertEqual(operations, {"source": 1, "target": 1})
        release.set()
        operation_thread.join(1)
        delete_thread.join(1)
        self.assertEqual(operations, {})

        self.namespace["_workspaces_deleting"].add("target")
        with self.assertRaises(HTTPException) as raised:
            with reserve("source", "target"):
                pass
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(operations, {})


class LineagePrivacyTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = _load_launch_functions(
            "_OutputLineageMutationGuard",
            "_output_lineage_mutation_guard",
            "_stage_json_replacement",
            "_restore_file_bytes",
            "_recheck_lineage_revisions",
            "_set_lineage_privacy",
        )
        namespace["_revoke_output_shares"] = lambda _workspace, _names: 0
        cls.namespace = namespace
        cls.set_privacy = staticmethod(namespace["_set_lineage_privacy"])

    def _lineage(self, directory: str):
        names = ["final.mp4", "window.mp4", "component.png"]
        originals = {}
        for index, name in enumerate(names):
            Path(directory, name).write_bytes(b"media")
            sidecar = Path(directory, f"{Path(name).stem}.meta.json")
            raw = (
                '{"artifact_lineage":"job:one","artifact_class":"%s",'
                '"private":false,"marker":%d}\n'
                % ("final" if index == 0 else "window", index)
            ).encode("utf-8")
            sidecar.write_bytes(raw)
            originals[sidecar] = raw
        return names, originals

    def test_replace_failure_restores_every_original_sidecar_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            names, originals = self._lineage(directory)
            real_replace = os.replace
            calls = 0

            def fail_second_commit(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected replace failure")
                return real_replace(source, destination)

            with mock.patch.object(os, "replace", side_effect=fail_second_commit):
                with self.assertRaisesRegex(
                    HTTPException, "Could not update output privacy metadata",
                ) as raised:
                    self.set_privacy(
                        directory, names, workspace="project",
                        private=True,
                    )
            self.assertEqual(raised.exception.status_code, 500)
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_staging_write_failure_leaves_every_sidecar_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            names, originals = self._lineage(directory)
            real_open = builtins.open
            staged_writes = 0

            def fail_second_stage(path, mode="r", *args, **kwargs):
                nonlocal staged_writes
                if "w" in mode and str(path).endswith(".tmp"):
                    staged_writes += 1
                    if staged_writes == 2:
                        raise OSError("injected staging failure")
                return real_open(path, mode, *args, **kwargs)

            with mock.patch("builtins.open", side_effect=fail_second_stage):
                with self.assertRaisesRegex(
                    HTTPException, "Could not update output privacy metadata",
                ):
                    self.set_privacy(
                        directory, names, workspace="project",
                        private=True,
                    )
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_revoke_failure_leaves_exact_original_sidecar_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            names, originals = self._lineage(directory)

            def fail_revoke(_workspace, _names):
                raise OSError("injected durable share-store failure")

            previous = self.namespace["_revoke_output_shares"]
            self.namespace["_revoke_output_shares"] = fail_revoke
            try:
                with self.assertRaisesRegex(
                    HTTPException, "Could not update output privacy metadata",
                ):
                    self.set_privacy(
                        directory, names, workspace="project",
                        private=True,
                    )
            finally:
                self.namespace["_revoke_output_shares"] = previous
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_success_changes_the_whole_lineage_together(self):
        with tempfile.TemporaryDirectory() as directory:
            names, _ = self._lineage(directory)
            changed = self.set_privacy(
                directory, names, workspace="project",
                private=True,
            )
            self.assertEqual(changed, names)
            for name in names:
                sidecar = Path(directory, f"{Path(name).stem}.meta.json")
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertTrue(metadata["private"])
                self.assertNotIn("owner_session_id", metadata)

    def test_writer_ignores_and_strips_a_legacy_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            names, originals = self._lineage(directory)
            first = next(iter(originals))
            foreign = json.loads(first.read_text(encoding="utf-8"))
            foreign["private"] = True
            foreign["owner_session_id"] = "f" * 32
            first.write_text(json.dumps(foreign), encoding="utf-8")
            self.set_privacy(
                directory, names, workspace="project",
                private=False,
            )
            for path in originals:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(metadata["private"])
                self.assertNotIn("owner_session_id", metadata)


class LineageMutationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = _load_launch_functions(
            "_stage_json_replacement",
            "_restore_file_bytes",
            "_output_revision_token",
            "_workspace_artifact_snapshot",
            "_authorized_lineage_plan",
            "_OutputLineageMutationGuard",
            "_output_lineage_mutation_guard",
            "_freeze_lineage_revisions",
            "_recheck_lineage_revisions",
            "_set_lineage_privacy",
            "_move_lineage_files",
            "_delete_frozen_output_names",
        )
        cls.plan = staticmethod(cls.namespace["_authorized_lineage_plan"])
        cls.freeze = staticmethod(cls.namespace["_freeze_lineage_revisions"])
        cls.move = staticmethod(cls.namespace["_move_lineage_files"])
        cls.set_privacy = staticmethod(cls.namespace["_set_lineage_privacy"])
        cls.delete = staticmethod(cls.namespace["_delete_frozen_output_names"])
        cls.guard = staticmethod(cls.namespace["_output_lineage_mutation_guard"])
        cls.namespace["_load_favorites"] = lambda workspace: set()
        cls.namespace["_save_favorites"] = lambda favorites, workspace: None

    def setUp(self):
        self.revocations = []
        self.namespace["_revoke_output_shares"] = (
            lambda workspace, names: self.revocations.append(
                (workspace, tuple(names)),
            ) or len(names)
        )

    @staticmethod
    def _write_output(directory: str, name: str, artifact_class: str) -> None:
        Path(directory, name).write_bytes(f"media:{name}".encode("utf-8"))
        Path(directory, f"{Path(name).stem}.meta.json").write_text(
            json.dumps({
                "artifact_lineage": "shared-loose-lineage",
                "artifact_class": artifact_class,
                "job_id": "job-shared",
                "params": {"seed": 7},
                "private": False,
            }),
            encoding="utf-8",
        )

    def _two_finals(self, directory: str) -> list[str]:
        for name, artifact_class in (
            ("final-a.mp4", "final"),
            ("final-b.mp4", "final"),
            ("window.mp4", "window"),
            ("temporary.png", "temporary"),
        ):
            self._write_output(directory, name, artifact_class)
        return ["final-a.mp4", "final-b.mp4", "temporary.png", "window.mp4"]

    def test_plan_never_sweeps_a_second_final_from_a_shared_job_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            self._two_finals(directory)
            plan, _, classes = self.plan(directory, "final-a.mp4")
            self.assertEqual(classes["final-b.mp4"], "final")
            self.assertEqual(plan, ["final-a.mp4", "temporary.png", "window.mp4"])

    def test_move_carries_nonfinals_but_leaves_the_other_final(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            self._two_finals(source)
            plan, _, _ = self.plan(source, "final-a.mp4")
            revisions = self.freeze(source, plan)
            moved = self.move(
                source, target, plan, source_workspace="source",
                target_workspace="target",
                expected_revisions=revisions,
            )
            self.assertEqual(moved, plan)
            self.assertTrue(Path(source, "final-b.mp4").is_file())
            self.assertTrue(Path(source, "final-b.meta.json").is_file())
            for name in plan:
                self.assertFalse(Path(source, name).exists())
                self.assertTrue(Path(target, name).is_file())
                metadata = json.loads(
                    Path(target, f"{Path(name).stem}.meta.json").read_text(encoding="utf-8"),
                )
                self.assertEqual(metadata["workspace"], "target")

    def test_privacy_changes_selected_lineage_without_touching_other_final(self):
        with tempfile.TemporaryDirectory() as directory:
            self._two_finals(directory)
            plan, _, _ = self.plan(directory, "final-a.mp4")
            revisions = self.freeze(directory, plan)
            changed = self.set_privacy(
                directory, plan, workspace="workspace",
                private=True,
                expected_revisions=revisions,
            )
            self.assertEqual(changed, plan)
            for name in plan:
                metadata = json.loads(
                    Path(directory, f"{Path(name).stem}.meta.json").read_text(encoding="utf-8"),
                )
                self.assertTrue(metadata["private"])
            other = json.loads(
                Path(directory, "final-b.meta.json").read_text(encoding="utf-8"),
            )
            self.assertFalse(other["private"])

    def test_legacy_single_output_can_gain_privacy_metadata_and_move(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            Path(source, "legacy.mp4").write_bytes(b"legacy")
            plan, _, _ = self.plan(source, "legacy.mp4")
            self.assertEqual(plan, ["legacy.mp4"])
            revisions = self.freeze(source, plan)
            self.set_privacy(
                source, plan, workspace="source",
                private=True,
                expected_revisions=revisions,
            )
            metadata = json.loads(
                Path(source, "legacy.meta.json").read_text(encoding="utf-8"),
            )
            self.assertTrue(metadata["private"])
            self.assertNotIn("owner_session_id", metadata)

            revisions = self.freeze(source, plan)
            self.move(
                source, target, plan, source_workspace="source",
                target_workspace="target",
                expected_revisions=revisions,
            )
            self.assertFalse(Path(source, "legacy.mp4").exists())
            self.assertTrue(Path(target, "legacy.mp4").is_file())
            self.assertEqual(
                json.loads(Path(target, "legacy.meta.json").read_text(encoding="utf-8"))["workspace"],
                "target",
            )

        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            Path(source, "sidecarless.mp4").write_bytes(b"legacy")
            revisions = self.freeze(source, ["sidecarless.mp4"])
            self.move(
                source, target, ["sidecarless.mp4"],
                source_workspace="source", target_workspace="target",
                expected_revisions=revisions,
            )
            self.assertTrue(Path(target, "sidecarless.mp4").is_file())
            self.assertFalse(Path(target, "sidecarless.meta.json").exists())

    def test_move_collision_preflight_and_fault_both_leave_the_lineage_intact(self):
        import shutil

        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            self._two_finals(source)
            plan, _, _ = self.plan(source, "final-a.mp4")
            revisions = self.freeze(source, plan)
            Path(target, "final-a.mp4").write_bytes(b"collision")
            with self.assertRaisesRegex(HTTPException, "Target already contains"):
                self.move(
                    source, target, plan, source_workspace="source",
                    target_workspace="target",
                    expected_revisions=revisions,
                )
            for name in plan:
                self.assertTrue(Path(source, name).is_file())

        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            self._two_finals(source)
            plan, _, _ = self.plan(source, "final-a.mp4")
            revisions = self.freeze(source, plan)
            real_move = shutil.move
            calls = 0

            def fail_third(source_path, target_path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected move failure")
                return real_move(source_path, target_path, *args, **kwargs)

            with mock.patch.object(shutil, "move", side_effect=fail_third):
                with self.assertRaisesRegex(HTTPException, "Could not move output lineage"):
                    self.move(
                        source, target, plan, source_workspace="source",
                        target_workspace="target",
                        expected_revisions=revisions,
                    )
            for name in plan:
                self.assertTrue(Path(source, name).is_file())
                self.assertTrue(Path(source, f"{Path(name).stem}.meta.json").is_file())
                self.assertFalse(Path(target, name).exists())

    def test_delete_fault_rolls_back_every_member_after_revoking_shares(self):
        with tempfile.TemporaryDirectory() as directory:
            self._two_finals(directory)
            plan, _, classes = self.plan(directory, "final-a.mp4")
            plan.sort(key=lambda candidate: classes.get(candidate) == "final")
            revisions = self.freeze(directory, plan)
            real_replace = os.replace
            calls = 0

            def fail_third(source, target):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected delete staging failure")
                return real_replace(source, target)

            with mock.patch.object(os, "replace", side_effect=fail_third):
                with self.assertRaisesRegex(HTTPException, "Output lineage is locked"):
                    self.delete(
                        directory, "workspace", plan,
                        expected_revisions=revisions,
                    )
            self.assertEqual(self.revocations, [("workspace", tuple(plan))])
            for name in plan:
                self.assertTrue(Path(directory, name).is_file())
                self.assertTrue(Path(directory, f"{Path(name).stem}.meta.json").is_file())
            self.assertTrue(Path(directory, "final-b.mp4").is_file())

    def test_revoke_failure_aborts_move_and_delete_before_any_path_mutation(self):
        def fail_revoke(_workspace, _names):
            raise OSError("injected durable share-store failure")

        self.namespace["_revoke_output_shares"] = fail_revoke
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            self._two_finals(source)
            plan, _, _ = self.plan(source, "final-a.mp4")
            revisions = self.freeze(source, plan)
            before = {
                path.name: path.read_bytes() for path in Path(source).iterdir()
            }
            with self.assertRaisesRegex(OSError, "share-store failure"):
                self.move(
                    source, target, plan, source_workspace="source",
                    target_workspace="target", expected_revisions=revisions,
                )
            self.assertEqual(
                {path.name: path.read_bytes() for path in Path(source).iterdir()},
                before,
            )
            self.assertEqual(list(Path(target).iterdir()), [])

        with tempfile.TemporaryDirectory() as directory:
            self._two_finals(directory)
            plan, _, classes = self.plan(directory, "final-a.mp4")
            plan.sort(key=lambda candidate: classes.get(candidate) == "final")
            revisions = self.freeze(directory, plan)
            before = {
                path.name: path.read_bytes() for path in Path(directory).iterdir()
            }
            with self.assertRaisesRegex(OSError, "share-store failure"):
                self.delete(
                    directory, "workspace", plan,
                    expected_revisions=revisions,
                )
            self.assertEqual(
                {path.name: path.read_bytes() for path in Path(directory).iterdir()},
                before,
            )

    def test_delete_commits_whole_selected_lineage_then_revokes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            self._two_finals(directory)
            plan, _, classes = self.plan(directory, "final-a.mp4")
            plan.sort(key=lambda candidate: classes.get(candidate) == "final")
            revisions = self.freeze(directory, plan)
            deleted, failed = self.delete(
                directory, "workspace", plan, expected_revisions=revisions,
            )
            self.assertEqual(deleted, plan)
            self.assertEqual(failed, [])
            self.assertEqual(self.revocations, [("workspace", tuple(plan))])
            self.assertTrue(Path(directory, "final-b.mp4").is_file())
            for name in plan:
                self.assertFalse(Path(directory, name).exists())
                self.assertFalse(Path(directory, f"{Path(name).stem}.meta.json").exists())

    def test_move_rechecks_every_frozen_member_revision(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            self._two_finals(source)
            plan, _, _ = self.plan(source, "final-a.mp4")
            revisions = self.freeze(source, plan)
            Path(source, "window.mp4").write_bytes(b"changed after snapshot")
            with self.assertRaisesRegex(HTTPException, "window.mp4") as raised:
                self.move(
                    source, target, plan, source_workspace="source",
                    target_workspace="target",
                    expected_revisions=revisions,
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertFalse(any(Path(target).iterdir()))

    def test_same_workspace_guard_serializes_concurrent_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def first():
                with self.guard(directory):
                    first_entered.set()
                    release_first.wait(2)

            def second():
                first_entered.wait(2)
                with self.guard(directory):
                    second_entered.set()

            first_thread = threading.Thread(target=first)
            second_thread = threading.Thread(target=second)
            first_thread.start()
            second_thread.start()
            self.assertTrue(first_entered.wait(1))
            self.assertFalse(second_entered.wait(0.1))
            release_first.set()
            self.assertTrue(second_entered.wait(1))
            first_thread.join(1)
            second_thread.join(1)


class LaunchPrivacyContractTests(unittest.TestCase):
    def test_remote_output_routes_never_fall_back_to_global_active_project(self):
        source = (APP_ROOT / "launch.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in (
            "list_favorites", "toggle_favorite", "list_outputs", "serve_file",
            "get_output_metadata", "create_output_share", "revoke_output_share",
            "rejoin_clips", "get_group_clips", "bulk_output_privacy",
            "bulk_move_outputs", "bulk_delete_outputs", "move_output",
            "delete_output_components", "delete_output",
        ):
            with self.subTest(route=name):
                self.assertIn(
                    "_request_project_workspace",
                    ast.get_source_segment(source, functions[name]) or "",
                )

    def test_routes_are_workspace_scoped_and_bulk_lineage_aware(self):
        source = (APP_ROOT / "launch.py").read_text(encoding="utf-8")
        self.assertIn("def serve_file(request: Request, filename: str, workspace: str = \"\")", source)
        self.assertNotIn("Search all workspace subdirectories", source)
        self.assertNotIn("can_access_output", source)
        self.assertIn("_require_project_access(", source)
        self.assertIn('@api.post("/api/v1/outputs/bulk/move")', source)
        self.assertIn('@api.post("/api/v1/outputs/bulk/privacy")', source)
        self.assertIn('@api.post("/api/v1/outputs/bulk/delete")', source)
        self.assertIn("_authorized_lineage_plan", source)
        self.assertIn("_validate_bulk_item_revision", source)
        bulk = source[source.index("async def bulk_output_privacy"):]
        self.assertIn("with _output_lineage_mutation_guard(out_dir):", bulk)
        self.assertIn("workspace=workspace", bulk)
        privacy_helper = source[
            source.index("def _set_lineage_privacy("):
            source.index("def _revoke_output_shares(")
        ]
        self.assertLess(
            privacy_helper.index("_revoke_output_shares(workspace, unique_names)"),
            privacy_helper.index("commit_started = True"),
        )
        single_move = source[
            source.index("async def move_output("):
            source.index("def _plan_output_component_cleanup", source.index("async def move_output("))
        ]
        for shared_step in (
            "_authorized_lineage_plan(",
            "_freeze_lineage_revisions(",
            "_move_lineage_files(",
        ):
            self.assertIn(shared_step, single_move)
        move_helper = source[
            source.index("def _move_lineage_files("):
            source.index('@api.post("/api/v1/outputs/bulk/privacy")')
        ]
        self.assertLess(
            move_helper.index("_revoke_output_shares(source_workspace, unique_names)"),
            move_helper.index("for target, updated in sidecar_updates"),
        )
        delete_helper = source[
            source.index("def _delete_frozen_output_names("):
            source.index('@api.post("/api/v1/outputs/bulk/delete")')
        ]
        self.assertLess(
            delete_helper.index("_revoke_output_shares(workspace, unique_names)"),
            delete_helper.index("os.replace(source, staged)"),
        )


if __name__ == "__main__":
    unittest.main()
