"""Real SIGKILL/CPU-media recovery, with synthetic app lifecycle wiring.

The worker, publication/adoption functions and journal are production code.
This does not exercise a live server restart, owner authentication or a GPU.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import subprocess
import unittest
import threading
import types
from unittest.mock import patch

import test_tool_input_execution as tool_fixture
import test_editor_export_route as editor_fixture
from services.queue_recovery import QueueRecoveryJournal
from services.video_transform import horizontal_flip
from services.atomic_file_publish import publish_file_no_replace
from services.queue_recovery_adapter import (QueueRecoveryCoordinator, project_instance_digest,
    processed_tool_publication_pending, AUTOMATIC_RETIREMENT_STATUSES)
from services.queue_recovery_runtime import (atomic_write_request_manifest, load_request_manifest,
    cleanup_orphan_request_manifests, cleanup_orphan_staged_outputs, validate_manifest_inputs,
    discover_request_manifest_pointers)
from services import job_lifecycle


def commit(journal, job):
    snapshot = journal.recover()
    journal.commit_job(job['id'], job,
                       expected_revision=snapshot.job_revisions.get(job['id'], 0),
                       expected_epoch=snapshot.epoch)


def run_worker(fixture, journal_path, counter, boundary=None, connection=None):
    """Fresh persisted job; only the parent owns disposable filesystem cleanup."""
    os.setsid()  # Exact owned group also bounds encoder cleanup on test failure.
    journal = QueueRecoveryJournal(journal_path)
    job = copy.deepcopy(next(iter(journal.recover().jobs.values())))
    durable = boundary in {'cancel-completion', 'cancel-media', 'adoption-ready', 'cancel-adoption'}
    if durable:
        coordinator = QueueRecoveryCoordinator(journal)
        coordinator.restore()
        job.update(_recovery_owner_digest=job['owner_principal'],
                   _recovery_project_digest=job['project_instance'],
                   _recovery_manifest_pointer=job['request_manifest'])
        job['params'] = load_request_manifest(fixture.project, job['request_manifest'], expected_job_id=job['id'])['params']
        fixture.ns['load_request_manifest'] = load_request_manifest
        job_lifecycle._reset_queue_state_for_tests()
        job_lifecycle.configure_durability_hook(coordinator.prospective_transition)
    job['status'] = 'queued'  # Synthetic requeue after owner authorization.
    job.pop('_tool_inputs_authorized_live', None)
    fixture.ns['_jobs'] = {job['id']: job}

    def barrier():
        connection.send(boundary if durable else 'sealed-pair' if boundary == 'completion' else 'sidecar-only')
        # Block on a pipe, not a timer. The parent kills this exact process.
        connection.recv()
        raise AssertionError('Crash barrier unexpectedly released')

    def start(current, **kw):
        if durable:
            return job_lifecycle.try_start(current, **kw)
        current.update(status='running')
        commit(journal, current)
        return True

    def finish(current, status, **kw):
        if durable and status == 'completed':
            if boundary != 'adoption-ready':
                job_lifecycle.request_cancel(current)
            barrier()
        if durable:
            return job_lifecycle.finish_job(current, status, **kw)
        if boundary == 'completion' and status == 'completed':
            barrier()
        current.update(status=status, **kw)
        commit(journal, current)
        return True

    def checkpoint(current, **kw):
        if durable:
            return job_lifecycle.checkpoint_recovery_job(current, **kw)
        current.update(**kw)
        commit(journal, current)
        return True

    def encode(source, destination, **kw):
        with open(counter, 'a', encoding='utf-8') as handle:
            handle.write('encode\n')
            handle.flush()
            os.fsync(handle.fileno())
        return horizontal_flip(source, destination, **kw)

    def publish(source, destination):
        if boundary == 'cancel-media' and str(destination).endswith('.mp4'):
            job_lifecycle.request_cancel(job)
            barrier()
        if boundary == 'media' and str(destination).endswith('.mp4'):
            barrier()
        return publish_file_no_replace(source, destination)

    fixture.ns.update(try_start=start, finish_job=finish, _queue_recovery_checkpoint=checkpoint)
    with patch('services.video_transform.horizontal_flip', encode), \
            patch('services.atomic_file_publish.publish_file_no_replace', publish):
        result = fixture.ns['_run_tool_hflip'](job['id'])
    if connection:
        connection.send({'result': result, 'status': job['status']})


@unittest.skipUnless('fork' in multiprocessing.get_all_start_methods()
                     and shutil.which('ffmpeg'), 'POSIX fork and FFmpeg required')
class ToolProcessCrashTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tool_fixture.ToolInputExecutionTests(
            'test_exact_project_and_upload_inputs_work_after_restore')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.context = multiprocessing.get_context('fork')
        self.journal_path = self.fixture.root / 'queue-recovery.jsonl'
        self.counter = self.fixture.root / 'encode-count.txt'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-y',
            '-f', 'lavfi', '-i',
            'color=red:s=128x64:r=12:d=1,drawbox=x=64:y=0:w=64:h=64:color=blue:t=fill',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=32000:duration=1',
            '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-shortest', str(self.fixture.video),
        ], check=True, timeout=15, capture_output=True)
        self.finishing_history = {'version': 1, 'steps': [
            {'step': 'upscale', 'outcome': 'applied', 'method': 'lanczos2'},
            {'step': 'voice_clone', 'outcome': 'not_applied'},
        ]}
        self.fixture.video.with_suffix('.meta.json').write_text(json.dumps({
            'workspace': 'project-a', 'private': True, 'postprocessing': self.finishing_history,
        }))
        self.job = self.fixture.job('tool_hflip')
        commit(QueueRecoveryJournal(self.journal_path), self.job)
        self.source_hash = self.digest(self.fixture.video)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def snapshot(self, path):
        stat = path.stat()
        return self.digest(path), stat.st_ino, stat.st_size, stat.st_mtime_ns

    def persisted(self):
        return QueueRecoveryJournal(self.journal_path).recover().jobs[self.job['id']]

    def encodes(self):
        return self.counter.read_text().splitlines()

    def spawn(self, boundary=None):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=run_worker, args=(
            self.fixture, self.journal_path, self.counter, boundary, child))
        process.start()
        child.close()

        def cleanup():
            if process.is_alive():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    process.kill()
            process.join(timeout=5)
            parent.close()
            process.close()
        self.addCleanup(cleanup)
        return process, parent

    def crash(self, boundary):
        process, connection = self.spawn(boundary)
        self.assertTrue(connection.poll(20), 'Worker did not reach exact publication barrier')
        self.assertEqual(connection.recv(), 'sealed-pair' if boundary == 'completion' else 'sidecar-only')
        self.assertEqual(self.persisted()['status'], 'running')
        self.assertEqual(self.persisted()['output_files'], [])
        process.kill()
        process.join(timeout=5)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        self.assertEqual(self.encodes(), ['encode'])

    def resume(self, expected_status='completed'):
        process, connection = self.spawn()
        self.assertTrue(connection.poll(20), 'Recovery worker did not finish')
        self.assertEqual(connection.recv(), {
            'result': expected_status == 'completed', 'status': expected_status})
        process.join(timeout=5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.persisted()['status'], expected_status)
        self.assertEqual(self.digest(self.fixture.video), self.source_hash)

    def output(self):
        outputs = list(self.fixture.project.glob('*_hflip_*.mp4'))
        self.assertEqual(len(outputs), 1)
        return outputs[0]

    def assert_media(self, output):
        def decode(path, args):
            return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                                   *args, '-'], check=True, capture_output=True, timeout=15).stdout
        source = decode(self.fixture.video, ['-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo'])
        flipped = decode(output, ['-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo'])
        self.assertEqual(len(source), 12 * 128 * 64 * 3)
        self.assertEqual(len(flipped), len(source))
        # Check every decoded pixel against the horizontally reversed original.
        expected = bytearray()
        for row in range(0, len(source), 128 * 3):
            for x in reversed(range(128)):
                expected.extend(source[row+x*3:row+x*3+3])
        self.assertLess(sum(abs(a-b) for a,b in zip(expected, flipped))/len(flipped), 3)
        audio_args = ['-vn', '-c:a', 'pcm_s16le', '-f', 's16le']
        self.assertEqual(decode(output, audio_args), decode(self.fixture.video, audio_args))
        meta = json.loads(output.with_suffix('.meta.json').read_text())
        self.assertTrue(meta['private'])
        self.assertEqual(meta['artifact_class'], 'final')
        self.assertEqual(meta['producer_media_sha256'], self.digest(output))
        self.assertEqual(meta['tool_source_revision'], self.job['params']['hflip_source_revision'])
        self.assertEqual(self.persisted()['output_files'], [output.name])

    def test_sigkill_after_publication_adopts_pair_without_reencoding(self):
        self.crash('completion')
        output = self.output()
        sidecar = output.with_suffix('.meta.json')
        before = self.snapshot(output), self.snapshot(sidecar)
        self.assertEqual(json.loads(sidecar.read_text())['postprocessing'], self.finishing_history)
        self.resume()
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), before)
        self.assertEqual(json.loads(sidecar.read_text())['postprocessing'], self.finishing_history)
        self.assert_media(output)

    def test_sigkill_after_sidecar_reencodes_once_and_preserves_foreign_marker(self):
        self.crash('media')
        self.assertEqual(list(self.fixture.project.glob('*_hflip_*.mp4')), [])
        self.assertEqual(len(list(self.fixture.project.glob('*_hflip_*.meta.json'))), 1)
        foreign = self.fixture.project / ('foreign_' + self.job['id'] + '.meta.json')
        foreign.write_text(json.dumps({'job_id': 'another-job', 'private': True}))
        foreign_before = self.snapshot(foreign)
        self.resume()
        self.assertEqual(self.encodes(), ['encode', 'encode'])
        self.assertEqual(self.snapshot(foreign), foreign_before)
        self.assert_media(self.output())

    def test_sigkill_then_changed_source_rejects_adoption_preserving_pair(self):
        self.crash('completion')
        output = self.output()
        sidecar = output.with_suffix('.meta.json')
        before = self.snapshot(output), self.snapshot(sidecar)
        source_sidecar = self.fixture.video.with_suffix('.meta.json')
        timestamp = source_sidecar.stat().st_mtime_ns + 100_000
        os.utime(source_sidecar, ns=(timestamp, timestamp))
        self.resume('failed')
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual(self.persisted()['output_files'], [])
        self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), before)

    def durable_registration(self):
        project_digest = project_instance_digest(b'tool-test-secret-value', 'a' * 32)
        self.job['_recovery_project_digest'] = project_digest
        self.fixture.ns['_queue_recovery_existing_project_identity'] = lambda _: project_digest
        pointer = atomic_write_request_manifest(self.fixture.project, job_id=self.job['id'],
            params=self.job['params'], inputs=self.fixture.manifests[self.job['id']]['inputs'])
        self.job['_recovery_manifest_pointer'] = pointer
        # Use a separate fresh durable registration, not the legacy raw journal fixture.
        self.journal_path = self.fixture.root / 'registered-queue.jsonl'
        QueueRecoveryCoordinator(QueueRecoveryJournal(self.journal_path)).register_job(
            self.job, owner_digest=self.fixture.owner, project_digest=project_digest, request_manifest=pointer)
        staging = self.fixture.project / '.maestro-recovery' / 'staging'
        staging.mkdir(parents=True, mode=0o700)
        self.staged = staging / f"unit-{self.job['id']}-pending.bin"
        self.staged.write_bytes(b'retained staging')
        self.manifest = self.fixture.project / pointer['path']

    def crash_durable(self, boundary):
        process, connection = self.spawn(boundary)
        self.assertTrue(connection.poll(20), 'Worker did not reach durable cancellation barrier')
        self.assertEqual(connection.recv(), boundary)
        snapshot = self.persisted()
        self.assertEqual(snapshot['status'], 'running' if boundary == 'adoption-ready' else 'cancelled')
        self.assertIn('processed_tool_publication', snapshot['recovery_cursor'])
        process.kill()
        process.join(timeout=5)
        self.assertEqual(process.exitcode, -signal.SIGKILL)

    def startup(self, *, restore_other=False, expect_params=True):
        class Registry(dict):
            def prepare(self, job): return job
            def publish_prepared(self, job_id, job): self[job_id] = job
        coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.journal_path))
        coordinator.restore()
        projects = {'project-a': (str(self.fixture.project), self.job['_recovery_project_digest'])}
        if restore_other:
            other = self.fixture.root / 'project-unrelated'
            other.mkdir()
            pointer = atomic_write_request_manifest(other, job_id='unrelated-tool',
                params={'model_type': 'post_processing'}, inputs=[])
            coordinator.register_job({'id': 'unrelated-tool', 'kind': 'tool_upscale',
                'status': 'cancelled', 'workspace': 'project-unrelated'},
                owner_digest=self.fixture.owner, project_digest=self.job['_recovery_project_digest'], request_manifest=pointer)
            projects['project-unrelated'] = (str(other), self.job['_recovery_project_digest'])
        restored = coordinator.restore()
        ns = self.fixture.ns
        job_lifecycle._reset_queue_state_for_tests()
        self.addCleanup(job_lifecycle._reset_queue_state_for_tests)
        job_lifecycle.configure_durability_hook(coordinator.prospective_transition)
        workers = []
        ns.update(_queue_recovery_coordinator=coordinator, _queue_recovery_restored=restored,
                  _queue_recovery_workers_started=False, _jobs=(ns.get('_JobRegistry') or Registry)(),
                  _queue_recovery_existing_projects=lambda: projects,
                  _queue_recovery_checkpoint=job_lifecycle.checkpoint_recovery_job,
                  load_request_manifest=load_request_manifest, validate_manifest_inputs=validate_manifest_inputs,
                  AUTOMATIC_RETIREMENT_STATUSES=AUTOMATIC_RETIREMENT_STATUSES,
                  _CREDIT_CLEANUP_PARAM='credit_cleanup', restore_scheduler_state=lambda *_: None,
                  cleanup_orphan_request_manifests=cleanup_orphan_request_manifests,
                  cleanup_orphan_staged_outputs=cleanup_orphan_staged_outputs,
                  _queue_recovery_worker=lambda job: workers.append(job['id']),
                  _queue_recovery_delivery_pending=lambda _: None,
                  _require_job_runtime_model_admission=lambda _: None,
                  Mapping=dict)
        tool_fixture.load(ns, '_h3_cow_manual_source_supported', '_require_h3_offload_plan_parity',
                          '_queue_recovery_materialize_job', '_restore_h3_prompt_rewriter_cleanup',
                          '_restore_queue_recovery_on_startup')
        self.assertTrue(ns['_restore_queue_recovery_on_startup']())
        self.assertEqual(workers, [])
        self.assertEqual(ns['_jobs'][self.job['id']]['status'], 'cancelled')
        self.assertEqual(ns['_jobs'][self.job['id']]['params'], self.job['params'] if expect_params else {})
        if restore_other:
            self.assertEqual(ns['_jobs']['unrelated-tool']['status'], 'cancelled')
            self.assertEqual(ns['_jobs']['unrelated-tool']['params'], {'model_type': 'post_processing'})
        return coordinator, ns['_jobs'][self.job['id']]

    def assert_cancel_retraction_without_consumed_inputs(self, boundary):
        self.durable_registration()
        self.crash_durable(boundary)
        reference_hash = self.digest(self.fixture.voice)
        self.fixture.video.unlink()
        coordinator, job = self.startup()
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertNotIn('processed_tool_publication', job['recovery_cursor'])
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.staged.exists())
        self.assertEqual(list(self.fixture.project.glob('*_hflip_*')), [])
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual(self.digest(self.fixture.voice), reference_hash)

    def test_durable_cancel_sigkill_retracts_pair_without_consumed_inputs(self):
        self.assert_cancel_retraction_without_consumed_inputs('cancel-completion')

    def test_durable_cancel_sigkill_retracts_sidecar_without_consumed_inputs(self):
        self.assert_cancel_retraction_without_consumed_inputs('cancel-media')

    def test_adoption_cancel_sigkill_remains_cancelled_and_retracts_without_reencoding(self):
        self.durable_registration()
        self.crash_durable('adoption-ready')
        self.crash_durable('cancel-adoption')
        self.startup()
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual(list(self.fixture.project.glob('*_hflip_*')), [])
        self.assertEqual(self.digest(self.fixture.video), self.source_hash)

    def test_partial_cancel_cleanup_retains_intent_manifest_staging_then_second_startup_settles(self):
        self.durable_registration()
        self.crash_durable('cancel-completion')
        output = self.output()
        remove = os.remove
        def interrupt(path):
            remove(path)
            if str(path) == str(output): raise OSError('interrupted after media unlink')
        with patch('os.remove', interrupt):
            coordinator, job = self.startup()
        self.assertFalse(output.exists())
        self.assertTrue(output.with_suffix('.meta.json').exists())
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.staged.exists())
        self.assertIn(self.job['id'], coordinator.compact().jobs)
        coordinator, _ = self.startup()
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertFalse(output.with_suffix('.meta.json').exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.staged.exists())
        self.assertEqual(self.encodes(), ['encode'])

    def test_changed_cancelled_member_is_preserved_with_durable_cleanup_hold(self):
        self.durable_registration()
        self.crash_durable('cancel-completion')
        output = self.output()
        output.write_bytes(b'foreign replacement')
        before = self.snapshot(output), self.snapshot(output.with_suffix('.meta.json'))
        coordinator, job = self.startup()
        self.assertTrue(processed_tool_publication_pending(coordinator.restore().jobs[self.job['id']]))
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertEqual((self.snapshot(output), self.snapshot(output.with_suffix('.meta.json'))), before)
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.staged.exists())
        self.assertEqual(self.encodes(), ['encode'])

    def assert_workspace_unavailable_keeps_cleanup_hold_and_restores_unrelated(self, *, deleting):
        self.durable_registration()
        self.crash_durable('cancel-completion')
        output = self.output()
        names = [output.relative_to(self.fixture.project), output.with_suffix('.meta.json').relative_to(self.fixture.project),
                 self.manifest.relative_to(self.fixture.project), self.staged.relative_to(self.fixture.project),
                 self.fixture.video.relative_to(self.fixture.project)]
        before = [self.snapshot(self.fixture.project / name) for name in names]
        ns = self.fixture.ns
        ns.update(wgp=types.SimpleNamespace(server_config={'save_path': str(self.fixture.root)}),
                  _workspace_lifecycle_lock=threading.RLock(), _workspace_operations={},
                  threading=threading, _request_remote=ContextVar('remote', default=False),
                  _request_session_id=ContextVar('session', default=None),
                  _workspaces_deleting={'project-a'} if deleting else set(), copy=copy,
                  discover_request_manifest_pointers=discover_request_manifest_pointers)
        tool_fixture.load(ns, '_JobRegistry', '_require_job_workspace_available',
                          '_existing_workspace_dir', '_require_workspace_not_deleting',
                          '_WorkspaceOperationReservation', '_reserve_workspace_operations',
                          '_local_h3_discovery_owner_digest', '_local_h3_recovery_candidates',
                          '_preserve_local_h3_recovery_evidence', '_h3_dependency_closed_recovery_prefix',
                          '_h3_incomplete_recovery_prefix')
        root = self.fixture.project
        if not deleting:
            root = self.fixture.project.with_name('retained-project')
            self.fixture.project.rename(root)
        coordinator, job = self.startup(restore_other=True, expect_params=deleting)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertTrue(job['queue_held'])
        self.assertNotIn('out_dir', job)
        durable = coordinator.restore().jobs[self.job['id']]
        self.assertTrue(processed_tool_publication_pending(durable))
        self.assertEqual(durable['recovery_state'], 'cleanup_blocked')
        self.assertEqual([self.snapshot(root / name) for name in names], before)
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual(ns['_workspace_operations'], {})

    def test_missing_workspace_cleanup_holds_and_actual_startup_restores_unrelated_job(self):
        self.assert_workspace_unavailable_keeps_cleanup_hold_and_restores_unrelated(deleting=False)

    def test_deleting_workspace_cleanup_holds_and_actual_startup_restores_unrelated_job(self):
        self.assert_workspace_unavailable_keeps_cleanup_hold_and_restores_unrelated(deleting=True)

    def test_cancel_cleanup_directory_sync_failure_keeps_absent_pair_intent_until_next_startup(self):
        self.durable_registration()
        self.crash_durable('cancel-completion')
        with patch('services.queue_recovery_runtime._fsync_directory', side_effect=OSError('directory sync failed')):
            coordinator, job = self.startup()
        self.assertEqual(list(self.fixture.project.glob('*_hflip_*')), [])
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertTrue(processed_tool_publication_pending(coordinator.restore().jobs[self.job['id']]))
        self.assertTrue(self.manifest.exists())
        self.assertTrue(self.staged.exists())
        coordinator, _ = self.startup()
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertEqual(self.encodes(), ['encode'])


def run_editor_worker(fixture, journal_path, counter, boundary, connection):
    os.setsid()
    coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(journal_path))
    snapshot = next(iter(coordinator.restore().jobs.values()))
    job = dict(snapshot, session_id=None, access_policy={key: snapshot[key] for key in ('private', 'explicit')},
               _recovery_owner_digest=snapshot['owner_principal'], _recovery_project_digest=snapshot['project_instance'],
               _recovery_manifest_pointer=snapshot['request_manifest'])
    job['params'] = load_request_manifest(fixture.project, snapshot['request_manifest'], expected_job_id=job['id'])['params']
    job['status'] = 'queued'
    ns = fixture.ns
    ns['_jobs'] = {job['id']: job}
    job_lifecycle._reset_queue_state_for_tests()
    job_lifecycle.configure_durability_hook(coordinator.prospective_transition)
    ns.update(try_start=job_lifecycle.try_start, _queue_recovery_checkpoint=job_lifecycle.checkpoint_recovery_job,
              is_cancel_requested=job_lifecycle.is_cancel_requested)
    def barrier():
        if boundary == 'cancel-completion':
            job_lifecycle.request_cancel(job)
        connection.send(boundary)
        connection.recv()
        raise AssertionError('Owned crash barrier unexpectedly released')
    def finish(current, status, **updates):
        if boundary in {'completion', 'cancel-completion'} and status == 'completed':
            barrier()
        if boundary == 'completion-error' and status == 'completed':
            raise editor_fixture.QueueRecoveryAdapterError('synthetic completion persistence failure')
        return job_lifecycle.finish_job(current, status, **updates)
    ns['finish_job'] = finish
    from services.editor_export import render_single_source_cut
    def encode(*args, **kwargs):
        with open(counter, 'a') as handle:
            handle.write('encode\n'); handle.flush(); os.fsync(handle.fileno())
        return render_single_source_cut(*args, **kwargs)
    def publish(source, destination):
        if boundary == 'media' and str(destination).endswith('.mp4'):
            barrier()
        return publish_file_no_replace(source, destination)
    with patch('services.editor_export.render_single_source_cut', encode), \
            patch('services.atomic_file_publish.publish_file_no_replace', publish):
        result = ns['_run_tool_editor_export'](job['id'])
    connection.send({'result': result, 'status': job['status']})


@unittest.skipUnless('fork' in multiprocessing.get_all_start_methods() and shutil.which('ffmpeg'),
                     'POSIX fork and FFmpeg required')
class EditorExportProcessCrashTests(unittest.TestCase):
    def setUp(self):
        from services.editor_projects import create_output_video_timeline, save_editor_project, probe_media
        self.fixture = editor_fixture.EditorExportRouteTests('test_worker_publishes_a_private_final_copy_with_cut_provenance')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=s=128x72:r=24:d=1', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=32000:duration=1',
            '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
            str(self.fixture.source)], check=True, capture_output=True, timeout=15)
        self.fixture.timeline = save_editor_project(str(self.fixture.outputs), 'scene',
            create_output_video_timeline(workspace='scene', output_name='source.mp4',
                output_revision=self.fixture.source_revision(), media=probe_media(str(self.fixture.source))), expected_revision=0)
        self.job = self.fixture.worker_namespace()
        self.owner = 'owner:v1:' + 'a' * 64
        self.project_digest = 'project:v1:' + 'b' * 64
        self.fixture.ns['_queue_recovery_existing_project_identity'] = lambda _: self.project_digest
        self.job.update(_recovery_owner_digest=self.owner, _recovery_project_digest=self.project_digest)
        self.journal_path = self.fixture.root / 'registered-editor.jsonl'
        QueueRecoveryCoordinator(QueueRecoveryJournal(self.journal_path)).register_job(self.job,
            owner_digest=self.owner, project_digest=self.project_digest, request_manifest=self.job['_recovery_manifest_pointer'])
        self.manifest = self.fixture.project / self.job['_recovery_manifest_pointer']['path']
        from services.queue_recovery_runtime import ensure_recovery_staging_directory
        self.staged = Path(ensure_recovery_staging_directory(self.fixture.project)) / f"unit-{self.job['id']}-pending.bin"
        self.staged.write_bytes(b'retained private staging')
        self.source_seal = self.snapshot(self.fixture.source), self.snapshot(self.fixture.source.with_suffix('.meta.json'))
        self.counter = self.fixture.root / 'editor-encode-count'
        self.context = multiprocessing.get_context('fork')

    @staticmethod
    def snapshot(path):
        info = path.stat()
        return hashlib.sha256(path.read_bytes()).hexdigest(), info.st_ino, info.st_size, info.st_mtime_ns

    def output(self):
        return self.fixture.project / f"editor_cut_{self.job['id']}.mp4"

    def persisted(self):
        return QueueRecoveryCoordinator(QueueRecoveryJournal(self.journal_path)).restore().jobs[self.job['id']]

    def spawn(self, boundary=None):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=run_editor_worker,
            args=(self.fixture, self.journal_path, self.counter, boundary, child))
        process.start(); child.close()
        def cleanup():
            if process.is_alive():
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: process.kill()
            process.join(5); parent.close(); process.close()
        self.addCleanup(cleanup)
        return process, parent

    def crash(self, boundary):
        process, connection = self.spawn(boundary)
        self.assertTrue(connection.poll(20), 'Editor worker did not reach publication boundary')
        self.assertEqual(connection.recv(), boundary)
        self.assertIn('editor_export_publication', self.persisted()['recovery_cursor'])
        self.assertEqual(self.persisted()['status'], 'cancelled' if boundary == 'cancel-completion' else 'running')
        process.kill(); process.join(5)
        self.assertEqual(process.exitcode, -signal.SIGKILL)

    def resume(self, status='completed'):
        process, connection = self.spawn()
        self.assertTrue(connection.poll(20), 'Editor recovery did not finish')
        self.assertEqual(connection.recv(), {'result': status == 'completed', 'status': status})
        process.join(5); self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.persisted()['status'], status)

    def startup(self):
        class Registry(dict):
            def prepare(self, job): return job
            def publish_prepared(self, job_id, job): self[job_id] = job
        coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(self.journal_path))
        ns = self.fixture.ns
        job_lifecycle._reset_queue_state_for_tests()
        self.addCleanup(job_lifecycle._reset_queue_state_for_tests)
        job_lifecycle.configure_durability_hook(coordinator.prospective_transition)
        workers = []
        ns.update(_queue_recovery_coordinator=coordinator, _queue_recovery_restored=coordinator.restore(),
            _queue_recovery_workers_started=False, _jobs=Registry(),
            _queue_recovery_existing_projects=lambda: {'scene': (str(self.fixture.project), self.project_digest)},
            _queue_recovery_checkpoint=job_lifecycle.checkpoint_recovery_job,
            AUTOMATIC_RETIREMENT_STATUSES=AUTOMATIC_RETIREMENT_STATUSES,
            _CREDIT_CLEANUP_PARAM='credit_cleanup', restore_scheduler_state=lambda *_: None,
            cleanup_orphan_request_manifests=cleanup_orphan_request_manifests,
            cleanup_orphan_staged_outputs=cleanup_orphan_staged_outputs,
            _queue_recovery_worker=lambda job: workers.append(job['id']),
            _queue_recovery_delivery_pending=lambda _: None, _require_job_runtime_model_admission=lambda _: None,
            Mapping=dict)
        tool_fixture.load(ns, '_h3_cow_manual_source_supported', '_require_h3_offload_plan_parity',
            '_queue_recovery_materialize_job', '_restore_h3_prompt_rewriter_cleanup',
            '_restore_queue_recovery_on_startup', '_cleanup_cancelled_processed_tool_output')
        self.assertTrue(ns['_restore_queue_recovery_on_startup']())
        self.assertEqual(workers, [])
        return coordinator, ns['_jobs'][self.job['id']]

    def failed_pair(self):
        process, connection = self.spawn('completion-error')
        self.assertTrue(connection.poll(20))
        self.assertEqual(connection.recv(), {'result': False, 'status': 'failed'})
        process.join(5); self.assertEqual(process.exitcode, 0)
        self.assertTrue(self.output().exists()); self.assertTrue(self.output().with_suffix('.meta.json').exists())
        self.assertEqual(self.persisted()['execution_attempt'], 1)
        return self.startup()[1]

    def native_retry(self, job, *, on_dispatch=None):
        from services.queue_recovery_runtime import next_recovery_attempt, MAX_RECOVERY_ATTEMPTS
        from fastapi import Response
        ns = self.fixture.ns
        scheduled = []
        class DeferredThread:
            def __init__(self, *, target, args, daemon, name):
                self.target, self.args = target, args
            def start(self):
                scheduled.append((self.target, self.args))
                if on_dispatch is not None: on_dispatch()
        ns.update(_queue_recovery_checkpoint_lock=threading.RLock(),
            validate_manifest_inputs=validate_manifest_inputs,
            try_start=job_lifecycle.try_start, finish_job=job_lifecycle.finish_job,
            is_cancel_requested=job_lifecycle.is_cancel_requested,
            _require_owned_job=lambda job_id, request: ns['_jobs'][job_id],
            _require_project_access=lambda request, workspace, *, permission: str(self.fixture.project),
            owner_principal_digest=lambda secret, session: self.owner, _session_secret=lambda: b'test',
            _queue_recovery_worker=lambda job: ns['_run_tool_editor_export'],
            _queue_recovery_delivery_pending=lambda _: None, _require_job_runtime_model_admission=lambda _: None,
            _h3_native_boundary_exact_retry_allowed=lambda _: False,
            _QUEUE_RECOVERY_REASON_TEXT={'generation_failed':'Generation failed'},
            _BLOCKED_QUEUE_RECOVERY_STATES={'blocked','blocked_remote_reauth','blocked_preparation'},
            _h3_ordinary_oom_hold=lambda _: False,
            next_recovery_attempt=next_recovery_attempt, MAX_RECOVERY_ATTEMPTS=MAX_RECOVERY_ATTEMPTS,
            retry_failed_recovery_job=job_lifecycle.retry_failed_recovery_job,
            update_queue_job=job_lifecycle.update_queue_job,
            threading=types.SimpleNamespace(Thread=DeferredThread))
        editor_fixture.load_functions(ns, '_queue_recovery_revalidate_job', '_queue_recovery_reason_code',
            '_queue_recovery_is_blocked', '_queue_recovery_attempt', '_resume_recovered_job',
            '_set_recovery_no_store', 'retry_recovered_job')
        request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_session_id='owner-session'))
        result = ns['retry_recovered_job'](job['id'], request, Response())
        return result, scheduled, request

    def test_non_cancelled_actual_startup_preserves_foreign_publication_members(self):
        self.crash('completion')
        output = self.output(); sidecar = output.with_suffix('.meta.json')
        for member in (output, sidecar):
            original = member.read_bytes()
            member.write_bytes(b'foreign' + original)
            before = self.snapshot(output), self.snapshot(sidecar)
            coordinator, job = self.startup()
            self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), before)
            self.assertTrue(job['queue_held']); self.assertEqual(job['recovery_state'], 'blocked')
            self.assertTrue(self.manifest.exists()); self.assertTrue(self.staged.exists())
            self.assertIn('editor_export_publication', coordinator.restore().jobs[job['id']]['recovery_cursor'])
            member.write_bytes(original)
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_native_retry_atomically_admits_completion_only_and_adopts_without_encode(self):
        job = self.failed_pair()
        original_intent = copy.deepcopy(job['recovery_cursor']['editor_export_publication'])
        before = self.snapshot(self.output()), self.snapshot(self.output().with_suffix('.meta.json'))
        result, scheduled, request = self.native_retry(job)
        self.assertEqual(result['status'], 'queued'); self.assertEqual(len(scheduled), 1)
        durable = self.persisted()
        self.assertEqual(durable['execution_attempt'], 2)
        self.assertEqual(durable['recovery_cursor']['editor_export_publication'], original_intent)
        self.assertEqual(durable['recovery_cursor']['editor_export_completion_retry'], {
            'producing_execution_attempt':1,'execution_attempt':2,'producer_unit_id':original_intent['producer_unit_id']})
        with self.assertRaises(editor_fixture.HTTPException) as duplicate:
            self.fixture.ns['retry_recovered_job'](job['id'], request, types.SimpleNamespace(headers={}))
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(len(scheduled), 1)
        worker, args = scheduled[0]
        self.assertTrue(worker(*args))
        self.assertEqual(self.persisted()['status'], 'completed')
        self.assertEqual(self.persisted()['execution_attempt'], 2)
        self.assertEqual((self.snapshot(self.output()), self.snapshot(self.output().with_suffix('.meta.json'))), before)
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_native_completion_retry_cancellation_cleans_original_attempt_without_inputs(self):
        job = self.failed_pair()
        _result, scheduled, _request = self.native_retry(job)
        self.assertTrue(job_lifecycle.request_cancel(job).changed)
        self.fixture.source.unlink()
        coordinator, restored = self.startup()
        self.assertFalse(self.output().exists()); self.assertFalse(self.output().with_suffix('.meta.json').exists())
        self.assertEqual(restored['status'], 'cancelled'); self.assertFalse(restored['queue_held'])
        self.assertNotIn(job['id'], coordinator.restore().jobs)
        self.assertFalse(self.manifest.exists())
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_completion_only_pair_becoming_partial_cannot_reencode_or_retract_survivor(self):
        job = self.failed_pair()
        _result, scheduled, _request = self.native_retry(job)
        self.output().unlink()
        sidecar = self.output().with_suffix('.meta.json'); before = self.snapshot(sidecar)
        worker, args = scheduled[0]
        self.assertFalse(worker(*args))
        self.assertEqual(self.snapshot(sidecar), before)
        self.assertEqual(self.persisted()['status'], 'failed')
        self.assertEqual(self.persisted()['recovery_state'], 'blocked')
        self.assertIn('editor_export_completion_retry', self.persisted()['recovery_cursor'])
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_cancel_at_native_retry_dispatch_is_final_and_retracts_bound_pair(self):
        job = self.failed_pair()
        def cancel():
            self.assertTrue(job_lifecycle.request_cancel(job).changed)
        _result, scheduled, _request = self.native_retry(job, on_dispatch=cancel)
        self.assertEqual(self.persisted()['status'], 'cancelled')
        self.assertEqual(self.persisted()['execution_attempt'], 2)
        self.assertEqual(len(scheduled), 1)
        self.fixture.ns.update(generation_slot=job_lifecycle.generation_slot, _gen_lock=threading.Lock())
        worker, args = scheduled[0]
        self.assertFalse(worker(*args))
        self.assertEqual(self.persisted()['status'], 'cancelled')
        self.assertNotIn('editor_export_publication', self.persisted()['recovery_cursor'])
        self.assertFalse(self.output().exists()); self.assertFalse(self.output().with_suffix('.meta.json').exists())
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_editor_sigkill_pair_adopts_exact_bytes_without_second_encode(self):
        self.crash('completion')
        output = self.output(); sidecar = output.with_suffix('.meta.json')
        seals = self.snapshot(output), self.snapshot(sidecar)
        self.resume()
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])
        self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), seals)
        metadata = json.loads(sidecar.read_bytes())
        self.assertTrue(metadata['private']); self.assertTrue(metadata['explicit'])
        self.assertEqual(metadata['producer_media_sha256'], seals[0][0])
        self.assertEqual(metadata['transform']['editor_project_id'], self.fixture.timeline['id'])
        self.assertEqual(self.persisted()['output_files'], [output.name])
        self.assertEqual(len(list(self.fixture.project.glob('editor_cut_*.mp4'))), 1)
        self.assertEqual((self.snapshot(self.fixture.source), self.snapshot(self.fixture.source.with_suffix('.meta.json'))), self.source_seal)

    def test_editor_sigkill_sidecar_retracts_orphan_then_encodes_once(self):
        self.crash('media')
        self.assertFalse(self.output().exists()); self.assertTrue(self.output().with_suffix('.meta.json').exists())
        self.resume()
        self.assertEqual(self.counter.read_text().splitlines(), ['encode', 'encode'])
        self.assertEqual(len(list(self.fixture.project.glob('editor_cut_*.mp4'))), 1)
        self.assertEqual((self.snapshot(self.fixture.source), self.snapshot(self.fixture.source.with_suffix('.meta.json'))), self.source_seal)

    def test_editor_pair_source_change_prevents_adoption_and_encode(self):
        self.crash('completion')
        self.fixture.source.write_bytes(b'changed input')
        self.resume('failed')
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])
        self.assertEqual(self.persisted()['output_files'], [])
        self.assertTrue(self.output().exists())

    def test_editor_replaced_identical_output_is_preserved_and_never_adopted(self):
        self.crash('completion')
        output = self.output(); sidecar = output.with_suffix('.meta.json')
        original = output.read_bytes()
        replacement = output.with_suffix('.foreign')
        replacement.write_bytes(original); os.replace(replacement, output)
        foreign = self.snapshot(output), self.snapshot(sidecar)
        self.resume('failed')
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])
        self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), foreign)
        self.assertEqual(self.persisted()['output_files'], [])
        self.assertEqual(self.persisted()['recovery_state'], 'blocked')

    def test_cancelled_editor_pair_cleanup_after_restart_needs_no_consumed_input(self):
        self.crash('cancel-completion')
        self.fixture.source.unlink()
        coordinator, job = self.startup()
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertNotIn('editor_export_publication', job['recovery_cursor'])
        self.assertEqual(job['status'], 'cancelled'); self.assertFalse(job['queue_held'])
        self.assertFalse(self.output().exists()); self.assertFalse(self.output().with_suffix('.meta.json').exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.staged.exists())
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_cancelled_editor_partial_unlink_or_foreign_member_holds_until_exact_cleanup(self):
        self.crash('cancel-completion')
        sidecar = self.output().with_suffix('.meta.json')
        original = sidecar.read_bytes()
        sidecar.write_bytes(b'foreign metadata')
        coordinator, job = self.startup()
        self.assertEqual(sidecar.read_bytes(), b'foreign metadata'); self.assertTrue(self.output().exists())
        self.assertEqual(job['recovery_state'], 'cleanup_blocked'); self.assertTrue(self.manifest.exists())
        self.assertTrue(self.staged.exists())
        self.assertTrue(processed_tool_publication_pending(coordinator.restore().jobs[self.job['id']]))
        sidecar.write_bytes(original)
        remove = os.remove
        def interrupted(path, *args, **kwargs):
            if str(path) == str(sidecar): raise OSError('synthetic interrupted second unlink')
            return remove(path, *args, **kwargs)
        with patch('os.remove', interrupted):
            coordinator, job = self.startup()
        self.assertFalse(self.output().exists()); self.assertTrue(sidecar.exists())
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        coordinator, job = self.startup()
        self.assertFalse(sidecar.exists()); self.assertFalse(job['queue_held'])
        self.assertEqual(job['recovery_state'], 'cancelled'); self.assertIsNone(job.get('_recovery_reason_code'))
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertEqual(self.counter.read_text().splitlines(), ['encode'])

    def test_cancelled_editor_posthash_same_inode_mutation_is_not_deleted(self):
        self.crash('cancel-completion')
        output = self.output(); original = output.read_bytes(); before = output.stat()
        hash_file = self.fixture.ns['_recovery_sha256_file']
        def mutate(path, **kwargs):
            result = hash_file(path, **kwargs)
            if str(path) == str(output):
                output.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                os.utime(output, ns=(before.st_atime_ns, before.st_mtime_ns))
            return result
        with patch.dict(self.fixture.ns, _recovery_sha256_file=mutate):
            coordinator, job = self.startup()
        self.assertEqual(output.stat().st_ino, before.st_ino)
        self.assertEqual(output.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertNotEqual(output.read_bytes(), original)
        self.assertTrue(output.with_suffix('.meta.json').exists())
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertTrue(processed_tool_publication_pending(coordinator.restore().jobs[self.job['id']]))
        self.assertTrue(self.manifest.exists()); self.assertTrue(self.staged.exists())

    def test_cancelled_editor_directory_sync_failure_retains_intent_until_second_startup(self):
        self.crash('cancel-completion')
        self.fixture.source.unlink()
        from services.queue_recovery_runtime import _fsync_directory
        def fail_sync(path):
            if str(path) == str(self.fixture.project): raise OSError('synthetic directory sync failure')
            return _fsync_directory(path)
        with patch('services.queue_recovery_runtime._fsync_directory', fail_sync):
            coordinator, job = self.startup()
        self.assertFalse(self.output().exists()); self.assertFalse(self.output().with_suffix('.meta.json').exists())
        self.assertTrue(processed_tool_publication_pending(coordinator.restore().jobs[self.job['id']]))
        self.assertEqual(job['recovery_state'], 'cleanup_blocked')
        self.assertTrue(self.manifest.exists()); self.assertTrue(self.staged.exists())
        coordinator, job = self.startup()
        self.assertNotIn(self.job['id'], coordinator.restore().jobs)
        self.assertEqual(job['recovery_state'], 'cancelled'); self.assertFalse(job['queue_held'])
        self.assertFalse(self.manifest.exists()); self.assertFalse(self.staged.exists())


if __name__ == '__main__':
    unittest.main()
