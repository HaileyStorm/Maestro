"""Execute the CPU flip route/worker contracts without importing model runtime."""
from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
import contextvars
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from fastapi import HTTPException
from services.output_access import stamp_sidecar_policy

TREE = ast.parse((ROOT / 'app/launch.py').read_text())


def load_functions(namespace, *names):
    nodes = []
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'launch.py', 'exec'), namespace)


class FlipRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.source = Path(self.root) / 'clip.mp4'
        self.source.write_bytes(b'original-video')
        self.sidecar = self.source.with_suffix('.meta.json')
        self.sidecar.write_text(json.dumps({'workspace': 'project-a', 'private': True,
            'explicit': True, 'params': {'prompt': 'adult dramatic fiction', 'model_type': 'original', 'multi_clip_info': {'group_id': 'source-group', 'index': 0}}}))
        self.registered = []
        self.permission = Mock(return_value=self.root)
        self.ns = {
            'os': os, 'json': json, 'time': time, 'uuid': uuid,
            'Request': object, 'HTTPException': HTTPException,
            '_request_remote': contextvars.ContextVar('remote', default=True),
            '_require_project_access': self.permission,
            '_get_active_workspace': lambda: self.fail('global workspace fallback'),
            '_reserve_workspace_operations': lambda *args: nullcontext(),
            '_output_lineage_mutation_guard': lambda *args: nullcontext(),
            '_existing_workspace_dir': lambda ws: self.root,
            'load_media_sidecars': lambda directory, names: {
                n: json.loads((Path(directory) / n).with_suffix('.meta.json').read_text())
                for n in names if (Path(directory) / n).with_suffix('.meta.json').exists()},
            '_new_generation_job_id': lambda: 'a' * 32,
            '_queue_recovery_register_and_publish': lambda job, **kw: self.registered.append((job, kw)),
            '_run_tool_hflip': object(),
        }
        load_functions(self.ns, '_request_project_workspace', '_require_authorized_output',
                       '_output_revision', '_hflip_source', 'tools_hflip')

    def request(self, **changes):
        body = {'workspace': 'project-a', 'name': 'clip.mp4',
                'revision': self.ns['_output_revision'](str(self.source), self.root, 'clip.mp4')}
        body.update(changes)
        async def read(): return body
        return types.SimpleNamespace(json=read, state=types.SimpleNamespace(
            maestro_remote=True, maestro_session_id='owner-session'))

    def test_valid_selection_keeps_scope_and_policy_and_registers_cpu_worker(self):
        response = asyncio.run(self.ns['tools_hflip'](self.request()))
        self.assertEqual(response['job_id'], 'a' * 32)
        job, options = self.registered[0]
        self.permission.assert_any_call(unittest.mock.ANY, 'project-a', permission='project.generate')
        self.assertEqual(job['params']['hflip_source_path'], str(self.source))
        self.assertTrue(job['params']['private_output'])
        self.assertTrue(job['params']['explicit_output'])
        self.assertNotIn('model_type', job['params'])
        self.assertEqual(options['recovery_kind'], 'tool_hflip')
        self.assertIs(options['worker'], self.ns['_run_tool_hflip'])

    def test_missing_scope_traversal_wrong_type_and_stale_revision_do_not_queue(self):
        for changes, status in [({'workspace': ''}, 400), ({'name': '../clip.mp4'}, 400),
                                ({'name': 'clip.png'}, 400), ({'revision': 'old'}, 409)]:
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as raised:
                asyncio.run(self.ns['tools_hflip'](self.request(**changes)))
            self.assertEqual(raised.exception.status_code, status)
        self.assertEqual(self.registered, [])

    def test_project_permission_failure_never_publishes(self):
        self.permission.side_effect = HTTPException(403, 'Project access denied')
        with self.assertRaises(HTTPException):
            asyncio.run(self.ns['tools_hflip'](self.request()))
        self.assertEqual(self.registered, [])

    def test_worker_revalidates_source_and_its_sidecar(self):
        asyncio.run(self.ns['tools_hflip'](self.request()))
        job = self.registered[0][0]
        self.assertEqual(self.ns['_hflip_source'](job)[0], str(self.source))
        self.sidecar.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.ns['_hflip_source'](job)

    def worker_namespace(self):
        asyncio.run(self.ns['tools_hflip'](self.request()))
        job = self.registered[0][0]
        job['access_policy'] = {'private': True, 'explicit': True}
        def start(j, **kw): j['status'] = 'running'; return True
        def finish(j, status, **kw): j.update(status=status, **kw); return True
        def record(j, names): j['output_files'] = names; return names
        self.ns.update({
            '_jobs': {job['id']: job}, '_gen_lock': threading.Lock(),
            '_active_gen_states': {}, 'generation_slot': lambda *args: nullcontext(True),
            'try_start': start, 'finish_job': finish, 'record_job_outputs': record,
            'register_abort_state': lambda *args: True, 'unregister_abort_state': Mock(),
            'is_cancel_requested': lambda j: j.get('status') == 'cancelled',
            'stamp_sidecar_policy': stamp_sidecar_policy,
        })
        load_functions(self.ns, '_run_tool_hflip', '_write_tool_sidecar')
        return job

    def test_worker_publishes_new_video_with_settings_and_provenance(self):
        job = self.worker_namespace()
        def encode(src, dst, **kw):
            self.assertEqual(src, str(self.source))
            self.assertTrue(callable(kw['abort_check']))
            Path(dst).write_bytes(b'flipped-video')
        with patch('services.video_transform.horizontal_flip', side_effect=encode):
            self.assertTrue(self.ns['_run_tool_hflip'](job['id']))
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(self.source.read_bytes(), b'original-video')
        output = Path(self.root) / job['output_files'][0]
        self.assertEqual(output.suffix, '.mp4')
        metadata = json.loads(output.with_suffix('.meta.json').read_text())
        self.assertEqual(metadata['transform'], {'kind': 'horizontal_flip', 'audio': 'copied', 'video_only': True})
        self.assertEqual(metadata['tool_source_workspace'], 'project-a')
        self.assertEqual(metadata['tool_source_revision'], job['params']['hflip_source_revision'])
        self.assertEqual(metadata['params']['model_type'], 'original')
        self.assertEqual(metadata['params']['prompt'], 'adult dramatic fiction')
        self.assertNotIn('edit_sub_mode', metadata['params'])
        self.assertNotIn('artifact_lineage', metadata)
        self.assertNotIn('multi_clip_info', metadata['params'])
        self.assertEqual(metadata['artifact_class'], 'final')
        from services.search_index import classify_gallery_artifacts
        self.assertEqual(classify_gallery_artifacts([{'name': output.name, 'meta': metadata}])[output.name], 'final')
        self.assertTrue(metadata['private'])
        self.assertTrue(metadata['explicit'])
        self.assertEqual(list(Path(self.root).glob('.hflip-*')), [])

    def test_cancel_or_source_change_during_encode_never_publishes(self):
        for mode in ('cancel', 'changed', 'failure'):
            with self.subTest(mode=mode):
                job = self.worker_namespace()
                def encode(src, dst, **kw):
                    Path(dst).write_bytes(b'partial')
                    if mode == 'cancel': job['status'] = 'cancelled'
                    elif mode == 'changed': self.source.write_bytes(b'changed-content')
                    else: raise RuntimeError('/private/operator/path')
                with patch('services.video_transform.horizontal_flip', side_effect=encode):
                    self.assertFalse(self.ns['_run_tool_hflip'](job['id']))
                self.assertEqual(job['status'], 'cancelled' if mode == 'cancel' else 'failed')
                self.assertEqual(job['output_files'], [])
                self.assertEqual(list(Path(self.root).glob('*_hflip_*')), [])
                self.assertEqual(list(Path(self.root).glob('.hflip-*')), [])
                self.assertNotIn('/private/', job.get('error') or '')
                self.registered.clear()

    def test_late_cancel_or_failed_durable_finish_rolls_back_publication(self):
        from services.job_lifecycle import finish_job as lifecycle_finish
        for mode in ('cancel', 'persistence'):
            with self.subTest(mode=mode):
                job = self.worker_namespace()
                def finish(j, status, **updates):
                    if status == 'completed':
                        if mode == 'persistence':
                            raise RuntimeError('journal unavailable')
                        j['cancel_requested'] = True
                    return lifecycle_finish(j, status, **updates)
                self.ns['finish_job'] = finish
                self.ns['is_cancel_requested'] = lambda j: bool(j.get('cancel_requested'))
                def encode(src, dst, **kw): Path(dst).write_bytes(b'flipped')
                with patch('services.video_transform.horizontal_flip', side_effect=encode):
                    self.assertFalse(self.ns['_run_tool_hflip'](job['id']))
                self.assertEqual(job['output_files'], [])
                self.assertEqual(list(Path(self.root).glob('*_hflip_*')), [])
                self.registered.clear()

    def test_concurrent_creator_is_not_overwritten_or_cleaned_up(self):
        for target in ('media', 'metadata'):
            with self.subTest(target=target):
                job = self.worker_namespace()
                original_link = os.link
                from services.atomic_file_publish import publish_file_no_replace
                def racing_link(src, dst, *args, **kw):
                    if ('_hflip_' in str(dst) and not str(Path(dst).parent).endswith('.hflip')
                        and Path(dst).parent == Path(self.root)
                        and (str(dst).endswith('.meta.json') == (target == 'metadata'))):
                        Path(dst).write_bytes(b'foreign-winner')
                    return original_link(src, dst, *args, **kw)
                def racing_publish(src, dst):
                    if target == 'metadata':
                        Path(dst).write_bytes(b'foreign-winner')
                    return publish_file_no_replace(src, dst)
                def encode(src, dst, **kw): Path(dst).write_bytes(b'flipped')
                with patch('services.video_transform.horizontal_flip', side_effect=encode), patch('os.link', side_effect=racing_link), patch('services.atomic_file_publish.publish_file_no_replace', side_effect=racing_publish):
                    self.assertFalse(self.ns['_run_tool_hflip'](job['id']))
                winners = list(Path(self.root).glob('*_hflip_*'))
                self.assertEqual(len(winners), 1)
                self.assertEqual(winners[0].read_bytes(), b'foreign-winner')
                winners[0].unlink()
                self.registered.clear()

    def test_source_descriptor_seals_managed_media_and_blocks_legacy_restart(self):
        from services.queue_recovery_runtime import sha256_file, QueueRecoveryRuntimeError
        asyncio.run(self.ns['tools_hflip'](self.request()))
        job = self.registered[0][0]
        self.ns.update({'_app_dir': self.root,
                        '_RECOVERABLE_INPUT_KEYS': {'hflip_source_path'},
                        '_recovery_sha256_file': sha256_file,
                        'QueueRecoveryRuntimeError': QueueRecoveryRuntimeError})
        load_functions(self.ns, '_queue_recovery_file_values', '_queue_recovery_input_descriptors',
                       '_queue_recovery_manifest_validator')
        descriptors = self.ns['_queue_recovery_input_descriptors'](job, 'owner')
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0]['field'], 'hflip_source_path:0')
        self.assertEqual(descriptors[0]['scope'], 'project')
        self.assertIn('sidecar_sha256', descriptors[0])
        self.sidecar.unlink()
        legacy = self.ns['_queue_recovery_input_descriptors'](job, 'owner')[0]
        self.assertEqual(legacy['scope'], 'derived')
        self.assertFalse(self.ns['_queue_recovery_manifest_validator'](
            legacy, owner_digest='owner', workspace='project-a', project_dir=self.root))
        # The server-owned transform input is not a new arbitrary file probe
        # through a general generation request.
        sha = Mock(side_effect=AssertionError('must not inspect a foreign input'))
        self.ns['_recovery_sha256_file'] = sha
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.ns['_queue_recovery_input_descriptors']({**job, 'kind': 'studio_generation'}, 'owner')
        sha.assert_not_called()

    def test_recovery_dispatch_never_uses_generation_and_input_is_sealed(self):
        sentinel = object()
        self.ns['_run_tool_hflip'] = sentinel
        load_functions(self.ns, '_queue_recovery_worker', '_startup_recovery_sensitive_path')
        self.assertIs(self.ns['_queue_recovery_worker']({'kind': 'tool_hflip'}), sentinel)
        self.assertTrue(self.ns['_startup_recovery_sensitive_path']('/api/v1/tools/hflip', 'POST'))
        assignment = next(n for n in TREE.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == '_RECOVERABLE_INPUT_KEYS' for t in n.targets))
        self.assertIn('hflip_source_path', ast.get_source_segment((ROOT / 'app/launch.py').read_text(), assignment))
        worker = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == '_run_tool_hflip')
        self.assertNotIn('_WgpNativeGpuExecutionSlot', ast.unparse(worker))


if __name__ == '__main__':
    unittest.main()
