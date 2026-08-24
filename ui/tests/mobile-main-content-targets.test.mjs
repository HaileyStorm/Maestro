import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { transform } from 'esbuild'
import { compile } from 'tailwindcss'

const mainUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadTabKeyboardContract(source) {
  const start = source.indexOf("const MAIN_VIEWS =")
  const end = source.indexOf('type QueueTabSnapshot', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  const runtime = `
    const Fragment = Symbol.for('main-view-fragment')
    const jsx = (type, props, ...children) => ({
      type,
      props: {
        ...(props || {}),
        ...(children.length > 0 ? { children: children.length === 1 ? children[0] : children } : {}),
      },
    })
  `
  const result = await transform(
    `${runtime}\n${source.slice(start, end)}\nexport { MAIN_VIEWS, MainViewPanels, MainViewTabs, nextMainViewFromKey }\n`,
    { format: 'esm', jsx: 'transform', jsxFactory: 'jsx', jsxFragment: 'Fragment', loader: 'tsx', target: 'es2022' },
  )
  return import(asDataModule(result.code))
}

async function loadEmptyStateContracts(source) {
  const start = source.indexOf('type GalleryEmptyState')
  const end = source.indexOf('type ResourcePresentation', start)
  assert.notEqual(start, -1)
  assert.notEqual(end, -1)
  const result = await transform(
    `${source.slice(start, end)}\nexport { galleryEmptyState, queuePanelEmptyState }\n`,
    { format: 'esm', loader: 'ts', target: 'es2022' },
  )
  return import(asDataModule(result.code))
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

function sourceRegion(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  const end = source.indexOf(endMarker, start)
  assert.ok(start >= 0 && end > start, `bounded region ${startMarker}`)
  return source.slice(start, end)
}

test('MainContent exposes one roving, keyboard-operable tab set and selected panel', async () => {
  const source = await readFile(mainUrl, 'utf8')
  const { MAIN_VIEWS, MainViewPanels, MainViewTabs, nextMainViewFromKey } = await loadTabKeyboardContract(source)

  assert.deepEqual(MAIN_VIEWS, ['gallery', 'queue', 'chat'])
  assert.equal(nextMainViewFromKey('gallery', 'ArrowRight'), 'queue')
  assert.equal(nextMainViewFromKey('chat', 'ArrowRight'), 'gallery')
  assert.equal(nextMainViewFromKey('gallery', 'ArrowLeft'), 'chat')
  assert.equal(nextMainViewFromKey('queue', 'Home'), 'gallery')
  assert.equal(nextMainViewFromKey('queue', 'End'), 'chat')
  assert.equal(nextMainViewFromKey('queue', 'Enter'), null)

  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const selected = []
  let focused = null
  globalThis.window = { requestAnimationFrame(callback) { callback(); return 1 } }
  globalThis.document = {
    getElementById(id) {
      return { focus() { focused = id } }
    },
  }
  try {
    const tree = MainViewTabs({
      activeView: 'gallery',
      onSelect(view) { selected.push(view) },
      queueTitle: 'Queue status',
      queueStateColor: 'queue-green',
      activeQueueCount: 2,
      queueStateLabel: '2 active',
      queueDetails: jsxSentinel('details'),
    })
    assert.equal(tree.props.role, 'tablist')
    assert.equal(tree.props['aria-label'], 'Main views')
    assert.match(tree.props.className, /overflow-x-auto/)
    const tabs = flattenElements(tree).filter(element => element.props?.role === 'tab')
    assert.equal(tabs.length, 3)
    for (const [index, view] of MAIN_VIEWS.entries()) {
      assert.equal(tabs[index].props.id, `main-${view}-tab`)
      assert.equal(tabs[index].props['aria-controls'], `main-${view}-panel`)
      assert.equal(tabs[index].props['aria-selected'], view === 'gallery')
      assert.equal(tabs[index].props.tabIndex, view === 'gallery' ? 0 : -1)
    }

    let prevented = false
    tabs[0].props.onKeyDown({ key: 'ArrowRight', preventDefault() { prevented = true } })
    assert.equal(prevented, true)
    assert.deepEqual(selected, ['queue'])
    assert.equal(focused, 'main-queue-tab')
    tabs[2].props.onClick()
    assert.deepEqual(selected, ['queue', 'chat'])

    const rerendered = MainViewTabs({
      activeView: 'queue', onSelect() {}, queueTitle: '', queueStateColor: '',
      activeQueueCount: 0, queueStateLabel: '',
    })
    const rerenderedTabs = flattenElements(rerendered).filter(element => element.props?.role === 'tab')
    assert.deepEqual(rerenderedTabs.map(tab => tab.props['aria-selected']), [false, true, false])
    assert.deepEqual(rerenderedTabs.map(tab => tab.props.tabIndex), [-1, 0, -1])

    const panels = flattenElements(MainViewPanels({ activeView: 'queue', children: jsxSentinel('queue content') }))
      .filter(element => element.props?.role === 'tabpanel')
    assert.equal(panels.length, 3)
    for (const view of MAIN_VIEWS) {
      const panel = panels.find(candidate => candidate.props.id === `main-${view}-panel`)
      assert.ok(panel)
      assert.equal(panel.props['aria-labelledby'], `main-${view}-tab`)
      assert.equal(Boolean(panel.props.hidden), view !== 'queue')
      assert.equal(panel.props.className === 'hidden', view !== 'queue')
    }
    assert.equal(panels.find(panel => panel.props.id === 'main-queue-panel').props.children.props.label, 'queue content')
  } finally {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
  }
})

function jsxSentinel(label) {
  return { type: 'sentinel', props: { label } }
}

test('MainContent toolbar keeps a stable two-row hierarchy across views', async () => {
  const source = await readFile(mainUrl, 'utf8')
  const topBar = sourceRegion(source, '{/* Top bar */}', '{/* Content area: feed + thumbnails */}')
  const primaryStart = topBar.indexOf('data-main-toolbar-primary')
  const navigationStart = topBar.indexOf('data-main-toolbar-navigation')
  const viewStart = topBar.indexOf('data-main-toolbar-view')
  assert.ok(primaryStart >= 0 && navigationStart > primaryStart && viewStart > navigationStart)
  const primaryRow = topBar.slice(primaryStart, viewStart)
  const navigationLane = topBar.slice(navigationStart, viewStart)
  const viewRow = topBar.slice(viewStart)

  for (const hook of ['data-main-toolbar', 'data-main-toolbar-primary', 'data-main-toolbar-view']) {
    assert.equal((topBar.match(new RegExp(`${hook}(?!-)`, 'g')) ?? []).length, 1)
  }
  assert.match(topBar, /grid min-w-0 grid-rows-\[auto_auto\]/)
  assert.doesNotMatch(topBar, /flex flex-wrap items-start justify-between/)
  assert.match(primaryRow, /flex min-w-0 flex-nowrap items-center/)
  assert.doesNotMatch(primaryRow.match(/className="[^"]+"/)?.[0] ?? '', /overflow/)
  assert.match(navigationLane, /flex min-w-0 flex-1 items-center[^"\n]*overflow-x-auto/)
  assert.match(viewRow, /flex min-h-11 min-w-0 flex-nowrap items-center[^"\n]*overflow-x-auto[^"\n]*md:min-h-8[^"\n]*lg:overflow-visible/)
  assert.match(source, /<\/div>\n      \{\/\* Content area: feed \+ thumbnails \*\/\}\n      <MainViewPanels/)

  assert.equal((topBar.match(/<MainViewTabs/g) ?? []).length, 1)
  assert.equal((topBar.match(/<TabFilter/g) ?? []).length, 1)
  assert.equal((topBar.match(/<WorkspaceSelector/g) ?? []).length, 1)
  assert.match(navigationLane, /<MainViewTabs[\s\S]*outputsTotal[\s\S]*cloudflare_enabled/)
  assert.match(primaryRow, /data-main-toolbar-navigation[\s\S]*shrink-0 md:mr-28[^>]*><WorkspaceSelector/)
  assert.doesNotMatch(primaryRow, /<TabFilter/)
  assert.match(viewRow, /mainView === 'gallery' \? <>[\s\S]*<TabFilter[\s\S]*private-preview-session-note[\s\S]*setGallerySelectionMode/)
  assert.match(viewRow, /<h2[^>]*>[\s\S]*mainView === 'queue' \? 'Queue' : 'LLM Chat'[\s\S]*<\/h2>/)
})

test('MainContent mobile targets remain 44px through 767 and keep narrow layouts local', async () => {
  const source = await readFile(mainUrl, 'utf8')
  const topBar = sourceRegion(source, '{/* Top bar */}', '{/* Content area: feed + thumbnails */}')
  const workspace = sourceRegion(source, 'function WorkspaceSelector()', '// How many items to render beyond the viewport')
  const queuePanel = sourceRegion(source, 'function QueuePanel({', 'function GalleryBulkToolbar()')
  const bulkToolbar = sourceRegion(source, 'function GalleryBulkToolbar()', 'function PipelinePlaceholder()')

  assert.match(topBar, /<MainViewTabs/)
  assert.match(topBar, /\[&_button\]:min-h-11/)
  assert.match(topBar, /\[&_button\]:min-w-11/)
  assert.match(topBar, /\[&_input:not\(\[type=checkbox\]\)\]:min-h-11/)
  assert.match(topBar, /\[&_label\]:min-h-11/)
  assert.match(topBar, /\[&_select\]:min-h-11/)
  assert.match(topBar, /md:\[&_button\]:min-h-0/)
  assert.match(topBar, /md:\[&_button\]:min-w-0/)
  assert.match(topBar, /md:\[&_input:not\(\[type=checkbox\]\)\]:min-h-0/)
  assert.match(topBar, /md:\[&_label\]:min-h-0/)
  assert.match(topBar, /md:\[&_select\]:min-h-0/)
  assert.match(workspace, /opacity-100 hover:text-red-400 md:opacity-0/)
  assert.match(workspace, /max-w-\[120px\] truncate md:hidden lg:inline/)
  assert.match(workspace, /flex min-h-11 cursor-pointer items-start[^"\n]*md:min-h-0/)
  assert.match(workspace, /fixed left-2 right-2 top-14 z-\[70\] max-h-\[calc\(100vh-4rem\)\] overflow-y-auto/)
  assert.match(workspace, /sm:absolute sm:left-auto sm:right-0 sm:top-full/)
  for (const region of [queuePanel, bulkToolbar]) {
    assert.match(region, /\[&_button\]:min-h-11/)
    assert.match(region, /\[&_button\]:min-w-11/)
    assert.match(region, /md:\[&_button\]:min-h-0/)
    assert.match(region, /md:\[&_button\]:min-w-0/)
  }
  assert.match(queuePanel, /\[&_input:not\(\[type=checkbox\]\)\]:min-h-11/)
  assert.match(queuePanel, /\[&_summary\]:min-h-11/)
  assert.match(queuePanel, /md:\[&_input:not\(\[type=checkbox\]\)\]:min-h-0/)
  assert.match(queuePanel, /md:\[&_summary\]:min-h-0/)
  assert.match(bulkToolbar, /\[&_select\]:min-h-11/)
  assert.match(bulkToolbar, /md:\[&_select\]:min-h-0/)
  assert.doesNotMatch(source, /sm:\[&_button\]:min-h-0/)
  assert.match(source, /className="ml-2 flex min-h-11 min-w-11[^"\n]*md:min-h-0 md:min-w-0"/)

  const compiler = await compile('@theme { --spacing: 0.25rem; --breakpoint-md: 48rem; } @tailwind utilities;')
  const css = compiler.build([
    'min-h-8', 'min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-h-8', 'md:min-w-0',
    '[&_button]:min-h-11', '[&_button]:min-w-11',
    'md:[&_button]:min-h-0', 'md:[&_button]:min-w-0',
    'grid', 'grid-rows-[auto_auto]', 'overflow-x-auto', 'flex-nowrap', 'min-w-0',
  ])
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /overflow-x: auto/)
  assert.match(css, /flex-wrap: nowrap/)
  assert.match(css, /grid-template-rows: auto auto/)
  assert.match(css, /@media \(width >= 48rem\)/)
})

test('Gallery and queue empty states add hierarchy without inventing actions or changing status copy', async () => {
  const source = await readFile(mainUrl, 'utf8')
  const { galleryEmptyState, queuePanelEmptyState } = await loadEmptyStateContracts(source)
  const gallery = sourceRegion(
    source,
    '{/* Empty-state quick start.',
    '{/* Thumbnail sidebar */}',
  )
  const queue = sourceRegion(
    source,
    "{panelEmptyState === 'pending' ? (",
    ') : visibleJobs.map((job, index) => {',
  )

  assert.match(gallery, /<Play size=\{24\} \/>/)
  assert.match(gallery, /<h2[^>]*>No finished \{noun\} yet<\/h2>/)
  assert.match(gallery, /Your generated \{noun\} will appear here\./)
  assert.match(gallery, /Pick a model in the sidebar \(a good default is already selected\)\./)
  assert.match(gallery, /The shared host cache is[\s\S]*loading into RAM\/VRAM is a separate step\./)
  assert.match(gallery, /aria-label="Browse recipes"/)
  assert.match(gallery, /min-h-11 min-w-11[^"]*focus-visible:ring-2/)
  assert.doesNotMatch(gallery, /Generate your first/)
  const projectEmpty = {
    outputsLoading: false,
    outputCount: 0,
    outputsTotal: 0,
    browsingUploads: false,
    activeWorkspace: 'project-a',
    hasActiveFilters: false,
    hasProjectJobs: false,
  }
  assert.equal(galleryEmptyState(projectEmpty), 'onboarding')
  assert.equal(galleryEmptyState({ ...projectEmpty, browsingUploads: true }), 'uploads')
  assert.equal(galleryEmptyState({ ...projectEmpty, hasActiveFilters: true }), 'filtered')
  assert.equal(galleryEmptyState({ ...projectEmpty, activeWorkspace: '' }), 'project-required')
  assert.equal(galleryEmptyState({ ...projectEmpty, hasProjectJobs: true }), 'none')
  assert.equal(galleryEmptyState({ ...projectEmpty, outputsTotal: 1 }), 'none')
  // An unrelated project job leaves hasProjectJobs false, so it cannot hide
  // onboarding for the authoritative empty project.
  assert.equal(galleryEmptyState({ ...projectEmpty, hasProjectJobs: false }), 'onboarding')
  assert.match(source, /jobs\.some\(job => job\.workspace === activeWorkspace\)/)
  assert.match(source, /galleryState === 'onboarding'/)

  assert.match(queue, /<Loader2 size=\{22\} className="animate-spin" \/>/)
  assert.match(queue, /<h3[^>]*>Loading queue<\/h3>/)
  assert.match(queue, /<h3[^>]*>Queue unavailable<\/h3>/)
  assert.match(queue, /The queue is unavailable\. Maestro is retrying automatically\./)
  assert.match(queue, /<ListChecks size=\{22\} \/>/)
  assert.match(queue, /<h3[^>]*>Last known queue is clear<\/h3>/)
  assert.match(queue, /<h3[^>]*>Queue is clear<\/h3>/)
  assert.match(queue, /No queued, running, or failed generations\./)
  assert.doesNotMatch(queue, /<button/)

  const queueState = { jobs: [] }
  assert.equal(queuePanelEmptyState(null, null, null, 0), 'pending')
  assert.equal(queuePanelEmptyState(null, 'offline', null, 0), 'unavailable')
  assert.equal(queuePanelEmptyState(queueState, 'offline', 1, 0), 'cached-stale')
  assert.equal(queuePanelEmptyState(queueState, null, 1, 0), 'empty')
  assert.equal(queuePanelEmptyState(queueState, null, 1, 1), 'none')
})

test('responsive queue controls preserve authorization, bounds, snapshots, and exact IDs', async () => {
  const source = await readFile(mainUrl, 'utf8')

  assert.match(source, /const effectiveJob = target\?\.schedulerJob \?\? job/)
  assert.match(source, /const schedulerJobId = target\?\.schedulerJobId \?\? job\.id/)
  assert.match(source, /value=\{countDrafts\[schedulerJobId\] \?\? info\.requested_outputs\}/)
  assert.match(source, /min=\{Math\.max\(1, info\.produced_outputs\)\}/)
  assert.match(source, /max=\{25\}/)
  assert.match(source, /setQueueOutputCount\(schedulerJobId, countDrafts\[schedulerJobId\] \?\? info\.requested_outputs\)/)
  assert.match(source, /startQueueJobNext\(schedulerJobId\)/)
  assert.match(source, /setQueuePriority\(schedulerJobId, info\.priority [+-] 1\)/)
  assert.match(source, /info\.held \? api\.resumeQueueJob\(schedulerJobId\) : api\.holdQueueJob\(schedulerJobId\)/)
  assert.match(source, /onStop=\{\(\) => onStop\(job\.id\)\}/)
  assert.match(source, /onToggleLog=\{\(\) => void toggleLog\(effectiveJob\)\}/)
  assert.match(source, /onReviewPlan=\{\(\) => void openH3PlanReview\(job\.id\)\}/)
  assert.match(source, /\{machineControls && queue && <div/)
  assert.match(source, /\{machineControls && <>/)
  assert.match(source, /return snapshot\.error \? snapshot\.jobs : liveJobs/)
})
