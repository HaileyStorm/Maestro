import assert from 'node:assert/strict'
import test from 'node:test'
import { audioPreviewTarget } from '../src/editor/audioPreviewClock.ts'

const layer = { id: 'bed', asset_id: 'sound', start: 3, source_in: 1, duration: 2, speed: 1, volume: 0.5, fade_in: 1, fade_out: 0.5 }

test('second trimmed clip follows the absolute sequence placement and trimmed audio clock', () => {
  const target = time => audioPreviewTarget(layer, time, 2, 4, 2)
  assert.equal(target(2.5), null) // sequence 2.5: before layer
  assert.deepEqual(target(3), { sourceTime: 1, gain: 0 })
  assert.deepEqual(target(3.5), { sourceTime: 1.5, gain: 0.25 })
  assert.deepEqual(target(4), { sourceTime: 2, gain: 0.5 })
  assert.deepEqual(target(4.75), { sourceTime: 2.75, gain: 0.25 })
  assert.equal(target(5), null) // half-open audio endpoint
})

test('seeks outside a selected cut, mute and zero gain cannot start audio', () => {
  for (const time of [1.9, 6, NaN, Infinity]) assert.equal(audioPreviewTarget(layer, time, 2, 4, 2), null)
  for (const silent of [{ ...layer, muted: true }, { ...layer, volume: 0 }]) {
    assert.equal(audioPreviewTarget(silent, 4, 2, 4, 2), null)
  }
  assert.equal(audioPreviewTarget(layer, 4, 2, 4, NaN), null)
})

test('reordering a clip changes the timeline overlap without changing source trim', () => {
  assert.equal(audioPreviewTarget(layer, 3.5, 2, 4, 0)?.sourceTime, undefined)
  assert.deepEqual(audioPreviewTarget(layer, 3.5, 2, 4, 3), { sourceTime: 2.5, gain: 0.5 })
  assert.deepEqual(audioPreviewTarget({ ...layer, start: 0, duration: 6, fade_in: 0, fade_out: 0 }, 2.5, 2, 0.5, 0), null)
})
