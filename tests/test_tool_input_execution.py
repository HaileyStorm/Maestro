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
from services.queue_recovery_adapter import owner_principal_digest, QueueRecoveryAdapterError
from services.output_access import stamp_sidecar_policy

TREE = ast.parse((ROOT / 'app/launch.py').read_text())


def load(ns, *names):
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
        self.ns = dict(os=os, json=json, time=time, uuid=uuid, hmac=hmac, Request=object,
            HTTPException=HTTPException, QueueRecoveryRuntimeError=QueueRecoveryRuntimeError,
            QueueRecoveryAdapterError=QueueRecoveryAdapterError, owner_principal_digest=owner_principal_digest,
            _app_dir=str(self.root), _RECOVERABLE_INPUT_KEYS={'_tool_input_paths', 'hflip_source_path'},
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
            update_job=lambda j,**kw: j.update(**kw) or True,
            finish_job=lambda j,status,**kw: j.update(status=status,**kw) or True,
            stamp_sidecar_policy=stamp_sidecar_policy, traceback=types.SimpleNamespace(print_exc=lambda: None))
        load(self.ns, '_ToolInputChanged', '_safe_failure_updates', '_job_failure_positions', '_queue_recovery_file_values', '_queue_recovery_input_descriptors',
             '_queue_recovery_manifest_validator', '_validated_tool_input_paths',
             '_processed_tool_settings', '_resume_processed_tool_output',
             '_h3_dependency_closed_recovery_units', '_queue_recovery_units', '_queue_recovery_unit_matches', '_queue_recovery_reconcile_cursor',
             '_publish_processed_tool_output', '_write_tool_sidecar', '_queue_recovery_worker',
             '_output_revision', '_hflip_source', '_run_tool_hflip',
             '_run_tool_upscale', '_run_tool_revoice', 'tools_upscale', 'tools_revoice',
             '_request_project_workspace')

    def job(self, kind='tool_revoice', legacy=False):
        if legacy: self.video.with_suffix('.meta.json').unlink()
        paths = [str(self.video)] + ([str(self.voice)] if kind == 'tool_revoice' else [])
        if kind == 'tool_hflip':
            params = {
                'hflip_source_path': paths[0], 'hflip_source_name': self.video.name,
                'hflip_source_revision': self.ns['_output_revision'](
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

    def test_post_rename_durability_failures_remove_only_owned_output(self):
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
                self.assertEqual(list(self.project.glob('*_upscale_*')), [])
                self.assertEqual(self.video.read_bytes(), b'original')

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
        self.assertIsNone(self.ns['_resume_processed_tool_output'](job))
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
        self.assertIsNone(self.ns['_resume_processed_tool_output'](job))
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


if __name__ == '__main__': unittest.main()
