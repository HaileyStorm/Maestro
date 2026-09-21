import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compile } from 'tailwindcss'

const selectorUrl = new URL('../src/components/SettingsDrawer/LoraSelector.tsx', import.meta.url)
const directorSelectorUrl = new URL('../src/components/SettingsDrawer/DirectorLoraSelector.tsx', import.meta.url)
const stylesUrl = new URL('../src/index.css', import.meta.url)

function openingTags(source, tagName) {
  const tags = []
  const needle = `<${tagName}`
  let cursor = 0
  while ((cursor = source.indexOf(needle, cursor)) !== -1) {
    const start = cursor
    let quote = null
    let braces = 0
    cursor += needle.length
    for (; cursor < source.length; cursor += 1) {
      const char = source[cursor]
      if (quote) {
        if (char === quote && source[cursor - 1] !== '\\') quote = null
        continue
      }
      if (char === '"' || char === "'" || char === '`') {
        quote = char
      } else if (char === '{') {
        braces += 1
      } else if (char === '}') {
        braces = Math.max(0, braces - 1)
      } else if (char === '>' && braces === 0) {
        tags.push(source.slice(start, cursor + 1))
        cursor += 1
        break
      }
    }
  }
  return tags
}

function buttonOpeningTag(source, marker) {
  const markerIndex = source.indexOf(marker)
  assert.notEqual(markerIndex, -1, `found ${marker}`)
  const start = source.lastIndexOf('<button', markerIndex)
  const closingLine = /\n\s*>/.exec(source.slice(markerIndex))
  assert.ok(start >= 0 && closingLine, `found button containing ${marker}`)
  const end = markerIndex + closingLine.index + closingLine[0].length
  return source.slice(start, end)
}

test('LoRA header actions retain behavior while exposing mobile targets and focus', async () => {
  const source = await readFile(selectorUrl, 'utf8')
  const sort = buttonOpeningTag(source, "onClick={() => onChange(newest ? 'name' : 'newest')}")
  const check = buttonOpeningTag(source, 'onClick={handleCheckUpdates}')
  const browse = buttonOpeningTag(source, 'onClick={() => openBrowser(true, modelType)}')

  for (const [name, button] of [['sort', sort], ['check', check], ['browse', browse]]) {
    assert.match(button, /type="button"/, `${name} cannot submit a surrounding form`)
    assert.match(button, /min-h-11/)
    assert.match(button, /min-w-11/)
    assert.match(button, /focus-visible:outline-none/)
    assert.match(button, /focus-visible:ring-2/)
    assert.match(button, /focus-visible:ring-accent-blue/)
    assert.match(button, /md:min-h-0/)
    assert.match(button, /md:min-w-0/)
  }

  assert.match(sort, /aria-label=\{newest \? 'Sort LoRAs by name' : 'Sort LoRAs by newest release'\}/)
  assert.match(check, /disabled=\{checking \|\| !modelType\}/)
  assert.match(check, /aria-label=\{checkUpdatesLabel\}/)
  assert.match(source, /\? `Check CivitAI updates, \$\{updatableCount\} update\$\{updatableCount === 1 \? '' : 's'\} available`/)
  assert.match(browse, /aria-label="Browse CivitAI"/)

  assert.match(source, /className="mb-1\.5 flex flex-wrap items-center justify-between gap-1"/)
  assert.match(source, /className="ml-auto flex flex-wrap items-center justify-end gap-1 md:gap-2"/)
  assert.match(source, /h3AdaptivePairActive/)
  assert.match(source, /'Text & frames',\s*'FL2VA adapters'/)
  assert.match(source, /'References',\s*'Ref2VA adapters'/)
  assert.match(source, /h3LorasForArchitecture/)
  assert.match(source, /Saved LoRA settings need repair/)
  assert.match(source, /Clear saved \{title\} LoRAs/)
  assert.match(source, /toggleH3ArchitectureLora\('fl2va'/)
  assert.match(source, /toggleH3ArchitectureLora\('ref2va'/)
  assert.match(source, /applies only to that model's shots/)
  assert.match(source, /aria-label=\{`\$\{displayName\(filename\)\} \$\{architecture/)
})

test('mobile target utilities compile to 44px and compact only from 768px', async () => {
  const compiler = await compile('@theme { --spacing: 0.25rem; --breakpoint-md: 48rem; } @tailwind utilities;')
  const css = compiler.build(['min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-w-0', 'flex-wrap'])

  assert.match(css, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /flex-wrap: wrap/)
  assert.match(css, /@media \(width >= 48rem\)/)
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 0\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 0\)/)
})

test('Director LoRA actions and fields share the mobile target and keyboard focus contract', async () => {
  const [source, sharedSource, styles] = await Promise.all([
    readFile(directorSelectorUrl, 'utf8'),
    readFile(selectorUrl, 'utf8'),
    readFile(stylesUrl, 'utf8'),
  ])
  const buttons = openingTags(source, 'button')
  const fields = [
    ...openingTags(source, 'input').filter(tag => !/type="range"/.test(tag)),
    ...openingTags(source, 'select'),
  ]

  assert.ok(buttons.length >= 10, 'covers every Director LoRA action family')
  assert.ok(fields.length >= 7, 'covers search, parameter, multiplier, and numeric weight fields')
  for (const [kind, controls] of [['button', buttons], ['field', fields]]) {
    for (const control of controls) {
      if (kind === 'button') assert.match(control, /type="button"/, 'button cannot submit a surrounding form')
      assert.match(control, /mobile-control-target/, `${kind} uses the shared <=767px target contract`)
      assert.match(control, /focus-visible:outline-none/, `${kind} removes the default outline only with a replacement`)
      assert.match(control, /focus-visible:ring-2/, `${kind} shows a two-pixel keyboard focus ring`)
      assert.match(control, /focus-visible:ring-accent-blue/, `${kind} uses the visible accent focus color`)
    }
  }

  assert.match(source, /aria-label="Search Director LoRAs"/)
  assert.match(source, /aria-label=\{`Browse \$\{directorRoleLabel\(role\)\} LoRAs`\}/)
  assert.match(source, /aria-label="Clear all Director LoRAs"/)
  assert.match(source, /aria-label=\{`Generate guide for \$\{filename\}`\}/)
  assert.match(source, /aria-label=\{`Remove \$\{filename\}`\}/)
  const guideButton = openingTags(sharedSource, 'button').find(tag => tag.includes('aria-label="Show LoRA guide"'))
  assert.ok(guideButton, 'shared LoRA guide control is discoverable to assistive technology')
  assert.match(guideButton, /type="button"/)
  assert.match(guideButton, /mobile-control-target/)
  assert.match(guideButton, /focus-visible:ring-2/)
  assert.match(guideButton, /focus-visible:ring-accent-blue/)
  assert.match(guideButton, /aria-expanded=\{show\}/)
  for (const pickerSource of [source, sharedSource]) {
    assert.doesNotMatch(pickerSource, /<span onClick=\{e => e\.stopPropagation\(\)\}>\s*<LoraGuideTooltip/)
    assert.match(pickerSource, /<\/button>\s*\{guideTexts\[filename\] && <LoraGuideTooltip/)
  }
  assert.match(styles, /@media \(max-width: 767px\)[\s\S]*?\.mobile-control-target\s*\{\s*min-width: 44px;\s*min-height: 44px;/)
  assert.doesNotMatch(styles, /@media \(min-width: 768px\)[\s\S]*?\.mobile-control-target/)
})
