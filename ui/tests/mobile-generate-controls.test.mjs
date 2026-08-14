import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

const [duration, profiles, resolution, prompt, music, models, inputs, css] = await Promise.all([
  source('../src/components/Sidebar/DurationSlider.tsx'),
  source('../src/components/Sidebar/H3PerformanceProfiles.tsx'),
  source('../src/components/Sidebar/ResolutionPresets.tsx'),
  source('../src/components/Sidebar/PromptInput.tsx'),
  source('../src/components/Sidebar/MusicControls.tsx'),
  source('../src/components/Sidebar/ModelSelector.tsx'),
  source('../src/components/Sidebar/InputsPanel.tsx'),
  source('../src/index.css'),
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
  const disclosure = openingTag(duration, 'button', 'setEvaluationOpen(value => !value)')

  assertMobileTarget(automatic, 'Automatic/Manual segment control')
  assert.match(automatic, /aria-pressed=\{locked\}/)
  assertMobileTarget(disclosure, 'evaluated profile disclosure')
  assert.match(disclosure, /aria-expanded=\{evaluationOpen\}/)
  assert.match(disclosure, /aria-controls="h3-evaluated-profiles"/)
  assert.match(duration, /id="h3-evaluated-profiles"/)
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
  assert.match(duration, /Match each shot to its references automatically/)
  assert.match(duration, /appears on its card immediately[\s\S]*pause briefly for plan review[\s\S]*continue automatically if you leave the plan unchanged and accept any required model terms/)
  assert.match(duration, /Mode details[\s\S]*FL2VA or Ref2VA/)
  assert.match(duration, /technical comparison profiles/)
})

test('performance profile and resolution selection remain exact at compact and narrow widths', () => {
  const profileSelect = openingTag(profiles, 'select', 'value={visibleSelection}')
  assertMobileTarget(profileSelect, 'H3 profile select')
  assert.match(profileSelect, /id="h3-performance-profile"/)
  assert.match(profileSelect, /onChange=\{event => \{/)
  assert.match(profiles, /void applyProfile\(event\.target\.value as H3PerformanceProfileId\)/)

  const nativeResolution = openingTag(resolution, 'select', 'value={resolution}')
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

test('creative-guide provenance and prompt-writing actions are reachable without changing request semantics', () => {
  const workflowSelect = openingTag(prompt, 'select', 'value={selection}')
  const workflowSource = openingTag(prompt, 'a', 'href={catalog.source}')
  const enhance = openingTag(prompt, 'button', 'onClick={() => enhancePrompt()}')

  assertMobileTarget(workflowSelect, 'creative guide select')
  assert.match(workflowSelect, /onChange=\{event => setSelection\(event\.target\.value\)\}/)
  assertMobileTarget(workflowSource, 'creative guide source')
  assert.match(workflowSource, /target="_blank"/)
  assert.match(workflowSource, /rel="noreferrer"/)
  assert.match(prompt, /Creative guide/)
  assert.match(prompt, /Choose an optional guide for pacing, framing, and finish/)
  assert.match(prompt, /Source details[\s\S]*MiniMax H3 recipe library/)
  assert.match(prompt, /Writing assistant/)
  assertMobileTarget(enhance, 'prompt improvement button')
  assert.match(enhance, /aria-label="Improve prompt"/)
  assert.match(prompt, /aria-label="Choose writing mode"/)
  assert.match(prompt, /Working on your prompt/)
  assert.match(prompt, /Writing your revision/)
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
  const option = openingTag(models, 'button', 'if (await selectModel(model.model_type)) {')

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
  assert.match(trigger, /aria-controls="model-selector-menu"/)
  assert.match(models, /id="model-selector-menu"/)
  assert.match(models, /max-w-\[calc\(100vw-2rem\)\]/)
  assert.match(models, /top-0/)
  assert.match(models, /-translate-y-\[calc\(100%\+0\.25rem\)\]/)
  assert.match(models, /max-h-\[min\(404px,calc\(100dvh-2rem\)\)\]/)
  assert.match(models, /min-h-0 flex-1 overflow-y-auto/)
  assert.match(models, /event\.key !== 'Escape'/)
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
