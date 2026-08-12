import ast
import asyncio
import contextvars
import http.cookies
import json
import multiprocessing
import os
import stat
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in os.sys.path:
    os.sys.path.insert(0, str(APP))

import services.account_auth as account_auth  # noqa: E402
from services.account_auth import (  # noqa: E402
    ACCOUNT_NONCE_PURPOSES,
    ACCOUNT_SESSION_COOKIE_NAME,
    AccountAuthError,
    AccountAuthStore,
    AccountStoreCapacityError,
    AccountStoreCorruptError,
    decode_account_session_cookie,
    encode_account_session_cookie,
    resolve_account_capabilities,
)
from services.output_access import (  # noqa: E402
    SESSION_COOKIE_NAME,
    decode_session_cookie,
    encode_session_cookie,
)

PASSWORD = "correct horse battery staple"
SECOND_PASSWORD = "a different long password"


def _issue_nonce_in_process(path, secret, session_id, start, results):
    store = AccountAuthStore(path, secret, password_n=1024)
    start.wait(10)
    try:
        results.put(("ok", store.issue_nonce(session_id, "login")["purpose"]))
    except Exception as error:  # pragma: no cover - failure returned to parent
        results.put(("error", type(error).__name__))


class _Clock:
    def __init__(self, value=1_800_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AccountAuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "account-auth.json"
        self.secret = b"account-test-secret" * 4
        self.clock = _Clock()
        self.store = self._new_store()
        self.browser_session = "a" * 32

    def _new_store(self):
        return AccountAuthStore(
            str(self.path),
            self.secret,
            clock=self.clock,
            password_n=1024,
            session_ttl_seconds=3600,
            reauth_ttl_seconds=90,
            nonce_ttl_seconds=60,
        )

    def _nonce(self, purpose, session_id=None, store=None):
        return (store or self.store).issue_nonce(
            session_id or self.browser_session, purpose,
        )["nonce"]

    def _bootstrap(self):
        return self.store.bootstrap_owner(
            username="Owner",
            password=PASSWORD,
            email="owner@example.test",
            device_label="Desktop browser",
            nonce_session_id=self.browser_session,
            nonce=self._nonce("bootstrap"),
            remote=False,
        )

    @property
    def marker_path(self):
        return Path(self.store.bootstrap_marker_path)

    def test_pristine_store_is_bootstrapable_without_completion_marker(self):
        self.assertFalse(self.path.exists())
        self.assertFalse(self.marker_path.exists())
        self.assertFalse(self.store.has_accounts())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.marker_path.exists())

        self._nonce("bootstrap")
        self.assertTrue(self.path.exists())
        self.assertFalse(self.marker_path.exists())
        self.assertFalse(self.store.has_accounts())

    def test_bootstrap_is_sealed_private_and_restart_persistent(self):
        result = self._bootstrap()
        principal = self.store.resolve_session(result["account_session_id"])

        self.assertEqual(principal["username"], "Owner")
        self.assertEqual(principal["role"], "owner")
        self.assertTrue(principal["has_email"])
        self.assertFalse(principal["passkey_authentication_available"])
        self.assertEqual(len(result["recovery_codes"]), 10)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(self.marker_path.is_file())
        self.assertEqual(stat.S_IMODE(self.marker_path.stat().st_mode), 0o600)

        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        self.assertNotIn(result["account_session_id"], raw)
        self.assertNotIn(result["recovery_codes"][0], raw)
        self.assertNotIn("owner@example.test".split("@")[0] + "@example.test", json.dumps(result))
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], 1)
        self.assertEqual(marker["owner_id"], result["account"]["id"])
        self.assertNotIn(str(self.path), json.dumps(marker))

        restarted = self._new_store()
        self.assertEqual(
            restarted.resolve_session(result["account_session_id"])["id"],
            result["account"]["id"],
        )
        with self.assertRaises(AccountAuthError) as duplicate:
            restarted.bootstrap_owner(
                username="Other",
                password=PASSWORD,
                email="",
                device_label="Browser",
                nonce_session_id="b" * 32,
                nonce=restarted.issue_nonce("b" * 32, "bootstrap")["nonce"],
                remote=False,
            )
        self.assertEqual(duplicate.exception.code, "bootstrap_complete")

    def test_legacy_nonempty_store_creates_missing_completion_marker(self):
        result = self._bootstrap()
        self.marker_path.unlink()
        self.assertFalse(self.marker_path.exists())

        restarted = self._new_store()
        self.assertTrue(restarted.has_accounts())
        self.assertTrue(self.marker_path.is_file())
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker["owner_id"], result["account"]["id"])

    def test_store_deletion_after_bootstrap_never_reopens_bootstrap(self):
        self._bootstrap()
        self.path.unlink()

        for operation in (
            self.store.has_accounts,
            lambda: self.store.issue_nonce(self.browser_session, "bootstrap"),
        ):
            with self.subTest(operation=operation), self.assertRaises(
                AccountStoreCorruptError,
            ) as unavailable:
                operation()
            self.assertEqual(unavailable.exception.code, "account_store_unavailable")
        self.assertTrue(self.marker_path.exists())

    def test_completion_marker_tamper_links_and_store_binding_fail_closed(self):
        self._bootstrap()
        encoded = self.marker_path.read_bytes()

        marker = json.loads(encoded.decode("utf-8"))
        marker["owner_id"] = "f" * 32
        self.marker_path.write_text(json.dumps(marker), encoding="utf-8")
        if os.name != "nt":
            os.chmod(self.marker_path, 0o600)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()

        self.marker_path.write_bytes(encoded)
        if os.name != "nt":
            os.chmod(self.marker_path, 0o600)
        linked = self.marker_path.with_suffix(".linked")
        try:
            os.link(self.marker_path, linked)
        except (OSError, NotImplementedError):
            linked = None
        if linked is not None:
            with self.assertRaises(AccountStoreCorruptError):
                self.store.has_accounts()
            linked.unlink()

        copied_store_path = self.path.with_name("copied-account-auth.json")
        copied_store_path.write_bytes(self.path.read_bytes())
        copied = AccountAuthStore(
            str(copied_store_path), self.secret, clock=self.clock, password_n=1024,
        )
        Path(copied.bootstrap_marker_path).write_bytes(encoded)
        with self.assertRaises(AccountStoreCorruptError):
            copied.has_accounts()

        target = self.marker_path.with_suffix(".target")
        target.write_bytes(encoded)
        self.marker_path.unlink()
        try:
            self.marker_path.symlink_to(target)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(AccountStoreCorruptError):
                self.store.has_accounts()

    def test_completion_marker_write_failure_leaves_bootstrap_closed(self):
        with patch.object(
            self.store, "_save_bootstrap_marker",
            side_effect=AccountStoreCorruptError(),
        ):
            with self.assertRaises(AccountStoreCorruptError) as unavailable:
                self._bootstrap()
            self.assertEqual(unavailable.exception.code, "account_store_unavailable")
            persisted = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(len(persisted["accounts"]), 1)
            with self.assertRaises(AccountStoreCorruptError):
                self.store.has_accounts()

        restarted = self._new_store()
        self.assertTrue(restarted.has_accounts())
        self.assertTrue(self.marker_path.exists())
        browser = "b" * 32
        with self.assertRaises(AccountAuthError) as duplicate:
            restarted.bootstrap_owner(
                username="Other", password=PASSWORD, email="",
                device_label="Browser", nonce_session_id=browser,
                nonce=restarted.issue_nonce(browser, "bootstrap")["nonce"],
                remote=False,
            )
        self.assertEqual(duplicate.exception.code, "bootstrap_complete")

    def test_windows_acl_cache_is_bound_to_security_descriptor_state(self):
        private = account_auth.hashlib.sha256(b"private").digest()
        repaired = account_auth.hashlib.sha256(b"repaired").digest()
        completed = [
            types.SimpleNamespace(
                returncode=0,
                stdout=account_auth.base64.b64encode(b"private"),
            ),
            types.SimpleNamespace(
                returncode=0,
                stdout=account_auth.base64.b64encode(b"repaired"),
            ),
        ]
        account_auth._windows_acl_cache.clear()
        with patch.object(account_auth.os, "name", "nt"), patch.object(
            account_auth.os, "lstat", return_value=object(),
        ), patch.object(
            account_auth,
            "_windows_security_descriptor_fingerprint",
            side_effect=[b"original", b"drifted-after-verification", repaired],
        ) as descriptor_fingerprint, patch.object(
            account_auth.subprocess, "run", side_effect=completed,
        ) as powershell:
            account_auth._tighten_windows_acl("C:/Maestro/accounts.json", directory=False)
            self.assertEqual(account_auth._windows_acl_cache[
                (os.path.abspath("C:/Maestro/accounts.json"), False)
            ], private)
            account_auth._tighten_windows_acl("C:/Maestro/accounts.json", directory=False)
            self.assertEqual(powershell.call_count, 2)
            self.assertEqual(descriptor_fingerprint.call_count, 2)
            self.assertEqual(account_auth._windows_acl_cache[
                (os.path.abspath("C:/Maestro/accounts.json"), False)
            ], repaired)
            account_auth._tighten_windows_acl("C:/Maestro/accounts.json", directory=False)
            self.assertEqual(powershell.call_count, 2)
            self.assertEqual(descriptor_fingerprint.call_count, 3)
            command = powershell.call_args.args[0][-1]
            self.assertIn("GetOwner([Security.Principal.SecurityIdentifier])", command)
            self.assertIn("GetAccessRules", command)
            self.assertIn("AreAccessRulesProtected", command)
            self.assertIn("GetSecurityDescriptorBinaryForm", command)
            self.assertIn("$actual.InheritanceFlags -ne $inheritance", command)
            self.assertIn("$actual.PropagationFlags -ne", command)
        account_auth._windows_acl_cache.clear()

    def test_windows_security_descriptor_query_failure_is_stable(self):
        account_auth._windows_acl_cache.clear()
        with patch.object(account_auth.os, "name", "nt"), patch.object(
            account_auth.os, "lstat", return_value=types.SimpleNamespace(),
        ), patch.object(
            account_auth,
            "_windows_security_descriptor_fingerprint",
            side_effect=AccountStoreCorruptError(),
        ), self.assertRaises(AccountStoreCorruptError) as unavailable:
            account_auth._tighten_windows_acl("C:/Maestro/accounts.json", directory=False)
        self.assertEqual(unavailable.exception.code, "account_store_unavailable")

    def test_nonce_is_bound_single_use_expiring_and_restart_durable(self):
        nonce = self._nonce("login")
        restarted = self._new_store()
        with self.assertRaises(AccountAuthError) as wrong_session:
            restarted.login(
                username="none",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id="b" * 32,
                nonce=nonce,
                remote=True,
            )
        self.assertEqual(wrong_session.exception.code, "invalid_nonce")

        with self.assertRaises(AccountAuthError) as first:
            restarted.login(
                username="none",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=self.browser_session,
                nonce=nonce,
                remote=True,
            )
        self.assertEqual(first.exception.code, "invalid_credentials")
        with self.assertRaises(AccountAuthError) as replay:
            restarted.login(
                username="none",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=self.browser_session,
                nonce=nonce,
                remote=True,
            )
        self.assertEqual(replay.exception.code, "invalid_nonce")

        expiring = restarted.issue_nonce(self.browser_session, "login")["nonce"]
        self.clock.advance(61)
        with self.assertRaises(AccountAuthError) as expired:
            restarted.login(
                username="none",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=self.browser_session,
                nonce=expiring,
                remote=True,
            )
        self.assertEqual(expired.exception.code, "invalid_nonce")

        exact_browser = "c" * 32
        exact = restarted.issue_nonce(exact_browser, "login")["nonce"]
        self.clock.advance(60)
        with self.assertRaises(AccountAuthError) as exact_expiry:
            restarted.login(
                username="none", password=PASSWORD, device_label="Browser",
                nonce_session_id=exact_browser, nonce=exact, remote=True,
            )
        self.assertEqual(exact_expiry.exception.code, "invalid_nonce")

    def test_nonce_issuance_is_durably_bounded_per_browser(self):
        for _ in range(16):
            self.store.issue_nonce(self.browser_session, "login")
        with self.assertRaises(AccountAuthError) as limited:
            self.store.issue_nonce(self.browser_session, "login")
        self.assertEqual(limited.exception.code, "rate_limited")
        self.assertGreaterEqual(limited.exception.retry_after, 60)

        restarted = self._new_store()
        with self.assertRaises(AccountAuthError) as still_limited:
            restarted.issue_nonce(self.browser_session, "login")
        self.assertEqual(still_limited.exception.code, "rate_limited")
        self.clock.advance(121)
        self.assertEqual(
            restarted.issue_nonce(self.browser_session, "login")["purpose"],
            "login",
        )

    def test_global_nonce_bound_evicts_instead_of_locking_authentication(self):
        issued = []
        with patch("services.account_auth._MAX_RECENT_NONCES", 4):
            for index in range(6):
                session_id = f"{index + 1:032x}"
                issued.append(self.store.issue_nonce(session_id, "login"))
            payload = self.store._load()
            self.assertEqual(len(payload["nonces"]), 4)
            newest_session = f"{6:032x}"
            with self.assertRaises(AccountAuthError) as consumed:
                self.store.login(
                    username="missing",
                    password=PASSWORD,
                    device_label="Browser",
                    nonce_session_id=newest_session,
                    nonce=issued[-1]["nonce"],
                    remote=True,
                )
            self.assertEqual(consumed.exception.code, "invalid_credentials")

    def test_login_rotates_authenticated_session_and_never_accepts_email_alone(self):
        boot = self._bootstrap()
        old_session = boot["account_session_id"]
        nonce = self.store.issue_nonce(self.browser_session, "login")["nonce"]
        login = self.store.login(
            username="owner",
            password=PASSWORD,
            device_label="Phone",
            nonce_session_id=self.browser_session,
            presented_account_session_id=old_session,
            nonce=nonce,
            remote=True,
        )
        self.assertNotEqual(login["account_session_id"], old_session)
        self.assertIsNone(self.store.resolve_session(old_session))
        self.assertEqual(self.store.resolve_session(login["account_session_id"])["role"], "owner")

        anonymous = "c" * 32
        with self.assertRaises(AccountAuthError) as email_login:
            self.store.login(
                username="owner@example.test",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=anonymous,
                nonce=self.store.issue_nonce(anonymous, "login")["nonce"],
                remote=True,
            )
        self.assertEqual(email_login.exception.code, "invalid_credentials")

    def test_session_listing_single_revoke_and_revoke_all(self):
        boot = self._bootstrap()
        current = boot["account_session_id"]
        second_browser = "d" * 32
        second = self.store.login(
            username="Owner",
            password=PASSWORD,
            device_label="Tablet",
            nonce_session_id=second_browser,
            nonce=self.store.issue_nonce(second_browser, "login")["nonce"],
            remote=True,
        )["account_session_id"]

        sessions = self.store.list_sessions(current)
        tablet = next(item for item in sessions if item["device_label"] == "Tablet")
        revoked = self.store.revoke_session(
            actor_session_id=current,
            target_handle=tablet["id"],
            nonce=self.store.issue_nonce(current, "revoke_session")["nonce"],
        )
        self.assertFalse(revoked["current"])
        self.assertIsNone(self.store.resolve_session(second))

        result = self.store.revoke_all_sessions(
            actor_session_id=current,
            nonce=self.store.issue_nonce(current, "revoke_all_sessions")["nonce"],
            retain_current=False,
        )
        self.assertTrue(result["current_revoked"])
        self.assertIsNone(self.store.resolve_session(current))

    def test_user_role_can_manage_own_sessions_but_not_accounts(self):
        owner = self._bootstrap()["account_session_id"]
        created = self.store.create_account(
            actor_session_id=owner,
            nonce=self.store.issue_nonce(owner, "create_account")["nonce"],
            username="Creator",
            password=PASSWORD,
            email="",
        )
        self.assertEqual(created["account"]["role"], "user")
        user_browser = "e" * 32
        user = self.store.login(
            username="creator",
            password=PASSWORD,
            device_label="Creator laptop",
            nonce_session_id=user_browser,
            nonce=self.store.issue_nonce(user_browser, "login")["nonce"],
            remote=True,
        )["account_session_id"]
        user = self.store.reauthenticate(
            account_session_id=user,
            password=PASSWORD,
            nonce=self.store.issue_nonce(user, "reauth")["nonce"],
        )["account_session_id"]
        self.assertEqual(len(self.store.list_sessions(user)), 1)
        with self.assertRaises(AccountAuthError) as denied:
            self.store.list_accounts(user)
        self.assertEqual(denied.exception.code, "owner_required")

    def test_disabled_account_revokes_sessions_and_refuses_login(self):
        owner = self._bootstrap()["account_session_id"]
        created = self.store.create_account(
            actor_session_id=owner,
            nonce=self.store.issue_nonce(owner, "create_account")["nonce"],
            username="DisabledSoon",
            password=PASSWORD,
        )
        browser = "f" * 32
        user = self.store.login(
            username="DisabledSoon",
            password=PASSWORD,
            device_label="Browser",
            nonce_session_id=browser,
            nonce=self.store.issue_nonce(browser, "login")["nonce"],
            remote=True,
        )["account_session_id"]
        self.store.set_account_disabled(
            actor_session_id=owner,
            account_id=created["account"]["id"],
            disabled=True,
            nonce=self.store.issue_nonce(owner, "disable_account")["nonce"],
        )
        self.assertIsNone(self.store.resolve_session(user))

        retry_browser = "1" * 32
        with self.assertRaises(AccountAuthError) as rejected:
            self.store.login(
                username="DisabledSoon",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=retry_browser,
                nonce=self.store.issue_nonce(retry_browser, "login")["nonce"],
                remote=True,
            )
        self.assertEqual(rejected.exception.code, "invalid_credentials")

    def test_recovery_consumes_code_revokes_sessions_and_rotates_credentials(self):
        boot = self._bootstrap()
        old_session = boot["account_session_id"]
        recovery_browser = "2" * 32
        recovered = self.store.recover(
            username="Owner",
            recovery_code=boot["recovery_codes"][0].lower(),
            new_password=SECOND_PASSWORD,
            device_label="Recovered browser",
            nonce_session_id=recovery_browser,
            nonce=self.store.issue_nonce(recovery_browser, "recover")["nonce"],
            remote=True,
        )
        self.assertIsNone(self.store.resolve_session(old_session))
        self.assertIsNotNone(self.store.resolve_session(recovered["account_session_id"]))
        self.assertNotEqual(recovered["recovery_codes"], boot["recovery_codes"])

        replay_browser = "3" * 32
        with self.assertRaises(AccountAuthError) as replay:
            self.store.recover(
                username="Owner",
                recovery_code=boot["recovery_codes"][0],
                new_password=PASSWORD,
                device_label="Browser",
                nonce_session_id=replay_browser,
                nonce=self.store.issue_nonce(replay_browser, "recover")["nonce"],
                remote=True,
            )
        self.assertEqual(replay.exception.code, "invalid_recovery")

    def test_cross_account_recovery_revokes_displaced_presented_bearer(self):
        owner = self._bootstrap()["account_session_id"]
        created = self.store.create_account(
            actor_session_id=owner,
            nonce=self.store.issue_nonce(owner, "create_account")["nonce"],
            username="RecoverableUser", password=PASSWORD,
        )
        browser = "4" * 31 + "a"
        recovered = self.store.recover(
            username="RecoverableUser",
            recovery_code=created["recovery_codes"][0],
            new_password=SECOND_PASSWORD,
            device_label="Recovered user",
            nonce_session_id=browser,
            nonce=self.store.issue_nonce(browser, "recover")["nonce"],
            remote=True,
            presented_account_session_id=owner,
        )
        self.assertIsNone(self.store.resolve_session(owner))
        principal = self.store.resolve_session(recovered["account_session_id"])
        self.assertEqual(principal["username"], "RecoverableUser")

    def test_rate_limit_state_survives_restart(self):
        self._bootstrap()
        browser = "4" * 32
        latest = None
        for _ in range(6):
            with self.assertRaises(AccountAuthError) as rejected:
                self.store.login(
                    username="Owner",
                    password="wrong password value",
                    device_label="Browser",
                    nonce_session_id=browser,
                    nonce=self.store.issue_nonce(browser, "login")["nonce"],
                    remote=True,
                )
            latest = rejected.exception
        self.assertGreater(latest.retry_after, 0)
        restarted = self._new_store()
        with self.assertRaises(AccountAuthError) as limited:
            restarted.login(
                username="Owner",
                password=PASSWORD,
                device_label="Browser",
                nonce_session_id=browser,
                nonce=restarted.issue_nonce(browser, "login")["nonce"],
                remote=True,
            )
        self.assertEqual(limited.exception.code, "rate_limited")

    def test_identity_rate_limit_survives_cookie_discard(self):
        self._bootstrap()
        first_browser = "5" * 32
        for attempt in range(13):
            with self.assertRaises(AccountAuthError):
                self.store.login(
                    username="Owner",
                    password="wrong password value",
                    device_label="Browser",
                    nonce_session_id=first_browser,
                    nonce=self.store.issue_nonce(first_browser, "login")["nonce"],
                    remote=True,
                )
            if attempt < 12:
                self.clock.advance(301)
        fresh_browser = "6" * 32
        with self.assertRaises(AccountAuthError) as limited:
            self.store.login(
                username="Owner",
                password=PASSWORD,
                device_label="Fresh cookie",
                nonce_session_id=fresh_browser,
                nonce=self.store.issue_nonce(fresh_browser, "login")["nonce"],
                remote=True,
            )
        self.assertEqual(limited.exception.code, "rate_limited")

    def test_browser_and_global_kdf_admission_survive_identifier_rotation(self):
        self._bootstrap()
        browser = "7" * 32
        with patch.object(
            self.store, "_verify_password", wraps=self.store._verify_password,
        ) as verifier:
            for index in range(6):
                with self.assertRaises(AccountAuthError) as rejected:
                    self.store.login(
                        username=f"Unknown{index}", password=PASSWORD,
                        device_label="Browser", nonce_session_id=browser,
                        nonce=self.store.issue_nonce(browser, "login")["nonce"],
                        remote=True, source_id="192.0.2.1",
                    )
                self.assertEqual(rejected.exception.code, "invalid_credentials")
            with self.assertRaises(AccountAuthError) as browser_limited:
                self.store.login(
                    username="Unknown6", password=PASSWORD,
                    device_label="Browser", nonce_session_id=browser,
                    nonce=self.store.issue_nonce(browser, "login")["nonce"],
                    remote=True, source_id="192.0.2.1",
                )
            self.assertEqual(browser_limited.exception.code, "rate_limited")
            self.assertEqual(verifier.call_count, 6)

        global_path = Path(self.temporary.name) / "global-limit.json"
        global_store = AccountAuthStore(
            str(global_path), self.secret, clock=self.clock, password_n=1024,
            session_ttl_seconds=3600, reauth_ttl_seconds=90,
            nonce_ttl_seconds=60,
        )
        bootstrap_browser = "6" * 32
        global_store.bootstrap_owner(
            username="Owner", password=PASSWORD, email="", device_label="Browser",
            nonce_session_id=bootstrap_browser,
            nonce=global_store.issue_nonce(bootstrap_browser, "bootstrap")["nonce"],
            remote=False,
        )
        with patch.object(
            global_store, "_verify_password", wraps=global_store._verify_password,
        ) as verifier:
            for index in range(13):
                rotating_browser = f"{index + 100:032x}"
                with self.assertRaises(AccountAuthError):
                    global_store.login(
                        username=f"Rotating{index}", password=PASSWORD,
                        device_label="Browser",
                        nonce_session_id=rotating_browser,
                        nonce=global_store.issue_nonce(rotating_browser, "login")["nonce"],
                        remote=True, source_id=f"192.0.2.{index + 10}",
                    )
            blocked_browser = "8" * 32
            with self.assertRaises(AccountAuthError) as global_limited:
                global_store.login(
                    username="NeverSeen", password=PASSWORD,
                    device_label="Browser", nonce_session_id=blocked_browser,
                    nonce=global_store.issue_nonce(blocked_browser, "login")["nonce"],
                    remote=True, source_id="198.51.100.2",
                )
            self.assertEqual(global_limited.exception.code, "rate_limited")
            self.assertEqual(verifier.call_count, 13)

    def test_malformed_username_never_collides_with_real_invalid_account(self):
        owner = self._bootstrap()["account_session_id"]
        self.store.create_account(
            actor_session_id=owner,
            nonce=self.store.issue_nonce(owner, "create_account")["nonce"],
            username="invalid", password=PASSWORD,
        )
        malformed_browser = "a" * 31 + "b"
        with self.assertRaises(AccountAuthError) as malformed:
            self.store.login(
                username="\0invalid", password=PASSWORD, device_label="Browser",
                nonce_session_id=malformed_browser,
                nonce=self.store.issue_nonce(malformed_browser, "login")["nonce"],
                remote=True,
            )
        self.assertEqual(malformed.exception.code, "invalid_credentials")
        valid_browser = "a" * 31 + "c"
        result = self.store.login(
            username="invalid", password=PASSWORD, device_label="Browser",
            nonce_session_id=valid_browser,
            nonce=self.store.issue_nonce(valid_browser, "login")["nonce"],
            remote=True,
        )
        self.assertEqual(result["account"]["username"], "invalid")

    def test_unknown_and_disabled_login_still_runs_password_verifier(self):
        boot = self._bootstrap()
        created = self.store.create_account(
            actor_session_id=boot["account_session_id"],
            nonce=self.store.issue_nonce(
                boot["account_session_id"], "create_account",
            )["nonce"],
            username="DisabledVerifier",
            password=PASSWORD,
        )
        self.store.set_account_disabled(
            actor_session_id=boot["account_session_id"],
            account_id=created["account"]["id"],
            disabled=True,
            nonce=self.store.issue_nonce(
                boot["account_session_id"], "disable_account",
            )["nonce"],
        )
        with patch.object(
            self.store, "_verify_password", wraps=self.store._verify_password,
        ) as verifier:
            unknown_browser = "7" * 32
            with self.assertRaises(AccountAuthError):
                self.store.login(
                    username="Unknown",
                    password=PASSWORD,
                    device_label="Browser",
                    nonce_session_id=unknown_browser,
                    nonce=self.store.issue_nonce(unknown_browser, "login")["nonce"],
                    remote=True,
                )
            self.assertEqual(verifier.call_count, 1)
            disabled_browser = "9" * 32
            with self.assertRaises(AccountAuthError):
                self.store.login(
                    username="DisabledVerifier",
                    password=PASSWORD,
                    device_label="Browser",
                    nonce_session_id=disabled_browser,
                    nonce=self.store.issue_nonce(disabled_browser, "login")["nonce"],
                    remote=True,
                )
            self.assertEqual(verifier.call_count, 2)

    def test_session_inventory_omits_expired_and_updates_bounded_activity(self):
        self._bootstrap()
        self.clock.advance(3500)
        browser = "8" * 32
        current = self.store.login(
            username="Owner",
            password=PASSWORD,
            device_label="Current",
            nonce_session_id=browser,
            nonce=self.store.issue_nonce(browser, "login")["nonce"],
            remote=True,
        )["account_session_id"]
        self.clock.advance(101)
        listed = self.store.list_sessions(current)
        self.assertEqual([item["device_label"] for item in listed], ["Current"])
        original_seen = listed[0]["last_seen_at"]
        self.clock.advance(300)
        self.store.resolve_session(current)
        refreshed = self.store.list_sessions(current)[0]
        self.assertGreater(refreshed["last_seen_at"], original_seen)

    def test_session_inventory_persists_expired_sibling_clock_high_water(self):
        first = self._bootstrap()["account_session_id"]
        self.clock.advance(1000)
        browser = "8" * 32
        current = self.store.login(
            username="Owner",
            password=PASSWORD,
            device_label="Current",
            nonce_session_id=browser,
            nonce=self.store.issue_nonce(browser, "login")["nonce"],
            remote=True,
        )["account_session_id"]
        self.clock.advance(2700)

        self.assertEqual(
            [item["device_label"] for item in self.store.list_sessions(current)],
            ["Current"],
        )
        self.assertGreaterEqual(
            self.store._load()["clock_high_water"], self.clock.value,
        )

        self.clock.advance(-1700)
        restarted = self._new_store()
        self.assertIsNone(restarted.resolve_session(first))
        self.assertEqual(
            [item["device_label"] for item in restarted.list_sessions(current)],
            ["Current"],
        )

    def test_container_email_and_device_labels_return_stable_errors(self):
        for field, value, code in (
            ("email", [], "invalid_email"),
            ("email", {}, "invalid_email"),
            ("device_label", [], "invalid_device_label"),
            ("device_label", {}, "invalid_device_label"),
        ):
            with self.subTest(field=field, container=type(value).__name__):
                arguments = {
                    "username": "Owner",
                    "password": PASSWORD,
                    "email": "",
                    "device_label": "Browser",
                    "nonce_session_id": self.browser_session,
                    "nonce": self._nonce("bootstrap"),
                    "remote": False,
                }
                arguments[field] = value
                with self.assertRaises(AccountAuthError) as invalid:
                    self.store.bootstrap_owner(**arguments)
                self.assertEqual(invalid.exception.code, code)
                self.assertFalse(self.store.has_accounts())

    def test_malformed_and_tampered_stores_fail_closed(self):
        malformed = b"{}"
        self.path.write_bytes(malformed)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()
        self.assertEqual(self.path.read_bytes(), malformed)
        self.assertFalse(self.marker_path.exists())

        self.path.unlink()
        boot = self._bootstrap()
        marker = self.marker_path.read_bytes()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["accounts"][0]["role"] = "owner"
        payload["accounts"][0]["username"] = "Tampered"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(AccountStoreCorruptError):
            self.store.resolve_session(boot["account_session_id"])
        self.assertEqual(self.marker_path.read_bytes(), marker)

    def test_two_processes_serialize_full_load_modify_save(self):
        if os.name == "nt":
            self.skipTest("POSIX process regression; Windows lock path is unit-reviewed")
        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        workers = [
            context.Process(
                target=_issue_nonce_in_process,
                args=(str(self.path), self.secret, f"{index + 1:032x}", start, results),
            )
            for index in range(2)
        ]
        for worker in workers:
            worker.start()
        start.set()
        outcomes = [results.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(outcomes.count(("ok", "login")), 2)
        self.assertEqual(len(self.store._load()["nonces"]), 2)

    def test_two_instances_cannot_both_consume_one_nonce(self):
        self._bootstrap()
        browser = "b" * 32
        nonce = self.store.issue_nonce(browser, "login")["nonce"]
        stores = [self._new_store(), self._new_store()]
        barrier = threading.Barrier(2)
        outcomes = []
        outcomes_lock = threading.Lock()

        def consume(store):
            barrier.wait()
            try:
                store.login(
                    username="Owner", password=PASSWORD, device_label="Browser",
                    nonce_session_id=browser, nonce=nonce, remote=False,
                )
                outcome = "ok"
            except AccountAuthError as error:
                outcome = error.code
            with outcomes_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=consume, args=(store,)) for store in stores]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["ok", "invalid_nonce"])

    def test_two_instances_serialize_kdf_without_holding_store_lock(self):
        self._bootstrap()
        stores = [self._new_store(), self._new_store()]
        browsers = ["b" * 31 + "1", "b" * 31 + "2"]
        nonces = [
            stores[index].issue_nonce(browsers[index], "login")["nonce"]
            for index in range(2)
        ]
        active = 0
        maximum = 0
        activity_lock = threading.Lock()
        start = threading.Barrier(2)
        outcomes = []

        def verifier(password, record):
            nonlocal active, maximum
            with activity_lock:
                active += 1
                maximum = max(maximum, active)
            # A different store operation must remain available while scrypt
            # admission is held; this would block if the store lock leaked.
            probe = self._new_store().issue_nonce("c" * 31 + "1", "login")
            self.assertEqual(probe["purpose"], "login")
            try:
                return AccountAuthStore._verify_password(password, record)
            finally:
                with activity_lock:
                    active -= 1

        def login(index):
            start.wait()
            with patch.object(stores[index], "_verify_password", side_effect=verifier):
                try:
                    stores[index].login(
                        username="Owner", password=PASSWORD,
                        device_label=f"Browser {index}",
                        nonce_session_id=browsers[index], nonce=nonces[index],
                        remote=False,
                    )
                    outcomes.append("ok")
                except AccountAuthError as error:
                    outcomes.append(error.code)

        threads = [threading.Thread(target=login, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ["ok", "ok"])
        self.assertEqual(maximum, 1)

    def test_store_size_cap_preserves_previous_file(self):
        self._bootstrap()
        previous = self.path.read_bytes()
        capped = AccountAuthStore(
            str(self.path), self.secret, clock=self.clock, password_n=1024,
            max_store_bytes=max(1024, len(previous) + 32),
        )
        with self.assertRaises(AccountStoreCapacityError):
            capped.issue_nonce("c" * 32, "login")
        self.assertEqual(self.path.read_bytes(), previous)

    def test_private_modes_are_repaired_and_schema_is_strict(self):
        self._bootstrap()
        lock_path = Path(str(self.path) + ".lock")
        if os.name != "nt":
            os.chmod(self.path.parent, 0o777)
            os.chmod(self.path, 0o666)
            os.chmod(lock_path, 0o666)
            self.assertTrue(self.store.has_accounts())
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["accounts"][0]["unexpected"] = True
        payload["seal"] = self.store._seal(payload)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()

    def test_exact_store_size_boundary_hardlinks_and_owner_invariant_fail_closed(self):
        self._bootstrap()
        encoded = self.path.read_bytes()
        exact_limit = len(encoded) + 17
        exact = AccountAuthStore(
            str(self.path), self.secret, clock=self.clock, password_n=1024,
            max_store_bytes=exact_limit,
        )
        self.path.write_bytes(encoded + b" " * 17)
        self.assertTrue(exact.has_accounts())
        self.path.write_bytes(encoded + b" " * 18)
        with self.assertRaises(AccountStoreCorruptError):
            exact.has_accounts()

        self.path.write_bytes(encoded)
        linked = self.path.with_suffix(".linked")
        try:
            os.link(self.path, linked)
        except (OSError, NotImplementedError):
            linked = None
        if linked is not None:
            with self.assertRaises(AccountStoreCorruptError):
                self.store.has_accounts()
            linked.unlink()

        payload = json.loads(encoded.decode("utf-8"))
        payload["accounts"][0]["role"] = "user"
        payload["seal"] = self.store._seal(payload)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()

        payload = json.loads(encoded.decode("utf-8"))
        second_owner = json.loads(json.dumps(payload["accounts"][0]))
        second_owner.update({
            "id": "f" * 32, "username": "OtherOwner",
            "username_key": "otherowner", "email": "",
        })
        payload["accounts"].append(second_owner)
        payload["seal"] = self.store._seal(payload)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()

        payload = json.loads(encoded.decode("utf-8"))
        payload["sessions"][0]["created_at"] = (
            payload["accounts"][0]["created_at"] - 1
        )
        payload["sessions"][0]["last_seen_at"] = payload["sessions"][0]["created_at"]
        payload["seal"] = self.store._seal(payload)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        with self.assertRaises(AccountStoreCorruptError):
            self.store.has_accounts()

    def test_recursion_and_directory_fsync_failures_are_stable_store_errors(self):
        self._bootstrap()
        with patch("services.account_auth.json.loads", side_effect=RecursionError):
            with self.assertRaises(AccountStoreCorruptError) as recursive:
                self.store.has_accounts()
        self.assertEqual(recursive.exception.code, "account_store_unavailable")
        with patch.object(
            self.store, "_validate_payload", side_effect=RecursionError,
        ):
            with self.assertRaises(AccountStoreCorruptError) as validation_recursive:
                self.store.has_accounts()
        self.assertEqual(
            validation_recursive.exception.code, "account_store_unavailable",
        )

        with patch(
            "services.account_auth._fsync_directory",
            side_effect=OSError("flush failed"),
        ):
            with self.assertRaises(AccountStoreCorruptError) as flush:
                self.store.issue_nonce("d" * 32, "login")
        self.assertEqual(flush.exception.code, "account_store_unavailable")
        self.assertTrue(self._new_store().has_accounts())

    def test_private_directory_creation_failure_is_stable_store_error(self):
        with patch(
            "services.account_auth.os.makedirs",
            side_effect=PermissionError("directory denied"),
        ), self.assertRaises(AccountStoreCorruptError) as unavailable:
            self.store.has_accounts()
        self.assertEqual(unavailable.exception.code, "account_store_unavailable")

    def test_attempt_pruning_preserves_current_identity_and_newest_records(self):
        self._bootstrap()
        payload = self.store._load()
        protected = frozenset({"login-identity:" + "1" * 64})
        payload["attempts"] = {
            "login-global:" + "0" * 64: {
                "failures": 20, "blocked_until": self.clock.value + 300,
            },
            "login-other:" + "1" * 64: {
                "failures": 1, "blocked_until": self.clock.value - 90_000,
            },
            "login-other:" + "2" * 64: {
                "failures": 1, "blocked_until": self.clock.value + 5,
            },
        }
        with patch("services.account_auth._MAX_ATTEMPTS", 3):
            self.store._record_failure(
                payload, next(iter(protected)), self.clock.value, 1,
                protected=protected,
            )
            self.assertIn(next(iter(protected)), payload["attempts"])
            self.assertNotIn("login-other:" + "1" * 64, payload["attempts"])
            self.store._save(payload)
            loaded = self.store._load()
        self.assertNotIn("login-other:" + "1" * 64, loaded["attempts"])
        self.assertIn("login-other:" + "2" * 64, loaded["attempts"])
        self.assertIn("login-global:" + "0" * 64, loaded["attempts"])

    def test_attempt_capacity_is_reserved_before_any_login_kdf(self):
        self._bootstrap()
        payload = self.store._load()
        payload["attempts"] = {
            f"recover-identity:{index:064x}": {
                "failures": 1, "blocked_until": self.clock.value,
            }
            for index in range(3)
        }
        with patch("services.account_auth._MAX_ATTEMPTS", 3):
            self.store._save(payload)
            browser = "d" * 32
            nonce = self.store.issue_nonce(browser, "login")["nonce"]
            with patch.object(
                self.store, "_verify_password",
                side_effect=AssertionError("scrypt reached"),
            ):
                with self.assertRaises(AccountAuthError) as limited:
                    self.store.login(
                        username="Unknown", password=PASSWORD,
                        device_label="Browser", nonce_session_id=browser,
                        nonce=nonce, remote=True,
                    )
            self.assertEqual(limited.exception.code, "rate_limited")

    def test_blocked_or_unauthorized_paths_do_not_hash_new_password(self):
        boot = self._bootstrap()
        with patch.object(
            self.store, "_password_record", side_effect=AssertionError("scrypt reached"),
        ):
            with self.assertRaises(AccountAuthError) as duplicate:
                self.store.bootstrap_owner(
                    username="Other", password=PASSWORD, email="",
                    device_label="Browser", nonce_session_id="d" * 32,
                    nonce=self.store.issue_nonce("d" * 32, "bootstrap")["nonce"],
                    remote=False,
                )
            self.assertEqual(duplicate.exception.code, "bootstrap_complete")
            with self.assertRaises(AccountAuthError) as bad_nonce:
                self.store.change_password(
                    session_id=boot["account_session_id"],
                    new_password=SECOND_PASSWORD,
                    nonce="invalid",
                )
            self.assertEqual(bad_nonce.exception.code, "invalid_nonce")
            recovery_browser = "9" * 32
            with self.assertRaises(AccountAuthError) as invalid_recovery:
                self.store.recover(
                    username="Owner", recovery_code="not-a-code",
                    new_password=SECOND_PASSWORD, device_label="Browser",
                    nonce_session_id=recovery_browser,
                    nonce=self.store.issue_nonce(recovery_browser, "recover")["nonce"],
                    remote=False,
                )
            self.assertEqual(invalid_recovery.exception.code, "invalid_recovery")
        with patch.object(
            self.store, "_verify_password", side_effect=AssertionError("scrypt reached"),
        ):
            with self.assertRaises(AccountAuthError) as invalid_reauth_nonce:
                self.store.reauthenticate(
                    account_session_id=boot["account_session_id"],
                    password=PASSWORD,
                    nonce="invalid",
                )
            self.assertEqual(invalid_reauth_nonce.exception.code, "invalid_nonce")
            login_browser = "8" * 32
            with self.assertRaises(AccountAuthError) as invalid_login_nonce:
                self.store.login(
                    username="Owner", password=PASSWORD, device_label="Browser",
                    nonce_session_id=login_browser, nonce="invalid", remote=False,
                )
            self.assertEqual(invalid_login_nonce.exception.code, "invalid_nonce")

        browser = "7" * 32
        for _ in range(6):
            with self.assertRaises(AccountAuthError):
                self.store.login(
                    username="Owner", password="wrong password value",
                    device_label="Browser", nonce_session_id=browser,
                    nonce=self.store.issue_nonce(browser, "login")["nonce"],
                    remote=False,
                )
        with patch.object(
            self.store, "_verify_password", side_effect=AssertionError("scrypt reached"),
        ):
            with self.assertRaises(AccountAuthError) as blocked_login:
                self.store.login(
                    username="Owner", password=PASSWORD, device_label="Browser",
                    nonce_session_id=browser,
                    nonce=self.store.issue_nonce(browser, "login")["nonce"],
                    remote=False,
                )
        self.assertEqual(blocked_login.exception.code, "rate_limited")

        account_session = boot["account_session_id"]
        for _ in range(4):
            with self.assertRaises(AccountAuthError):
                self.store.reauthenticate(
                    account_session_id=account_session,
                    password="wrong password value",
                    nonce=self.store.issue_nonce(account_session, "reauth")["nonce"],
                )
        with patch.object(
            self.store, "_verify_password", side_effect=AssertionError("scrypt reached"),
        ):
            with self.assertRaises(AccountAuthError) as blocked_reauth:
                self.store.reauthenticate(
                    account_session_id=account_session,
                    password=PASSWORD,
                    nonce=self.store.issue_nonce(account_session, "reauth")["nonce"],
                )
        self.assertEqual(blocked_reauth.exception.code, "rate_limited")

    def test_backward_clock_cannot_corrupt_mutation_paths(self):
        operations = ("login", "reauth", "change", "logout", "recover")
        for index, operation in enumerate(operations):
            with self.subTest(operation=operation):
                path = Path(self.temporary.name) / f"clock-{index}.json"
                clock = _Clock(1000)
                store = AccountAuthStore(
                    str(path), self.secret, clock=clock, password_n=1024,
                    session_ttl_seconds=3600, reauth_ttl_seconds=90,
                    nonce_ttl_seconds=60,
                )
                browser = f"{index + 1:032x}"
                bootstrap = store.bootstrap_owner(
                    username="Owner", password=PASSWORD, email="",
                    device_label="Browser", nonce_session_id=browser,
                    nonce=store.issue_nonce(browser, "bootstrap")["nonce"],
                    remote=False,
                )
                account_session = bootstrap["account_session_id"]
                clock.value = 900
                if operation == "login":
                    login_browser = f"{index + 100:032x}"
                    store.login(
                        username="Owner", password=PASSWORD,
                        device_label="Browser", nonce_session_id=login_browser,
                        nonce=store.issue_nonce(login_browser, "login")["nonce"],
                        remote=False,
                    )
                elif operation == "reauth":
                    store.reauthenticate(
                        account_session_id=account_session, password=PASSWORD,
                        nonce=store.issue_nonce(account_session, "reauth")["nonce"],
                    )
                elif operation == "change":
                    store.change_password(
                        session_id=account_session, new_password=SECOND_PASSWORD,
                        nonce=store.issue_nonce(
                            account_session, "change_password",
                        )["nonce"],
                    )
                elif operation == "logout":
                    principal = store.resolve_session(account_session)
                    store.revoke_session(
                        actor_session_id=account_session,
                        target_handle=principal["session_handle"],
                        nonce=store.issue_nonce(
                            account_session, "revoke_session",
                        )["nonce"],
                    )
                else:
                    recovery_browser = f"{index + 200:032x}"
                    store.recover(
                        username="Owner",
                        recovery_code=bootstrap["recovery_codes"][0],
                        new_password=SECOND_PASSWORD,
                        device_label="Recovered",
                        nonce_session_id=recovery_browser,
                        nonce=store.issue_nonce(
                            recovery_browser, "recover",
                        )["nonce"],
                        remote=False,
                    )
                restarted = AccountAuthStore(
                    str(path), self.secret, clock=clock, password_n=1024,
                    session_ttl_seconds=3600, reauth_ttl_seconds=90,
                    nonce_ttl_seconds=60,
                )
                self.assertTrue(restarted.has_accounts())

    def test_reauthentication_rotates_bearer_and_cookie_signature(self):
        old = self._bootstrap()["account_session_id"]
        result = self.store.reauthenticate(
            account_session_id=old,
            password=PASSWORD,
            nonce=self.store.issue_nonce(old, "reauth")["nonce"],
        )
        new = result["account_session_id"]
        self.assertNotEqual(new, old)
        self.assertIsNone(self.store.resolve_session(old))
        self.assertIsNotNone(self.store.resolve_session(new))
        old_cookie = encode_account_session_cookie(old, self.secret)
        new_cookie = encode_account_session_cookie(new, self.secret)
        self.assertEqual(decode_account_session_cookie(old_cookie, self.secret), old)
        self.assertEqual(decode_account_session_cookie(new_cookie, self.secret), new)
        self.assertIsNone(decode_account_session_cookie(new_cookie + "0", self.secret))

    def test_password_change_revokes_other_sessions_and_changes_login_secret(self):
        boot = self._bootstrap()
        current = boot["account_session_id"]
        other_browser = "e" * 32
        other = self.store.login(
            username="Owner", password=PASSWORD, device_label="Other",
            nonce_session_id=other_browser,
            nonce=self.store.issue_nonce(other_browser, "login")["nonce"],
            remote=False,
        )["account_session_id"]
        self.store.change_password(
            session_id=current,
            new_password=SECOND_PASSWORD,
            nonce=self.store.issue_nonce(current, "change_password")["nonce"],
        )
        self.assertIsNone(self.store.resolve_session(other))
        old_browser = "1" * 32
        with self.assertRaises(AccountAuthError) as old_password:
            self.store.login(
                username="Owner", password=PASSWORD, device_label="Browser",
                nonce_session_id=old_browser,
                nonce=self.store.issue_nonce(old_browser, "login")["nonce"],
                remote=False,
            )
        self.assertEqual(old_password.exception.code, "invalid_credentials")
        new_browser = "2" * 32
        logged_in = self.store.login(
            username="Owner", password=SECOND_PASSWORD, device_label="Browser",
            nonce_session_id=new_browser,
            nonce=self.store.issue_nonce(new_browser, "login")["nonce"],
            remote=False,
        )
        self.assertIsNotNone(self.store.resolve_session(logged_in["account_session_id"]))

    def test_account_and_session_caps_fail_or_prune_deterministically(self):
        boot = self._bootstrap()
        owner = boot["account_session_id"]
        with patch("services.account_auth._MAX_ACCOUNTS", 1), patch.object(
            self.store, "_password_record", side_effect=AssertionError("scrypt reached"),
        ):
            with self.assertRaises(AccountStoreCapacityError):
                self.store.create_account(
                    actor_session_id=owner,
                    nonce=self.store.issue_nonce(owner, "create_account")["nonce"],
                    username="Another", password=PASSWORD,
                )

        sessions = [owner]
        with patch("services.account_auth._MAX_ACTIVE_SESSIONS_PER_ACCOUNT", 2):
            for index in range(3):
                browser = f"{index + 10:032x}"
                sessions.append(self.store.login(
                    username="Owner", password=PASSWORD, device_label=f"Device {index}",
                    nonce_session_id=browser,
                    nonce=self.store.issue_nonce(browser, "login")["nonce"],
                    remote=False,
                )["account_session_id"])
        active = [session for session in sessions if self.store.resolve_session(session)]
        self.assertLessEqual(len(active), 2)

    def test_input_bounds_and_session_expiry_fail_closed(self):
        with self.assertRaises(AccountAuthError) as invalid_text:
            self.store.bootstrap_owner(
                username="Owner", password="x" * 12 + "\ud800", email="",
                device_label="Browser", nonce_session_id=self.browser_session,
                nonce=self.store.issue_nonce(
                    self.browser_session, "bootstrap",
                )["nonce"], remote=False,
            )
        self.assertEqual(invalid_text.exception.code, "invalid_password")
        with self.assertRaises(AccountAuthError) as username:
            self.store.bootstrap_owner(
                username=" x ", password=PASSWORD, email="", device_label="Browser",
                nonce_session_id=self.browser_session,
                nonce=self.store.issue_nonce(self.browser_session, "bootstrap")["nonce"],
                remote=False,
            )
        self.assertEqual(username.exception.code, "invalid_username")
        boot = self._bootstrap()
        session = boot["account_session_id"]
        self.clock.advance(3601)
        self.assertIsNone(self.store.resolve_session(session))
        self.clock.advance(-4000)
        self.assertIsNone(self._new_store().resolve_session(session))

    def test_reauth_freshness_uses_durable_observed_clock_high_water(self):
        path = Path(self.temporary.name) / "reauth-clock.json"
        clock = _Clock(1000)
        store = AccountAuthStore(
            str(path), self.secret, clock=clock, password_n=1024,
            session_ttl_seconds=3600, reauth_ttl_seconds=90,
            nonce_ttl_seconds=300,
        )
        browser = "e" * 32
        boot = store.bootstrap_owner(
            username="Owner", password=PASSWORD, email="",
            device_label="Browser", nonce_session_id=browser,
            nonce=store.issue_nonce(browser, "bootstrap")["nonce"],
            remote=False,
        )
        account_session = boot["account_session_id"]
        pending = {
            purpose: store.issue_nonce(account_session, purpose)["nonce"]
            for purpose in (
                "change_password", "rotate_recovery_codes", "revoke_all_sessions",
            )
        }
        clock.value = 1091
        principal = store.resolve_session(account_session)
        self.assertFalse(principal["recently_reauthenticated"])
        operations = (
            lambda: store.change_password(
                session_id=account_session, new_password=SECOND_PASSWORD,
                nonce=pending["change_password"],
            ),
            lambda: store.rotate_recovery_codes(
                session_id=account_session,
                nonce=pending["rotate_recovery_codes"],
            ),
            lambda: store.revoke_all_sessions(
                actor_session_id=account_session,
                nonce=pending["revoke_all_sessions"], retain_current=True,
            ),
        )
        for operation in operations:
            with self.assertRaises(AccountAuthError) as stale:
                operation()
            self.assertEqual(stale.exception.code, "reauth_required")
        clock.value = 900
        restarted = AccountAuthStore(
            str(path), self.secret, clock=clock, password_n=1024,
            session_ttl_seconds=3600, reauth_ttl_seconds=90,
            nonce_ttl_seconds=60,
        )
        principal = restarted.resolve_session(account_session)
        self.assertFalse(principal["recently_reauthenticated"])
        for operation in (
            lambda: restarted.change_password(
                session_id=account_session, new_password=SECOND_PASSWORD,
                nonce=pending["change_password"],
            ),
            lambda: restarted.rotate_recovery_codes(
                session_id=account_session,
                nonce=pending["rotate_recovery_codes"],
            ),
            lambda: restarted.revoke_all_sessions(
                actor_session_id=account_session,
                nonce=pending["revoke_all_sessions"], retain_current=True,
            ),
        ):
            with self.assertRaises(AccountAuthError) as rollback:
                operation()
            self.assertEqual(rollback.exception.code, "reauth_required")
        self.assertGreaterEqual(
            restarted._load()["clock_high_water"], 1091,
        )


class AccountCapabilityTests(unittest.TestCase):
    @staticmethod
    def _launch_subset(*names, constants=()):
        path = APP / "launch.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        body = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                node.decorator_list = []
                body.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in constants
                for target in node.targets
            ):
                body.append(node)
        module = ast.Module(body=body, type_ignores=[])
        ast.fix_missing_locations(module)
        return module, path

    def test_remote_capabilities_come_from_role_not_address(self):
        anonymous_remote = resolve_account_capabilities(None, remote=True)
        anonymous_local = resolve_account_capabilities(None, remote=False)
        user_remote = resolve_account_capabilities(
            {"role": "user", "disabled": False}, remote=True,
        )
        owner_remote = resolve_account_capabilities(
            {"role": "owner", "disabled": False}, remote=True,
        )

        self.assertNotIn("owner.remote_parity", anonymous_remote)
        self.assertIn("owner.remote_parity", anonymous_local)
        self.assertNotIn("owner.remote_parity", user_remote)
        self.assertIn("owner.remote_parity", owner_remote)
        self.assertNotIn("machine.local", owner_remote)
        self.assertIn("machine.local", anonymous_local)

    def test_launch_contract_is_opt_in_rotating_and_cookie_hardened(self):
        source = (APP / "launch.py").read_text(encoding="utf-8")
        self.assertIn('MAESTRO_ACCOUNTS_ENABLED', source)
        self.assertIn('MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED', source)
        self.assertIn('request.state.maestro_account_principal', source)
        self.assertIn('request.state.maestro_account_capabilities', source)
        self.assertIn('request.state.maestro_account_session_cookie_id', source)
        self.assertIn('ACCOUNT_SESSION_COOKIE_NAME', source)
        self.assertIn('httponly=True', source)
        self.assertIn('samesite="strict"', source)
        self.assertIn('secure=_request_is_https(request)', source)
        self.assertIn('@api.post("/api/v1/account/login")', source)
        self.assertIn('@api.post("/api/v1/account/recover")', source)
        self.assertIn('@api.post("/api/v1/account/reauth")', source)
        self.assertIn('@api.get("/api/v1/account/sessions")', source)
        self.assertIn('@api.post("/api/v1/account/sessions/revoke-all")', source)
        self.assertNotIn('passkey_authentication_available": True', source)

    def test_opt_in_defaults_are_behaviorally_disabled(self):
        module, path = self._launch_subset(
            "_env_flag_enabled", "_accounts_enabled", "_account_bootstrap_enabled",
            constants=("_TRUE_ENV_VALUES",),
        )
        namespace = {"os": os}
        exec(compile(module, str(path), "exec"), namespace)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(namespace["_accounts_enabled"]())
            self.assertFalse(namespace["_account_bootstrap_enabled"]())
        with patch.dict(os.environ, {"MAESTRO_ACCOUNTS_ENABLED": "true"}, clear=True):
            self.assertTrue(namespace["_accounts_enabled"]())
            self.assertFalse(namespace["_account_bootstrap_enabled"]())
        with patch.dict(os.environ, {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED": "true",
        }, clear=True):
            self.assertTrue(namespace["_account_bootstrap_enabled"]())

    def test_exact_account_origin_rejects_rebinding_and_other_local_ports(self):
        names = (
            "_env_flag_enabled", "_server_bind_is_widened",
            "_cloudflare_origin_has_suffix", "_is_quick_tunnel_origin",
            "_is_workers_dev_origin", "_canonical_http_origin",
            "_first_forwarded_value", "_request_external_origins",
            "_configured_app_origins", "_matches_verified_stable_redirect_origin",
            "_account_exact_origin_allowed", "_account_local_bootstrap_allowed",
            "_reject_cross_origin_mutation",
        )
        module, path = self._launch_subset(
            *names, constants=("_TRUE_ENV_VALUES", "_STATE_CHANGING_METHODS"),
        )

        class _Response:
            def __init__(self, body, status_code):
                self.body = body
                self.status_code = status_code

        namespace = {
            "os": os,
            "socket": __import__("socket"),
            "ipaddress": __import__("ipaddress"),
            "urlsplit": __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit,
            "Request": object,
            "JSONResponse": _Response,
            "_runtime_share_registration": lambda: ("", "", False),
            "_request_is_cloudflare_remote": lambda _request: False,
            "_is_loopback_request_client": lambda request: request.client.host == "127.0.0.1",
        }
        exec(compile(module, str(path), "exec"), namespace)

        def request(base, origin, client="127.0.0.1", forwarded_host=""):
            headers = {"origin": origin}
            if forwarded_host:
                headers.update({"x-forwarded-proto": "https", "x-forwarded-host": forwarded_host})
            return types.SimpleNamespace(
                base_url=base,
                headers=headers,
                client=types.SimpleNamespace(host=client),
                method="POST",
                url=types.SimpleNamespace(path="/api/v1/account/nonce"),
            )

        allowed = namespace["_account_exact_origin_allowed"]
        bootstrap = namespace["_account_local_bootstrap_allowed"]
        with patch.dict(os.environ, {"SERVER_PORT": "7860"}, clear=True):
            exact = request("http://127.0.0.1:7860/", "http://127.0.0.1:7860")
            self.assertTrue(allowed(exact))
            self.assertTrue(bootstrap(exact))
            self.assertIsNone(namespace["_reject_cross_origin_mutation"](exact))
            self.assertFalse(allowed(request(
                "http://127.0.0.1:7860/", "http://localhost:5173",
            )))
            self.assertEqual(namespace["_reject_cross_origin_mutation"](request(
                "http://127.0.0.1:7860/", "http://localhost:5173",
            )).status_code, 403)
            self.assertFalse(bootstrap(request(
                "https://attacker.example/", "https://attacker.example",
            )))
            origins = namespace["_configured_app_origins"]()
            self.assertIn("http://127.0.0.1:7860", origins)
            self.assertNotIn("http://localhost:5173", origins)

    def test_manual_widened_bind_classifies_external_request_remote(self):
        module, path = self._launch_subset(
            "_env_flag_enabled", "_server_bind_is_widened",
            "_request_is_cloudflare_remote",
            constants=("_TRUE_ENV_VALUES",),
        )
        namespace = {
            "os": os,
            "ipaddress": __import__("ipaddress"),
            "Request": object,
            "_is_loopback_request_client": lambda request: request.client.host == "127.0.0.1",
            "_request_external_origins": lambda request: {request.base_url.rstrip("/")},
            "_approved_local_origin": lambda origin: origin.startswith("http://127.0.0.1"),
        }
        exec(compile(module, str(path), "exec"), namespace)
        request = types.SimpleNamespace(
            client=types.SimpleNamespace(host="192.0.2.10"),
            headers={}, base_url="http://192.0.2.20:7860/",
        )
        with patch.dict(os.environ, {
            "SERVER_NAME": "0.0.0.0",
            "PINOKIO_SHARE_LOCAL": "false",
            "PINOKIO_SHARE_CLOUDFLARE": "false",
        }, clear=True):
            self.assertFalse(namespace["_server_bind_is_widened"]())
            self.assertTrue(namespace["_request_is_cloudflare_remote"](request))
            local = types.SimpleNamespace(
                client=types.SimpleNamespace(host="127.0.0.1"),
                headers={}, base_url="http://127.0.0.1:7860/",
            )
            self.assertFalse(namespace["_request_is_cloudflare_remote"](local))
        loopback = types.SimpleNamespace(
            client=types.SimpleNamespace(host="127.0.0.1"),
            headers={}, base_url="http://192.0.2.20:7860/",
        )
        with patch.dict(os.environ, {
            "SERVER_NAME": "0.0.0.0",
            "PINOKIO_SHARE_CLOUDFLARE": "false",
        }, clear=True):
            self.assertTrue(namespace["_server_bind_is_widened"]())
            self.assertTrue(namespace["_request_is_cloudflare_remote"](loopback))

    def test_account_routes_preserve_browser_project_and_output_principal(self):
        module, path = self._launch_subset(
            "_account_request_body", "_rotate_account_session", "_clear_account_session",
            "_account_request_source",
            "bootstrap_account_owner", "login_account", "reauthenticate_account",
            "logout_account",
        )

        class _Request:
            def __init__(self):
                self.state = types.SimpleNamespace(
                    maestro_session_id="f" * 32,
                    maestro_account_session_id="",
                    maestro_remote=False,
                )
                self.body = {"nonce": "nonce", "username": "Owner", "password": PASSWORD}

            async def json(self):
                return self.body

        class _Store:
            counter = 0

            def bootstrap_owner(self, **kwargs):
                self.counter += 1
                return {"account": {"role": "owner"}, "account_session_id": f"{self.counter:032x}"}

            def login(self, **kwargs):
                self.counter += 1
                return {"account": {"role": "owner"}, "account_session_id": f"{self.counter:032x}"}

            def reauthenticate(self, **kwargs):
                self.counter += 1
                return {"account": {"role": "owner"}, "account_session_id": f"{self.counter:032x}"}

            def revoke_session(self, **kwargs):
                return {"revoked": True, "current": True}

        store = _Store()
        namespace = {
            "asyncio": asyncio,
            "Request": object,
            "AccountAuthError": AccountAuthError,
            "HTTPException": RuntimeError,
            "_account_bootstrap_enabled": lambda: True,
            "_account_local_bootstrap_allowed": lambda _request: True,
            "_require_account_store": lambda _request: store,
            "_require_account_principal": lambda request: request.state.maestro_account_principal,
            "_raise_account_http_error": lambda error: (_ for _ in ()).throw(error),
            "_attach_account_request_state": lambda request, session, remote: setattr(
                request.state, "maestro_account_principal",
                {"session_handle": "handle", "role": "owner"} if session else None,
            ),
        }
        exec(compile(module, str(path), "exec"), namespace)
        request = _Request()
        request.state.maestro_account_principal = None
        browser = request.state.maestro_session_id
        project_grant = {"session_id": browser, "unlocked": True}
        job = {"owner_session_id": browser}
        output = {"owner_session_id": browser}

        async def exercise():
            await namespace["bootstrap_account_owner"](request)
            await namespace["login_account"](request)
            await namespace["reauthenticate_account"](request)
            await namespace["logout_account"](request)
            request.body = {"nonce": "nonce", "username": "Owner", "password": PASSWORD}
            await namespace["login_account"](request)

        asyncio.run(exercise())
        self.assertEqual(request.state.maestro_session_id, browser)
        self.assertEqual(project_grant["session_id"], browser)
        self.assertEqual(job["owner_session_id"], browser)
        self.assertEqual(output["owner_session_id"], browser)
        self.assertTrue(request.state.maestro_account_session_id)

    def test_asgi_cookie_flow_rotates_only_account_authority(self):
        class _Headers(dict):
            def __init__(self, pairs=()):
                super().__init__(
                    (key.decode("latin-1").lower(), value.decode("latin-1"))
                    for key, value in pairs
                )
                self._set_cookie = []

            def getlist(self, name):
                return list(self._set_cookie) if name.lower() == "set-cookie" else []

        class _Response:
            def __init__(self, body, status_code=200):
                self.body = json.dumps(body).encode("utf-8")
                self.status_code = status_code
                self.headers = _Headers()

            def set_cookie(self, key, value, **attributes):
                cookie = http.cookies.SimpleCookie()
                cookie[key] = value
                for name, setting in attributes.items():
                    attribute = name.replace("_", "-")
                    if name in {"httponly", "secure"}:
                        if setting:
                            cookie[key][attribute] = True
                    else:
                        cookie[key][attribute] = setting
                self.headers._set_cookie.append(cookie.output(header="").strip())

            def delete_cookie(self, key, **attributes):
                self.set_cookie(key, "", max_age=0, **attributes)

        class _Request:
            def __init__(self, scope, receive):
                self.scope = scope
                self._receive = receive
                self.method = scope["method"]
                self.headers = _Headers(scope["headers"])
                self.cookies = {}
                parsed = http.cookies.SimpleCookie()
                parsed.load(self.headers.get("cookie", ""))
                self.cookies = {key: value.value for key, value in parsed.items()}
                self.url = types.SimpleNamespace(
                    path=scope["path"], scheme=scope["scheme"],
                )
                self.client = types.SimpleNamespace(host=scope["client"][0])
                self.state = types.SimpleNamespace()

            async def json(self):
                event = await self._receive()
                return json.loads(event.get("body", b"{}").decode("utf-8"))

        class _HTTPException(Exception):
            pass

        names = (
            "_set_maestro_session_cookie", "_set_maestro_account_session_cookie",
            "_clear_maestro_account_session_cookie", "_maestro_session_middleware",
            "_account_request_body", "_rotate_account_session",
            "_clear_account_session", "_account_request_source",
            "issue_account_nonce", "bootstrap_account_owner", "login_account",
            "reauthenticate_account", "recover_account", "logout_account",
            "change_account_password", "rotate_account_recovery_codes",
            "revoke_account_session", "revoke_all_account_sessions",
            "create_server_account", "update_server_account",
        )
        module, path = self._launch_subset(*names)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        secret = b"route-cookie-secret" * 4
        store = AccountAuthStore(
            str(Path(temporary.name) / "accounts.json"), secret,
            password_n=1024,
        )

        def attach(request, session_id, *, remote):
            principal = store.resolve_session(session_id) if session_id else None
            request.state.maestro_account_principal = principal
            request.state.maestro_account_error = ""
            request.state.maestro_account_capabilities = resolve_account_capabilities(
                principal, remote=remote,
            )

        def require_principal(request):
            principal = getattr(request.state, "maestro_account_principal", None)
            if not isinstance(principal, dict):
                raise AccountAuthError(
                    "Authentication is required.", code="authentication_required",
                )
            return principal

        async def call_next_with_no_store(request, call_next):
            return await call_next(request)

        namespace = {
            "asyncio": asyncio,
            "uuid": __import__("uuid"),
            "Request": _Request,
            "Response": _Response,
            "JSONResponse": _Response,
            "HTTPException": _HTTPException,
            "SESSION_COOKIE_NAME": SESSION_COOKIE_NAME,
            "ACCOUNT_SESSION_COOKIE_NAME": ACCOUNT_SESSION_COOKIE_NAME,
            "ACCOUNT_NONCE_PURPOSES": ACCOUNT_NONCE_PURPOSES,
            "AccountAuthError": AccountAuthError,
            "decode_session_cookie": decode_session_cookie,
            "encode_session_cookie": encode_session_cookie,
            "decode_account_session_cookie": decode_account_session_cookie,
            "encode_account_session_cookie": encode_account_session_cookie,
            "_session_secret": lambda: secret,
            "_request_is_https": lambda _request: False,
            "_request_is_cloudflare_remote": lambda _request: False,
            "_research_local_only_denial": lambda _request: None,
            "_local_recovery_control_denial": lambda _request: None,
            "_reject_cross_origin_mutation": lambda _request: None,
            "_remote_local_only_denial": lambda _request: None,
            "_stamp_recovery_no_store_response": lambda _request, response: response,
            "_call_next_with_recovery_no_store": call_next_with_no_store,
            "_REMOTE_OWNER_REAUTH_ALLOWED_EXACT": frozenset(),
            "_request_session_id": contextvars.ContextVar("route_session"),
            "_request_remote": contextvars.ContextVar("route_remote"),
            "_request_account_id": contextvars.ContextVar(
                "route_account", default="",
            ),
            "_attach_account_request_state": attach,
            "_require_account_store": lambda _request: store,
            "_require_account_principal": require_principal,
            "_raise_account_http_error": lambda error: (_ for _ in ()).throw(error),
            "_account_bootstrap_enabled": lambda: True,
            "_account_local_bootstrap_allowed": lambda _request: True,
        }
        exec(compile(module, str(path), "exec"), namespace)

        routes = {
            "/api/v1/account/nonce": namespace["issue_account_nonce"],
            "/api/v1/account/bootstrap": namespace["bootstrap_account_owner"],
            "/api/v1/account/login": namespace["login_account"],
            "/api/v1/account/reauth": namespace["reauthenticate_account"],
            "/api/v1/account/recover": namespace["recover_account"],
            "/api/v1/account/logout": namespace["logout_account"],
            "/api/v1/account/password": namespace["change_account_password"],
            "/api/v1/account/recovery-codes": namespace["rotate_account_recovery_codes"],
            "/api/v1/account/sessions/revoke-all": namespace["revoke_all_account_sessions"],
            "/api/v1/account/users": namespace["create_server_account"],
        }
        jar = {
            SESSION_COOKIE_NAME: "attacker-fixed-value",
            ACCOUNT_SESSION_COOKIE_NAME: "attacker-account-value",
        }

        async def request(path_name, body, *, method="POST"):
            encoded = json.dumps(body).encode("utf-8")
            cookie = "; ".join(f"{key}={value}" for key, value in jar.items())
            headers = [(b"host", b"127.0.0.1:7860")]
            if cookie:
                headers.append((b"cookie", cookie.encode("latin-1")))
            scope = {
                "type": "http", "asgi": {"version": "3.0"},
                "http_version": "1.1", "method": method,
                "scheme": "http", "path": path_name,
                "raw_path": path_name.encode("ascii"), "query_string": b"",
                "root_path": "", "headers": headers,
                "client": ("127.0.0.1", 42000),
                "server": ("127.0.0.1", 7860),
            }
            delivered = False

            async def receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.disconnect"}
                delivered = True
                return {"type": "http.request", "body": encoded, "more_body": False}

            incoming = _Request(scope, receive)

            async def dispatch(actual_request):
                if path_name == "/resource":
                    result = {
                        "browser": actual_request.state.maestro_session_id,
                        "allowed": actual_request.state.maestro_session_id
                        == resource_owner["session_id"],
                    }
                elif path_name.startswith("/api/v1/account/users/"):
                    account_id = path_name.rsplit("/", 1)[-1]
                    result = await namespace["update_server_account"](
                        account_id, actual_request,
                    )
                elif (
                    path_name.startswith("/api/v1/account/sessions/")
                    and path_name != "/api/v1/account/sessions/revoke-all"
                ):
                    session_handle = path_name.rsplit("/", 1)[-1]
                    result = await namespace["revoke_account_session"](
                        session_handle, actual_request,
                    )
                else:
                    result = await routes[path_name](actual_request)
                return _Response(result)

            response = await namespace["_maestro_session_middleware"](
                incoming, dispatch,
            )
            for raw_cookie in response.headers.getlist("set-cookie"):
                parsed = http.cookies.SimpleCookie()
                parsed.load(raw_cookie)
                for key, morsel in parsed.items():
                    if morsel["max-age"] == "0" or not morsel.value:
                        jar.pop(key, None)
                    else:
                        jar[key] = morsel.value
            return json.loads(bytes(response.body).decode("utf-8"))

        resource_owner = {"session_id": ""}

        async def exercise():
            bootstrap_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "bootstrap"},
            )
            browser_cookie = jar[SESSION_COOKIE_NAME]
            browser_id = decode_session_cookie(browser_cookie, secret)
            self.assertIsNotNone(browser_id)
            self.assertNotEqual(browser_id, "attacker-fixed-value")
            self.assertNotIn(ACCOUNT_SESSION_COOKIE_NAME, jar)
            resource_owner["session_id"] = browser_id

            boot = await request("/api/v1/account/bootstrap", {
                "username": "Owner", "password": PASSWORD,
                "nonce": bootstrap_nonce["nonce"],
            })
            recovery_code = boot["recovery_codes"][0]
            account_cookie = jar[ACCOUNT_SESSION_COOKIE_NAME]
            account_id = decode_account_session_cookie(account_cookie, secret)
            self.assertIsNotNone(store.resolve_session(account_id))

            logout_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "revoke_session"},
            )
            await request("/api/v1/account/logout", {"nonce": logout_nonce["nonce"]})
            self.assertNotIn(ACCOUNT_SESSION_COOKIE_NAME, jar)
            self.assertIsNone(store.resolve_session(account_id))

            login_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "login"},
            )
            await request("/api/v1/account/login", {
                "username": "Owner", "password": PASSWORD,
                "nonce": login_nonce["nonce"],
            })
            logged_in = decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            )

            reauth_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "reauth"},
            )
            await request("/api/v1/account/reauth", {
                "password": PASSWORD, "nonce": reauth_nonce["nonce"],
            })
            reauthenticated = decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            )
            self.assertNotEqual(reauthenticated, logged_in)
            self.assertIsNone(store.resolve_session(logged_in))

            recovery_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "recover"},
            )
            await request("/api/v1/account/recover", {
                "username": "Owner", "recovery_code": recovery_code,
                "new_password": SECOND_PASSWORD, "nonce": recovery_nonce["nonce"],
            })
            recovered = decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            )
            self.assertNotEqual(recovered, reauthenticated)
            self.assertIsNone(store.resolve_session(reauthenticated))

            stable_browser = jar[SESSION_COOKIE_NAME]
            jar[ACCOUNT_SESSION_COOKIE_NAME] += "tampered"
            await request("/resource", {})
            self.assertNotIn(ACCOUNT_SESSION_COOKIE_NAME, jar)
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)
            resource = await request("/resource", {})
            self.assertTrue(resource["allowed"])
            self.assertEqual(resource["browser"], browser_id)

            relogin_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "login"},
            )
            await request("/api/v1/account/login", {
                "username": "Owner", "password": SECOND_PASSWORD,
                "nonce": relogin_nonce["nonce"],
            })
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)
            self.assertIsNotNone(decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            ))

            created = await request_nonce_and_mutate(
                "create_account", "/api/v1/account/users", {
                    "username": "Second User",
                    "password": "another sufficiently long password",
                    "role": "user",
                },
            )
            created_account_id = created["account"]["id"]
            self.assertEqual(created["account"]["role"], "user")

            await request_nonce_and_mutate(
                "disable_account",
                f"/api/v1/account/users/{created_account_id}",
                {"disabled": True}, method="PUT",
            )
            accounts = store.list_accounts(decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            ))
            self.assertTrue(next(
                account for account in accounts
                if account["id"] == created_account_id
            )["disabled"])

            rotated = await request_nonce_and_mutate(
                "rotate_recovery_codes", "/api/v1/account/recovery-codes", {},
            )
            self.assertEqual(len(rotated["recovery_codes"]), 10)

            other_browser = "f" * 32
            extra_login_nonce = store.issue_nonce(other_browser, "login")["nonce"]
            extra_session = store.login(
                username="Owner", password=SECOND_PASSWORD,
                device_label="Other browser", nonce_session_id=other_browser,
                nonce=extra_login_nonce, remote=False,
            )["account_session_id"]
            revoked = await request_nonce_and_mutate(
                "revoke_all_sessions", "/api/v1/account/sessions/revoke-all",
                {"retain_current": True},
            )
            self.assertGreaterEqual(revoked["revoked"], 1)
            self.assertIsNone(store.resolve_session(extra_session))
            self.assertIsNotNone(store.resolve_session(
                decode_account_session_cookie(
                    jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
                ),
            ))

            current_session = decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            )
            current_handle = store.resolve_session(current_session)["session_handle"]
            revoke_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "revoke_session"},
            )
            revoked_current = await request(
                f"/api/v1/account/sessions/{current_handle}",
                {"nonce": revoke_nonce["nonce"]}, method="DELETE",
            )
            self.assertTrue(revoked_current["current"])
            self.assertNotIn(ACCOUNT_SESSION_COOKIE_NAME, jar)
            self.assertIsNone(store.resolve_session(current_session))
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)

            post_revoke_login_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "login"},
            )
            await request("/api/v1/account/login", {
                "username": "Owner", "password": SECOND_PASSWORD,
                "nonce": post_revoke_login_nonce["nonce"],
            })
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)

            third_password = "a third sufficiently long password"
            changed = await request_nonce_and_mutate(
                "change_password", "/api/v1/account/password",
                {"new_password": third_password}, method="PUT",
            )
            self.assertEqual(changed["status"], "password_changed")

            final_logout_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "revoke_session"},
            )
            await request(
                "/api/v1/account/logout",
                {"nonce": final_logout_nonce["nonce"]},
            )
            final_login_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "login"},
            )
            await request("/api/v1/account/login", {
                "username": "Owner", "password": third_password,
                "nonce": final_login_nonce["nonce"],
            })
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)
            self.assertIsNotNone(store.resolve_session(
                decode_account_session_cookie(
                    jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
                ),
            ))

            final_session = decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            )
            final_revoke_nonce = await request(
                "/api/v1/account/nonce", {"purpose": "revoke_all_sessions"},
            )
            final_revoke = await request(
                "/api/v1/account/sessions/revoke-all", {
                    "nonce": final_revoke_nonce["nonce"],
                    "retain_current": False,
                },
            )
            self.assertTrue(final_revoke["current_revoked"])
            self.assertNotIn(ACCOUNT_SESSION_COOKIE_NAME, jar)
            self.assertIsNone(store.resolve_session(final_session))
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)

        async def request_nonce_and_mutate(purpose, path_name, body, *, method="POST"):
            stable_browser = jar[SESSION_COOKIE_NAME]
            nonce_result = await request(
                "/api/v1/account/nonce", {"purpose": purpose},
            )
            result = await request(
                path_name, {**body, "nonce": nonce_result["nonce"]}, method=method,
            )
            self.assertEqual(jar[SESSION_COOKIE_NAME], stable_browser)
            self.assertIsNotNone(decode_account_session_cookie(
                jar[ACCOUNT_SESSION_COOKIE_NAME], secret,
            ))
            return result

        asyncio.run(exercise())

    def test_remote_owner_parity_requires_role_and_fresh_reauth(self):
        module, path = self._launch_subset(
            "_request_has_account_capability",
            "_request_has_recent_account_reauth",
            "_remote_local_only_denial",
            constants=(
                "_REMOTE_LOCAL_ONLY_PREFIXES",
                "_REMOTE_LOCAL_ONLY_EXACT",
                "_REMOTE_OWNER_REAUTH_ALLOWED_EXACT",
            ),
        )

        class _Response:
            def __init__(self, body, status_code):
                self.body = body
                self.status_code = status_code

        namespace = {
            "Request": object,
            "JSONResponse": _Response,
            "time": __import__("time"),
            "_request_is_cloudflare_remote": lambda _request: True,
        }
        exec(compile(module, str(path), "exec"), namespace)

        def request(capabilities, reauthenticated, method="POST", path="/api/v1/llm/load"):
            return types.SimpleNamespace(
                method=method,
                url=types.SimpleNamespace(path=path),
                state=types.SimpleNamespace(
                    maestro_account_capabilities=frozenset(capabilities),
                    maestro_account_principal={
                        "recently_reauthenticated": reauthenticated,
                    },
                ),
            )

        deny = namespace["_remote_local_only_denial"]
        for method, path in namespace["_REMOTE_OWNER_REAUTH_ALLOWED_EXACT"]:
            self.assertIsNone(deny(request(
                {"owner.remote_parity"}, True, method, path,
            )))
            self.assertEqual(deny(request(
                {"owner.remote_parity"}, False, method, path,
            )).status_code, 403)
        self.assertEqual(
            deny(request({"owner.remote_parity"}, False)).status_code, 403,
        )
        self.assertEqual(deny(request(set(), True)).status_code, 403)

    def test_access_context_truthfully_bounds_remote_owner_controls(self):
        module, path = self._launch_subset(
            "get_access_context",
            constants=("_REMOTE_OWNER_REAUTH_ALLOWED_EXACT",),
        )
        namespace = {
            "Request": object,
            "_public_account_context": lambda _request: {"authenticated": True},
            "_request_has_account_capability": lambda _request, capability: (
                capability == "owner.remote_parity"
            ),
            "_request_has_recent_account_reauth": lambda _request: True,
            "_env_flag_enabled": lambda _name: True,
            "_public_share_url": lambda: "https://hidden.example",
        }
        exec(compile(module, str(path), "exec"), namespace)
        request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=True))
        context = namespace["get_access_context"](request)
        self.assertFalse(context["machine_controls"])
        controls = context["remote_owner_controls"]
        self.assertTrue(controls["enabled"])
        self.assertEqual(
            {(item["method"], item["path"]) for item in controls["available_routes"]},
            namespace["_REMOTE_OWNER_REAUTH_ALLOWED_EXACT"],
        )
        self.assertTrue(all(item["reason"] for item in controls["unavailable"]))

    def test_llm_control_repeats_capability_gate_at_endpoint_boundary(self):
        module, path = self._launch_subset("_require_local_llm_control")

        class _HTTPException(Exception):
            def __init__(self, *, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        namespace = {
            "Request": object,
            "HTTPException": _HTTPException,
            "_promote_external_llm_request": lambda _request: None,
            "_request_has_account_capability": lambda request, capability: (
                capability in request.state.maestro_account_capabilities
            ),
            "_request_has_recent_account_reauth": lambda request: (
                request.state.reauthenticated
            ),
        }
        exec(compile(module, str(path), "exec"), namespace)
        require = namespace["_require_local_llm_control"]
        owner = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=True,
            maestro_account_capabilities={"owner.remote_parity"},
            reauthenticated=True,
        ))
        require(owner)
        owner.state.reauthenticated = False
        with self.assertRaises(_HTTPException):
            require(owner)

    def test_cookie_writer_sets_exact_security_attributes(self):
        module, path = self._launch_subset(
            "_set_maestro_session_cookie", "_set_maestro_account_session_cookie",
        )
        captured = []

        class _Response:
            def set_cookie(self, *args, **kwargs):
                captured.append((args, kwargs))

        namespace = {
            "Response": object,
            "Request": object,
            "SESSION_COOKIE_NAME": "maestro_session",
            "ACCOUNT_SESSION_COOKIE_NAME": ACCOUNT_SESSION_COOKIE_NAME,
            "encode_session_cookie": lambda session, _secret: f"signed:{session}",
            "encode_account_session_cookie": lambda session, _secret: f"account:{session}",
            "_session_secret": lambda: b"secret",
            "_request_is_https": lambda _request: True,
        }
        exec(compile(module, str(path), "exec"), namespace)
        namespace["_set_maestro_session_cookie"](
            _Response(), types.SimpleNamespace(), "a" * 32,
        )
        namespace["_set_maestro_account_session_cookie"](
            _Response(), types.SimpleNamespace(), "b" * 32,
        )
        self.assertEqual(captured[0][0], ("maestro_session", "signed:" + "a" * 32))
        self.assertEqual(captured[1][0], (
            ACCOUNT_SESSION_COOKIE_NAME, "account:" + "b" * 32,
        ))
        for _, kwargs in captured:
            self.assertEqual(kwargs["httponly"], True)
            self.assertEqual(kwargs["samesite"], "strict")
            self.assertEqual(kwargs["secure"], True)
            self.assertEqual(kwargs["path"], "/")


if __name__ == "__main__":
    unittest.main()
