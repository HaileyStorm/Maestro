import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = build({
  stdin: { contents: "export { useStore } from './src/stores/useStore.ts'", resolveDir: new URL('..', import.meta.url).pathname, loader: 'js' },
  bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
}).then(result => result.outputFiles[0].text)
let realm = 0

async function submitBlend(mode, transition, overlap) {
  const names = ['fetch', 'window', 'document', 'localStorage', 'sessionStorage']
  const originals = Object.fromEntries(names.map(name => [name, globalThis[name]]))
  const storage = () => {
    const values = new Map()
    return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) }
  }
  const requests = []
  globalThis.localStorage = storage()
  globalThis.sessionStorage = storage()
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    location: { hostname: '127.0.0.1' },
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
    alert(message) { assert.fail(message) },
  })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  globalThis.fetch = async (input, options = {}) => {
    assert.equal(String(input), '/api/v1/blend')
    assert.equal(options.method, 'POST')
    requests.push(JSON.parse(options.body))
    return Response.json({ job_id: 'blend-test-job', status: 'queued' })
  }
  try {
    const { useStore } = await import(`data:text/javascript;base64,${Buffer.from(await bundle).toString('base64')}#blend-submit-${++realm}`)
    useStore.setState(state => ({
      activeWorkspace: 'blend-test', generationMode: 'video',
      modelOptionsLoading: false, modelOptions: { i2v_class: true, t2v_class: true },
      _pollRecoveredJob() {},
      params: { ...state.params, model_type: 'ltx2_19B', image_mode: 4, prompt: 'A continuous camera move', seed: 71 },
    }))
    useStore.setState({ blendClipAPath: 'clip-a.mp4', blendClipBPath: 'clip-b.mp4' })
    useStore.getState().setBlendMode(mode)
    useStore.getState().setBlendTransitionSec(transition)
    useStore.getState().setBlendOverlapSec(overlap)
    await useStore.getState().startGeneration('queue')
    assert.equal(requests.length, 1)
    assert.equal(requests[0].workspace, 'blend-test')
    assert.equal(requests[0].seed, 71)
    assert.equal(useStore.getState().jobs[0].id, 'blend-test-job')
    return requests[0]
  } finally {
    for (const name of names) {
      if (originals[name] === undefined) delete globalThis[name]
      else globalThis[name] = originals[name]
    }
  }
}

test('Insert submits the selected new-footage duration independently of overlap', async () => {
  const request = await submitBlend('insert', 7, 2)
  assert.equal(request.blend_mode, 'insert')
  assert.equal(request.transition_sec, 7)
  assert.equal(request.overlap_sec, 2)
  assert.equal(Object.hasOwn(request, 'motion_prefix_sec'), false)
  assert.equal(Object.hasOwn(request, 'motion_suffix_sec'), false)
  assert.equal(Object.hasOwn(request, 'input_video_strength'), false)
})

test('Overlap keeps its selected trim duration when Insert has a different duration', async () => {
  const request = await submitBlend('overlap', 9, 3)
  assert.equal(request.blend_mode, 'overlap')
  assert.equal(request.overlap_sec, 3)
  assert.equal(request.transition_sec, 9)
  assert.equal(request.motion_prefix_sec, 1)
  assert.equal(request.motion_suffix_sec, 1)
  assert.equal(request.input_video_strength, 0.7)
})
