import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { compile } from 'tailwindcss'

const selectorUrl = new URL('../src/components/SettingsDrawer/LoraSelector.tsx', import.meta.url)

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
