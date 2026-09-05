import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

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
    assert.deepEqual(loadTerminalJobs(), [])
    assert.doesNotThrow(() => persistTerminalJobs([]))
  } finally {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original)
    else delete globalThis.sessionStorage
  }
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

test('mergeTerminalJobs restores remembered failures without duplicating live rows', async () => {
  const { mergeTerminalJobs } = await loadMemory()
  const merged = mergeTerminalJobs(
    [{
      id: 'live',
      status: 'running',
      progress: 0.5,
      step: 3,
      totalSteps: 13,
      phase: '',
      message: '',
      outputFiles: [],
      error: null,
    }],
    [{
      id: 'failed',
      status: 'failed',
      progress: 0,
      step: 0,
      totalSteps: 13,
      phase: '',
      message: 'Generation failed during denoising.',
      outputFiles: [],
      error: 'Generation failed during denoising.',
    }, {
      id: 'live',
      status: 'failed',
      progress: 0,
      step: 0,
      totalSteps: 13,
      phase: '',
      message: 'stale',
      outputFiles: [],
      error: 'stale',
    }],
  )
  assert.equal(merged.length, 2)
  assert.equal(merged[0].id, 'live')
  assert.equal(merged[0].status, 'running')
  assert.equal(merged[1].id, 'failed')
  assert.equal(merged[1].status, 'failed')
})
