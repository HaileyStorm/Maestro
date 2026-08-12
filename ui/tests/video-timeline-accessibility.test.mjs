import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

const componentUrl = new URL('../src/components/shared/VideoTimelineSelector.tsx', import.meta.url)

function childrenOf(node) {
  if (!node || typeof node !== 'object') return []
  const children = node.props?.children
  return Array.isArray(children) ? children : children == null ? [] : [children]
}

function findNodes(node, predicate, matches = []) {
  if (node && typeof node === 'object' && predicate(node)) matches.push(node)
  for (const child of childrenOf(node)) findNodes(child, predicate, matches)
  return matches
}

async function loadTimeline() {
  const modules = new Map([
    ['react', `
      export function useState(initial) {
        const index = globalThis.__timelineStateIndex++
        if (!(index in globalThis.__timelineState)) {
          globalThis.__timelineState[index] = typeof initial === 'function' ? initial() : initial
        }
        return [globalThis.__timelineState[index], value => {
          const current = globalThis.__timelineState[index]
          globalThis.__timelineState[index] = typeof value === 'function' ? value(current) : value
        }]
      }
      export function useRef(initial) {
        const index = globalThis.__timelineRefIndex++
        if (!globalThis.__timelineRefs[index]) globalThis.__timelineRefs[index] = { current: initial }
        return globalThis.__timelineRefs[index]
      }
      export function useEffect() {}
      export function useCallback(callback) { return callback }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
  ])
  const result = await build({
    absWorkingDir: new URL('../', import.meta.url).pathname,
    entryPoints: [componentUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'timeline-harness',
      setup(bundle) {
        bundle.onResolve({ filter: /^(react|react\/jsx-runtime)$/ }, args => ({
          path: args.path,
          namespace: 'timeline-test',
        }))
        bundle.onLoad({ filter: /.*/, namespace: 'timeline-test' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiled = { exports: {} }
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    createRequire(import.meta.url),
    compiled,
    compiled.exports,
  )
  return compiled.exports.VideoTimelineSelector
}

function resetHarness() {
  globalThis.__timelineStateIndex = 0
  globalThis.__timelineState = []
  globalThis.__timelineRefIndex = 0
  globalThis.__timelineRefs = []
}

function renderTimeline(Component, overrides = {}, initialThumbnails = []) {
  resetHarness()
  globalThis.__timelineState[0] = initialThumbnails
  const starts = []
  const ends = []
  const props = {
    videoUrl: '/content-free.mp4',
    duration: 10,
    startTime: 2,
    endTime: 8,
    onStartChange: value => starts.push(value),
    onEndChange: value => ends.push(value),
    ...overrides,
  }
  const tree = Component(props)
  const handles = findNodes(tree, node => node.props?.role === 'slider')
  const track = findNodes(tree, node => node.props?.['data-timeline-track'] !== undefined)[0]
  assert.equal(handles.length, 2)
  assert.ok(track)
  return {
    ends,
    handles,
    starts,
    track,
    start: handles.find(node => node.props['data-timeline-handle'] === 'start'),
    end: handles.find(node => node.props['data-timeline-handle'] === 'end'),
    tree,
  }
}

function keyEvent(key) {
  return {
    key,
    prevented: 0,
    stopped: 0,
    preventDefault() { this.prevented += 1 },
    stopPropagation() { this.stopped += 1 },
  }
}

function pointerTarget({ captureFails = false } = {}) {
  return {
    captured: new Set(),
    released: [],
    setPointerCapture(pointerId) {
      if (captureFails) throw new Error('capture unavailable')
      this.captured.add(pointerId)
    },
    hasPointerCapture(pointerId) { return this.captured.has(pointerId) },
    releasePointerCapture(pointerId) {
      this.captured.delete(pointerId)
      this.released.push(pointerId)
    },
  }
}

function pointerEvent(pointerId, clientX, currentTarget) {
  return {
    pointerId,
    clientX,
    currentTarget,
    prevented: 0,
    stopped: 0,
    preventDefault() { this.prevented += 1 },
    stopPropagation() { this.stopped += 1 },
  }
}

test('rendered handles expose complete horizontal slider semantics and decorative filmstrip images', async () => {
  const Component = await loadTimeline()
  const { start, end, tree } = renderTimeline(Component, {}, ['data:image/jpeg;base64,content-free'])

  assert.deepEqual({
    role: start.props.role,
    label: start.props['aria-label'],
    orientation: start.props['aria-orientation'],
    min: start.props['aria-valuemin'],
    max: start.props['aria-valuemax'],
    now: start.props['aria-valuenow'],
    text: start.props['aria-valuetext'],
    tabIndex: start.props.tabIndex,
  }, {
    role: 'slider', label: 'Start time', orientation: 'horizontal',
    min: 0, max: 7.9, now: 2, text: '2.0 seconds', tabIndex: 0,
  })
  assert.deepEqual({
    label: end.props['aria-label'], min: end.props['aria-valuemin'],
    max: end.props['aria-valuemax'], now: end.props['aria-valuenow'],
    text: end.props['aria-valuetext'], tabIndex: end.props.tabIndex,
  }, {
    label: 'End time', min: 2.1, max: 10, now: 8,
    text: '8.0 seconds', tabIndex: 0,
  })
  const images = findNodes(tree, node => node.type === 'img')
  assert.equal(images.length, 1)
  assert.ok(images.every(node => node.props.alt === '' && node.props['aria-hidden'] === 'true'))
})

test('keyboard arrows, pages, Home, and End share exact tenth-second bounds without duplicate callbacks', async () => {
  const Component = await loadTimeline()
  const { start, end, starts, ends } = renderTimeline(Component)

  for (const [handle, key, expected] of [
    [start, 'ArrowRight', 2.1],
    [start, 'ArrowUp', 2.2],
    [start, 'PageUp', 3.2],
    [start, 'End', 7.9],
    [start, 'End', null],
    [start, 'PageDown', 6.9],
    [start, 'Home', 0],
    [start, 'ArrowLeft', null],
  ]) {
    const event = keyEvent(key)
    handle.props.onKeyDown(event)
    assert.equal(event.prevented, 1, `${key} prevents page scrolling`)
    assert.equal(event.stopped, 1, `${key} stays within the slider`)
    if (expected !== null) assert.equal(starts.at(-1), expected)
  }
  assert.deepEqual(starts, [2.1, 2.2, 3.2, 7.9, 6.9, 0])

  for (const [key, expected] of [['Home', 0.1], ['PageUp', 1.1], ['End', 10], ['End', null]]) {
    const event = keyEvent(key)
    end.props.onKeyDown(event)
    if (expected !== null) assert.equal(ends.at(-1), expected)
  }
  assert.deepEqual(ends, [0.1, 1.1, 10])

  const unhandled = keyEvent('Enter')
  start.props.onKeyDown(unhandled)
  assert.equal(unhandled.prevented, 0)
  assert.equal(unhandled.stopped, 0)
})

test('captured pointer drag preserves its grab offset, bounds, nearest-handle routing, and cancellation path', async () => {
  const Component = await loadTimeline()
  const fixture = renderTimeline(Component)
  fixture.track.props.ref.current = {
    getBoundingClientRect: () => ({ left: 100, width: 500 }),
  }
  const target = pointerTarget()
  const down = pointerEvent(7, 350, target)
  fixture.start.props.onPointerDown(down)
  assert.equal(down.prevented, 1)
  assert.equal(down.stopped, 1)
  assert.equal(target.captured.has(7), true)
  assert.deepEqual(fixture.starts, [], 'pressing anywhere in the enlarged target never jumps the value')

  fixture.start.props.onPointerMove(pointerEvent(7, 350, target))
  assert.deepEqual(fixture.starts, [], 'same pointer position is a no-op after capture')
  fixture.start.props.onPointerMove(pointerEvent(99, 600, target))
  assert.deepEqual(fixture.starts, [], 'a second pointer cannot hijack the captured drag')
  fixture.start.props.onPointerMove(pointerEvent(7, 600, target))
  assert.deepEqual(fixture.starts, [7], 'moving preserves the initial three-second grab offset')
  fixture.start.props.onPointerMove(pointerEvent(7, 700, target))
  assert.deepEqual(fixture.starts, [7, 7.9], 'crossing clamps at the same legal maximum as End key')
  fixture.start.props.onPointerUp(pointerEvent(7, 600, target))
  assert.deepEqual(target.released, [7])
  fixture.start.props.onPointerMove(pointerEvent(7, 100, target))
  assert.deepEqual(fixture.starts, [7, 7.9], 'released drag cannot mutate the range')

  const endFixture = renderTimeline(Component)
  endFixture.track.props.ref.current = fixture.track.props.ref.current
  const endTarget = pointerTarget()
  endFixture.end.props.onPointerDown(pointerEvent(8, 500, endTarget))
  endFixture.end.props.onPointerMove(pointerEvent(8, 100, endTarget))
  assert.deepEqual(endFixture.ends, [2.1], 'end crossing clamps to start plus one tenth')
  endFixture.end.props.onPointerCancel(pointerEvent(8, 100, endTarget))
  endFixture.end.props.onPointerMove(pointerEvent(8, 600, endTarget))
  assert.deepEqual(endFixture.ends, [2.1], 'pointer cancellation ends the interaction')

  const routed = renderTimeline(Component)
  routed.track.props.ref.current = fixture.track.props.ref.current
  routed.end.props.onPointerDown(pointerEvent(9, 200, pointerTarget()))
  assert.deepEqual(routed.starts, [])
  assert.deepEqual(routed.ends, [])
})

test('boundary hit areas never jump and capture failure leaves the next interaction usable', async () => {
  const Component = await loadTimeline()
  const fixture = renderTimeline(Component, { duration: 60, startTime: 0, endTime: 60 })
  fixture.track.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 320 }) }

  const failedTarget = pointerTarget({ captureFails: true })
  fixture.start.props.onPointerDown(pointerEvent(1, 22, failedTarget))
  fixture.start.props.onPointerMove(pointerEvent(1, 100, failedTarget))
  assert.deepEqual(fixture.starts, [], 'failed capture never records a wedged active pointer')

  const startTarget = pointerTarget()
  fixture.start.props.onPointerDown(pointerEvent(2, 22, startTarget))
  assert.deepEqual(fixture.starts, [], 'center of the 44px start target preserves zero')
  fixture.start.props.onPointerMove(pointerEvent(2, 32, startTarget))
  assert.deepEqual(fixture.starts, [1.9], 'drag changes only by the pointer delta after capture')
  fixture.start.props.onPointerUp(pointerEvent(2, 32, startTarget))

  const endTarget = pointerTarget()
  fixture.end.props.onPointerDown(pointerEvent(3, 298, endTarget))
  assert.deepEqual(fixture.ends, [], 'center of the 44px end target preserves exact duration')
  fixture.end.props.onPointerMove(pointerEvent(3, 288, endTarget))
  assert.deepEqual(fixture.ends, [58.1], 'end drag also preserves its grab offset')
})

test('non-tenth media boundaries remain reachable and invalid finite ranges stay inert', async () => {
  const Component = await loadTimeline()
  const exact = renderTimeline(Component, { duration: 10.04, startTime: 2, endTime: 10.04 })
  const startEnd = keyEvent('End')
  exact.start.props.onKeyDown(startEnd)
  assert.deepEqual(exact.starts, [9.94])

  const exactEnd = renderTimeline(Component, { duration: 10.04, startTime: 2, endTime: 9 })
  exactEnd.end.props.onKeyDown(keyEvent('End'))
  assert.deepEqual(exactEnd.ends, [10.04])

  const invalid = renderTimeline(Component, { duration: 10, startTime: 12, endTime: 15 })
  for (const handle of invalid.handles) {
    assert.equal(handle.props['aria-invalid'], true)
    assert.equal(handle.props['aria-disabled'], true)
    assert.equal(handle.props.tabIndex, -1)
  }
  invalid.start.props.onKeyDown(keyEvent('Home'))
  assert.deepEqual(invalid.starts, [])
  assert.deepEqual(invalid.ends, [])
})

test('unavailable metadata state is non-focusable, announced disabled, and mutation-free', async () => {
  const Component = await loadTimeline()
  const fixture = renderTimeline(Component, { duration: 0, startTime: 0, endTime: 0 })
  for (const handle of fixture.handles) {
    assert.equal(handle.props['aria-disabled'], true)
    assert.equal(handle.props['aria-invalid'], undefined)
    assert.equal(handle.props.tabIndex, -1)
    const event = keyEvent('ArrowRight')
    handle.props.onKeyDown(event)
    assert.equal(event.prevented, 0)
  }
  const target = pointerTarget()
  fixture.start.props.onPointerDown(pointerEvent(1, 100, target))
  assert.equal(target.captured.size, 0)
  assert.deepEqual(fixture.starts, [])
  assert.deepEqual(fixture.ends, [])

  for (const props of [
    { duration: 0.05, startTime: 0, endTime: 0.05 },
    { duration: 0.1, startTime: 0, endTime: 0.1 },
  ]) {
    const threshold = renderTimeline(Component, props)
    for (const handle of threshold.handles) {
      assert.equal(handle.props['aria-disabled'], true)
      assert.equal(handle.props['aria-invalid'], undefined)
      assert.equal(handle.props.tabIndex, -1)
    }
  }

  const oneFixed = renderTimeline(Component, { duration: 0.2, startTime: 0, endTime: 0.1 })
  assert.equal(oneFixed.start.props['aria-disabled'], true)
  assert.equal(oneFixed.start.props.tabIndex, -1)
  assert.equal(oneFixed.end.props['aria-disabled'], false)
  assert.equal(oneFixed.end.props.tabIndex, 0)
  oneFixed.track.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 320 }) }
  const disabledTarget = pointerTarget()
  oneFixed.start.props.onPointerDown(pointerEvent(4, 22, disabledTarget))
  assert.equal(disabledTarget.captured.size, 0, 'disabled start never delegates pointer work to end')
  assert.deepEqual(oneFixed.starts, [])
  assert.deepEqual(oneFixed.ends, [])

  const reverseFixed = renderTimeline(Component, { duration: 10, startTime: 9.9, endTime: 10 })
  assert.equal(reverseFixed.start.props['aria-disabled'], false)
  assert.match(reverseFixed.start.props.className, /\bz-20\b/)
  assert.equal(reverseFixed.end.props['aria-disabled'], true)
  assert.match(reverseFixed.end.props.className, /\bz-10\b/)
  reverseFixed.track.props.ref.current = { getBoundingClientRect: () => ({ left: 0, width: 320 }) }
  const adjustableTarget = pointerTarget()
  reverseFixed.start.props.onPointerDown(pointerEvent(5, 298, adjustableTarget))
  assert.equal(adjustableTarget.captured.has(5), true, 'adjustable start stays above overlapping disabled end')
  reverseFixed.start.props.onPointerMove(pointerEvent(5, 278, adjustableTarget))
  assert.ok(reverseFixed.starts[0] < 9.9)
})

test('touch, focus, narrow-width, zoom, and reduced-motion contracts remain executable CSS', async () => {
  const [Component, source] = await Promise.all([
    loadTimeline(),
    readFile(componentUrl, 'utf8'),
  ])
  const { handles } = renderTimeline(Component)
  for (const handle of handles) {
    assert.match(handle.props.className, /min-h-11/)
    assert.match(handle.props.className, /w-11 min-w-11/)
    assert.match(handle.props.className, /touch-none/)
    assert.match(handle.props.className, /focus-visible:ring-2/)
    assert.match(handle.props.style.left, /^clamp\(0px, calc\(.+% - 22px\), calc\(100% - 44px\)\)$/)
  }
  assert.match(source, /flex-wrap/)
  assert.match(source, /min-w-0/)
  assert.doesNotMatch(source, /transition-|animate-|scrollBehavior:\s*['"]smooth/)

  const compiler = await compile('@theme { --spacing: 0.25rem; } @tailwind utilities;')
  const css = compiler.build(['min-h-11', 'w-11', 'min-w-11', 'touch-none', 'focus-visible:ring-2'])
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /touch-action: none/)
  assert.match(css, /:focus-visible/)

  for (const [width, height] of [
    [160, 284], // 320x568 at 200% zoom
    [320, 568],
    [390, 844],
    [568, 320],
    [767, 390],
    [768, 430],
  ]) {
    assert.ok(width >= 44, `${width}x${height} retains a 44px horizontal slider target`)
    assert.ok(height >= 44, `${width}x${height} retains a 44px vertical slider target`)
    for (const pct of [0, 0.5, 1]) {
      const targetLeft = Math.max(0, Math.min(width * pct - 22, width - 44))
      assert.ok(targetLeft >= 0 && targetLeft + 44 <= width, `${width}px clamps the full target inside the track`)
    }
  }
})
