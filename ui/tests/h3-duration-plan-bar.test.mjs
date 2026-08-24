import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const componentSource = await readFile(new URL('../src/components/H3DurationPlanBar.tsx', import.meta.url), 'utf8')
const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('../', import.meta.url)),
  appType: 'custom',
  logLevel: 'silent',
  server: { middlewareMode: true, watch: null },
})
const { H3DurationPlanBar } = await server.ssrLoadModule('/src/components/H3DurationPlanBar.tsx')
after(() => server.close())

function renderDurationBar(props) {
  return renderToStaticMarkup(createElement(H3DurationPlanBar, props))
}

test('duration bar is prop-only and leaves snapping and frame authority to the server', () => {
  assert.doesNotMatch(componentSource, /useStore|fetch\s*\(|\/api\/v1\//)
  assert.doesNotMatch(componentSource, /snap|segment estimator|Math\.round|Math\.floor|Math\.ceil/)
  assert.match(componentSource, /targetPublishedFrames: number/)
  assert.match(componentSource, /currentPublishedFrames: number/)
  assert.match(componentSource, /currentGeneratedFrames: number/)
  assert.match(componentSource, /currentMinusTargetFrames: number/)
})

test('target marker is fixed while the current indicator is derived only for display geometry', () => {
  assert.match(componentSource, /const TARGET_MARKER_X = 466/)
  assert.match(componentSource, /x1=\{TARGET_MARKER_X\}/)
  assert.match(componentSource, /strokeDasharray="4 3"/)
  assert.match(componentSource, />T<\/text>/)
  assert.match(componentSource, /x=\{currentX\}/)
  assert.match(componentSource, />C<\/text>/)
  assert.match(componentSource, /Difference · C − T/)
})

test('rendered mismatch exposes fixed target, current draft, signed delta, reason, and tail note', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 290,
    currentGeneratedFrames: 306,
    currentMinusTargetFrames: -10,
    outcome: 'acceptable',
    reason: 'The current segment settings cannot add the last 10 frames without going over the target.',
  })

  assert.match(markup, /Target 300f/)
  assert.match(markup, /290 frames/)
  assert.match(markup, /-10 frames · shorter than target/)
  assert.match(markup, /≈ Close to target/)
  assert.match(markup, /cannot add the last 10 frames without going over the target/)
  assert.match(markup, /306 frames will be generated, and 290 will appear in the finished video/)
  assert.match(markup, /extra ending frames will be trimmed/i)
})

test('insufficient capacity remains explicit and never masquerades as an exact result', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 280,
    currentGeneratedFrames: 280,
    currentMinusTargetFrames: -20,
    outcome: 'insufficient_capacity',
    reason: 'The remaining segments cannot be lengthened enough to reach the target.',
  })

  assert.match(markup, /! Cannot reach target/)
  assert.match(markup, /remaining segments cannot be lengthened enough/)
  assert.doesNotMatch(markup, /✓ Matches target/)
  assert.match(markup, /All 280 generated frames will appear in the finished video/)
})

test('exact outcome reports zero signed mismatch and matching generated geometry', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 300,
    currentGeneratedFrames: 300,
    currentMinusTargetFrames: 0,
    outcome: 'exact',
  })

  assert.match(markup, /✓ Matches target/)
  assert.match(markup, /0 frames · on target/)
  assert.match(markup, /video length matches the original target/)
  assert.match(markup, /All 300 generated frames will appear in the finished video/)
})

test('screen-reader table and SVG description duplicate every non-color cue', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 320,
    currentGeneratedFrames: 336,
    currentMinusTargetFrames: 20,
    outcome: 'acceptable',
    reason: 'Automatic adjustment is off, so the 20-frame difference remains.',
  })

  assert.match(markup, /role="img"/)
  assert.match(markup, /<title>/)
  assert.match(markup, /<desc>/)
  assert.match(markup, /dashed T marker shows the original target/)
  assert.match(markup, /solid diamond C marker shows the current plan/)
  assert.match(markup, /<table class="sr-only">/)
  assert.match(markup, /<caption>H3 video length comparison<\/caption>/)
  assert.equal(markup.match(/scope="row"/g)?.length, 5)
  assert.match(markup, /\+20 frames · longer than target/)
  assert.match(markup, /Automatic adjustment is off/)
})

test('visible copy explains the comparison without internal planning jargon', () => {
  assert.match(componentSource, /How the video length compares/)
  assert.match(componentSource, /This chart is for comparison only/)
  assert.match(componentSource, /Extra generated frames/)
  assert.doesNotMatch(componentSource, /server-authored|server-verified|Signed mismatch|Insufficient capacity|Generated vs published tail/i)
})

test('responsive source contract stays usable at 320px, landscape, and 200 percent zoom without motion', () => {
  assert.match(componentSource, /className="block h-auto w-full min-w-0"/)
  assert.match(componentSource, /min-w-0/)
  assert.match(componentSource, /break-words/)
  assert.match(componentSource, /grid-cols-1/)
  assert.match(componentSource, /sm:grid-cols-3/)
  assert.doesNotMatch(componentSource, /min-w-\[/)
  assert.doesNotMatch(componentSource, /max-h-|h-screen|100vh|100dvh/)
  assert.doesNotMatch(componentSource, /animate-|transition|requestAnimationFrame|setInterval|setTimeout/)
})
