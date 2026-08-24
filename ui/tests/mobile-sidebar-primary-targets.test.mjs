import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const sidebarUrl = new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url)
const privacyUrl = new URL('../src/components/Sidebar/GenerationPrivacyControls.tsx', import.meta.url)
const generationModeUrl = new URL('../src/components/Sidebar/GenerationModeSelector.tsx', import.meta.url)
const videoModeUrl = new URL('../src/components/Sidebar/ModeToggle.tsx', import.meta.url)
const cssUrl = new URL('../src/index.css', import.meta.url)

const [sidebar, privacy, generationMode, videoMode, css] = await Promise.all([
  readFile(sidebarUrl, 'utf8'),
  readFile(privacyUrl, 'utf8'),
  readFile(generationModeUrl, 'utf8'),
  readFile(videoModeUrl, 'utf8'),
  readFile(cssUrl, 'utf8'),
])

function openingTag(source, tag, marker) {
  const markerIndex = source.indexOf(marker)
  assert.notEqual(markerIndex, -1, `found ${marker}`)
  const start = source.lastIndexOf(`<${tag}`, markerIndex)
  const end = source.indexOf('>', markerIndex)
  assert.ok(start >= 0 && end > markerIndex, `found ${tag} containing ${marker}`)
  return source.slice(start, end + 1)
}

function elementBlock(source, tag, marker) {
  const markerIndex = source.indexOf(marker)
  assert.notEqual(markerIndex, -1, `found ${marker}`)
  const start = source.lastIndexOf(`<${tag}`, markerIndex)
  const end = source.indexOf(`</${tag}>`, markerIndex)
  assert.ok(start >= 0 && end > markerIndex, `found complete ${tag} containing ${marker}`)
  return source.slice(start, end + tag.length + 3)
}

test('creative workspace tabs and Recipes retain exact actions with mobile targets', () => {
  assert.match(sidebar, /role="group" aria-label="Creative workspace"/)

  for (const [label, setter] of [
    ['Open Generate', "setSidebarMode('studio')"],
    ['Open Director', "setSidebarMode('director')"],
    ['Open References', "setSidebarMode('reference')"],
  ]) {
    const button = openingTag(sidebar, 'button', setter)
    assert.match(button, /type="button"/)
    assert.match(button, /aria-pressed=/)
    assert.match(button, /mobile-control-target/)
    assert.match(button, /focus-visible:ring-2/)
    assert.match(button, /focus-visible:ring-accent-blue/)
    assert.match(button, new RegExp(setter.replace(/[()]/g, '\\$&')))
    if (label !== 'Open References') assert.match(button, new RegExp(`aria-label="${label}"`))
  }

  assert.match(sidebar, /disabled=\{!activeWorkspace \|\| browsingUploads \|\| referenceLocked\}/)
  assert.match(sidebar, /aria-label=\{referenceLocked \? 'Unlock project to open References' : 'Open References'\}/)

  const recipes = openingTag(sidebar, 'button', 'aria-label="Browse recipes"')
  assert.match(recipes, /type="button"/)
  assert.match(recipes, /onClick=\{openRecipes\}/)
  assert.match(recipes, /mobile-control-target/)
  assert.match(recipes, /focus-visible:ring-2/)
  assert.match(sidebar, /setSidebarOpen\(false\)[\s\S]{0,120}setRecipesOpen\(true\)/)
})

test('Explicit and Private use their complete wrapping labels as mobile targets', () => {
  assert.match(privacy, /role="group" aria-label="Generation output privacy"/)

  const explicit = elementBlock(privacy, 'label', 'Mark this Generate or Director job as explicit')
  const privatePreview = elementBlock(privacy, 'label', "Blur this output's gallery preview until deliberately revealed")
  for (const label of [explicit, privatePreview]) {
    assert.match(label, /mobile-control-target/)
    assert.match(label, /flex-wrap/)
    assert.match(label, /focus-within:ring-2/)
    assert.match(label, /type="checkbox"/)
    assert.match(label, /className="sr-only"/)
  }

  assert.match(explicit, /checked=\{explicitOutput\}/)
  assert.match(explicit, /setExplicitOutput\(enabled\)/)
  assert.match(explicit, /Explicit \{explicitOutput \? 'On' : 'Off'\}/)
  assert.match(privatePreview, /checked=\{privateOutput\}/)
  assert.match(privatePreview, /setPrivateOutput\(event\.target\.checked\)/)
  assert.match(privatePreview, /Private \{privateOutput \? 'On' : 'Off'\}/)
  assert.match(privacy, /Private controls preview blur only\. Project access rules always apply separately\./)
})

test('generation and video selectors wrap without shrinking or changing exact values', () => {
  assert.match(generationMode, /role="group" aria-label="Generation mode"/)
  assert.match(generationMode, /grid-cols-3[^"]*md:grid-cols-5/)
  assert.match(generationMode, /type="button"/)
  assert.match(generationMode, /aria-pressed=\{active\}/)
  assert.match(generationMode, /mobile-control-target/)
  assert.match(generationMode, /onClick=\{\(\) => setGenerationMode\(m\.value\)\}/)
  for (const entry of [
    "{ value: 'image', label: 'Image'",
    "{ value: 'video', label: 'Video'",
    "{ value: 'audio', label: 'Audio'",
    "{ value: 'avatar', label: 'Edit'",
    "{ value: 'tools', label: 'Tools'",
  ]) assert.ok(generationMode.includes(entry), entry)

  assert.match(videoMode, /role="group" aria-label="Video input mode"/)
  assert.match(videoMode, /grid-cols-2[^"]*md:grid-cols-4/)
  assert.match(videoMode, /type="button"/)
  assert.match(videoMode, /aria-pressed=\{imageMode === m\.value\}/)
  assert.match(videoMode, /mobile-control-target/)
  assert.match(videoMode, /onClick=\{\(\) => setParam\('image_mode', m\.value\)\}/)
  for (const entry of [
    "{ value: 0, label: 'Frames' }",
    "{ value: 2, label: 'Multi-Shot' }",
    "{ value: 3, label: 'Extend' }",
    "{ value: 4, label: 'Blend' }",
  ]) assert.ok(videoMode.includes(entry), entry)
})

test('the shared target floor ends before the compact 768px layout', () => {
  const mobile = css.slice(css.indexOf('@media (max-width: 767px)'))
  assert.match(mobile, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.doesNotMatch(css.slice(0, css.indexOf('@media (max-width: 767px)')), /\.mobile-control-target\s*\{/)
})
