"""Reviewed-face tool contract and private native crop result.

The launch adapter owns project access, selected-source revisions, model/GPU
admission and journal publication. No public paths or tensor inputs are accepted.
"""
from dataclasses import dataclass, field
import copy
import math
from pathlib import Path

from services import h3_face_refine as face
from services import h3_gallery_av_guide as av


def validate_request(value, facts):
    required = {'workspace', 'name', 'revision', 'prompt', 'observations',
                'strength', 'frame_multipliers', 'audio_stream', 'settings'}
    if (type(value) is not dict or set(value) - required - {'private_output', 'explicit_output'}
            or not required <= set(value)
            or any(type(value[k]) is not str or not 0 < len(value[k]) <= 255
                   for k in ('workspace', 'name', 'revision'))
            or Path(value['name']).name != value['name'] or '\\' in value['name']
            or type(value['prompt']) is not str or not value['prompt'].strip()
            or len(value['prompt']) > 16384
            or any(k in value and type(value[k]) is not bool for k in ('private_output', 'explicit_output'))):
        raise ValueError('Select a current Gallery video and reviewed face observations')
    frames = facts['frame_count']
    if not 124 <= frames <= 345 or frames % 17 != 5:
        raise ValueError('Face repair requires an exact Base-H3 frame count from 124 to 345')
    track = face.plan_crops(facts, value['observations'])
    settings = value['settings']
    if (type(settings) is not dict or set(settings) - {'num_inference_steps', 'seed', 'override_profile'}
            or any(k in settings and (type(settings[k]) is not int or not lo <= settings[k] <= hi)
                   for k,lo,hi in [('num_inference_steps',2,100),('seed',0,2**63-1),('override_profile',1,5)])):
        raise ValueError('Select valid face-repair sampling settings')
    steps = settings.get('num_inference_steps', 20)
    strength = value['strength']; weights = value['frame_multipliers']; audio = value['audio_stream']
    if (type(strength) not in (int,float) or not math.isfinite(strength)
            or not steps / 4096 <= strength <= 1
            or type(weights) is not list or len(weights) != frames
            or any(type(x) not in (int,float) or not math.isfinite(x) or not 0 <= x <= 1 for x in weights)
            or any(x != 0 for rect,x in zip(track['rectangles'],weights) if rect is None)
            or (audio is not None and (type(audio) is not int or not 0 <= audio < 16))):
        raise ValueError('Face repair needs bounded strength, one weight per frame and an explicit audio stream or null')
    width,height = track['canvas']
    from models.minimax_h3.packing import audio_latent_num_frames
    samples = 0 if audio is None else audio_latent_num_frames(frames)*800
    if (frames*width*height*12 + samples*8 > 512*1024**2
            or frames*width*height*3*13 + samples*2*13 + 8*1024**2 > av.MAX_DECODED_BYTES
            or (facts['width']*facts['height']+width*height)*frames*3 > face.MAX_RGB_BYTES):
        raise ValueError('The selected clip and crop exceed the face-repair memory limit')
    result = copy.deepcopy(value)
    result['settings'] = {'num_inference_steps':steps, 'seed':settings.get('seed',42),
                          'override_profile':settings.get('override_profile',3)}
    return result


@dataclass
class FaceRefineResultSink:
    """Worker-only, single-use lossless result; never an ordinary WGP output."""
    destination: Path
    binding: dict
    frames: int
    width: int
    height: int
    cancel_check: object = None
    receipt: dict | None = field(default=None, init=False)

    def validate_binding(self, binding, frames, width, height):
        if (self.receipt is not None or binding != self.binding
                or (frames,width,height) != (self.frames,self.width,self.height)
                or self.destination.exists()):
            raise ValueError('Face repair result binding changed or was already consumed')

    def capture(self, video, binding):
        import torch
        self.validate_binding(binding,self.frames,self.width,self.height)
        if (self.receipt is not None or binding != self.binding
                or not isinstance(video, torch.Tensor) or video.device.type != 'cpu'
                or video.layout != torch.strided or not video.is_floating_point()
                or tuple(video.shape) != (3,self.frames,self.height,self.width)
                or video.numel()*video.element_size() > 512*1024**2):
            raise ValueError('Face repair native result changed or was already consumed')
        av._check(self.cancel_check)
        def pixels():
            for index in range(self.frames):
                av._check(self.cancel_check)
                frame = video[:,index].detach()
                if not torch.isfinite(frame).all() or (frame.abs()>1).any():
                    raise ValueError('Face repair returned invalid pixels')
                yield ((frame.float()+1)*127.5).round().clamp(0,255).to(torch.uint8).permute(1,2,0).contiguous().numpy().tobytes()
        face._encode(self.destination, pixels(), self.width,self.height,self.cancel_check)
        if not 0 < self.destination.stat().st_size <= av.MAX_ENCODED_BYTES:
            raise ValueError('Face repair native crop exceeds the encoded limit')
        expected = {'width':self.width,'height':self.height,'frame_count':self.frames,'fps':'24/1'}
        if face._probe(self.destination,self.cancel_check) != expected:
            raise ValueError('Face repair native crop changed its frame clock')
        av._check(self.cancel_check)
        self.receipt = {'schema':'maestro.h3.face-native-result','version':1,
                        'binding':copy.deepcopy(self.binding),
                        'replacement_sha256':face._file_digest(self.destination), **expected}


def run_face_repair(source, request, directory, *, native, cancel_check=None):
    """Prepare -> private native result -> sealed source composition.

    No model replay occurs after publication: the launch adapter adopts its
    existing processed-tool publication before calling this producer.
    """
    directory = Path(directory)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    plan = face.prepare_crops(source,request['observations'],directory/'crops',cancel_check=cancel_check)
    captured = request['source']
    if (plan['source']['sha256'] != captured['sha256'] or plan['source']['size'] != captured['size']
            or any(plan['source'][k] != captured[k] for k in ('width','height','frame_count','fps'))):
        raise ValueError('The selected source changed before face repair')
    from services.h3_face_refine_worker import make_face_refine_dispatch
    dispatch = make_face_refine_dispatch(source,directory/'crops'/'crops.mkv',plan,
        expected_plan_sha256=plan['plan_sha256'],strength=request['strength'],
        frame_multipliers=tuple(request['frame_multipliers']),audio_stream=request['audio_stream'],
        sampling_steps=request['settings']['num_inference_steps'],cancel_check=cancel_check)
    width,height=plan['track']['canvas'];frames=plan['source']['frame_count']
    sink = FaceRefineResultSink(directory/'replacement.mkv',copy.deepcopy(dispatch.binding),frames,width,height,cancel_check)
    av._check(cancel_check)
    if native(dispatch,sink,request) is not True or sink.receipt is None:
        raise ValueError('Face repair did not produce a verified native crop')
    av._check(cancel_check)
    result = face.compose_crops(source,sink.destination,plan,
        replacement_sha256=sink.receipt['replacement_sha256'],destination=directory/'composite',cancel_check=cancel_check)
    provenance = {'version':1, 'source':copy.deepcopy(captured),'plan_sha256':plan['plan_sha256'],
                  'crops_sha256':plan['crops_sha256'],'native_result':sink.receipt,
                  'model_type':'minimax_h3','settings':copy.deepcopy(request['settings']),
                  'strength':request['strength'],'frame_multipliers':request['frame_multipliers'],
                  'audio_stream':request['audio_stream'],'composition':result}
    return directory/'composite'/'composite.mkv', provenance
