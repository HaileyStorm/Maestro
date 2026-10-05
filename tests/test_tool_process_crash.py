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
        self.resume()
        self.assertEqual(self.encodes(), ['encode'])
        self.assertEqual((self.snapshot(output), self.snapshot(sidecar)), before)
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


if __name__ == '__main__':
    unittest.main()
