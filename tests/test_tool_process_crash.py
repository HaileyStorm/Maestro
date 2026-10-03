"""Real SIGKILL/CPU-media recovery, with synthetic app lifecycle wiring.

The worker, publication/adoption functions and journal are production code.
This does not exercise a live server restart, owner authentication or a GPU.
"""
from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import signal
import subprocess
import unittest
from unittest.mock import patch

import test_tool_input_execution as tool_fixture
from services.queue_recovery import QueueRecoveryJournal
from services.video_transform import horizontal_flip
from services.atomic_file_publish import publish_file_no_replace


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
    job['status'] = 'queued'  # Synthetic requeue after owner authorization.
    job.pop('_tool_inputs_authorized_live', None)
    fixture.ns['_jobs'] = {job['id']: job}

    def barrier():
        connection.send('sealed-pair' if boundary == 'completion' else 'sidecar-only')
        # Block on a pipe, not a timer. The parent kills this exact process.
        connection.recv()
        raise AssertionError('Crash barrier unexpectedly released')

    def start(current, **kw):
        current.update(status='running')
        commit(journal, current)
        return True

    def finish(current, status, **kw):
        if boundary == 'completion' and status == 'completed':
            barrier()
        current.update(status=status, **kw)
        commit(journal, current)
        return True

    def encode(source, destination, **kw):
        with open(counter, 'a', encoding='utf-8') as handle:
            handle.write('encode\n')
            handle.flush()
            os.fsync(handle.fileno())
        return horizontal_flip(source, destination, **kw)

    def publish(source, destination):
        if boundary == 'media' and str(destination).endswith('.mp4'):
            barrier()
        return publish_file_no_replace(source, destination)

    fixture.ns.update(try_start=start, finish_job=finish)
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


if __name__ == '__main__':
    unittest.main()
