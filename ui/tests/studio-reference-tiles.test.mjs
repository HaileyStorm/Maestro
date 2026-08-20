import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, test } from 'node:test'
import { fileURLToPath } from 'node:url'

const inputs = readFileSync(new URL('../src/components/Sidebar/InputsPanel.tsx', import.meta.url), 'utf8')
const applyLib = readFileSync(new URL('../src/lib/studioSemanticReferences.ts', import.meta.url), 'utf8')
const library = readFileSync(new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url), 'utf8')
const attachmentLib = readFileSync(new URL('../src/lib/generateAttachmentOptions.ts', import.meta.url), 'utf8')

test('Generate keeps first/last frames separate from every reference kind', () => {
  assert.match(inputs, /label=\{frameUploading \? 'Uploading…' : 'First \/ last frame'\}/)
  assert.match(inputs, /label="Reference image"/)
  assert.match(inputs, /label="Reference video"/)
  assert.match(inputs, /label="Reference audio"|label=\{audioUploadTarget === 'semantic-audio' \? 'Uploading…' : 'Reference audio'\}/)
  assert.match(inputs, /label="Project reference"/)
  assert.match(inputs, /orderGenerateAttachmentOptions/)
  assert.match(inputs, /filterProjectReferenceChoices/)
  assert.match(inputs, /GENERATE_ATTACHMENT_DISABLED_TILE_CLASS/)
  assert.match(inputs, /Use a Continuum project reference — images, videos, audio, or packs/)
  assert.match(inputs, /classifyStudioReferenceMedia/)
})

test('project references classify video and audio instead of collapsing to images', () => {
  assert.match(applyLib, /export type StudioReferenceKind = 'image' \| 'video' \| 'audio'/)
  assert.match(library, /kind === 'video'/)
  assert.match(library, /kind === 'audio'/)
  assert.match(library, /uploadAudio/)
  assert.match(library, /nextSemanticSlotPaths/)
})

void describe('studio reference helper', () => {
  test('source stays media-kind explicit', () => {
    assert.match(applyLib, /function classifyStudioReferenceMedia/)
    assert.match(applyLib, /function nextSemanticSlotPaths/)
  })
})

test('Generate attachment helper keeps honest labels and disable-to-end order', () => {
  assert.match(attachmentLib, /First \/ last frame/)
  assert.match(attachmentLib, /Reference image/)
  assert.match(attachmentLib, /Project reference/)
  assert.match(attachmentLib, /function orderGenerateAttachmentOptions/)
  assert.match(attachmentLib, /function filterProjectReferenceChoices/)
})

void fileURLToPath
