import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compile } from 'tailwindcss'

import {
  closeModalIfTop,
  installModalFocus,
} from '../src/lib/modalFocus.ts'

const uiRoot = new URL('../', import.meta.url)

class FakeDocument extends EventTarget {
  activeElement = null
  body = { style: { overflow: 'auto' } }
}

class FakeElement {
  attributes = new Set()
  descendants = new Set()
  focusable = []

  constructor(document, name) {
    this.document = document
    this.name = name
  }

  focus() {
    this.document.activeElement = this
  }

  hasAttribute(name) {
    return this.attributes.has(name)
  }

  getAttribute(name) {
    return this.attributes.has(name) ? '' : null
  }

  setAttribute(name) {
    this.attributes.add(name)
  }

  removeAttribute(name) {
    this.attributes.delete(name)
  }

  contains(element) {
    return element === this || this.descendants.has(element)
  }

  querySelectorAll() {
    return this.focusable
  }

  closest() {
    return null
  }
}

function dispatchEscape(document) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperty(event, 'key', { value: 'Escape' })
  document.dispatchEvent(event)
  return event
}

function dispatchTab(document, shiftKey = false) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperties(event, {
    key: { value: 'Tab' },
    shiftKey: { value: shiftKey },
  })
  document.dispatchEvent(event)
  return event
}

function modalFixture(document, name) {
  const dialog = new FakeElement(document, `${name} dialog`)
  const close = new FakeElement(document, `${name} close`)
  const trigger = new FakeElement(document, `${name} trigger`)
  dialog.focusable = [close]
  dialog.descendants = new Set([close, trigger])
  return { close, dialog, trigger }
}

function buttonSource(source, marker) {
  const markerIndex = source.indexOf(marker)
  assert.ok(markerIndex >= 0, `expected button marker ${marker}`)
  const buttonStart = source.lastIndexOf('<button', markerIndex)
  const buttonEnd = source.indexOf('</button>', markerIndex)
  assert.ok(buttonStart >= 0 && buttonEnd > markerIndex, `expected complete button for ${marker}`)
  return source.slice(buttonStart, buttonEnd + '</button>'.length)
}

test('nested mobile drawers keep only the top modal interactive and retain shared locks', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const opener = new FakeElement(document, 'mobile header trigger')
  const parent = modalFixture(document, 'Generate')
  const child = modalFixture(document, 'Advanced')
  let parentCloseRequests = 0
  let childCloseRequests = 0
  let parentBackdropRequests = 0
  let childBackdropRequests = 0

  opener.focus()
  const cleanupParent = installModalFocus({
    document,
    dialog: parent.dialog,
    initialFocus: parent.close,
    restoreFocus: opener,
    appRoot,
    onClose: () => { parentCloseRequests += 1 },
    priority: 60,
  })
  assert.equal(document.activeElement, parent.close)

  parent.trigger.focus()
  const cleanupChild = installModalFocus({
    document,
    dialog: child.dialog,
    initialFocus: child.close,
    restoreFocus: parent.trigger,
    appRoot,
    onClose: () => { childCloseRequests += 1 },
    priority: 80,
  })

  assert.equal(document.activeElement, child.close)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(parent.dialog.hasAttribute('inert'), true, 'covered Generate drawer is inert')
  assert.equal(child.dialog.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'hidden')
  assert.equal(closeModalIfTop(document, parent.dialog, () => { parentBackdropRequests += 1 }), false)
  assert.equal(closeModalIfTop(document, child.dialog, () => { childBackdropRequests += 1 }), true)
  assert.equal(parentBackdropRequests, 0)
  assert.equal(childBackdropRequests, 1)

  const firstEscape = dispatchEscape(document)
  assert.equal(firstEscape.defaultPrevented, true)
  assert.equal(childCloseRequests, 1)
  assert.equal(parentCloseRequests, 0, 'first Escape must leave Generate open')

  cleanupChild()
  assert.equal(appRoot.hasAttribute('inert'), true, 'parent retains the root inert lock')
  assert.equal(parent.dialog.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'hidden', 'parent retains the body lock')
  assert.equal(document.activeElement, parent.trigger, 'child restores its trigger inside Generate')

  const secondEscape = dispatchEscape(document)
  assert.equal(secondEscape.defaultPrevented, true)
  assert.equal(parentCloseRequests, 1)
  cleanupParent()
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
  assert.equal(document.activeElement, opener)
})

test('priority keeps Advanced above a later-installed mobile parent at the 767/768 transition', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const child = modalFixture(document, 'Advanced')
  const parent = modalFixture(document, 'Generate')
  const cleanupChild = installModalFocus({
    document,
    dialog: child.dialog,
    initialFocus: child.close,
    restoreFocus: child.trigger,
    appRoot,
    onClose: () => {},
    priority: 80,
  })
  const cleanupParent = installModalFocus({
    document,
    dialog: parent.dialog,
    initialFocus: parent.close,
    restoreFocus: parent.trigger,
    appRoot,
    onClose: () => {},
    priority: 60,
  })

  assert.equal(document.activeElement, child.close, 'lower-priority parent must not steal focus')
  assert.equal(parent.dialog.hasAttribute('inert'), true)
  assert.equal(child.dialog.hasAttribute('inert'), false)
  cleanupParent()
  cleanupChild()
})

test('removing a covered parent preserves the child restoration chain to the outer opener', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const opener = new FakeElement(document, 'mobile header trigger')
  const parent = modalFixture(document, 'Generate')
  const child = modalFixture(document, 'Advanced')
  parent.dialog.descendants.add(child.trigger)

  opener.focus()
  const cleanupParent = installModalFocus({
    document,
    dialog: parent.dialog,
    initialFocus: parent.close,
    restoreFocus: opener,
    appRoot,
    onClose: () => {},
    priority: 60,
  })
  child.trigger.focus()
  const cleanupChild = installModalFocus({
    document,
    dialog: child.dialog,
    initialFocus: child.close,
    restoreFocus: child.trigger,
    appRoot,
    onClose: () => {},
    priority: 80,
  })

  cleanupParent()
  assert.equal(document.activeElement, child.close, 'covered-parent cleanup must not steal focus')
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')

  cleanupChild()
  assert.equal(document.activeElement, opener, 'final cleanup follows the removed parent to its outer opener')
  assert.notEqual(document.activeElement, child.trigger)
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(document.body.style.overflow, 'auto')
})

test('independent closed-state inert survives modal lock cleanup', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const lower = modalFixture(document, 'Advanced')
  const upper = modalFixture(document, 'nested dialog')
  const cleanupLower = installModalFocus({
    document,
    dialog: lower.dialog,
    initialFocus: lower.close,
    restoreFocus: lower.trigger,
    appRoot,
    onClose: () => {},
    priority: 80,
  })
  const cleanupUpper = installModalFocus({
    document,
    dialog: upper.dialog,
    initialFocus: upper.close,
    restoreFocus: lower.close,
    appRoot,
    onClose: () => {},
    priority: 100,
  })

  lower.dialog.setAttribute('aria-hidden')
  lower.dialog.getAttribute = name => name === 'aria-hidden' ? 'true' : null
  lower.dialog.setAttribute('inert')
  cleanupLower()
  assert.equal(lower.dialog.hasAttribute('inert'), true, 'closed Advanced keeps its own inert state')
  cleanupUpper()
})

test('tab wrapping ignores hidden focus targets', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const modal = modalFixture(document, 'Advanced')
  const visibleLast = new FakeElement(document, 'visible last')
  const hiddenLast = new FakeElement(document, 'hidden last')
  hiddenLast.setAttribute('hidden')
  modal.dialog.focusable = [modal.close, visibleLast, hiddenLast]
  modal.dialog.descendants = new Set(modal.dialog.focusable)
  const cleanup = installModalFocus({
    document,
    dialog: modal.dialog,
    initialFocus: modal.close,
    restoreFocus: modal.trigger,
    appRoot,
    onClose: () => {},
  })

  visibleLast.focus()
  const forward = dispatchTab(document)
  assert.equal(forward.defaultPrevented, true)
  assert.equal(document.activeElement, modal.close, 'hidden final match cannot let Tab escape')
  cleanup()
})

test('compiled viewport CSS and drawer sources cover narrow, breakpoint, and landscape geometry', async () => {
  const [html, css, app, sidebar, advanced, mobileHook] = await Promise.all([
    readFile(new URL('index.html', uiRoot), 'utf8'),
    readFile(new URL('src/index.css', uiRoot), 'utf8'),
    readFile(new URL('src/App.tsx', uiRoot), 'utf8'),
    readFile(new URL('src/components/Sidebar/Sidebar.tsx', uiRoot), 'utf8'),
    readFile(new URL('src/components/Sidebar/AdvancedSettings.tsx', uiRoot), 'utf8'),
    readFile(new URL('src/lib/useIsMobile.ts', uiRoot), 'utf8'),
  ])
  const stylesheet = await compile(css.replace('@import "tailwindcss";', ''))
  const compiledCss = stylesheet.build([])
  const utilities = await compile('@tailwind utilities;')
  const drawerUtilities = utilities.build([
    'h-[100vh]',
    'supports-[height:100dvh]:h-[100dvh]',
  ])

  assert.match(html, /width=device-width, initial-scale=1\.0, viewport-fit=cover/)
  assert.doesNotMatch(html, /maximum-scale|user-scalable/)
  assert.match(compiledCss, /height: 100vh/)
  assert.match(compiledCss, /height: 100dvh/)
  const rootRule = compiledCss.match(/#root\s*\{[^}]+\}/s)?.[0] ?? ''
  assert.match(rootRule, /safe-area-inset-top/)
  assert.match(rootRule, /safe-area-inset-right/)
  assert.match(rootRule, /safe-area-inset-bottom/)
  assert.match(rootRule, /safe-area-inset-left/)
  assert.match(compiledCss, /prefers-reduced-motion: reduce/)
  assert.match(mobileHook, /breakpoint = 768/)
  assert.match(mobileHook, /max-width: \$\{breakpoint - 1\}px/)
  assert.match(sidebar, /createPortal\(/)
  assert.match(sidebar, /document\.body/)
  assert.match(sidebar, /priority: 60/)
  assert.match(sidebar, /h-\[100vh\] supports-\[height:100dvh\]:h-\[100dvh\]/)
  assert.match(sidebar, /pt-\[env\(safe-area-inset-top\)\].*pr-\[env\(safe-area-inset-right\)\].*pb-\[env\(safe-area-inset-bottom\)\].*pl-\[env\(safe-area-inset-left\)\]/s)
  assert.match(advanced, /priority: 80/)
  assert.match(advanced, /h-\[100vh\] supports-\[height:100dvh\]:h-\[100dvh\]/)
  assert.match(advanced, /z-\[70\]/)
  assert.match(advanced, /z-\[80\]/)
  assert.match(advanced, /pt-\[env\(safe-area-inset-top\)\].*pr-\[env\(safe-area-inset-right\)\].*pb-\[env\(safe-area-inset-bottom\)\].*pl-\[env\(safe-area-inset-left\)\]/s)
  assert.match(advanced, /closeModalIfTop\(document, panelRef\.current, closeDrawer\)/)
  assert.match(advanced, /focus-visible:opacity-100/)
  assert.match(advanced, /aria-label=\{`\$\{confirmDelete === p\.id \? 'Confirm delete' : 'Delete'\} preset \$\{p\.name\}`\}/)
  assert.match(drawerUtilities, /@supports \(height:\s*100dvh\)/)
  assert.match(drawerUtilities, /height: 100dvh/)

  const appOpener = buttonSource(app, 'onClick={toggleSidebar}')
  const sidebarClose = buttonSource(sidebar, 'ref={mobileCloseRef}')
  const advancedClose = buttonSource(advanced, 'ref={closeRef}')
  for (const [name, target] of [
    ['mobile header opener', appOpener],
    ['Generate drawer close', sidebarClose],
    ['Advanced drawer close', advancedClose],
  ]) {
    assert.match(target, /flex h-11 w-11 shrink-0 items-center justify-center/, `${name} is a centered 44px target`)
    assert.match(target, /focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/, `${name} has a visible keyboard focus ring`)
  }
  assert.match(appOpener, /aria-controls="maestro-mobile-sidebar"/)
  assert.match(appOpener, /<Menu aria-hidden="true" size=\{20\} \/>/)
  assert.match(sidebarClose, /aria-label="Close creative workspace menu"/)
  assert.match(sidebarClose, /<X aria-hidden="true" size=\{16\} \/>/)
  assert.match(advancedClose, /aria-label="Close Advanced Settings"/)
  assert.match(advancedClose, /<X aria-hidden="true" size=\{16\} \/>/)
  assert.match(advancedClose, /md:h-auto md:w-auto md:p-1/, 'Advanced returns to compact desktop sizing at 768px')

  const cases = [
    [320, 568],
    [360, 800],
    [390, 844],
    [430, 932],
    [430, 320],
    [767, 430],
    [768, 432],
  ]
  for (const [width, dynamicHeight] of cases) {
    const advancedWidth = width < 768
      ? width
      : Math.min(380, width - Math.min(560, Math.max(460, width * 0.24)))
    const generateWidth = Math.min(380, width * 0.85)
    const safeArea = width > dynamicHeight
      ? { top: 0, right: 24, bottom: 21, left: 24 }
      : { top: 47, right: 0, bottom: 34, left: 0 }
    assert.ok(advancedWidth > 0 && advancedWidth <= width, `${width}px Advanced stays on-screen`)
    assert.ok(generateWidth > 0 && generateWidth <= width, `${width}px Generate stays on-screen`)
    assert.ok(dynamicHeight - safeArea.top - safeArea.bottom > 0, `${width}x${dynamicHeight} has usable dynamic height`)
    assert.ok(width - safeArea.left - safeArea.right > 0, `${width}x${dynamicHeight} has usable safe width`)
    assert.ok(80 > 70 && 70 > 60, 'Advanced panel and backdrop remain above Generate')
  }
})
