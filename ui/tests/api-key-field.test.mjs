import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const filename = fileURLToPath(new URL('../src/components/shared/ApiKeyField.tsx', import.meta.url))

test('failed API key save retains the entered value for a successful retry', async () => {
  const states = []
  let cursor = 0
  const react = {
    useId: () => 'key-field',
    useState(initial) {
      const index = cursor++
      if (!(index in states)) states[index] = initial
      return [states[index], value => { states[index] = value }]
    },
  }
  const jsx = (type, props) => ({ type, props })
  const module = { exports: {} }
  vm.runInNewContext(ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, {
    module, exports: module.exports, Error,
    require: name => name === 'react' ? react : { jsx, jsxs: jsx },
  }, { filename })

  const props = { label: 'CivitAI API Key', maskedValue: '', isSet: false }
  let tree
  const render = () => { cursor = 0; tree = module.exports.ApiKeyField(props) }
  const nodes = (node = tree) => {
    if (!node || typeof node !== 'object') return []
    return [node, ...[].concat(node.props?.children || []).flatMap(child => nodes(child))]
  }
  const find = (type, label) => nodes().find(node =>
    node.type === type && (!label || node.props.children === label))
  const flush = () => new Promise(resolve => setImmediate(resolve))

  const attempts = []
  let rejectSave
  props.onSave = value => {
    attempts.push(value)
    return new Promise((_, reject) => { rejectSave = reject })
  }
  render()
  find('button', 'Set').props.onClick()
  render()
  find('input').props.onChange({ target: { value: 'entered-key-with-ellipsis...' } })
  render()
  find('button', 'Save').props.onClick()
  render()
  assert.equal(find('input').props.value, 'entered-key-with-ellipsis...')
  assert.equal(find('button', 'Saving…').props.disabled, true)
  assert.equal(find('button', 'Cancel').props.disabled, true)

  rejectSave(new Error('Server unavailable'))
  await flush()
  render()
  assert.equal(find('input').props.value, 'entered-key-with-ellipsis...')
  assert.equal(nodes().find(node => node.props?.role === 'alert').props.children, 'Server unavailable')
  assert.equal(find('button', 'Save').props.disabled, false)

  props.onSave = async value => {
    attempts.push(value)
    props.isSet = true
    props.maskedValue = 'entered...'
  }
  render()
  find('button', 'Save').props.onClick()
  await flush()
  render()
  assert.equal(find('input'), undefined)
  assert.ok(find('button', 'Change'))
  assert.ok(nodes().some(node => node.props?.children === 'entered...'))
  assert.deepEqual(attempts, ['entered-key-with-ellipsis...', 'entered-key-with-ellipsis...'])
})
