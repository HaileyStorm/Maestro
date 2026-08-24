import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { build } from 'esbuild'

import {
  appendMusicSection,
  moveMusicSection,
  parseMusicSections,
  serializeMusicSections,
  setMusicSectionTag,
  updateMusicSection,
} from '../src/lib/musicSections.ts'

const UI_ROOT = fileURLToPath(new URL('..', import.meta.url))
const asModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

let componentPromise
function loadComponent() {
  if (componentPromise) return componentPromise
  componentPromise = build({
    stdin: { contents: "export { MusicLyricPlayground } from './src/components/Sidebar/MusicLyricPlayground.tsx'", resolveDir: UI_ROOT, loader: 'js' },
    bundle: true, format: 'esm', jsx: 'automatic', platform: 'node', write: false, logLevel: 'silent',
    plugins: [{
      name: 'music-playground-react',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'music-playground' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'music-playground' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide-react', namespace: 'music-playground' }))
        bundle.onLoad({ filter: /.*/, namespace: 'music-playground' }, args => ({
          contents: args.path === 'react'
            ? 'export const useMemo = callback => callback()'
            : args.path === 'lucide-react'
              ? `const icon = props => ({type:'svg',props:props||{}}); export const ArrowDown=icon; export const ArrowUp=icon; export const Clapperboard=icon; export const Plus=icon; export const Trash2=icon`
              : `export const Fragment=Symbol.for('fragment'); export const jsx=(type,props,key)=>({type,key,props:props||{}}); export const jsxs=jsx`,
        }))
      },
    }],
  }).then(result => import(asModule(result.outputFiles[0].text)))
  return componentPromise
}

let storePromise
function loadStore() {
  if (storePromise) return storePromise
  storePromise = build({
    stdin: { contents: "export { useStore } from './src/stores/useStore.ts'", resolveDir: UI_ROOT, loader: 'js' },
    bundle: true, format: 'esm', platform: 'node', write: false, logLevel: 'silent',
  }).then(result => import(asModule(result.outputFiles[0].text)))
  return storePromise
}

function flatten(value, result = []) {
  if (Array.isArray(value)) { value.forEach(item => flatten(item, result)); return result }
  if (!value || typeof value !== 'object') return result
  if (typeof value.type === 'function') return flatten(value.type(value.props || {}), result)
  if ('type' in value && 'props' in value) result.push(value)
  flatten(value.props?.children, result)
  return result
}

function text(value) {
  if (Array.isArray(value)) return value.map(text).join('')
  if (value == null || typeof value === 'boolean') return ''
  if (typeof value !== 'object') return String(value)
  if (typeof value.type === 'function') return text(value.type(value.props || {}))
  return text(value.props?.children)
}

test('section parsing is occurrence-based and lossless for authored lyric lines', () => {
  const lyrics = 'untagged opening\n[Verse]\nHello 世界\n\n[Chorus]\nFirst hook\n[Chorus]\nSecond hook\n[Unknown Mood]\nkeep me'
  const sections = parseMusicSections(lyrics)
  assert.equal(sections.length, 5)
  assert.equal(sections[2].tag, 'Chorus')
  assert.equal(sections[3].tag, 'Chorus')
  assert.equal(sections[4].valid, false)
  assert.equal(serializeMusicSections(sections), lyrics)

  const moved = moveMusicSection(sections, 3, -1)
  assert.equal(moved[2].lines[0], 'Second hook')
  assert.equal(moved[3].lines[0], 'First hook')
  assert.equal(serializeMusicSections(setMusicSectionTag(moved, 4, 'Bridge')).includes('[Bridge]\nkeep me'), true)
})

test('section editing and append operate on lyrics only', () => {
  const caption = 'Dream pop with warm analog synths'
  let sections = parseMusicSections('[Verse]\nOld line')
  sections = updateMusicSection(sections, 0, { lines: ['New line', 'Second line'] })
  sections = appendMusicSection(sections, 'Chorus')
  assert.equal(serializeMusicSections(sections), '[Verse]\nNew line\nSecond line\n[Chorus]')
  assert.equal(caption, 'Dream pop with warm analog synths')
})

test('mounted Playground exposes ordered cards, fixes tags, and hands off without generation', async () => {
  const { MusicLyricPlayground } = await loadComponent()
  const changes = []
  let sent = 0
  const tree = MusicLyricPlayground({
    lyrics: '[Verse]\nLine one\n[Chorus]\nHook',
    onChange: value => changes.push(value),
    onSendToDirector: () => { sent += 1 },
  })
  const elements = flatten(tree)
  const rendered = text(tree)
  assert.match(rendered, /Music3 Lyric Playground/)
  assert.match(rendered, /2 sections/)
  assert.match(rendered, /Send to Director/)
  const textareas = elements.filter(element => element.type === 'textarea')
  assert.equal(textareas[0].props['aria-label'], 'Section 1 lyrics')
  textareas[0].props.onChange({ target: { value: 'Changed' } })
  assert.equal(changes.at(-1), '[Verse]\nChanged\n[Chorus]\nHook')
  const send = elements.find(element => element.type === 'button' && text(element).includes('Send to Director'))
  send.props.onClick()
  assert.equal(sent, 1)
  assert.ok(elements.filter(element => element.type === 'button').every(element => String(element.props.className).includes('mobile-control-target')))
  assert.ok(elements.filter(element => element.type === 'select').every(element => String(element.props.className).includes('mobile-control-target')))
})

test('Studio song handoff fills Director atomically and never starts generation', async () => {
  const originalFetch = globalThis.fetch
  let fetches = 0
  globalThis.fetch = async () => { fetches += 1; throw new Error('unexpected network') }
  try {
    const { useStore } = await loadStore()
    useStore.setState({
      activeWorkspace: 'music-project',
      musicDescription: 'Midnight drive',
      musicInstrumental: false,
      durationSeconds: 245,
      params: { ...useStore.getState().params, model_type: 'minimax_music3', alt_prompt: 'Synthwave', prompt: '[Verse]\nDrive' },
      models: [{ model_type: 'minimax_music3', architecture: 'sglang_omni', name: 'Music3', execution_allowed: false }],
      directorRequestId: 'old-song-request',
      directorRequestWorkspace: 'music-project',
      directorPreparationStatus: { status: 'complete', director_request_id: 'old-song-request' },
    })
    useStore.getState().sendMusicToDirector()
    const state = useStore.getState()
    assert.equal(state.sidebarMode, 'director')
    assert.equal(state.directorSkill, 'music_video')
    assert.equal(state.directorMusicSource, 'generate')
    assert.equal(state.directorMusicModel, 'minimax_music3')
    assert.equal(state.directorSongDescription, 'Midnight drive')
    assert.equal(state.directorSongStyle, 'Synthwave')
    assert.equal(state.directorSongLyrics, '[Verse]\nDrive')
    assert.equal(state.directorSongDuration, 245)
    assert.equal(state.directorTrackGenerating, false)
    assert.equal(state.directorRequestId, null)
    assert.equal(state.directorRequestWorkspace, null)
    assert.equal(state.directorPreparationStatus, null)
    assert.equal(fetches, 0)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('writer requests carry the selected model and reject same-project stale results', async () => {
  const source = await readFile(new URL('../src/components/Sidebar/MusicControls.tsx', import.meta.url), 'utf8')
  assert.match(source, /model_type: requestModelType \|\| undefined/)
  assert.match(source, /const requestIsCurrent = \(\) =>/)
  assert.match(source, /current\.musicDescription\.trim\(\) === requestDescription/)
  assert.match(source, /String\(current\.params\.prompt \|\| ''\) === requestLyrics/)
  assert.match(source, /controller\.signal\.aborted \|\| !requestIsCurrent\(\)/)
  assert.match(source, /Instrumental mode uses the Music Caption and the canonical \[Instrumental\] control tag/)
  assert.match(source, /ariaLabel="Style and music caption"/)
  const store = await readFile(new URL('../src/stores/useStore.ts', import.meta.url), 'utf8')
  assert.match(store, /model_type: s\.directorMusicModel \|\| undefined/)
  assert.match(store, /current\.directorSongLyrics !== requestLyrics/)
  assert.match(store, /current\.directorSongLyrics\.trim\(\) === lyrics/)
  assert.match(store, /_storeDirectorPreparation\(null, null\)/)
  assert.match(store, /setDirectorSongLyrics: \(v\) => set\(\{ \.\.\._retireDirectorMusicPreparation\(\)/)
  assert.match(store, /if \(get\(\)\.directorRequestId !== directorRequestId\) return/)
})
