import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const built = await build({ entryPoints: [fileURLToPath(new URL('../src/lib/faceRepairReview.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' })
const { validateFaceRepairSource, resolveFaceRepairSelection, reviewFaceRange, buildFaceRepairRequest, defaultFaceRepairCanvas, faceRepairAdmissionKey, readFaceRepairAdmission, beginFaceRepairAdmission, setFaceRepairAdmission } = await import('data:text/javascript;base64,' + Buffer.from(built.outputFiles[0].text).toString('base64'))
const source = { workspace: 'project', name: 'clip.mkv', revision: 'r1', type: 'video', artifact_class: 'final', private: true, explicit: true }
const facts = { workspace: 'project', name: 'clip.mkv', revision: 'r1', width: 192, height: 128, frame_count: 124, fps: '24/1', audio_streams: [{ ordinal: 0, label: 'Track 1' }, { ordinal: 1, label: 'Track 2' }] }
const model = { model_type: 'minimax_h3', h3_face_refine: true, execution_allowed: true }
const blank = () => Array(124).fill(null)

test('selection requires its own host capability, final video, current project and generation authority', () => {
  const resolve = (override = {}) => resolveFaceRepairSelection(override.outputs ?? [source], ['project\0clip.mkv'], override.workspace ?? 'project', override.allowed ?? true, [override.model ?? model], new Set(override.enabled ?? ['minimax_h3']), override.loaded ?? true)
  assert.equal(resolve(), source)
  for (const override of [{ allowed:false }, { loaded:false }, { enabled:[] }, { workspace:'other' }, { model:{...model,h3_face_refine:undefined,h3_gallery_av_guides:true} }, { model:{...model,execution_allowed:false} }, { model:{...model,availability_status:'legal_blocked'} }, { outputs:[{...source,artifact_class:'component'}] }, { outputs:[{...source,type:'image'}] }]) assert.equal(resolve(override), null)
})
test('server source binding and exact clock cannot come from duration or stale metadata', () => {
  assert.equal(validateFaceRepairSource(facts, source), facts)
  for (const delta of [{revision:'r2'}, {fps:'23.976'}, {frame_count:125}, {width:NaN}, {frame_count:346}, {audio_streams:[{ordinal:2,label:'Container stream 2'}]}]) assert.throws(() => validateFaceRepairSource({...facts,...delta}, source))
})
test('review is explicit, gap preserving and shot bounded; independent box copies remain editable', () => {
  const boxes = reviewFaceRange(blank(), [32,16,96,80], 10, 20, [0,62], facts)
  assert.equal(boxes.filter(Boolean).length, 11)
  assert.equal(boxes[9], null); assert.equal(boxes[21], null)
  boxes[10][0] = 40; assert.equal(boxes[11][0], 32)
  assert.throws(() => reviewFaceRange(boxes, [32,16,96,80], 60, 63, [0,62], facts))
  assert.throws(() => reviewFaceRange(blank(), [32,16,96,90], 0, 0, [0], facts))
  assert.throws(() => reviewFaceRange(blank(), [160,16,224,80], 0, 0, [0], facts))
  assert.equal(reviewFaceRange(boxes, null, 10, 20, [0,62], facts).filter(Boolean).length, 0)
})
test('public repair recipe preserves opaque creative text, unresolved frames and source privacy/audio ordinal', () => {
  const boxes = reviewFaceRange(blank(), [32,16,96,80], 0, 3, [0,62], facts)
  const prompt = 'Adult horror portrait, bruised face, controversial dialogue — preserve identity.'
  const result = buildFaceRepairRequest(source,facts,boxes,[0,62],prompt,.5,128,1,4,42)
  assert.equal(result.prompt,prompt); assert.deepEqual(result.observations.shots,[0,62])
  assert.deepEqual(result.observations.canvas,[128,128]); assert.equal(result.observations.padding,1); assert.equal(result.observations.smoothing,1)
  assert.deepEqual(result.frame_multipliers.slice(0,6),[1,1,1,1,0,0])
  assert.equal(result.audio_stream,1); assert.equal(result.private_output,true); assert.equal(result.explicit_output,true)
  assert.equal(result.settings.seed,42)
  for (const [strength,canvas,audio,steps,seed] of [[0,128,null,20,42],[.5,65,null,20,42],[.5,128,2,20,42],[.5,128,null,1,42],[.5,128,null,20,Number.MAX_SAFE_INTEGER+1],[.5,1536,null,20,42]]) assert.throws(()=>buildFaceRepairRequest(source,facts,boxes,[0],prompt,strength,canvas,audio,steps,seed))
})
test('measured long sources get a usable default without relaxing crop or audio budgets', () => {
  for (const [frame_count, expected] of [[124,384],[294,384],[311,352],[328,352],[345,352]]) {
    const measured={...facts,frame_count}, boxes=Array(frame_count).fill(null)
    boxes[0]=[32,16,96,80]
    const canvas=defaultFaceRepairCanvas(validateFaceRepairSource(measured,source))
    assert.equal(canvas,expected)
    for (const audio of [null,1]) {
      const request=buildFaceRepairRequest(source,measured,boxes,[0],'Preserve expression.',.5,canvas,audio,20,42)
      assert.deepEqual(request.observations.canvas,[canvas,canvas])
    }
    if(frame_count>=311) assert.throws(()=>buildFaceRepairRequest(source,measured,boxes,[0],'Preserve expression.',.5,384,null,20,42))
  }
  const exhausted=validateFaceRepairSource({...facts,width:1280,height:405,frame_count:345},source)
  assert.throws(()=>defaultFaceRepairCanvas(exhausted),/too large/)
})
test('submission receipts are account/source scoped, durable before POST, and fail closed without storage', () => {
  const values=new Map(), previousStorage=globalThis.sessionStorage, previousWindow=globalThis.window
  globalThis.sessionStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}
  globalThis.window=new EventTarget()
  try {
    const key=faceRepairAdmissionKey('account-a',source)
    assert.equal(readFaceRepairAdmission(key),null);assert.equal(beginFaceRepairAdmission(key),true)
    assert.equal(values.get(key),'uncertain');assert.equal(readFaceRepairAdmission(key),'pending')
    assert.equal(beginFaceRepairAdmission(key),false)
    assert.equal(readFaceRepairAdmission(faceRepairAdmissionKey('account-b',source)),null)
    setFaceRepairAdmission(key,'uncertain');assert.equal(beginFaceRepairAdmission(key),false)
    setFaceRepairAdmission(key,'accepted');assert.equal(values.get(key),'accepted');assert.equal(beginFaceRepairAdmission(key),false)
    setFaceRepairAdmission(key,null);assert.equal(beginFaceRepairAdmission(key),true)
    globalThis.sessionStorage.setItem=()=>{throw Error('Storage unavailable')}
    assert.equal(beginFaceRepairAdmission(faceRepairAdmissionKey('account-c',source)),false)
  } finally {globalThis.sessionStorage=previousStorage;globalThis.window=previousWindow}
})
