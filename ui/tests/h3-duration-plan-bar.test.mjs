import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const componentSource = await readFile(new URL('../src/components/H3DurationPlanBar.tsx', import.meta.url), 'utf8')
const server = await createServer({
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
  assert.match(componentSource, /Signed mismatch · C − T/)
})

test('rendered mismatch exposes fixed target, current draft, signed delta, reason, and tail note', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 290,
    currentGeneratedFrames: 306,
    currentMinusTargetFrames: -10,
    outcome: 'acceptable',
    reason: 'The verified frame grid cannot represent the final 10 frames without crossing the target.',
  })

  assert.match(markup, /Target 300f/)
  assert.match(markup, /290 published frames/)
  assert.match(markup, /-10 frames · shorter than target/)
  assert.match(markup, /≈ Acceptable mismatch/)
  assert.match(markup, /verified frame grid cannot represent the final 10 frames/)
  assert.match(markup, /306 frames are generated; 290 frames are published/)
  assert.match(markup, /Generated tail work remains outside the published output/)
})

test('insufficient capacity remains explicit and never masquerades as an exact result', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 280,
    currentGeneratedFrames: 280,
    currentMinusTargetFrames: -20,
    outcome: 'insufficient_capacity',
    reason: 'No unlocked future segment has enough verified capacity to restore the target.',
  })

  assert.match(markup, /! Insufficient capacity/)
  assert.match(markup, /No unlocked future segment has enough verified capacity/)
  assert.doesNotMatch(markup, /✓ Exact target/)
  assert.match(markup, /no generated tail is omitted/)
})

test('exact outcome reports zero signed mismatch and matching generated geometry', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 300,
    currentGeneratedFrames: 300,
    currentMinusTargetFrames: 0,
    outcome: 'exact',
  })

  assert.match(markup, /✓ Exact target/)
  assert.match(markup, /0 frames · on target/)
  assert.match(markup, /matches the original target/)
  assert.match(markup, /300 frames are generated and 300 frames are published/)
})

test('screen-reader table and SVG description duplicate every non-color cue', async () => {
  const markup = await renderDurationBar({
    targetPublishedFrames: 300,
    currentPublishedFrames: 320,
    currentGeneratedFrames: 336,
    currentMinusTargetFrames: 20,
    outcome: 'acceptable',
    reason: 'Manual redistribution is disabled, so the verified mismatch remains visible.',
  })

  assert.match(markup, /role="img"/)
  assert.match(markup, /<title>/)
  assert.match(markup, /<desc>/)
  assert.match(markup, /fixed dashed T marker/)
  assert.match(markup, /solid diamond C marker/)
  assert.match(markup, /<table class="sr-only">/)
  assert.match(markup, /<caption>Read-only H3 duration plan totals<\/caption>/)
  assert.equal(markup.match(/scope="row"/g)?.length, 5)
  assert.match(markup, /\+20 frames · longer than target/)
  assert.match(markup, /Manual redistribution is disabled/)
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
