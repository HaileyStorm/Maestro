import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  generateProjectAssetReferences,
  getProjectAssetComponentOutputs,
  getProjectReferenceRetrySettings,
  isProjectAssetOperationCurrent,
  lockProjectAssetVariantOperation,
  projectAssetVariantOperationKey,
  selectProjectAssetApplyOutput,
} from '../src/api/client.ts'

const componentUrl = new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url)

function output(id, role) {
  return {
    id,
    filename: `${id}.png`,
    relative_path: `${id}.png`,
    media_type: 'image/png',
    label: id,
    metadata: role ? { reference_sheet: { role } } : {},
  }
}

function variant(outputs, variantType = 'reference_sheet') {
  return {
    id: 'variant',
    variant_type: variantType,
    label: 'Candidate',
    status: 'kept',
    outputs,
    metadata: {},
  }
}

test('role-aware selection applies one sheet and exposes panels only for display', () => {
  const panel = output('front', 'identity_front')
  const sheet = output('sheet', 'sheet')
  const palette = output('palette', 'color_palette')
  const current = variant([panel, sheet, palette])

  assert.equal(selectProjectAssetApplyOutput(current), sheet)
  assert.deepEqual(getProjectAssetComponentOutputs(current), [panel, palette])
  assert.equal(new Set(getProjectAssetComponentOutputs(current)).size, 2)
})

test('legacy and non-sheet variants preserve first-output fallback', () => {
  const first = output('legacy-first')
  const second = output('legacy-second')

  assert.equal(selectProjectAssetApplyOutput(variant([first, second])), first)
  assert.deepEqual(getProjectAssetComponentOutputs(variant([first, second])), [second])
  assert.equal(selectProjectAssetApplyOutput(variant([first, second], 'reference')), first)
  assert.deepEqual(getProjectAssetComponentOutputs(variant([first, second], 'reference')), [])
})

test('retry settings preserve recorded source policy and locks are asset-scoped', () => {
  const sheet = output('sheet', 'sheet')
  sheet.metadata.private = true
  sheet.metadata.explicit = true
  const source = variant([sheet])
  source.metadata.reference_sheet = {
    mode: 'hybrid',
    model: 'source-model',
    review_status: 'review_unavailable',
  }
  assert.deepEqual(getProjectReferenceRetrySettings(source, {
    mode: 'draft',
    model_type: 'current-model',
    private_output: false,
    explicit_output: false,
    review: false,
  }), {
    mode: 'hybrid',
    model_type: 'source-model',
    private_output: true,
    explicit_output: true,
    review: false,
  })

  const locks = new Set()
  const first = lockProjectAssetVariantOperation(locks, 'project', 'asset-a', 'same-variant')
  assert.equal(first, projectAssetVariantOperationKey('project', 'asset-a', 'same-variant'))
  assert.equal(lockProjectAssetVariantOperation(locks, 'project', 'asset-a', 'same-variant'), null)
  assert.notEqual(lockProjectAssetVariantOperation(locks, 'project', 'asset-b', 'same-variant'), null)
})

test('project operation adoption requires both project and lifecycle epoch', () => {
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'one', 3), true)
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'two', 3), false)
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'one', 4), false)
})

test('generation helper preserves fresh modes and retry/edit lineage payloads exactly', async t => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response(JSON.stringify({ job_id: `job-${requests.length}`, asset: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  for (const mode of ['production', 'hybrid', 'draft']) {
    await generateProjectAssetReferences('My Project', {
      name: 'A',
      asset_type: 'character',
      description: '',
      mode,
      candidate_count: 2,
      columns: 3,
      palette_swatches: 7,
      review: true,
    })
  }
  await generateProjectAssetReferences('My Project', {
    asset_id: 'asset-1',
    parent_variant_id: 'kept-source',
    edit_instruction: 'change only the coat',
    mode: 'hybrid',
    candidate_count: 1,
  })

  assert.deepEqual(requests.map(request => request.body.mode), ['production', 'hybrid', 'draft', 'hybrid'])
  assert.equal(requests[0].url, '/api/v1/projects/My%20Project/assets/generate')
  assert.deepEqual(requests[3].body, {
    asset_id: 'asset-1',
    parent_variant_id: 'kept-source',
    edit_instruction: 'change only the coat',
    mode: 'hybrid',
    candidate_count: 1,
  })
})

test('component source guards lifecycle, accessibility, mobile flow, and sheet-only apply', async () => {
  const source = await readFile(componentUrl, 'utf8')

  for (const copy of [
    'Generates independent panels',
    'Generates one identity anchor',
    'Generates the complete sheet in one shot',
    'palette is an embedded region',
    'Local VLM review with at most one bounded panel repair',
  ]) assert.match(source, new RegExp(copy))

  assert.match(source, /asset_id: asset\.id/)
  assert.match(source, /parent_variant_id: variant\.id/)
  assert.match(source, /edit_instruction: instruction\?\.trim\(\) \|\| undefined/)
  assert.match(source, /lockProjectAssetVariantOperation\(/)
  assert.match(source, /projectAssetVariantOperationKey\(project, asset\.id, variant\.id\)/)
  assert.match(source, /isProjectAssetOperationCurrent\(submittedProject, epoch, currentProject\.current, projectEpoch\.current\)/)
  assert.match(source, /setPendingSheetActions\(\{\}\)/)
  assert.match(source, /await reconnectJobs\(\)/)
  assert.doesNotMatch(source, /setInterval/)

  assert.match(source, /role="dialog" aria-modal="true"/)
  assert.match(source, /aria-label="Close project references"/)
  assert.match(source, /onKeyDown=\{handleDialogKeyDown\}/)
  assert.match(source, /event\.key === 'Escape'/)
  assert.match(source, /event\.key !== 'Tab'/)
  assert.match(source, /element\.getClientRects\(\)\.length > 0/)
  assert.match(source, /aria-pressed=\{assetType === option\.value\}/)
  assert.match(source, /aria-label="Reference name"/)
  assert.match(source, /aria-label=\{`Import media for \$\{asset\.name\}`\}/)
  assert.doesNotMatch(source, /accept="image\/\*,video\/\*"\s+className="hidden"/)
  assert.match(source, /aria-expanded=\{editing\}/)
  assert.match(source, /htmlFor=\{`reference-sheet-edit-instruction-/)
  assert.match(source, /overflow-y-auto md:grid-cols/)
  assert.match(source, /md:overflow-y-auto/)

  assert.match(source, /const applyOutput = selectProjectAssetApplyOutput\(variant\)/)
  assert.match(source, /void applyReference\(asset, variant\)/)
  assert.doesNotMatch(source, /applyReference\(asset, variant, /)
  assert.match(source, /getProjectAssetComponentOutputs\(variant\)/)
  assert.match(source, /getProjectReferenceRetrySettings\(variant/)
  assert.match(source, /mode: sourceSettings\.mode/)
  assert.match(source, /private_output: sourceSettings\.private_output/)
  assert.match(source, /provenance: 'imported'/)
  assert.match(source, /disabled=\{submitting \|\| !name\.trim\(\)\}/)
  assert.doesNotMatch(source, /!name\.trim\(\) \|\| !description\.trim\(\)/)
  assert.doesNotMatch(source, /localStorage/)
})
