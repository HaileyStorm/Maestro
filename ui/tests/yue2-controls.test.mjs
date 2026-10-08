import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

// Poll scheduling is inert; deferred API reads are controlled explicitly below.
globalThis.window = { setInterval: () => 0, clearInterval() {} }

const componentBundle = await build({
  stdin: {
    contents: `export { Yue2Controls } from './src/components/Sidebar/Yue2Controls';
      export { createHarness } from 'react'; export { control } from './src/api/client';
      export { account } from './src/stores/useStore';
      export { Yue2Training } from './src/components/Sidebar/Yue2Training';`,
    resolveDir: new URL('..', import.meta.url).pathname,
  },
  bundle: true, format: 'esm', platform: 'node', jsx: 'automatic', write: false,
  plugins: [{
    name: 'yue2-component-harness',
    setup(bundle) {
      const modules = {
        react: `
          let active;
          const equal = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
          export function createHarness(Component, props) {
            const h = { slots: [], index: 0, dirty: false, layout: [], passive: [], mutations: [], props };
            h.render = (next = h.props) => {
              h.props = next; h.index = 0; h.dirty = false; active = h;
              h.tree = Component(next); active = null;
              h.flushLayout(); return h.tree;
            };
            const flush = queue => {
              for (const { slot, effect } of queue.splice(0)) {
                slot.cleanup?.(); slot.cleanup = effect();
              }
            };
            h.flushLayout = () => flush(h.layout);
            h.flush = () => {
              for (let i = 0; i < 20; i++) {
                flush(h.passive);
                if (!h.dirty) return;
                h.render();
              }
              throw new Error('YuE2 hook harness did not settle');
            };
            h.unmount = () => {
              for (const slot of h.slots) slot?.cleanup?.();
              h.layout = []; h.passive = [];
            };
            return h;
          }
          export function useState(initial) {
            const h = active, index = h.index++;
            const slot = h.slots[index] ||= { value: typeof initial === 'function' ? initial() : initial };
            return [slot.value, value => {
              const next = typeof value === 'function' ? value(slot.value) : value;
              h.mutations.push({ index, value: next });
              if (!Object.is(next, slot.value)) { slot.value = next; h.dirty = true; }
            }];
          }
          export function useRef(initial) {
            return (active.slots[active.index++] ||= { value: { current: initial } }).value;
          }
          export function useMemo(factory, deps) {
            const h = active, index = h.index++, previous = h.slots[index];
            if (!previous || !equal(previous.deps, deps)) h.slots[index] = { value: factory(), deps };
            return h.slots[index].value;
          }
          export const useCallback = (callback, deps) => useMemo(() => callback, deps);
          const effect = (callback, deps, queue) => {
            const h = active, slot = h.slots[h.index++] ||= {};
            if (!equal(slot.deps, deps)) {
              slot.deps = deps; h[queue].push({ slot, effect: callback });
            }
          };
          export const useEffect = (callback, deps) => effect(callback, deps, 'passive');
          export const useLayoutEffect = (callback, deps) => effect(callback, deps, 'layout');
        `,
        'react/jsx-runtime': `
          export const Fragment = Symbol('Fragment');
          export const jsx = (type, props) => ({ type, props }); export const jsxs = jsx;
        `,
        'lucide-react': `export const Check = 'Check', Loader2 = 'Loader2', Music2 = 'Music2',
          RefreshCw = 'RefreshCw', Sparkles = 'Sparkles', Square = 'Square';`,
        store: `
          import { useState, useLayoutEffect } from 'react';
          const listeners = new Set();
          const subscribe = listener => { listeners.add(listener); return () => listeners.delete(listener); };
          export const account = { epoch: 0, context: undefined, advance() { this.epoch++; for (const listener of listeners) listener(); }, setContext(context) { this.context = context; for (const listener of listeners) listener(); } };
          export const currentAccountIdentityEpoch = () => account.epoch;
          export const useStore = selector => {
            const [value, setValue] = useState(() => selector(useStore.getState()));
            useLayoutEffect(() => subscribe(() => setValue(selector(useStore.getState()))), [selector]);
            return value;
          };
          useStore.subscribe = subscribe;
          useStore.getState = () => ({ accountContext: account.context === undefined ? { enabled: true, authenticated: true, account: { id: 'owner-' + account.epoch } } : account.context });
        `,
        api: `
          import { account } from '../../stores/useStore';
          export class Yue2RequestError extends Error {
            constructor(status, message) { super(message); this.status = status; }
          }
          export const control = { reads: [], submits: [], continues: [], libraries: {}, nextLibrary: null, trainingReads: [], trainingSubmits: [], trainingCancels: [], trainingJobs: {}, nextTraining: null, requestError: (status, message) => new Yue2RequestError(status, message) };
          const deferred = () => {
            let resolve, reject;
            const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
            return { promise, resolve, reject };
          };
          export async function fetchYue2Status(workspace) {
            control.reads.push(workspace);
            return { available: true, model: 'YuE2', sampleRate: 44100, queue: 'local', loras: [] };
          }
          export async function fetchYue2Library(workspace) {
            if (control.nextLibrary) {
              const pending = control.nextLibrary; control.nextLibrary = null;
              return pending.promise;
            }
            return { tracks: control.libraries[workspace] || [] };
          }
          export async function fetchYue2Training(workspace) {
            control.trainingReads.push({ workspace, epoch: account.epoch });
            if (control.nextTraining) { const pending = control.nextTraining; control.nextTraining = null; return pending.promise; }
            return { jobs: control.trainingJobs[workspace] || [] };
          }
          export function submitYue2Training(input) {
            const pending = { ...deferred(), input }; control.trainingSubmits.push(pending); return pending.promise;
          }
          export function cancelYue2Training(id, workspace) {
            const pending = { ...deferred(), id, workspace }; control.trainingCancels.push(pending); return pending.promise;
          }
          export async function fetchYue2Plan(id) { if (control.nextPlan) { const p = control.nextPlan; control.nextPlan = null; return p.promise; } return { reviewable: true, abc: 'X:1\\n% ' + id + '\\nK:C\\nC4|' }; }
          export function submitYue2(input) {
            const pending = { ...deferred(), input }; control.submits.push(pending); return pending.promise;
          }
          export function continueYue2(id, workspace, abc) {
            const pending = { ...deferred(), id, workspace, abc }; control.continues.push(pending); return pending.promise;
          }
          export function composeYue2() { throw new Error('Unexpected compose request'); }
          export function cancelYue2() { throw new Error('Unexpected cancel request'); }
          export function yue2AudioUrl(id) { return '/audio/' + id; }
        `,
      }
      bundle.onResolve({ filter: /.*/ }, args => {
        const key = args.path.includes('stores/useStore') ? 'store' : args.path.includes('api/client') ? 'api' : args.path
        if (key in modules) return { path: key, namespace: 'yue2-test' }
      })
      bundle.onLoad({ filter: /.*/, namespace: 'yue2-test' }, args => ({ contents: modules[args.path], loader: 'js' }))
    },
  }],
})
let componentRealm = 0
const textContent = node => Array.isArray(node) ? node.map(textContent).join('')
  : node && typeof node === 'object' ? textContent(node.props?.children)
    : node == null || typeof node === 'boolean' ? '' : String(node)
function findNode(node, predicate) {
  if (Array.isArray(node)) return node.map(child => findNode(child, predicate)).find(Boolean)
  if (!node || typeof node !== 'object') return null
  return predicate(node) ? node : findNode(node.props?.children, predicate)
}
const button = (h, label) => {
  const node = findNode(h.tree, node => node.type === 'button' && textContent(node).includes(label))
  assert.ok(node, `Missing button: ${label}`)
  return node
}
async function settle(h) {
  for (let i = 0; i < 4; i++) {
    await new Promise(resolve => setImmediate(resolve))
    h.flush()
  }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
async function componentFixture({ review = false, reload = false, accountContext } = {}) {
  if (!reload) {
    const entries = new Map()
    globalThis.sessionStorage = { getItem: key => entries.get(key) ?? null,
      setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) }
  }
  const { Yue2Controls, createHarness, control, account } = await import(
    `data:text/javascript;base64,${Buffer.from(componentBundle.outputFiles[0].text).toString('base64')}#yue2-${++componentRealm}`,
  )
  if (accountContext !== undefined) account.setContext(accountContext)
  const props = { workspace: 'A', description: 'A local song', style: 'pop', lyrics: 'Rain falls',
    instrumental: false, onStyle() {}, onLyrics() {} }
  if (review) control.libraries.A = [{ id: 'old-take', project: 'A', title: 'Old take', status: 'needs-review' }]
  const h = createHarness(Yue2Controls, props)
  h.render(); h.flush(); await settle(h)
  const transition = async (workspace, passive = true) => {
    h.render({ ...h.props, workspace })
    if (passive) { h.flush(); await settle(h) }
  }
  const remount = async () => {
    h.unmount();
    const next = createHarness(Yue2Controls, h.props);
    next.render(); next.flush(); await settle(next);
    return next;
  };
  return { h, control, transition, account, remount }
}



const trainingJob = (request, fields = {}) => ({
  id: request.input.requestId, project: request.input.workspace, name: request.input.name,
  kind: request.input.kind, trigger: request.input.trigger, sourceTakeIds: request.input.tracks.map(track => track.takeId),
  state: 'queued', stage: 'queued', cancel_requested: 0, ...fields,
})
const trainingAck = (request, fields = {}) => ({ job: trainingJob(request), reused: false, ...fields })
const trainingSubmitButton = h => {
  const node = findNode(h.tree, node => node.type === 'button' && node.props.className?.includes('bg-cta'))
  assert.ok(node)
  return node
}
const trainingRefresh = h => {
  const node = findNode(h.tree, node => node.type === 'button' && node.props['aria-label'] === 'Refresh YuE2 training jobs')
  assert.ok(node)
  return node
}
const trainingField = (h, label, value) => {
  const node = findNode(h.tree, node => node.type === 'label' && textContent(node).startsWith(label))
  assert.ok(node, `Missing training field ${label}`)
  const input = findNode(node.props.children, node => ['input', 'select', 'textarea'].includes(node.type))
  input.props.onChange({ target: { value } }); h.flush()
}
const fillTraining = (h, name = 'Private artist', caption = 'Private style caption', lyrics = 'Private lyrics') => {
  const select = findNode(h.tree, node => node.type === 'input' && node.props.type === 'checkbox')
  assert.ok(select)
  select.props.onChange({ target: { checked: true } }); h.flush()
  trainingField(h, 'Name', name)
  trainingField(h, 'Trigger word', 'sv_private_artist')
  trainingField(h, 'Style caption', caption)
  trainingField(h, 'Lyrics, if present', lyrics)
}
async function trainingFixture() {
  const { Yue2Training, createHarness, control, account } = await import(
    `data:text/javascript;base64,${Buffer.from(componentBundle.outputFiles[0].text).toString('base64')}#yue2-training-${++componentRealm}`,
  )
  const reports = []
  const props = { workspace: 'A', gpuBlocked: false,
    tracks: [{ id: 'private-take', project: 'A', title: 'Private take', status: 'succeeded' }],
    onJobs(jobs) { reports.push(jobs) } }
  const h = createHarness(Yue2Training, props)
  h.render(); h.flush(); await settle(h)
  const remount = async (next = h.props) => {
    h.unmount()
    const mounted = createHarness(Yue2Training, next)
    mounted.render(); mounted.flush(); await settle(mounted)
    return mounted
  }
  const transition = async (workspace, passive = true) => {
    h.render({ ...h.props, workspace,
      tracks: [{ id: `${workspace}-take`, project: workspace, title: `${workspace} take`, status: 'succeeded' }] })
    if (passive) { h.flush(); await settle(h) }
  }
  return { h, control, account, reports, remount, transition }
}

test('YuE2 training account remount excludes private pending inputs and stale rejection starts no reconciliation', async () => {
  const { h, control, account, remount } = await trainingFixture()
  fillTraining(h)
  button(h, 'Queue training').props.onClick(); h.flush()
  const old = control.trainingSubmits[0]
  h.unmount(); account.advance()
  const current = await remount({ ...h.props, tracks: [{ id: 'new-owner-take', project: 'A', title: 'New owner take', status: 'succeeded' }] })
  assert.doesNotMatch(textContent(current.tree), /previous request is unconfirmed|Retry original training request/)
  fillTraining(current, 'New owner artist', 'New owner caption', 'New owner lyrics')
  button(current, 'Queue training').props.onClick(); current.flush()
  const next = control.trainingSubmits[1]
  assert.notEqual(next.input.requestId, old.input.requestId)
  assert.deepEqual(next.input.tracks, [{ takeId: 'new-owner-take', caption: 'New owner caption', lyrics: 'New owner lyrics' }])
  const reads = control.trainingReads.length
  h.mutations.length = 0; current.mutations.length = 0
  old.reject(control.requestError(503, 'Prior account response lost'))
  await settle(current)
  assert.equal(control.trainingReads.length, reads, 'old catch must not contact the new account project library')
  assert.deepEqual(h.mutations, [], 'unmounted component cannot change busy or messages')
  assert.deepEqual(current.mutations, [], 'old completion cannot clear the newer account intent')
  assert.equal(trainingSubmitButton(current).props.disabled, true)
  next.reject(control.requestError(503, 'New account response lost')); await settle(current)
  button(current, 'Retry original training request').props.onClick()
  assert.equal(control.trainingSubmits[2].input.requestId, next.input.requestId)
})

for (const outcome of ['success', 'failure']) {
  test(`YuE2 training ${outcome} is fenced through workspace ABA and cannot settle a newer retry`, async () => {
    const { h, control, transition } = await trainingFixture()
    fillTraining(h)
    button(h, 'Queue training').props.onClick(); h.flush()
    const old = control.trainingSubmits[0]
    await transition('B'); await transition('A')
    button(h, 'Retry original training request').props.onClick(); h.flush()
    const current = control.trainingSubmits[1]
    assert.deepEqual(current.input, old.input, 'same-account project return retries the original request')
    const reads = control.trainingReads.length
    h.mutations.length = 0
    if (outcome === 'success') old.resolve(trainingAck(old))
    else old.reject(control.requestError(503, 'Obsolete response lost'))
    await settle(h)
    assert.deepEqual(h.mutations, [])
    assert.equal(control.trainingReads.length, reads)
    assert.equal(trainingSubmitButton(h).props.disabled, true)
    current.reject(control.requestError(503, 'Current response lost')); await settle(h)
    assert.match(textContent(h.tree), /Could not confirm whether training was queued/)
    button(h, 'Retry original training request').props.onClick()
    assert.equal(control.trainingSubmits[2].input.requestId, old.input.requestId)
  })
}

test('YuE2 training captured handlers from a prior workspace visit cannot start requests after ABA', async () => {
  const { h, control, transition } = await trainingFixture()
  fillTraining(h)
  const oldSubmit = button(h, 'Queue training').props.onClick
  const oldRefresh = trainingRefresh(h).props.onClick
  await transition('B'); await transition('A')
  const reads = control.trainingReads.length
  oldRefresh(); oldSubmit()
  await settle(h)
  assert.equal(control.trainingReads.length, reads)
  assert.equal(control.trainingSubmits.length, 0)
})

test('YuE2 training prior ambiguity survives retry422 and edited fields with identical frozen inputs', async () => {
  const { h, control } = await trainingFixture()
  fillTraining(h)
  const click = button(h, 'Queue training').props.onClick
  click(); click(); h.flush()
  assert.equal(control.trainingSubmits.length, 1)
  const first = control.trainingSubmits[0]
  const original = JSON.stringify(first.input)
  first.reject(control.requestError(503, 'Acknowledgement lost')); await settle(h)
  assert.match(textContent(h.tree), /previous request is unconfirmed/)
  trainingField(h, 'Name', 'Edited artist')
  trainingField(h, 'Style caption', 'Edited caption')
  trainingField(h, 'Lyrics, if present', 'Edited lyrics')
  button(h, 'Retry original training request').props.onClick(); h.flush()
  const retry = control.trainingSubmits[1]
  assert.equal(JSON.stringify(retry.input), original)
  retry.reject(control.requestError(422, 'Retry validation rejected')); await settle(h)
  assert.match(textContent(h.tree), /previous request is unconfirmed/)
  button(h, 'Retry original training request').props.onClick()
  assert.equal(JSON.stringify(control.trainingSubmits[2].input), original)
})

for (const outcome of ['success', 'failure']) {
  test(`YuE2 training pending POST survives unmount, retry422 and late original ${outcome}`, async () => {
    const { h, control, remount } = await trainingFixture()
    fillTraining(h)
    button(h, 'Queue training').props.onClick(); h.flush()
    const original = control.trainingSubmits[0]
    const current = await remount()
    button(current, 'Retry original training request').props.onClick(); current.flush()
    const retry = control.trainingSubmits[1]
    assert.deepEqual(retry.input, original.input)
    retry.reject(control.requestError(422, 'Retry validation rejected')); await settle(current)
    assert.match(textContent(current.tree), /previous request is unconfirmed/)
    const reads = control.trainingReads.length
    h.mutations.length = 0; current.mutations.length = 0
    if (outcome === 'success') original.resolve(trainingAck(original))
    else original.reject(control.requestError(503, 'Original reply lost'))
    await settle(current)
    assert.deepEqual(h.mutations, [])
    assert.deepEqual(current.mutations, [])
    assert.equal(control.trainingReads.length, reads)
    button(current, 'Retry original training request').props.onClick()
    assert.deepEqual(control.trainingSubmits[2].input, original.input)
  })
}

test('YuE2 training canonical GET completes pending POST and fences its later rejection', async () => {
  const { h, control } = await trainingFixture()
  fillTraining(h)
  button(h, 'Queue training').props.onClick(); h.flush()
  const original = control.trainingSubmits[0]
  control.trainingJobs.A = [trainingJob(original)]
  trainingRefresh(h).props.onClick(); await settle(h)
  assert.doesNotMatch(textContent(h.tree), /previous request is unconfirmed/)
  assert.equal(button(h, 'Queue training').props.disabled, false)
  h.mutations.length = 0
  original.reject(control.requestError(422, 'Obsolete rejected inputs')); await settle(h)
  assert.deepEqual(h.mutations, [])
  assert.doesNotMatch(textContent(h.tree), /Obsolete rejected inputs/)
})

for (const status of [400, 422]) {
  test(`YuE2 training initial definite HTTP${status} releases its rejected attempt`, async () => {
    const { h, control } = await trainingFixture()
    fillTraining(h)
    button(h, 'Queue training').props.onClick(); h.flush()
    control.trainingSubmits[0].reject(control.requestError(status, 'Invalid training inputs')); await settle(h)
    assert.match(textContent(h.tree), /Invalid training inputs/)
    assert.doesNotMatch(textContent(h.tree), /previous request is unconfirmed/)
    assert.equal(button(h, 'Queue training').props.disabled, false)
  })
}

for (const response of ['wrong acknowledgement', 'wrong project library', 'empty library']) {
  test(`YuE2 training ${response} cannot confirm a pending request`, async () => {
    const { h, control } = await trainingFixture()
    fillTraining(h)
    button(h, 'Queue training').props.onClick(); h.flush()
    const request = control.trainingSubmits[0]
    if (response === 'wrong acknowledgement') request.resolve(trainingAck(request, { job: trainingJob(request, { project: 'B' }) }))
    else {
      if (response === 'wrong project library') control.trainingJobs.A = [trainingJob(request, { project: 'B' })]
      request.reject(control.requestError(503, 'Lost reply'))
    }
    await settle(h)
    assert.match(textContent(h.tree), /previous request is unconfirmed/)
    assert.equal(control.trainingSubmits.length, 1)
    control.trainingJobs.A = [trainingJob(request)]
    trainingRefresh(h).props.onClick(); await settle(h)
    assert.doesNotMatch(textContent(h.tree), /previous request is unconfirmed|Could not confirm whether training was queued/)
    assert.match(textContent(h.tree), /Private artist/)
    assert.equal(control.trainingSubmits.length, 1, 'canonical library adoption never resends training')
  })
}

for (const action of ['refresh', 'reconciliation', 'cancel']) {
  test(`YuE2 training late ${action} callbacks cannot mutate a new account or start more requests`, async () => {
    const { h, control, account, reports } = await trainingFixture()
    let pending
    if (action === 'cancel') {
      const fake = { input: { requestId: 'cancel-job', workspace: 'A', name: 'Old job', kind: 'artist', trigger: 'sv_old', tracks: [{ takeId: 'private-take' }] } }
      control.trainingJobs.A = [trainingJob(fake)]
      trainingRefresh(h).props.onClick(); await settle(h)
      button(h, 'Cancel').props.onClick(); pending = control.trainingCancels[0]
    } else {
      pending = deferred(); control.nextTraining = pending
      if (action === 'refresh') trainingRefresh(h).props.onClick()
      else {
        fillTraining(h); button(h, 'Queue training').props.onClick()
        control.trainingSubmits[0].reject(control.requestError(503, 'Lost reply'))
        await new Promise(resolve => setImmediate(resolve))
      }
    }
    account.advance(); h.flush(); await settle(h)
    const reads = control.trainingReads.length
    h.mutations.length = 0; reports.length = 0
    if (action === 'cancel') pending.reject(new Error('Old cancellation failed'))
    else pending.resolve({ jobs: [{ id: 'old', project: 'A', name: 'Old private job', state: 'failed', kind: 'artist', trigger: 'sv_old', sourceTakeIds: [], stage: 'failed', cancel_requested: 0 }] })
    await settle(h)
    assert.deepEqual(h.mutations, [])
    assert.deepEqual(reports, [])
    assert.equal(control.trainingReads.length, reads)
    assert.doesNotMatch(textContent(h.tree), /Old private job|Old cancellation failed/)
  })
}

const submittedTrack = (request, fields = {}) => ({
  id: 'accepted-take', project: request.input.workspace, requestId: request.input.requestId,
  title: 'Accepted take', status: 'succeeded', stage: 'done', duration: 1, elapsed: 1, ...fields,
})
const acknowledgement = (request, fields = {}) => ({
  requestId: request.input.requestId, tracks: [submittedTrack(request)], reused: false, ...fields,
})
const refreshButton = h => {
  const node = findNode(h.tree, node => node.type === 'button' && node.props['aria-label'] === 'Refresh YuE2 status')
  assert.ok(node)
  return node
}

test('YuE2 reload restores an uncertain identity and adopts only its exact canonical take without POST', async () => {
  const original = await componentFixture()
  button(original.h, 'Generate with YuE2').props.onClick(); original.h.flush()
  const request = original.control.submits[0]
  request.reject(new Error('Acknowledgement lost after acceptance')); await settle(original.h)
  original.h.unmount()
  const reloaded = await componentFixture({ reload: true })
  assert.equal(button(reloaded.h, 'Generate with YuE2').props.disabled, true)
  assert.match(textContent(reloaded.h.tree), /not yet confirmed/)
  assert.equal(reloaded.control.submits.length, 0)
  reloaded.control.libraries.A = [submittedTrack(request, { status: 'completed' })]
  refreshButton(reloaded.h).props.onClick(); await settle(reloaded.h)
  assert.doesNotMatch(textContent(reloaded.h.tree), /not yet confirmed/)
  assert.equal(reloaded.control.submits.length, 0)
  assert.match(textContent(reloaded.h.tree), /Accepted take/)
})

test('YuE2 reload before an acknowledgement allows only an explicit original-input retry', async () => {
  const original = await componentFixture()
  button(original.h, 'Generate with YuE2').props.onClick(); original.h.flush()
  const request = original.control.submits[0], frozen = JSON.stringify(request.input)
  original.h.unmount()
  const reloaded = await componentFixture({ reload: true })
  assert.equal(reloaded.control.submits.length, 0)
  reloaded.h.render({ ...reloaded.h.props, lyrics: 'New draft', style: 'rock' }); reloaded.h.flush()
  button(reloaded.h, 'Generate with YuE2').props.onClick()
  assert.equal(reloaded.control.submits.length, 0)
  button(reloaded.h, 'Retry this submission').props.onClick()
  assert.equal(JSON.stringify(reloaded.control.submits[0].input), frozen)
  reloaded.control.submits[0].reject(new Error('Retry also lost')); await settle(reloaded.h)
  assert.equal(button(reloaded.h, 'Generate with YuE2').props.disabled, true)
})

test('YuE2 scope changes clear an existing library and retire a delayed score review without a POST', async () => {
  const { h, control, account } = await componentFixture({ review: true })
  const plan = deferred(); control.nextPlan = plan
  control.libraries.A = [{ id: 'new-review', project: 'A', title: 'Private take', status: 'needs-review' }]
  refreshButton(h).props.onClick(); await settle(h)
  assert.match(textContent(h.tree), /Private take/)
  const nextLibrary = deferred(); control.nextLibrary = nextLibrary
  account.setContext(null); await settle(h)
  assert.doesNotMatch(textContent(h.tree), /Private take/)
  assert.equal(control.submits.length, 0)
  h.mutations.length = 0
  plan.resolve({ reviewable: true, abc: 'X:77\nK:C\nC4|' }); await settle(h)
  assert.deepEqual(h.mutations, [])
  assert.ok(!findNode(h.tree, node => node.type === 'textarea' && node.props.value === 'X:77\nK:C\nC4|'))
})

test('YuE2 persisted receipts use stable account identity across fresh page epochs', async () => {
  const original = await componentFixture()
  button(original.h, 'Generate with YuE2').props.onClick(); original.h.flush()
  original.control.submits[0].reject(new Error('Lost acknowledgement')); await settle(original.h)
  original.h.unmount()
  const other = await componentFixture({ reload: true, accountContext: { enabled: true, authenticated: true, account: { id: 'different-owner' } } })
  assert.equal(other.account.epoch, 0)
  assert.equal(button(other.h, 'Generate with YuE2').props.disabled, false)
  assert.doesNotMatch(textContent(other.h.tree), /not yet confirmed/)
  other.h.unmount()
  const returned = await componentFixture({ reload: true })
  assert.equal(returned.account.epoch, 0)
  assert.equal(button(returned.h, 'Generate with YuE2').props.disabled, true)
  assert.match(textContent(returned.h.tree), /not yet confirmed/)
  assert.equal(returned.control.submits.length, 0)
})

test('YuE2 unresolved account projection retires pending submit and library callbacks without discarding recovery', async () => {
  const { h, control, account } = await componentFixture()
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const pending = control.submits[0]
  const library = deferred(); control.nextLibrary = library
  refreshButton(h).props.onClick()
  account.setContext(null); await settle(h)
  assert.match(textContent(h.tree), /recovery could not be read/)
  h.mutations.length = 0
  pending.resolve(acknowledgement(pending)); library.resolve({ tracks: [submittedTrack(pending)] })
  await settle(h)
  assert.deepEqual(h.mutations, [])
  button(h, 'Generate with YuE2').props.onClick()
  assert.equal(control.submits.length, 1)
  account.setContext(undefined); await settle(h)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
  assert.match(textContent(h.tree), /not yet confirmed/)
})

test('YuE2 storage failures and malformed receipts refuse submission before POST', async () => {
  let nested = {}
  for (let i = 0; i < 30; i++) nested = { nested }
  for (const storage of [
    { getItem: () => null, setItem: () => { throw new Error('Quota') } },
    { getItem: () => '{bad', setItem() {} },
    { getItem: () => JSON.stringify({ A: { payload: { workspace: 'A', requestId: 'maestro-uncertain', nested } } }), setItem() {} },
    { getItem: () => { throw new Error('Denied') }, setItem() {} },
  ]) {
    const { h, control } = await componentFixture()
    globalThis.sessionStorage = storage
    button(h, 'Generate with YuE2').props.onClick(); await settle(h)
    assert.equal(control.submits.length, 0)
  }
})

test('YuE2 coalesces rapid clicks before render and freezes the entire retry payload', async () => {
  const { h, control } = await componentFixture()
  const click = button(h, 'Generate with YuE2').props.onClick
  click(); click()
  assert.equal(control.submits.length, 1)
  const original = control.submits[0]
  const frozen = JSON.stringify(original.input)
  assert.equal(Object.isFrozen(original.input.form.semantic), true)
  assert.equal(Object.isFrozen(original.input.form.loras), true)
  original.reject(new TypeError('Connection lost after acceptance'))
  await settle(h)
  assert.match(textContent(h.tree), /not yet confirmed/)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
  h.render({ ...h.props, description: 'Edited description', style: 'jazz', lyrics: 'New words' })
  h.flush()
  const title = findNode(h.tree, node => node.type === 'input' && node.props.value === 'Untitled YuE2 song')
  title.props.onChange({ target: { value: 'Edited title' } }); h.flush()
  const score = findNode(h.tree, node => node.type === 'textarea' && node.props.value === '')
  score.props.onChange({ target: { value: 'X:2\nK:G\nG4|' } }); h.flush()
  const decoder = findNode(h.tree, node => node.type === 'select' && node.props.value === 'stock')
  decoder.props.onChange({ target: { value: 'joint-v9' } }); h.flush()
  assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
  const retry = button(h, 'Retry this submission').props.onClick
  retry(); retry()
  assert.equal(control.submits.length, 2)
  assert.equal(JSON.stringify(control.submits[1].input), frozen)
  control.submits[1].resolve(acknowledgement(control.submits[1], { reused: true }))
  control.libraries.A = [submittedTrack(original)]
  await settle(h)
  assert.doesNotMatch(textContent(h.tree), /not yet confirmed/)
  assert.match(textContent(h.tree), /Accepted take/)
  assert.equal(control.submits.length, 2, 'library reconciliation never submits')
})

test('YuE2 lost acknowledgement reconciles only exact request and project and preserves score review', async () => {
  const { h, control } = await componentFixture()
  button(h, 'Generate with YuE2').props.onClick()
  const original = control.submits[0]
  control.libraries.A = [submittedTrack(original, { requestId: 'another-request' })]
  original.reject(new Error('Lost acknowledgement'))
  await settle(h)
  assert.match(textContent(h.tree), /not yet confirmed/)
  control.libraries.A = [submittedTrack(original, { project: 'B' })]
  refreshButton(h).props.onClick(); await settle(h)
  assert.match(textContent(h.tree), /not yet confirmed/)
  control.libraries.A = [submittedTrack(original, { status: 'needs-review' })]
  refreshButton(h).props.onClick(); await settle(h)
  assert.doesNotMatch(textContent(h.tree), /not yet confirmed/)
  assert.equal(button(h, 'Continue with reviewed score').props.disabled, false)
  assert.equal(control.submits.length, 1)
})

for (const status of ['needs-review', 'succeeded']) {
  for (const rejection of [400, 422]) {
    test(`YuE2 library acceptance of ${status} completes pending POST and fences later HTTP ${rejection}`, async () => {
      const { h, control } = await componentFixture()
      button(h, 'Generate with YuE2').props.onClick(); h.flush()
      const pending = control.submits[0]
      control.libraries.A = [submittedTrack(pending, { status })]
      refreshButton(h).props.onClick(); await settle(h)
      assert.doesNotMatch(textContent(h.tree), /Waiting for YuE2|not yet confirmed/)
      assert.equal(button(h, status === 'needs-review' ? 'Continue with reviewed score' : 'Generate with YuE2').props.disabled, false,
        'canonical GET acceptance completes busy submission even while POST remains pending')
      h.mutations.length = 0
      pending.reject(control.requestError(rejection, 'Obsolete validation rejection'))
      await settle(h)
      assert.deepEqual(h.mutations, [], 'accepted GET fences every old POST setter and finalizer')
      assert.doesNotMatch(textContent(h.tree), /Obsolete validation rejection/)
      assert.match(textContent(h.tree), /Accepted take/)
      assert.equal(control.submits.length, 1)
    })
  }
}

test('YuE2 library acceptance allows a newer submission before old POST acknowledgement and fences that acknowledgement', async () => {
  const { h, control } = await componentFixture()
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const old = control.submits[0]
  control.libraries.A = [submittedTrack(old)]
  refreshButton(h).props.onClick(); await settle(h)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, false)
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const current = control.submits[1]
  assert.ok(current)
  assert.notEqual(current.input.requestId, old.input.requestId)
  h.mutations.length = 0
  const reads = control.reads.length
  old.resolve(acknowledgement(old, { tracks: [submittedTrack(old, { title: 'Obsolete acknowledged take', status: 'needs-review' })] }))
  await settle(h)
  assert.deepEqual(h.mutations, [])
  assert.equal(control.reads.length, reads, 'old acknowledgement cannot refresh the newer operation')
  assert.doesNotMatch(textContent(h.tree), /Obsolete acknowledged take/)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
  control.libraries.A = [submittedTrack(current, { id: 'new-take', title: 'Newer take' }), submittedTrack(old)]
  current.resolve(acknowledgement(current, { tracks: [submittedTrack(current, { id: 'new-take', title: 'Newer take' })] }))
  await settle(h)
  assert.match(textContent(h.tree), /Newer take/)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, false)
})

for (const malformed of ['empty', 'wrong request', 'wrong project', 'invalid track']) {
  test(`YuE2 ${malformed} acknowledgement remains unresolved even after an empty library`, async () => {
    const { h, control } = await componentFixture()
    button(h, 'Generate with YuE2').props.onClick()
    const request = control.submits[0]
    const response = malformed === 'empty' ? {}
      : malformed === 'wrong request' ? acknowledgement(request, { requestId: 'wrong' })
        : acknowledgement(request, { tracks: [submittedTrack(request,
          malformed === 'wrong project' ? { project: 'B' } : { duration: 'invalid' })] })
    request.resolve(response)
    await settle(h)
    assert.match(textContent(h.tree), /not yet confirmed/)
    assert.ok(button(h, 'Retry this submission'))
    assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
    assert.equal(control.submits.length, 1)
  })
}

for (const status of [400, 422, 408, 409, 429, 500]) {
  test(`YuE2 initial HTTP ${status} ${[400, 422].includes(status) ? 'releases validation rejection' : 'retains uncertain submission'}`, async () => {
    const { h, control } = await componentFixture()
    button(h, 'Generate with YuE2').props.onClick()
    control.submits[0].reject(control.requestError(status, 'Request rejected'))
    await settle(h)
    if ([400, 422].includes(status)) {
      assert.match(textContent(h.tree), /Request rejected/)
      assert.equal(button(h, 'Generate with YuE2').props.disabled, false)
    } else {
      assert.match(textContent(h.tree), /not yet confirmed/)
      button(h, 'Retry this submission').props.onClick()
      control.submits[1].reject(control.requestError(422, 'Retry validation rejected'))
      await settle(h)
      assert.match(textContent(h.tree), /not yet confirmed/, 'a later rejection cannot disprove original acceptance')
      assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
      assert.equal(control.submits[1].input.requestId, control.submits[0].input.requestId)
    }
  })
}

for (const outcome of ['success', 'failure']) {
  test(`YuE2 submission survives A–B–A and unmount after late ${outcome} without resending`, async () => {
    const { h, control, transition, remount } = await componentFixture()
    button(h, 'Generate with YuE2').props.onClick(); h.flush()
    const request = control.submits[0]
    await transition('B'); await transition('A')
    assert.equal(button(h, 'Generate with YuE2').props.disabled, true)
    button(h, 'Generate with YuE2').props.onClick()
    assert.equal(control.submits.length, 1)
    const mounted = await remount()
    h.mutations.length = 0
    if (outcome === 'success') request.resolve(acknowledgement(request))
    else request.reject(new Error('Late lost acknowledgement'))
    await settle(mounted)
    assert.deepEqual(h.mutations, [], 'unmounted handler cannot mutate its old component')
    assert.match(textContent(mounted.tree), /not yet confirmed/)
    assert.equal(control.submits.length, 1)
    button(mounted, 'Retry this submission').props.onClick()
    assert.deepEqual(control.submits[1].input, request.input)
  })
}

test('YuE2 account changes exclude old intents and fence late submit and library responses', async () => {
  const { h, control, account } = await componentFixture()
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const old = control.submits[0]
  const library = deferred()
  control.nextLibrary = library
  refreshButton(h).props.onClick()
  account.advance()
  h.flush(); await settle(h)
  assert.equal(button(h, 'Generate with YuE2').props.disabled, false)
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const current = control.submits[1]
  h.mutations.length = 0
  old.resolve(acknowledgement(old))
  library.resolve({ tracks: [submittedTrack(old)] })
  await settle(h)
  assert.deepEqual(h.mutations, [])
  assert.notEqual(current.input.requestId, old.input.requestId)
  current.reject(new Error('Current lost acknowledgement')); await settle(h)
  assert.match(textContent(h.tree), /not yet confirmed/)
  button(h, 'Retry this submission').props.onClick()
  assert.equal(control.submits[2].input.requestId, current.input.requestId)
})

test('YuE2 account changes while unmounted exclude the old submission before remount', async () => {
  const { h, control, account, remount } = await componentFixture()
  button(h, 'Generate with YuE2').props.onClick(); h.flush()
  const old = control.submits[0]
  h.unmount()
  account.advance()
  old.reject(new Error('Old account lost acknowledgement'))
  await new Promise(resolve => setImmediate(resolve))
  const current = await remount()
  assert.equal(button(current, 'Generate with YuE2').props.disabled, false)
  assert.doesNotMatch(textContent(current.tree), /not yet confirmed/)
  button(current, 'Generate with YuE2').props.onClick()
  assert.notEqual(control.submits[1].input.requestId, old.input.requestId)
})

test('YuE2 bounded intent store fails new projects closed and never evicts unresolved submissions', async () => {
  const { h, control, transition } = await componentFixture()
  for (let i = 0; i < 16; i++) {
    await transition(`project-${i}`)
    button(h, 'Generate with YuE2').props.onClick()
    control.submits[i].reject(new Error('Unconfirmed'))
    await settle(h)
  }
  const reloaded = await componentFixture({ reload: true })
  await reloaded.transition('overflow')
  button(reloaded.h, 'Generate with YuE2').props.onClick(); await settle(reloaded.h)
  assert.equal(reloaded.control.submits.length, 0)
  assert.match(textContent(reloaded.h.tree), /Confirm an unresolved YuE2 submission/)
  await transition('overflow')
  button(h, 'Generate with YuE2').props.onClick(); await settle(h)
  assert.equal(control.submits.length, 16)
  assert.match(textContent(h.tree), /Confirm an unresolved YuE2 submission/)
  await transition('project-0')
  button(h, 'Retry this submission').props.onClick()
  assert.equal(control.submits[16].input.requestId, control.submits[0].input.requestId)
})

{
  const action = 'continue'
  const label = 'Continue with reviewed score'
  const requests = control => control.continues
  for (const outcome of ['success', 'failure']) {
    test(`late YuE2 ${action} ${outcome} cannot change a returned project or clear its new operation`, async () => {
      const { h, control, transition } = await componentFixture({ review: true })
      assert.equal(button(h, label).props.disabled, false)
      button(h, label).props.onClick(); h.flush()
      const old = requests(control)[0]
      assert.equal(old.abc, undefined, 'unchanged review resumes its saved plan')
      await transition('B')
      control.libraries.A = [{ id: 'new-take', project: 'A', title: 'New take', status: 'needs-review' }]
      await transition('A')
      assert.equal(button(h, label).props.disabled, false)
      button(h, label).props.onClick(); h.flush()
      const reads = control.reads.length
      h.mutations.length = 0
      if (outcome === 'failure') old.reject(new Error('Obsolete operation failed'))
      else old.resolve({})
      await settle(h)
      assert.deepEqual(h.mutations, [], 'late result must not run any state setters')
      assert.equal(control.reads.length, reads, 'late success must not start a refresh')
      assert.equal(button(h, label).props.disabled, true, 'new operation remains busy')
      assert.doesNotMatch(textContent(h.tree), /Obsolete operation failed/)
      assert.equal(requests(control).length, 2, 'project changes do not resend accepted requests')
      assert.equal(requests(control)[1].id, 'new-take')
      requests(control)[1].reject(new Error('Current operation failed'))
      await settle(h)
      assert.match(textContent(h.tree), /Current operation failed/)
      assert.equal(button(h, label).props.disabled, false, 'current operation can clear its own busy state')
    })
  }

  test(`YuE2 ${action} invalidates before passive project effects and on unmount`, async () => {
    for (const boundary of ['layout', 'unmount']) {
      const { h, control, transition } = await componentFixture({ review: true })
      button(h, label).props.onClick(); h.flush()
      if (boundary === 'layout') {
        await transition('B', false)
        await transition('A', false)
      } else h.unmount()
      const reads = control.reads.length
      h.mutations.length = 0
      requests(control)[0].resolve({})
      await new Promise(resolve => setImmediate(resolve))
      assert.deepEqual(h.mutations, [], `${boundary} boundary must fence completion before passive cleanup`)
      assert.equal(control.reads.length, reads)
    }
  })

  test(`current YuE2 ${action} success refreshes its project and clears its own busy state`, async () => {
    const { h, control } = await componentFixture({ review: true })
    button(h, label).props.onClick(); h.flush()
    const reads = control.reads.length
    control.libraries.A = [{ id: 'accepted-take', project: 'A', title: 'Accepted take', status: 'succeeded' }]
    requests(control)[0].resolve({})
    await settle(h)
    assert.equal(control.reads.length, reads + 1)
    assert.match(textContent(h.tree), /Accepted take/)
    assert.equal(button(h, 'Generate with YuE2').props.disabled, false)
    assert.equal(requests(control).length, 1, 'completion does not create another generation request')
  })

  for (const outcome of ['success', 'failure']) {
    test(`YuE2 ${action} refresh ${outcome} is fenced if the project changes while it is pending`, async () => {
      const { h, control, transition } = await componentFixture({ review: true })
      button(h, label).props.onClick(); h.flush()
      const refreshResponse = deferred()
      control.nextLibrary = refreshResponse
      const reads = control.reads.length
      requests(control)[0].resolve({})
      await new Promise(resolve => setImmediate(resolve))
      assert.equal(control.reads.length, reads + 1, 'accepted operation started its refresh')
      await transition('B', false)
      await transition('A', false)
      h.mutations.length = 0
      if (outcome === 'failure') refreshResponse.reject(new Error('Obsolete refresh failed'))
      else refreshResponse.resolve({ tracks: [{ id: 'obsolete', project: 'A', title: 'Obsolete take', status: 'succeeded' }] })
      await new Promise(resolve => setImmediate(resolve))
      assert.deepEqual(h.mutations, [], 'neither the refresh nor operation finalizer may mutate the new scope')
      assert.equal(requests(control).length, 1)
    })
  }
}

const result = await build({
  entryPoints: [new URL('../src/components/Sidebar/yue2GenerationSettings.ts', import.meta.url).pathname],
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
})
const module = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
const {
  preferredYue2Checkpoint,
  resolveYue2CheckpointSelection,
  resolveYue2GenerationSettings,
  reviewedAbcForContinuation,
  sameYue2ComposeDraft,
  unavailableYue2LoraSelections,
  yue2LyricDensityWarning,
} = module

const group = (id, generation = {}, checkpoints = []) => ({
  id, name: id, kind: 'style', generation, checkpoints,
})

test('YuE2 uses the workspace sampler defaults when no LoRA overrides them', () => {
  assert.deepEqual(resolveYue2GenerationSettings([]), {
    cot: 'full', cfgScale: 1, odeSteps: 100, maxTokens: 9000,
  })
})

test('compatible LoRA defaults override the workspace settings', () => {
  assert.deepEqual(resolveYue2GenerationSettings([
    group('dream', { cot: 'off', cfg_scale: 1, ode_steps: 32 }),
    group('hiphop', { cot: 'off', cfg_scale: 1, ode_steps: 32 }),
  ]), { cot: 'off', cfgScale: 1, odeSteps: 32, maxTokens: 9000 })
})

test('incompatible stacked LoRA defaults fail with an actionable error', () => {
  assert.throws(
    () => resolveYue2GenerationSettings([
      group('one', { cot: 'off' }), group('two', { cot: 'full' }),
    ]),
    /different score-planning modes.*compatible stack/,
  )
})

test('preferred checkpoint follows the catalog preferred step', () => {
  const checkpoints = [
    { id: 'early', sha256: 'a'.repeat(64), step: 500 },
    { id: 'preferred', sha256: 'b'.repeat(64), step: 1000 },
  ]
  assert.equal(preferredYue2Checkpoint({ ...group('dream', {}, checkpoints), preferredStep: 1000 }).id, 'preferred')
  assert.equal(preferredYue2Checkpoint(group('dream', {}, checkpoints)).id, 'preferred')
})

test('checkpoint choice resolves the exact ID and SHA-256 pair', () => {
  const checkpoints = [
    { id: 'same-id', filename: 'older.safetensors', sha256: 'a'.repeat(64), step: 500 },
    { id: 'same-id', filename: 'newer.safetensors', sha256: 'b'.repeat(64), step: 1000 },
  ]
  const selected = resolveYue2CheckpointSelection(
    { ...group('dream', {}, checkpoints), preferredStep: 1000 },
    { id: 'same-id', sha256: 'a'.repeat(64) },
  )
  assert.equal(selected.checkpoint.filename, 'older.safetensors')
  assert.equal(selected.checkpoint.sha256, 'a'.repeat(64))
  assert.equal(selected.error, null)
})

test('stale checkpoint IDs or hashes fail closed instead of falling back to preferred', () => {
  const checkpoints = [
    { id: 'current', filename: 'current.safetensors', sha256: 'c'.repeat(64), step: 1000 },
  ]
  const groupWithCheckpoint = { ...group('dream', {}, checkpoints), preferredStep: 1000 }
  const staleId = resolveYue2CheckpointSelection(groupWithCheckpoint, { id: 'removed', sha256: 'b'.repeat(64) })
  const staleHash = resolveYue2CheckpointSelection(groupWithCheckpoint, { id: 'current', sha256: 'b'.repeat(64) })
  const missingDefault = resolveYue2CheckpointSelection(group('empty'))
  const missingCatalog = resolveYue2CheckpointSelection({ ...group('missing'), checkpoints: undefined })
  const invalidHash = resolveYue2CheckpointSelection(group('invalid', {}, [
    { id: 'bad-checksum', sha256: 'not-a-sha256' },
  ]))
  assert.equal(staleId.checkpoint, null)
  assert.match(staleId.error, /no longer installed or its checksum changed/)
  assert.equal(staleHash.checkpoint, null)
  assert.match(staleHash.error, /no longer installed or its checksum changed/)
  assert.equal(missingDefault.checkpoint, null)
  assert.match(missingDefault.error, /No installed checkpoint is available/)
  assert.equal(missingCatalog.checkpoint, null)
  assert.match(missingCatalog.error, /No installed checkpoint is available/)
  assert.equal(invalidHash.checkpoint, null)
  assert.match(invalidHash.error, /missing a valid installed ID or SHA-256/)
})

test('a selected LoRA missing after a catalog refresh cannot silently fall out of a stack', () => {
  const selected = { dream: 1, artist: 0.7 }
  assert.deepEqual(unavailableYue2LoraSelections(selected, [group('dream')]), ['artist'])
  assert.deepEqual(unavailableYue2LoraSelections(selected, [group('dream'), group('artist')]), [])
})

test('an untouched reviewed score resumes the exact saved YuE2 plan', () => {
  const plan = 'X:1\nV: Vocal\nC4|\nV: Ins\nC,4|'
  assert.equal(reviewedAbcForContinuation(plan, plan), undefined)
  assert.equal(reviewedAbcForContinuation(plan, `${plan}\n% edited`), `${plan}\n% edited`)
})

test('YuE2 warns when even the English word count overwhelms Vocal note slots', () => {
  const abc = 'X:1\nV: Vocal clef=treble\n"C" C8 D8 E8 G8|\nV: Ins\nC8 E8 G8 E8|'
  assert.match(yue2LyricDensityWarning(
    '[Verse]\nRain on the roof while we repair the bicycle', abc, 'English', false,
  ), /9 lyric words for 4 Vocal notes/)
  assert.match(yue2LyricDensityWarning(
    '[Verse]\nRain on the roof while we repair the bicycle',
    'X:1\nV: Vocal\n"C" C8D8E8G8|\nV: Ins\nC8E8G8E8|', 'English', false,
  ), /9 lyric words for 4 Vocal notes/)
  assert.equal(yue2LyricDensityWarning('[Verse]\nRain falls', abc, 'English', false), null)
  assert.equal(yue2LyricDensityWarning('[Verse]\n雨が降る', abc, 'Japanese', false), null)
  assert.equal(yue2LyricDensityWarning('[Verse]\nToo many words for four notes', abc, 'English', true), null)
  assert.equal(yue2LyricDensityWarning('Many sung words', 'X:1\nK:C', 'English', false), null)
})

test('YuE2 compose results apply only to the unchanged draft they started from', () => {
  const draft = {
    workspace: 'alpha', description: 'bright synth pop', language: 'English',
    instrumental: false, style: 'synth pop', lyrics: 'one line', abc: 'X:1',
  }
  assert.equal(sameYue2ComposeDraft(draft, { ...draft }), true)
  for (const [key, value] of [
    ['workspace', 'beta'], ['description', 'dark synth pop'], ['language', 'Japanese'],
    ['instrumental', true], ['style', 'rock'], ['lyrics', 'new line'], ['abc', 'X:2'],
  ]) {
    assert.equal(sameYue2ComposeDraft(draft, { ...draft, [key]: value }), false, key)
  }
})

test('YuE2 async results are fenced to the captured workspace and review can be retried', async () => {
  const source = await import('node:fs/promises').then(fs => fs.readFile(
    new URL('../src/components/Sidebar/Yue2Controls.tsx', import.meta.url),
    'utf8',
  ))
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| refreshSequence\.current !== sequence/)
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| composeSequence\.current !== sequence/)
  assert.match(source, /workspaceRef\.current !== requestWorkspace \|\| planSequence\.current !== sequence/)
  assert.match(source, /!plan\.reviewable/)
  assert.match(source, /disabled=\{reviewLoading\}/)
  assert.match(source, /tracks\.filter\(track => track\.project === workspace\)/)
  assert.match(source, /Retry score review/)
})

test('YuE2 training keeps existing jobs visible but blocks new work after supervision loss', async () => {
  const fs = await import('node:fs/promises')
  const controls = await fs.readFile(new URL('../src/components/Sidebar/Yue2Controls.tsx', import.meta.url), 'utf8')
  const training = await fs.readFile(new URL('../src/components/Sidebar/Yue2Training.tsx', import.meta.url), 'utf8')
  assert.match(controls, /gpuBlocked=\{!!status\.gpuBlocked\}/)
  assert.match(training, /disabled=\{\(!valid && !pending\) \|\| busy \|\| gpuBlocked\}/)
  assert.match(training, /gpuBlocked && <p role="alert"/)
  assert.match(training, /jobs\.map\(job =>/)
})

test('YuE2 picker submits the selected exact checkpoint pair and blocks stale choices', async () => {
  const source = await import('node:fs/promises').then(fs => fs.readFile(
    new URL('../src/components/Sidebar/Yue2Controls.tsx', import.meta.url),
    'utf8',
  ))
  assert.match(source, /aria-label=\{`\$\{group\.name\} checkpoint`\}/)
  assert.match(source, /resolveYue2CheckpointSelection\(group, checkpointSelections\[group\.id\]\)/)
  assert.match(source, /id: checkpoint\.id, sha256: checkpoint\.sha256, strength/)
  assert.match(source, /!!checkpointError/)
  assert.match(source, /Unavailable · \{savedCheckpoint\.id\} · SHA-256 \{savedCheckpoint\.sha256\}/)
})
