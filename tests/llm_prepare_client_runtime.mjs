import assert from 'node:assert/strict'
import * as api from '../ui/src/api/client.ts'

const originalFetch = globalThis.fetch
const originalCrypto = globalThis.crypto
const originalDocument = globalThis.document
const originalWindow = globalThis.window

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

try {
  // randomUUID is secure-context-only; getRandomValues remains available on
  // supported plain-HTTP LAN pages and must still produce a canonical v4 UUID.
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: {
      getRandomValues(bytes) {
        for (let index = 0; index < bytes.length; index += 1) bytes[index] = index
        return bytes
      },
    },
  })
  assert.match(
    api.createLlmRequestId(),
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  )

  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: originalCrypto,
  })
  globalThis.window = globalThis
  let visibility = 'visible'
  const visibilityTarget = new EventTarget()
  Object.defineProperty(visibilityTarget, 'visibilityState', {
    get: () => visibility,
  })
  globalThis.document = visibilityTarget

  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({
        operation_id: 'prepare-1', status: 'ready', phase: 'ready', retryable: false,
      }, 202)
    }
    if (String(url).endsWith('/api/v1/llm/write-song')) {
      return jsonResponse({ style: 'style', lyrics: 'lyrics', raw: 'raw' })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  await api.writeSong({
    workspace: 'project-a',
    description: 'private creative content',
  })
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    workspace: 'project-a', purpose: 'configured',
  })
  assert.equal(calls[0].init.body.includes('private creative content'), false)
  assert.equal(JSON.parse(calls[1].init.body).description, 'private creative content')

  // Host refusal-corpus capture transmits only the exact selected literal and
  // projects the response to content-free status fields.
  calls.length = 0
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/api/v1/llm/refusal-literals')) {
      return jsonResponse({
        added: true,
        count: 7,
        revision: 'revision-2',
        ignored_extra: 'must not reach the UI',
      })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  const exactMultilineLiteral = '  exact 😀 selected wording\nsecond line\t  '
  const refusalStatus = await api.addLlmRefusalLiteral(exactMultilineLiteral)
  assert.deepEqual(refusalStatus, {
    added: true,
    count: 7,
    revision: 'revision-2',
  })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    literal: exactMultilineLiteral,
  })
  assert.equal(api.validateLlmRefusalLiteral('😀'.repeat(256)), null)
  assert.match(api.validateLlmRefusalLiteral('😀'.repeat(257)), /256/)
  assert.equal(api.validateLlmRefusalLiteral('line one\nline two\t'), null)
  assert.equal(api.validateLlmRefusalLiteral('\ufeff'), null)
  assert.match(api.validateLlmRefusalLiteral('\n\t\r'), /Select refusal wording/)
  assert.match(api.validateLlmRefusalLiteral('word\u0000'), /control character/)
  assert.match(api.validateLlmRefusalLiteral('word\u007f'), /control character/)
  assert.match(api.validateLlmRefusalLiteral('word\ud800'), /control character/)

  // A lost 202 is recovered by the same request id; the actual inference POST
  // is never duplicated when status already exists.
  calls.length = 0
  let chatPosts = 0
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init })
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({
        operation_id: 'prepare-chat', status: 'ready', phase: 'ready', retryable: false,
      }, 202)
    }
    if (String(url).endsWith('/api/v1/llm/chat') && init.method === 'POST') {
      chatPosts += 1
      throw new TypeError('proxy disconnected after accepting request')
    }
    if (String(url).includes('/api/v1/llm/chat/')) {
      return jsonResponse({
        request_id: '00000000-0000-4000-8000-000000000001',
        status: 'completed', phase: 'completed', retryable: false,
        result: { text: 'answer', model_id: 'model', guide_ids: [] },
      })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  const result = await api.llmChat({
    workspace: 'project-a',
    request_id: '00000000-0000-4000-8000-000000000001',
    model_id: 'model',
    messages: [{ role: 'user', content: 'one turn' }],
    guide_ids: [],
  })
  assert.equal(result.text, 'answer')
  assert.equal(chatPosts, 1)
  assert.equal(calls.filter(call => call.url.includes('/api/v1/llm/chat/')).length, 1)

  // Running Chat telemetry reaches the caller as request-scoped partial text,
  // while terminal performance is merged into the committed result.
  visibility = 'hidden'
  const operationStatuses = []
  let operationChecks = 0
  globalThis.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({
        operation_id: 'prepare-progress', status: 'ready', phase: 'ready', retryable: false,
      }, 202)
    }
    if (String(url).endsWith('/api/v1/llm/chat') && init.method === 'POST') {
      return jsonResponse({
        request_id: '00000000-0000-4000-8000-000000000004',
        status: 'running', phase: 'inference', retryable: false,
        partial_text: 'partial answer', attempt: 1, attempt_limit: 2,
        generated_tokens_approx: 2, elapsed_seconds: 0.5, live_tps: 4,
      }, 202)
    }
    if (String(url).includes('/api/v1/llm/chat/')) {
      operationChecks += 1
      return jsonResponse({
        request_id: '00000000-0000-4000-8000-000000000004',
        status: 'completed', phase: 'completed', retryable: false,
        generated_tokens_approx: 4, elapsed_seconds: 1, average_tps: 4,
        result: { text: 'final answer', model_id: 'model', guide_ids: [] },
      })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  const progressiveChat = api.llmChat({
    workspace: 'project-a',
    request_id: '00000000-0000-4000-8000-000000000004',
    model_id: 'model',
    messages: [{
      role: 'user', content: 'progress please',
      attachments: [{ kind: 'image', name: 'browser-only.png' }],
    }],
    guide_ids: [],
  }, undefined, undefined, status => operationStatuses.push(status))
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(operationStatuses.length, 1)
  assert.equal(operationStatuses[0].partial_text, 'partial answer')
  assert.equal(operationStatuses[0].attempt_limit, 2)
  visibility = 'visible'
  visibilityTarget.dispatchEvent(new Event('visibilitychange'))
  const progressiveResult = await progressiveChat
  assert.equal(operationChecks, 1)
  assert.equal(operationStatuses.at(-1).phase, 'completed')
  assert.equal(progressiveResult.average_tps, 4)
  assert.equal(progressiveResult.generated_tokens_approx, 4)

  // The durable request id becomes recoverable immediately before submission,
  // even when an accepted 202 is lost and the browser stops waiting.
  const lostResponseController = new AbortController()
  const lostResponseEvents = []
  globalThis.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      return jsonResponse({
        operation_id: 'prepare-lost-response', status: 'ready', phase: 'ready', retryable: false,
      }, 202)
    }
    if (String(url).endsWith('/api/v1/llm/chat') && init.method === 'POST') {
      lostResponseEvents.push('post')
      lostResponseController.abort()
      throw new TypeError('proxy disconnected after accepting request')
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  await assert.rejects(api.llmChat({
    workspace: 'project-a',
    request_id: '00000000-0000-4000-8000-000000000002',
    model_id: 'model',
    messages: [{ role: 'user', content: 'recover this turn' }],
    guide_ids: [],
  }, lostResponseController.signal, undefined, undefined, () => {
    lostResponseEvents.push('recoverable')
  }), error => error?.name === 'AbortError')
  assert.deepEqual(lostResponseEvents, ['recoverable', 'post'])

  // Resume retries a transient failure on its very first status lookup using
  // the same request id instead of discarding the durable operation record.
  visibility = 'hidden'
  let resumeChecks = 0
  globalThis.fetch = async url => {
    if (!String(url).includes('/api/v1/llm/chat/')) {
      throw new Error(`Unexpected URL: ${url}`)
    }
    resumeChecks += 1
    if (resumeChecks === 1) return jsonResponse({}, 500)
    return jsonResponse({
      request_id: '00000000-0000-4000-8000-000000000003',
      status: 'completed', phase: 'completed', retryable: false,
      result: { text: 'resumed', model_id: 'model', guide_ids: [] },
    })
  }
  const resumed = api.waitForLlmChatOperation(
    '00000000-0000-4000-8000-000000000003',
    'project-a',
  )
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(resumeChecks, 1)
  visibility = 'visible'
  visibilityTarget.dispatchEvent(new Event('visibilitychange'))
  assert.equal((await resumed).text, 'resumed')
  assert.equal(resumeChecks, 2)

  // If a ready preparation expires while the tab is hidden, visibility wakeup
  // revalidates it and a 404 starts a new content-free preparation before the
  // inference request is sent.
  visibility = 'hidden'
  let preparationStarts = 0
  let preparationChecks = 0
  let inferenceCalls = 0
  const preparationBodies = []
  globalThis.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      preparationStarts += 1
      preparationBodies.push(JSON.parse(init.body))
      return jsonResponse({
        operation_id: `prepare-expiry-${preparationStarts}`,
        status: preparationStarts === 1 ? 'preparing' : 'ready',
        phase: preparationStarts === 1 ? 'loading' : 'ready',
        retryable: false,
      }, 202)
    }
    if (String(url).includes('/api/v1/llm/prepare/')) {
      preparationChecks += 1
      return jsonResponse({ detail: 'LLM preparation not found' }, 404)
    }
    if (String(url).endsWith('/api/v1/llm/write-song')) {
      inferenceCalls += 1
      return jsonResponse({ style: 'style', lyrics: 'lyrics', raw: 'raw' })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  const refreshedWrite = api.writeSong({
    workspace: 'project-a',
    description: 'content stays out of preparation',
  })
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(preparationStarts, 1)
  assert.equal(preparationChecks, 0)
  visibility = 'visible'
  visibilityTarget.dispatchEvent(new Event('visibilitychange'))
  await refreshedWrite
  assert.equal(preparationChecks, 1)
  assert.equal(preparationStarts, 2)
  assert.equal(inferenceCalls, 1)
  assert.deepEqual(preparationBodies, [
    { workspace: 'project-a', purpose: 'configured' },
    { workspace: 'project-a', purpose: 'configured' },
  ])

  // A visible timer is suspended if the tab becomes hidden before it fires.
  visibility = 'visible'
  let transitionChecks = 0
  globalThis.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/api/v1/llm/prepare') && init.method === 'POST') {
      return jsonResponse({
        operation_id: 'prepare-transition', status: 'preparing', phase: 'loading', retryable: false,
      }, 202)
    }
    if (String(url).includes('/api/v1/llm/prepare/')) {
      transitionChecks += 1
      return jsonResponse({
        operation_id: 'prepare-transition', status: 'preparing', phase: 'loading', retryable: false,
      })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }
  const transitionController = new AbortController()
  const transitionWait = api.prepareLlmForRequest(
    { workspace: 'project-a', purpose: 'configured' },
    { signal: transitionController.signal },
  )
  await new Promise(resolve => setTimeout(resolve, 20))
  visibility = 'hidden'
  visibilityTarget.dispatchEvent(new Event('visibilitychange'))
  await new Promise(resolve => setTimeout(resolve, 1_050))
  assert.equal(transitionChecks, 0)
  transitionController.abort()
  await assert.rejects(transitionWait, error => error?.name === 'AbortError')

  // Hidden tabs make no repeated status requests; becoming visible or aborting
  // wakes the wait immediately.
  visibility = 'hidden'
  let prepareCalls = 0
  globalThis.fetch = async url => {
    if (String(url).endsWith('/api/v1/llm/prepare')) {
      prepareCalls += 1
      return jsonResponse({
        operation_id: 'prepare-hidden', status: 'preparing', phase: 'loading', retryable: false,
      }, 202)
    }
    throw new Error(`Hidden wait unexpectedly polled ${url}`)
  }
  const controller = new AbortController()
  const hiddenWait = api.prepareLlmForRequest(
    { workspace: 'project-a', purpose: 'configured' },
    { signal: controller.signal },
  )
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(prepareCalls, 1)
  controller.abort()
  await assert.rejects(hiddenWait, error => error?.name === 'AbortError')
} finally {
  globalThis.fetch = originalFetch
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: originalCrypto,
  })
  if (originalDocument === undefined) delete globalThis.document
  else globalThis.document = originalDocument
  if (originalWindow === undefined) delete globalThis.window
  else globalThis.window = originalWindow
}
