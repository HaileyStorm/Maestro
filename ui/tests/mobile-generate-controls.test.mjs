import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

const [
  duration, profiles, resolution, prompt, music, models, inputs, css,
  typesSource, storeSource, generateSource,
] = await Promise.all([
  source('../src/components/Sidebar/DurationSlider.tsx'),
  source('../src/components/Sidebar/H3PerformanceProfiles.tsx'),
  source('../src/components/Sidebar/ResolutionPresets.tsx'),
  source('../src/components/Sidebar/PromptInput.tsx'),
  source('../src/components/Sidebar/MusicControls.tsx'),
  source('../src/components/Sidebar/ModelSelector.tsx'),
  source('../src/components/Sidebar/InputsPanel.tsx'),
  source('../src/index.css'),
  source('../src/types/index.ts'),
  source('../src/stores/useStore.ts'),
  source('../src/components/Sidebar/GenerateButton.tsx'),
])

function openingTag(contents, element, marker) {
  const markerIndex = contents.indexOf(marker)
  assert.notEqual(markerIndex, -1, `found ${marker}`)
  const start = contents.lastIndexOf(`<${element}`, markerIndex)
  const closingLine = /\n\s*>/.exec(contents.slice(markerIndex))
  assert.ok(start >= 0 && closingLine, `found ${element} containing ${marker}`)
  const end = markerIndex + closingLine.index + closingLine[0].length
  return contents.slice(start, end)
}

function assertMobileTarget(tag, name) {
  assert.match(tag, /mobile-control-target/, `${name} uses the <=767px 44px target contract`)
  assert.match(tag, /focus-visible:(?:outline-none|ring-2)/, `${name} keeps a visible keyboard focus treatment`)
}

function flattenElements(value, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) flattenElements(child, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if ('type' in value && 'props' in value) result.push(value)
  flattenElements(value.props?.children, result)
  return result
}

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

let renderedControlsPromise
function loadRenderedControls() {
  if (renderedControlsPromise) return renderedControlsPromise
  renderedControlsPromise = build({
    stdin: {
      contents: `
        export { H3EstimateBadge } from './src/components/Sidebar/H3PerformanceProfiles.tsx'
        export { ResolutionPresets } from './src/components/Sidebar/ResolutionPresets.tsx'
      `,
      resolveDir: new URL('..', import.meta.url).pathname,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    treeShaking: true,
    write: false,
    plugins: [{
      name: 'mobile-generate-render-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'mobile-generate' }))
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'mobile-generate' }))
        bundle.onResolve({ filter: /^lucide-react$/ }, () => ({ path: 'icons', namespace: 'mobile-generate' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'mobile-generate' }))
        bundle.onLoad({ filter: /.*/, namespace: 'mobile-generate' }, args => {
          if (args.path === 'react') return { contents: `
            export const useEffect = () => {}
            export const useState = initial => [typeof initial === 'function' ? initial() : initial, () => {}]
          ` }
          if (args.path === 'jsx-runtime') return { contents: `
            export const Fragment = Symbol.for('mobile-generate-fragment')
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          if (args.path === 'icons') return { contents: `
            export const Clock3 = props => ({ type: 'svg', props })
            export const Gauge = props => ({ type: 'svg', props })
          ` }
          return { contents: `
            export const h3ProfileMatches = () => false
            export const useStore = selector => selector(globalThis.__maestroMobileGenerateStore)
          ` }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text)))
  return renderedControlsPromise
}

test('H3 duration controls retain planning callbacks and expose mobile targets', () => {
  const automatic = openingTag(duration, 'button', 'setLocked(!locked)')
  const disclosure = duration.match(/<summary[^>]*>Mode details<\/summary>/)?.[0]
  assert.ok(disclosure, 'found native mode-details disclosure')

  assertMobileTarget(automatic, 'Automatic/Manual segment control')
  assert.match(automatic, /aria-pressed=\{locked\}/)
  assertMobileTarget(disclosure, 'mode details disclosure')
  assert.match(duration, /<details[^>]*>[\s\S]*<summary[^>]*>Mode details<\/summary>[\s\S]*FL2VA[\s\S]*Ref2VA[\s\S]*<\/details>/)
  assert.match(duration, /htmlFor="studio-duration-seconds"/)
  assert.match(duration, /id="studio-duration-seconds"/)
  assert.match(duration, /htmlFor="studio-window-seconds"/)
  assert.match(duration, /id="studio-window-seconds"/)
  assert.match(duration, /htmlFor="advanced-window-overlap"/)
  assert.match(duration, /id="advanced-window-overlap"/)
  assert.match(duration, /mobile-control-target mt-2 flex cursor-pointer items-start/)
  assert.match(duration, /checked=\{h3AdaptiveConditioning\}/)
  assert.match(duration, /onChange=\{event => setParam\('h3_adaptive_conditioning', event\.target\.checked\)\}/)
  assert.match(duration, /mb-1\.5 flex flex-wrap items-center justify-between/)
  assert.match(duration, /Estimated shots \$\{estimatedSegmentLabel\}/)
  assert.match(duration, /Match each shot automatically/)
  assert.match(duration, /Choose one Text &amp; frames checkpoint and one References checkpoint next to Generate/)
  assert.match(duration, /Multi-shot videos appear on the job card immediately,[\s\S]*pause for plan review,[\s\S]*continue automatically/)
  assert.match(duration, /Mode details[\s\S]*FL2VA[\s\S]*Ref2VA/)
})

test('performance profile and resolution selection remain exact at compact and narrow widths', () => {
  const profileSelect = openingTag(profiles, 'select', 'value={visibleSelection}')
  assertMobileTarget(profileSelect, 'H3 profile select')
  assert.match(profileSelect, /id="h3-performance-profile"/)
  assert.match(profileSelect, /onChange=\{event => \{/)
  assert.match(profiles, /void applyProfile\(event\.target\.value as H3PerformanceProfileId\)/)

  const nativeResolution = openingTag(resolution, 'select', 'value={normalizedResolution}')
  assertMobileTarget(nativeResolution, 'H3 resolution select')
  assert.match(nativeResolution, /onChange=\{event => setH3NativeResolution\(event\.target\.value\)\}/)
  assert.match(resolution, /Loading supported creation sizes/)
  assert.match(resolution, /choose a supported size/)
  assert.match(resolution, /supported creation size/)
  assert.match(resolution, /max-w-full overflow-x-auto/)
  assert.match(resolution, /role="group" aria-label="Resolution presets"/)
  const preset = openingTag(resolution, 'button', 'onClick={() => setResolutionPreset(p)}')
  assert.match(preset, /type="button"/)
  assert.match(preset, /aria-pressed=\{resolutionPreset === p\}/)
  assert.match(preset, /mobile-control-target/)
  assert.match(preset, /min-w-11/)
  assert.match(preset, /md:min-w-0/)

  const narrowUsableWidth = 320 - 32
  assert.ok(5 * 44 <= narrowUsableWidth, 'five preset controls retain a 44px floor at 320px')
})

test('adaptive H3 controls keep independent model groups and fail closed before Generate', () => {
  assert.match(storeSource, /selectAdaptiveH3Model/)
  assert.match(storeSource, /h3_adaptive_fl2va_model/)
  assert.match(storeSource, /h3_adaptive_ref2va_model/)
  assert.match(models, /heading="Text & frames"/)
  assert.match(models, /detail="FL2VA · follows prompts, start and end frames, and continuity"/)
  assert.match(models, /heading="References"/)
  assert.match(models, /detail="Ref2VA · follows reference images, video, and audio"/)
  assert.match(models, /h3AdaptivePickerModelCompatible/)
  assert.match(models, /allowed=\{H3_FL2VA_MODEL_SET\}/)
  assert.match(models, /allowed=\{H3_REF2VA_MODEL_SET\}/)
  assert.equal((models.match(/function CheckpointPicker\(/g) || []).length, 1)
  assert.doesNotMatch(models, /function AdaptiveCheckpointPicker/)
  assert.match(models, /disabled=\{w4a8Unavailable \|\| legalBlocked\}/)
  assert.match(models, /aria-label=\{heading \? `\$\{heading\} model:/)
  assert.match(models, /selectedOutsideAllowed/)
  assert.match(models, /This saved checkpoint is no longer available/)
  assert.match(generateSource, /h3AdaptiveSelectionError/)
  assert.match(generateSource, /h3ActiveCheckpoints/)
})

test('external H3 experiment profile IDs remain typed and restorable', () => {
  assert.match(typesSource, /dasiwa_ref2va_experimental/)
  assert.match(typesSource, /dasiwa_ref2va_suspected_experimental/)
  assert.match(typesSource, /better_motion_ref2va_experimental/)
  assert.match(storeSource, /dasiwa_ref2va_experimental/)
  assert.match(storeSource, /dasiwa_ref2va_suspected_experimental/)
  assert.match(storeSource, /better_motion_ref2va_experimental/)
})

test('creative-guide provenance and prompt-writing actions are reachable without changing request semantics', () => {
  const workflowCard = openingTag(prompt, 'button', 'data-workflow-id={style.id}')
  const surprise = openingTag(prompt, 'button', 'onClick={chooseSurprise}')
  const clear = openingTag(prompt, 'button', "onClick={() => setSelection('')}")
  const workflowSource = openingTag(prompt, 'a', 'href={catalog.source}')
  const enhance = openingTag(prompt, 'button', 'onClick={runEnhancement}')

  assertMobileTarget(workflowCard, 'creative guide card')
  assert.match(workflowCard, /aria-pressed=\{isSelected\}/)
  assert.match(workflowCard, /onClick=\{\(\) => setSelection\(style\.id\)\}/)
  assertMobileTarget(surprise, 'creative guide Surprise helper')
  assert.match(surprise, /disabled=\{loading \|\| styles\.length === 0\}/)
  assertMobileTarget(clear, 'creative guide Clear action')
  assert.match(clear, /disabled=\{!selection\}/)
  assertMobileTarget(workflowSource, 'creative guide source')
  assert.match(workflowSource, /target="_blank"/)
  assert.match(workflowSource, /rel="noreferrer"/)
  assert.match(prompt, /Creative guide jukebox/)
  assert.match(prompt, /grid-cols-\[repeat\(auto-fit,minmax\(132px,1fr\)\)\]/)
  assert.match(prompt, /max-h-\[28rem\].*overflow-y-auto/)
  assert.match(prompt, /Workflow ID: \{style\.id\}/)
  assert.match(prompt, /<span aria-hidden="true">✓<\/span> Selected/)
  assert.match(prompt, /No guide selected · prompt only/)
  assert.match(prompt, /Choose an optional guide for pacing, framing, and finish/)
  assert.match(prompt, /Source details[\s\S]*MiniMax H3 recipe library/)
  assertMobileTarget(enhance, 'prompt improvement button')
  assert.match(prompt, /const runEnhancement = \(\) => \{\s*requestQueueView\(\)\s*void enhancePrompt\(\)\s*\}/)
  assert.match(enhance, /aria-label="Improve prompt"/)
  assert.match(prompt, /aria-label="Choose writing mode"/)
  assert.match(prompt, /Improve before Generate/)
  assert.match(prompt, /Single speaker, more detailed/)
  assert.match(prompt, /More detailed and creative/)
  assert.match(prompt, /Faster draft/)
  assert.doesNotMatch(prompt, /loadingPhase\.replaceAll|vision projector|compactBytes/)
  assert.match(prompt, /mobile-control-target mt-1 flex cursor-pointer items-start/)
  assert.match(prompt, /checked=\{studioPromptEnhance\}/)
  assert.match(prompt, /onChange=\{event => setStudioPromptEnhance\(event\.target\.checked\)\}/)
  assert.match(prompt, /block break-words md:truncate/)
  assert.match(prompt, /aria-label=\{isMultiVoice[\s\S]{0,180}Write a speech \(use dropdown to switch to dialogue\)/)
  assert.match(prompt, /id="prompt-enhancement-menu"/)
  assert.match(prompt, /max-w-\[calc\(100vw-2rem\)\]/)
  assert.match(prompt, /event\.key !== 'Escape'/)
  assert.match(prompt, /ttsPopupRef\.current\?\.querySelector<HTMLButtonElement>/)
  assert.match(prompt, /window\.requestAnimationFrame\(\(\) => ttsMenuTriggerRef\.current\?\.focus\(\)\)/)
  for (const mode of ['monologue', 'monologue_fast', 'dialogue', 'dialogue_fast']) {
    assert.match(prompt, new RegExp(`runTtsEnhancement\\('${mode}'\\)`))
  }
})

test('song drafting copy leads with the creator goal instead of implementation jargon', () => {
  assert.match(music, /configured AI writing assistant can draft the Style/)
  assert.match(music, /Review and edit the draft below/)
  assert.doesNotMatch(music, /Let the LLM write/)
})

test('model selection, terms, and manual-install actions retain authority and exact URLs', () => {
  const trigger = openingTag(models, 'button', 'onClick={() => setOpen(!open)}')
  const terms = openingTag(models, 'a', 'href={requirement.license_url}')
  const accept = openingTag(models, 'button', 'acceptHostTerm(requirement.term)')
  const sourceLink = openingTag(models, 'a', 'href={currentModel.manual_installation.source_url}')
  const downloadLink = openingTag(models, 'a', 'href={currentModel.manual_installation.download_url}')
  const verify = openingTag(models, 'button', 'verifyCurrentManualCheckpoint()')
  const option = openingTag(models, 'button', 'if (await onSelect(model.model_type)) {')

  for (const [name, tag] of [
    ['model trigger', trigger],
    ['terms link', terms],
    ['terms acceptance', accept],
    ['manual source', sourceLink],
    ['manual download', downloadLink],
    ['manual verification', verify],
    ['model option', option],
  ]) assertMobileTarget(tag, name)

  assert.match(trigger, /aria-expanded=\{open\}/)
  assert.match(trigger, /aria-controls=\{menuId\}/)
  assert.match(models, /menuId="model-selector-menu"/)
  assert.match(models, /id=\{menuId\}/)
  assert.match(models, /max-w-\[calc\(100vw-2rem\)\]/)
  assert.match(models, /top-0/)
  assert.match(models, /-translate-y-\[calc\(100%\+0\.25rem\)\]/)
  assert.match(models, /max-h-\[min\(404px,calc\(100dvh-2rem\)\)\]/)
  assert.match(models, /min-h-0 flex-1 overflow-y-auto/)
  assert.match(models, /event\.key !== 'Escape'/)
  const sharedPopupLifecycle = models.match(
    /\/\/ Treat the model list as a non-modal dialog:[\s\S]*?\}, \[open\]\)/,
  )?.[0] || ''
  assert.match(sharedPopupLifecycle, /if \(!open\) return/)
  assert.doesNotMatch(sharedPopupLifecycle, /includeW4a8/)
  assert.match(sharedPopupLifecycle, /document\.addEventListener\('mousedown', handleClick\)/)
  assert.match(sharedPopupLifecycle, /document\.addEventListener\('keydown', handleKeyDown, true\)/)
  assert.match(models, /if \(!open \|\| !includeW4a8\) return[\s\S]*fetchH3AccelerationStatus/)
  assert.match(models, /event\.stopImmediatePropagation\(\)/)
  assert.match(models, /popupRef\.current\?\.querySelector<HTMLElement>/)
  assert.match(models, /window\.requestAnimationFrame\(\(\) => triggerRef\.current\?\.focus\(\)\)/)
  assert.match(terms, /target="_blank"/)
  assert.match(terms, /rel="noreferrer"/)
  assert.match(sourceLink, /target="_blank"/)
  assert.match(sourceLink, /rel="noreferrer"/)
  assert.match(downloadLink, /target="_blank"/)
  assert.match(downloadLink, /rel="noreferrer"/)
  assert.match(models, /disabled=\{hostTermsLoading \|\| !hostTerms\}/)
  assert.match(models, /disabled=\{verifyingManualCheckpoint \|\| pendingRequirements\.length > 0\}/)
  assert.match(models, /label: 'Reference media'/)
  assert.match(models, /label: 'Reference images'/)
  assert.match(models, /h3AdaptivePair/)
  assert.match(models, /selectAdaptiveH3Model\('fl2va', type\)/)
  assert.match(models, /selectAdaptiveH3Model\('ref2va', type\)/)
  assert.match(models, /aria-label=\{heading \? `\$\{heading\} models` : 'Models'\}/)
  assert.match(option, /aria-pressed=\{isSelected\}/)
  assert.match(models, /\[&_button\]:min-h-11/)
  assert.match(models, /md:\[&_button\]:min-h-0/)

  const h3Authorization = openingTag(inputs, 'a', 'href={HOST_TERM_NOTICES.minimax_h3_ref2va.href}')
  assertMobileTarget(h3Authorization, 'MiniMax H3 Ref2VA authorization link')
  assert.match(h3Authorization, /target="_blank"/)
  assert.match(h3Authorization, /rel="noreferrer"/)
  assert.match(h3Authorization, />\{HOST_TERM_NOTICES\.minimax_h3_ref2va\.linkLabel\}<\/a>/)
  assert.match(inputs, /void acceptHostTerm\('minimax_h3_ref2va'\)/)
  assert.match(inputs, /MiniMax H3 input mode/)
  assert.match(inputs, /Reference media can guide characters, objects, settings, style, motion, or sound/)
  assert.doesNotMatch(inputs, />\s*Semantic context:/)
})

test('shared target contract ends below the 768px compact breakpoint', () => {
  const mobile = css.slice(css.indexOf('@media (max-width: 767px)'))
  assert.match(mobile, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.doesNotMatch(css, /@media \(max-width: 768px\)[\s\S]*\.mobile-control-target/)
})

test('rendered narrow controls expose non-overflowing estimate and exact preset state', async t => {
  const { H3EstimateBadge, ResolutionPresets } = await loadRenderedControls()
  const estimateTree = H3EstimateBadge({
    estimate: {
      confidence: 'low',
      seconds: 3600,
      model_load_state: 'cold',
      model_load_seconds: 600,
      range_seconds: { low: 3000, high: 4200 },
      sample_count: 1,
      source: 'conservative extrapolation',
      uncertainty_reasons: ['extrapolated'],
    },
    downloadRequired: true,
  })
  assert.match(estimateTree.props.className, /min-w-0 flex-wrap/)
  assert.doesNotMatch(estimateTree.props.className, /whitespace-nowrap/)
  assert.match(flattenElements(estimateTree).find(node => node.props?.className === 'break-words')?.props.className || '', /break-words/)

  const previousStore = globalThis.__maestroMobileGenerateStore
  t.after(() => { globalThis.__maestroMobileGenerateStore = previousStore })
  globalThis.__maestroMobileGenerateStore = {
    resolutionPreset: '720p',
    setResolutionPreset() {},
    params: { resolution: '1280x720', model_type: 'image', delivery_resolution: '', delivery_fit: '' },
    modelOptions: null,
    generationMode: 'image',
    spatialUpsampling: 'none',
    setH3NativeResolution() {},
  }
  const resolutionTree = ResolutionPresets()
  const elements = flattenElements(resolutionTree)
  const group = elements.find(node => node.props?.role === 'group')
  assert.equal(group?.props['aria-label'], 'Resolution presets')
  const buttons = elements.filter(node => node.type === 'button')
  assert.equal(buttons.length, 5)
  assert.equal(buttons.filter(button => button.props['aria-pressed'] === true).length, 1)
  assert.ok(buttons.every(button => /mobile-control-target/.test(button.props.className)))
})

test('H3 resolution control survives model options arriving before resolution hydration', async t => {
  const { ResolutionPresets } = await loadRenderedControls()
  const previousStore = globalThis.__maestroMobileGenerateStore
  t.after(() => { globalThis.__maestroMobileGenerateStore = previousStore })
  globalThis.__maestroMobileGenerateStore = {
    resolutionPreset: 'high',
    setResolutionPreset() {},
    params: {
      resolution: undefined,
      model_type: 'minimax_h3',
      delivery_resolution: '',
      delivery_fit: '',
    },
    modelOptions: {
      model_type: 'minimax_h3',
      resolutions: [{ value: '1344x768' }, { value: '960x544' }],
    },
    generationMode: 'video',
    spatialUpsampling: 'none',
    setH3NativeResolution() {},
  }

  let tree
  assert.doesNotThrow(() => { tree = ResolutionPresets() })
  const select = flattenElements(tree).find(node => node.type === 'select')
  assert.equal(select?.props.value, '')
  const options = flattenElements(tree).filter(node => node.type === 'option')
  assert.equal(options[0]?.props.children, 'Choose a supported size')
})
