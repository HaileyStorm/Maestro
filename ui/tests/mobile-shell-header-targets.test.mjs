import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compile } from 'tailwindcss'
import { build } from 'esbuild'

const uiRoot = new URL('../', import.meta.url)
const [appSource, whatsNewSource, accountSupportSource] = await Promise.all([
  readFile(new URL('src/App.tsx', uiRoot), 'utf8'),
  readFile(new URL('src/components/WhatsNewDialog.tsx', uiRoot), 'utf8'),
  readFile(new URL('src/components/AccountSupport/AccountSupportDrawer.tsx', uiRoot), 'utf8'),
])

function asDataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadWhatsNewRuntime() {
  const result = await build({
    entryPoints: [new URL('src/components/WhatsNewDialog.tsx', uiRoot).pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'mobile-header-whats-new-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /lib\/branding$/ }, () => ({ path: 'branding', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /lib\/changelog$/ }, () => ({ path: 'changelog', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /lib\/modalFocus$/ }, () => ({ path: 'focus', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /PerformanceHistoryChart$/ }, () => ({ path: 'performance', namespace: 'header-targets' }))
        bundle.onLoad({ filter: /.*/, namespace: 'header-targets' }, args => {
          if (args.path === 'react') return { contents: `
            export const useCallback = value => value
            export const useEffect = () => {}
            export const useId = () => 'whats-new-title'
            export const useRef = value => ({ current: value })
            export const useSyncExternalStore = (_subscribe, getSnapshot) => getSnapshot()
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'react-dom') return { contents: 'export const createPortal = value => value' }
          if (args.path === 'lucide') return { contents: "export const Check='Check', ChevronRight='ChevronRight', History='History', Megaphone='Megaphone', X='X'" }
          if (args.path === 'branding') return { contents: "export const PRODUCT_NAME='Maestro'" }
          if (args.path === 'changelog') return { contents: `
            export const CHANGELOG_MANIFEST = { currentVersion: 'test', releases: [], whyContinuum: [], lineageNote: '' }
            export const CURRENT_RELEASE = { version: 'test', highlights: [], summary: '' }
          ` }
          if (args.path === 'focus') return { contents: 'export const closeModalIfTop = () => true; export const installModalFocus = () => () => {}' }
          if (args.path === 'performance') return { contents: 'export const PerformanceHistoryChart = () => null' }
          return null
        })
      },
    }],
  })
  return import(`${asDataModule(result.outputFiles[0].text)}#mobile-header-${Date.now()}`)
}

async function loadAccountSupportRuntime() {
  const result = await build({
    entryPoints: [new URL('src/components/AccountSupport/AccountSupportDrawer.tsx', uiRoot).pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'mobile-header-account-support-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^react-dom$/ }, () => ({ path: 'react-dom', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'client', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /lib\/modalFocus$/ }, () => ({ path: 'focus', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /accountDrawerLifecycle$/ }, () => ({ path: 'lifecycle', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /SupportPanel$/ }, () => ({ path: 'support', namespace: 'header-targets' }))
        bundle.onResolve({ filter: /supportPresentation$/ }, () => ({ path: 'presentation', namespace: 'header-targets' }))
        bundle.onLoad({ filter: /.*/, namespace: 'header-targets' }, args => {
          if (args.path === 'react') return { contents: `
            export const useCallback = value => value
            export const useEffect = () => {}
            export const useId = () => 'account-support-id'
            export const useRef = value => ({ current: value })
            export const useState = value => [value, () => {}]
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'react-dom') return { contents: 'export const createPortal = value => value' }
          if (args.path === 'lucide') return { contents: `
            export const Check='Check', HeartHandshake='HeartHandshake', KeyRound='KeyRound', Loader2='Loader2',
              LogIn='LogIn', LogOut='LogOut', RefreshCw='RefreshCw', ShieldCheck='ShieldCheck',
              UserCog='UserCog', UserPlus='UserPlus', UserRound='UserRound', X='X'
          ` }
          if (args.path === 'client') return { contents: 'export class AccountApiError extends Error { retryAfter = 0 }' }
          if (args.path === 'focus') return { contents: 'export const closeModalIfTop = () => true; export const installModalFocus = () => () => {}' }
          if (args.path === 'store') return { contents: 'export const useStore = selector => selector(globalThis.__mobileHeaderAccountStore)' }
          if (args.path === 'lifecycle') return { contents: `
            export const createAccountDrawerLifecycle = () => ({
              opened() {}, closed() {}, operationLease: () => () => true,
            })
          ` }
          if (args.path === 'support') return { contents: 'export const SupportPanel = () => null' }
          if (args.path === 'presentation') return { contents: 'export const nextAccountSupportTab = () => null' }
          return null
        })
      },
    }],
  })
  return import(`${asDataModule(result.outputFiles[0].text)}#mobile-account-header-${Date.now()}`)
}

function buttonSource(source, marker) {
  const markerIndex = source.indexOf(marker)
  assert.ok(markerIndex >= 0, `expected button marker ${marker}`)
  const start = source.lastIndexOf('<button', markerIndex)
  const end = source.indexOf('</button>', markerIndex)
  assert.ok(start >= 0 && end > markerIndex, `expected complete button for ${marker}`)
  return source.slice(start, end + '</button>'.length)
}

test('mobile shell actions retain dialog semantics and expose 44px compact targets', () => {
  const menu = buttonSource(appSource, 'onClick={toggleSidebar}')
  const settings = buttonSource(appSource, 'aria-label="Open machine settings"')

  for (const [name, source] of [
    ['workspace menu', menu],
    ['machine settings', settings],
  ]) {
    assert.match(source, /flex h-11 w-11 shrink-0 items-center justify-center/, `${name} remains a centered 44px action`)
    assert.match(source, /focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/, `${name} has a visible focus indicator`)
  }
  assert.match(menu, /aria-expanded=\{sidebarOpen\}/)
  assert.match(menu, /aria-controls="maestro-mobile-sidebar"/)
  assert.match(settings, /<Settings aria-hidden="true" size=\{20\} \/>/)

  for (const [name, source] of [
    ["What's New", whatsNewSource],
    ['Support', accountSupportSource],
  ]) {
    assert.match(source, /compact \? 'h-11 w-11 p-0'/, `${name} compact trigger is exactly 44px`)
    assert.match(source, /aria-haspopup="dialog"/)
    assert.match(source, /aria-expanded=\{open\}/)
  }
  assert.match(whatsNewSource, /ref=\{triggerRef\}/)
  assert.match(whatsNewSource, /data-responsive-dialog-trigger=\{`whats-new:/)
  assert.match(whatsNewSource, /data-responsive-dialog-focus-return="whats-new"/)
  assert.match(whatsNewSource, /closeModalIfTop\(document, dialogRef\.current, onClose\)/)
  assert.match(accountSupportSource, /aria-controls="account-support-drawer"/)
  assert.match(accountSupportSource, /'Open Support'/)
  assert.match(accountSupportSource, /restoreFocusRef\.current = document\.activeElement instanceof HTMLElement/)
  assert.match(accountSupportSource, /data-responsive-dialog-trigger=\{`account-support:/)
  assert.match(accountSupportSource, /data-responsive-dialog-focus-return="account-support"/)
  assert.match(accountSupportSource, /restoreFocus: focusReturnRef\.current/)
  assert.match(accountSupportSource, /closeModalIfTop\(document, dialogRef\.current, closeDrawer\)/)
  assert.equal(accountSupportSource.match(/onClick=\{requestCloseDrawer\}/g)?.length, 2)
})

test('mobile header fixed geometry fits the 320px and 200-percent-zoom contract', async () => {
  const mobileHeaderStart = appSource.indexOf('{isMobile && (')
  const sidebarMount = appSource.indexOf('<Sidebar />', mobileHeaderStart)
  assert.ok(mobileHeaderStart >= 0 && sidebarMount > mobileHeaderStart)
  const mobileHeader = appSource.slice(mobileHeaderStart, sidebarMount)

  assert.match(mobileHeader, /className="h-12 shrink-0 border-b border-border bg-bg-secondary px-4 flex items-center justify-between"/)
  assert.match(mobileHeader, /className="mx-1 flex min-w-0 items-center gap-2"/)
  assert.match(mobileHeader, /className="min-w-0"/)
  assert.match(mobileHeader, /className="block truncate/)
  assert.equal(mobileHeader.match(/<WhatsNewButton compact \/>/g)?.length, 1)
  assert.equal(mobileHeader.match(/<AccountSupportButton compact \/>/g)?.length, 1)
  assert.equal(mobileHeader.match(/<span className="h-11 w-11 shrink-0" aria-hidden="true" \/>/g)?.length, 2)
  assert.match(appSource, /<WhatsNewDialogHost \/>/)

  const viewport = 320
  const horizontalPadding = 32
  const menuWidth = 44
  const centerMargins = 8
  const centerFixedWidth = 28 + 44 + 16
  const rightActionsWidth = 44 + 4 + 44
  const brandWidth = viewport - horizontalPadding - menuWidth - centerMargins - centerFixedWidth - rightActionsWidth
  assert.ok(brandWidth >= 0, 'fixed 44px actions leave a non-negative truncation budget at 320 CSS pixels')
  assert.equal(viewport, 640 / 2, '320 CSS pixels represents a 640px viewport at 200% zoom')

  const compiler = await compile('@theme { --spacing: 0.25rem; } @tailwind utilities;')
  const css = compiler.build(['h-11', 'w-11', 'shrink-0', 'min-w-0', 'truncate'])
  assert.match(css, /height:\s*calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /width:\s*calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /flex-shrink:\s*0/)
  assert.match(css, /min-width:\s*calc\(var\(--spacing\) \* 0\)/)
  assert.match(css, /text-overflow:\s*ellipsis/)
})

test('open dialog state and responsive focus handoff survive 767-to-768 replacement', async () => {
  const [runtime, accountRuntime] = await Promise.all([
    loadWhatsNewRuntime(),
    loadAccountSupportRuntime(),
  ])
  let width = 767
  const focused = []
  const triggers = {
    'whats-new:mobile': { isConnected: true, focus: () => focused.push('whats-new:mobile') },
    'whats-new:desktop': { isConnected: true, focus: () => focused.push('whats-new:desktop') },
    'account-support:mobile': { isConnected: true, focus: () => focused.push('account-support:mobile') },
    'account-support:desktop': { isConnected: true, focus: () => focused.push('account-support:desktop') },
  }
  const fakeDocument = {
    defaultView: { matchMedia: () => ({ matches: width <= 767 }) },
    querySelector(selector) {
      const exact = selector.match(/="([^"]+)"/u)?.[1]
      if (exact) return triggers[exact] || null
      const prefix = selector.match(/\^="([^"]+)"/u)?.[1]
      return Object.entries(triggers).find(([key]) => key.startsWith(prefix || ''))?.[1] || null
    },
  }
  const previousDocument = globalThis.document
  const previousAccountStore = globalThis.__mobileHeaderAccountStore
  globalThis.document = fakeDocument
  globalThis.__mobileHeaderAccountStore = new Proxy({
    accountDrawerOpen: false,
    accountContext: { enabled: false },
    setAccountDrawerOpen() {},
  }, {
    get(target, property) {
      return property in target ? target[property] : () => Promise.resolve(null)
    },
  })
  try {
    const mobileButton = runtime.WhatsNewButton({ compact: true })
    mobileButton.props.ref.current = triggers['whats-new:mobile']
    mobileButton.props.onClick()
    assert.equal(runtime.WhatsNewButton({ compact: true }).props['aria-expanded'], true)

    const mobileHost = runtime.WhatsNewDialogHost()
    const [focusReturn, openDialog] = mobileHost.props.children
    assert.ok(openDialog, 'the persistent host renders the open dialog at 767px')
    focusReturn.props.onFocus()
    assert.equal(focused.at(-1), 'whats-new:mobile')

    width = 768
    triggers['whats-new:mobile'].isConnected = false
    const desktopButton = runtime.WhatsNewButton({ compact: false })
    assert.equal(desktopButton.props['aria-expanded'], true, 'replacement trigger observes the shared open state')
    const desktopHost = runtime.WhatsNewDialogHost()
    assert.ok(desktopHost.props.children[1], 'dialog remains mounted through the breakpoint transition')
    desktopHost.props.children[0].props.onFocus()
    assert.equal(focused.at(-1), 'whats-new:desktop')

    desktopHost.props.children[1].props.onClose()
    assert.equal(runtime.WhatsNewButton({ compact: false }).props['aria-expanded'], false)

    width = 320
    triggers['whats-new:mobile'].isConnected = true
    triggers['whats-new:desktop'].isConnected = false
    assert.equal(width, 640 / 2, 'focus routing uses the mobile trigger at a 200-percent-zoom CSS width')
    runtime.WhatsNewDialogHost().props.children[0].props.onFocus()
    assert.equal(focused.at(-1), 'whats-new:mobile')

    const mobileSupportReturn = accountRuntime.AccountSupportDrawer()
    mobileSupportReturn.props.onFocus()
    assert.equal(focused.at(-1), 'account-support:mobile')

    width = 768
    triggers['account-support:mobile'].isConnected = false
    triggers['account-support:desktop'].isConnected = true
    accountRuntime.AccountSupportDrawer().props.onFocus()
    assert.equal(focused.at(-1), 'account-support:desktop')

    width = 320
    triggers['account-support:mobile'].isConnected = true
    triggers['account-support:desktop'].isConnected = false
    accountRuntime.AccountSupportDrawer().props.onFocus()
    assert.equal(focused.at(-1), 'account-support:mobile')
  } finally {
    globalThis.document = previousDocument
    globalThis.__mobileHeaderAccountStore = previousAccountStore
  }
})
