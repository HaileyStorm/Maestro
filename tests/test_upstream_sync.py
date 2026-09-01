"""Offline contracts for selective WanGP upstream integration."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import subprocess
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cherry_pick_upstream.py"
SPEC = importlib.util.spec_from_file_location("maestro_upstream_sync", SCRIPT)
assert SPEC and SPEC.loader
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


def _patch(path: str = "models/example.py") -> str:
    old_path = f'"a/{path}"' if " " in path else f"a/{path}"
    new_path = f'"b/{path}"' if " " in path else f"b/{path}"
    return (
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        "From: Upstream Author <author@example.invalid>\n"
        "Date: Fri, 8 Aug 2026 00:00:00 +0000\n"
        "Subject: [PATCH] keep provenance\n"
        "\n"
        "Commit message literal must stay unchanged: --- a/models/literal.py\n"
        "---\n"
        f" {path} | 2 +-\n"
        " 1 file changed, 1 insertion(+), 1 deletion(-)\n"
        "\n"
        f"diff --git {old_path} {new_path}\n"
        "index 1111111111111111111111111111111111111111.."
        "2222222222222222222222222222222222222222 100644\n"
        f"--- {old_path}\n"
        f"+++ {new_path}\n"
        "@@ -1 +1 @@\n"
        "-literal --- a/models/hunk.py\n"
        "+literal +++ b/models/hunk.py\n"
    )


class PathRewriteTests(unittest.TestCase):
    def test_known_app_paths_map_without_touching_message_or_hunks(self):
        rewritten = SYNC.rewrite_patch(_patch())
        self.assertIn(
            "Commit message literal must stay unchanged: --- a/models/literal.py",
            rewritten,
        )
        self.assertIn(
            "diff --git a/app/models/example.py b/app/models/example.py",
            rewritten,
        )
        self.assertIn("--- a/app/models/example.py", rewritten)
        self.assertIn("+++ b/app/models/example.py", rewritten)
        self.assertIn("-literal --- a/models/hunk.py", rewritten)
        self.assertIn("+literal +++ b/models/hunk.py", rewritten)

    def test_root_files_map_under_app_and_spaces_remain_quoted(self):
        path = "Custom Resolutions Instructions.txt"
        rewritten = SYNC.rewrite_patch(_patch(path))
        self.assertIn(
            'diff --git "a/app/Custom Resolutions Instructions.txt" '
            '"b/app/Custom Resolutions Instructions.txt"',
            rewritten,
        )
        self.assertEqual(SYNC.rewrite_path("LICENSE.txt"), "app/LICENSE.txt")
        self.assertEqual(
            SYNC.rewrite_path("requirements.txt"), "app/requirements.txt",
        )
        self.assertEqual(SYNC.rewrite_path("scripts/tool.py"), "app/scripts/tool.py")
        for protected in (
            "models/minimax_h3",
            "models/minimax_h3/transformer.py",
            "models/MINIMAX_H3/transformer.py",
            "models/_SETTINGS.json",
            "defaults/minimax_h3_ref2va.json",
            "profiles/MiniMax_H3/Turbo.json",
        ):
            with self.subTest(protected=protected):
                with self.assertRaisesRegex(SYNC.UpstreamSyncError, "protected"):
                    SYNC.rewrite_path(protected)

    def test_unknown_and_unsafe_paths_fail_closed(self):
        for path in (
            "install.js", "app/wgp.py", "unknown/file.py", "../escape.py",
            "/absolute.py", ".git/config", "models/../escape.py",
            "models/.git/config", "models/CON", "models/com1.txt",
            "models/trailing ", "models/trailing.", "models\\escape.py",
            "C:drive/file.py",
        ):
            with self.subTest(path=path):
                with self.assertRaises(SYNC.UpstreamSyncError):
                    SYNC.rewrite_path(path)
        with self.assertRaisesRegex(SYNC.UpstreamSyncError, "unmapped"):
            SYNC.rewrite_patch(_patch("install.js"))
        with self.assertRaisesRegex(SYNC.UpstreamSyncError, "no file diff"):
            SYNC.rewrite_patch("From: no-diff@example.invalid\n")

    def test_provenance_file_deletion_and_reserved_trailer_spoofing_are_rejected(self):
        deletion = (
            "diff --git a/LICENSE.txt b/LICENSE.txt\n"
            "deleted file mode 100644\n"
            "--- a/LICENSE.txt\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-license\n"
        )
        with self.assertRaisesRegex(SYNC.UpstreamSyncError, "provenance"):
            SYNC.rewrite_patch(deletion)

        extended_name_deletion = deletion.replace(
            "LICENSE.txt", "models/LICENSE-FlashVSR.txt",
        ).replace(
            "deleted file mode 100644\n---",
            "deleted file mode 100644\nGIT binary patch\nliteral 0\n---",
        )
        with self.assertRaisesRegex(SYNC.UpstreamSyncError, "provenance"):
            SYNC.rewrite_patch(extended_name_deletion)

        for trailer in ("Upstream-Commit:", "upstream-commit:", " Upstream-Commit :"):
            spoofed = _patch().replace(
                "---\n", trailer + " " + "b" * 40 + "\n---\n", 1,
            )
            rewritten = SYNC.rewrite_patch(spoofed)
            with self.subTest(trailer=trailer):
                with self.assertRaisesRegex(SYNC.UpstreamSyncError, "reserved"):
                    SYNC.add_provenance_trailers(rewritten, "a" * 40)

    def test_symlink_and_gitlink_modes_fail_closed(self):
        for unsafe_mode in ("120000", "160000"):
            patch = _patch().replace(
                "index 1111111111111111111111111111111111111111.."
                "2222222222222222222222222222222222222222 100644",
                "index 1111111111111111111111111111111111111111.."
                f"2222222222222222222222222222222222222222 {unsafe_mode}",
            )
            with self.subTest(mode=unsafe_mode):
                with self.assertRaisesRegex(SYNC.UpstreamSyncError, "symlink or gitlink"):
                    SYNC.rewrite_patch(patch)


class GitProvenanceTests(unittest.TestCase):
    def test_format_patch_requests_lossless_single_commit_output(self):
        raw_patch = b"message byte: \x80\n" + _patch().encode("utf-8")
        completed = types.SimpleNamespace(stdout=raw_patch)
        with mock.patch.object(subprocess, "run", return_value=completed) as run:
            decoded = SYNC.get_patch("a" * 40)
        self.assertEqual(SYNC._encode_patch(decoded), raw_patch)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["git", "format-patch", "-1", "--stdout"])
        self.assertIn("--full-index", command)
        self.assertIn("--binary", command)
        self.assertIn("--no-renames", command)

    def test_trailers_preserve_author_date_subject_and_bind_full_commit(self):
        rewritten = SYNC.rewrite_patch(_patch())
        full = "a" * 40
        result = SYNC.add_provenance_trailers(rewritten, full)
        self.assertIn("From: Upstream Author <author@example.invalid>", result)
        self.assertIn("Date: Fri, 8 Aug 2026 00:00:00 +0000", result)
        self.assertIn("Subject: [PATCH] keep provenance", result)
        self.assertIn(
            f"Upstream-Repository: {SYNC.UPSTREAM_REPOSITORY}\n"
            f"Upstream-Commit: {full}\n---\n",
            result,
        )

    def test_remote_must_be_official_and_commit_contained_in_remote_ref(self):
        official = types.SimpleNamespace(stdout=SYNC.UPSTREAM_REPOSITORY + "\n")
        with mock.patch.object(subprocess, "run", return_value=official):
            SYNC.verify_remote()
        malicious = types.SimpleNamespace(stdout="https://example.invalid/fork.git\n")
        with mock.patch.object(subprocess, "run", return_value=malicious):
            with self.assertRaisesRegex(SYNC.UpstreamSyncError, "official"):
                SYNC.verify_remote()

        full = "c" * 40
        responses = [
            types.SimpleNamespace(stdout=full + "\n"),
            types.SimpleNamespace(stdout="refs/remotes/upstream-wgp/main\n"),
        ]
        with mock.patch.object(subprocess, "run", side_effect=responses) as run:
            self.assertEqual(SYNC.resolve_upstream_commit("c" * 7), full)
        self.assertIn("--contains=" + full, run.call_args_list[1].args[0])

        responses = [
            types.SimpleNamespace(stdout=full + "\n"),
            types.SimpleNamespace(stdout=""),
        ]
        with mock.patch.object(subprocess, "run", side_effect=responses):
            with self.assertRaisesRegex(SYNC.UpstreamSyncError, "not contained"):
                SYNC.resolve_upstream_commit("c" * 7)
        with self.assertRaisesRegex(SYNC.UpstreamSyncError, "hexadecimal"):
            SYNC.resolve_upstream_commit("--all")


class CherryPickCliTests(unittest.TestCase):
    def test_later_unmapped_commit_is_refused_before_any_apply(self):
        commits = ["1" * 7, "2" * 7]
        full_commits = ["1" * 40, "2" * 40]
        patches = [_patch("models/first.py"), _patch("unknown/file.py")]

        for dry_run in (False, True):
            argv = [str(SCRIPT)]
            if dry_run:
                argv.append("--dry-run")
            argv.extend(commits)
            with self.subTest(dry_run=dry_run), mock.patch.object(
                SYNC, "verify_remote",
            ), mock.patch.object(
                SYNC, "resolve_upstream_commit", side_effect=full_commits,
            ), mock.patch.object(
                SYNC, "get_patch", side_effect=patches,
            ), mock.patch.object(
                SYNC, "apply_patch",
            ) as apply, mock.patch.object(
                SYNC.sys, "argv", argv,
            ), mock.patch.object(
                SYNC.sys, "stdout", io.StringIO(),
            ) as stdout:
                self.assertEqual(SYNC.main(), 1)

            apply.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")

    def test_valid_commits_apply_in_requested_order_after_preflight(self):
        commits = ["1" * 7, "2" * 7]
        full_commits = ["1" * 40, "2" * 40]
        patches = [_patch("models/first.py"), _patch("models/second.py")]

        with mock.patch.object(
            SYNC, "verify_remote",
        ), mock.patch.object(
            SYNC, "resolve_upstream_commit", side_effect=full_commits,
        ), mock.patch.object(
            SYNC, "get_patch", side_effect=patches,
        ), mock.patch.object(
            SYNC, "apply_patch",
        ) as apply, mock.patch.object(
            SYNC.sys, "argv", [str(SCRIPT), *commits],
        ):
            self.assertEqual(SYNC.main(), 0)

        self.assertEqual(apply.call_count, len(commits))
        applied = [call.args[0] for call in apply.call_args_list]
        self.assertIn("a/app/models/first.py", applied[0])
        self.assertIn("a/app/models/second.py", applied[1])
        for rewritten, full_commit in zip(applied, full_commits):
            self.assertIn(f"Upstream-Commit: {full_commit}", rewritten)

    def test_dry_run_prints_valid_commits_in_requested_order(self):
        commits = ["1" * 7, "2" * 7]
        full_commits = ["1" * 40, "2" * 40]
        patches = [_patch("models/first.py"), _patch("models/second.py")]
        stdout = io.StringIO()

        with mock.patch.object(
            SYNC, "verify_remote",
        ), mock.patch.object(
            SYNC, "resolve_upstream_commit", side_effect=full_commits,
        ), mock.patch.object(
            SYNC, "get_patch", side_effect=patches,
        ), mock.patch.object(
            SYNC, "apply_patch",
        ) as apply, mock.patch.object(
            SYNC.sys, "argv", [str(SCRIPT), "--dry-run", *commits],
        ), mock.patch.object(
            SYNC.sys, "stdout", stdout,
        ):
            self.assertEqual(SYNC.main(), 0)

        apply.assert_not_called()
        output = stdout.getvalue()
        self.assertLess(
            output.index("a/app/models/first.py"),
            output.index("a/app/models/second.py"),
        )
        self.assertLess(
            output.index(f"Upstream-Commit: {full_commits[0]}"),
            output.index(f"Upstream-Commit: {full_commits[1]}"),
        )


class SelectiveIntegrationLedgerTests(unittest.TestCase):
    def test_flashvsr_public_entry_is_inference_only(self):
        source = (ROOT / "app/postprocessing/flashvsr/runtime.py").read_text(
            encoding="utf-8",
        )
        tree = ast.parse(source)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upscale_video"
        )
        decorators = [ast.unparse(node) for node in function.decorator_list]
        self.assertIn("torch.inference_mode()", decorators)

    def test_dependency_license_and_provenance_pins_are_exact(self):
        requirements = (ROOT / "app/requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(
            [line for line in requirements.splitlines() if line.startswith("mmgp ")],
            [
                "mmgp @ https://files.pythonhosted.org/packages/d1/da/"
                "df5d4be821577120eb4370dbbce9bbdd87e1fb4aa65e37c8dba0916ae1ea/"
                "mmgp-3.7.12-py3-none-any.whl#sha256="
                "2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf"
            ],
        )
        license_bytes = (ROOT / "app/LICENSE.txt").read_bytes()
        self.assertEqual(
            hashlib.sha256(license_bytes).hexdigest(),
            "67c8e68389c945423c560c13936f0a960e5d2ffdcc5bb2ded4122fe1b095960f",
        )
        self.assertTrue(license_bytes.startswith(b"WanGP Community License 2.0\n"))
        root_notice = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("WanGP Community License 2.0", root_notice)
        self.assertNotIn("Non-Commercial Evaluation License 1.1", root_notice)

        ledger = (ROOT / "app/models/minimax_h3/UPSTREAM.md").read_text(
            encoding="utf-8",
        )
        for immutable in (
            "5c8b4ac3c5e15135b6510d9b6d4d57002e4bb5e4",
            "ecf8cf24f7eb9eabc5866a1dc4244c105cc9b3ca",
            "fc2feee1141f04a4a3be286ca1b3a768e21e79fb",
            "c49d021d43838f2fc41b14b0b2310796bc2232f5792271cb4df9f53ab22124e6",
            "2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf",
            "67c8e68389c945423c560c13936f0a960e5d2ffdcc5bb2ded4122fe1b095960f",
        ):
            self.assertIn(immutable, ledger)
        self.assertIn("not a wholesale merge", ledger)


if __name__ == "__main__":
    unittest.main()
