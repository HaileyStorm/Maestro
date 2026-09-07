"""Selected-interpreter dispatch for the standalone kernel installer."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def load_setup(app_root, windows=False):
    tree = ast.parse((ROOT / 'app/setup.py').read_text())
    names = {'run_python_script', 'install_logic', 'resolve_cmd', 'get_os_key'}
    nodes = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name in names
    ) or (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'ENV_TEMPLATES' for target in node.targets)
    )]
    namespace = dict(Path=Path, os=os, sys=sys, subprocess=Mock(), IS_WIN=windows,
                     __file__=str(app_root / 'setup.py'), run_cmd=Mock())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'setup-functions', 'exec'), namespace)
    return namespace


class LightXSetupTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.artifacts-temp'
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name)
        (self.app / 'scripts').mkdir()
        (self.app / 'scripts/install_lightx2v_runtime.py').write_text('')

    def test_venv_and_uv_use_selected_interpreter_without_a_shell(self):
        for windows in (False, True):
            for kind in ('venv', 'uv'):
                with self.subTest(windows=windows, kind=kind):
                    ns = load_setup(self.app, windows)
                    selected = self.app / 'environment with spaces'
                    ns['run_python_script'](kind, str(selected), 'scripts/install_lightx2v_runtime.py')
                    args, kwargs = ns['subprocess'].run.call_args
                    suffix = 'Scripts/python.exe' if windows else 'bin/python'
                    self.assertEqual(args[0][0], str(selected / suffix))
                    self.assertEqual(len(args[0]), 2)
                    self.assertEqual(kwargs, {'cwd': self.app, 'check': True})

    def test_conda_and_current_interpreter_selection(self):
        for kind in ('conda', 'none'):
            ns = load_setup(self.app)
            selected = self.app / 'selected'
            ns['run_python_script'](kind, str(selected), 'scripts/install_lightx2v_runtime.py')
            command = ns['subprocess'].run.call_args.args[0]
            expected = ['conda', 'run', '-p', str(selected), 'python'] if kind == 'conda' else [sys.executable]
            self.assertEqual(command[:-1], expected)

    def test_scripts_outside_scripts_directory_and_missing_files_are_rejected(self):
        (self.app / 'outside.py').write_text('')
        ns = load_setup(self.app)
        for script in ('outside.py', 'scripts/../outside.py', str(self.app / 'outside.py'), 'scripts/missing.py'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                ns['run_python_script']('uv', str(self.app / 'env'), script)
        ns['subprocess'].run.assert_not_called()

    def test_linked_escape_is_rejected(self):
        (self.app / 'outside.py').write_text('')
        (self.app / 'scripts/link.py').symlink_to(self.app / 'outside.py')
        ns = load_setup(self.app)
        with self.assertRaises(ValueError):
            ns['run_python_script']('uv', str(self.app / 'env'), 'scripts/link.py')
        ns['subprocess'].run.assert_not_called()

    def test_linux_uses_script_and_windows_retains_wheel_install(self):
        component = json.loads((ROOT / 'app/setup_config.json').read_text())['components']['kernels']['light2xv']
        config = {'components': {'python': {'x': {'ver': '3.11'}},
                                'torch': {'x': {'label': 'Torch', 'cmd': 'torch==2.10.0'}},
                                'kernels': {'light2xv': component}}}
        for windows in (False, True):
            with self.subTest(windows=windows):
                ns = load_setup(self.app, windows)
                ns['run_python_script'] = Mock()
                ns['install_logic']('selected', 'uv', 'selected env', 'x', 'x', None, None, None,
                                    ['light2xv'], config)
                commands = [call.args[0] for call in ns['run_cmd'].call_args_list]
                if windows:
                    ns['run_python_script'].assert_not_called()
                    self.assertTrue(any('win_amd64.whl' in command for command in commands))
                else:
                    ns['run_python_script'].assert_called_once_with('uv', 'selected env', 'scripts/install_lightx2v_runtime.py')
                    self.assertFalse(any('Light2xv/' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
