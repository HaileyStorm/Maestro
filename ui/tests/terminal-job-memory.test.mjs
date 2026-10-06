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
  let jobStatusResponse = null
  let cancellationResponse = null
  const cancellationRequests = []
  const statusRequests = []
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
    else if (url.endsWith('/workspaces/active')) {
      active = JSON.parse(init.body).name
      body = { status: 'ok', active }
    }
    else if (url.endsWith('/jobs')) {
      if (remote && !active) return Response.json({ detail: 'Select a project' }, { status: 423 })
      body = { jobs: statuses }
    }
    else if (/\/status\/[^/]+$/.test(url) && jobStatusResponse) {
      statusRequests.push({ url, method: init.method || 'GET' })
      return jobStatusResponse(url)
    }
    else if (/\/cancel\/[^/]+$/.test(url) && cancellationResponse) {
      cancellationRequests.push({ url, method: init.method })
      return cancellationResponse(url)
    }
    else if (url.endsWith('/account/nonce')) body = { nonce: 'test', purpose: JSON.parse(init.body).purpose }
    else if (url.endsWith('/account/logout')) {
      context = accountContext(null)
      body = { status: 'logged_out' }
    } else throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  }
  const bundled = await build({
    stdin: {
      contents: "export { useStore, currentAccountIdentityEpoch } from './src/stores/useStore.ts'",
      resolveDir: new URL('..', import.meta.url).pathname, loader: 'js',
    },
    bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
  })
  const module = `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`
  const memory = await loadMemory()
  let sequence = 0
  const accountEpochs = new WeakMap()
  async function freshStore() {
    const { useStore, currentAccountIdentityEpoch } = await import(`${module}#terminal-${++sequence}`)
    accountEpochs.set(useStore, currentAccountIdentityEpoch)
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
  await t.test('selecting a project after restart rediscovers its held job without reloading', async () => {
    memory.clearTerminalJobs()
    context = accountContext('a')
    remote = true
    active = ''
    workspaces = [{ name: 'project-a', project_permissions: ['project.read'] }]
    statuses = [{
      job_id: 'held-after-crash', status: 'queued', workspace: 'project-a',
      progress: 0, step: 0, total_steps: 0, phase: '',
      message: 'Unlock this project to resume', output_files: [], error: null,
      queue_held: true, recovery_state: 'blocked_remote_reauth',
      recovery_actions: ['resume'],
    }]
    const store = await freshStore()
    store.setState({ loadOutputs: async () => true, _pollRecoveredJob: () => {} })
    await store.getState().loadAccessContext(false)
    await store.getState().loadWorkspaces()
    await store.getState().reconnectJobs()
    assert.deepEqual(store.getState().jobs, [], 'the server hides jobs before project selection')
    assert.equal(await store.getState().switchWorkspace('project-a'), true)
    assert.equal(store.getState().jobs[0]?.id, 'held-after-crash')
    assert.equal(store.getState().jobs[0]?.recoveryState, 'blocked_remote_reauth')
    statuses = []
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

  const timers = new Map()
  let timerId = 0
  window.setTimeout = (callback, delay) => {
    const id = ++timerId
    timers.set(id, { callback, delay })
    return id
  }
  window.clearTimeout = id => timers.delete(id)
  const drain = () => new Promise(resolve => setImmediate(resolve))
  const jobStatus = status => Response.json({
    job_id: 'accepted-repeat', workspace: 'project-a', status,
    progress: status === 'completed' ? 100 : 0, step: 0, total_steps: 0,
    phase: status, message: status, error: null,
    output_files: status === 'completed' ? ['repeats.mp4'] : [],
    generation_mode: 'tool_editor_export', model_type: 'blender',
  })
  async function compositionStore() {
    context = accountContext('a')
    memory.clearTerminalJobs()
    const store = await freshStore()
    await store.getState().loadAccessContext(false)
    const loads = []
    store.setState({ activeWorkspace: 'project-a', jobs: [],
      loadOutputs: async () => { loads.push(store.getState().activeWorkspace) },
      refreshOutputs: async () => {},
    })
    statusRequests.length = 0
    return { store, loads, epoch: accountEpochs.get(store)() }
  }
  const track = ({ store, epoch }) => store.getState().trackAcceptedGenerationJob(
    { job_id: 'accepted-repeat', status: 'queued' }, 'project-a', epoch,
  )
  await t.test('accepted composition completed before discovery refreshes Gallery from its exact status', async () => {
    const fixture = await compositionStore()
    jobStatusResponse = () => jobStatus('completed')
    track(fixture)
    await drain()
    assert.deepEqual(fixture.store.getState().jobs, [])
    assert.equal(fixture.store.getState().isGenerating, false)
    assert.deepEqual(fixture.loads, ['project-a'])
    assert.deepEqual(statusRequests.map(request => request.method), ['GET'])
    assert.match(statusRequests[0].url, /\/status\/accepted-repeat$/)
    assert.equal(timers.size, 0)
  })
  await t.test('accepted queued composition completes promptly without a running or active-list snapshot', async () => {
    const fixture = await compositionStore()
    let complete = false
    jobStatusResponse = () => jobStatus(complete ? 'completed' : 'queued')
    track(fixture)
    await drain()
    assert.equal(fixture.store.getState().jobs[0].status, 'queued')
    const [id, timer] = [...timers][0]
    assert.equal(timer.delay, 2000)
    timers.delete(id)
    complete = true
    timer.callback()
    await drain()
    assert.equal(statusRequests.length, 2)
    assert.deepEqual(fixture.loads, ['project-a'])
    assert.deepEqual(fixture.store.getState().jobs, [])
    assert.equal(timers.size, 0)
  })
  await t.test('transient accepted-job GET failure retries status without resubmitting or losing the card', async () => {
    const fixture = await compositionStore()
    let calls = 0
    jobStatusResponse = () => {
      if (++calls === 1) throw new Error('temporary disconnect')
      return jobStatus('completed')
    }
    track(fixture)
    await drain()
    assert.equal(fixture.store.getState().jobs[0].id, 'accepted-repeat')
    const [id, timer] = [...timers][0]
    assert.equal(timer.delay, 2000)
    timers.delete(id)
    timer.callback()
    await drain()
    assert.deepEqual(statusRequests.map(request => request.method), ['GET', 'GET'])
    assert.deepEqual(fixture.loads, ['project-a'])
    assert.equal(timers.size, 0)
  })
  await t.test('accepted tracking replaces an existing slow poll and ignores its late queued response', async () => {
    const fixture = await compositionStore()
    let resolveOldStatus
    let calls = 0
    jobStatusResponse = () => ++calls === 1
      ? new Promise(resolve => { resolveOldStatus = resolve })
      : jobStatus('completed')
    fixture.store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'queued' }] })
    fixture.store.getState()._pollRecoveredJob('accepted-repeat')
    track(fixture)
    await drain()
    resolveOldStatus(jobStatus('queued'))
    await drain()
    assert.deepEqual(fixture.store.getState().jobs, [])
    assert.deepEqual(fixture.loads, ['project-a'])
    assert.equal(calls, 2)
    assert.equal(timers.size, 0)
  })
  await t.test('accepted-job registration and pending status responses respect account and project drift', async () => {
    for (const drift of ['account', 'project']) {
      const fixture = await compositionStore()
      let resolveStatus
      jobStatusResponse = () => new Promise(resolve => { resolveStatus = resolve })
      track(fixture)
      if (drift === 'account') {
        context = accountContext('b')
        await fixture.store.getState().loadAccountContext(false)
      } else fixture.store.setState({ activeWorkspace: 'project-b', jobs: [] })
      resolveStatus(jobStatus('completed'))
      await drain()
      track(fixture)
      assert.deepEqual(fixture.store.getState().jobs, [])
      assert.deepEqual(fixture.loads, [])
      assert.equal(statusRequests.length, 1, 'stale acknowledgement must not start a new status request')
      assert.equal(timers.size, 0)
    }
  })
  await t.test('ordinary recovered queued jobs retain their queue-driven safety cadence', async () => {
    const { store } = await compositionStore()
    jobStatusResponse = () => jobStatus('queued')
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'queued' }] })
    store.getState()._pollRecoveredJob('accepted-repeat', 'project-a')
    await drain()
    assert.equal([...timers.values()][0].delay, 300000)
    context = accountContext('b')
    await store.getState().loadAccountContext(false)
    assert.equal(timers.size, 0)
  })
  await t.test('Stop keeps observing while pending and accepts completion instead of inventing cancellation', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running' }], isGenerating: true })
    let resolveCancel
    cancellationRequests.length = 0
    cancellationResponse = () => new Promise(resolve => { resolveCancel = resolve })
    jobStatusResponse = () => jobStatus('running')
    store.getState()._pollRecoveredJob('accepted-repeat', 'project-a')
    await drain()
    store.getState().stopGeneration('accepted-repeat')
    store.getState().stopGeneration('accepted-repeat')
    await drain()
    assert.equal(store.getState().jobs[0].status, 'running')
    assert.equal(store.getState().isGenerating, true)
    assert.equal(cancellationRequests.length, 1, 'repeated clicks coalesce while the request is pending')
    const [id, timer] = [...timers][0]
    timers.delete(id)
    jobStatusResponse = () => jobStatus('completed')
    timer.callback()
    await drain()
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(loads, ['project-a'])
    assert.equal(store.getState().isGenerating, false)
    assert.equal(timers.size, 0)
    const reads = statusRequests.length
    resolveCancel(Response.json({ status: 'completed', was_running: false }))
    await drain()
    assert.equal(statusRequests.length, reads, 'late Stop response cannot resurrect a settled card')
  })
  await t.test('ambiguous Stop failure retains status observation and never resends the POST', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running' }], isGenerating: true })
    cancellationRequests.length = 0
    cancellationResponse = () => { throw new Error('connection lost after dispatch') }
    jobStatusResponse = () => jobStatus('running')
    store.getState().stopGeneration('accepted-repeat')
    await drain()
    assert.equal(store.getState().jobs[0].status, 'running')
    assert.equal(store.getState().isGenerating, true)
    const [id, timer] = [...timers][0]
    assert.equal(timer.delay, 2000)
    timers.delete(id)
    jobStatusResponse = () => jobStatus('cancelled')
    timer.callback()
    await drain()
    assert.equal(store.getState().jobs[0].status, 'cancelled')
    assert.equal(store.getState().isGenerating, false)
    assert.deepEqual(loads, ['project-a'])
    assert.equal(cancellationRequests.length, 1)
    assert.equal(timers.size, 0)
  })
  await t.test('Stop all preserves terminal cards and reconciles mixed completion and held-job cancellation', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [
      { ...failedJob, id: 'finished' },
      { ...failedJob, id: 'won-completion', status: 'running' },
      { ...failedJob, id: 'held', status: 'queued', held: true },
    ], isGenerating: true })
    cancellationRequests.length = 0
    cancellationResponse = () => Response.json({ status: 'cancelled', was_running: true })
    jobStatusResponse = url => {
      const id = url.split('/').at(-1)
      return Response.json({ job_id: id, workspace: 'project-a',
        status: id === 'won-completion' ? 'completed' : 'cancelled',
        progress: 100, step: 0, total_steps: 0, phase: '', message: '', error: null,
        output_files: id === 'won-completion' ? ['finished.mp4'] : [] })
    }
    store.getState().stopGeneration()
    await drain()
    assert.deepEqual(cancellationRequests.map(r => r.url.split('/').at(-1)), ['won-completion', 'held'])
    assert.deepEqual(store.getState().jobs.map(j => [j.id, j.status]), [['finished', 'failed'], ['held', 'cancelled']])
    assert.equal(store.getState().isGenerating, false)
    assert.equal(loads.length, 2)
    assert.equal(timers.size, 0)
  })
  await t.test('pending Stop cannot reconcile into a changed account, project roundtrip or replacement job', async () => {
    for (const drift of ['account', 'project-roundtrip', 'incarnation']) {
      const { store, loads } = await compositionStore()
      store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running', createdAt: 1 }] })
      let resolveCancel
      cancellationResponse = () => new Promise(resolve => { resolveCancel = resolve })
      let resolveStatus
      jobStatusResponse = () => new Promise(resolve => { resolveStatus = resolve })
      store.getState().stopGeneration('accepted-repeat')
      if (drift === 'account') {
        context = accountContext('b')
        await store.getState().loadAccountContext(false)
      } else if (drift === 'project-roundtrip') {
        store.setState({ activeWorkspace: 'project-b' })
        store.setState({ activeWorkspace: 'project-a' })
      } else store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running', createdAt: 2 }] })
      const before = store.getState().jobs
      resolveCancel(Response.json({ status: 'completed', was_running: false }))
      resolveStatus(jobStatus('completed'))
      await drain()
      assert.strictEqual(store.getState().jobs, before)
      assert.equal(statusRequests.length, 1, 'dispatch observer must not continue into the changed scope')
      assert.deepEqual(loads, [])
      assert.equal(timers.size, 0)
    }
  })
  await t.test('plan Stop observes automatic completion and ambiguous failure through the canonical poller', async () => {
    for (const failure of [false, true]) {
      const { store, loads } = await compositionStore()
      store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'waiting_for_plan_approval' }],
        pendingH3PlanJobId: 'accepted-repeat', pendingH3PlanWorkspace: 'project-a', isGenerating: true })
      cancellationResponse = () => failure
        ? Response.json({ detail: 'Connection failed' }, { status: 500 })
        : Response.json({ status: 'completed', was_running: false })
      jobStatusResponse = () => jobStatus('completed')
      await store.getState().cancelH3Plan()
      await drain()
      assert.deepEqual(store.getState().jobs, [])
      assert.deepEqual(loads, ['project-a'])
      assert.equal(store.getState().isGenerating, false)
      assert.equal(store.getState().pendingH3PlanJobId, null)
      assert.equal(store.getState().h3PlanReviewError, null)
      assert.equal(timers.size, 0)
    }
  })
  await t.test('poller hydrates a provisional timestamp once and refuses a different server incarnation', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running', createdAt: 123 }] })
    let state = 'running'
    let created = 100
    jobStatusResponse = async () => Response.json({ ...(await jobStatus(state).json()), created_at: created })
    store.getState()._pollRecoveredJob('accepted-repeat', 'project-a')
    await drain()
    assert.equal(store.getState().jobs[0].createdAt, 100)
    const tick = async () => {
      const [id, timer] = [...timers][0]
      timers.delete(id)
      timer.callback()
      await drain()
    }
    state = 'completed'
    created = 200
    await tick()
    assert.equal(store.getState().jobs[0].status, 'running')
    assert.equal(store.getState().jobs[0].createdAt, 100)
    assert.deepEqual(loads, [])
    created = 100
    await tick()
    assert.deepEqual(store.getState().jobs, [])
    assert.deepEqual(loads, ['project-a'])
    assert.equal(timers.size, 0)
  })
  await t.test('pending poll cannot publish after a project changes away and back', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'running' }] })
    let resolveStatus
    jobStatusResponse = () => new Promise(resolve => { resolveStatus = resolve })
    store.getState()._pollRecoveredJob('accepted-repeat', 'project-a')
    store.setState({ activeWorkspace: 'project-b' })
    store.setState({ activeWorkspace: 'project-a' })
    resolveStatus(jobStatus('completed'))
    await drain()
    assert.equal(store.getState().jobs[0].status, 'running')
    assert.deepEqual(loads, [])
    assert.equal(timers.size, 0)
  })
  await t.test('held queued Stop reaches canonical cancellation while the POST acknowledgement stays pending', async () => {
    const { store, loads } = await compositionStore()
    store.setState({ jobs: [{ ...failedJob, id: 'accepted-repeat', status: 'queued', held: true }] })
    let resolveCancel
    cancellationRequests.length = 0
    cancellationResponse = () => new Promise(resolve => { resolveCancel = resolve })
    jobStatusResponse = () => jobStatus('queued')
    store.getState().stopGeneration('accepted-repeat')
    await drain()
    const [id, timer] = [...timers][0]
    assert.equal(timer.delay, 2000)
    timers.delete(id)
    jobStatusResponse = () => jobStatus('cancelled')
    timer.callback()
    await drain()
    assert.equal(store.getState().jobs[0].status, 'cancelled')
    assert.deepEqual(loads, ['project-a'])
    resolveCancel(Response.json({ status: 'cancelled', was_running: false }))
    await drain()
    assert.equal(cancellationRequests.length, 1)
    assert.equal(timers.size, 0)
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
