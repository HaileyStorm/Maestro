import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

let formatterPromise
function loadFormatters() {
  if (formatterPromise) return formatterPromise
  formatterPromise = build({
    entryPoints: [new URL('../src/lib/format.ts', import.meta.url).pathname],
    bundle: true,
    format: 'esm',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
  }).then(result => import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`))
  return formatterPromise
}

test('media durations round once before selecting units', async () => {
  const { formatMediaDuration } = await loadFormatters()

  assert.equal(formatMediaDuration(0), '0s')
  assert.equal(formatMediaDuration(5), '5s')
  assert.equal(formatMediaDuration(124 / 24), '5.17s')
  assert.equal(formatMediaDuration(10), '10s')
  assert.equal(formatMediaDuration(59.999999), '1m')
  assert.equal(formatMediaDuration(60.000001), '1m')
  assert.equal(formatMediaDuration(61.25), '1m 1.25s')
  assert.equal(formatMediaDuration(119.999999), '2m')
  assert.equal(formatMediaDuration(3600.4), '1h 0.4s')
})

test('media durations remain bounded and fail closed for invalid display values', async () => {
  const { formatMediaDuration } = await loadFormatters()

  assert.equal(formatMediaDuration(1.23456, 3), '1.235s')
  assert.equal(formatMediaDuration(1.9, -4), '2s')
  assert.equal(formatMediaDuration(1.23456, Number.NaN), '1.23s')
  assert.equal(formatMediaDuration(-1), '—')
  assert.equal(formatMediaDuration(Number.NaN), '—')
  assert.equal(formatMediaDuration(Number.POSITIVE_INFINITY, 2, 'unavailable'), 'unavailable')
})

test('approximate durations roll seconds, minutes, and hours without boundary artifacts', async () => {
  const { formatApproximateDuration } = await loadFormatters()

  assert.equal(formatApproximateDuration(0), '1s')
  assert.equal(formatApproximateDuration(59.49), '59s')
  assert.equal(formatApproximateDuration(59.5), '1m')
  assert.equal(formatApproximateDuration(60.0001), '1m')
  assert.equal(formatApproximateDuration(119.999999), '2m')
  assert.equal(formatApproximateDuration(3599.49), '59m 59s')
  assert.equal(formatApproximateDuration(3599.5), '1h')
  assert.equal(formatApproximateDuration(3600.4), '1h')
  assert.equal(formatApproximateDuration(7199.5), '2h')
  assert.equal(formatApproximateDuration(-1), 'unknown')
  assert.equal(formatApproximateDuration(Number.NaN, 'calculating…'), 'calculating…')
  assert.equal(formatApproximateDuration(Number.POSITIVE_INFINITY), 'unknown')
})

test('Generate duration surfaces share the canonical display formatters', async () => {
  const [slider, profiles, plan, queue] = await Promise.all([
    readFile(new URL('../src/components/Sidebar/DurationSlider.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/Sidebar/H3PerformanceProfiles.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/H3GenerationPlanDialog.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/MainContent/MainContent.tsx', import.meta.url), 'utf8'),
  ])

  assert.match(slider, /formatMediaDuration\(duration\)/)
  assert.match(slider, /formatMediaDuration\(windowSize\)/)
  assert.match(profiles, /formatApproximateDuration\(seconds, ''\)/)
  assert.match(plan, /formatMediaDuration\(planPublishedFrames \/ planFps\)/)
  assert.match(plan, /formatApproximateDuration\(seconds, 'calculating…'\)/)
  assert.match(queue, /formatApproximateDuration\(job\.etaSeconds\)/)
  assert.match(queue, /formatMediaDuration\(publishedSeconds\)/)

  const affectedSource = [slider, profiles, plan, queue].join('\n')
  assert.doesNotMatch(affectedSource, /compactEta|compactTime|function formatSeconds/)
  assert.doesNotMatch(affectedSource, /windowSize\.toFixed|publishedSeconds\.toFixed|generatedSeconds\.toFixed/)
})
