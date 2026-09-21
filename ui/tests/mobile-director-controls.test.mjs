import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const directorUrl = new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url)

function openingTagContaining(source, tagName, marker) {
  const markerIndex = source.indexOf(marker)
  assert.notEqual(markerIndex, -1, `found ${marker}`)
  const start = source.lastIndexOf(`<${tagName}`, markerIndex)
  assert.ok(start >= 0, `found ${tagName} containing ${marker}`)
  let quote = null
  let braces = 0
  for (let cursor = start + tagName.length + 1; cursor < source.length; cursor += 1) {
    const char = source[cursor]
    if (quote) {
      if (char === quote && source[cursor - 1] !== '\\') quote = null
      continue
    }
    if (char === '"' || char === "'" || char === '`') quote = char
    else if (char === '{') braces += 1
    else if (char === '}') braces = Math.max(0, braces - 1)
    else if (char === '>' && braces === 0) return source.slice(start, cursor + 1)
  }
  assert.fail(`closed ${tagName} containing ${marker}`)
}

function assertMobileButton(tag, name) {
  assert.match(tag, /type="button"/, `${name} cannot submit a surrounding form`)
  assert.match(tag, /mobile-control-target/, `${name} uses the shared <=767px target contract`)
  assert.match(tag, /touch-manipulation/, `${name} has direct touch behavior`)
  assert.match(tag, /focus-visible:outline-none/, `${name} replaces the default focus outline`)
  assert.match(tag, /focus-visible:ring-2/, `${name} shows a two-pixel focus ring`)
  assert.match(tag, /focus-visible:ring-accent-blue/, `${name} uses the visible accent focus color`)
}

test('Director first-run choices expose mobile targets and keyboard focus', async () => {
  const source = await readFile(directorUrl, 'utf8')
  const controls = [
    ['start over', openingTagContaining(source, 'button', 'onClick={reset}')],
    ['continue', openingTagContaining(source, 'button', 'onClick={() => void generateTrack()}')],
    ['music source', openingTagContaining(source, 'button', 'onClick={() => setMusicSource(opt)}')],
    ['skill', openingTagContaining(source, 'button', 'onClick={() => s.active && onSelect(s.id)}')],
    ['short film path', openingTagContaining(source, 'button', 'onClick={() => onSelect(p.id)}')],
  ]
  for (const [name, control] of controls) assertMobileButton(control, name)
  assert.match(controls[2][1], /aria-pressed=\{active\}/)
})

test('Director persistent toggles and composer expose usable labels, targets, and focus', async () => {
  const source = await readFile(directorUrl, 'utf8')
  const seamless = openingTagContaining(source, 'label', `title="Each clip's end frame uses the next clip's start image for smooth transitions"`)
  const auto = openingTagContaining(source, 'label', 'title="Skip all review steps and generate automatically"')
  for (const [name, label] of [['seamless', seamless], ['auto', auto]]) {
    assert.match(label, /mobile-control-target/, `${name} label is the full mobile hit target`)
    assert.match(label, /touch-manipulation/)
    assert.match(label, /focus-within:ring-2/)
    assert.match(label, /focus-within:ring-accent-blue/)
  }

  const composer = openingTagContaining(source, 'AutoResizeTextarea', 'placeholder={chatInputPlaceholder}')
  assert.match(composer, /aria-label="Director prompt"/)
  assert.match(composer, /mobile-control-target/)
  assert.match(composer, /focus-visible:ring-2/)
  assert.match(composer, /focus-visible:ring-accent-blue/)

  const send = openingTagContaining(source, 'button', 'onClick={handleChatSubmit}')
  assertMobileButton(send, 'send prompt')
  assert.match(send, /aria-label="Send Director prompt"/)
})

test('Director first-run disclosures expose mobile targets, focus, and expanded state', async () => {
  const source = await readFile(directorUrl, 'utf8')
  const additionalReferences = openingTagContaining(source, 'button', 'className="mobile-control-target flex w-full touch-manipulation items-center gap-1 text-[9px]')
  const advanced = openingTagContaining(source, 'button', 'aria-expanded={open}')
  const videoLoras = openingTagContaining(source, 'button', 'aria-expanded={videoOpen}')

  for (const [name, control, state] of [
    ['additional references', additionalReferences, 'expanded'],
    ['advanced', advanced, 'open'],
    ['video LoRAs', videoLoras, 'videoOpen'],
  ]) {
    assertMobileButton(control, name)
    assert.match(control, new RegExp(`aria-expanded=\\{${state}\\}`), `${name} announces disclosure state`)
  }

  for (const marker of ['aria-label="Director video model"', 'aria-label="Director visual style"']) {
    const select = openingTagContaining(source, 'select', marker)
    assert.match(select, /mobile-control-target/)
    assert.match(select, /focus-visible:ring-2/)
    assert.match(select, /focus-visible:ring-accent-blue/)
  }
})

test('Director review actions expose mobile geometry, names, and keyboard focus', async () => {
  const source = await readFile(directorUrl, 'utf8')
  assert.match(source, /grid min-w-0 grid-cols-\[minmax\(0,1fr\)_auto\] gap-1\.5/)

  const generate = openingTagContaining(source, 'button', 'onClick={directorGenerate}')
  assertMobileButton(generate, 'review generate')
  assert.match(generate, /min-w-0/, 'review generate can shrink without overflowing the viewport')

  const hold = openingTagContaining(source, 'button', 'onClick={() => { void queueCurrentDirectorPipeline() }}')
  assertMobileButton(hold, 'review hold')
  assert.match(hold, /aria-label="Hold this complete project in the persistent queue without starting it"/)
})
