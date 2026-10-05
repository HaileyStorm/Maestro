import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { transform } from 'esbuild'

import { fetchLlmChatOperation, llmChat, LlmChatRecoveryUnavailableError, LlmChatWaitError, waitForLlmChatOperation } from '../src/api/client.ts'

const requestId = '12345678-1234-4234-8234-123456789abc'
const normalizedId = requestId.replaceAll('-', '')
const workspace = 'private-chat-project'
const params = {
  workspace, request_id: requestId, model_id: 'local-model', guide_ids: ['local-guide'],
  messages: [{ role: 'user', content: 'Consensual adult fiction, violent drama and controversial dialogue.', images: [{ url: 'browser-local-only' }] }],
  image_paths: ['one-use-private-image'], explicit_output: true,
}
const result = { text: 'Canonical local result', model_id: 'local-model', guide_ids: ['local-guide'] }
const completed = (extra = {}) => ({ request_id: normalizedId, status: 'completed', phase: 'completed', retryable: false, result, ...extra })
const running = () => ({ request_id: normalizedId, status: 'running', phase: 'generating', retryable: false, partial_text: 'Partial', attempt: 1, attempt_limit: 2, generated_tokens_approx: 4, elapsed_seconds: 0.2, live_tps: null, average_tps: 20 })
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fixture(t, handler, pollAdvance = 1_000) {
  const original = { fetch: globalThis.fetch, window: globalThis.window, document: globalThis.document, now: Date.now }
  let clock = 0
  const requests = []
  globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
  globalThis.window = { setTimeout(callback) { clock += pollAdvance; queueMicrotask(callback); return 1 }, clearTimeout() {} }
  Date.now = () => clock
  globalThis.fetch = async (url, options = {}) => {
    const request = { url: String(url), method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : undefined }
    requests.push(request)
    if (request.url === '/api/v1/llm/prepare') return response({ operation_id: 'ready-operation', status: 'ready', phase: 'ready', retryable: false })
    return handler(request)
  }
  t.after(() => {
    globalThis.fetch = original.fetch
    Date.now = original.now
    if (original.window === undefined) delete globalThis.window
    else globalThis.window = original.window
    if (original.document === undefined) delete globalThis.document
    else globalThis.document = original.document
  })
  const callbacks = { attempted: 0, acknowledged: [], status: [] }
  const send = () => llmChat(params, undefined, undefined,
    value => callbacks.status.push(value), () => ++callbacks.attempted,
    value => callbacks.acknowledged.push(value))
  return { requests, callbacks, send }
}
const chatPosts = requests => requests.filter(request => request.url === '/api/v1/llm/chat')
const statusReads = requests => requests.filter(request => request.url.startsWith('/api/v1/llm/chat/'))

test('truncated successful Chat ACK reconciles the original ID once and returns only canonical text', async t => {
  const f = fixture(t, request => request.method === 'POST'
    ? new Response('{"request_id":', { status: 202 }) : response(completed()))
  assert.deepEqual(await f.send(), { ...result, generated_tokens_approx: undefined, elapsed_seconds: undefined, average_tps: undefined })
  assert.equal(chatPosts(f.requests).length, 1)
  assert.deepEqual(chatPosts(f.requests)[0].body, { ...params, messages: params.messages.map(({ role, content }) => ({ role, content })) })
  assert.equal(statusReads(f.requests)[0].url, `/api/v1/llm/chat/${requestId}?workspace=${workspace}`)
  assert.equal(f.callbacks.attempted, 1)
  assert.deepEqual(f.callbacks.acknowledged, [completed()])
  assert.deepEqual(f.callbacks.status, [completed()])
})

test('wrong-ID successful Chat ACK cannot acknowledge admission before matching canonical GET', async t => {
  const f = fixture(t, request => request.method === 'POST'
    ? response(completed({ request_id: 'ffffffffffffffffffffffffffffffff', result: { ...result, text: 'Foreign text' } }), 202)
    : response(completed()))
  assert.equal((await f.send()).text, result.text)
  assert.equal(chatPosts(f.requests).length, 1)
  assert.equal(statusReads(f.requests).length, 1)
  assert.deepEqual(f.callbacks.acknowledged, [completed()])
  assert.deepEqual(f.callbacks.status, [completed()])
})

test('malformed successful Chat bodies never supply result or callback acknowledgement', async t => {
  const malformed = [
    null, [], {}, completed({ request_id: ['12345678123442348234123456789abc'] }),
    completed({ request_id: 'ffffffffffffffffffffffffffffffff' }), completed({ status: ['completed'] }),
    completed({ status: 'unknown' }), completed({ phase: ['completed'] }), completed({ retryable: 'false' }),
    completed({ result: [] }), completed({ result: { ...result, text: ['Foreign text'] } }),
    completed({ result: { ...result, model_id: ['local-model'] } }), completed({ result: { ...result, guide_ids: 'local-guide' } }),
    completed({ result: { ...result, guide_ids: [1] } }), completed({ result: null }),
    completed({ partial_text: 1 }), completed({ elapsed_seconds: '1' }), completed({ attempt: 1.5 }),
    completed({ generated_tokens_approx: -1 }), completed({ average_tps: [] }),
    completed({ result: { ...result, elapsed_seconds: -1 } }),
    { ...running(), result }, { ...running(), error: [] },
    { request_id: normalizedId, status: 'failed', phase: 'failed', retryable: true, error: { code: 'chat_failed', message: ['failed'], retryable: true } },
    JSON.stringify(completed({ elapsed_seconds: 'nonfinite' })).replace('"nonfinite"', '1e400'),
    JSON.stringify(completed({ result: { ...result, average_tps: 'nonfinite' } })).replace('"nonfinite"', '1e400'),
  ]
  const f = fixture(t, () => response(null))
  for (const value of malformed) {
    globalThis.fetch = async (url, options = {}) => {
      f.requests.push({ url: String(url), method: options.method || 'GET' })
      if (String(url) === '/api/v1/llm/prepare') return response({ status: 'ready' })
      const status = options.method === 'POST' ? 202 : 200
      return typeof value === 'string' ? new Response(value, { status }) : response(value, status)
    }
    await assert.rejects(f.send(), LlmChatWaitError)
  }
  assert.equal(chatPosts(f.requests).length, malformed.length)
  assert.equal(statusReads(f.requests).length, malformed.length)
  assert.equal(f.callbacks.attempted, malformed.length)
  assert.deepEqual(f.callbacks.acknowledged, [])
  assert.deepEqual(f.callbacks.status, [])
})

test('lost Chat ACK followed by unknown 404 retains pending without automatic resubmission', async t => {
  const f = fixture(t, request => {
    if (request.method === 'POST') throw new TypeError('Connection lost after dispatch')
    return response({ detail: 'Chat request not found' }, 404)
  })
  await assert.rejects(f.send(), LlmChatRecoveryUnavailableError)
  assert.equal(chatPosts(f.requests).length, 1)
  assert.equal(statusReads(f.requests).length, 1)
  assert.equal(f.callbacks.attempted, 1)
  assert.deepEqual(f.callbacks.acknowledged, [])
  assert.deepEqual(f.callbacks.status, [])
})

test('transient Chat status timeout remains recoverable and never resends a POST', async t => {
  const f = fixture(t, request => request.method === 'POST' ? response({}, 503) : response({}, 502), 45 * 60 * 1_000)
  await assert.rejects(f.send(), LlmChatWaitError)
  assert.equal(chatPosts(f.requests).length, 1)
  assert.equal(statusReads(f.requests).length, 1)
  assert.deepEqual(f.callbacks.acknowledged, [])
})

test('initial definitive Chat rejection preserves generic rollback and does not reconcile', async t => {
  const f = fixture(t, () => response({ detail: 'Invalid request shape' }, 400))
  await assert.rejects(f.send(), error => !(error instanceof LlmChatWaitError) && error.message === 'Invalid request shape')
  assert.equal(chatPosts(f.requests).length, 1)
  assert.equal(statusReads(f.requests).length, 0)
  assert.equal(f.callbacks.attempted, 1)
  assert.deepEqual(f.callbacks.acknowledged, [])
})

test('valid Chat running/completed progression and canonical failed operations retain terminal semantics', async t => {
  let final = completed({ request_id: requestId.toUpperCase(), generated_tokens_approx: 8, elapsed_seconds: 1, average_tps: 8 })
  const f = fixture(t, request => request.method === 'POST' ? response(running(), 202) : response(final))
  assert.deepEqual(await f.send(), { ...result, generated_tokens_approx: 8, elapsed_seconds: 1, average_tps: 8 })
  assert.deepEqual(f.callbacks.acknowledged, [running()])
  assert.deepEqual(f.callbacks.status, [running(), final])
  final = { request_id: normalizedId, status: 'failed', phase: 'failed', retryable: true, error: { code: 'chat_failed', message: 'Canonical generation failed', retryable: true } }
  await assert.rejects(f.send(), error => !(error instanceof LlmChatWaitError) && error.message === 'Canonical generation failed')
  assert.equal(chatPosts(f.requests).length, 2)
  assert.equal(f.callbacks.acknowledged.length, 2)
  assert.deepEqual(f.callbacks.status.at(-1), final)
})

test('missing/malformed later Chat status preserves pending while keeping invalid text out of callbacks', async t => {
  let next = response({}, 404)
  const f = fixture(t, request => request.method === 'POST' ? response(running(), 202) : next.clone())
  for (const value of [response({}, 404), new Response('{', { status: 200 }), response(completed({ request_id: 'ffffffffffffffffffffffffffffffff' }))]) {
    next = value
    await assert.rejects(f.send(), LlmChatWaitError)
  }
  assert.equal(chatPosts(f.requests).length, 3)
  assert.deepEqual(f.callbacks.acknowledged, [running(), running(), running()])
  assert.deepEqual(f.callbacks.status, [running(), running(), running()])
  await assert.rejects(waitForLlmChatOperation(requestId, workspace), LlmChatWaitError)
  assert.equal(chatPosts(f.requests).length, 3, 'Resume performs only a read')
  await assert.rejects(fetchLlmChatOperation(requestId, workspace), LlmChatWaitError)
})

test('actual Chat upload cleanup preserves uncertain submissions and releases only unsubmitted or definitively rejected inputs', async () => {
  const source = await readFile(new URL('../src/components/LlmChat.tsx', import.meta.url), 'utf8')
  const start = source.indexOf('function cleanupUnsubmittedUploads(')
  const end = source.indexOf('\nexport function LlmChat()', start)
  assert.ok(start >= 0 && end > start)
  const { code } = await transform(source.slice(start, end), { loader: 'ts' })
  const deleted = []
  const cleanup = new Function('api', `${code}\nreturn cleanupUnsubmittedUploads`)(
    { deleteLlmChatImage: async (project, filename) => { deleted.push({ project, filename }) } },
  )
  const pending = { workspace, admissionAcknowledged: false, submissionAttempted: true, uploadedRefs: ['private-one-use-input'] }
  cleanup(pending)
  assert.deepEqual(pending.uploadedRefs, ['private-one-use-input'])
  assert.deepEqual(deleted, [])
  cleanup(pending, true)
  assert.deepEqual(pending.uploadedRefs, [])
  assert.deepEqual(deleted, [{ project: workspace, filename: 'private-one-use-input' }])
  const accepted = { ...pending, admissionAcknowledged: true, uploadedRefs: ['claimed-input'] }
  cleanup(accepted, true)
  assert.deepEqual(accepted.uploadedRefs, ['claimed-input'], 'validated admission owns its inputs even on terminal failure')
  const unsubmitted = { ...pending, submissionAttempted: false, uploadedRefs: ['not-submitted-input'] }
  cleanup(unsubmitted)
  assert.deepEqual(unsubmitted.uploadedRefs, [])
  assert.deepEqual(deleted.at(-1), { project: workspace, filename: 'not-submitted-input' })
})
