import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../src/components/Sidebar/BlendControls.tsx', import.meta.url), 'utf8')
const mediaContract = await readFile(new URL('../src/components/Sidebar/blendMediaTypes.ts', import.meta.url), 'utf8')
const body = source.split('\n').filter(line => !line.startsWith('import ')).join('\n')
  .replace('export function BlendControls', 'function BlendControls')
const compiled = await transform(`
${mediaContract.replaceAll('export ', '')}
export function createHarness() {
  let epoch = 1, state, nextUrl = 0, nextTimer = 0
  const listeners = new Set(), timers = new Map(), cleanups = []
  const requests = [], assigned = [], errors = [], revoked = [], videos = []
  const change = values => { state = {...state, ...values}; for (const listener of listeners) listener() }
  const useStore = select => select(state)
  useStore.getState = () => state
  useStore.subscribe = listener => { listeners.add(listener); return () => listeners.delete(listener) }
  const currentAccountIdentityEpoch = () => epoch
  const useRef = value => ({current:value})
  const useCallback = fn => fn
  const useState = value => [value, next => errors.push(next)]
  const useEffect = fn => { const cleanup = fn(); if (cleanup) cleanups.push(cleanup) }
  const node = (type, props, ...children) => ({type, props:props || {}, children})
  const X = 'icon', Film = 'icon', ArrowRight = 'icon'
  const URL = {createObjectURL: () => 'blob:test-' + ++nextUrl, revokeObjectURL: url => revoked.push(url)}
  const window = {setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer }, clearTimeout: id => timers.delete(id)}
  const document = {createElement: () => {
    const video = {duration:5, onloadedmetadata:null, onerror:null, removeAttribute() {}, load() {}}
    videos.push(video); return video
  }}
  const api = {uploadImage: file => new Promise((resolve,reject) => requests.push({file,resolve,reject}))}
  const install = (target,file,path,url,duration) => {
    assigned.push({target,file,path,url,duration})
    change({['blendClip'+target]:file,['blendClip'+target+'Path']:path})
  }
  state = {
    activeWorkspace:'project-a',generationMode:'video',params:{image_mode:4},
    selectedOutput:0,selectedOutputMetaName:'blend-a.mp4',blendRestoreSourceKey:'',
    blendClipA:null,blendClipB:null,blendClipAUrl:'',blendClipBUrl:'',blendClipADuration:0,blendClipBDuration:0,
    blendTransitionSec:5,blendMode:'overlap',blendOverlapSec:3,blendMotionPrefixSec:1,blendMotionSuffixSec:1,blendAnchorStrength:0.7,
    setBlendClipA:(...args)=>install('A',...args),setBlendClipB:(...args)=>install('B',...args),
    clearBlendClipA:()=>change({blendClipA:null,blendClipAPath:''}),clearBlendClipB:()=>change({blendClipB:null,blendClipBPath:''}),
    ensureTransitionLoraForBlend(){},setBlendTransitionSec(){},setBlendMode(){},setBlendOverlapSec(){},
    setBlendMotionPrefixSec(){},setBlendMotionSuffixSec(){},setBlendAnchorStrength(){},
  }
  ${body}
  const tree = BlendControls()
  const walk = value => Array.isArray(value) ? value.flatMap(walk) : value && typeof value==='object' ? [value,...value.children.flatMap(walk)] : []
  const target = label => walk(tree).find(item=>item.props.label===label)
  return {change, requests, assigned, errors, revoked, videos, timers,
    upload:(file,label='Clip A')=>target(label).props.onUpload(file),
    clear:(label='Clip A')=>target(label).props.onClear(),
    accountChange:()=>{epoch++;change({})},dispose:()=>cleanups.forEach(fn=>fn()),
  }
}`, {loader:'tsx',format:'esm',jsxFactory:'node'})
const {createHarness} = await import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString('base64')}`)
const image = name => ({name,type:'image/png'})
const video = {name:'clip.mp4',type:'video/mp4'}
const complete = (request,path='uploaded.png') => request.resolve({path})

test('unsupported browser media is rejected before upload with an actionable format list', async () => {
  const h=createHarness()
  await h.upload({name:'animated.gif',type:'image/gif'})
  assert.equal(h.requests.length,0)
  assert.deepEqual(h.errors.filter(Boolean),[
    'Choose a PNG, JPEG, WebP, BMP, TIFF, MP4, MKV, AVI, MOV, or WebM file.',
  ])
  h.dispose()
})

test('late Blend uploads cannot cross project, account, mode, output, or restore boundaries', async () => {
  for (const change of [
    h=>{h.change({activeWorkspace:'project-b'});h.change({activeWorkspace:'project-a'})},
    h=>h.accountChange(),
    h=>{h.change({params:{image_mode:0}});h.change({params:{image_mode:4}})},
    h=>h.change({selectedOutput:1,selectedOutputMetaName:'blend-b.mp4'}),
    h=>h.change({blendRestoreSourceKey:'restored-output'}),
  ]) {
    const h=createHarness(), pending=h.upload(image('old.png'))
    change(h);complete(h.requests[0]);await pending
    assert.equal(h.assigned.length,0)
    assert.equal(h.videos.length,0)
    h.dispose()
  }
})

test('a newer upload or explicit clear supersedes an older upload for the same clip', async () => {
  const h=createHarness(), first=h.upload(image('first.png')), second=h.upload(image('second.png'))
  complete(h.requests[1],'second.png');await second
  complete(h.requests[0],'first.png');await first
  assert.deepEqual(h.assigned.map(x=>x.path),['second.png'])
  const cleared=h.upload(image('cleared.png'));h.clear();complete(h.requests[2]);await cleared
  assert.equal(h.assigned.length,1)
  h.dispose()
})

test('leaving a project disposes a pending video URL and ignores an already queued callback', async () => {
  const h=createHarness(), pending=h.upload(video)
  complete(h.requests[0],'clip.mp4');await pending
  const callback=h.videos[0].onloadedmetadata
  h.change({activeWorkspace:'project-b'});callback()
  assert.equal(h.assigned.length,0)
  assert.deepEqual(h.revoked,['blob:test-1'])
  assert.equal(h.timers.size,0)
  h.dispose()
})

test('successful video metadata installs the clip and keeps its display URL', async () => {
  const h=createHarness(), pending=h.upload(video,'Clip B')
  complete(h.requests[0],'clip.mp4');await pending
  h.videos[0].duration=6.25;h.videos[0].onloadedmetadata()
  assert.deepEqual(h.assigned,[{target:'B',file:video,path:'clip.mp4',url:'blob:test-1',duration:6.25}])
  assert.deepEqual(h.revoked,[])
  assert.equal(h.timers.size,0)
  h.dispose()
  assert.deepEqual(h.revoked,[], 'installed display URL is owned by the selected clip')
})

test('video errors and metadata timeout release pending URLs and show one actionable error', async () => {
  for (const action of ['error','timeout']) {
    const h=createHarness(), pending=h.upload(video)
    complete(h.requests[0],'clip.mp4');await pending
    const fail=action==='error'?h.videos[0].onerror:[...h.timers.values()][0]
    fail();fail()
    assert.equal(h.assigned.length,0)
    assert.deepEqual(h.revoked,['blob:test-1'])
    assert.deepEqual(h.errors.filter(Boolean),['Could not read this video. Try another file.'])
    h.dispose()
  }
})

test('unmount invalidates an unfinished upload without publishing an old error', async () => {
  const h=createHarness(), pending=h.upload(image('old.png'))
  h.dispose();h.requests[0].reject(new Error('old network failure'));await pending
  assert.equal(h.assigned.length,0)
  assert.deepEqual(h.errors.filter(Boolean),[])
})
