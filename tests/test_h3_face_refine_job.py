"""CPU media and model-free execution of the reviewed FaceRefine job boundary."""
import ast
import asyncio
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import torch
from services import h3_face_refine as face
from services import h3_face_refine_job as repair
from services import h3_gallery_av_guide as av
import test_tool_input_execution as tool


def request_for(source, facts):
    boxes=[[16,16,48,48] for _ in range(facts['frame_count'])]
    boxes[5:8]=[None]*3
    return dict(workspace='project-a',name=source.name,revision='reviewed',
        prompt='An adult actor, violent stage makeup, controversial theatrical scene.',
        observations=dict(shots=[0,62],boxes=boxes,canvas=[64,64],padding=1,smoothing=3),
        strength=0.5,frame_multipliers=[0 if x is None else 0.8 for x in boxes],
        audio_stream=1,settings=dict(num_inference_steps=4,seed=42,override_profile=3))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'CPU FFmpeg required')
class FaceJobMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.threads=torch.get_num_threads();torch.set_num_threads(1)
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        cls.source=cls.root/'source.mkv'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i','testsrc2=s=96x64:r=24:d=5.167',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=6','-itsoffset','0.125',
            '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=6','-map','0:v','-map','1:a',
            '-map','2:a','-frames:v','124','-c:v','ffv1','-threads','1','-c:a','pcm_s16le',str(cls.source)],
            check=True,capture_output=True,timeout=30)
        cls.facts=face._probe(cls.source,None)
        cls.body=request_for(cls.source,cls.facts)
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup();torch.set_num_threads(cls.threads)
    def prepared_request(self):
        result=repair.validate_request(self.body,self.facts)
        result['source']={**self.facts,'sha256':face._file_digest(self.source),
            'size':self.source.stat().st_size,'revision':'reviewed','private':True,'explicit':False}
        return result
    def test_real_crop_capture_composition_retains_original_pixels_and_all_audio(self):
        directory=self.root/'pipeline'
        calls=[]
        def fixture_native(dispatch,sink,request):
            calls.append(dispatch)
            self.assertEqual(dispatch.payload.waveform.shape,(2,165600))
            self.assertEqual(dispatch.payload.waveform[:,:3900].abs().max().item(),0)
            # Explicit synthetic output: no native-weight acceptance is implied.
            sink.capture(torch.zeros(3,124,64,64),dispatch.binding)
            return True
        before=face._file_digest(self.source)
        with patch.dict(os.environ,MAESTRO_H3_FACE_REFINE_EXPERIMENTAL='1'):
            output,provenance=repair.run_face_repair(self.source,self.prepared_request(),directory,native=fixture_native)
        self.assertEqual(len(calls),1);self.assertEqual(face._file_digest(self.source),before)
        original=face._read_rgb(self.source,self.facts,None);composite=face._read_rgb(output,self.facts,None)
        plan=json.loads((directory/'crops/plan.json').read_text())
        for index,rect in enumerate(plan['track']['rectangles']):
            if rect is None:self.assertTrue((original[index]==composite[index]).all())
            else:
                x,y,w,h=rect
                mask=torch.ones(64,96,dtype=torch.bool).numpy();mask[y:y+h,x:x+w]=False
                self.assertTrue((original[index][mask]==composite[index][mask]).all())
                self.assertTrue((composite[index,y:y+h,x:x+w]==128).all())
        face._check_audio(face._audio(self.source,None),face._audio(output,None))
        self.assertEqual(provenance['composition']['output_sha256'],face._file_digest(output))
        self.assertTrue(provenance['composition']['audio_packets_preserved'])
        self.assertFalse(provenance['composition']['native_h3_generation'])
        self.assertNotIn(str(self.root),json.dumps(provenance))
    def test_changed_source_commitment_stops_before_native_dispatch(self):
        request=self.prepared_request();request['source']['sha256']='sha256:'+'0'*64
        native=Mock(side_effect=AssertionError('model called'))
        with self.assertRaisesRegex(ValueError,'source changed'):
            repair.run_face_repair(self.source,request,self.root/'changed',native=native)
        native.assert_not_called()
    def test_invalid_inputs_refused_without_pixels_or_model(self):
        cases=[dict(name='../source.mkv'),dict(audio_stream=True),dict(strength=0),
            dict(settings={'seed':-1}),dict(settings={'loras':['x']}),dict(video_path='/tmp/x'),
            dict(frame_multipliers=[0.8]*124),dict(private_output='false'),dict(prompt='')]
        for fields in cases:
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                repair.validate_request({**self.body,**fields},self.facts)
        with self.assertRaises(ValueError):repair.validate_request(self.body,{**self.facts,'frame_count':125})
        huge=copy.deepcopy(self.body);huge['observations']['canvas']=[1024,1024]
        with self.assertRaisesRegex(ValueError,'memory'):repair.validate_request(huge,self.facts)
    def test_result_shape_binding_pixels_cancel_and_single_use(self):
        for label,video,binding in [('shape',torch.zeros(3,1,64,64),{'id':1}),
                ('binding',torch.zeros(3,124,64,64),{'id':2}),
                ('nan',torch.full((3,124,64,64),float('nan')),{'id':1}),
                ('range',torch.full((3,124,64,64),2.0),{'id':1})]:
            with self.subTest(label=label):
                sink=repair.FaceRefineResultSink(self.root/(label+'.mkv'),{'id':1},124,64,64)
                with self.assertRaises(ValueError):sink.capture(video,binding)
                self.assertIsNone(sink.receipt)
        sink=repair.FaceRefineResultSink(self.root/'cancel.mkv',{},124,64,64,lambda:True)
        with self.assertRaises(av.H3GalleryAVGuideCancelled):sink.capture(torch.zeros(3,124,64,64),{})
        self.assertFalse(sink.destination.exists())
        sink=repair.FaceRefineResultSink(self.root/'once.mkv',{},124,64,64)
        sink.capture(torch.zeros(3,124,64,64),{})
        digest=face._file_digest(sink.destination)
        with self.assertRaises(ValueError):sink.capture(torch.zeros(3,124,64,64),{})
        self.assertEqual(face._file_digest(sink.destination),digest)


class FaceJobBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=tool.ToolInputExecutionTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ns=self.fixture.ns;self.ns.update(copy=copy,inspect=inspect,
            _failure_stage_from_job=lambda _: "generation",
            _WgpNativeGpuExecutionSlot=lambda *args,**kwargs:tool.nullcontext(True))
        tool.load(self.ns,'_face_refine_available','_face_refine_source','_run_tool_h3_face_refine','tools_h3_face_refine',
                  '_h3_gallery_av_source_state')
    def job(self):
        job=self.fixture.job('tool_h3_face_refine')
        source=dict(workspace='project-a',name=self.fixture.video.name,
            revision=self.ns['_output_revision'](str(self.fixture.video),str(self.fixture.project),self.fixture.video.name),
            private=True,explicit=False)
        job['params'].update(face_refine={'source':source,'prompt':'test','settings':{}},private_output=True)
        self.fixture.manifests[job['id']]['params']=copy.deepcopy(job['params'])
        return job
    def publish(self,job):
        staged=self.fixture.root/'staged.mkv';staged.write_bytes(b'composite')
        job['_face_refine_provenance']={'composition':{'output_sha256':'sha256:'+hashlib.sha256(b'composite').hexdigest()},
                                      'source':job['params']['face_refine']['source']}
        return self.ns['_publish_processed_tool_output'](job,str(staged),source=str(self.fixture.video),
                tool='h3_face_refine',params={'model_type':'minimax_h3'},source_revision='reviewed',elapsed=1)
    def test_source_revision_policy_and_manifest_are_rechecked(self):
        job=self.job();self.assertEqual(self.ns['_validated_tool_input_paths'](job),[str(self.fixture.video)])
        for changed in ('revision','private','explicit','project'):
            with self.subTest(changed=changed):
                candidate=copy.deepcopy(job)
                if changed=='project':candidate['out_dir']=str(self.fixture.root)
                else:candidate['params']['face_refine']['source'][changed]='changed' if changed=='revision' else not candidate['params']['face_refine']['source'][changed]
                with self.assertRaises(ValueError):self.ns['_face_refine_source'](candidate)
        self.fixture.video.write_bytes(b'changed')
        with self.assertRaises(self.ns['_ToolInputChanged']):self.ns['_validated_tool_input_paths'](job)
    def test_durable_publication_adopts_without_model_or_available_gate(self):
        job=self.job();self.assertTrue(self.publish(job))
        output=self.fixture.project/job['output_files'][0]
        meta=json.loads(output.with_suffix('.meta.json').read_text())
        self.assertIsNone(meta['params']);self.assertTrue(meta['private'])
        self.assertEqual(meta['face_refine']['source']['name'],self.fixture.video.name)
        job.update(status='running',output_files=[]);job.pop('_face_refine_provenance')
        self.ns['_face_refine_available']=Mock(side_effect=AssertionError('availability after sealed adoption'))
        with patch.object(repair,'run_face_repair',side_effect=AssertionError('rerender')):
            self.assertTrue(self.ns['_run_tool_h3_face_refine'](job['id']))
        self.assertEqual(job['status'],'completed');self.assertEqual(len(job['output_files']),1)
        self.assertEqual(output.read_bytes(),b'composite')
    def test_changed_source_prevents_sealed_adoption_and_failure_stays_redacted(self):
        job=self.job();self.assertTrue(self.publish(job));job.update(status='running')
        self.fixture.video.write_bytes(b'changed')
        self.ns['_failure_stage_from_job']=lambda _: 'generation'
        self.assertFalse(self.ns['_run_tool_h3_face_refine'](job['id']))
        self.assertEqual(job['status'],'failed')
        self.assertIn('Refresh',job['message']);self.assertNotIn(str(self.fixture.root),job['message'])
    def test_cancelled_publication_cleanup_preserves_foreign_output(self):
        job=self.job();self.assertTrue(self.publish(job))
        media=self.fixture.project/job['output_files'][0];media.unlink();media.write_bytes(b'foreign')
        job.update(status='cancelled',cancel_requested=True)
        self.assertFalse(self.ns['_cleanup_cancelled_processed_tool_output'](job))
        self.assertEqual(media.read_bytes(),b'foreign');self.assertEqual(job['status'],'cancelled')
    def test_mismatched_compositor_digest_cannot_publish(self):
        job=self.job();staged=self.fixture.root/'bad.mkv';staged.write_bytes(b'bad')
        job['_face_refine_provenance']={'composition':{'output_sha256':'sha256:'+'0'*64}}
        with self.assertRaises(ValueError):self.ns['_publish_processed_tool_output'](job,str(staged),source=str(self.fixture.video),tool='h3_face_refine',params={},elapsed=1)
        self.assertFalse(job['output_files']);self.assertEqual(staged.read_bytes(),b'bad')
    def test_actual_admission_authorizes_current_source_and_registers_before_work(self):
        source=self.fixture.video
        facts=dict(width=96,height=64,frame_count=124,fps='24/1')
        body=request_for(source,facts)
        body['revision']=self.ns['_output_revision'](str(source),str(self.fixture.project),source.name)
        body['private_output']=False
        async def json_body():return body
        request=types.SimpleNamespace(json=json_body,state=types.SimpleNamespace(maestro_session_id='session'))
        access=Mock(return_value=str(self.fixture.project));registered=[]
        async def offthread(operation):return operation()
        self.ns.update(_request_project_workspace=lambda r,w:w,_require_remote_visible_models=Mock(),
            _require_h3_legal_execution=Mock(),_require_model_recipe_terms=Mock(),
            _require_project_access=access,_require_authorized_output=lambda r,w,n:(str(self.fixture.project),str(source),{}),
            upload_usage=types.SimpleNamespace(to_thread=offthread),_new_generation_job_id=lambda:'b'*32,
            _request_remote=types.SimpleNamespace(get=lambda:False),
            _queue_recovery_register_and_publish=lambda job,**kw:registered.append((job,kw)))
        with patch.dict(os.environ,MAESTRO_H3_FACE_REFINE_EXPERIMENTAL='1',MAESTRO_H3_FACE_REFINE_GALLERY_EXPERIMENTAL='1'),patch.object(face,'_probe',return_value=facts):
            response=asyncio.run(self.ns['tools_h3_face_refine'](request))
        access.assert_called_once_with(request,'project-a',permission='project.generate')
        job,kwargs=registered[0]
        self.assertEqual(response['job_id'],job['id']);self.assertTrue(job['params']['private_output'])
        self.assertEqual(job['params']['face_refine']['source']['sha256'],face._file_digest(source))
        self.assertEqual(job['params']['face_refine']['prompt'],body['prompt'])
        self.assertEqual(job['params']['custom_settings'],{'h3_attention_engine':'sdpa'})
        self.assertEqual(kwargs['worker'],self.ns['_run_tool_h3_face_refine'])
        self.assertEqual(kwargs['recovery_kind'],'tool_h3_face_refine')
        json.dumps(job)  # durable manifest has no runtime payload or sink

    def test_actual_worker_uses_admission_sealed_plan_and_private_invocation(self):
        job=self.job();request=job['params']['face_refine']
        request['settings']=dict(num_inference_steps=4,seed=42,override_profile=3)
        self.fixture.manifests[job['id']]['params']=copy.deepcopy(job['params'])
        admission=Mock();parity=Mock(return_value={'sealed':True});apply=Mock()
        calls=[]
        def generate(task,send_cmd,plugin_data,state,model_type,mode,video_length,resolution,image_refs,custom_settings,
                     _h3_face_refine_dispatch,_h3_face_refine_output):
            calls.append((_h3_face_refine_dispatch,_h3_face_refine_output,video_length,resolution))
            self.assertEqual((model_type,mode),('minimax_h3','generate'))
            self.assertEqual(image_refs,[])
            self.assertEqual(custom_settings,{'h3_attention_engine':'sdpa'});return True
        # The real attachment loader removes an empty reference-image list.
        # The worker must restore the required no-reference invocation value.
        tree=ast.parse((ROOT/'app/wgp.py').read_text())
        loader=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                    and n.name=='_load_task_attachments')
        attachment_ns={'ATTACHMENT_KEYS':{'image_refs'}}
        exec(compile(ast.Module(body=[loader],type_ignores=[]),'wgp.py','exec'),attachment_ns)
        from models.minimax_h3.minimax_h3_handler import family_handler
        def parse(manifest,state,cwd):
            # Real H3 defaults add dormant SOL tuning keys even for SDPA.
            family_handler.fix_settings('minimax_h3',0,{},manifest[0]['params'])
            self.assertIn('h3_sol_tau',manifest[0]['params']['custom_settings'])
            attachment_ns['_load_task_attachments'](manifest[0]['params'],cwd)
            self.assertNotIn('image_refs',manifest[0]['params'])
            return manifest,None
        parser=Mock(side_effect=parse)
        self.ns.update(wgp=types.SimpleNamespace(task_id=0,get_default_settings=lambda _: {},
                _parse_task_manifest=parser,generate_video=generate,save_path='unchanged'),
            _GENERATION_MEDIA_INPUTS={'image_refs','video_source','audio_source'},
            _require_remote_visible_job_models=Mock(),_require_job_runtime_model_admission=admission,
            _require_h3_offload_plan_parity=parity,_apply_h3_offload_plan_to_manifest=apply,
            _run_generation_task_with_llm_exclusion=lambda model,send,operation:operation())
        dispatch=types.SimpleNamespace(plan={'source':{'frame_count':124},'track':{'canvas':[64,64]}})
        sink=object()
        def producer(source,request,staging,*,native,cancel_check):
            self.assertTrue(native(dispatch,sink,request))
            output=Path(staging)/'composite.mkv';output.write_bytes(b'composite')
            return output,{'composition':{'output_sha256':face._file_digest(output)}}
        with patch.dict(os.environ,MAESTRO_H3_FACE_REFINE_EXPERIMENTAL='1',MAESTRO_H3_FACE_REFINE_GALLERY_EXPERIMENTAL='1'),patch.object(repair,'run_face_repair',side_effect=producer):
            self.assertTrue(self.ns['_run_tool_h3_face_refine'](job['id']))
        admission.assert_called_once_with(job);parity.assert_called_once_with(job)
        apply.assert_called_once();self.assertEqual(calls,[(dispatch,sink,124,'64x64')])
        self.assertNotIn('_h3_face_refine_dispatch',parser.call_args[0][0][0]['params'])
        self.assertNotIn('_h3_face_refine_output',parser.call_args[0][0][0]['params'])
        self.assertEqual(self.ns['wgp'].save_path,'unchanged');self.assertEqual(job['status'],'completed')
        self.assertNotIn('_face_refine_provenance',job)

    def test_registered_h3_unsealed_restore_holds_then_owner_retry_starts_exact_tool(self):
        from services.h3_legal_access import is_registered_h3_family
        original=self.fixture.job
        def face_job(kind,legacy=False):
            job=original(kind,legacy)
            job['params']['model_type']='minimax_h3'
            job['private']=True  # Match the real registry's source-inherited policy stamp.
            job['params']['face_refine']={'source':dict(workspace='project-a',name=self.fixture.video.name,
                revision=self.ns['_output_revision'](str(self.fixture.video),str(self.fixture.project),self.fixture.video.name),private=True,explicit=False)}
            self.fixture.manifests[job['id']]['params']=copy.deepcopy(job['params'])
            return job
        self.fixture.job=face_job
        coordinator,_=self.fixture._restore_held_tool('tool_h3_face_refine',status='running')
        self.ns.update(is_registered_h3_family=is_registered_h3_family,
            wgp=types.SimpleNamespace(get_model_def=lambda _: {},get_base_model_type=lambda _:'minimax_h3'))
        tool.load(self.ns,'_h3_job_model_types','_h3_registered_architectures','_job_uses_registered_h3')
        snapshot=coordinator.restore().jobs['a'*32]
        restored,may_start=self.ns['_queue_recovery_materialize_job'](snapshot,
            {'project-a':(str(self.fixture.project),snapshot['project_instance'])})
        self.assertTrue(self.ns['_job_uses_registered_h3'](restored));self.assertFalse(may_start)
        self.assertTrue(restored['queue_held']);self.assertEqual(restored['recovery_state'],'blocked')
        self.assertFalse(restored['reruns_denoise']);self.assertNotIn('_recovery_worker_pending',restored)
        self.assertEqual(restored['_recovery_reason_code'],'generation_failed')
        self.assertEqual(restored['recovery_attempt'],0)
        # The hold must survive a second startup, now with queued status.
        coordinator.prospective_transition(types.SimpleNamespace(jobs=(restored,)))
        second=coordinator.restore().jobs[restored['id']]
        restored,may_start=self.ns['_queue_recovery_materialize_job'](second,
            {'project-a':(str(self.fixture.project),second['project_instance'])})
        self.assertFalse(may_start);self.assertEqual(restored['_recovery_reason_code'],'generation_failed')
        starts=[]
        class DeferredThread:
            def __init__(self,**kwargs):self.kwargs=kwargs
            def start(self):starts.append((self.kwargs['target'],self.kwargs['args']))
        self.ns.update(_require_owned_job=lambda *args:restored,_require_project_access=Mock(),
            _queue_recovery_reason_code=lambda job:job.get('_recovery_reason_code',''),
            _queue_recovery_delivery_pending=lambda _:None,
            _h3_native_boundary_exact_retry_allowed=lambda _:False,
            _prepare_h3_peak_recovery=Mock(side_effect=AssertionError('longform recovery')),
            MAX_RECOVERY_ATTEMPTS=3,update_queue_job=lambda *args,**kw:True,
            threading=types.SimpleNamespace(Thread=DeferredThread),
            _QUEUE_RECOVERY_REASON_TEXT={},Response=object,_set_recovery_no_store=Mock())
        tool.load(self.ns,'_resume_recovered_job','retry_recovered_job')
        request=types.SimpleNamespace(state=types.SimpleNamespace(maestro_session_id='session'))
        response=self.ns['retry_recovered_job'](restored['id'],request,object())
        self.assertEqual(response['recovery_attempt'],1);self.assertTrue(response['reruns_denoise'])
        self.assertEqual(starts,[(self.ns['_run_tool_h3_face_refine'],(restored['id'],))])
        self.ns['_prepare_h3_peak_recovery'].assert_not_called()
        self.assertTrue(restored['access_policy']['private'])
        self.ns['_validated_tool_input_paths'](restored)

    def test_actual_worker_dispatch_kind_and_disabled_request(self):
        self.assertIs(self.ns['_queue_recovery_worker']({'kind':'tool_h3_face_refine'}),self.ns['_run_tool_h3_face_refine'])
        with patch.dict(os.environ,MAESTRO_H3_FACE_REFINE_EXPERIMENTAL='0'),self.assertRaises(tool.HTTPException) as error:
            asyncio.run(self.ns['tools_h3_face_refine'](object()))
        self.assertEqual(error.exception.status_code,409)


class FacePrivateOutputTests(unittest.TestCase):
    def test_actual_private_return_does_not_emit_ordinary_outputs(self):
        tree=ast.parse((ROOT/'app/wgp.py').read_text())
        impl=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_generate_video_impl')
        block=next(n for n in ast.walk(impl) if isinstance(n,ast.If)
            and ast.unparse(n.test)=='_h3_face_refine_output is not None' and any(isinstance(x,ast.Return) for x in n.body))
        wrapper=ast.FunctionDef(name='capture',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),
            body=[copy.deepcopy(block),ast.Raise(exc=ast.Call(func=ast.Name(id='AssertionError',ctx=ast.Load()),args=[ast.Constant('ordinary publication')],keywords=[]))],decorator_list=[])
        module=ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[]))
        for cancelled,missing in ((False,False),(True,False),(False,True)):
            with self.subTest(cancelled=cancelled,missing=missing):
                sink=Mock();samples=None if missing else object();clear=Mock()
                ns=dict(_h3_face_refine_output=sink,_h3_face_refine_dispatch=types.SimpleNamespace(binding={'source':'sealed'}),
                    samples=samples,abort_scheduled=False,gen={'abort':cancelled},state={},clear_status=clear)
                exec(compile(module,'wgp.py','exec'),ns)
                self.assertEqual(ns['capture'](),not (cancelled or missing));clear.assert_called_once()
                if cancelled or missing:sink.capture.assert_not_called()
                else:sink.capture.assert_called_once_with(samples,{'source':'sealed'})
    def test_both_metadata_paths_remove_private_tensors_and_sink(self):
        tree=ast.parse((ROOT/'app/wgp.py').read_text());impl=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_generate_video_impl')
        blocks=[]
        for node in ast.walk(impl):
            if (isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='pop'
                    and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='inputs'
                    and node.value.args and isinstance(node.value.args[0],ast.Constant)
                    and str(node.value.args[0].value).startswith('_h3_')):
                blocks.append(node)
        private=object();ns={'inputs':{'_h3_control_dispatch':private,'_h3_face_refine_dispatch':private,'_h3_face_refine_output':private,'prompt':'ordinary'}}
        exec(compile(ast.Module(body=blocks,type_ignores=[]),'wgp.py','exec'),ns)
        self.assertEqual(ns['inputs'],{'prompt':'ordinary'})
        self.assertEqual(sum('_h3_face_refine_output' in ast.unparse(n) for n in blocks),2)

if __name__=='__main__':unittest.main()
