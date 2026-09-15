import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = build({
  stdin: {contents:"export { useStore } from './src/stores/useStore.ts'",resolveDir:new URL('..',import.meta.url).pathname,loader:'js'},
  bundle:true,format:'esm',platform:'node',write:false,logLevel:'silent',
}).then(result=>result.outputFiles[0].text)
let realm=0
const file={name:'clip.mp4',type:'video/mp4'}
async function withStore(run, fetch=async()=>{throw new Error('Unexpected request')}) {
  const names=['window','document','localStorage','sessionStorage','fetch']
  const originals=Object.fromEntries(names.map(name=>[name,globalThis[name]]))
  const revoke=URL.revokeObjectURL, revoked=[]
  const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)}}
  globalThis.localStorage=storage();globalThis.sessionStorage=storage();globalThis.fetch=fetch
  globalThis.window=Object.assign(new EventTarget(),{setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},location:{hostname:'127.0.0.1'},matchMedia:()=>({matches:true,addEventListener(){},removeEventListener(){}})})
  globalThis.document=Object.assign(new EventTarget(),{hidden:false})
  URL.revokeObjectURL=url=>revoked.push(url)
  try {
    const {useStore}=await import(`data:text/javascript;base64,${Buffer.from(await bundle).toString('base64')}#blend-media-${++realm}`)
    useStore.setState({activeWorkspace:'project-a',generationMode:'video',loadOutputs:async()=>true,loadWorkspaces:async()=>true,loadPresets:async()=>{}})
    await run(useStore,revoked)
    await new Promise(resolve=>setImmediate(resolve))
  } finally {
    URL.revokeObjectURL=revoke
    for(const name of names){if(originals[name]===undefined)delete globalThis[name];else globalThis[name]=originals[name]}
  }
}
function assertEmpty(store) {
  const state=store.getState()
  for(const slot of ['A','B']) {
    assert.equal(state['blendClip'+slot],null)
    assert.equal(state['blendClip'+slot+'Path'],'')
    assert.equal(state['blendClip'+slot+'Url'],'')
    assert.equal(state['blendClip'+slot+'Duration'],0)
  }
}

test('installed Blend media is discarded across a project round trip even without a mounted panel', async()=>{
  await withStore(async(store,revoked)=>{
    store.getState().setBlendClipA(file,'a.mp4','blob:clip-a',4)
    store.getState().setBlendClipB(file,'b.mp4','blob:clip-b',5)
    store.setState({activeWorkspace:'project-b'});assertEmpty(store)
    store.setState({activeWorkspace:'project-a'});assertEmpty(store)
    assert.deepEqual(revoked.sort(),['blob:clip-a','blob:clip-b'])
  })
})

test('normal project switching clears installed media only when the new project commits', async()=>{
  let resolve
  const pending=new Promise(done=>{resolve=done})
  await withStore(async(store,revoked)=>{
    store.getState().setBlendClipA(file,'a.mp4','blob:clip-a',4)
    const switching=store.getState().switchWorkspace('project-b')
    assert.equal(store.getState().blendClipA,file)
    resolve(Response.json({status:'ok'}));assert.equal(await switching,true)
    assertEmpty(store);assert.deepEqual(revoked,['blob:clip-a'])
  },async(input,options)=>{
    assert.equal(String(input),'/api/v1/workspaces/active');assert.equal(options.method,'PUT')
    return pending
  })
})

test('account scrub clears installed Blend media and releases its owned previews', async()=>{
  await withStore(async(store,revoked)=>{
    store.setState({accountContext:{enabled:true,authenticated:true,account:{id:'old-owner'},capabilities:[]},accessContext:{accounts:{enabled:false,authenticated:false,account:null,capabilities:[]}}})
    store.getState().setBlendClipA(file,'a.mp4','blob:clip-a',4)
    store.getState().setBlendClipB(file,'b.mp4','blob:clip-b',5)
    await store.getState().loadAccountContext(false)
    assertEmpty(store);assert.deepEqual(revoked.sort(),['blob:clip-a','blob:clip-b'])
  })
})

test('replacement and clear release a preview only after its last Blend slot releases it', async()=>{
  await withStore(async(store,revoked)=>{
    store.getState().setBlendClipA(file,'a.mp4','blob:shared',4)
    store.getState().setBlendClipB(file,'a.mp4','blob:shared',4)
    store.getState().setBlendClipA(file,'new.mp4','blob:new',6)
    assert.deepEqual(revoked,[])
    store.getState().clearBlendClipB();assert.deepEqual(revoked,['blob:shared'])
    store.getState().clearBlendClipA();assert.deepEqual(revoked,['blob:shared','blob:new'])
    store.getState().clearBlendClipA();assert.equal(revoked.length,2)
    store.getState().setBlendClipA(file,'remote.mp4','https://example.invalid/clip.mp4',4)
    store.getState().clearBlendClipA();assert.equal(revoked.length,2)
  })
})

test('same-project mode changes retain selected Blend media until it is explicitly discarded', async()=>{
  await withStore(async(store,revoked)=>{
    store.getState().setBlendClipA(file,'a.mp4','blob:clip-a',4)
    store.setState({generationMode:'image'});store.setState({generationMode:'video'})
    assert.equal(store.getState().blendClipA,file);assert.deepEqual(revoked,[])
    store.getState().clearBlendClipA();assert.deepEqual(revoked,['blob:clip-a'])
  })
})
