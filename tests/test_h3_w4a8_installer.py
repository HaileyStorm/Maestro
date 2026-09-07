"""CPU-only pinned W4A8 installation, rollback, and API admission tests."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def installer():
    return runpy.run_path(str(ROOT/'app/scripts/install_h3_w4a8_runtime.py'))


class W4A8InstallerTests(unittest.TestCase):
    def test_uv_targets_interpreter_and_rolls_back_failed_validation(self):
        module = installer(); operation = module['install_and_validate']; namespace = operation.__globals__
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); site = root/'site'; site.mkdir()
            package = site/'comfy_kitchen'; package.mkdir(); (package/'__init__.py').write_text('old package')
            metadata = site/'comfy_kitchen-0.2.26.dist-info'; metadata.mkdir(); (metadata/'METADATA').write_text('old metadata')
            unrelated = site/'unrelated'; unrelated.write_text('preserve')
            marker = root/'marker.json'; marker.write_text('old marker')
            calls = []

            def run(*args, **kwargs):
                calls.append(args)
                if len(calls) == 1:
                    self.assertFalse(marker.exists())
                    shutil.rmtree(package); shutil.rmtree(metadata)
                    package.mkdir(); (package/'__init__.py').write_text('new package')
                    (site/'comfy_kitchen-0.2.25.dist-info').mkdir()
                else:
                    marker.write_text('partial result')
                    raise RuntimeError('synthetic numerical validation failed')

            with mock.patch.dict(namespace, run=run):
                with self.assertRaisesRegex(RuntimeError, 'numerical validation'):
                    operation(root/'candidate.whl', uv='/bundled/uv', site=site, marker=marker,
                              backup=root/'backup', validator=root/'validator.py', cwd=root)
            self.assertEqual(calls[0][:5], ('/bundled/uv', 'pip', 'install', '--python', sys.executable))
            for flag in ['--no-deps', '--no-index', '--force-reinstall']:
                self.assertIn(flag, calls[0])
            self.assertNotIn('-m', calls[0])
            self.assertEqual((package/'__init__.py').read_text(), 'old package')
            self.assertEqual((metadata/'METADATA').read_text(), 'old metadata')
            self.assertFalse((site/'comfy_kitchen-0.2.25.dist-info').exists())
            self.assertEqual(marker.read_text(), 'old marker')
            self.assertEqual(unrelated.read_text(), 'preserve')

    def test_success_requires_package_bound_marker(self):
        module = installer(); operation = module['install_and_validate']; namespace = operation.__globals__
        for matches in [True, False]:
            with self.subTest(matches=matches), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); site = root/'site'; site.mkdir(); marker = root/'marker.json'
                calls = []
                def run(*args, **kwargs):
                    calls.append(args)
                    if len(calls) == 1:
                        (site/'comfy_kitchen').mkdir()
                    else:
                        marker.write_text(json.dumps({'schema_version': 2}))
                with mock.patch.dict(namespace, run=run, marker_package_matches=lambda *_: matches):
                    if matches:
                        operation(root/'candidate.whl', uv='uv', site=site, marker=marker,
                                  backup=root/'backup', validator=root/'validator.py', cwd=root)
                        self.assertTrue(marker.exists())
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'does not match'):
                            operation(root/'candidate.whl', uv='uv', site=site, marker=marker,
                                      backup=root/'backup', validator=root/'validator.py', cwd=root)
                        self.assertFalse(marker.exists())
                        self.assertFalse((site/'comfy_kitchen').exists())

    def test_archive_uses_committed_bytes_not_dirty_or_untracked_inputs(self):
        operation = installer()['extract_pinned_source']; namespace = operation.__globals__
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'repo'; source.mkdir(); destination = root/'build'; destination.mkdir()
            subprocess.run(['git','init','-q',str(source)], check=True)
            (source/'setup.py').write_text('committed bytes')
            subprocess.run(['git','add','setup.py'], cwd=source, check=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid',
                            'commit','-qm','fixture'], cwd=source, check=True)
            revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=source, text=True).strip()
            (source/'setup.py').write_text('dirty bytes')
            (source/'untracked.py').write_text('untracked bytes')
            with mock.patch.dict(namespace, REVISION=revision):
                operation(source, destination)
            self.assertEqual((destination/'setup.py').read_text(), 'committed bytes')
            self.assertFalse((destination/'untracked.py').exists())
            self.assertEqual((source/'setup.py').read_text(), 'dirty bytes')

    def test_missing_uv_or_system_python_fails_before_build(self):
        main = installer()['main']; namespace = main.__globals__
        with mock.patch.object(namespace['sys'], 'prefix', '/selected-venv'), \
             mock.patch.object(namespace['shutil'], 'which', return_value=None), \
             mock.patch.dict(namespace, extract_pinned_source=mock.Mock()) as _unused:
            with self.assertRaisesRegex(RuntimeError, 'uv command'):
                main()
            namespace['extract_pinned_source'].assert_not_called()
        with mock.patch.object(namespace['sys'], 'prefix', sys.base_prefix):
            with self.assertRaisesRegex(RuntimeError, 'virtual environment'):
                main()

    def test_wrong_source_revision_fails_before_archive(self):
        operation = installer()['extract_pinned_source']; namespace = operation.__globals__
        with mock.patch.object(namespace['subprocess'], 'check_output', return_value='wrong-revision') as command:
            with self.assertRaisesRegex(RuntimeError, 'revision'):
                operation(Path('source'), Path('destination'))
        self.assertEqual(command.call_count, 1)

    def test_validator_invalidates_marker_and_rejects_api_before_allocating(self):
        main = runpy.run_path(str(ROOT/'app/scripts/validate_h3_w4a8.py'))['main']
        for missing in ['quantize_w4a8_int8_weight', 'w4a8_int8_linear']:
            kitchen = types.SimpleNamespace(**{name: lambda: None for name in
                ['quantize_w4a8_int8_weight', 'w4a8_int8_linear'] if name != missing})
            torch = types.SimpleNamespace(cuda=types.SimpleNamespace(
                is_available=lambda: True, get_device_capability=lambda _: (12, 0)),
                manual_seed=mock.Mock(), randn=mock.Mock())
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                marker = Path(temporary)/'marker'; marker.write_text('old success')
                with mock.patch.dict(main.__globals__, MARKER=marker,
                        locate_pinned_package=lambda: (Path(temporary), "fixture-digest")), \
                     mock.patch.dict(sys.modules, comfy_kitchen=kitchen, torch=torch, triton=types.SimpleNamespace()):
                    with self.assertRaisesRegex(RuntimeError, 'runtime is incomplete'):
                        main()
                self.assertFalse(marker.exists())
            torch.manual_seed.assert_not_called(); torch.randn.assert_not_called()

    def test_catalog_rejects_partial_api_before_backend_checks(self):
        source = (ROOT/'app/services/h3_acceleration.py').read_text()
        node = next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_w4a8_capability')
        namespace = {"Path": Path}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'w4a8-capability', 'exec'), namespace)
        kitchen = types.SimpleNamespace(w4a8_int8_linear=lambda: None, list_backends=mock.Mock(),
                                        __file__=str(Path.cwd()/"__init__.py"))
        with mock.patch.dict(sys.modules, comfy_kitchen=kitchen), \
             mock.patch("services.h3_w4a8_provenance.locate_pinned_package", return_value=(Path.cwd(), "fixture")):
            available, _reason = namespace['_w4a8_capability']()
        self.assertFalse(available); kitchen.list_backends.assert_not_called()


class W4A8CapabilityProvenanceTests(unittest.TestCase):
    def load(self, name, namespace):
        source = (ROOT/'app/services/h3_acceleration.py').read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'capability-provenance', 'exec'), namespace)
        return namespace[name]

    def test_sage_validation_is_independent_of_w4a8(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); checkout = root/'sage'; checkout.mkdir()
            marker = {'revision':'revision','version':'2.2.0','torch':'2.10.0',
                      'torch_cuda':'13.0','compute_capability':[12,0],'distribution_sha256':'digest'}
            (root/'.maestro_h3_sage2.json').write_text(json.dumps(marker))
            namespace = {'Path':Path,'json':json,'sys':types.SimpleNamespace(prefix=temporary),
                'platform':types.SimpleNamespace(system=lambda:'Linux'),
                'torch':types.SimpleNamespace(__version__='2.10.0',version=types.SimpleNamespace(cuda='13.0'),
                    cuda=types.SimpleNamespace(is_available=lambda:True,get_device_capability=lambda:(12,0))),
                '_checkout_revision':lambda *_:'revision','_checkout_source_clean':lambda *_:True,
                '_cuda_version_tuple':lambda *_:(13,0),'SAGEATTENTION_CHECKOUT':checkout,
                'SAGEATTENTION_REVISION':'revision','SAGEATTENTION_VERSION':'2.2.0',
                '_sage2_distribution_provenance':lambda:('2.2.0',checkout.resolve(),'digest')}
            operation = self.load('_sage2_capability', namespace)
            with mock.patch('services.h3_w4a8_provenance.marker_package_matches', side_effect=AssertionError('unrelated W4A8 guard')):
                self.assertTrue(operation()[0])

    def test_valid_marker_cannot_authorize_replaced_package(self):
        from services import h3_w4a8_provenance as provenance
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root/'comfy_kitchen'; package.mkdir()
            source = package/'__init__.py'
            source.write_text("def quantize_w4a8_int8_weight(): pass\ndef w4a8_int8_linear(): pass\ndef list_backends(): return {'triton': {'available': True}}\n")
            digest = provenance.package_fingerprint(package)
            marker = {'schema_version':2,'runtime_revision':provenance.RUNTIME_REVISION,
                      'package_digest':digest,'gpu':'fixture','compute_capability':[12,0],
                      'torch':'2.10.0','triton':'3.6.0'}
            (root/'.maestro_h3_w4a8_validated.json').write_text(json.dumps(marker))
            namespace = {'Path':Path,'json':json,'sys':types.SimpleNamespace(prefix=temporary),
                'COMFY_KITCHEN_W4A8_REVISION':provenance.RUNTIME_REVISION,
                'torch':types.SimpleNamespace(__version__='2.10.0',cuda=types.SimpleNamespace(
                    get_device_name=lambda *_:'fixture',get_device_capability=lambda *_:(12,0)))}
            operation = self.load('_w4a8_capability', namespace)
            with mock.patch.object(provenance,'EXPECTED_PACKAGE_DIGEST',digest), \
                 mock.patch.object(sys,'path',[temporary,*sys.path]), \
                 mock.patch.dict(sys.modules,triton=types.SimpleNamespace(__version__='3.6.0')):
                sys.modules.pop('comfy_kitchen',None)
                self.assertTrue(operation()[0])
                source.write_text(source.read_text()+'# replaced package\n')
                self.assertFalse(operation()[0])

    def test_mismatched_package_is_not_imported_by_validator_or_catalog(self):
        main = runpy.run_path(str(ROOT/'app/scripts/validate_h3_w4a8.py'))['main']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root/'comfy_kitchen'; package.mkdir(); sentinel=root/'executed'
            (package/'__init__.py').write_text('from pathlib import Path\nPath('+repr(str(sentinel))+').touch()\n')
            namespace = {'Path':Path}
            operation = self.load('_w4a8_capability',namespace)
            with mock.patch.object(sys,'path',[temporary,*sys.path]), mock.patch.dict(sys.modules):
                sys.modules.pop('comfy_kitchen',None)
                self.assertFalse(operation()[0])
                with mock.patch.dict(main.__globals__,MARKER=root/'marker'):
                    with self.assertRaises(ValueError):
                        main()
            self.assertFalse(sentinel.exists())


if __name__ == '__main__':
    unittest.main()
