import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const source = await readFile(new URL('../src/lib/h3Submission.ts', import.meta.url), 'utf8')
const compiled = await transform(source, { loader: 'ts', format: 'esm', target: 'es2022' })
const h3 = await import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString('base64')}`)
const base = 'minimax_h3'
const pink = 'minimax_h3_pinkcherry_fl2va'
const quantized = 'minimax_h3_w4a8_fl2va'
const ref = 'minimax_h3_ref2va'
const ordinary = 'folder/ordinary.safetensors'
const motion = 'folder/h3_Better_NSFW_Motion_V1.safetensors'
const turbo = 'folder/minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors'

test('explicit checkpoint choice wins, then selected flavor, then Base', () => {
  for (const selected of [base, pink, quantized, ref]) {
    assert.equal(h3.defaultAdaptiveFl2vaModel(selected, quantized), quantized)
  }
  for (const selected of [base, pink, quantized]) {
    for (const absent of [undefined, null, '']) {
      assert.equal(h3.defaultAdaptiveFl2vaModel(selected, absent), selected)
    }
  }
  for (const explicitOutput of [false, true]) {
    assert.deepEqual(h3.h3ActiveCheckpoints({
      model_type: ref, explicit_output: explicitOutput,
    }), [base, ref])
  }
})

test('invalid nonempty choices remain invalid and require repair', () => {
  assert.equal(h3.defaultAdaptiveFl2vaModel(base, 'unknown-model'), 'unknown-model')
  assert.equal(h3.defaultAdaptiveRef2vaModel('unknown-model'), 'unknown-model')
  for (const invalid of ['unknown-model', [], [base], {}, 4]) {
    assert.ok(h3.h3AdaptiveSelectionError({ model_type: base, h3_adaptive_fl2va_model: invalid }))
    assert.ok(h3.h3AdaptiveSelectionError({ model_type: base, h3_adaptive_ref2va_model: invalid }))
  }
  assert.equal(h3.h3AdaptiveSelectionError({ model_type: pink }), null)
})

test('pinned mode excludes saved adaptive selections', () => {
  const params = { model_type: pink, h3_adaptive_conditioning: false,
    h3_adaptive_fl2va_model: quantized, h3_adaptive_ref2va_model: 'unknown-model' }
  assert.deepEqual(h3.h3ActiveCheckpoints(params), [pink])
  assert.equal(h3.h3AdaptiveSelectionError(params), null)
  assert.equal(h3.isH3StudioModel([base]), false)
  assert.equal(h3.h3ArchitectureForModel([base]), null)
})

test('selected unavailable choices stay visible only within their architecture', () => {
  const options = { allowed: new Set([base, pink, quantized]), selectedType: pink }
  assert.equal(h3.h3AdaptivePickerModelCompatible({ model_type: pink, execution_allowed: false }, options), true)
  assert.equal(h3.h3AdaptivePickerModelCompatible({ model_type: ref }, { ...options, selectedType: ref }), false)
  assert.equal(h3.h3AdaptivePickerModelCompatible({ model_type: quantized }, options), false)
  assert.equal(h3.h3AdaptivePickerModelCompatible({ model_type: quantized }, { ...options, w4a8Available: true }), true)
  assert.equal(h3.h3AdaptivePickerModelCompatible({ model_type: base, execution_allowed: false }, options), false)
})

test('known LoRA paths classify by basename without changing stored identity', () => {
  for (const ordinaryName of ['constructor', '__proto__', 'toString']) {
    assert.deepEqual(h3.h3LoraContract(ordinaryName).architectures, ['fl2va', 'ref2va'])
  }
  for (const prefix of ['folder/', 'C:\\models\\']) {
    const dasiwa = `${prefix}dasiwa_ref2va_hybrid_v1_4step.safetensors`
    const fast = `${prefix}minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors`
    assert.deepEqual(h3.h3LoraContract(dasiwa).architectures, ['ref2va'])
    assert.equal(h3.h3LoraContract(fast).exclusive, true)
    assert.deepEqual(h3.filterLorasForArchitecture([ordinary, dasiwa, fast], 'fl2va'), [ordinary, fast])
    assert.ok(h3.h3LoraBlockReason(ordinary, 'fl2va', [fast]))
    assert.ok(h3.h3LoraBlockReason(dasiwa, 'fl2va', []))
  }
})

test('missing and null lists inherit positional weights while explicit empty clears one side', () => {
  const shared = { activated_loras: [ordinary, motion], loras_multipliers: '0.25;0.50 0.80' }
  for (const absent of [undefined, null]) {
    assert.deepEqual(h3.h3LorasForArchitecture({ ...shared, h3_fl2va_loras: absent }, 'fl2va'), {
      loras: [ordinary], multipliers: '0.25;0.50',
    })
    assert.deepEqual(h3.h3LorasForArchitecture({ ...shared, h3_ref2va_loras: absent }, 'ref2va'), {
      loras: [ordinary, motion], multipliers: '0.25;0.50 0.80',
    })
  }
  const cleared = { ...shared, h3_fl2va_loras: [], h3_ref2va_loras: null }
  assert.deepEqual(h3.h3LorasForArchitecture(cleared, 'fl2va'), { loras: [], multipliers: '' })
  assert.deepEqual(h3.h3LorasForArchitecture(cleared, 'ref2va').loras, [ordinary, motion])
  assert.deepEqual(shared.activated_loras, [ordinary, motion])
})

test('same named LoRA has independent explicit weights and missing weights default once', () => {
  const params = { activated_loras: [turbo], h3_fl2va_loras: [ordinary],
    h3_fl2va_loras_multipliers: '0.333333333333', h3_ref2va_loras: [ordinary],
    h3_ref2va_loras_multipliers: '0.8;0.9' }
  assert.equal(h3.h3LorasForArchitecture(params, 'fl2va').multipliers, '0.333333333333')
  assert.equal(h3.h3LorasForArchitecture(params, 'ref2va').multipliers, '0.8;0.9')
  assert.equal(h3.h3LorasForArchitecture({ h3_ref2va_loras: [ordinary, motion] }, 'ref2va').multipliers, '1.00 1.00')
})

test('malformed lists and weight tokens fail without exposing asset names', () => {
  const cases = [
    { h3_fl2va_loras: 'private.safetensors' },
    { h3_fl2va_loras: [ordinary, ordinary] },
    { h3_fl2va_loras: [''] },
    { h3_fl2va_loras: [motion] },
    { h3_fl2va_loras: [], h3_fl2va_loras_multipliers: '1' },
    { activated_loras: 17 },
    { activated_loras: [ordinary], loras_multipliers: {} },
    ...['1 2', 'NaN', 'Infinity', '1e9999', '1;;2', '0x10', 'bad'].map(value => ({
      activated_loras: [ordinary], loras_multipliers: value,
    })),
  ]
  for (const params of cases) {
    assert.throws(() => h3.h3LorasForArchitecture(params, 'fl2va'), error => {
      assert.ok(error instanceof Error)
      assert.equal(error.message.includes('.safetensors'), false)
      return true
    })
  }
})

test('numeric editor maps preserve full keys, phases and precision', () => {
  const values = h3.parseLoraMultiplierMap([ordinary, '__proto__'], '0.333333333333;0.8 -0.5', 3)
  assert.equal(Object.getPrototypeOf(values), null)
  assert.deepEqual(values[ordinary], [0.333333333333, 0.8, 0.8])
  assert.deepEqual(values.__proto__, [-0.5, -0.5, -0.5])
  assert.equal(h3.serializeLoraMultipliers([ordinary], values, 3), '0.333333333333;0.8;0.8')
  const saved = h3.parseLoraMultiplierMap([ordinary], '0.7;0.9', 1)
  saved[ordinary][0] = 0.4
  assert.equal(h3.serializeLoraMultipliers([ordinary], saved, 1), '0.4;0.9')
  assert.throws(() => h3.parseLoraMultiplierMap([ordinary], 'NaN', 1))
  assert.throws(() => h3.serializeLoraMultipliers([ordinary], { [ordinary]: [Infinity] }, 1))
})
