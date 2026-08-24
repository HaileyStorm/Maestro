"""Source and orchestration contracts for trusted, GPU-masked local CI."""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_CI_PATH = _ROOT / ".github" / "workflows" / "ci.yml"
_H3_PATH = _ROOT / ".github" / "workflows" / "h3-turbo-upstream.yml"
_WRAPPER_PATH = _ROOT / "scripts" / "run_local_ci.py"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("maestro_local_ci_test", _WRAPPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WorkflowContractTests(unittest.TestCase):
    def test_repository_defines_no_github_actions_workflow(self):
        workflow_root = _ROOT / ".github" / "workflows"
        workflows = sorted(workflow_root.glob("*.yml")) + sorted(
            workflow_root.glob("*.yaml")
        )
        self.assertEqual(workflows, [])
        self.assertFalse(_CI_PATH.exists())
        self.assertFalse(_H3_PATH.exists())


class LocalWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = _load_wrapper()

    def test_gate_sets_reuse_app_python_and_are_bounded(self):
        python = Path("/opt/maestro/app/env/bin/python")
        guard = self.wrapper.commands_for_gate("guard", python=python)
        backend = self.wrapper.commands_for_gate("backend", python=python)
        ui = self.wrapper.commands_for_gate("ui", python=python)
        all_commands = self.wrapper.commands_for_gate("all", python=python)
        h3 = self.wrapper.commands_for_gate("h3-upstream", python=python)

        self.assertEqual(len(guard), 2)
        self.assertEqual(len(backend), 4)
        self.assertEqual(len(ui), 2)
        self.assertEqual(all_commands, backend + ui)
        self.assertEqual(
            h3[0].argv,
            (str(python), "scripts/check_h3_turbo_upstream.py"),
        )
        self.assertTrue(all(command.argv[0] == str(python) for command in backend))
        self.assertEqual(ui[0].argv, ("npm", "test"))
        self.assertEqual(ui[1].argv, ("npm", "run", "build"))
        self.assertIn("test_*.py", backend[2].argv)

    def test_child_environment_hides_gpu_runtimes(self):
        with mock.patch.dict(
            os.environ,
            {"CUDA_VISIBLE_DEVICES": "7", "NVIDIA_VISIBLE_DEVICES": "all"},
        ):
            env = self.wrapper.cpu_only_environment()
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(env["NVIDIA_VISIBLE_DEVICES"], "void")
        self.assertEqual(env["HIP_VISIBLE_DEVICES"], "")
        self.assertEqual(env["ROCR_VISIBLE_DEVICES"], "")
        self.assertEqual(env["MAESTRO_CI_CPU_ONLY"], "1")

    def test_runner_uses_argument_arrays_repo_paths_and_fails_fast(self):
        python = Path("/opt/maestro/app/env/bin/python")
        commands = self.wrapper.commands_for_gate("backend", python=python)
        outcomes = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=9)]

        with (
            mock.patch.object(self.wrapper, "_app_python", return_value=python),
            mock.patch.object(self.wrapper, "validate_dependencies") as validate,
            mock.patch.object(
                self.wrapper.subprocess,
                "run",
                side_effect=outcomes,
            ) as run,
        ):
            result = self.wrapper.run_commands(commands)

        self.assertEqual(result, 9)
        validate.assert_called_once_with(commands, python=python)
        self.assertEqual(run.call_count, 2)
        for call, command in zip(run.call_args_list, commands):
            self.assertEqual(call.args[0], command.argv)
            self.assertEqual(call.kwargs["cwd"], command.cwd)
            self.assertNotIn("shell", call.kwargs)
            self.assertEqual(call.kwargs["env"]["CUDA_VISIBLE_DEVICES"], "")
            self.assertIn("PYTHONPYCACHEPREFIX", call.kwargs["env"])

    def test_dry_run_executes_nothing_and_skips_dependency_probe(self):
        commands = self.wrapper.commands_for_gate(
            "ui",
            python=Path("/missing/app/env/bin/python"),
        )
        output = io.StringIO()
        with (
            mock.patch.object(self.wrapper, "validate_dependencies") as validate,
            mock.patch.object(self.wrapper.subprocess, "run") as run,
            redirect_stdout(output),
        ):
            result = self.wrapper.run_commands(commands, dry_run=True)
        self.assertEqual(result, 0)
        validate.assert_not_called()
        run.assert_not_called()
        self.assertIn("DRY RUN: commands listed; no gates were executed.", output.getvalue())
        self.assertNotIn("PASS:", output.getvalue())

    def test_app_python_cannot_be_redirected_by_environment(self):
        with mock.patch.dict(
            os.environ,
            {"MAESTRO_CI_PYTHON": "/tmp/not-maestro/python"},
        ):
            selected = self.wrapper._app_python()
        self.assertIn(selected, (
            _ROOT / "app" / "env" / "bin" / "python",
            _ROOT / "app" / "env" / "Scripts" / "python.exe",
        ))

    def test_wrapper_defines_no_dependency_install_command(self):
        commands = self.wrapper.commands_for_gate(
            "all",
            python=Path("/opt/maestro/app/env/bin/python"),
        )
        for command in commands:
            self.assertNotIn("install", command.argv)
            self.assertNotEqual(command.argv, ("npm", "ci"))


if __name__ == "__main__":
    unittest.main()
