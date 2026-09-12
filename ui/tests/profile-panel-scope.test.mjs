import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../src/components/Sidebar/GenerationProfiles.tsx', import.meta.url), 'utf8')
const body = source.split('\n').filter(line => !line.startsWith('import ')).join('\n')
const compiled = await transform(`
  let state, epoch, values, cursor
  const useStore = select => select(state)
  const currentAccountIdentityEpoch = () => epoch
  const useEffect = () => {}
  const useMemo = compute => compute()
  const useRef = value => ({current:value})
  const useState = initial => {
    const index = cursor++
    if (!(index in values)) values[index] = initial
    const instance = values
    return [values[index], value => { instance[index] = value }]
  }
  const node = (type, props, ...children) => ({type,props:props || {},children})
  const FolderOpen = 'icon', Save = 'icon', Trash2 = 'icon'
  const GenerationProfileRefreshStatus = 'status'
  class GenerationProfileSettingsError extends Error {}
  ${body}
  export function render(value, identityEpoch, local = []) {
    state = value; epoch = identityEpoch; values = local; cursor = 0
    const wrapper = GenerationProfiles()
    return { key: wrapper.props.key, tree: wrapper.type(wrapper.props), local }
  }
`, { loader: 'tsx', format: 'esm', jsxFactory: 'node' })
const { render } = await import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString('base64')}`)
const walk = value => Array.isArray(value) ? value.flatMap(walk) : value && typeof value === 'object' ? [value,...value.children.flatMap(walk)] : []
const profile = { id: 'profile-a', name: 'Private name', mode: 'video', model_type: 'model', activated_loras: [] }
const base = { accountContext: {account:{id:'owner'}}, activeWorkspace:'a', generationMode:'video', presets:[profile], presetsLoading:false, presetsError:null, models:[], modelsLoaded:false, selectedGenerationProfileId:'profile-a', loadPresets() {}, setSelectedGenerationProfileId() {} }

test('profile form identity changes for account project mode and same-account epoch', () => {
  const initial = render(base, 1).key
  assert.equal(render({...base}, 1).key, initial)
  for (const state of [{...base,activeWorkspace:'b'}, {...base,generationMode:'image'}, {...base,accountContext:{account:{id:'other'}}}]) {
    assert.notEqual(render(state,1).key, initial)
  }
  assert.notEqual(render(base,2).key, initial)
  assert.notEqual(render({...base,activeWorkspace:'a|b'},1).key, render({...base,activeWorkspace:'a',generationMode:'b|video'},1).key)
})

test('late deletion cannot clear a selection made in the new profile panel', async () => {
  let resolve
  const deletion = new Promise(done => {resolve=done})
  let selected = 'profile-a'
  const local = ['',false,null,true,null]
  const old = render({...base,deletePreset:()=>deletion,setSelectedGenerationProfileId:id=>{selected=id}},1,local)
  const button = walk(old.tree).find(item=>item.props['aria-label']==='Confirm delete profile Private name')
  button.props.onClick()
  selected = 'profile-b'
  const next = render({...base,activeWorkspace:'b',selectedGenerationProfileId:'profile-b',presets:[{...profile,id:'profile-b'}]},1)
  assert.notEqual(next.key,old.key)
  assert.deepEqual(next.local,['',false,null,false,null])
  resolve()
  await deletion
  await new Promise(done=>setTimeout(done,0))
  assert.equal(selected,'profile-b')
  assert.deepEqual(next.local,['',false,null,false,null], 'old completion cannot update the new form instance')
})

test('store deletion clears only the deleted selection in its original scope', async () => {
  const storeSource = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  const start = storeSource.indexOf('  deletePreset: async (id) => {')
  const end = storeSource.indexOf('\n  // Model options', start)
  assert.ok(start >= 0 && end > start)
  const script = await transform(`
    export function setup(initial, request) {
      let state = initial, _accountIdentityEpoch = 1
      const get = () => state
      const set = update => { state = {...state,...update(state)} }
      const api = {deletePreset:request}
      const actions = {${storeSource.slice(start,end)}}
      return {actions,get,change: update => {state={...state,...update}},signOut:()=>{_accountIdentityEpoch++}}
    }
  `, {loader:'ts',format:'esm'})
  const {setup} = await import(`data:text/javascript;base64,${Buffer.from(script.code).toString('base64')}`)
  for (const change of ['none','selection','project','account']) {
    let resolve
    const request = new Promise(done=>{resolve=done})
    const runtime = setup({activeWorkspace:'a',presets:[{id:'a'},{id:'b'}],selectedGenerationProfileId:'a'},()=>request)
    const pending = runtime.actions.deletePreset('a')
    if (change==='selection') runtime.change({selectedGenerationProfileId:'b'})
    if (change==='project') runtime.change({activeWorkspace:'b'})
    if (change==='account') runtime.signOut()
    resolve(); await pending
    const state = runtime.get()
    assert.equal(state.selectedGenerationProfileId, change==='none' ? '' : change==='selection' ? 'b' : 'a')
    assert.deepEqual(state.presets.map(p=>p.id), ['project','account'].includes(change) ? ['a','b'] : ['b'])
  }
})
