import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  directorV2Plan,
  fetchH3StyleWorkflows,
  previewGenerationPlan,
  startPipeline,
  submitGeneration,
} from '../src/api/client.ts'
import {
  captureH3StyleWorkflowRequest,
  h3StyleWorkflowCatalogStateLabel,
  h3StyleWorkflowSupportsModel,
  h3StyleWorkflowSwatch,
  nextH3StyleWorkflowSurprise,
  resolveH3StyleWorkflowRequest,
  stripLegacyH3StylePrefix,
} from '../src/lib/h3StyleWorkflows.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))

const workflow = {
  id: 'papercraft-stop-motion-explainer',
  label: 'Papercraft stop-motion explainer',
  description: 'Tactile handmade paper explainer metadata.',
  prompt_brief: 'server-owned brief must never be sent by the UI',
  workflow_identity_source: 'official_minimax_h3_skill',
  workflow_source: 'https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/papercraft-stop-motion-explainer',
  prompt_brief_provenance: 'maestro_adapted',
  surface: 'huggingface_hub_canvas',
  supported_prompt_schemas: ['base_context_ir', 'ref2va_context_ir', 'freeform'],
  supported_h3_modes: ['t2va', 'fl2va', 'ref2va'],
}

function catalog(updateStatus = 'cached', styles = [workflow]) {
  return {
    source: 'https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills',
    revision: 'official-revision',
    source_revision: 'official-revision',
    checked_at: 123,
    update_status: updateStatus,
    supported_model_types: ['minimax_h3', 'minimax_h3_ref2va'],
    provenance: {
      workflow_identity_source: 'official_minimax_h3_skill',
      workflow_source: 'https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills',
      prompt_brief_provenance: 'maestro_adapted',
      surface: 'huggingface_hub_canvas',
      supported_prompt_schemas: ['base_context_ir', 'ref2va_context_ir', 'freeform'],
      supported_h3_modes: ['t2va', 'fl2va', 'ref2va'],
      supported_model_types: ['minimax_h3', 'minimax_h3_ref2va'],
    },
    styles,
  }
}

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

let controlModulePromise
function loadControlModule() {
  if (controlModulePromise) return controlModulePromise
  controlModulePromise = build({
    stdin: {
      contents: "export { H3StyleWorkflowField } from './src/components/Sidebar/PromptInput.tsx'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'h3-workflow-control-test-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'h3-control-test' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'h3-control-test' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'h3-control-test' }))
        bundle.onLoad({ filter: /.*/, namespace: 'h3-control-test' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export const useEffect = callback => callback()
              export const useRef = value => ({ current: value })
              export const useState = initial => [typeof initial === 'function' ? initial() : initial, () => {}]
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('h3-control-test-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          return { contents: 'export const useStore = selector => selector(globalThis.__maestroH3WorkflowStore)' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return controlModulePromise
}

let storeModulePromise
function loadStoreModule() {
  if (storeModulePromise) return storeModulePromise
  storeModulePromise = build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
      resolveDir: UI_ROOT,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return storeModulePromise
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, reject, resolve }
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

function elementText(value) {
  if (Array.isArray(value)) return value.map(elementText).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  return elementText(value.props?.children)
}

test('catalog model support is exact and selection never mutates prompt text', () => {
  const current = catalog()
  assert.equal(h3StyleWorkflowSupportsModel(current, 'minimax_h3'), true)
  assert.equal(h3StyleWorkflowSupportsModel(current, 'minimax_h3_ref2va'), true)
  assert.equal(h3StyleWorkflowSupportsModel(current, 'minimax_h3_spoof'), false)
  assert.equal(h3StyleWorkflowSupportsModel(current, 'ltx2_22B_distilled_1_1'), false)
  assert.equal(resolveH3StyleWorkflowRequest(current, 'minimax_h3', workflow.id), workflow.id)
  assert.equal(resolveH3StyleWorkflowRequest(current, 'ltx2_22B_distilled_1_1', workflow.id), undefined)
  assert.equal(resolveH3StyleWorkflowRequest(current, 'minimax_h3', 'stale-id'), undefined)

  const canonical = 'integrated_multimodal_description:\n[Shot 1] | audiovisual_description: authored | dialogue_and_vocalizations: <d>[English]Hello</d>\noverall_soundscape: authored'
  assert.equal(stripLegacyH3StylePrefix(canonical), canonical)
  assert.equal(stripLegacyH3StylePrefix(
    `H3 prepared style [Old label]: old client brief\n\n${canonical}`,
  ), canonical)
})

test('pipeline request captures the effective model and workflow together after readiness', async () => {
  const ready = deferred()
  let current = {
    catalog: null,
    model: 'ltx2_22B_distilled_1_1',
    selection: '',
  }
  const pending = (async () => {
    await ready.promise
    return captureH3StyleWorkflowRequest(current.catalog, current.model, current.selection)
  })()

  current = {
    catalog: catalog(),
    model: 'minimax_h3',
    selection: workflow.id,
  }
  ready.resolve()
  const captured = await pending

  current = {
    catalog: catalog(),
    model: 'minimax_h3_ref2va',
    selection: '',
  }
  assert.deepEqual(captured, {
    video_model: 'minimax_h3',
    h3_style_workflow: workflow.id,
  })
})

test('mounted Generate and Director jukeboxes use exact server cards without mutating prompt text', async () => {
  const { H3StyleWorkflowField } = await loadControlModule()
  const selections = []
  let retries = 0
  let prompt = 'authored prompt bytes\n\nremain exact'
  const duplicateLabelWorkflow = {
    ...workflow,
    id: 'paper-collage-explainer-generator',
    description: 'A second exact server description.',
  }
  globalThis.__maestroH3WorkflowStore = {
    h3StyleWorkflowCatalog: catalog('offline_fallback', [workflow, duplicateLabelWorkflow]),
    h3StyleWorkflowCatalogLoading: false,
    h3StyleWorkflowCatalogError: null,
    h3StyleWorkflow: workflow.id,
    setH3StyleWorkflow(id) { selections.push(id) },
    loadH3StyleWorkflowCatalog(force) { if (force) retries += 1 },
    params: { prompt },
  }

  for (const surface of ['Generate', 'Director']) {
    const tree = H3StyleWorkflowField({ effectiveVideoModel: 'minimax_h3', surface })
    const elements = flattenElements(tree)
    const cards = elements.filter(element => element.props?.['data-workflow-id'])
    assert.deepEqual(cards.map(card => card.props['data-workflow-id']), [workflow.id, duplicateLabelWorkflow.id])
    assert.deepEqual(cards.map(card => card.key), [workflow.id, duplicateLabelWorkflow.id])
    assert.equal(cards[0].props['aria-pressed'], true)
    assert.equal(cards[1].props['aria-pressed'], false)
    assert.match(elementText(cards[0]), /Papercraft stop-motion explainer.*Tactile handmade paper explainer metadata\..*Selected/)
    assert.match(elementText(cards[1]), /Papercraft stop-motion explainer.*A second exact server description\./)
    assert.match(elementText(tree), /Choose an optional guide for pacing, framing, and finish/)
    assert.match(elementText(tree), /original recipe may include details this guide does not apply/)
    assert.match(elementText(tree), /Source detailsMiniMax H3 recipe library · Offline fallback catalog · revision official-revision · Maestro interpretation/)
    assert.equal(elements.find(element => element.type === 'a')?.props.href, catalog().source)
    const surprise = elements.find(element => element.type === 'button' && /Surprise me/.test(elementText(element)))
    surprise.props.onClick()
    cards[1].props.onClick()
    const clear = elements.find(element => element.type === 'button' && elementText(element) === 'Clear')
    clear.props.onClick()
    assert.equal(prompt, 'authored prompt bytes\n\nremain exact')
  }
  assert.deepEqual(selections, [
    duplicateLabelWorkflow.id, duplicateLabelWorkflow.id, '',
    duplicateLabelWorkflow.id, duplicateLabelWorkflow.id, '',
  ])
  assert.equal(H3StyleWorkflowField({ effectiveVideoModel: 'minimax_h3_spoof', surface: 'Generate' }), null)

  globalThis.__maestroH3WorkflowStore.h3StyleWorkflowCatalog = catalog('cached', [])
  globalThis.__maestroH3WorkflowStore.h3StyleWorkflow = ''
  const emptyTree = H3StyleWorkflowField({ effectiveVideoModel: 'minimax_h3', surface: 'Generate' })
  const emptyElements = flattenElements(emptyTree)
  assert.match(elementText(emptyTree), /No creative guides are available right now/)
  assert.match(elementText(emptyTree), /No guide selected · prompt only/)
  assert.equal(emptyElements.find(element => element.type === 'button' && /Surprise me/.test(elementText(element)))?.props.disabled, true)
  assert.equal(emptyElements.find(element => elementText(element) === 'Clear')?.props.disabled, true)

  globalThis.__maestroH3WorkflowStore.h3StyleWorkflowCatalog = null
  globalThis.__maestroH3WorkflowStore.h3StyleWorkflowCatalogLoading = true
  const loadingTree = H3StyleWorkflowField({ effectiveVideoModel: 'minimax_h3', surface: 'Generate' })
  assert.equal(loadingTree.type, 'fieldset')
  assert.match(elementText(loadingTree), /Creative guide jukeboxLoading creative guides…/)
  assert.equal(flattenElements(loadingTree).find(element => element.props?.role === 'status')?.props.children, 'Loading creative guides…')

  globalThis.__maestroH3WorkflowStore.h3StyleWorkflowCatalogLoading = false
  globalThis.__maestroH3WorkflowStore.h3StyleWorkflowCatalogError = 'Creative guide catalog unavailable.'
  const errorTree = H3StyleWorkflowField({ effectiveVideoModel: 'minimax_h3', surface: 'Generate' })
  assert.equal(errorTree.type, 'fieldset')
  assert.match(elementText(errorTree), /Creative guide catalog unavailable\.Retry creative guides/)
  flattenElements(errorTree).find(element => element.type === 'button')?.props.onClick()
  assert.equal(retries, 1)
})

test('Surprise is deterministic per revision, follows server order, and avoids the current card', () => {
  const styles = Array.from({ length: 32 }, (_, index) => ({ id: `server-style-${index}` }))
  const first = nextH3StyleWorkflowSurprise(styles, '', 'revision-a')
  assert.equal(first, nextH3StyleWorkflowSurprise(styles, '', 'revision-a'))
  assert.ok(styles.some(style => style.id === first))

  const second = nextH3StyleWorkflowSurprise(styles, first, 'revision-a')
  assert.notEqual(second, first)
  assert.ok(styles.some(style => style.id === second))
  assert.notEqual(nextH3StyleWorkflowSurprise(styles, '', 'revision-b'), '')
  const cycle = new Set()
  let current = ''
  for (let index = 0; index < styles.length; index += 1) {
    current = nextH3StyleWorkflowSurprise(styles, current, 'revision-a')
    cycle.add(current)
  }
  assert.equal(cycle.size, styles.length)
  assert.equal(nextH3StyleWorkflowSurprise([], 'current', 'revision-a'), '')
  assert.equal(nextH3StyleWorkflowSurprise([{ id: 'only' }], 'only', 'revision-a'), 'only')
  assert.equal(h3StyleWorkflowSwatch('papercraft-stop-motion-explainer'), 'paper')
  assert.equal(h3StyleWorkflowSwatch('music-video-subtitle-generator'), 'rhythmic')
  assert.equal(h3StyleWorkflowSwatch('unknown-upstream-style'), h3StyleWorkflowSwatch('unknown-upstream-style'))
})

test('catalog transport preserves fallback and provenance fields without a client catalog', async t => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify(catalog('bundled_fallback')), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
  t.after(() => { globalThis.fetch = originalFetch })

  const result = await fetchH3StyleWorkflows()
  assert.equal(result.source_revision, 'official-revision')
  assert.equal(result.provenance.surface, 'huggingface_hub_canvas')
  assert.deepEqual(result.supported_model_types, ['minimax_h3', 'minimax_h3_ref2va'])
  assert.equal(h3StyleWorkflowCatalogStateLabel(result), 'Bundled fallback catalog')
})

test('store catalog refresh is last-request-wins, clears stale IDs, and runs legacy prompt migration once', async t => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const originalDocument = globalThis.document
  const originalLocalStorage = globalThis.localStorage
  class StorageFake {
    values = new Map([['maestro:h3-prepared-style', 'stale-id']])
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
    removeItem(key) { this.values.delete(key) }
  }
  globalThis.localStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval, alert() {} })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  const first = deferred()
  const second = deferred()
  let requestCount = 0
  globalThis.fetch = async url => {
    assert.equal(String(url), '/api/v1/h3/style-workflows')
    requestCount += 1
    return requestCount === 1 ? first.promise : second.promise
  }
  t.after(() => {
    globalThis.fetch = originalFetch
    globalThis.window = originalWindow
    globalThis.document = originalDocument
    globalThis.localStorage = originalLocalStorage
  })

  const { useStore } = await loadStoreModule()
  const firstLoad = useStore.getState().loadH3StyleWorkflowCatalog(true)
  const secondLoad = useStore.getState().loadH3StyleWorkflowCatalog(true)
  const currentCatalog = { ...catalog(), revision: 'second', source_revision: 'second' }
  second.resolve(Response.json(currentCatalog))
  await secondLoad
  const staleCatalog = { ...catalog(), revision: 'first', source_revision: 'first' }
  first.resolve(Response.json(staleCatalog))
  await firstLoad

  const state = useStore.getState()
  assert.equal(state.h3StyleWorkflowCatalog?.revision, 'second')
  assert.equal(state.h3StyleWorkflow, '')
  assert.match(state.h3StyleWorkflowCatalogError || '', /saved H3 workflow.*cleared/)
  assert.equal(globalThis.localStorage.getItem('maestro:h3-prepared-style'), null)

  const retiredPrefix = 'H3 prepared style [Old label]: old client brief\n\nauthored prompt'
  useStore.getState().setParam('prompt', retiredPrefix)
  useStore.getState().migrateLegacyH3StylePrompt()
  assert.equal(useStore.getState().params.prompt, 'authored prompt')
  assert.equal(globalThis.localStorage.getItem('maestro:h3-style-prefix-migration-v1'), '1')

  const newlyAuthoredMatchingText = 'H3 prepared style [Intentional]: authored syntax\n\nmust remain byte-for-byte'
  useStore.getState().setParam('prompt', newlyAuthoredMatchingText)
  useStore.getState().migrateLegacyH3StylePrompt()
  assert.equal(useStore.getState().params.prompt, newlyAuthoredMatchingText)
})

test('Generate, plan preview, Director v2, and pipeline start send ID only while preserving canonical prompt and visual style', async t => {
  const originalFetch = globalThis.fetch
  const calls = []
  const directorRequestId = '00000000-0000-4000-8000-000000000701'
  const projectInstance = '7'.repeat(64)
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    const body = init.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url, body })
    if (url.endsWith('/api/v1/llm/prepare')) {
      return new Response(JSON.stringify({ operation_id: 'h3-workflow-prepare', status: 'ready' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/api/v1/generate')) return Response.json({ job_id: 'job-1', status: 'queued' })
    if (url.endsWith('/api/v1/generate/plan')) return Response.json({ requires_review: false, plan: null, effective_model_type: 'minimax_h3', requirements: {}, h3_estimate: null, segment_count_estimate: null })
    if (url.includes('/api/v1/llm/models?')) return Response.json({ models: [], guides: [], project_instance: projectInstance })
    if (url.endsWith('/api/v1/director/v2/plan')) return Response.json({
      request_id: directorRequestId,
      operation_kind: 'director_preview',
      status: 'completed', phase: 'completed', stage: 'completed',
      pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
      partial_text: '', generated_tokens_approx: 0, elapsed_seconds: 0,
      live_tps: null, average_tps: null, result_available: true, retryable: false,
    }, { status: 202 })
    if (url.includes(`/api/v1/llm/operations/director_preview/${directorRequestId}/result?`)) {
      return Response.json({ clip_plans: [], production_plan: {}, skill_type: 'music_video' })
    }
    if (url.endsWith('/api/v1/director/pipeline/start')) return Response.json({ pipeline_id: 'pipeline-1' })
    throw new Error(`Unexpected request ${url}`)
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const canonicalPrompt = 'integrated_multimodal_description:\n[Shot 1] | audiovisual_description: authored | dialogue_and_vocalizations: <d>[English]Hello</d>\noverall_soundscape: authored'
  await submitGeneration({ model_type: 'minimax_h3', prompt: canonicalPrompt, h3_style_workflow: workflow.id })
  await previewGenerationPlan({ model_type: 'minimax_h3', prompt: canonicalPrompt, h3_style_workflow: workflow.id })
  await directorV2Plan({
    request_id: directorRequestId,
    project_instance: projectInstance,
    workspace: 'project one',
    skill_type: 'music_video',
    video_model: 'minimax_h3',
    visual_style: 'hand-painted realism',
    h3_style_workflow: workflow.id,
  }, { projectInstance })
  await startPipeline({
    workspace: 'project one',
    video_model: 'minimax_h3',
    visual_style: 'hand-painted realism',
    h3_style_workflow: workflow.id,
  })

  const bodies = calls.filter(call => (
    call.body !== undefined && !call.url.endsWith('/api/v1/llm/prepare')
  )).map(call => call.body)
  assert.equal(bodies.length, 4)
  for (const body of bodies) {
    assert.equal(body.h3_style_workflow, workflow.id)
    assert.equal(body.prompt_brief, undefined)
    assert.equal(body.h3_style_workflow_metadata, undefined)
  }
  assert.equal(bodies[0].prompt, canonicalPrompt)
  assert.equal(bodies[1].prompt, canonicalPrompt)
  assert.equal(bodies[2].visual_style, 'hand-painted realism')
  assert.equal(bodies[2].request_id, directorRequestId)
  assert.equal(bodies[2].project_instance, projectInstance)
  assert.equal(bodies[3].visual_style, 'hand-painted realism')
})

test('source removes client prefix authoring and wires catalog-gated requests only', async () => {
  const [promptInput, director, store, client] = await Promise.all([
    readFile(new URL('../src/components/Sidebar/PromptInput.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
  ])
  assert.doesNotMatch(promptInput, /H3_PREPARED_STYLES|H3 prepared style \[|applyPreparedStyle|startsWith\(['"]minimax_h3/)
  assert.doesNotMatch(promptInput, /Math\.random/)
  assert.match(promptInput, /nextH3StyleWorkflowSurprise\(styles, selection, sourceRevision\)/)
  assert.match(promptInput, /H3StyleWorkflowField effectiveVideoModel=\{effectiveVideoModel\} surface="Generate"/)
  assert.match(director, /H3StyleWorkflowField effectiveVideoModel=\{effectiveVideoModel\} surface="Director"/)
  assert.match(client, /supported_model_types: string\[\]/)
  assert.equal((store.match(/_runDirectorV2Preview\(\{/g) || []).length, 3)
  for (const match of store.matchAll(/_runDirectorV2Preview\(\{([\s\S]*?)\}, lifecycle\)/g)) {
    assert.match(match[1], /h3_style_workflow: resolveH3StyleWorkflowRequest\(/)
    assert.match(match[1], /visual_style:/)
  }
  assert.match(store, /delete params\.h3_style_workflow[\s\S]*?params\.h3_style_workflow = h3StyleWorkflow/)
  assert.match(store, /const h3WorkflowRequest = captureH3StyleWorkflowRequest\(/)
  assert.match(store, /h3_style_workflow: h3WorkflowRequest\.h3_style_workflow/)
  assert.match(store, /H3_STYLE_PREFIX_MIGRATION_KEY/)
  assert.doesNotMatch(store, /prompt_brief:/)
})

test('Director image progress uses friendly known states and a neutral unknown fallback', async () => {
  const director = await readFile(
    new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url),
    'utf8',
  )
  for (const copy of [
    "generating: 'Creating image'",
    "polling: 'Waiting for image'",
    "downloading: 'Saving image'",
    "done: 'Image ready'",
    "error: 'Image needs attention'",
    "|| 'Working on image'",
  ]) assert.match(director, new RegExp(copy.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(director, /imageStatusCopy\(imageGenProgress\.status\)/)
  assert.doesNotMatch(director, /` — \$\{imageGenProgress\.status\}`/)
  assert.match(director, /<summary[^>]*>Technical details<\/summary>[\s\S]*?Reported state: \{imageGenProgress\.status\}/)
})
