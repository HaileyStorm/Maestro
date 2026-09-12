import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { transform } from 'esbuild'

const root = new URL('../', import.meta.url)
const helper = await readFile(new URL('src/lib/promptEnhancement.ts', root), 'utf8')
const store = await readFile(new URL('src/stores/useStore.ts', root), 'utf8')
const prompt = await readFile(new URL('src/components/Sidebar/PromptInput.tsx', root), 'utf8')
const start = store.indexOf('    const enhanceRequested = state.studioPromptEnhance')
const end = store.indexOf('    if (enhanceRequested', start)
assert.ok(start >= 0 && end > start)
const compiled = await transform(`${helper}
export function effective(state) { ${store.slice(start, end)} return enhanceRequested }
`, { loader: 'ts', format: 'esm' })
const { supportsPromptPreparation, effective } = await import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString('base64')}`)

test('control and submission share supported workflow decisions without changing saved preference', () => {
  for (const mode of ['video', 'image', 'audio', 'avatar']) {
    for (const imageMode of [0, 2, 4]) {
      for (const editSubMode of [null, 'recast', 'restyle', 'outpaint', 'inpaint', 'retake']) {
        for (const audioOnly of [false, true]) {
          const supported = !audioOnly && !(mode === 'video' && imageMode === 4) && !(mode === 'avatar' && editSubMode)
          assert.equal(supportsPromptPreparation(mode, imageMode, editSubMode, audioOnly), supported)
          for (const saved of [false, true]) {
            const state = { generationMode: mode, editSubMode, params: { image_mode: imageMode }, modelOptions: { audio_only: audioOnly }, studioPromptEnhance: saved }
            assert.equal(effective(state), saved && supported)
            assert.equal(state.studioPromptEnhance, saved)
          }
        }
      }
    }
  }
  const state = { generationMode: 'video', editSubMode: null, params: { image_mode: 0 }, studioPromptEnhance: true }
  assert.equal(effective(state), true)
  state.params.image_mode = 4
  assert.equal(effective(state), false)
  state.params.image_mode = 0
  assert.equal(effective(state), true, 'returning to a supported workflow restores the saved preference')
})

test('unsupported workflows omit the checkbox while keeping standalone improvement', () => {
  assert.match(prompt, /const enhancerFooter = supportsPromptPreparation\(generationMode, imageMode, editSubMode, isAudioOnly\)/)
  assert.match(prompt, /\{enhancerFooter && \(/)
  assert.match(prompt, /enhancePrompt/)
  assert.doesNotMatch(store, /Enhance before Generate is available for standard Studio generations/)
})
