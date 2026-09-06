import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { build, transform } from 'esbuild'

const moduleUrl = new URL('../src/lib/terminalJobMemory.ts', import.meta.url)

async function loadMemory() {
  const source = await readFile(moduleUrl, 'utf8')
  const result = await transform(source, {
    format: 'esm',
    loader: 'ts',
    target: 'es2022',
  })
  return import(`data:text/javascript;base64,${Buffer.from(result.code).toString('base64')}`)
}

test('denied storage property does not prevent startup or job updates', async () => {
  const { loadTerminalJobs, persistTerminalJobs } = await loadMemory()
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    get() { throw new DOMException('Storage denied', 'SecurityError') },
  })
  try {
    assert.deepEqual(loadTerminalJobs('account:a'), [])
    assert.doesNotThrow(() => persistTerminalJobs([], 'account:a'))
  } finally {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original)
    else delete globalThis.sessionStorage
  }
})

class StorageFake {
  values = new Map()
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

const failedJob = {
  id: 'failed-a', status: 'failed', progress: 0, step: 0, totalSteps: 1,
  phase: '', message: 'Generation failed.', outputFiles: [], error: 'Generation failed.',
  workspace: 'project-a',
}

function accountContext(id) {
  return {
    enabled: true, authenticated: Boolean(id), account: id ? { id, role: 'user' } : null,
    capabilities: id ? ['account.self'] : [], reauthenticated: false,
    passkey_authentication_available: false, activation_state: 'ready',
  }
}

test('terminal memory requires matching verified identity and never adopts unscoped records', async t => {
  const memory = await loadMemory()
  const original = globalThis.sessionStorage
  globalThis.sessionStorage = new StorageFake()
  t.after(() => { globalThis.sessionStorage = original })
  const scope = memory.terminalJobScope(accountContext('a'))
  assert.equal(memory.terminalJobScope(null), null)
  assert.equal(memory.terminalJobScope(accountContext(null)), null)
  assert.equal(memory.terminalJobScope({ enabled: false }), 'local')
  sessionStorage.setItem('maestro.terminal-jobs.v1', JSON.stringify([failedJob]))
  assert.deepEqual(memory.loadTerminalJobs(scope), [])
  memory.persistTerminalJobs([failedJob], scope)
  assert.equal(sessionStorage.getItem('maestro.terminal-jobs.v1'), null)
  assert.deepEqual(memory.loadTerminalJobs('account:b'), [])
  assert.deepEqual(memory.loadTerminalJobs('local'), [])
  assert.deepEqual(memory.loadTerminalJobs(), [])
  memory.persistTerminalJobs([])
  assert.equal(memory.loadTerminalJobs(scope)[0].id, failedJob.id)
  memory.clearTerminalJobs()
  assert.deepEqual(memory.loadTerminalJobs(scope), [])
})

test('real store hydrates failures after account bootstrap and scrubs on identity change', async t => {
  const original = Object.fromEntries(['window', 'document', 'localStorage', 'sessionStorage', 'fetch']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  t.after(() => {
    for (const [key, descriptor] of Object.entries(original)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })
  globalThis.localStorage = new StorageFake()
  globalThis.sessionStorage = new StorageFake()
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval })
  globalThis.document = Object.assign(new EventTarget(), { hidden: false })
  let context = accountContext('a')
  let statuses = []
  let remote = false
  let workspaces = [{ name: 'project-a', unlocked: true, project_permissions: ['project.read'] }]
  let active = 'project-a'
  let workspaceResponse = null
  const mainSource = await readFile(new URL('../src/components/MainContent/MainContent.tsx', import.meta.url), 'utf8')
  const effectStart = mainSource.indexOf('useEffect(() => {\n    if (queuePollingReady) return')
  assert.notEqual(effectStart, -1)
  const effectEnd = mainSource.indexOf('}, [queuePollingReady])', effectStart)
  assert.notEqual(effectEnd, -1)
  const resetQueue = new Function('useStore', 'queuePollingReady', 'queuePollSequence', 'queuePollAbort', 'setQueueTabSnapshot',
    mainSource.slice(effectStart + 'useEffect(() => {'.length, effectEnd))
  function noSelectionEffect(store) {
    resetQueue(store, false, { current: 0 }, { current: null }, () => {})
  }
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    let body
    if (url.endsWith('/access-context')) body = { remote, accounts: context, account_project_access_active: context.enabled === true }
    else if (url.endsWith('/account/context')) body = context
    else if (url.endsWith('/workspaces')) {
      if (workspaceResponse) return workspaceResponse()
      body = { workspaces, active }
    }
    else if (url.endsWith('/jobs')) body = { jobs: statuses }
    else if (url.endsWith('/account/nonce')) body = { nonce: 'test', purpose: JSON.parse(init.body).purpose }
    else if (url.endsWith('/account/logout')) {
      context = accountContext(null)
      body = { status: 'logged_out' }
    } else throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  }
  const bundled = await build({
    stdin: {
      contents: "export { useStore } from './src/stores/useStore.ts'",
      resolveDir: new URL('..', import.meta.url).pathname, loader: 'js',
    },
    bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
  })
  const module = `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`
  const memory = await loadMemory()
  let sequence = 0
  async function freshStore() {
    const { useStore } = await import(`${module}#terminal-${++sequence}`)
    useStore.setState({
      loadModelOptions: async () => {}, loadPresets: async () => {},
      resumeEnhancePrompt: async () => {}, resumeDirectorPreview: async () => {},
      reconnectDirectorPreparation: async () => {}, loadOutputs: async () => {},
    })
    return useStore
  }
  await t.test('same-account reload waits for identity, then survives an empty active-job snapshot', async () => {
    memory.persistTerminalJobs([failedJob], 'account:a')
    const store = await freshStore()
    assert.deepEqual(store.getState().jobs, [])
    await store.getState().loadAccountContext(false) // drawer before access bootstrap
    assert.equal(memory.loadTerminalJobs('account:a').length, 1)
    await store.getState().loadAccessContext(false)
    await store.getState().loadAccountContext(false)
    assert.deepEqual(store.getState().jobs, [])
    await store.getState().loadWorkspaces()
    await store.getState().reconnectJobs()
    assert.equal(store.getState().jobs[0].id, failedJob.id)
    assert.equal(memory.loadTerminalJobs('account:a').length, 1)
    statuses = [{ job_id: failedJob.id, status: 'completed', progress: 1, output_files: [] }]
    await store.getState().reconnectJobs()
    assert.equal(store.getState().jobs[0].status, 'completed')
    assert.deepEqual(memory.loadTerminalJobs('account:a'), [])
    statuses = []
  })
  await t.test('different-account reload cannot restore old cards', async () => {
    memory.persistTerminalJobs([failedJob], 'account:a')
    context = accountContext('b')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('account:a'), [])
  })
  await t.test('live account switch and logout erase prior-account memory', async () => {
    context = accountContext('a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    await store.getState().loadWorkspaces()
    store.setState({ jobs: [failedJob] })
    context = accountContext('b')
    await store.getState().loadAccountContext(false)
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('account:a'), [])
    store.setState({ jobs: [{ ...failedJob, id: 'failed-b', workspace: 'project-b' }] })
    await store.getState().logoutAccount()
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('account:b'), [])
  })
  await t.test('explicit accounts-disabled bootstrap restores only local memory', async () => {
    context = { ...accountContext(null), enabled: false, activation_state: 'disabled' }
    memory.persistTerminalJobs([failedJob], 'local')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    assert.deepEqual(store.getState().jobs, [])
    await store.getState().loadWorkspaces()
    assert.equal(store.getState().jobs[0].id, failedJob.id)
    context = accountContext(null)
    await store.getState().loadAccessContext(false)
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('local'), [])
  })
  await t.test('same-account reload removes absent and read-revoked inactive projects', async () => {
    context = accountContext('a')
    remote = false // local account access must enforce membership too
    active = ''
    workspaces = [
      { name: 'project-a', project_permissions: ['project.list'] },
      { name: 'allowed', project_permissions: ['project.read'] },
    ]
    memory.persistTerminalJobs([
      failedJob, { ...failedJob, id: 'missing', workspace: 'removed' },
      { ...failedJob, id: 'allowed', workspace: 'allowed' },
    ], 'account:a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    assert.deepEqual(store.getState().jobs, [])
    noSelectionEffect(store)
    assert.equal(memory.loadTerminalJobs('account:a').length, 3)
    await store.getState().loadWorkspaces()
    assert.deepEqual(store.getState().jobs.map(job => job.id), ['allowed'])
    assert.deepEqual(memory.loadTerminalJobs('account:a').map(job => job.id), ['allowed'])
    workspaces = [{ name: 'allowed', project_permissions: ['project.list'] }]
    await store.getState().loadWorkspaces()
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('account:a'), [])
  })
  await t.test('legacy remote reload rejects expired unlocks and retains authorized no-selection cards', async () => {
    context = { ...accountContext(null), enabled: false }
    remote = true
    active = ''
    workspaces = [{ name: 'project-a', password_protected: true, unlocked: false }]
    memory.persistTerminalJobs([failedJob], 'local')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    noSelectionEffect(store)
    assert.deepEqual(store.getState().jobs, [])
    assert.equal(memory.loadTerminalJobs('local').length, 1)
    await store.getState().loadWorkspaces()
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('local'), [])

    context = accountContext('a')
    workspaces = [{ name: 'project-a', project_permissions: ['project.read'] }]
    memory.persistTerminalJobs([failedJob], 'account:a')
    const accountStore = await freshStore()
    await accountStore.getState().loadAccessContext(false)
    noSelectionEffect(accountStore)
    assert.deepEqual(accountStore.getState().jobs, [])
    await accountStore.getState().loadWorkspaces()
    noSelectionEffect(accountStore)
    assert.equal(accountStore.getState().activeWorkspace, '')
    assert.equal(accountStore.getState().jobs[0].id, failedJob.id)
    assert.equal(memory.loadTerminalJobs('account:a').length, 1)
  })
  await t.test('only the newest workspace response may publish or erase pending recovery', async () => {
    context = accountContext('a')
    memory.persistTerminalJobs([failedJob], 'account:a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    const pending = []
    workspaceResponse = () => new Promise(resolve => pending.push(resolve))
    const first = store.getState().loadWorkspaces()
    const second = store.getState().loadWorkspaces()
    pending[1](Response.json({ workspaces, active: '' }))
    assert.equal(await second, true)
    pending[0](Response.json({ workspaces: [], active: '' }))
    assert.equal(await first, false)
    assert.equal(store.getState().jobs[0].id, failedJob.id)
    assert.equal(memory.loadTerminalJobs('account:a').length, 1)

    const oldAccount = store.getState().loadWorkspaces()
    context = accountContext('b')
    await store.getState().loadAccountContext(false)
    const newAccount = store.getState().loadWorkspaces()
    pending[3](Response.json({ workspaces, active: '' }))
    assert.equal(await newAccount, true)
    store.setState({ jobs: [{ ...failedJob, id: 'b-only' }] })
    pending[2](Response.json({ workspaces: [], active: '' }))
    assert.equal(await oldAccount, false)
    assert.deepEqual(store.getState().jobs.map(job => job.id), ['b-only'])
    assert.deepEqual(memory.loadTerminalJobs('account:b').map(job => job.id), ['b-only'])
    workspaceResponse = null
  })
  await t.test('same-account context refresh does not strand a pending workspace response', async () => {
    context = accountContext('a')
    memory.persistTerminalJobs([failedJob], 'account:a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    let resolveWorkspace
    workspaceResponse = () => new Promise(resolve => { resolveWorkspace = resolve })
    const request = store.getState().loadWorkspaces()
    await store.getState().loadAccountContext(false)
    resolveWorkspace(Response.json({ workspaces, active: '' }))
    assert.equal(await request, true)
    assert.equal(store.getState().jobs[0].id, failedJob.id)
    workspaceResponse = null
  })
  await t.test('workspace refresh preserves live tool placeholders but rejects unscoped reload cards', async () => {
    context = accountContext('a')
    remote = false
    active = 'project-a'
    memory.persistTerminalJobs([{ ...failedJob, workspace: undefined }], 'account:a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    await store.getState().loadWorkspaces()
    assert.deepEqual(store.getState().jobs, [])
    const tool = { ...failedJob, id: 'tool', status: 'queued', workspace: undefined }
    store.setState({ jobs: [tool] })
    await store.getState().loadWorkspaces()
    assert.strictEqual(store.getState().jobs[0], tool)
    workspaces = [{ name: 'project-a', project_permissions: ['project.list'] }]
    await store.getState().loadWorkspaces()
    assert.deepEqual(store.getState().jobs, [])
  })
  await t.test('signed-out bootstrap clears storage without exposing prior-account jobs', async () => {
    context = accountContext(null)
    memory.persistTerminalJobs([failedJob], 'account:a')
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(memory.loadTerminalJobs('account:a'), [])
  })
})

test('compactTerminalJob keeps failed cards and drops prompts', async () => {
  const { compactTerminalJob } = await loadMemory()
  const kept = compactTerminalJob({
    id: 'job-1',
    status: 'failed',
    progress: 0,
    step: 0,
    totalSteps: 13,
    phase: 'Denoising',
    message: 'Generation failed during denoising.',
    outputFiles: [],
    error: 'Generation failed during denoising.',
    promptPreview: 'secret scene text',
    activeWindowPrompt: 'secret window text',
    workspace: 'x_test',
    recoveryActions: ['retry'],
  })
  assert.equal(kept?.id, 'job-1')
  assert.equal(kept?.status, 'failed')
  assert.equal(kept?.workspace, 'x_test')
  assert.deepEqual(kept?.recoveryActions, ['retry'])
  assert.equal(kept?.promptPreview, undefined)
  assert.equal(kept?.activeWindowPrompt, undefined)
  assert.equal(compactTerminalJob({
    id: 'job-2',
    status: 'running',
    progress: 0.2,
    step: 1,
    totalSteps: 13,
    phase: '',
    message: '',
    outputFiles: [],
    error: null,
  }), null)
})
