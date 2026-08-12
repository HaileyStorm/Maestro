import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const {
  PERFORMANCE_HISTORY_PRIVACY_NOTE,
  PERFORMANCE_HISTORY_CHART_MAX,
  PERFORMANCE_TREND_SERIES,
  PUBLISHED_PERFORMANCE_REFERENCES,
  validatePerformanceHistory,
} = await import('../src/lib/performanceHistory.ts')

const componentSource = await readFile(new URL('../src/components/PerformanceHistoryChart.tsx', import.meta.url), 'utf8')
const dialogSource = await readFile(new URL('../src/components/WhatsNewDialog.tsx', import.meta.url), 'utf8')
const dataSource = await readFile(new URL('../src/lib/performanceHistory.ts', import.meta.url), 'utf8')

async function renderHistoryChart() {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  try {
    const { PerformanceHistoryChart } = await server.ssrLoadModule('/src/components/PerformanceHistoryChart.tsx')
    return renderToStaticMarkup(createElement(PerformanceHistoryChart))
  } finally {
    await server.close()
  }
}

test('performance history schema is complete, ordered, and bounded', () => {
  assert.doesNotThrow(() => validatePerformanceHistory())
  assert.deepEqual(PERFORMANCE_TREND_SERIES.map(series => series.id), ['iteration', 'quality'])
  assert.deepEqual(PERFORMANCE_TREND_SERIES.map(series => series.marker), ['triangle', 'circle'])
  assert.equal(PERFORMANCE_TREND_SERIES.every(series => series.points.length === 4), true)
  assert.deepEqual(PERFORMANCE_TREND_SERIES[0].points.map(point => point.provenance.profile), [
    'Native 20-step reference',
    'Experimental six-step Turbo',
    'Managed six-step Turbo',
    'Managed Draft, four-step Turbo at 608×352',
  ])
  assert.deepEqual(PERFORMANCE_TREND_SERIES[0].points.map(point => point.value), [1, 2.2, 2.2, 3])
  assert.deepEqual(PERFORMANCE_TREND_SERIES[1].points.map(point => point.value), [1, 1, 1, 1])
  assert.match(PERFORMANCE_TREND_SERIES[0].points[2].provenance.basis, /no later named profile is backdated/)

  for (const series of PERFORMANCE_TREND_SERIES) {
    const dates = series.points.map(point => point.date)
    assert.deepEqual(dates, [...dates].sort())
    for (const point of series.points) {
      assert.equal(point.seriesId, series.id)
      assert.equal(point.datePrecision, 'day')
      assert.equal(point.provenance.kind, 'estimated_backfill')
      assert.equal(point.provenance.confidence, 'low')
      assert.ok(point.uncertainty.low <= point.value)
      assert.ok(point.uncertainty.high >= point.value)
      assert.match(point.provenance.method, /inverse step-count proxy/i)
      assert.match(point.provenance.comparability, /inverse documented-step index/i)
    }
  }
})

test('provenance discriminants prevent measured or published points from joining estimate lines', () => {
  const firstSeries = PERFORMANCE_TREND_SERIES[0]
  const invalidPoint = {
    ...firstSeries.points[0],
    provenance: {
      ...PUBLISHED_PERFORMANCE_REFERENCES[0].provenance,
      kind: 'measured_local',
      protocol: 'invalid test fixture',
    },
  }
  const invalidSeries = [{ ...firstSeries, points: [invalidPoint, ...firstSeries.points.slice(1)] }, ...PERFORMANCE_TREND_SERIES.slice(1)]
  assert.throws(() => validatePerformanceHistory(invalidSeries, PUBLISHED_PERFORMANCE_REFERENCES), /estimated backfill only/)

  const connectedReference = [{ ...PUBLISHED_PERFORMANCE_REFERENCES[0], chartComparable: true }, ...PUBLISHED_PERFORMANCE_REFERENCES.slice(1)]
  assert.throws(() => validatePerformanceHistory(PERFORMANCE_TREND_SERIES, connectedReference), /disconnected context/)

  const invalidAdaptation = PUBLISHED_PERFORMANCE_REFERENCES.map(reference => reference.id === 'hailuo02-efficiency'
    ? { ...reference, provenance: { ...reference.provenance, adaptation: '' } }
    : reference)
  assert.throws(() => validatePerformanceHistory(PERFORMANCE_TREND_SERIES, invalidAdaptation), /original metric and adaptation/)

  assert.match(dataSource, /kind: 'measured_local'/)
  assert.match(dataSource, /kind: 'measured_published'/)
  assert.match(dataSource, /kind: 'adapted_published'/)
  assert.match(dataSource, /kind: 'estimated_backfill'/)
})

test('validator rejects duplicate or misaligned series, invalid dates, non-finite ranges, scale overflow, and non-HTTPS sources', () => {
  const first = PERFORMANCE_TREND_SERIES[0]
  const second = PERFORMANCE_TREND_SERIES[1]
  assert.throws(() => validatePerformanceHistory([first, { ...second, id: first.id }], PUBLISHED_PERFORMANCE_REFERENCES), /series IDs must be unique/)

  const misaligned = [first, { ...second, points: [{ ...second.points[0], date: '2026-08-05' }, ...second.points.slice(1)] }]
  assert.throws(() => validatePerformanceHistory(misaligned, PUBLISHED_PERFORMANCE_REFERENCES), /timelines must align/)

  const truncated = [first, { ...second, points: second.points.slice(0, -1) }]
  assert.throws(() => validatePerformanceHistory(truncated, PUBLISHED_PERFORMANCE_REFERENCES), /timelines must align/)

  const invalidDate = [{ ...first, points: [{ ...first.points[0], date: '2026-02-31' }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(invalidDate, PUBLISHED_PERFORMANCE_REFERENCES), /dates must be valid/)

  const duplicateDate = [
    { ...first, points: [first.points[0], { ...first.points[1], date: first.points[0].date }, ...first.points.slice(2)] },
    { ...second, points: [second.points[0], { ...second.points[1], date: second.points[0].date }, ...second.points.slice(2)] },
  ]
  assert.throws(() => validatePerformanceHistory(duplicateDate, PUBLISHED_PERFORMANCE_REFERENCES), /dates must be unique and chronological/)

  const nonFinite = [{ ...first, points: [{ ...first.points[0], uncertainty: { ...first.points[0].uncertainty, high: Number.NaN } }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(nonFinite, PUBLISHED_PERFORMANCE_REFERENCES), /uncertainty must contain/)

  const wrongUnit = [{ ...first, points: [{ ...first.points[0], uncertainty: { ...first.points[0].uncertainty, unit: 'seconds' } }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(wrongUnit, PUBLISHED_PERFORMANCE_REFERENCES), /uncertainty must contain/)

  const beyondScale = [{ ...first, points: [{ ...first.points[0], value: PERFORMANCE_HISTORY_CHART_MAX + 1, uncertainty: { ...first.points[0].uncertainty, high: PERFORMANCE_HISTORY_CHART_MAX + 1 } }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(beyondScale, PUBLISHED_PERFORMANCE_REFERENCES), /must be positive/)

  const insecureSource = [{ ...first, points: [{ ...first.points[0], provenance: { ...first.points[0].provenance, sourceUrl: 'http://example.invalid' } }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(insecureSource, PUBLISHED_PERFORMANCE_REFERENCES), /must use HTTPS/)

  const invalidSourceDate = [{ ...first, points: [{ ...first.points[0], provenance: { ...first.points[0].provenance, sourceDate: 'not-a-date' } }, ...first.points.slice(1)] }, second]
  assert.throws(() => validatePerformanceHistory(invalidSourceDate, PUBLISHED_PERFORMANCE_REFERENCES), /source dates must be valid/)

  const invalidReferenceSourceDate = [{ ...PUBLISHED_PERFORMANCE_REFERENCES[0], provenance: { ...PUBLISHED_PERFORMANCE_REFERENCES[0].provenance, sourceDate: '2025-02-31' } }, ...PUBLISHED_PERFORMANCE_REFERENCES.slice(1)]
  assert.throws(() => validatePerformanceHistory(PERFORMANCE_TREND_SERIES, invalidReferenceSourceDate), /source dates must be valid/)
})

test('published references are primary-source context and never comparable chart data', () => {
  assert.deepEqual(PUBLISHED_PERFORMANCE_REFERENCES.map(reference => reference.label), [
    'LTX-Video paper',
    'Wan2.1 1.3B',
    'MiniMax Hailuo 02',
    'HunyuanVideo-1.5',
  ])
  assert.equal(PUBLISHED_PERFORMANCE_REFERENCES.every(reference => reference.chartComparable === false), true)
  assert.equal(PUBLISHED_PERFORMANCE_REFERENCES.every(reference => reference.provenance.sourceUrl.startsWith('https://')), true)
  assert.equal(PUBLISHED_PERFORMANCE_REFERENCES.every(reference => /not plotted|excluded|hollow context marker/.test(reference.provenance.comparability)), true)
  assert.equal(PUBLISHED_PERFORMANCE_REFERENCES.some(reference => reference.provenance.kind === 'adapted_published'), true)
  assert.deepEqual(PUBLISHED_PERFORMANCE_REFERENCES.map(reference => new URL(reference.provenance.sourceUrl).hostname), [
    'arxiv.org',
    'github.com',
    'www.minimax.io',
    'github.com',
  ])
  const ltx = PUBLISHED_PERFORMANCE_REFERENCES.find(reference => reference.id === 'ltx-video-h100')
  assert.equal(ltx.displayMetric, '5s at 768×512 / 24 fps in 2s on one H100')
  assert.equal(ltx.provenance.sourceUrl, 'https://arxiv.org/abs/2501.00103')
  assert.match(ltx.provenance.profile, /121 frames, 20 diffusion steps/)
  const wan = PUBLISHED_PERFORMANCE_REFERENCES.find(reference => reference.id === 'wan21-4090')
  assert.equal(wan.displayMetric, 'about 4 min for 5s / 480p on RTX 4090')
  assert.match(wan.provenance.sourceUrl, /\/tree\/[0-9a-f]{40}$/)
  const hailuo = PUBLISHED_PERFORMANCE_REFERENCES.find(reference => reference.id === 'hailuo02-efficiency')
  assert.match(hailuo.provenance.publishedMetric, /2\.5× training and inference efficiency/)
  assert.match(hailuo.provenance.adaptation, /not converted into wall time or a local generation rate/)
  const hunyuan = PUBLISHED_PERFORMANCE_REFERENCES.find(reference => reference.id === 'hunyuan15-4090')
  assert.match(hunyuan.provenance.method, /without inferring an unstated output duration/)
  assert.match(hunyuan.provenance.publishedMetric, /generation time reduced by 75%/)
  assert.match(hunyuan.provenance.sourceUrl, /\/tree\/[0-9a-f]{40}$/)
})

test('bundled history contains no private runtime or local-machine data', () => {
  const serialized = JSON.stringify({ PERFORMANCE_TREND_SERIES, PUBLISHED_PERFORMANCE_REFERENCES })
  assert.doesNotMatch(serialized, /(?:[A-Z]:\\|\/(?:home|media|Users)\/)/)
  assert.doesNotMatch(serialized, /"(?:prompt|project|job|seed|user|session|account)(?:s|Id|_id)?"\s*:/i)
  assert.match(PERFORMANCE_HISTORY_PRIVACY_NOTE, /does not read prompts, projects, jobs, paths, seeds, devices, sessions, accounts, or runtime records/)
})

test('chart is accessible without color, hover, animation, or SVG-only disclosure', () => {
  assert.match(componentSource, /<svg\s+[\s\S]*role="img"/)
  assert.match(componentSource, /<title id=\{titleId\}>/)
  assert.match(componentSource, /<desc id=\{descriptionId\}>/)
  assert.match(componentSource, /<figcaption/)
  assert.match(componentSource, /marker === 'square'/)
  assert.match(componentSource, /marker === 'triangle'/)
  assert.match(componentSource, /strokeDasharray=\{series\.dash\}/)
  assert.match(componentSource, /<table/)
  assert.match(componentSource, /<caption/)
  assert.match(componentSource, /<th scope="row"/)
  assert.match(componentSource, /Data, uncertainty, and sources/)
  assert.match(componentSource, /target="_blank" rel="noreferrer"/)
  assert.doesNotMatch(componentSource, /onMouseEnter|onMouseMove|requestAnimationFrame|animate-/)
})

test('server-rendered chart exposes every evidence row, source link, row header, and keyboard scroll region', async () => {
  const markup = await renderHistoryChart()
  const evidenceRows = PERFORMANCE_TREND_SERIES.reduce((count, series) => count + series.points.length, 0)
    + PUBLISHED_PERFORMANCE_REFERENCES.length
  assert.equal(markup.match(/<tr/g)?.length, evidenceRows + 1, 'one header row plus every evidence row')
  assert.equal(markup.match(/<a /g)?.length, evidenceRows, 'every evidence row has one keyboard source link')
  assert.equal(markup.match(/scope="row"/g)?.length, evidenceRows)
  assert.equal(markup.match(/role="region"/g)?.length, 2)
  assert.equal(markup.match(/tabindex="0"/g)?.length, 2)
  assert.match(markup, /Sensitivity basis:/)
})

test('performance disclosure remains usable at 320, 390, 768, and desktop widths', () => {
  assert.match(componentSource, /min-h-11 cursor-pointer/)
  assert.match(componentSource, /inline-flex min-h-11 items-center/)
  assert.match(componentSource, /className="block h-auto min-w-\[34rem\] w-full"/)
  assert.match(componentSource, /viewBox="0 0 720 274"/)
  assert.match(componentSource, /sm:flex-row/)
  assert.match(componentSource, /sm:grid-cols-2/)
  assert.match(componentSource, /overflow-x-auto overscroll-x-contain/)
  assert.equal(componentSource.match(/role="region"/g)?.length, 2)
  assert.equal(componentSource.match(/tabIndex=\{0\}/g)?.length, 2)
  assert.match(componentSource, /aria-label="Scrollable H3 denoising-work chart"/)
  assert.match(componentSource, /aria-label="Scrollable generation-work evidence table"/)
  assert.match(componentSource, /motion-reduce:transition-none/)

  const chartMinWidth = 34 * 16
  const tableMinWidth = 35 * 16
  for (const viewportWidth of [320, 390, 768, 1440]) {
    const dialogWidth = viewportWidth < 640
      ? viewportWidth - 24
      : Math.min(672, viewportWidth - 48)
    const bodyWidth = dialogWidth - (viewportWidth < 640 ? 40 : 48)
    const chartWidth = bodyWidth - (viewportWidth < 640 ? 24 : 32)
    const tableWidth = chartWidth - 24
    const expectsChartScroll = viewportWidth < 768
    assert.equal(chartWidth < chartMinWidth, expectsChartScroll, `${viewportWidth}px chart scroll contract`)
    assert.equal(tableWidth < tableMinWidth, expectsChartScroll, `${viewportWidth}px table scroll contract`)
  }
})

test("what's-new dialog mounts exactly one restrained performance view", () => {
  assert.match(dialogSource, /import \{ PerformanceHistoryChart \} from '\.\/PerformanceHistoryChart'/)
  assert.equal(dialogSource.match(/<PerformanceHistoryChart \/>/g)?.length, 1)
  assert.equal(componentSource.match(/<figure/g)?.length, 1)
  assert.doesNotMatch(dialogSource, /https?:\/\//i)
  const whyIndex = dialogSource.indexOf('Why Continuum')
  const evidenceIndex = dialogSource.indexOf('<PerformanceHistoryChart />')
  const archiveIndex = dialogSource.indexOf('All release history')
  assert.ok(whyIndex < evidenceIndex && evidenceIndex < archiveIndex)
  assert.doesNotMatch(`${componentSource}\n${dataSource}`, /useStore|fetch\s*\(|\/api\/v1\//)
})
