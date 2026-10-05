import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const componentBundle = await build({
  stdin: {
    contents: `export { Yue2Controls } from './src/components/Sidebar/Yue2Controls';
      export { createHarness } from 'react'; export { control } from './src/api/client';`,
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
        api: `
          export const control = { reads: [], submits: [], continues: [], libraries: {}, nextLibrary: null };
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
          export async function fetchYue2Training() { return { jobs: [] }; }
          export async function fetchYue2Plan(id) { return { reviewable: true, abc: 'X:1\\n% ' + id + '\\nK:C\\nC4|' }; }
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
        training: 'export const Yue2Training = () => null;',
      }
      bundle.onResolve({ filter: /.*/ }, args => {
        const key = args.path.includes('api/client') ? 'api'
          : args.path.endsWith('/Yue2Training') ? 'training' : args.path
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
async function componentFixture({ review = false } = {}) {
  const { Yue2Controls, createHarness, control } = await import(
    `data:text/javascript;base64,${Buffer.from(componentBundle.outputFiles[0].text).toString('base64')}#yue2-${++componentRealm}`,
  )
  const props = { workspace: 'A', description: 'A local song', style: 'pop', lyrics: 'Rain falls',
    instrumental: false, onStyle() {}, onLyrics() {} }
  if (review) control.libraries.A = [{ id: 'old-take', project: 'A', title: 'Old take', status: 'needs-review' }]
  const h = createHarness(Yue2Controls, props)
  h.render(); h.flush(); await settle(h)
  const transition = async (workspace, passive = true) => {
    h.render({ ...h.props, workspace })
    if (passive) { h.flush(); await settle(h) }
  }
  return { h, control, transition }
}

for (const action of ['submit', 'continue']) {
  const label = action === 'submit' ? 'Generate with YuE2' : 'Continue with reviewed score'
  const requests = control => action === 'submit' ? control.submits : control.continues
  for (const outcome of ['success', 'failure']) {
    test(`late YuE2 ${action} ${outcome} cannot change a returned project or clear its new operation`, async () => {
      const { h, control, transition } = await componentFixture({ review: action === 'continue' })
      assert.equal(button(h, label).props.disabled, false)
      button(h, label).props.onClick(); h.flush()
      const old = requests(control)[0]
      if (action === 'continue') assert.equal(old.abc, undefined, 'unchanged review resumes its saved plan')
      else assert.equal(old.input.workspace, 'A')
      await transition('B')
      if (action === 'continue') control.libraries.A = [{ id: 'new-take', project: 'A', title: 'New take', status: 'needs-review' }]
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
      if (action === 'continue') assert.equal(requests(control)[1].id, 'new-take')
      requests(control)[1].reject(new Error('Current operation failed'))
      await settle(h)
      assert.match(textContent(h.tree), /Current operation failed/)
      assert.equal(button(h, label).props.disabled, false, 'current operation can clear its own busy state')
    })
  }

  test(`YuE2 ${action} invalidates before passive project effects and on unmount`, async () => {
    for (const boundary of ['layout', 'unmount']) {
      const { h, control, transition } = await componentFixture({ review: action === 'continue' })
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
    const { h, control } = await componentFixture({ review: action === 'continue' })
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
      const { h, control, transition } = await componentFixture({ review: action === 'continue' })
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
