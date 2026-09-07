import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
const start = source.indexOf('function _buildH3EstimateRequest(')
const end = source.indexOf('\nfunction ', start + 9)
assert.ok(start >= 0 && end > start)
const compiled = await transform(`${source.slice(start, end)}\nexport { _buildH3EstimateRequest as build }`, {
  loader: 'ts', format: 'esm', target: 'es2022',
})
const { build } = await import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString('base64')}`)

function state(params = {}) {
  return { params: { model_type: 'minimax_h3_ref2va', prompt: '', activated_loras: ['legacy.safetensors'],
    loras_multipliers: '0.75', ...params }, imageRefs: [], explicitOutput: false,
    durationSeconds: 12, slidingWindowSeconds: 5, slidingWindowOverlap: 0,
    slidingWindowLocked: false, spatialUpsampling: '' }
}

test('estimates retain explicit checkpoint selections and independent LoRA weights', () => {
  const input = state({ h3_adaptive_fl2va_model: 'minimax_h3_w4a8_fl2va',
    h3_adaptive_ref2va_model: 'minimax_h3_ref2va',
    h3_fl2va_loras: ['folder/fl.safetensors'], h3_fl2va_loras_multipliers: '0.25;0.50',
    h3_ref2va_loras: ['folder/ref.safetensors'], h3_ref2va_loras_multipliers: '0.80' })
  const request = build(input)
  for (const key of Object.keys(input.params).filter(key => key.startsWith('h3_'))) {
    assert.deepEqual(request[key], input.params[key])
  }
  request.h3_fl2va_loras.push('extra')
  assert.equal(input.params.h3_fl2va_loras.length, 1)
})

test('absent and null split lists inherit while empty lists remain explicit', () => {
  for (const value of [undefined, null]) {
    const request = build(state({ h3_fl2va_loras: value, h3_fl2va_loras_multipliers: 'stale' }))
    assert.equal(Object.hasOwn(request, 'h3_fl2va_loras'), false)
    assert.equal(Object.hasOwn(request, 'h3_fl2va_loras_multipliers'), false)
    assert.deepEqual(request.activated_loras, ['legacy.safetensors'])
  }
  assert.deepEqual(build(state({ h3_fl2va_loras: [] })).h3_fl2va_loras, [])
})

test('compatibility estimates use their requested FL2VA flavor over a stale picker', () => {
  const input = state({ h3_adaptive_fl2va_model: 'minimax_h3_pinkcherry_fl2va' })
  for (const model of ['minimax_h3', 'minimax_h3_w4a8_fl2va', 'minimax_h3_pinkcherry_fl2va']) {
    const request = build(input, model)
    assert.equal(request.model_type, model)
    assert.equal(request.h3_adaptive_fl2va_model, model)
  }
  assert.equal(build(input).h3_adaptive_fl2va_model, 'minimax_h3_pinkcherry_fl2va')
  assert.equal(build(input, 'minimax_h3_ref2va').h3_adaptive_fl2va_model, 'minimax_h3_pinkcherry_fl2va')
})

test('output metadata does not introduce an adaptive checkpoint selection', () => {
  const input = state()
  input.explicitOutput = true
  assert.equal(build(input).h3_adaptive_fl2va_model, undefined)
  assert.equal(build(input).model_type, 'minimax_h3_ref2va')
})
