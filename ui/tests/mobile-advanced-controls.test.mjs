import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const advancedUrl = new URL('../src/components/Sidebar/AdvancedSettings.tsx', import.meta.url)
const postProcessingUrl = new URL('../src/components/Sidebar/PostProcessing.tsx', import.meta.url)
const cssUrl = new URL('../src/index.css', import.meta.url)

const [advanced, postProcessing, css] = await Promise.all([
  readFile(advancedUrl, 'utf8'),
  readFile(postProcessingUrl, 'utf8'),
  readFile(cssUrl, 'utf8'),
])

test('Advanced mobile controls expose 44px targets without changing their handlers', () => {
  assert.match(
    advanced,
    /aria-controls="advanced-settings-drawer"[\s\S]{0,320}onClick=\{\(\) => setOpen\(current => !current\)\}[\s\S]{0,240}mobile-control-target/,
  )
  assert.match(advanced, /mobile-control-target[^"\n]*focus-visible:ring-2 focus-visible:ring-accent-blue/)

  for (const marker of [
    'aria-controls="advanced-preset-save-form"',
    'id="h3-attention-engine"',
    'id="advanced-seed-label"',
    'id="advanced-seed"',
    'aria-labelledby="advanced-seed-label"',
    'id="advanced-inference-steps-label"',
    'id="advanced-inference-steps"',
    'aria-labelledby="advanced-inference-steps-label"',
    'id="advanced-guidance-scale-label"',
    'id="advanced-guidance-scale"',
    'aria-labelledby="advanced-guidance-scale-label"',
    'aria-label="Output Count"',
  ]) {
    assert.match(advanced, new RegExp(marker), marker)
  }

  assert.match(advanced, /aria-controls="advanced-preset-save-form"[\s\S]{0,240}mobile-control-target[\s\S]{0,240}Save Current/)
  assert.match(advanced, /setH3Custom\('h3_sol_dense_steps', 0\)[\s\S]{0,240}mobile-control-target[\s\S]{0,240}Apply benchmark settings/)
  assert.match(advanced, /setParam\('seed', -1\)[\s\S]{0,240}mobile-control-target[\s\S]{0,240}Random/)
  assert.match(advanced, /mobile-control-target[^"\n]*flex items-center gap-2 cursor-pointer group/)
  assert.match(advanced, /onClick=\{\(\) => setParam\('seed', -1\)\}/)
  assert.match(advanced, /onChange=\{e => setParam\('repeat_generation', Number\(e\.target\.value\)\)\}/)
  assert.match(advanced, /aria-label="Preset name"/)
  assert.match(advanced, /mobile-control-target min-w-0 flex-1/)
  assert.match(advanced, /mobile-control-target flex shrink-0 items-center justify-center/)
  assert.match(advanced, /opacity-100 md:opacity-0 md:group-hover:opacity-100/)
})

test('H3 advanced copy leads with practical tradeoffs and labels local measurements clearly', () => {
  assert.match(advanced, /H3 Performance/)
  assert.match(advanced, /Kijai Sol-Attn · faster, small quality tradeoff/)
  assert.match(advanced, /Published speed claims are not measurements from this computer/)
  assert.match(advanced, /Benchmark on this computer/)
  assert.match(advanced, /Each result is one measured run/)
  assert.doesNotMatch(advanced, /fail-closed|kernel, visual, and audio gates|conditioning prefix|privacy-safe timing|this-PC|authored distilled schedule/i)
})

test('Post Processing is a labelled disclosure with the same state transition', () => {
  assert.match(postProcessing, /type="button"\s+onClick=\{\(\) => setOpen\(!open\)\}/)
  assert.match(postProcessing, /aria-expanded=\{open\}/)
  assert.match(postProcessing, /aria-controls="post-processing-settings"/)
  assert.match(postProcessing, /id="post-processing-settings"/)
  assert.match(postProcessing, /mobile-control-target[^"\n]*w-full/)
  assert.match(postProcessing, /id="post-processing-upscaling-label"/)
  assert.match(postProcessing, /id="post-processing-upscaling"/)
  assert.match(postProcessing, /aria-labelledby="post-processing-upscaling-label"/)
  assert.match(postProcessing, /aria-label="Film Grain Intensity"/)
  assert.match(postProcessing, /aria-label="Film Grain Saturation"/)
  assert.match(postProcessing, /aria-label="Voice Clone"[\s\S]{0,240}mobile-control-target flex items-center justify-center/)
  assert.equal(postProcessing.match(/mobile-control-target flex-1 py-1\.5/g)?.length, 2)
  assert.match(postProcessing, /aria-label=\{`Remove \$\{label\} reference`\}[\s\S]{0,240}mobile-control-target/)
})

test('Voice Clone upload is a keyboard-native button with a mobile-only 44px target', () => {
  const uploadButton = postProcessing.match(
    /<button\s+type="button"\s+onClick=\{\(\) => vcFileRefs\[idx\]\.current\?\.click\(\)\}[\s\S]{0,700}<\/button>/,
  )?.[0]

  assert.ok(uploadButton, 'the upload prompt uses a native button so Enter and Space activation are built in')
  assert.match(uploadButton, /disabled=\{vcUploading === idx\}/)
  assert.match(uploadButton, /className="[^"]*mobile-control-target[^"]*w-full[^"]*focus-visible:ring-2/)
  assert.match(uploadButton, /Uploading\.\.\.[\s\S]*Upload \$\{label\.toLowerCase\(\)\} sample/)
  assert.doesNotMatch(uploadButton, /<input/, 'the file input is not nested inside the interactive button')
  assert.match(
    postProcessing,
    /<\/button>\s*<input\s+ref=\{vcFileRefs\[idx\]\}\s+type="file"\s+accept="audio\/\*,video\/\*"\s+className="hidden"\s+onChange=\{e => \{ const f = e\.target\.files\?\.\[0\]; if \(f\) handleVcUpload\(idx, f\) \}\}/,
  )

  const mobile = css.slice(css.indexOf('@media (max-width: 767px)'))
  const desktop = css.slice(0, css.indexOf('@media (max-width: 767px)'))
  assert.match(mobile, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.doesNotMatch(desktop, /\.mobile-control-target\s*\{/, 'the 44px floor stops before the 768px compact layout')
})

test('native ranges keep thin tracks and gain a 44px mobile focus and touch box cross-engine', () => {
  assert.match(css, /input\[type="range"\]\s*\{[^}]*height:\s*14px;[^}]*background:\s*transparent;/s)
  assert.match(css, /input\[type="range"\]::-webkit-slider-runnable-track\s*\{[^}]*height:\s*4px;/s)
  assert.match(css, /input\[type="range"\]::-webkit-slider-thumb\s*\{[^}]*box-sizing:\s*border-box;[^}]*width:\s*14px;[^}]*height:\s*14px;/s)
  assert.match(css, /input\[type="range"\]::-moz-range-track\s*\{[^}]*height:\s*4px;/s)
  assert.match(css, /input\[type="range"\]::-moz-range-thumb\s*\{[^}]*box-sizing:\s*border-box;[^}]*width:\s*14px;[^}]*height:\s*14px;/s)

  const mobile = css.slice(css.indexOf('@media (max-width: 767px)'))
  assert.match(mobile, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.match(mobile, /input\[type="range"\]\s*\{[^}]*height:\s*44px;/s)
  assert.match(mobile, /::-webkit-slider-runnable-track\s*\{[^}]*height:\s*6px;/s)
  assert.match(mobile, /::-webkit-slider-thumb\s*\{[^}]*width:\s*20px;[^}]*height:\s*20px;/s)
  assert.match(mobile, /::-moz-range-track\s*\{[^}]*height:\s*6px;/s)
  assert.match(mobile, /::-moz-range-thumb\s*\{[^}]*width:\s*20px;[^}]*height:\s*20px;/s)
})
