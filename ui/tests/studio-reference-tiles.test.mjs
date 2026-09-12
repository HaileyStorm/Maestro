import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { transform } from 'esbuild'

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


test('retained incompatible H3 inputs offer Automatic without removing attachments', async () => {
  const start = inputs.indexOf('          {!h3AdaptiveConditioning && (')
  const end = inputs.indexOf('          {!h3ExecutionBlocked && <div', start)
  assert.ok(start >= 0 && end > start)
  const result = await transform(`
    const Fragment = 'fragment'
    function node(type, props, ...children) { return { type, props: props || {}, children } }
    export function render({h3AdaptiveConditioning, dedicatedRef2VAMode, h3HasSemanticInputs, hasStart, hasEnd, setParam}) {
      return <>${inputs.slice(start, end)}</>
    }
  `, { loader: 'tsx', format: 'esm', jsxFactory: 'node', jsxFragment: 'Fragment' })
  const { render } = await import(`data:text/javascript;base64,${Buffer.from(result.code).toString('base64')}`)
  const walk = value => value && typeof value === 'object'
    ? [value, ...value.children.flatMap(walk)] : []
  for (const automatic of [false, true]) {
    for (const referenceMode of [false, true]) {
      for (const semantic of [false, true]) {
        for (const anchors of [[false, false], [true, false], [false, true]]) {
          const changes = []
          const tree = render({
            h3AdaptiveConditioning: automatic,
            dedicatedRef2VAMode: referenceMode,
            h3HasSemanticInputs: semantic,
            hasStart: anchors[0], hasEnd: anchors[1],
            setParam: (...args) => changes.push(args),
          })
          const buttons = walk(tree).filter(value => value.type === 'button')
          const needsRepair = !automatic && (referenceMode ? anchors.some(Boolean) : semantic)
          assert.equal(buttons.length, Number(needsRepair))
          assert.deepEqual(changes, [], 'rendering does not alter inputs')
          if (needsRepair) {
            assert.equal(buttons[0].children.join('').trim(), 'Use Automatic')
            buttons[0].props.onClick()
            assert.deepEqual(changes, [['h3_adaptive_conditioning', true]], 'repair changes only the matching mode')
          }
        }
      }
    }
  }
  assert.doesNotMatch(inputs, /Turn Automatic back on or remove/)
})
