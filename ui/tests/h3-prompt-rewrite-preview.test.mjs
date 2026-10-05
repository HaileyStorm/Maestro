import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { createHash } from 'node:crypto'

const bundle = await build({ entryPoints: [new URL('../src/lib/h3PromptRewritePreview.ts', import.meta.url).pathname],
  bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent' })
const { parseH3PromptRewritePreview, isCurrentH3PromptRewritePreview } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`)

function fixture() {
  // Commitment independently frozen with Python's public UTF-8 projection.
  return {
    schema_version: 2, request_commitment: 'a'.repeat(64), original_prompt: 'A paper boat drifts. 雨 🌧️',
    candidates: [
      { kind: 'deterministic', text: 'A paper boat drifts. 雨 🌧️', produced_by_runtime: false },
      { kind: 'base', text: 'A boat crosses the pond.\nA low camera follows.', produced_by_runtime: true },
      { kind: 'adapted', text: '静かな池。 <b>Keep this as plain text.</b>', produced_by_runtime: true },
    ], selection: null,
    runtime_evidence: { execution_available: true, base_executed: true, adapter_executed: true,
      fallback_used: false, execution_receipt_sha256: 'b'.repeat(64) },
    commitment: '59683f3c445172e34fb456c87da57c0eacdb2fde4f5a170c235e876689b166db',
  }
}
const binding = () => ({ requestCommitment: 'a'.repeat(64), originalPrompt: 'A paper boat drifts. 雨 🌧️' })

function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  }
  return JSON.stringify(value)
}

test('executed Unicode preview preserves all plain text and is immutable after admission', async () => {
  const input = fixture()
  const pending = parseH3PromptRewritePreview(input, binding())
  input.candidates[1].text = 'Changed while hashing'
  const preview = await pending
  assert.equal(preview.candidates[1].text, 'A boat crosses the pond.\nA low camera follows.')
  assert.equal(preview.candidates[2].text, '静かな池。 <b>Keep this as plain text.</b>')
  assert.equal(preview.selection, null)
  assert.equal(preview.original_prompt, binding().originalPrompt)
  assert.equal(isCurrentH3PromptRewritePreview(preview, binding()), true)
  assert.throws(() => { preview.candidates[1].text = 'Mutated' }, TypeError)
  assert.throws(() => { preview.runtime_evidence.base_executed = false }, TypeError)
  assert.equal(isCurrentH3PromptRewritePreview(structuredClone(preview), binding()), false)
})

test('source-only, unknown fields, wrong runtime semantics and order fail closed', async () => {
  for (const mutate of [
    value => { value.schema_version = 1 },
    value => { value.selection = 'adapted' },
    value => { value.private_path = '/must-not-be-accepted' },
    value => { value.candidates[1].extra = 'foreign' },
    value => { value.runtime_evidence.fallback_used = true },
    value => { value.runtime_evidence.execution_available = 1 },
    value => { value.runtime_evidence.adapter_executed = false },
    value => { value.runtime_evidence.execution_receipt_sha256 = 'not-a-receipt' },
    value => { value.candidates.reverse() },
    value => { value.candidates[0].produced_by_runtime = true },
    value => { value.candidates[2].produced_by_runtime = false },
  ]) {
    const input = fixture()
    mutate(input)
    await assert.rejects(parseH3PromptRewritePreview(input, binding()))
  }
})

test('foreign request, changed original and modified candidate cannot become a current preview', async () => {
  await assert.rejects(parseH3PromptRewritePreview(fixture(), { ...binding(), requestCommitment: 'c'.repeat(64) }))
  await assert.rejects(parseH3PromptRewritePreview(fixture(), { ...binding(), originalPrompt: 'A new draft' }))
  const preview = await parseH3PromptRewritePreview(fixture(), binding())
  assert.equal(isCurrentH3PromptRewritePreview(preview, { ...binding(), requestCommitment: 'c'.repeat(64) }), false)
  assert.equal(isCurrentH3PromptRewritePreview(preview, { ...binding(), originalPrompt: 'A new draft' }), false)
  for (const mutate of [
    value => { value.candidates[0].text = 'Modified original' },
    value => { value.candidates[1].text += 'Changed' },
    value => { value.commitment = '0'.repeat(64) },
  ]) {
    const input = fixture()
    mutate(input)
    await assert.rejects(parseH3PromptRewritePreview(input, binding()))
  }
})

test('empty, oversized, NUL and malformed Unicode text cannot pass the bounded protocol', async () => {
  for (const text of ['', 'x'.repeat(65537), '\0', '\ud800']) {
    const input = fixture()
    input.candidates[1].text = text
    await assert.rejects(parseH3PromptRewritePreview(input, binding()))
  }
  for (const input of [null, [], { ...fixture(), candidates: [] }, Object.assign(new Date(), fixture())]) {
    await assert.rejects(parseH3PromptRewritePreview(input, binding()))
  }
})

test('HTTP LAN fallback verifies independent SHA-256 digests across padding boundaries and rejects tampering', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true })
  try {
    const golden = await parseH3PromptRewritePreview(fixture(), binding())
    assert.equal(golden.commitment, fixture().commitment)
    for (const length of [...Array(64).keys(), 1200, 65500]) {
      const input = fixture()
      input.candidates[1].text = '基地 🎥' + 'x'.repeat(length)
      const { commitment: _prior, ...unsigned } = input
      input.commitment = createHash('sha256').update(canonical(unsigned)).digest('hex')
      const parsed = await parseH3PromptRewritePreview(input, binding())
      assert.equal(parsed.candidates[1].text, input.candidates[1].text)
      input.candidates[1].text += '!'
      await assert.rejects(parseH3PromptRewritePreview(input, binding()))
    }
    const malformed = fixture()
    malformed.commitment = '0'.repeat(64)
    await assert.rejects(parseH3PromptRewritePreview(malformed, binding()))
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor)
    else delete globalThis.crypto
  }
})
