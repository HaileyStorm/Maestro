import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compile } from 'tailwindcss'

import {
  chatVisibleHeight,
  observeChatVisualViewport,
} from '../src/lib/chatVisualViewport.ts'

const uiRoot = new URL('../', import.meta.url)

class FakeVisualViewport extends EventTarget {
  constructor(height, offsetTop = 0) {
    super()
    this.height = height
    this.offsetTop = offsetTop
  }
}

class FakeWindow extends EventTarget {
  constructor(viewport) {
    super()
    this.visualViewport = viewport
    this.frames = new Map()
    this.nextFrame = 1
    this.cancelledFrames = []
    this.layoutObservers = []
    const owner = this
    this.ResizeObserver = class {
      constructor(callback) {
        this.callback = callback
        this.observed = []
        this.disconnected = false
        owner.layoutObservers.push(this)
      }

      observe(element) {
        this.observed.push(element)
      }

      disconnect() {
        this.disconnected = true
      }
    }
  }

  requestAnimationFrame = callback => {
    const handle = this.nextFrame++
    this.frames.set(handle, callback)
    return handle
  }

  cancelAnimationFrame = handle => {
    this.cancelledFrames.push(handle)
    this.frames.delete(handle)
  }

  flushFrame() {
    const entries = [...this.frames.entries()]
    this.frames.clear()
    for (const [, callback] of entries) callback(0)
  }
}

test('content-free visual viewport geometry handles portrait, landscape, keyboard, and offset chrome', () => {
  for (const fixture of [
    { name: '320x568 portrait', viewport: { height: 568, offsetTop: 0 }, top: 48, expected: 520 },
    { name: '390x844 portrait', viewport: { height: 844, offsetTop: 0 }, top: 48, expected: 796 },
    { name: '568x320 landscape', viewport: { height: 320, offsetTop: 0 }, top: 48, expected: 272 },
    { name: '767px mobile edge', viewport: { height: 568, offsetTop: 0 }, top: 48, expected: 520 },
    { name: '768px desktop edge', viewport: { height: 568, offsetTop: 0 }, top: 48, expected: 520 },
    { name: 'keyboard shrink', viewport: { height: 286, offsetTop: 0 }, top: 48, expected: 238 },
    { name: 'shifted visual viewport', viewport: { height: 286, offsetTop: 42 }, top: 48, expected: 280 },
  ]) {
    assert.equal(chatVisibleHeight(fixture.viewport, fixture.top), fixture.expected, fixture.name)
  }
  assert.equal(chatVisibleHeight({ height: 100, offsetTop: 20 }, 180), 0)
  assert.equal(chatVisibleHeight({ height: Number.NaN, offsetTop: 0 }, 0), 0)
})

test('observer coalesces resize, scroll, and rotation and removes every listener on cleanup', () => {
  const viewport = new FakeVisualViewport(568)
  const window = new FakeWindow(viewport)
  let top = 48
  const heights = []
  const cleanup = observeChatVisualViewport(
    {
      getBoundingClientRect: () => ({ top }),
      previousElementSibling: { name: 'MainContent toolbar' },
    },
    height => heights.push(height),
    window,
  )

  window.flushFrame()
  assert.deepEqual(heights, [520])
  assert.deepEqual(window.layoutObservers[0].observed, [{ name: 'MainContent toolbar' }])

  viewport.height = 286
  viewport.dispatchEvent(new Event('resize'))
  viewport.dispatchEvent(new Event('scroll'))
  window.dispatchEvent(new Event('resize'))
  assert.equal(window.frames.size, 1, 'bursts share one geometry read')
  window.flushFrame()
  assert.deepEqual(heights, [520, 238])

  top = 28
  viewport.height = 320
  window.dispatchEvent(new Event('orientationchange'))
  window.flushFrame()
  assert.deepEqual(heights, [520, 238, 292])

  top = 64
  window.layoutObservers[0].callback()
  window.flushFrame()
  assert.deepEqual(heights, [520, 238, 292, 256], 'toolbar resize remeasures the shell top')

  viewport.dispatchEvent(new Event('resize'))
  assert.equal(window.frames.size, 1)
  cleanup()
  assert.equal(window.frames.size, 0)
  assert.equal(window.layoutObservers[0].disconnected, true)
  viewport.dispatchEvent(new Event('resize'))
  viewport.dispatchEvent(new Event('scroll'))
  window.dispatchEvent(new Event('resize'))
  window.dispatchEvent(new Event('orientationchange'))
  assert.equal(window.frames.size, 0, 'cleanup prevents work after unmount')
  assert.deepEqual(heights, [520, 238, 292, 256])
})

test('missing visualViewport retains the root 100vh and 100dvh fallback contract', () => {
  const window = new FakeWindow(null)
  const heights = []
  const cleanup = observeChatVisualViewport(
    { getBoundingClientRect: () => ({ top: 48 }) },
    height => heights.push(height),
    window,
  )
  window.flushFrame()
  cleanup()
  assert.deepEqual(heights, [null])
})

test('mobile Chat budgets remain bounded through 768 and relax only at the wide breakpoint', async () => {
  const [source, rootCss, viewportHelper] = await Promise.all([
    readFile(new URL('src/components/LlmChat.tsx', uiRoot), 'utf8'),
    readFile(new URL('src/index.css', uiRoot), 'utf8'),
    readFile(new URL('src/lib/chatVisualViewport.ts', uiRoot), 'utf8'),
  ])
  const compiler = await compile('@theme { --breakpoint-lg: 64rem; } @tailwind utilities;')
  const css = compiler.build([
    'max-h-[24%]',
    'max-h-[46%]',
    'min-h-[30%]',
    'lg:max-h-none',
    'lg:min-h-0',
  ])

  for (const contract of [
    'data-chat-shell',
    'data-chat-transcript',
    'data-chat-composer',
    'max-h-[24%]',
    'max-h-[46%]',
    'min-h-[30%]',
    'lg:max-h-none',
    'lg:min-h-0',
  ]) assert.match(source, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  assert.match(source, /endRef\.current\?\.scrollTo\(\{\s*top: endRef\.current\?\.scrollHeight/)
  assert.doesNotMatch(source, /endRef\.current\?\.scrollIntoView/)
  assert.match(source, /role="region"\s+aria-label="Chat transcript"\s+tabIndex=\{0\}/)
  assert.match(source, /data-chat-composer className="[^"]*overflow-y-auto[^"]*overscroll-contain/)
  assert.match(source, /type="file"\s+tabIndex=\{-1\}\s+aria-hidden="true"/)
  assert.match(source, /className="flex h-11 w-11[^\n]+md:h-9 md:w-9"/)
  assert.match(source, /className="ml-auto flex h-11[^\n]+md:h-10"/)
  assert.match(rootCss, /body\s*\{[^}]*overflow: hidden/s)
  assert.match(rootCss, /#root\s*\{[^}]*height: 100vh;[^}]*height: 100dvh;/s)
  assert.doesNotMatch(viewportHelper, /document|body|textContent|innerHTML/)
  assert.match(css, /@media \(width >= 64rem\)/)
  assert.match(css, /max-height: 24%/)
  assert.match(css, /max-height: 46%/)
  assert.match(css, /min-height: 30%/)
})

test('intrinsically long header and composer states cannot consume the transcript budget', () => {
  const layout = (width, height, headerContentHeight, composerContentHeight) => {
    const bounded = width < 1024
    const header = bounded ? Math.min(headerContentHeight, height * 0.24) : headerContentHeight
    const composer = bounded ? Math.min(composerContentHeight, height * 0.46) : composerContentHeight
    return {
      header,
      composer,
      transcript: Math.max(bounded ? height * 0.30 : 0, height - header - composer),
    }
  }

  for (const [width, height] of [
    [320, 568],
    [390, 844],
    [568, 320],
    [767, 568],
    [768, 568],
  ]) {
    const result = layout(width, height, 10_000, 10_000)
    assert.equal(result.header, height * 0.24, `${width} header is internally scrollable`)
    assert.equal(result.composer, height * 0.46, `${width} composer is internally scrollable`)
    assert.ok(result.transcript >= height * 0.30, `${width} retains a useful transcript`)
  }
})
