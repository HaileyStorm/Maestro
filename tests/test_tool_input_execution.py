"""Model-free execution of standalone tool input and publication contracts."""
from __future__ import annotations
import ast
import asyncio
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from fastapi import HTTPException
from services.queue_recovery_runtime import (sha256_file, QueueRecoveryRuntimeError, recovery_unit_id,
    artifact_descriptor, validate_artifact_descriptor)
from services.queue_recovery_adapter import owner_principal_digest, QueueRecoveryAdapterError, processed_tool_publication_pending
from services.output_access import stamp_sidecar_policy

TREE = ast.parse((ROOT / 'app/launch.py').read_text())


def load(ns, *names):
    if '_restore_queue_recovery_on_startup' in names:
        ns.setdefault('_startup_recovery_stop', threading.Event())
    nodes = [copy.deepcopy(n) for n in TREE.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    for n in nodes: n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'launch.py', 'exec'), ns)


class ToolInputExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project-a'; self.project.mkdir()
        self.video = self.project / 'clip.mp4'; self.video.write_bytes(b'original')
        self.video.with_suffix('.meta.json').write_text(json.dumps({'workspace': 'project-a', 'private': True}))
        self.uploads = self.root / 'uploads' / 'audio'; self.uploads.mkdir(parents=True)
        self.voice = self.uploads / 'voice.wav'; self.voice.write_bytes(b'voice')
        Path(str(self.voice)+'.access.json').write_text(json.dumps({'owner_session_id': 'session'}))
        self.jobs = {}; self.manifests = {}; self.calls = []
        self.owner = owner_principal_digest(b'tool-test-secret-value', 'session')
        self.ns = dict(os=os, json=json, time=time, uuid=uuid, hmac=hmac, hashlib=hashlib, Request=object,
            HTTPException=HTTPException, QueueRecoveryRuntimeError=QueueRecoveryRuntimeError,
            QueueRecoveryAdapterError=QueueRecoveryAdapterError, owner_principal_digest=owner_principal_digest,
            _app_dir=str(self.root), _RECOVERABLE_INPUT_KEYS={'_tool_input_paths', 'hflip_source_path', 'browser_copy_source_path'},
            recovery_unit_id=recovery_unit_id, _recovery_artifact_descriptor=artifact_descriptor,
            validate_artifact_descriptor=validate_artifact_descriptor,
            _queue_recovery_reconcile_orphan_delivery=lambda *a: None,
            _quarantine_recovery_artifact=lambda *a: None,
            _recovery_sha256_file=sha256_file, _session_secret=lambda: b'tool-test-secret-value',
            read_upload_access_sidecar=lambda p: json.loads(Path(p+'.access.json').read_text()),
            _existing_workspace_dir=lambda ws: str(self.project),
            _queue_recovery_existing_project_identity=lambda p: 'project-digest',
            load_media_sidecars=lambda root, names: {
                name: json.loads((Path(root) / name).with_suffix('.meta.json').read_text())
                for name in names if (Path(root) / name).with_suffix('.meta.json').is_file()
            },
            load_request_manifest=lambda root,pointer,**kw: copy.deepcopy(self.manifests[kw['expected_job_id']]),
            _jobs=self.jobs, _gen_lock=threading.Lock(), _active_gen_states={},
            generation_slot=lambda *a: nullcontext(True), _WgpNativeGpuExecutionSlot=lambda *a: nullcontext(),
            _reserve_workspace_operations=lambda *a: nullcontext(),
            _output_lineage_mutation_guard=lambda *a: nullcontext(),
            try_start=lambda j,**kw: j.update(status='running') or True,
            register_abort_state=lambda *a: True, unregister_abort_state=Mock(),
            is_cancel_requested=lambda j: j.get('status') == 'cancelled',
            processed_tool_publication_pending=processed_tool_publication_pending,
            _queue_recovery_checkpoint=lambda j,**kw: j.update(**kw) or True,
            update_job=lambda j,**kw: j.update(**kw) or True,
            finish_job=lambda j,status,**kw: j.update(status=status,**kw) or True,
            stamp_sidecar_policy=stamp_sidecar_policy, traceback=types.SimpleNamespace(print_exc=lambda: None))
        load(self.ns, '_ToolInputChanged', '_safe_failure_updates', '_job_failure_positions', '_queue_recovery_file_values', '_queue_recovery_input_descriptors',
             '_queue_recovery_manifest_validator', '_validated_tool_input_paths',
             '_processed_tool_settings', '_processed_tool_publication_members', '_retract_processed_tool_publication',
             '_prepare_processed_tool_completion_retry', '_materialize_processed_tool_publication', '_hold_processed_tool_publication',
             '_processed_tool_legacy_cleanup_record', '_settle_processed_tool_legacy_cleanup',
             '_cleanup_cancelled_processed_tool_output', '_resume_processed_tool_output',
             '_h3_dependency_closed_recovery_units', '_queue_recovery_units', '_queue_recovery_unit_matches', '_queue_recovery_reconcile_cursor',
             '_publish_processed_tool_output', '_write_tool_sidecar', '_queue_recovery_worker',
             '_output_revision', '_hflip_source', '_browser_copy_source', '_run_tool_hflip',
             '_run_tool_upscale', '_run_tool_revoice', 'tools_upscale', 'tools_revoice',
             '_request_project_workspace', '_resolve_tool_voice_reference')

    def job(self, kind='tool_revoice', legacy=False):
        if legacy: self.video.with_suffix('.meta.json').unlink()
        paths = [str(self.video)] + ([str(self.voice)] if kind == 'tool_revoice' else [])
        if kind in {'tool_hflip', 'tool_browser_copy'}:
            prefix = 'hflip' if kind == 'tool_hflip' else 'browser_copy'
            params = {
                f'{prefix}_source_path': paths[0], f'{prefix}_source_name': self.video.name,
                f'{prefix}_source_revision': self.ns['_output_revision'](
                    paths[0], str(self.project), self.video.name),
            }
        else:
            params = {'video_path': paths[0], '_tool_input_paths': paths}
            if kind == 'tool_revoice': params['voice_ref_paths'] = paths[1:]
        job = dict(id='a'*32, kind=kind, status='queued', params=params, workspace='project-a',
                   out_dir=str(self.project), _tool_inputs_authorized_live=True,
                   _recovery_owner_digest=self.owner, _recovery_project_digest='project-digest',
                   _recovery_manifest_pointer={}, output_files=[], access_policy={'private': True})
        self.jobs[job['id']] = job
        self.manifests[job['id']] = {'params': copy.deepcopy(params),
            'inputs': self.ns['_queue_recovery_input_descriptors'](job, self.owner)}
        return job

    def test_exact_project_and_upload_inputs_work_after_restore(self):
        job = self.job(); job.pop('_tool_inputs_authorized_live')
        self.assertEqual(self.ns['_validated_tool_input_paths'](job), [str(self.video), str(self.voice)])
        self.assertIs(self.ns['_queue_recovery_worker'](job), self.ns['_run_tool_revoice'])
        self.assertIs(self.ns['_queue_recovery_worker']({'kind':'tool_upscale'}), self.ns['_run_tool_upscale'])

    def _restore_held_tool(self, kind='tool_revoice', **changes):
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import QueueRecoveryCoordinator
        from services.queue_recovery_runtime import (atomic_write_request_manifest,
            load_request_manifest, validate_manifest_inputs, next_recovery_attempt)
        from services.h3_offload_plan import H3_OFFLOAD_PLAN_PARAM_KEY

        project_digest = 'project:v1:' + 'b' * 64
        self.ns['_queue_recovery_existing_project_identity'] = lambda _: project_digest
        job = self.job(kind)
        job['_recovery_project_digest'] = project_digest
        job.update(queue_held=True, recovery_attempt=0, execution_attempt=1)
        job.update(changes)
        pointer = atomic_write_request_manifest(self.project, job_id=job['id'],
            params=job['params'], inputs=self.manifests[job['id']]['inputs'])
        coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.root / (uuid.uuid4().hex + '.jsonl')))
        coordinator.register_job(job, owner_digest=self.owner,
            project_digest=project_digest, request_manifest=pointer)
        self.ns.update(load_request_manifest=load_request_manifest,
            validate_manifest_inputs=validate_manifest_inputs,
            next_recovery_attempt=next_recovery_attempt,
            H3_OFFLOAD_PLAN_PARAM_KEY=H3_OFFLOAD_PLAN_PARAM_KEY,
            _job_uses_registered_h3=lambda _: False,
            _h3_incomplete_recovery_prefix=lambda _: None,
            _queue_recovery_checkpoint_lock=threading.RLock(),
            _queue_recovery_is_blocked=lambda j: str(j.get('recovery_state', '')).startswith('blocked'),
            _require_job_runtime_model_admission=Mock(),
            _start_generation_worker=Mock(side_effect=AssertionError('Wrong generation worker')),
            threading=threading)
        load(self.ns, '_h3_cow_manual_source_supported', '_require_h3_offload_plan_parity', '_queue_recovery_materialize_job',
             '_queue_recovery_revalidate_job', '_start_restored_held_generation_worker')
        snapshot = coordinator.restore().jobs[job['id']]
        restored, may_start = self.ns['_queue_recovery_materialize_job'](snapshot,
            {'project-a': (str(self.project), project_digest)})
        self.assertFalse(may_start)
        self.jobs[job['id']] = restored
        return coordinator, restored

    def test_held_tools_restore_then_release_exact_worker_once(self):
        for kind in ('tool_revoice', 'tool_upscale', 'tool_hflip'):
            with self.subTest(kind=kind):
                coordinator, job = self._restore_held_tool(kind)
                self.assertTrue(job['queue_held'])
                self.assertEqual(job['recovery_attempt'], 0)
                starts = []
                class DeferredThread:
                    def __init__(self, *, target, args, **_kwargs):
                        self.target, self.args = target, args
                    def start(self):
                        starts.append((self.target, self.args))
                with patch.object(threading, 'Thread', DeferredThread):
                    start = self.ns['_start_restored_held_generation_worker']
                    start(job)
                    self.assertEqual(starts, [])
                    job['queue_held'] = False
                    start(job)
                    start(job)
                self.assertEqual(starts, [(self.ns['_run_' + kind], (job['id'],))])
                self.assertNotIn('_recovery_worker_pending', job)
                self.assertEqual(job['recovery_attempt'], 0)

    def test_changed_input_after_held_restore_reblocks_without_worker(self):
        _, job = self._restore_held_tool()
        self.voice.write_bytes(b'changed voice reference')
        job['queue_held'] = False
        with patch.object(threading, 'Thread') as thread, self.assertRaises(HTTPException) as error:
            self.ns['_start_restored_held_generation_worker'](job)
        self.assertEqual(error.exception.status_code, 409)
        thread.assert_not_called()
        self.assertTrue(job['queue_held'])
        self.assertEqual(job['recovery_state'], 'blocked')
        self.assertEqual(job['_recovery_reason_code'], 'input_missing_or_changed')

    def test_remote_held_tool_stays_blocked_without_worker_obligation(self):
        _, job = self._restore_held_tool(source_remote=True)
        self.assertTrue(job['queue_held'])
        self.assertEqual(job['recovery_state'], 'blocked_remote_reauth')
        self.assertNotIn('_recovery_worker_pending', job)

    def test_released_tool_crash_restores_one_automatic_worker_obligation(self):
        coordinator, job = self._restore_held_tool()
        job['queue_held'] = False
        # The durable release precedes Thread.start; simulate death there.
        coordinator.prospective_transition(types.SimpleNamespace(jobs=(job,)))
        snapshot = coordinator.restore().jobs[job['id']]
        self.assertTrue(snapshot['_recovery_worker_pending'])
        restored, may_start = self.ns['_queue_recovery_materialize_job'](snapshot,
            {'project-a': (str(self.project), job['_recovery_project_digest'])})
        self.assertTrue(may_start)
        self.assertEqual(restored['recovery_attempt'], 1)
        self.assertFalse(restored['queue_held'])
        self.assertNotIn('_recovery_worker_pending', restored)
        with patch.object(threading, 'Thread') as thread:
            self.ns['_start_restored_held_generation_worker'](restored)
        thread.assert_not_called()

    def test_concurrent_held_tool_releases_attach_only_one_worker(self):
        _, job = self._restore_held_tool()
        job['queue_held'] = False
        barrier = threading.Barrier(3)
        starts, errors = [], []
        def release():
            try:
                barrier.wait(timeout=5)
                self.ns['_start_restored_held_generation_worker'](job)
            except BaseException as error:
                errors.append(error)
        callers = [threading.Thread(target=release) for _ in range(2)]
        class DeferredThread:
            def __init__(self, **_kwargs):
                pass
            def start(self):
                starts.append(job['id'])
        with patch.object(threading, 'Thread', DeferredThread):
            for caller in callers:
                caller.start()
            barrier.wait(timeout=5)
            for caller in callers:
                caller.join(timeout=5)
        self.assertFalse(any(caller.is_alive() for caller in callers))
        self.assertEqual(errors, [])
        self.assertEqual(starts, [job['id']])

    def test_held_tool_worker_failure_preserves_recoverable_hold(self):
        for failure in ('unavailable', 'thread-start'):
            with self.subTest(failure=failure):
                _, job = self._restore_held_tool()
                job['queue_held'] = False
                if failure == 'unavailable':
                    self.ns['_run_tool_revoice'] = None
                with patch.object(threading, 'Thread') as thread:
                    thread.return_value.start.side_effect = RuntimeError('start failed')
                    expected = HTTPException if failure == 'unavailable' else RuntimeError
                    with self.assertRaises(expected):
                        self.ns['_start_restored_held_generation_worker'](job)
                    if failure == 'unavailable':
                        thread.assert_not_called()
                self.assertTrue(job['queue_held'])
                self.assertEqual(job['recovery_state'], 'blocked')
                self.assertEqual(job['_recovery_reason_code'], 'worker_start_failed')
                self.assertTrue(job['_recovery_worker_pending'])
                load(self.ns, '_run_tool_revoice')

    def test_changed_content_owner_project_or_manifest_is_rejected(self):
        for change in ('content','owner','project','manifest','missing-descriptor'):
            with self.subTest(change=change):
                job = self.job()
                if change == 'content': self.video.write_bytes(b'changed')
                if change == 'owner': job['_recovery_owner_digest'] = owner_principal_digest(b'tool-test-secret-value','other')
                if change == 'project': job['_recovery_project_digest'] = 'other'
                if change == 'manifest': job['params']['video_path'] = '/private/secret.mp4'
                if change == 'missing-descriptor': self.manifests[job['id']]['inputs'] = []
                with self.assertRaisesRegex(ValueError, 'authorization expired'):
                    self.ns['_validated_tool_input_paths'](job)
                self.video.write_bytes(b'original')

    def test_legacy_inputs_require_live_request_authority_and_same_content(self):
        job = self.job('tool_upscale', legacy=True)
        self.assertEqual(self.ns['_validated_tool_input_paths'](job), [str(self.video)])
        job.pop('_tool_inputs_authorized_live')
        with self.assertRaises(ValueError): self.ns['_validated_tool_input_paths'](job)

    def test_hflip_requires_exact_gallery_revision_and_rejects_legacy_restore(self):
        job = self.job('tool_hflip')
        self.assertEqual(self.ns['_validated_tool_input_paths'](job), [str(self.video)])
        sidecar = self.video.with_suffix('.meta.json')
        original = sidecar.stat().st_mtime_ns
        os.utime(sidecar, ns=(original + 100_000, original + 100_000))
        with self.assertRaises(ValueError):
            self.ns['_validated_tool_input_paths'](job)
        job = self.job('tool_hflip', legacy=True)
        self.assertEqual(self.ns['_validated_tool_input_paths'](job), [str(self.video)])
        job.pop('_tool_inputs_authorized_live')
        with self.assertRaises(ValueError):
            self.ns['_validated_tool_input_paths'](job)

    def test_live_derived_authority_is_not_persisted_in_recovery(self):
        from services.queue_recovery import QueueRecoveryJournal
        from services.queue_recovery_adapter import QueueRecoveryCoordinator, project_instance_digest
        job = self.job('tool_upscale', legacy=True)
        job['created_at'] = time.time()
        coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.root/'queue.json'))
        coordinator.register_job(job, owner_digest=self.owner,
            project_digest=project_instance_digest(b'tool-test-secret-value', 'a'*32),
            request_manifest={'kind':'tool_upscale'})
        restored = coordinator.restore().jobs[job['id']]
        self.assertNotIn('_tool_inputs_authorized_live', restored)

    def test_publication_intent_is_durable_before_any_public_mutation_and_failure_blocks_both_names(self):
        job = self.job('tool_hflip')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        def checkpoint(current, **updates):
            intent = updates['recovery_cursor']['processed_tool_publication']
            self.assertEqual(intent['media']['sha256'], hashlib.sha256(staged.read_bytes()).hexdigest())
            self.assertEqual(list(self.project.glob('*_hflip_*')), [])
            return False
        self.ns['_queue_recovery_checkpoint'] = checkpoint
        with self.assertRaises(RuntimeError):
            self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
                tool='hflip', params={}, elapsed=1)
        self.assertEqual(staged.read_bytes(), b'processed')
        self.assertEqual(list(self.project.glob('*_hflip_*')), [])

    def test_cancel_cleanup_preserves_foreign_unsafe_members_and_invalid_bindings(self):
        for changed in ('sidecar', 'symlink', 'hardlink', 'owner', 'project', 'manifest', 'intent'):
            with self.subTest(changed=changed):
                job = self.job('tool_hflip')
                staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
                self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
                    tool='hflip', params={}, elapsed=1))
                media = self.project / job['output_files'][0]
                sidecar = media.with_suffix('.meta.json')
                job.update(status='cancelled', cancel_requested=True)
                if changed == 'sidecar': sidecar.write_bytes(b'foreign marker')
                if changed in ('symlink', 'hardlink'):
                    media.unlink()
                    if changed == 'symlink': media.symlink_to(self.video)
                    else: os.link(self.video, media)
                if changed == 'owner': job['_recovery_owner_digest'] = 'changed'
                if changed == 'project': job['_recovery_project_digest'] = 'changed'
                if changed == 'manifest': job['_recovery_manifest_pointer'] = {'path': 'changed'}
                if changed == 'intent': job['recovery_cursor']['processed_tool_publication']['media']['basename'] = '../clip.mp4'
                before = media.read_bytes(), sidecar.read_bytes(), self.video.read_bytes(), self.voice.read_bytes()
                self.assertFalse(self.ns['_cleanup_cancelled_processed_tool_output'](job))
                self.assertEqual((media.read_bytes(), sidecar.read_bytes(), self.video.read_bytes(), self.voice.read_bytes()), before)
                self.assertEqual(job['status'], 'cancelled')
                self.assertEqual(job['recovery_state'], 'cleanup_blocked')
                self.assertIn('processed_tool_publication', job['recovery_cursor'])
                media.unlink(); sidecar.unlink()

    def test_legacy_byte_only_publication_cannot_delete_present_files_but_settles_absence(self):
        job = self.job('tool_hflip')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
            tool='hflip', params={}, elapsed=1))
        media = self.project / job['output_files'][0]; sidecar = media.with_suffix('.meta.json')
        intent = job['recovery_cursor']['processed_tool_publication']
        intent['schema_version'] = 1
        for key in ('media','sidecar'): intent[key].pop('file_id')
        before = media.read_bytes(), sidecar.read_bytes()
        job.update(status='cancelled', cancel_requested=True)
        self.assertFalse(self.ns['_cleanup_cancelled_processed_tool_output'](job))
        self.assertEqual((media.read_bytes(), sidecar.read_bytes()), before)
        media.unlink(); sidecar.unlink()
        self.assertTrue(self.ns['_cleanup_cancelled_processed_tool_output'](job))
        self.assertEqual(job['status'], 'cancelled'); self.assertFalse(job['queue_held'])
        self.assertNotIn('processed_tool_publication', job['recovery_cursor'])

    def test_cancel_cleanup_preserves_same_inode_replacement_after_hash_with_restored_mtime(self):
        job = self.job('tool_hflip')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
            tool='hflip', params={}, elapsed=1))
        media = self.project / job['output_files'][0]
        sidecar = media.with_suffix('.meta.json')
        original = media.stat()
        source_before = self.video.read_bytes(), self.voice.read_bytes(), sidecar.read_bytes()
        hash_file = self.ns['_recovery_sha256_file']
        def replace_after_hash(path, **kwargs):
            result = hash_file(path, **kwargs)
            if str(path) == str(media):
                media.write_bytes(b'replaced!')
                os.utime(media, ns=(original.st_atime_ns, original.st_mtime_ns))
            return result
        job.update(status='cancelled', cancel_requested=True)
        with patch.dict(self.ns, _recovery_sha256_file=replace_after_hash):
            self.assertFalse(self.ns['_cleanup_cancelled_processed_tool_output'](job))
        current = media.stat()
        self.assertEqual((current.st_ino, current.st_size, current.st_mtime_ns),
                         (original.st_ino, original.st_size, original.st_mtime_ns))
        self.assertNotEqual(current.st_ctime_ns, original.st_ctime_ns)
        self.assertEqual(media.read_bytes(), b'replaced!')
        self.assertEqual((self.video.read_bytes(), self.voice.read_bytes(), sidecar.read_bytes()), source_before)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertIn('processed_tool_publication', job['recovery_cursor'])

    def test_tool_field_cannot_probe_files_from_general_generation(self):
        job = self.job(); job['kind'] = 'studio_generation'
        probe = Mock(side_effect=AssertionError('must not read'))
        self.ns['_recovery_sha256_file'] = probe
        with self.assertRaises(QueueRecoveryRuntimeError):
            self.ns['_queue_recovery_input_descriptors'](job, self.owner)
        probe.assert_not_called()

    def configure_worker(self):
        def filename(root, name, suffix, force_extension):
            return str(Path(root)/(Path(name).stem+suffix+force_extension))
        self.ns['wgp'] = types.SimpleNamespace(save_path=str(self.project), server_config={},
            get_available_filename=filename, format_time=lambda _: '0s',
            extract_audio_tracks=lambda _: ([],[]), cleanup_temp_audio_files=lambda _: None,
            flashvsr=types.SimpleNamespace(is_upsampling=lambda _: True), release_flashvsr_vram=lambda: None)
        def processor(video, *a, **kw):
            self.calls.append(video)
            self.assertEqual(kw["scratch_directory"], self.ns["wgp"].save_path)
            self.assertEqual(list(self.project.glob('*_upscale_*')), [])
            staged = Path(self.ns['wgp'].save_path)/'upscaled.mp4'; staged.write_bytes(b'processed')
            return str(staged)
        self.ns['_chunked_flashvsr_upscale'] = processor
        def revoice(path, voices, **kw):
            self.calls.append((path,voices)); self.assertTrue(Path(path).parent.name.startswith('.tool-'))
            self.assertFalse(kw['cancel_check']())
            self.assertEqual(list(self.project.glob('*_revoice_*')), [])
            Path(path).write_bytes(b'processed'); return True
        return {
            'shared.utils.utils': types.SimpleNamespace(get_video_info=lambda _: (24,32,16,24)),
            'shared.utils.audio_video': types.SimpleNamespace(_remove_encoding_temporary=lambda p: Path(p).unlink(missing_ok=True)),
            'postprocessing.voice_clone': types.SimpleNamespace(apply_voice_clone_to_file=revoice)}

    def test_revoice_progress_reports_activity_and_lifecycle_cancellation_wins(self):
        from services import job_lifecycle as lifecycle
        lifecycle._reset_queue_state_for_tests()
        self.addCleanup(lifecycle._reset_queue_state_for_tests)
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                job = self.job(); modules = self.configure_worker()
                phases = []
                self.ns['try_start'] = lifecycle.try_start
                self.ns['is_cancel_requested'] = lifecycle.is_cancel_requested
                def update(current, **updates):
                    accepted = lifecycle.update_job(current, **updates)
                    phases.append((updates.get('phase'), accepted))
                    return accepted
                self.ns['update_job'] = update
                publish = Mock(return_value=True)
                self.ns['_publish_processed_tool_output'] = publish
                def convert(path, refs, **kwargs):
                    callback = kwargs['progress_callback']
                    callback({'audio_chunk': 1, 'diffusion_step': 1, 'diffusion_steps': 25})
                    self.assertEqual(job['progress'], 10)
                    self.assertEqual(job['phase'], 'Audio chunk 1: starting voice step 1 of 25')
                    callback(None)
                    self.assertEqual(job['phase'], '')
                    if cancelled:
                        self.assertTrue(lifecycle.request_cancel(job))
                        before = (job['phase'], job['message'], job['progress'])
                        callback({'audio_chunk': 2, 'diffusion_step': 4, 'diffusion_steps': 25})
                        self.assertEqual((job['phase'], job['message'], job['progress']), before)
                        self.assertTrue(kwargs['cancel_check']())
                        return False
                    Path(path).write_bytes(b'processed')
                    return True
                modules['postprocessing.voice_clone'] = types.SimpleNamespace(apply_voice_clone_to_file=convert)
                with patch.dict(sys.modules, modules):
                    self.assertEqual(self.ns['_run_tool_revoice'](job['id']), not cancelled)
                if cancelled:
                    self.assertEqual(job['status'], 'cancelled')
                    self.assertEqual(job['message'], 'Cancelled')
                    self.assertIn(('Audio chunk 2: starting voice step 4 of 25', False), phases)
                    publish.assert_not_called()
                else:
                    publish.assert_called_once()
                    self.assertEqual(job['phase'], '')
                self.assertEqual(self.video.read_bytes(), b'original')
                self.assertEqual(list(self.project.glob('.tool-*')), [])

    def test_both_workers_reach_processor_and_publish_only_owned_result(self):
        for kind in ('tool_upscale','tool_revoice'):
            with self.subTest(kind=kind):
                job = self.job(kind)
                with patch.dict(sys.modules, self.configure_worker()):
                    self.assertTrue(self.ns['_run_'+kind](job['id']))
                self.assertEqual(job['status'], 'completed')
                self.assertEqual(len(job['output_files']),1)
                output = self.project/job['output_files'][0]
                self.assertEqual(output.read_bytes(), b'processed')
                self.assertTrue(json.loads(output.with_suffix('.meta.json').read_text())['private'])
                self.assertEqual(self.video.read_bytes(), b'original')
                self.assertEqual(list(self.project.glob('.tool-*')), [])
                self.assertEqual(self.ns['wgp'].save_path, str(self.project))

    def test_changed_input_never_reaches_either_processor(self):
        for kind in ('tool_upscale','tool_revoice'):
            job = self.job(kind); self.video.write_bytes(b'changed')
            with patch.dict(sys.modules, self.configure_worker()):
                self.assertFalse(self.ns['_run_'+kind](job['id']))
            self.assertEqual(self.calls, [])
            self.assertEqual(job['status'],'failed'); self.video.write_bytes(b'original')

    def test_missing_upscale_result_is_terminal_and_empty_output_is_not_published(self):
        job = self.job('tool_upscale')
        modules = self.configure_worker()
        self.ns['_chunked_flashvsr_upscale'] = lambda *a, **kw: None
        with patch.dict(sys.modules, modules):
            self.assertFalse(self.ns['_run_tool_upscale'](job['id']))
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['output_files'], [])
        staged=self.root/'empty.mp4'; staged.touch()
        with self.assertRaises(ValueError):
            self.ns['_publish_processed_tool_output'](job,str(staged),source=str(self.video),
                            tool='upscale',params={},elapsed=1)
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_upscale_sidecar_records_only_measured_video_facts(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        input_facts = {'size_bytes': len(b'original'), 'width': 64, 'height': 48,
                       'fps': 24, 'duration_seconds': 1, 'has_audio': True}
        output_facts = {'size_bytes': len(b'processed'), 'width': 128, 'height': 96,
                        'fps': 24, 'duration_seconds': 1, 'has_audio': True}
        paths = []
        def probe(path, **_kwargs):
            paths.append(path)
            return output_facts if path == str(staged) else input_facts
        with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=probe)}):
            self.assertTrue(self.ns['_publish_processed_tool_output'](
                job, str(staged), source=str(self.video), tool='upscale',
                params={'method': 'lanczos2', 'prompt': 'private creative text'}, elapsed=1))
        metadata = json.loads((self.project / job['output_files'][0]).with_suffix('.meta.json').read_text())
        self.assertEqual(metadata['processing'], {
            'version': 1, 'input': input_facts, 'output': output_facts,
        })
        self.assertEqual(paths, [str(staged), str(self.video)])
        self.assertTrue(metadata['private'])
        self.assertNotIn('prompt', json.dumps(metadata['processing']))
        self.assertNotIn(str(self.project), json.dumps(metadata['processing']))

    def test_generation_receipts_survive_ordered_repeated_tools_without_private_fields(self):
        initial = [
            {'step': 'upscale', 'outcome': 'applied', 'method': 'lanczos2', 'private_path': str(self.voice)},
            {'step': 'film_grain', 'outcome': 'unconfirmed'},
            {'step': 'voice_clone', 'outcome': 'not_applied'},
        ]
        source_meta = self.video.with_suffix('.meta.json')
        source_meta.write_text(json.dumps({'workspace': 'project-a', 'private': True,
            'postprocessing': {'version': 1, 'steps': initial},
            'params': {'voice_ref_paths': [str(self.voice)]}}))
        original = self.video.read_bytes(), source_meta.read_bytes()
        expected = [{k: v for k, v in record.items() if k != 'private_path'} for record in initial]
        first_source = self.video
        for tool in ('upscale', 'hflip', 'revoice', 'browser_copy'):
            job = self.job('tool_' + tool)
            staged = self.root / 'processed.mp4'; staged.write_bytes(b'processed-' + tool.encode())
            with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=lambda *a, **kw: None)}):
                self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged),
                    source=str(self.video), tool=tool, params={'method': 'lanczos2'}, elapsed=1))
            self.video = self.project / job['output_files'][0]
            metadata = json.loads(self.video.with_suffix('.meta.json').read_text())
            if tool in {'upscale', 'revoice'}:
                event = {'step': 'upscale' if tool == 'upscale' else 'voice_clone', 'outcome': 'applied'}
                if tool == 'upscale': event['method'] = 'lanczos2'
                expected.append(event)
            self.assertEqual(metadata['postprocessing'], {'version': 1, 'steps': expected})
            self.assertTrue(metadata['private'])
            self.assertNotIn(str(self.root), json.dumps(metadata['postprocessing']))
        self.assertEqual((first_source.read_bytes(), source_meta.read_bytes()), original)

    def test_editor_branch_history_survives_tools_with_whole_output_steps_separate(self):
        child = {'version': 1, 'steps': [{'step': 'film_grain', 'outcome': 'unconfirmed'}]}
        history = {'version': 2, 'steps': [], 'branches': [
            {'name': 'first.mp4', 'revision': 'sha256:' + 'a' * 64, 'source_in': 1, 'duration': 2, 'history': child},
            {'name': 'legacy.mp4', 'revision': 'sha256:' + 'b' * 64, 'source_in': 0, 'duration': 1},
        ]}
        self.video.with_suffix('.meta.json').write_text(json.dumps({'workspace':'project-a','private':True,'postprocessing':history}))
        for tool in ('upscale', 'hflip', 'revoice', 'browser_copy'):
            job = self.job('tool_' + tool)
            staged = self.root / 'processed.mp4'; staged.write_bytes(b'processed-' + tool.encode())
            with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=lambda *a, **kw: None)}):
                self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video), tool=tool,
                    params={'method':'lanczos2','postprocessing':{'version':1,'steps':[{'step':'delivery_fit','outcome':'applied'}]}}, elapsed=1))
            self.video = self.project / job['output_files'][0]
            metadata = json.loads(self.video.with_suffix('.meta.json').read_text())
            if tool in {'upscale', 'revoice'}:
                history['steps'].append({'step':'upscale' if tool == 'upscale' else 'voice_clone', 'outcome':'applied',
                    **({'method':'lanczos2'} if tool == 'upscale' else {})})
            self.assertEqual(metadata['postprocessing'], history)

    def test_nested_source_histories_bound_all_source_rows_and_report_omissions(self):
        from services.recorded_finishing import sanitize_history
        history = {'version':2,'steps':[], 'branches': [
            {'name':f'clip-{i}.mp4','revision':'sha256:'+'a'*64,'source_in':0,'duration':1} for i in range(8)]}
        for _ in range(5):
            history = {'version':2,'steps':[], 'branches': [
                {'name':f'export-{i}.mp4','revision':'sha256:'+'b'*64,'source_in':0,'duration':1,'history':history} for i in range(8)]}
        result = sanitize_history(history)
        def counts(node):
            rows = node.get('branches', [])
            nested = [counts(row['history']) for row in rows if 'history' in row]
            return len(rows) + sum(c[0] for c in nested), node.get('omitted_branches',0) + sum(c[1] for c in nested)
        count, omitted = counts(result)
        self.assertLessEqual(count, 64)
        self.assertGreater(omitted, 0)
        self.assertEqual(sanitize_history(result), result)

    def test_history_bound_retains_newest_repeats_and_counts_earlier_records(self):
        source_meta = self.video.with_suffix('.meta.json')
        source_meta.write_text(json.dumps({'workspace': 'project-a', 'private': True,
            'postprocessing': {'version': 1, 'omitted_steps': 4, 'steps': [
                {'step': 'upscale', 'outcome': 'applied', 'method': f'lanczos{index}'} for index in range(1, 34)
            ]}}))
        job = self.job('tool_revoice')
        staged = self.root / 'processed.mp4'; staged.write_bytes(b'revoiced')
        self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
            tool='revoice', params={'mode': 'single'}, elapsed=1))
        metadata = json.loads((self.project / job['output_files'][0]).with_suffix('.meta.json').read_text())
        history = metadata['postprocessing']
        self.assertEqual(history['omitted_steps'], 6)
        self.assertEqual(len(history['steps']), 32)
        self.assertEqual(history['steps'][0]['method'], 'lanczos3')
        self.assertEqual(history['steps'][-2]['method'], 'lanczos33')
        self.assertEqual(history['steps'][-1], {'step': 'voice_clone', 'outcome': 'applied'})

    def test_history_never_imports_request_upload_legacy_or_malformed_receipts(self):
        for excluded in ('request', 'legacy', 'upload', 'malformed'):
            with self.subTest(excluded=excluded):
                self.video = self.project / 'clip.mp4'
                sidecar = self.video.with_suffix('.meta.json')
                sidecar.write_text(json.dumps({'workspace': 'project-a', 'private': True,
                    'params': {'postprocessing': {'version': 1, 'steps': [{'step': 'film_grain', 'outcome': 'applied'}]}},
                    **({'postprocessing': {'version': 1, 'omitted_steps': True,
                        'steps': [{'step': 'film_grain', 'outcome': 'applied'}]}} if excluded == 'malformed' else {})}))
                job = self.job('tool_revoice', legacy=excluded == 'legacy')
                if excluded == 'upload':
                    upload = self.uploads / 'uploaded.mp4'; upload.write_bytes(b'uploaded')
                    Path(str(upload) + '.access.json').write_text(json.dumps({'owner_session_id': 'session',
                        'postprocessing': {'version': 1, 'steps': [{'step': 'film_grain', 'outcome': 'applied'}]}}))
                    job['params']['video_path'] = str(upload)
                    job['params']['_tool_input_paths'][0] = str(upload)
                    self.manifests[job['id']] = {'params': copy.deepcopy(job['params']),
                        'inputs': self.ns['_queue_recovery_input_descriptors'](job, self.owner)}
                staged = self.root / 'processed.mp4'; staged.write_bytes(b'revoiced')
                self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged),
                    source=job['params']['video_path'], tool='revoice',
                    params={'postprocessing': {'version': 1, 'steps': [{'step': 'film_grain', 'outcome': 'applied'}]}}, elapsed=1))
                output = self.project / job['output_files'][0]
                metadata = json.loads(output.with_suffix('.meta.json').read_text())
                self.assertEqual(metadata['postprocessing'], {'version': 1, 'steps': [{'step': 'voice_clone', 'outcome': 'applied'}]})
                output.unlink(); output.with_suffix('.meta.json').unlink()

    def test_source_sidecar_replaced_between_validation_and_history_read_blocks_publication(self):
        job = self.job('tool_revoice')
        staged = self.root / 'processed.mp4'; staged.write_bytes(b'revoiced')
        sidecar = self.video.with_suffix('.meta.json')
        validate = self.ns['_validated_tool_input_paths']
        passed = []
        def replace_after_validation(current):
            paths = validate(current)
            passed.append(paths)
            sidecar.write_text(json.dumps({'workspace': 'project-a', 'postprocessing': {
                'version': 1, 'steps': [{'step': 'film_grain', 'outcome': 'applied'}]}}))
            return paths
        self.ns['_validated_tool_input_paths'] = replace_after_validation
        with self.assertRaises(self.ns['_ToolInputChanged']):
            self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
                tool='revoice', params={}, elapsed=1)
        self.assertEqual(len(passed), 1)
        self.assertEqual(job['output_files'], [])
        self.assertEqual(list(self.project.glob('*_revoice_*')), [])
        self.assertEqual(self.video.read_bytes(), b'original')

    def test_oversize_sealed_sidecar_does_not_block_optional_history_publication(self):
        sidecar = self.video.with_suffix('.meta.json')
        sidecar.write_text(json.dumps({'workspace': 'project-a', 'private': True,
            'params': {'prompt': 'x' * (1024 * 1024)}}))
        original = self.video.read_bytes(), sidecar.read_bytes()
        self.assertGreater(len(original[1]), 1024 * 1024)
        for tool in ('upscale', 'hflip', 'browser_copy'):
            with self.subTest(tool=tool):
                job = self.job('tool_' + tool)
                staged = self.root / 'processed.mp4'; staged.write_bytes(b'processed')
                with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=lambda *a, **kw: None)}):
                    self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged),
                        source=str(self.video), tool=tool, params={'method': 'lanczos2'}, elapsed=1))
                output = self.project / job['output_files'][0]
                metadata = json.loads(output.with_suffix('.meta.json').read_text())
                self.assertEqual(job['status'], 'completed')
                if tool == 'upscale':
                    self.assertEqual(metadata['postprocessing'], {'version': 1, 'steps': [
                        {'step': 'upscale', 'outcome': 'applied', 'method': 'lanczos2'}]})
                else:
                    self.assertNotIn('postprocessing', metadata)
                self.assertEqual((self.video.read_bytes(), sidecar.read_bytes()), original)

    def test_optional_upscale_probe_failure_preserves_completed_output(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        def probe(path, **_kwargs):
            return {'size_bytes': len(b'processed'), 'width': 128, 'height': 96} if path == str(staged) else None
        with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=probe)}):
            self.assertTrue(self.ns['_publish_processed_tool_output'](
                job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1))
        metadata = json.loads((self.project / job['output_files'][0]).with_suffix('.meta.json').read_text())
        self.assertNotIn('processing', metadata)
        self.assertEqual(job['status'], 'completed')

    def test_cancelled_upscale_skips_optional_probes_and_publication(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        paths = []
        def probe(path, **_kwargs):
            paths.append(path)
            job['status'] = 'cancelled'
            return {'size_bytes': len(b'processed'), 'width': 128, 'height': 96}
        with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=probe)}):
            job['status'] = 'cancelled'
            self.assertFalse(self.ns['_publish_processed_tool_output'](
                job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1))
            self.assertEqual(paths, [])
            job['status'] = 'queued'
            self.assertFalse(self.ns['_publish_processed_tool_output'](
                job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1))
        self.assertEqual(paths, [str(staged)])
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_same_size_staged_change_during_probe_is_not_published(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'processed')
        def probe(path, **_kwargs):
            if path == str(staged):
                staged.write_bytes(b'changed!!')
                return {'size_bytes': len(b'processed'), 'width': 128, 'height': 96}
            return {'size_bytes': len(b'original'), 'width': 64, 'height': 48}
        with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(probe_video_facts=probe)}):
            with self.assertRaisesRegex(ValueError, 'output changed'):
                self.ns['_publish_processed_tool_output'](
                    job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1)
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_cancel_during_staged_verification_hash_publishes_nothing(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'x' * (2 * 1024 * 1024))
        original_hash = self.ns['_recovery_sha256_file']
        staged_hashes = 0
        def cancelling_hash(path, **kwargs):
            nonlocal staged_hashes
            if path != str(staged):
                return original_hash(path, **kwargs)
            staged_hashes += 1
            if staged_hashes != 2:
                return original_hash(path, **kwargs)
            checks = 0
            def abort_during_read():
                nonlocal checks
                checks += 1
                if checks >= 3:
                    job['status'] = 'cancelled'
                return kwargs['abort_check']()
            return original_hash(path, abort_check=abort_during_read)
        self.ns['_recovery_sha256_file'] = cancelling_hash
        with patch.dict(sys.modules, {'services.media_info': types.SimpleNamespace(
            probe_video_facts=lambda path, **_kwargs: None,
        )}):
            self.assertFalse(self.ns['_publish_processed_tool_output'](
                job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1))
        self.assertEqual(staged_hashes, 2)
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_cancel_during_initial_staged_hash_stops_read_and_publishes_nothing(self):
        job = self.job('tool_upscale')
        staged = self.project / 'staged.mp4'; staged.write_bytes(b'x' * (2 * 1024 * 1024))
        original_hash = self.ns['_recovery_sha256_file']
        staged_hashes = 0

        def cancelling_hash(path, **kwargs):
            nonlocal staged_hashes
            if path != str(staged):
                return original_hash(path, **kwargs)
            staged_hashes += 1
            checks = 0

            def abort_during_read():
                nonlocal checks
                checks += 1
                if checks >= 3:
                    job['status'] = 'cancelled'
                return kwargs['abort_check']()

            return original_hash(path, abort_check=abort_during_read)

        self.ns['_recovery_sha256_file'] = cancelling_hash
        self.assertFalse(self.ns['_publish_processed_tool_output'](
            job, str(staged), source=str(self.video), tool='upscale', params={}, elapsed=1))
        self.assertEqual(staged_hashes, 1)
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_gpu_failure_keeps_safe_oom_diagnostics_without_paths(self):
        job = self.job('tool_upscale')
        modules = self.configure_worker()
        self.ns['_chunked_flashvsr_upscale'] = Mock(side_effect=RuntimeError('CUDA out of memory: /private/operator/file'))
        with patch.dict(sys.modules, modules):
            self.assertFalse(self.ns['_run_tool_upscale'](job['id']))
        self.assertTrue(job['failure_details']['is_oom'])
        self.assertEqual(job['failure_details']['stage'], 'flashvsr')
        self.assertNotIn('/private/', json.dumps(job['failure_details']))
        self.assertNotIn('/private/', job['error'])

    def test_cancelled_publication_has_no_output_or_sidecar(self):
        job = self.job('tool_upscale'); job['status']='running'
        staged=self.root/'result.mp4'; staged.write_bytes(b'done')
        def finish(j,*a,**kw): j['status']='cancelled'; return False
        self.ns['finish_job']=finish
        self.assertFalse(self.ns['_publish_processed_tool_output'](job,str(staged),source=str(self.video),
                           tool='upscale',params={},elapsed=1))
        self.assertEqual(list(self.project.glob('*_upscale_*')), [])

    def test_crash_after_publication_recovers_without_processor_replay(self):
        class ProcessDeath(BaseException): pass
        job = self.job('tool_upscale')
        modules = self.configure_worker()
        original_finish = self.ns['finish_job']
        self.ns['finish_job'] = Mock(side_effect=ProcessDeath())
        with patch.dict(sys.modules, modules), self.assertRaises(ProcessDeath):
            self.ns['_run_tool_upscale'](job['id'])
        self.assertEqual(len(self.calls), 1)
        outputs = list(self.project.glob('*_upscale_*.mp4'))
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].stat().st_nlink, 1)
        self.assertEqual(job['output_files'], [])
        job['status'] = 'queued'
        self.ns['finish_job'] = original_finish
        with patch.dict(sys.modules, modules):
            self.assertTrue(self.ns['_run_tool_upscale'](job['id']))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(job['output_files'], [outputs[0].name])

    def test_hflip_crash_adopts_sealed_result_without_encoding_again(self):
        class ProcessDeath(BaseException): pass
        job = self.job('tool_hflip')
        calls = []
        def flip(src, dst, **_kw):
            calls.append(src)
            Path(dst).write_bytes(b'flipped-with-audio')
        original_finish = self.ns['finish_job']
        self.ns['finish_job'] = Mock(side_effect=ProcessDeath())
        with patch.dict(sys.modules, {'services.video_transform': types.SimpleNamespace(horizontal_flip=flip)}), self.assertRaises(ProcessDeath):
            self.ns['_run_tool_hflip'](job['id'])
        outputs = list(self.project.glob('*_hflip_*.mp4'))
        self.assertEqual(len(outputs), 1)
        self.assertEqual(job['output_files'], [])
        metadata = json.loads(outputs[0].with_suffix('.meta.json').read_text())
        self.assertEqual(metadata['tool_source_revision'], job['params']['hflip_source_revision'])
        self.assertEqual(metadata['producer_media_sha256'], sha256_file(str(outputs[0]))[1])
        job['status'] = 'queued'
        self.ns['finish_job'] = original_finish
        with patch.dict(sys.modules, {'services.video_transform': types.SimpleNamespace(horizontal_flip=flip)}):
            self.assertTrue(self.ns['_run_tool_hflip'](job['id']))
        self.assertEqual(calls, [str(self.video)])
        self.assertEqual(job['output_files'], [outputs[0].name])
        self.assertEqual(self.video.read_bytes(), b'original')

    def test_hflip_crash_rejects_changed_source_before_adoption(self):
        class ProcessDeath(BaseException): pass
        job = self.job('tool_hflip')
        calls = []
        def flip(src, dst, **_kw):
            calls.append(src)
            Path(dst).write_bytes(b'flipped-with-audio')
        original_finish = self.ns['finish_job']
        self.ns['finish_job'] = Mock(side_effect=ProcessDeath())
        with patch.dict(sys.modules, {'services.video_transform': types.SimpleNamespace(horizontal_flip=flip)}), self.assertRaises(ProcessDeath):
            self.ns['_run_tool_hflip'](job['id'])
        output = next(self.project.glob('*_hflip_*.mp4'))
        sidecar = output.with_suffix('.meta.json')
        original_media = output.read_bytes()
        original_metadata = sidecar.read_bytes()
        source_sidecar = self.video.with_suffix('.meta.json')
        original_mtime = source_sidecar.stat().st_mtime_ns
        os.utime(source_sidecar, ns=(original_mtime + 100_000, original_mtime + 100_000))
        job['status'] = 'queued'
        self.ns['finish_job'] = original_finish
        with patch.dict(sys.modules, {'services.video_transform': types.SimpleNamespace(horizontal_flip=flip)}):
            self.assertFalse(self.ns['_run_tool_hflip'](job['id']))
        self.assertEqual(calls, [str(self.video)])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['output_files'], [])
        self.assertEqual(output.read_bytes(), original_media)
        self.assertEqual(sidecar.read_bytes(), original_metadata)

    def test_post_rename_durability_failure_retracts_partial_or_adopts_complete_pair(self):
        from services.atomic_file_publish import publish_file_no_replace, PublishedFileDurabilityError
        for boundary in ('metadata', 'media'):
            with self.subTest(boundary=boundary):
                job = self.job('tool_upscale')
                staged = self.project/'staged.mp4'; staged.write_bytes(b'processed')
                def sync_failure(src, dst):
                    publish_file_no_replace(src, dst)
                    if dst.endswith('.meta.json') == (boundary == 'metadata'):
                        raise PublishedFileDurabilityError(5, 'io')
                with patch('services.atomic_file_publish.publish_file_no_replace', side_effect=sync_failure):
                    with self.assertRaises((RuntimeError, PublishedFileDurabilityError)):
                        self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
                            tool='upscale', params={}, elapsed=1)
                if boundary == 'metadata':
                    self.assertEqual(list(self.project.glob('*_upscale_*')), [])
                    self.assertNotIn('processed_tool_publication', job['recovery_cursor'])
                else:
                    pair = list(self.project.glob('*_upscale_*'))
                    self.assertEqual(len(pair), 2)
                    before = {path.name: path.read_bytes() for path in pair}
                    job.update(status='queued', output_files=[])
                    self.assertTrue(self.ns['_resume_processed_tool_output'](job))
                    self.assertEqual({path.name:path.read_bytes() for path in pair}, before)
                    for path in pair: path.unlink()
                self.assertEqual(self.video.read_bytes(), b'original')

    def test_publication_failure_preserves_same_bytes_foreign_inode_and_its_own_marker(self):
        from services.atomic_file_publish import publish_file_no_replace, PublishedFileDurabilityError
        for boundary in ('publication','completion'):
            with self.subTest(boundary=boundary):
                job = self.job('tool_hflip')
                staged = self.project/'staged.mp4'; staged.write_bytes(b'processed')
                output = self.project/f"clip_hflip_{job['id']}.mp4"
                marker = output.with_suffix('.meta.json')
                def replace():
                    foreign = self.project/'foreign.mp4'
                    foreign.write_bytes(output.read_bytes()); os.replace(foreign,output)
                def publish(source,destination):
                    publish_file_no_replace(source,destination)
                    if str(destination) == str(output) and boundary == 'publication':
                        replace(); raise PublishedFileDurabilityError(5,'io')
                def finish(current,*args,**kwargs):
                    replace(); raise OSError('completion persistence failed')
                with patch('services.atomic_file_publish.publish_file_no_replace',side_effect=publish), \
                        patch.dict(self.ns,finish_job=finish):
                    with self.assertRaisesRegex(ValueError,'ownership'):
                        self.ns['_publish_processed_tool_output'](job,str(staged),source=str(self.video),
                            tool='hflip',params={},elapsed=1)
                before = output.read_bytes(),marker.read_bytes(),output.stat().st_ino
                job.update(status='cancelled',cancel_requested=True)
                self.assertFalse(self.ns['_cleanup_cancelled_processed_tool_output'](job))
                self.assertEqual((output.read_bytes(),marker.read_bytes(),output.stat().st_ino),before)
                self.assertEqual(job['recovery_state'],'cleanup_blocked')
                self.assertEqual(self.video.read_bytes(),b'original')
                output.unlink(); marker.unlink()

    def test_cancellation_during_recovery_adoption_removes_exact_owned_result(self):
        job = self.job('tool_upscale')
        staged = self.project/'staged.mp4'; staged.write_bytes(b'processed')
        self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged),
            source=str(self.video), tool='upscale', params={}, elapsed=1))
        output = self.project/job['output_files'][0]
        job.update(status='running', output_files=[])
        def cancelled(j, *args, **kwargs):
            j['status'] = 'cancelled'
            return False
        self.ns['finish_job'] = cancelled
        self.assertFalse(self.ns['_resume_processed_tool_output'](job))
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.meta.json').exists())
        self.assertEqual(self.video.read_bytes(), b'original')

    def test_changed_published_output_is_never_adopted(self):
        job = self.job('tool_upscale')
        staged = self.project/'staged.mp4'; staged.write_bytes(b'processed')
        self.assertTrue(self.ns['_publish_processed_tool_output'](job, str(staged),
            source=str(self.video), tool='upscale', params={}, elapsed=1))
        output = self.project/job['output_files'][0]
        output.write_bytes(b'changed')
        job.update(status='queued', output_files=[])
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.ns['_resume_processed_tool_output'](job)
        self.assertEqual(job['output_files'], [])
        self.assertEqual(output.read_bytes(), b'changed')

    def test_sidecar_only_crash_can_replay_but_foreign_marker_is_preserved(self):
        class ProcessDeath(BaseException): pass
        from services.atomic_file_publish import publish_file_no_replace
        job = self.job('tool_upscale')
        staged = self.project/'staged.mp4'; staged.write_bytes(b'processed')
        def interrupted(src, dst):
            if dst.endswith('.mp4'): raise ProcessDeath()
            return publish_file_no_replace(src, dst)
        with patch('services.atomic_file_publish.publish_file_no_replace', side_effect=interrupted):
            with self.assertRaises(ProcessDeath):
                self.ns['_publish_processed_tool_output'](job, str(staged), source=str(self.video),
                    tool='upscale', params={}, elapsed=1)
        marker = next(self.project.glob('*_upscale_*.meta.json'))
        original = marker.read_text()
        meta = json.loads(original); meta['job_id'] = 'foreign'
        marker.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, 'bytes changed'):
            self.ns['_resume_processed_tool_output'](job)
        self.assertTrue(marker.exists())
        marker.write_text(original)
        self.assertIsNone(self.ns['_resume_processed_tool_output'](job))
        self.assertFalse(marker.exists())

    def test_remote_routes_require_project_and_pin_all_authorized_references(self):
        def request(body):
            async def read(): return body
            return types.SimpleNamespace(json=read,state=types.SimpleNamespace(maestro_remote=True,maestro_session_id='session'))
        self.ns.update(_get_active_workspace=lambda: self.fail('host-global fallback'),
            _require_project_access=lambda *a,**kw: str(self.project),
            _resolve_authorized_request_media=lambda req,path,ws: path if path in {str(self.video),str(self.voice)} else None,
            _inherit_media_access_policy=lambda *a: {'private':True,'explicit':False},
            _new_generation_job_id=lambda: 'b'*32,
            _request_remote=types.SimpleNamespace(get=lambda: True),
            _queue_recovery_register_and_publish=lambda job,**kw: self.jobs.update({job['id']:job}))
        for route in ('tools_upscale','tools_revoice'):
            with self.assertRaises(HTTPException):
                asyncio.run(self.ns[route](request({'video_path':str(self.video)})))
        body={'workspace':'project-a','video_path':str(self.video),'voice_ref_paths':[str(self.voice)]}
        asyncio.run(self.ns['tools_revoice'](request(body)))
        self.assertEqual(self.jobs['b'*32]['params']['_tool_input_paths'], [str(self.video),str(self.voice)])
        self.jobs.clear(); body['voice_ref_paths'].append('/missing/voice.wav')
        with self.assertRaises(HTTPException): asyncio.run(self.ns['tools_revoice'](request(body)))
        self.assertEqual(self.jobs,{})

    def test_gallery_voice_reference_uses_exact_output_even_with_same_named_upload(self):
        import wave
        gallery_voice = self.project / self.voice.name
        with wave.open(str(gallery_voice), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(b'\x00\x00' * 800)
        gallery_voice.with_suffix('.meta.json').write_text(json.dumps({
            'workspace': 'project-a', 'private': True, 'explicit': True,
        }))
        load(self.ns, '_require_authorized_output', '_resolve_authorized_request_media',
             '_inherit_media_access_policy')
        self.ns.update(
            _require_project_access=lambda req, ws, **kw: str(self.project) if ws == 'project-a' else self._deny_project(),
            _require_upload_content_access=lambda *a: None,
            can_access_upload=lambda path, session: path == str(self.voice) and session == 'session',
            _workspace_dir=lambda ws: str(self.project),
            _new_generation_job_id=lambda: 'b'*32,
            _request_remote=types.SimpleNamespace(get=lambda: True),
            _queue_recovery_register_and_publish=lambda job, **kw: self.jobs.update({job['id']: job}),
        )
        revision = self.ns['_output_revision'](str(gallery_voice), str(self.project), gallery_voice.name)
        selected = {'name': gallery_voice.name, 'revision': revision}
        body = {'workspace': 'project-a', 'video_path': str(self.video),
                'voice_ref_paths': [selected, str(self.voice)], 'mode': 'two'}
        with patch('os.getcwd', return_value=str(self.root)):
            asyncio.run(self.ns['tools_revoice'](self._tool_request(body)))
        job = self.jobs['b'*32]
        self.assertEqual(job['params']['voice_ref_paths'], [str(gallery_voice), str(self.voice)])
        self.assertEqual(job['params']['_tool_input_paths'], [str(self.video), str(gallery_voice), str(self.voice)])
        self.assertTrue(job['params']['private_output'])
        self.assertTrue(job['params']['explicit_output'])
        descriptors = self.ns['_queue_recovery_input_descriptors'](job, self.owner)
        self.assertEqual({d['path'] for d in descriptors}, {str(self.video), str(gallery_voice), str(self.voice)})

        self.jobs.clear()
        gallery_voice.write_bytes(b'changed')
        with patch('os.getcwd', return_value=str(self.root)), self.assertRaises(HTTPException) as error:
            asyncio.run(self.ns['tools_revoice'](self._tool_request(body)))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.jobs, {})

        for invalid in ({'name': '../voice.wav', 'revision': revision},
                        {'name': self.voice.name}, {'name': self.voice.name, 'revision': ''},
                        {'name': self.voice.name, 'revision': revision, 'workspace': 'other'}, 17, ''):
            with self.subTest(reference=invalid), patch('os.getcwd', return_value=str(self.root)), self.assertRaises(HTTPException):
                asyncio.run(self.ns['tools_revoice'](self._tool_request({**body, 'voice_ref_paths': [invalid]})))
            self.assertEqual(self.jobs, {})
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.ns['tools_revoice'](self._tool_request({**body, 'workspace': 'other'})))
        self.assertEqual(error.exception.status_code, 403)

    @staticmethod
    def _deny_project():
        raise HTTPException(status_code=403, detail='Project access required')

    @staticmethod
    def _tool_request(body):
        async def read(): return body
        return types.SimpleNamespace(json=read, state=types.SimpleNamespace(
            maestro_remote=True, maestro_session_id='session'))


if __name__ == '__main__': unittest.main()
