import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/lib/clipboard.ts', import.meta.url), 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText
const clipboardModule = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`)
const { copyTextToClipboard, isAssistantCopyScopeCurrent } = clipboardModule

class FakeHTMLElement {
  focusCount = 0
  focus() { this.focusCount += 1 }
}

globalThis.HTMLElement = FakeHTMLElement

function fakeDocument(execResult = true) {
  const previousFocus = new FakeHTMLElement()
  const events = []
  let area
  const document = {
    activeElement: previousFocus,
    body: {
      appendChild(node) {
        events.push(['append', node.value])
      },
    },
    createElement(name) {
      assert.equal(name, 'textarea')
      area = {
        value: '',
        readOnly: false,
        tabIndex: 0,
        style: {},
        setAttribute(name, value) { events.push(['attribute', name, value]) },
        focus() { events.push(['focus']) },
        select() { events.push(['select', this.value]) },
        setSelectionRange(start, end) { events.push(['range', start, end]) },
        remove() { events.push(['remove']) },
      }
      return area
    },
    execCommand(command) {
      events.push(['command', command, area.value])
      return execResult
    },
  }
  return { document, events, previousFocus, getArea: () => area }
}

test('Clipboard API receives the exact assistant content without fallback', async () => {
  const content = '  exact text\nwith whitespace  '
  const writes = []
  const fallback = fakeDocument()
  const copied = await copyTextToClipboard(content, {
    clipboard: { async writeText(value) { writes.push(value) } },
    document: fallback.document,
  })
  assert.equal(copied, true)
  assert.deepEqual(writes, [content])
  assert.equal(fallback.getArea(), undefined)
})

test('local HTTP fallback preserves content, removes its textarea, and restores focus', async () => {
  const content = 'response only\nno filename or metrics'
  const fallback = fakeDocument(true)
  const copied = await copyTextToClipboard(content, {
    clipboard: { async writeText() { throw new Error('insecure context') } },
    document: fallback.document,
  })
  assert.equal(copied, true)
  assert.equal(fallback.getArea().value, content)
  assert.ok(fallback.events.some(event => event[0] === 'command' && event[1] === 'copy' && event[2] === content))
  assert.ok(fallback.events.some(event => event[0] === 'remove'))
  assert.equal(fallback.previousFocus.focusCount, 1)
})

test('failed fallback is reported and awaited copy scope rejects every stale dimension', async () => {
  const fallback = fakeDocument(false)
  assert.equal(await copyTextToClipboard('unchanged', {
    document: fallback.document,
  }), false)

  const request = { token: 4, workspace: 'project-a', projectInstance: 'instance-a' }
  assert.equal(isAssistantCopyScopeCurrent(request, { ...request }), true)
  assert.equal(isAssistantCopyScopeCurrent(request, { ...request, token: 5 }), false)
  assert.equal(isAssistantCopyScopeCurrent(request, { ...request, workspace: 'project-b' }), false)
  assert.equal(isAssistantCopyScopeCurrent(request, { ...request, projectInstance: 'instance-b' }), false)
})
