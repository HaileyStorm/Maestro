import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'
import {
  addProjectMember,
  AccountApiError,
  decodeProjectAccessProjection,
  fetchProjectMembers,
  removeProjectMember,
  setProjectMember,
} from '../src/api/client.ts'

const componentUrl = new URL('../src/components/MainContent/ProjectAccessPanel.tsx', import.meta.url)
const mainContentUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function withFetchMock(handler, action) {
  const previous = globalThis.fetch
  globalThis.fetch = handler
  try {
    return await action()
  } finally {
    globalThis.fetch = previous
  }
}

const ownerId = 'a'.repeat(32)
const editorId = 'b'.repeat(32)

function projection(revision = 7) {
  return {
    workspace: 'Film Cut',
    revision,
    members: [
      { account_id: ownerId, username: 'Owner', role: 'owner' },
      { account_id: editorId, username: 'Editor', role: 'editor' },
    ],
  }
}

test('project membership client uses exact encoded routes, revisions, and closed response decoders', async () => {
  const calls = []
  const responses = [
    projection(7),
    { ...projection(8), members: [
      ...projection(8).members,
      { account_id: 'c'.repeat(32), username: 'NewMember', role: 'viewer' },
    ] },
    { ...projection(9), members: projection(9).members.map(member => (
      member.account_id === editorId ? { ...member, role: 'viewer' } : member
    )) },
    { ...projection(10), members: projection(10).members.slice(0, 1) },
  ]
  await withFetchMock(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return jsonResponse(responses.shift())
  }, async () => {
    assert.equal((await fetchProjectMembers('Film Cut')).revision, 7)
    assert.equal((await addProjectMember('Film Cut', 'NewMember', 'viewer', 7)).revision, 8)
    assert.equal((await setProjectMember('Film Cut', editorId, 'viewer', 8)).revision, 9)
    assert.equal((await removeProjectMember('Film Cut', editorId, 9)).revision, 10)
  })

  assert.deepEqual(calls.map(call => [call.init.method || 'GET', call.url]), [
    ['GET', '/api/v1/workspaces/Film%20Cut/members'],
    ['POST', '/api/v1/workspaces/Film%20Cut/members'],
    ['PUT', `/api/v1/workspaces/Film%20Cut/members/${editorId}`],
    ['DELETE', `/api/v1/workspaces/Film%20Cut/members/${editorId}`],
  ])
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    username: 'NewMember', role: 'viewer', expected_revision: 7,
  })
  assert.deepEqual(JSON.parse(calls[2].init.body), { role: 'viewer', expected_revision: 8 })
  assert.deepEqual(JSON.parse(calls[3].init.body), { expected_revision: 9 })
  for (const call of calls) {
    assert.equal(call.init.credentials, 'same-origin')
    assert.equal(call.init.cache, 'no-store')
  }

  for (const invalid of [
    { ...projection(), extra: true },
    { ...projection(), workspace: 'Other project' },
    { ...projection(), revision: true },
    { ...projection(), members: [{ ...projection().members[0], role: 'admin' }] },
    { ...projection(), members: [{ ...projection().members[0], private_field: 'nope' }] },
  ]) {
    assert.throws(
      () => decodeProjectAccessProjection(invalid, 'Film Cut'),
      error => error instanceof AccountApiError
        && error.code === 'project_access_invalid_response'
        && error.status === 502,
    )
  }
})

function createHooks(stateSeeds = {}) {
  const states = []
  const initialized = new Set()
  let cursor = 0
  let id = 0
  return {
    begin() { cursor = 0 },
    useId() { id += 1; return `project-access-${id}` },
    useState(initial) {
      const index = cursor++
      if (!initialized.has(index)) {
        states[index] = Object.hasOwn(stateSeeds, index)
          ? stateSeeds[index]
          : typeof initial === 'function' ? initial() : initial
        initialized.add(index)
      }
      return [states[index], value => {
        states[index] = typeof value === 'function' ? value(states[index]) : value
      }]
    },
  }
}

async function loadPanelHarness() {
  const icons = ['Check', 'Copy', 'Loader2', 'RefreshCw', 'Share2', 'Trash2', 'UserPlus', 'Users', 'X']
  const modules = new Map([
    ['react', `
      export function useState(initial) { return globalThis.__projectAccessHooks.useState(initial) }
      export function useEffect() {}
      export function useRef(initial) { return { current: initial } }
      export function useCallback(callback) { return callback }
      export function useId() { return globalThis.__projectAccessHooks.useId() }
    `],
    ['react/jsx-runtime', `
      export const Fragment = Symbol('Fragment')
      export function jsx(type, props, key) { return { type, props: props || {}, key } }
      export const jsxs = jsx
    `],
    ['react-dom', 'export function createPortal(element) { return element }'],
    ['lucide-react', `
      const icon = props => ({ type: 'svg', props: props || {} })
      ${icons.map(name => `export const ${name} = icon`).join('\n')}
    `],
    ['../../api/client', `
      export class AccountApiError extends Error {
        constructor(message, options = {}) { super(message); this.status = options.status || 0; this.code = options.code || 'account_request_failed'; this.retryAfter = 0 }
      }
      globalThis.__ProjectAccessAccountApiError = AccountApiError
      export const addProjectMember = (...args) => globalThis.__addProjectMember(...args)
      export const fetchProjectMembers = (...args) => globalThis.__fetchProjectMembers(...args)
      export const isDirectLoopbackHostname = hostname => {
        const value = hostname.toLowerCase().replace(/^\\[|\\]$/g, '')
        return value === 'localhost' || value === '::1' || /^127(?:\\.\\d{1,3}){3}$/.test(value)
      }
      export const removeProjectMember = (...args) => globalThis.__removeProjectMember(...args)
      export const setProjectMember = (...args) => globalThis.__setProjectMember(...args)
    `],
    ['../../lib/clipboard', 'export const copyTextToClipboard = value => globalThis.__copyStudioLink(value)'],
    ['../../lib/modalFocus', `
      export function closeModalIfTop(_document, _dialog, close) { close(); return true }
      export function installModalFocus() { return () => {} }
    `],
  ])
  const result = await build({
    entryPoints: [componentUrl.pathname],
    bundle: true,
    format: 'cjs',
    jsx: 'automatic',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'project-sharing-ui-mocks',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => (
          modules.has(args.path) ? { path: args.path, namespace: 'project-sharing-test' } : undefined
        ))
        builder.onLoad({ filter: /.*/, namespace: 'project-sharing-test' }, args => ({
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
  return compiledModule.exports
}

function materialize(element) {
  if (Array.isArray(element)) return element.map(materialize)
  if (element === null || element === undefined || typeof element !== 'object') return element
  if (typeof element.type === 'function') return materialize(element.type(element.props))
  return {
    ...element,
    props: { ...element.props, children: materialize(element.props?.children) },
  }
}

function findElements(element, predicate, matches = []) {
  if (Array.isArray(element)) {
    for (const child of element) findElements(child, predicate, matches)
    return matches
  }
  if (element === null || element === undefined || typeof element !== 'object') return matches
  if (predicate(element)) matches.push(element)
  findElements(element.props?.children, predicate, matches)
  return matches
}

function nodeText(element) {
  if (Array.isArray(element)) return element.map(nodeText).join('')
  if (element === null || element === undefined || typeof element === 'boolean') return ''
  if (typeof element !== 'object') return String(element)
  return nodeText(element.props?.children)
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  assert.fail('Condition did not become true')
}

function runtime(harness, {
  recent = true,
  username = 'Editor',
  role = 'viewer',
  configuredStudioUrl = 'https://studio.example.test/',
  browserStudioUrl = 'http://127.0.0.1:8188/',
} = {}) {
  const hooks = createHooks({
    0: projection(),
    1: false,
    5: username,
    6: role,
  })
  globalThis.__projectAccessHooks = hooks
  const props = {
    open: true,
    workspace: 'Film Cut',
    recentlyReauthenticated: recent,
    configuredStudioUrl,
    browserStudioUrl,
    restoreFocus: null,
    onClose() {},
  }
  const render = () => {
    hooks.begin()
    return materialize(harness.ProjectAccessPanel(props))
  }
  return { render }
}

test('project access panel renders private account sharing, role clarity, mobile targets, and exact-username mutations', async t => {
  const harness = await loadPanelHarness()
  const calls = []
  const copied = []
  globalThis.__addProjectMember = async (...args) => {
    calls.push(args)
    return projection(8)
  }
  globalThis.__setProjectMember = async () => projection(8)
  globalThis.__removeProjectMember = async () => projection(8)
  globalThis.__fetchProjectMembers = async () => projection()
  globalThis.__copyStudioLink = async value => { copied.push(value); return true }
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  globalThis.window = { setTimeout() {}, location: { origin: 'https://studio.example.test', pathname: '/' } }
  globalThis.document = { body: {}, getElementById() { return null } }
  t.after(() => {
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    delete globalThis.__projectAccessHooks
    delete globalThis.__addProjectMember
    delete globalThis.__setProjectMember
    delete globalThis.__removeProjectMember
    delete globalThis.__fetchProjectMembers
    delete globalThis.__copyStudioLink
    delete globalThis.__ProjectAccessAccountApiError
  })

  const session = runtime(harness)
  let tree = session.render()
  const text = nodeText(tree)
  assert.match(text, /not a public project bearer link/)
  assert.match(text, /does not show suggestions or expose an account directory/)
  assert.match(text, /Can open and view the project and its outputs/)
  assert.match(text, /Can view, edit, and generate/)
  assert.match(text, /Full project control, including members and deletion/)

  const usernameInput = findElements(tree, node => node.props?.['data-project-member-username'] !== undefined)[0]
  const roleSelect = findElements(tree, node => node.props?.['data-project-member-role'] !== undefined)[0]
  const memberForm = findElements(tree, node => node.props?.['data-project-member-form'] !== undefined)[0]
  const copyButton = findElements(tree, node => node.props?.['data-copy-studio-link'] !== undefined)[0]
  assert.equal(usernameInput.props.autoComplete, 'off')
  assert.equal(usernameInput.props.disabled, false)
  assert.equal(roleSelect.props.value, 'viewer')
  assert.match(usernameInput.props.className, /h-11/)
  assert.match(copyButton.props.className, /h-11/)

  memberForm.props.onSubmit({ preventDefault() {} })
  await waitFor(() => calls.length === 1)
  assert.deepEqual(calls[0], ['Film Cut', 'Editor', 'viewer', 7])
  await copyButton.props.onClick()
  assert.deepEqual(copied, ['https://studio.example.test/'])

  tree = runtime(harness, { recent: false }).render()
  assert.match(nodeText(tree), /Confirm your password in Account before adding, changing, or removing project members/)
  assert.equal(
    findElements(tree, node => node.props?.['data-project-member-username'] !== undefined)[0].props.disabled,
    true,
  )

  assert.equal(harness.exactProjectUsername('Editor'), 'Editor')
  assert.equal(harness.exactProjectUsername(' editor '), null)
  assert.equal(harness.exactProjectUsername('ab'), null)

  const publicLink = harness.projectStudioLink(
    'https://share.example.test/',
    'http://127.0.0.1:8188/',
  )
  assert.deepEqual(
    { scope: publicLink.scope, source: publicLink.source, enabled: publicLink.copyEnabled, url: publicLink.url },
    { scope: 'public', source: 'configured', enabled: true, url: 'https://share.example.test/' },
  )

  const loopbackTree = runtime(harness, {
    configuredStudioUrl: '',
    browserStudioUrl: 'http://127.0.0.1:8188/',
  }).render()
  const loopbackScope = findElements(loopbackTree, node => node.props?.['data-studio-link-scope'] === 'loopback')[0]
  const loopbackCopy = findElements(loopbackTree, node => node.props?.['data-copy-studio-link'] !== undefined)[0]
  assert.match(nodeText(loopbackScope), /works only on this computer/)
  assert.equal(loopbackCopy.props.disabled, true)
  assert.match(nodeText(loopbackCopy), /Not shareable/)

  const lanTree = runtime(harness, {
    configuredStudioUrl: '',
    browserStudioUrl: 'http://192.168.1.24:8188/',
  }).render()
  const lanScope = findElements(lanTree, node => node.props?.['data-studio-link-scope'] === 'lan')[0]
  const lanCopy = findElements(lanTree, node => node.props?.['data-copy-studio-link'] !== undefined)[0]
  assert.match(nodeText(lanScope), /same local network/)
  assert.equal(lanCopy.props.disabled, false)
  assert.equal(
    harness.projectAccessErrorMessage(new globalThis.__ProjectAccessAccountApiError(
      'Project members changed or the last owner would be removed',
      { status: 409 },
    )),
    'Project members changed or the last owner would be removed',
  )
})

test('MainContent exposes Share project only from the exact active project permission', async () => {
  const source = await readFile(mainContentUrl, 'utf8')
  assert.match(source, /canManageActiveProjectMembers = activeProject\?\.project_permissions\?\.includes\('project\.membership\.manage'\) === true/)
  assert.match(source, /activeWorkspace && canManageActiveProjectMembers && \([\s\S]*data-project-share-trigger[\s\S]*Share project/)
  assert.doesNotMatch(source, /canManageActiveProjectMembers[\s\S]{0,120}project_permissions === undefined/)
  assert.match(source, /open=\{projectAccessOpen && activeWorkspace !== '' && canManageActiveProjectMembers\}/)
})
