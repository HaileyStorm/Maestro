import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const result = await build({
  entryPoints: [new URL('../src/components/Sidebar/yue2GenerationSettings.ts', import.meta.url).pathname],
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
})
const module = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
const {
  preferredYue2Checkpoint,
  resolveYue2CheckpointSelection,
  resolveYue2GenerationSettings,
  reviewedAbcForContinuation,
  sameYue2ComposeDraft,
} = module

const group = (id, generation = {}, checkpoints = []) => ({
  id, name: id, kind: 'style', generation, checkpoints,
})

test('YuE2 uses the workspace sampler defaults when no LoRA overrides them', () => {
  assert.deepEqual(resolveYue2GenerationSettings([]), {
    cot: 'full', cfgScale: 1, odeSteps: 100, maxTokens: 9000,
  })
})

test('compatible LoRA defaults override the workspace settings', () => {
  assert.deepEqual(resolveYue2GenerationSettings([
    group('dream', { cot: 'off', cfg_scale: 1, ode_steps: 32 }),
    group('hiphop', { cot: 'off', cfg_scale: 1, ode_steps: 32 }),
  ]), { cot: 'off', cfgScale: 1, odeSteps: 32, maxTokens: 9000 })
})

test('incompatible stacked LoRA defaults fail with an actionable error', () => {
  assert.throws(
    () => resolveYue2GenerationSettings([
      group('one', { cot: 'off' }), group('two', { cot: 'full' }),
    ]),
    /different score-planning modes.*compatible stack/,
  )
})

test('preferred checkpoint follows the catalog preferred step', () => {
  const checkpoints = [
    { id: 'early', sha256: 'a'.repeat(64), step: 500 },
    { id: 'preferred', sha256: 'b'.repeat(64), step: 1000 },
  ]
  assert.equal(preferredYue2Checkpoint({ ...group('dream', {}, checkpoints), preferredStep: 1000 }).id, 'preferred')
  assert.equal(preferredYue2Checkpoint(group('dream', {}, checkpoints)).id, 'preferred')
})

test('checkpoint choice resolves the exact ID and SHA-256 pair', () => {
  const checkpoints = [
    { id: 'same-id', filename: 'older.safetensors', sha256: 'a'.repeat(64), step: 500 },
    { id: 'same-id', filename: 'newer.safetensors', sha256: 'b'.repeat(64), step: 1000 },
  ]
  const selected = resolveYue2CheckpointSelection(
    { ...group('dream', {}, checkpoints), preferredStep: 1000 },
    { id: 'same-id', sha256: 'a'.repeat(64) },
  )
  assert.equal(selected.checkpoint.filename, 'older.safetensors')
  assert.equal(selected.checkpoint.sha256, 'a'.repeat(64))
  assert.equal(selected.error, null)
})

test('stale checkpoint IDs or hashes fail closed instead of falling back to preferred', () => {
  const checkpoints = [
    { id: 'current', filename: 'current.safetensors', sha256: 'c'.repeat(64), step: 1000 },
  ]
  const groupWithCheckpoint = { ...group('dream', {}, checkpoints), preferredStep: 1000 }
  const staleId = resolveYue2CheckpointSelection(groupWithCheckpoint, { id: 'removed', sha256: 'b'.repeat(64) })
  const staleHash = resolveYue2CheckpointSelection(groupWithCheckpoint, { id: 'current', sha256: 'b'.repeat(64) })
  const missingDefault = resolveYue2CheckpointSelection(group('empty'))
  const missingCatalog = resolveYue2CheckpointSelection({ ...group('missing'), checkpoints: undefined })
  const invalidHash = resolveYue2CheckpointSelection(group('invalid', {}, [
    { id: 'bad-checksum', sha256: 'not-a-sha256' },
  ]))
  assert.equal(staleId.checkpoint, null)
  assert.match(staleId.error, /no longer installed or its checksum changed/)
  assert.equal(staleHash.checkpoint, null)
  assert.match(staleHash.error, /no longer installed or its checksum changed/)
  assert.equal(missingDefault.checkpoint, null)
  assert.match(missingDefault.error, /No installed checkpoint is available/)
  assert.equal(missingCatalog.checkpoint, null)
  assert.match(missingCatalog.error, /No installed checkpoint is available/)
  assert.equal(invalidHash.checkpoint, null)
  assert.match(invalidHash.error, /missing a valid installed ID or SHA-256/)
})

test('an untouched reviewed score resumes the exact saved YuE2 plan', () => {
  const plan = 'X:1\nV: Vocal\nC4|\nV: Ins\nC,4|'
  assert.equal(reviewedAbcForContinuation(plan, plan), undefined)
  assert.equal(reviewedAbcForContinuation(plan, `${plan}\n% edited`), `${plan}\n% edited`)
})

test('YuE2 compose results apply only to the unchanged draft they started from', () => {
  const draft = {
    workspace: 'alpha', description: 'bright synth pop', language: 'English',
    instrumental: false, style: 'synth pop', lyrics: 'one line', abc: 'X:1',
  }
  assert.equal(sameYue2ComposeDraft(draft, { ...draft }), true)
  for (const [key, value] of [
    ['workspace', 'beta'], ['description', 'dark synth pop'], ['language', 'Japanese'],
    ['instrumental', true], ['style', 'rock'], ['lyrics', 'new line'], ['abc', 'X:2'],
  ]) {
    assert.equal(sameYue2ComposeDraft(draft, { ...draft, [key]: value }), false, key)
  }
})

test('YuE2 async results are fenced to the captured workspace and review can be retried', async () => {
  const source = await import('node:fs/promises').then(fs => fs.readFile(
    new URL('../src/components/Sidebar/Yue2Controls.tsx', import.meta.url),
    'utf8',
  ))
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| refreshSequence\.current !== sequence/)
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| composeSequence\.current !== sequence/)
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| planSequence\.current !== sequence/)
  assert.match(source, /!plan\.reviewable/)
  assert.match(source, /disabled=\{reviewLoading\}/)
  assert.match(source, /tracks\.filter\(track => track\.project === workspace\)/)
  assert.match(source, /Retry score review/)
})

test('YuE2 picker submits the selected exact checkpoint pair and blocks stale choices', async () => {
  const source = await import('node:fs/promises').then(fs => fs.readFile(
    new URL('../src/components/Sidebar/Yue2Controls.tsx', import.meta.url),
    'utf8',
  ))
  assert.match(source, /aria-label=\{`\$\{group\.name\} checkpoint`\}/)
  assert.match(source, /resolveYue2CheckpointSelection\(group, checkpointSelections\[group\.id\]\)/)
  assert.match(source, /id: checkpoint\.id, sha256: checkpoint\.sha256, strength/)
  assert.match(source, /!!checkpointError/)
  assert.match(source, /Unavailable · \{savedCheckpoint\.id\} · SHA-256 \{savedCheckpoint\.sha256\}/)
})
