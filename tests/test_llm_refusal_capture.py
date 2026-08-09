"""Security and durability contracts for host-only refusal literal capture."""

from __future__ import annotations

import ast
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH_PATH = APP / "launch.py"
sys.path.insert(0, str(APP))

from services import llm_refusal_corpus as corpus  # noqa: E402
from services import llm_response_assist as assist  # noqa: E402


def _concurrent_corpus_add(path: str, literal: str, start, results) -> None:
    try:
        start.wait(timeout=10)
        update = corpus.RefusalCorpusStore(path).add_literal(literal)
        results.put((update.snapshot.revision, None))
    except Exception as error:  # pragma: no cover - reported to parent process
        results.put((None, type(error).__name__))


class RefusalCorpusStoreTests(unittest.TestCase):
    def _store(self, root: str) -> corpus.RefusalCorpusStore:
        return corpus.RefusalCorpusStore(
            Path(root) / "private-corpus" / "corpus.json",
        )

    def test_exact_text_is_preserved_with_private_modes_and_fixed_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            literal = "  I cannot fulfill this request.\nPlease ask another.  "
            update = store.add_literal(literal)

            self.assertTrue(update.added)
            self.assertEqual(update.snapshot.revision, 1)
            self.assertEqual(update.snapshot.literals, (literal,))
            self.assertEqual(store.snapshot(), update.snapshot)
            document = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(set(document), {"schema", "revision", "literals"})
            self.assertEqual(document, {
                "schema": corpus.CORPUS_SCHEMA,
                "revision": 1,
                "literals": [literal],
            })
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(os.stat(store.path.parent).st_mode), 0o700,
                )
                self.assertEqual(
                    stat.S_IMODE(os.stat(store.path).st_mode), 0o600,
                )
            self.assertEqual(list(store.path.parent.glob("*.tmp")), [])

    def test_casefold_duplicate_is_idempotent_and_retains_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            first = store.add_literal("I CANNOT Continue")
            duplicate = store.add_literal("i cannot continue")

            self.assertTrue(first.added)
            self.assertFalse(duplicate.added)
            self.assertEqual(duplicate.snapshot.revision, 1)
            self.assertEqual(
                duplicate.snapshot.literals, ("I CANNOT Continue",),
            )

    def test_validation_is_bounded_without_trimming_or_normalizing(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            invalid = (
                None,
                b"text",
                "",
                " \n\t ",
                "x" * 257,
                "\ud800",
                "text\x00more",
                "text\x7fmore",
                "text\x85more",
            )
            for value in invalid:
                with self.subTest(value=repr(value)), self.assertRaises(
                    corpus.RefusalCorpusValidationError,
                ):
                    store.add_literal(value)
            for index in range(corpus.MAX_LEARNED_LITERALS):
                store.add_literal(f"literal {index}")
            with self.assertRaises(corpus.RefusalCorpusValidationError):
                store.add_literal("one too many")

    def test_missing_malformed_and_symlink_reads_fail_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = self._store(temporary)
            self.assertEqual(missing.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)

            malformed_path = Path(temporary) / "malformed" / "corpus.json"
            malformed_path.parent.mkdir(mode=0o700)
            malformed_path.write_bytes(b"not json")
            if os.name != "nt":
                os.chmod(malformed_path, 0o600)
            malformed = corpus.RefusalCorpusStore(malformed_path)
            self.assertEqual(malformed.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)
            with self.assertRaises(corpus.RefusalCorpusStorageError):
                malformed.add_literal("literal")
            self.assertEqual(malformed_path.read_bytes(), b"not json")

            if hasattr(os, "symlink"):
                target = Path(temporary) / "target.json"
                target.write_text(json.dumps({
                    "schema": corpus.CORPUS_SCHEMA,
                    "revision": 1,
                    "literals": ["do not follow"],
                }), encoding="utf-8")
                link = Path(temporary) / "linked" / "corpus.json"
                link.parent.mkdir(mode=0o700)
                link.symlink_to(target)
                linked = corpus.RefusalCorpusStore(link)
                self.assertEqual(linked.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)
                with self.assertRaises(corpus.RefusalCorpusStorageError):
                    linked.add_literal("replacement")
                self.assertTrue(link.is_symlink())

    def test_duplicate_keys_boolean_schema_and_unsafe_modes_fail_open(self):
        malformed_documents = (
            b'{"schema":1,"schema":1,"revision":0,"literals":[]}',
            b'{"schema":true,"revision":0,"literals":[]}',
            b'{"schema":1,"revision":0,"literals":[],"extra":0}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, raw in enumerate(malformed_documents):
                path = Path(temporary) / f"case-{index}" / "corpus.json"
                path.parent.mkdir(mode=0o700)
                path.write_bytes(raw)
                if os.name != "nt":
                    os.chmod(path, 0o600)
                store = corpus.RefusalCorpusStore(path)
                self.assertEqual(store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)
                with self.assertRaises(corpus.RefusalCorpusStorageError):
                    store.add_literal("literal")

            if os.name != "nt":
                path = Path(temporary) / "public" / "corpus.json"
                path.parent.mkdir(mode=0o755)
                path.write_text(
                    '{"schema":1,"revision":0,"literals":[]}',
                    encoding="utf-8",
                )
                os.chmod(path, 0o644)
                store = corpus.RefusalCorpusStore(path)
                self.assertEqual(store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)
                with self.assertRaises(corpus.RefusalCorpusStorageError):
                    store.add_literal("literal")

    def test_failed_durable_write_never_publishes_new_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            first = store.add_literal("first").snapshot
            with mock.patch.object(
                store,
                "_write_locked",
                side_effect=corpus.RefusalCorpusStorageError("synthetic"),
            ), self.assertRaises(corpus.RefusalCorpusStorageError):
                store.add_literal("second")
            self.assertEqual(store.snapshot(), first)

    def test_snapshot_immediately_observes_external_breakage_and_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            first = store.add_literal("first").snapshot
            self.assertEqual(store.snapshot(), first)

            store.path.write_bytes(b"malformed")
            if os.name != "nt":
                os.chmod(store.path, 0o600)
            self.assertEqual(store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)

            repaired = {
                "schema": corpus.CORPUS_SCHEMA,
                "revision": 8,
                "literals": ["externally repaired"],
            }
            store.path.write_text(json.dumps(repaired), encoding="utf-8")
            if os.name != "nt":
                os.chmod(store.path, 0o600)
            self.assertEqual(store.snapshot(), corpus.RefusalCorpusSnapshot(
                revision=8, literals=("externally repaired",),
            ))

            store.path.unlink()
            self.assertEqual(store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)

            target = Path(temporary) / "external-target.json"
            target.write_text(json.dumps(repaired), encoding="utf-8")
            store.path.symlink_to(target)
            self.assertEqual(store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS)

    def test_raw_loader_failures_fail_open_for_reads_and_wrap_mutations(self):
        for failure in (OSError("synthetic"), RecursionError("synthetic")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                store = self._store(temporary)
                with mock.patch.object(
                    store, "_read_regular_file_locked", side_effect=failure,
                ):
                    self.assertEqual(
                        store.snapshot(), corpus.EMPTY_REFUSAL_CORPUS,
                    )
                    with self.assertRaises(corpus.RefusalCorpusStorageError):
                        store.add_literal("literal")

    def test_independent_store_instances_merge_updates_under_process_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = self._store(temporary)
            second = self._store(temporary)
            first.add_literal("first")
            update = second.add_literal("second")
            self.assertEqual(update.snapshot.revision, 2)
            self.assertEqual(update.snapshot.literals, ("first", "second"))
            self.assertEqual(first.snapshot(), update.snapshot)

    def test_process_lock_prevents_concurrent_lost_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(temporary)
            context = multiprocessing.get_context(
                "fork" if "fork" in multiprocessing.get_all_start_methods()
                else "spawn"
            )
            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=_concurrent_corpus_add,
                    args=(str(store.path), literal, start, results),
                )
                for literal in ("process one", "process two")
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=15)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=5) for _process in processes]
            self.assertEqual({revision for revision, _error in outcomes}, {1, 2})
            self.assertTrue(all(error is None for _revision, error in outcomes))
            snapshot = store.snapshot()
            self.assertEqual(snapshot.revision, 2)
            self.assertEqual(set(snapshot.literals), {"process one", "process two"})


class ResponseAssistLearnedCorpusTests(unittest.TestCase):
    def test_builder_adds_only_snapshot_literals_and_keeps_v2_identity_fixed(self):
        snapshot = corpus.RefusalCorpusSnapshot(
            revision=7,
            literals=("Exact refusal copy",),
        )
        options = assist.build_server_response_assist(
            corpus_snapshot=snapshot,
        )
        self.assertEqual(options["refusal_literals"], ["Exact refusal copy"])
        self.assertEqual(options["refusal_profile"], "high_confidence")
        self.assertTrue(options["retry_on_refusal"])
        self.assertEqual(assist.SERVER_RESPONSE_ASSIST_IDENTITY, {
            "version": "owner-approved-v2",
            "profile": "high_confidence",
        })
        self.assertNotIn("Exact refusal copy", repr(assist.SERVER_RESPONSE_ASSIST_IDENTITY))


def _capture_route_namespace(**overrides):
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    route = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "add_llm_refusal_literal"
    )
    route.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[route], type_ignores=[]))

    class FakeHTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    namespace = {
        "Request": object,
        "HTTPException": FakeHTTPException,
        "JSONResponse": __import__(
            "fastapi.responses", fromlist=["JSONResponse"],
        ).JSONResponse,
        "_require_local_llm_control": lambda _request: None,
        "_reject_cross_origin_mutation": lambda _request: None,
        **overrides,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    namespace["FakeHTTPException"] = FakeHTTPException
    return namespace


class RefusalCaptureRouteTests(unittest.TestCase):
    class Request:
        def __init__(self, body):
            self.body = body
            self.json_calls = 0

        async def json(self):
            self.json_calls += 1
            return self.body

    def test_machine_control_and_csrf_guards_run_before_body_parse(self):
        request = self.Request({"literal": "private"})
        namespace = _capture_route_namespace(
            _require_local_llm_control=lambda _request: (_ for _ in ()).throw(
                RuntimeError("remote denied"),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "remote denied"):
            asyncio.run(namespace["add_llm_refusal_literal"](request))
        self.assertEqual(request.json_calls, 0)

        request = self.Request({"literal": "private"})
        denial = object()
        namespace = _capture_route_namespace(
            _reject_cross_origin_mutation=lambda _request: denial,
        )
        self.assertIs(
            asyncio.run(namespace["add_llm_refusal_literal"](request)), denial,
        )
        self.assertEqual(request.json_calls, 0)

    def test_success_uses_only_exact_literal_and_returns_content_free_no_store(self):
        request = self.Request({
            "literal": "  Exact selected refusal  ",
            "full_response": "must be ignored",
            "prompt": "must be ignored",
        })
        update = corpus.RefusalCorpusUpdate(
            snapshot=corpus.RefusalCorpusSnapshot(
                revision=9, literals=("opaque", "opaque two"),
            ),
            added=True,
        )
        with mock.patch.object(
            corpus, "add_refusal_literal", return_value=update,
        ) as add:
            namespace = _capture_route_namespace()
            response = asyncio.run(
                namespace["add_llm_refusal_literal"](request),
            )
        add.assert_called_once_with("  Exact selected refusal  ")
        self.assertEqual(json.loads(response.body), {
            "added": True, "count": 2, "revision": 9,
        })
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertNotIn(b"opaque", response.body)
        self.assertNotIn(b"refusal", response.body)

    def test_invalid_body_and_storage_errors_are_redacted(self):
        namespace = _capture_route_namespace()
        for value in (None, [], {}, {"literal": None}):
            with self.subTest(value=value), self.assertRaises(
                namespace["FakeHTTPException"],
            ) as raised:
                asyncio.run(namespace["add_llm_refusal_literal"](
                    self.Request(value),
                ))
            self.assertEqual(raised.exception.status_code, 400)

        with mock.patch.object(
            corpus,
            "add_refusal_literal",
            side_effect=corpus.RefusalCorpusStorageError("private path"),
        ):
            with self.assertRaises(namespace["FakeHTTPException"]) as raised:
                asyncio.run(namespace["add_llm_refusal_literal"](
                    self.Request({"literal": "valid"}),
                ))
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("private path", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
