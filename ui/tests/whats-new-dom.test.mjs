import assert from 'node:assert/strict'
import test from 'node:test'

import { installModalFocus, MODAL_FOCUSABLE_SELECTOR } from '../src/lib/modalFocus.ts'

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

  setAttribute(name) {
    this.attributes.add(name)
  }

  removeAttribute(name) {
    this.attributes.delete(name)
  }

  contains(element) {
    return element === this || this.descendants.has(element)
  }

  querySelectorAll(selector) {
    assert.equal(selector, MODAL_FOCUSABLE_SELECTOR)
    return this.focusable
  }
}

function dispatchKey(document, key, shiftKey = false) {
  const event = new Event('keydown', { cancelable: true })
  Object.defineProperties(event, {
    key: { value: key },
    shiftKey: { value: shiftKey },
  })
  document.dispatchEvent(event)
  return event
}

test('dialog DOM controller traps focus, handles Escape, restores state, and leaves the closed mobile drawer inert', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'app root')
  const mobileDrawer = new FakeElement(document, 'closed mobile drawer')
  const mobileHeaderTrigger = new FakeElement(document, 'visible mobile header trigger')
  const dialog = new FakeElement(document, 'dialog')
  const close = new FakeElement(document, 'close')
  const archive = new FakeElement(document, 'archive summary')
  const done = new FakeElement(document, 'done')
  const outside = new FakeElement(document, 'outside')
  mobileDrawer.setAttribute('inert', '')
  dialog.focusable = [close, archive, done]
  dialog.descendants = new Set(dialog.focusable)
  let closeRequests = 0

  assert.equal(mobileDrawer.contains(mobileHeaderTrigger), false)
  mobileHeaderTrigger.focus()
  const cleanup = installModalFocus({
    document,
    dialog,
    initialFocus: close,
    restoreFocus: mobileHeaderTrigger,
    appRoot,
    onClose: () => { closeRequests += 1 },
  })

  assert.equal(document.activeElement, close)
  assert.equal(appRoot.hasAttribute('inert'), true)
  assert.equal(mobileDrawer.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'hidden')

  done.focus()
  assert.equal(dispatchKey(document, 'Tab').defaultPrevented, true)
  assert.equal(document.activeElement, close)

  close.focus()
  assert.equal(dispatchKey(document, 'Tab', true).defaultPrevented, true)
  assert.equal(document.activeElement, done)

  outside.focus()
  assert.equal(dispatchKey(document, 'Tab').defaultPrevented, true)
  assert.equal(document.activeElement, close)

  const escape = dispatchKey(document, 'Escape')
  assert.equal(escape.defaultPrevented, true)
  assert.equal(closeRequests, 1)

  cleanup()
  assert.equal(appRoot.hasAttribute('inert'), false)
  assert.equal(mobileDrawer.hasAttribute('inert'), true)
  assert.equal(document.body.style.overflow, 'auto')
  assert.equal(document.activeElement, mobileHeaderTrigger)

  dispatchKey(document, 'Escape')
  assert.equal(closeRequests, 1, 'cleanup must remove the key listener')
})

test('dialog cleanup preserves a pre-existing inert application root', () => {
  const document = new FakeDocument()
  const appRoot = new FakeElement(document, 'already inert app root')
  const dialog = new FakeElement(document, 'dialog')
  const close = new FakeElement(document, 'close')
  appRoot.setAttribute('inert', '')
  dialog.focusable = [close]
  dialog.descendants = new Set([close])

  const cleanup = installModalFocus({
    document,
    dialog,
    initialFocus: close,
    restoreFocus: null,
    appRoot,
    onClose: () => {},
  })
  cleanup()

  assert.equal(appRoot.hasAttribute('inert'), true)
})
