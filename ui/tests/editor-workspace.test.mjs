import assert from 'node:assert/strict'
import test from 'node:test'

import { exportEditorProject, isBackendJobId, openOutputInEditor, saveEditorProject } from '../src/api/client.ts'

test('Editor export job IDs remain usable in Queue and job logs', () => {
  assert.equal(isBackendJobId('a1b2c3d4e5f6471889abcdef01234567'), true)
  assert.equal(isBackendJobId('faceb00c'), true)
  assert.equal(isBackendJobId('a1b2c3d4e5f6471889abcdef01234567/other'), false)
})

test('Editor requests keep the project, source revision and autosave revision together', async () => {
  const previous = globalThis.fetch
  const calls = []
  const project = {
    id: 'cut-1', workspace: 'scene a', revision: 3, name: 'Cut',
    canvas: { width: 1920, height: 1080, fps: 30, background: '#000000' },
    assets: {}, tracks: [],
  }
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) })
    return { ok: true, json: async () => ({ project }) }
  }
  try {
    assert.equal(await openOutputInEditor('scene a', 'clip #1.mp4', 'sha256:abc'), project)
    assert.equal(await saveEditorProject('scene a', project), project)
    assert.match(calls[0].url, /\/projects\/scene%20a\/editor\/projects$/)
    assert.deepEqual(calls[0].body, { output_name: 'clip #1.mp4', output_revision: 'sha256:abc' })
    assert.equal(calls[0].init.method, 'POST')
    assert.match(calls[1].url, /\/projects\/scene%20a\/editor\/projects\/cut-1$/)
    assert.equal(calls[1].body.expected_revision, 3)
    assert.equal(calls[1].init.method, 'PUT')
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor save reports stale revisions as a safe, actionable conflict', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false, status: 409 })
  try {
    await assert.rejects(
      saveEditorProject('scene', { id: 'cut', revision: 2 }),
      error => error.status === 409 && error.message.includes('reopen it'),
    )
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor export submits only the saved project revision to its project route', async () => {
  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return { ok: true, json: async () => ({ job_id: 'export-1', status: 'queued' }) }
  }
  try {
    assert.deepEqual(await exportEditorProject('scene a', { id: 'cut #1', revision: 4 }), {
      job_id: 'export-1', status: 'queued',
    })
    assert.match(calls[0].url, /\/projects\/scene%20a\/editor\/projects\/cut%20%231\/exports$/)
    assert.equal(calls[0].init.method, 'POST')
    assert.deepEqual(JSON.parse(calls[0].init.body), { expected_revision: 4 })
  } finally {
    globalThis.fetch = previous
  }
})

test('Editor export reports a changed draft as an actionable conflict', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => ({ ok: false, status: 409 })
  try {
    await assert.rejects(
      exportEditorProject('scene', { id: 'cut', revision: 2 }),
      error => error.status === 409 && error.message.includes('reopen the video'),
    )
  } finally {
    globalThis.fetch = previous
  }
})
