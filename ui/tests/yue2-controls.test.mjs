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
const { preferredYue2Checkpoint, resolveYue2GenerationSettings } = module

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
    { id: 'early', step: 500 },
    { id: 'preferred', step: 1000 },
  ]
  assert.equal(preferredYue2Checkpoint({ ...group('dream', {}, checkpoints), preferredStep: 1000 }).id, 'preferred')
  assert.equal(preferredYue2Checkpoint(group('dream', {}, checkpoints)).id, 'preferred')
})
