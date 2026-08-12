import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'
import { compile } from 'tailwindcss'

const tabFilterUrl = new URL('../src/components/MainContent/TabFilter.tsx', import.meta.url)

function findElements(node, predicate, matches = []) {
  if (node === null || node === undefined || typeof node === 'boolean') return matches
  if (Array.isArray(node)) {
    for (const child of node) findElements(child, predicate, matches)
    return matches
  }
  if (typeof node !== 'object') return matches
  if (predicate(node)) matches.push(node)
  findElements(node.props?.children, predicate, matches)
  return matches
}

function materialize(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return node
  if (Array.isArray(node)) return node.map(materialize)
  if (typeof node !== 'object') return node
  if (typeof node.type === 'function') return materialize(node.type(node.props || {}))
  return {
    ...node,
    props: {
      ...(node.props || {}),
      children: materialize(node.props?.children),
    },
  }
}

async function loadTabFilterHarness() {
  const modules = new Map([
    ['react', `
      export function useState(initial) { return globalThis.__mobileGalleryHooks.useState(initial) }
      export function useEffect() {}
      export function useRef(initial) { return globalThis.__mobileGalleryHooks.useRef(initial) }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      export const Heart = icon
      export const Film = icon
      export const Search = icon
      export const SlidersHorizontal = icon
      export const X = icon
    `],
    ['../../stores/useStore', `
      export function useStore(selector) { return selector(globalThis.__mobileGalleryStore) }
    `],
  ])
  const result = await build({
    entryPoints: [tabFilterUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'mobile-gallery-filter-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'mobile-gallery-filter' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'mobile-gallery-filter' }, args => ({
          contents: modules.get(args.path),
          loader: 'js',
        }))
      },
    }],
  })
  const compiledModule = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', result.outputFiles[0].text)(
    require,
    compiledModule,
    compiledModule.exports,
  )
  return compiledModule.exports.TabFilter
}

function createSearchRuntime() {
  const states = [true]
  const initialized = new Set([0])
  const refs = []
  const frames = []
  let stateCursor = 0
  let refCursor = 0
  const calls = []
  const store = {
    mediaFilter: 'all',
    outputArtifactScope: 'final',
    outputSearchQuery: 'portrait model:"h3"',
    setMediaFilter() {},
    setOutputArtifactScope() {},
    setOutputSearchQuery(value) { calls.push(value) },
    resetGalleryFilters() {},
  }
  const hooks = {
    begin() { stateCursor = 0; refCursor = 0 },
    useState(initial) {
      const index = stateCursor++
      if (!initialized.has(index)) {
        states[index] = typeof initial === 'function' ? initial() : initial
        initialized.add(index)
      }
      return [states[index], value => {
        states[index] = typeof value === 'function' ? value(states[index]) : value
      }]
    },
    useRef(initial) {
      const index = refCursor++
      if (!refs[index]) refs[index] = { current: initial }
      return refs[index]
    },
  }
  return {
    calls,
    frames,
    hooks,
    refs,
    states,
    store,
    window: {
      requestAnimationFrame(callback) { frames.push(callback); return frames.length },
      cancelAnimationFrame() {},
      setTimeout() { return 1 },
      clearTimeout() {},
    },
  }
}

test('Gallery search and filter controls keep mobile targets and compact at 768px', async () => {
  const source = await readFile(tabFilterUrl, 'utf8')

  const facetButton = source.slice(
    source.indexOf('function facetButton'),
    source.indexOf('export function TabFilter'),
  )
  assert.match(facetButton, /min-h-11 min-w-11/)
  assert.match(facetButton, /md:min-h-0 md:min-w-0/)

  const targetOwners = [
    source.match(/<button\s+ref=\{searchTriggerRef\}[\s\S]*?<\/button>/)?.[0],
    source.match(/<button type="button" onClick=\{closeSearch\}[\s\S]*?<\/button>/)?.[0],
    source.match(/<button\s+ref=\{filterTriggerRef\}[\s\S]*?<\/button>/)?.[0],
    source.match(/<button type="button" onClick=\{resetAllFilters\}[\s\S]*?<\/button>/)?.[0],
    source.match(/<button type="button" onClick=\{closeFilterPopover\}[\s\S]*?<\/button>/)?.[0],
    source.match(/<button type="button" onClick=\{clearStructuredFilters\}[\s\S]*?<\/button>/)?.[0],
  ]
  for (const targetOwner of targetOwners) {
    assert.ok(targetOwner, 'requested Gallery target owner remains present')
    assert.match(targetOwner, /min-h-11/)
    assert.match(targetOwner, /min-w-11/)
    assert.match(targetOwner, /md:min-h-0/)
    assert.match(targetOwner, /md:min-w-0/)
  }

  const searchInput = source.match(/<input\s+ref=\{searchRef\}[\s\S]*?\/>/)?.[0]
  assert.ok(searchInput)
  assert.match(searchInput, /min-h-11/)
  assert.match(searchInput, /md:min-h-0/)

  const metadataControls = source.slice(
    source.indexOf('<div className="grid grid-cols-1 gap-2 sm:grid-cols-2">'),
    source.lastIndexOf('</div>'),
  )
  assert.equal((metadataControls.match(/className="min-h-11 w-full/g) || []).length, 6)
  assert.equal((metadataControls.match(/md:min-h-0/g) || []).length, 6)

  const compiler = await compile('@theme { --spacing: 0.25rem; --breakpoint-md: 48rem; } @tailwind utilities;')
  const css = compiler.build(['min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-w-0'])
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /@media \(width >= 48rem\)/)
})

test('Gallery disclosures and focus exits retain accessible state and exact filter semantics', async () => {
  const source = await readFile(tabFilterUrl, 'utf8')

  assert.match(source, /aria-expanded=\{false\}[\s\S]{0,100}aria-controls="gallery-search-controls"/)
  assert.match(source, /id="gallery-search-controls" role="search" aria-label="Search Gallery"/)
  assert.match(source, /aria-expanded=\{filtersOpen\}/)
  assert.match(source, /aria-haspopup="dialog"/)
  assert.match(source, /aria-controls="gallery-filter-popover"/)
  assert.match(source, /role="dialog"[\s\S]{0,160}aria-labelledby="gallery-filter-title"[\s\S]{0,160}aria-describedby="gallery-filter-description"/)

  assert.match(source, /if \(event\.key !== 'Escape'\) return[\s\S]{0,180}closeSearch\(\)/)
  assert.match(source, /closeSearch[\s\S]*requestAnimationFrame\(\(\) => searchTriggerRef\.current\?\.focus\(\)\)/)
  assert.match(source, /if \(event\.key !== 'Escape'\) return[\s\S]{0,220}setFiltersOpen\(false\)[\s\S]{0,140}trigger\?\.focus\(\)/)
  assert.match(source, /dialog\?\.contains\(target\) \|\| trigger\?\.contains\(target\)[\s\S]{0,100}setFiltersOpen\(false\)/)

  assert.match(source, /setSearchQuery\(buildOutputSearchQuery\('', filters\)\)/)
  assert.match(source, /setSearchQuery\(buildOutputSearchQuery\(draftSearch, next\)\)/)
  assert.match(source, /setSearchQuery\(buildOutputSearchQuery\(draftSearch\)\)/)
  assert.match(source, /resetGalleryFilters\(\)/)
})

test('Search Escape clears only free text and restores the remounted trigger', async t => {
  const originalWindow = globalThis.window
  const TabFilter = await loadTabFilterHarness()
  const runtime = createSearchRuntime()
  globalThis.window = runtime.window
  globalThis.__mobileGalleryHooks = runtime.hooks
  globalThis.__mobileGalleryStore = runtime.store
  t.after(() => {
    globalThis.window = originalWindow
    delete globalThis.__mobileGalleryHooks
    delete globalThis.__mobileGalleryStore
  })

  runtime.hooks.begin()
  let tree = materialize(TabFilter())
  const searchInput = findElements(tree, element => (
    element.type === 'input' && element.props?.['aria-label'] === 'Search Gallery'
  ))[0]
  assert.ok(searchInput)
  let prevented = 0
  let propagationStopped = 0
  searchInput.props.onKeyDown({
    key: 'Escape',
    preventDefault() { prevented += 1 },
    stopPropagation() { propagationStopped += 1 },
  })
  assert.equal(prevented, 1)
  assert.equal(propagationStopped, 1)
  assert.equal(runtime.states[0], false)
  assert.equal(runtime.calls.at(-1), 'model:"h3"')

  runtime.hooks.begin()
  tree = materialize(TabFilter())
  const trigger = findElements(tree, element => (
    element.type === 'button' && element.props?.['aria-controls'] === 'gallery-search-controls'
  ))[0]
  assert.ok(trigger)
  const triggerNode = { focusCount: 0, focus() { this.focusCount += 1 } }
  trigger.props.ref.current = triggerNode
  for (const frame of runtime.frames.splice(0)) frame()
  assert.equal(triggerNode.focusCount, 1)
})

test('Gallery filter popover stays inside narrow and zoomed visual viewports', async () => {
  const source = await readFile(tabFilterUrl, 'utf8')
  const popoverStart = source.indexOf('id="gallery-filter-popover"')
  const popover = source.slice(popoverStart, popoverStart + 1200)

  assert.match(popover, /fixed inset-x-2/)
  assert.match(popover, /bottom-\[max\(0\.5rem,env\(safe-area-inset-bottom\)\)\]/)
  assert.match(popover, /top-\[max\(4rem,env\(safe-area-inset-top\)\)\]/)
  assert.match(popover, /overflow-y-auto overscroll-contain/)
  assert.match(popover, /\[-webkit-overflow-scrolling:touch\]/)
  assert.match(popover, /lg:absolute/)
  assert.match(popover, /lg:w-\[min\(440px,calc\(100vw-1rem\)\)\]/)
  assert.doesNotMatch(popover, /sm:absolute|md:absolute/)
})
