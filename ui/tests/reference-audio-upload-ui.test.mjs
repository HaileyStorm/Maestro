import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const uploadZoneUrl = new URL('../src/components/shared/FileUploadZone.tsx', import.meta.url)
const inputsPanelUrl = new URL('../src/components/Sidebar/InputsPanel.tsx', import.meta.url)
const audioModeUrl = new URL('../src/components/Sidebar/AudioModeSection.tsx', import.meta.url)

const asDataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

async function loadUploadZone() {
  const result = await build({
    entryPoints: [uploadZoneUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'upload-zone-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'upload-zone' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'upload-zone' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'upload-zone' }))
        bundle.onLoad({ filter: /.*/, namespace: 'upload-zone' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export const useCallback = callback => callback
              export const useRef = () => ({ current: globalThis.__uploadZoneInput })
              export const useState = initial => [
                globalThis.__uploadZoneStateValues.length ? globalThis.__uploadZoneStateValues.shift() : initial,
                value => globalThis.__uploadZoneStateUpdates.push(value),
              ]
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('upload-zone-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          return { contents: `
            export const Upload = props => ({ type: 'svg', props: props || {} })
            export const X = Upload
          ` }
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

async function loadAudioModeSection() {
  const result = await build({
    entryPoints: [audioModeUrl.pathname],
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'audio-mode-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'lucide', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /api\/client$/ }, () => ({ path: 'api', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /shared\/ChoiceControl$/ }, () => ({ path: 'choice', namespace: 'audio-mode' }))
        bundle.onResolve({ filter: /shared\/FileUploadZone$/ }, () => ({ path: 'file-zone', namespace: 'audio-mode' }))
        bundle.onLoad({ filter: /.*/, namespace: 'audio-mode' }, args => {
          if (args.path === 'react') {
            return { contents: `
              export function useState(initial) {
                const index = globalThis.__audioModeHookIndex++
                const provided = globalThis.__audioModeStateValues[index]
                const value = provided === undefined ? initial : provided
                return [value, update => globalThis.__audioModeStateUpdates.push({
                  index,
                  value: typeof update === 'function' ? update(value) : update,
                })]
              }
            ` }
          }
          if (args.path === 'jsx-runtime') {
            return { contents: `
              export const Fragment = Symbol.for('audio-mode-fragment')
              export const jsx = (type, props, key) => ({ type, key, props: props || {} })
              export const jsxs = jsx
            ` }
          }
          if (args.path === 'lucide') return { contents: `export const Plus = 'Plus'; export const X = 'X'` }
          if (args.path === 'store') return { contents: `export const useStore = selector => selector(globalThis.__audioModeStore)` }
          if (args.path === 'api') {
            return { contents: `
              export const uploadAudio = file => globalThis.__audioModeUpload(file)
              export const uploadImage = file => {
                globalThis.__audioModeImageUploads.push(file)
                return globalThis.__audioModeImageUpload(file)
              }
            ` }
          }
          if (args.path === 'choice') return { contents: `export function ChoiceControl() { return null }` }
          return { contents: `export function FileUploadZone() { return null }` }
        })
      },
    }],
  })
  return import(asDataModule(result.outputFiles[0].text))
}

function resetAudioMode(upload, stateValues = []) {
  globalThis.__audioModeHookIndex = 0
  globalThis.__audioModeStateValues = stateValues
  globalThis.__audioModeStateUpdates = []
  globalThis.__audioModeImageUploads = []
  globalThis.__audioModeUpload = upload
  globalThis.__audioModeImageUpload = () => Promise.reject(new Error('wrong upload route'))
  globalThis.__audioModeStoreCalls = []
  const record = (name, ...args) => globalThis.__audioModeStoreCalls.push([name, ...args])
  globalThis.__audioModeStore = {
    modelOptions: {
      audio_only: true,
      audio_mode_from_voice_count: true,
      max_voice_count: 6,
      audio_prompt_type_sources: { selection: [''], default: '' },
    },
    params: { audio_prompt_type: '' },
    setParam: (...args) => record('setParam', ...args),
    audioGuideFilename: null,
    setAudioGuideFilename: (...args) => record('setAudioGuideFilename', ...args),
    ttsVoiceCount: 2,
    ttsVoices: [
      { name: 'A', filename: null, path: null },
      { name: 'B', filename: null, path: null },
    ],
    addTtsVoice: () => record('addTtsVoice'),
    removeTtsVoice: index => record('removeTtsVoice', index),
    setTtsVoiceName: (...args) => record('setTtsVoiceName', ...args),
    setTtsVoiceFile: (...args) => record('setTtsVoiceFile', ...args),
    setDurationSeconds: (...args) => record('setDurationSeconds', ...args),
  }
}

function flatten(value, result = []) {
  if (Array.isArray(value)) {
    value.forEach(child => flatten(child, result))
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flatten(value.props?.children, result)
  return result
}

function resetRuntime(stateValues = []) {
  globalThis.__uploadZoneStateValues = [...stateValues]
  globalThis.__uploadZoneStateUpdates = []
  globalThis.__uploadZoneInput = {
    value: 'previous-selection',
    clicks: 0,
    click() { this.clicks += 1 },
  }
}

function baseProps(overrides = {}) {
  return {
    label: 'Drop reference audio',
    accept: '.wav,.mp3,.flac,.ogg,.m4a',
    filename: null,
    onFile() {},
    onClear() {},
    ...overrides,
  }
}

test('mounted input handles selection, cancel, and same-file reselection', async () => {
  const { FileUploadZone } = await loadUploadZone()
  resetRuntime()
  const selected = []
  const elements = flatten(FileUploadZone(baseProps({ onFile: file => selected.push(file) })))
  const input = elements.find(element => element.type === 'input' && element.props.type === 'file')
  const openButton = elements.find(element => element.type === 'button' && element.props.onDrop)
  assert.ok(input, 'the file input must remain attached to the rendered tree')
  assert.equal(input.props.accept, '.wav,.mp3,.flac,.ogg,.m4a')

  openButton.props.onClick()
  assert.equal(globalThis.__uploadZoneInput.value, '')
  assert.equal(globalThis.__uploadZoneInput.clicks, 1)

  const cancelled = { files: [], value: 'cancelled' }
  input.props.onChange({ currentTarget: cancelled })
  assert.equal(cancelled.value, '')
  assert.equal(selected.length, 0)

  const wav = { name: 'reference.WAV', type: '' }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const target = { files: [wav], value: 'reference.WAV' }
    input.props.onChange({ currentTarget: target })
    assert.equal(target.value, '')
  }
  assert.deepEqual(selected, [wav, wav], 'reselecting the same WAV must fire again')

  openButton.props.onDrop({
    preventDefault() {},
    dataTransfer: { files: [{ name: 'mobile-drop.wav', type: '' }] },
  })
  assert.equal(selected.at(-1).name, 'mobile-drop.wav', 'empty mobile MIME must fall back to the WAV extension')
})

test('clear, busy progress, and bounded errors are visible and accessible', async () => {
  const { FileUploadZone } = await loadUploadZone()
  let clears = 0

  resetRuntime()
  let elements = flatten(FileUploadZone(baseProps({ filename: 'voice.wav', onClear: () => { clears += 1 } })))
  const clearButton = elements.find(element => element.props['aria-label'] === 'Remove voice.wav')
  assert.ok(clearButton)
  assert.match(clearButton.props.className, /min-h-11/)
  clearButton.props.onClick()
  assert.equal(clears, 1)

  resetRuntime()
  elements = flatten(FileUploadZone(baseProps({ busy: true, error: `Voice upload failed. ${'x'.repeat(500)}` })))
  const status = elements.find(element => element.props.role === 'status')
  const alert = elements.find(element => element.props.role === 'alert')
  const input = elements.find(element => element.type === 'input')
  const disabledButton = elements.find(element => element.type === 'button')
  assert.equal(input.props.disabled, true, 'the mounted input cannot bypass busy state')
  assert.equal(disabledButton.props.disabled, true)
  assert.match(status.props.children, /progress/i)
  assert.ok(alert.props.children.length <= 220, 'presented errors stay bounded')
})

test('shared picker consumption clears cancelled and consumed callbacks', async () => {
  const { cancelFileSelection, consumeFileSelection } = await loadUploadZone()
  const calls = []
  const pending = { current: files => calls.push(['stale', files]) }
  const cancelledInput = { files: [], value: 'voice.wav' }
  cancelFileSelection(cancelledInput, pending)
  assert.equal(cancelledInput.value, '')
  assert.equal(pending.current, null)

  consumeFileSelection({ files: [{ name: 'unrouted.wav' }], value: 'unrouted.wav' }, pending)
  assert.deepEqual(calls, [], 'direct activation cannot reach a stale role callback')

  pending.current = files => calls.push(['fresh', files])
  const selectedInput = { files: [{ name: 'fresh.wav' }], value: 'fresh.wav' }
  consumeFileSelection(selectedInput, pending)
  assert.equal(selectedInput.value, '')
  assert.equal(pending.current, null)
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 'fresh')
})

test('TTS voice upload locks every mutable slot and settles success or bounded error', async () => {
  const { AudioModeSection } = await loadAudioModeSection()
  let resolveUpload
  resetAudioMode(() => new Promise(resolve => { resolveUpload = resolve }))
  let elements = flatten(AudioModeSection())
  const zones = elements.filter(element => element.type?.name === 'FileUploadZone')
  assert.equal(zones.length, 2)

  const inFlight = zones[0].props.onFile({ name: 'voice.wav', type: 'audio/wav' })
  await Promise.resolve()
  assert.deepEqual(globalThis.__audioModeStateUpdates.slice(0, 2), [
    { index: 1, value: 'voice-0' },
    { index: 2, value: null },
  ])

  resetAudioMode(() => Promise.resolve({ path: '/unused' }), [null, 'voice-0', null])
  elements = flatten(AudioModeSection())
  const busyZones = elements.filter(element => element.type?.name === 'FileUploadZone')
  assert.equal(busyZones[0].props.busy, true)
  assert.ok(busyZones.every(zone => zone.props.disabled === true), 'no second slot can overlap an upload')
  const mutationButtons = elements.filter(element => element.type === 'button')
  const nameInputs = elements.filter(element => element.type === 'input' && element.props.type === 'text')
  assert.ok(mutationButtons.every(button => button.props.disabled === true), 'add/remove cannot shift a pending slot')
  assert.ok(nameInputs.every(input => input.props.disabled === true))

  resolveUpload({ path: '/uploads/voice.wav' })
  await inFlight
  assert.deepEqual(globalThis.__audioModeStoreCalls.find(call => call[0] === 'setTtsVoiceFile'), [
    'setTtsVoiceFile', 0, 'voice.wav', '/uploads/voice.wav',
  ])
  assert.equal(globalThis.__audioModeImageUploads.length, 0)

  resetAudioMode(() => Promise.reject(new Error(`private-path/${'x'.repeat(500)}`)))
  elements = flatten(AudioModeSection())
  const failedZone = elements.find(element => element.type?.name === 'FileUploadZone')
  await failedZone.props.onFile({ name: 'broken.wav', type: 'audio/wav' })
  const errorUpdate = globalThis.__audioModeStateUpdates.find(update => update.index === 2 && update.value)
  assert.ok(errorUpdate)
  assert.ok(errorUpdate.value.message.length <= 220)
  assert.doesNotMatch(errorUpdate.value.message, /private-path|xxx/)
  assert.equal(globalThis.__audioModeStateUpdates.at(-1).value, null, 'busy state always settles')

  resetAudioMode(() => Promise.resolve({ path: '/unused' }), [null, 'audio_guide', null])
  globalThis.__audioModeStore.modelOptions = {
    audio_only: false,
    audio_prompt_type_sources: { selection: ['A'], default: 'A' },
  }
  globalThis.__audioModeStore.params.audio_prompt_type = 'A'
  elements = flatten(AudioModeSection())
  const modeLock = elements.find(element => element.type === 'fieldset')
  assert.equal(modeLock.props.disabled, true, 'mode switching cannot hide or invalidate an active upload')

  resetAudioMode(() => Promise.resolve({ path: '/unused' }))
  globalThis.__audioModeImageUpload = () => Promise.reject(new Error('unsupported video format'))
  globalThis.__audioModeStore.modelOptions = {
    audio_only: false,
    guide_preprocessing: false,
    audio_prompt_type_sources: { selection: ['K'], default: 'K' },
  }
  globalThis.__audioModeStore.params.audio_prompt_type = 'K'
  elements = flatten(AudioModeSection())
  const videoZone = elements.find(element => element.type?.name === 'FileUploadZone')
  await videoZone.props.onFile({ name: 'guide.mkv', type: 'video/x-matroska' })
  const videoError = globalThis.__audioModeStateUpdates.find(update => update.index === 2 && update.value)
  assert.equal(videoError.value.message, 'Control video is not a supported readable file. Choose another file and try again.')
})

test('the attached picker keeps the same usable touch target on mobile and desktop', async () => {
  const { FileUploadZone } = await loadUploadZone()
  for (const width of [360, 1080]) {
    globalThis.innerWidth = width
    resetRuntime()
    const elements = flatten(FileUploadZone(baseProps()))
    const input = elements.find(element => element.type === 'input')
    const button = elements.find(element => element.type === 'button')
    assert.ok(input)
    assert.match(button.props.className, /min-h-11/)
    assert.match(button.props.className, /w-full/)
    button.props.onClick()
    assert.equal(globalThis.__uploadZoneInput.clicks, 1)
  }
})

test('InputsPanel and TTS audio source contracts use mounted pickers and audio upload', async () => {
  const [inputsSource, audioModeSource, uploadZoneSource] = await Promise.all([
    readFile(inputsPanelUrl, 'utf8'),
    readFile(audioModeUrl, 'utf8'),
    readFile(uploadZoneUrl, 'utf8'),
  ])

  assert.doesNotMatch(inputsSource, /document\.createElement\(['"]input['"]\)/)
  assert.doesNotMatch(uploadZoneSource, /document\.createElement\(['"]input['"]\)/)
  assert.match(inputsSource, /ref=\{filePickerRef\}[\s\S]*type="file"/)
  assert.match(inputsSource, /input\.value = ''[\s\S]*input\.click\(\)/)
  assert.match(inputsSource, /tabIndex=\{-1\}/)
  assert.match(inputsSource, /aria-hidden="true"/)
  assert.match(inputsSource, /addEventListener\('cancel', handleCancel\)/)
  assert.match(inputsSource, /cancelFileSelection\(input, pendingFilePickRef\)/)
  assert.match(inputsSource, /consumeFileSelection\(event\.currentTarget, pendingFilePickRef\)/)
  assert.match(inputsSource, /matchesMediaKind\(f, dropAccept\)/)
  assert.match(inputsSource, /role="status"/)
  assert.match(inputsSource, /role="alert"/)
  assert.match(inputsSource, /handleAddSemanticAudio[\s\S]*api\.uploadAudio\(file\)/)
  assert.match(inputsSource, /handleAddSoundtrack[\s\S]*api\.uploadAudio\(file\)/)

  const voiceHandler = audioModeSource.match(/const handleVoiceUpload[\s\S]*?\n  const clearVoice/)?.[0] || ''
  assert.match(voiceHandler, /api\.uploadAudio\(file\)/)
  assert.doesNotMatch(voiceHandler, /api\.uploadImage\(file\)/)
  assert.match(audioModeSource, /disabled=\{uploadTarget !== null\}/)
  assert.match(uploadZoneSource, /disabled=\{busy \|\| disabled\}/)
  assert.match(audioModeSource, /role="alert"|error=\{uploadError/)
})
