import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import { compile } from 'tailwindcss'
import { resolveSidebarNavigation } from '../src/lib/sidebarNavigation.ts'

import {
  addProjectAssetVariant,
  createProjectAsset,
  createProjectReferenceRequestId,
  deleteProjectAssetVariant,
  directorV2Plan,
  fetchProjectAssets,
  fetchProjectReferenceAuthoring,
  fetchProjectReferenceCapabilities,
  generateProjectAssetReferences,
  getDirectorProjectReferenceKind,
  getEffectiveProjectReferenceRepairAttempts,
  getProjectAssetApplyOutputs,
  getProjectAssetComponentOutputs,
  getProjectReferenceEditorModels,
  getProjectReferenceGenerationModels,
  getProjectReferencePreferredGenerationModel,
  getProjectReferenceQueueBlockers,
  getProjectReferenceVisibilityHints,
  getLoraParameterDefaults,
  getLoraParameterValue,
  hasProjectReferenceLoraParameterSummary,
  getProjectReferenceRepairCopy,
  getProjectReferenceReviewerAction,
  getProjectReferenceReviewerSetupCopy,
  getProjectReferenceRetrySettings,
  isProjectReferenceReviewMandatory,
  isProjectReferenceCharacterReplayReady,
  isProjectReferenceStyleReplayReady,
  isProjectReferenceReviewerEligible,
  isProjectAssetOperationCurrent,
  lockProjectAssetVariantOperation,
  loadLlm,
  loraParameterSchemasConflict,
  normalizeProjectReferenceAssetType,
  normalizeProjectReferenceAnchorPrivacy,
  ProjectAssetRequestError,
  PROJECT_REFERENCE_REQUEST_ID_PATTERN,
  projectAssetOutputNeedsInitialBlur,
  projectAssetVariantOperationKey,
  projectReferenceJobQualitySummary,
  projectReferenceQualityPresentation,
  projectReferenceRequestIdFromRandomBytes,
  projectReferenceRetryNeedsPrivateAuthoring,
  projectReferenceSafeErrorMessage,
  resolveProjectReferenceRetryReview,
  selectProjectReferenceModel,
  serializeProjectReferenceCharacterProfile,
  selectProjectAssetApplyOutput,
  setProjectAssetVariantStatus,
  validateLoraParameterValues,
  PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER,
  PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER,
} from '../src/api/client.ts'

const componentUrl = new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url)
const sidebarUrl = new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url)
const directorUrl = new URL('../src/components/Sidebar/DirectorChat.tsx', import.meta.url)
const advancedSettingsUrl = new URL('../src/components/Sidebar/AdvancedSettings.tsx', import.meta.url)
const modelSelectorUrl = new URL('../src/components/Sidebar/ModelSelector.tsx', import.meta.url)
const blenderUrl = new URL('../src/components/Sidebar/BlenderSceneTool.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const referenceQueueUrl = new URL('../src/lib/referenceQueue.ts', import.meta.url)
const mainViewNavigationUrl = new URL('../src/lib/mainViewNavigation.ts', import.meta.url)
const manualInstallationUrl = new URL('../src/lib/manualInstallation.ts', import.meta.url)
const clientUrl = new URL('../src/api/client.ts', import.meta.url)
const typesUrl = new URL('../src/types/index.ts', import.meta.url)

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

test('public outputs can request an initial session-only blur without becoming private', () => {
  const publicBlurred = output('public-blurred')
  publicBlurred.metadata.initial_blur = true
  publicBlurred.metadata.private = false
  const publicRevealed = output('public-revealed')
  publicRevealed.metadata.initial_blur = false
  const privateRevealRequested = output('private-reveal-requested')
  privateRevealRequested.metadata.private = true
  privateRevealRequested.metadata.initial_blur = false

  assert.equal(projectAssetOutputNeedsInitialBlur(publicBlurred), true)
  assert.equal(projectAssetOutputNeedsInitialBlur(publicRevealed), false)
  assert.equal(projectAssetOutputNeedsInitialBlur(privateRevealRequested), true)
})

test('v2 reference packs preserve authored sheet order and canonicalize legacy type aliases', () => {
  const third = output('third')
  third.metadata.reference_pack = { schema_version: 2, planner_version: 'reference-pack-v2', role: 'details', index: 3 }
  const first = output('first')
  first.metadata.reference_pack = { schema_version: 2, planner_version: 'reference-pack-v2', role: 'identity', index: 1 }
  const second = output('second')
  second.metadata.reference_pack = { schema_version: 2, planner_version: 'reference-pack-v2', role: 'wardrobe', index: 2 }
  const pack = variant([third, first, second], 'reference_pack')

  assert.deepEqual(getProjectAssetApplyOutputs(pack), [first, second, third])
  assert.equal(selectProjectAssetApplyOutput(pack), first)
  assert.deepEqual(getProjectAssetComponentOutputs(pack), [])
  assert.equal(normalizeProjectReferenceAssetType('setting'), 'location')
  assert.equal(normalizeProjectReferenceAssetType('item'), 'prop')
  assert.equal(normalizeProjectReferenceAssetType('machine'), 'vehicle')
  assert.equal(normalizeProjectReferenceAssetType('accessory'), 'wardrobe')
  assert.equal(normalizeProjectReferenceAssetType('style'), 'world')
  assert.equal(normalizeProjectReferenceAssetType('unknown'), null)
  for (const value of ['private_blurred', 'private_visible', 'project_blurred', 'project_visible']) {
    assert.equal(normalizeProjectReferenceAnchorPrivacy(value, 2), value)
  }
  assert.equal(normalizeProjectReferenceAnchorPrivacy('standard', 1), 'project_visible')
  assert.equal(normalizeProjectReferenceAnchorPrivacy('standard', undefined), 'project_visible')
  assert.equal(normalizeProjectReferenceAnchorPrivacy('standard', 2), null)
})

test('v2 retry replays public plan and exact resolved local model selectors', () => {
  const source = variant([output('identity')], 'reference_pack')
  source.metadata.reference_pack = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    mode: 'production',
    intent: 'exact_spec',
    reference_type: 'character',
    depth: 'custom',
    sheet_count: 4,
    preset: 'anatomy',
    anchor_basis: 'anatomy',
    anchor_privacy: 'private_visible',
    private_output: true,
    generation_model: 'flux2_dev',
    editor_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step',
    max_repair_attempts: 5,
    explicit_output: true,
    content_capability: 'unrestricted_local',
    initial_blur: true,
    intelligence_policy: 'uncensored_auto',
    additional_loras: {
      applied: [{ id: 'create.safetensors', weight: 0.8, requested_scope: 'generation', resolved_scope: ['generation'], roles: ['canonical_anchor'] }],
      skipped: [{ id: 'auto.safetensors', weight: 1.1, requested_scope: 'auto', reason: 'editor_incompatible' }],
    },
    operation_routing: {
      requested_capability: 'unrestricted_local',
      operations: {
        generation: { status: 'applied', requested_model: 'flux2_dev', resolved_model: 'flux2_dev_unrestricted', schedule: null, recipe_id: 'generation-v1', verification_status: 'verified' },
        edit: { status: 'skipped', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null, reason: 'no_verified_compatible_recipe' },
        repair: { status: 'standard', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null },
        callout: { status: 'standard', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null },
      },
    },
    planning: { requested_model: 'auto', resolved_model: 'local-planner', resolved_provider: 'local' },
    review: { requested_model: 'auto_local', resolved_model: 'local-vlm', resolved_provider: 'local', status: 'pass' },
    authored_settings: {
      seal: 'authored-seal',
      type_fields: [{ field: 'poses', items: [
        { id: 'views:front', custom: false, group: 'views' },
        { id: 'custom:abcdefghijkl', custom: true, group: 'expressions' },
      ] }],
      detail_callouts: [{
        custom_id: 'builtin:face', kind: 'face', requested_operation: 'enhance',
        source_role: 'canonical_identity', target_role: 'detail_callout:builtin:face',
        label_digest: 'digest',
      }],
    },
  }
  const settings = getProjectReferenceRetrySettings(source, {
    mode: 'draft', model_type: 'other', editor_model_type: 'other-editor',
    private_output: false, explicit_output: false, review: true, max_repair_attempts: 1,
    asset_type: 'character', intent: 'generic', depth: 'standard', preset: 'identity',
    anchor_basis: 'primary_outfit', type_fields: { poses: [
      { id: 'views:front', label: 'Front', custom: false, group: 'views' },
      { id: 'custom:abcdefghijkl', label: 'Wry half-smile', custom: true, group: 'expressions' },
    ] },
    detail_callouts: [{ custom_id: 'builtin:face', label: 'Face', kind: 'face', operation: 'crop', source_role: 'turnaround' }],
    authored_settings_seal: 'authored-seal',
    managed_layout_assist: 'off', planning_model: 'deterministic', review_model: 'off',
  }, {
    reference_types: [{
      id: 'character',
      type_fields: [{ id: 'poses', groups: [{ id: 'views', label: 'Views', options: [{ id: 'views:front', label: 'Front' }] }] }],
      detail_kinds: [{ id: 'face', label: 'Face' }],
    }],
  })
  assert.equal(settings.schema_version, 2)
  assert.equal(settings.intent, 'exact_spec')
  assert.equal(settings.depth, 'custom')
  assert.equal(settings.sheet_count, 4)
  assert.equal(settings.preset, 'anatomy')
  assert.equal(settings.anchor_basis, 'anatomy')
  assert.equal(settings.private_output, true)
  assert.equal(settings.model_type, 'flux2_dev')
  assert.equal(settings.editor_model_type, 'qwen_image_edit_2511_20B_fp8_lightning_8step')
  assert.equal(settings.planning_model, 'local-planner')
  assert.equal(settings.planning_provider, 'local')
  assert.equal(settings.review_model, 'local-vlm')
  assert.equal(settings.review_provider, 'local')
  assert.equal(settings.content_capability, 'unrestricted_local')
  assert.equal(settings.initial_blur, true)
  assert.equal(settings.intelligence_policy, 'uncensored_auto')
  assert.deepEqual(settings.additional_loras, [
    { id: 'create.safetensors', multiplier: 0.8, scope: 'generation' },
    { id: 'auto.safetensors', multiplier: 1.1, scope: 'auto' },
  ])
  assert.deepEqual(settings.type_fields, { poses: [
    { id: 'views:front', label: 'Front', custom: false, group: 'views' },
    { id: 'custom:abcdefghijkl', label: 'Wry half-smile', custom: true, group: 'expressions' },
  ] })
  assert.deepEqual(settings.detail_callouts, [{
    custom_id: 'builtin:face', label: 'Face', kind: 'face',
    operation: 'enhance', source_role: 'canonical_identity',
  }])
  const mismatchedSnapshot = getProjectReferenceRetrySettings(source, {
    ...settings,
    authored_settings_seal: 'different-seal',
    type_fields: { poses: [{
      id: 'custom:abcdefghijkl', label: 'Edited after generation', custom: true, group: 'expressions',
    }] },
  }, {
    reference_types: [{
      id: 'character',
      type_fields: [{ id: 'poses', groups: [{ id: 'views', label: 'Views', options: [{ id: 'views:front', label: 'Front' }] }] }],
      detail_kinds: [{ id: 'face', label: 'Face' }],
    }],
  })
  assert.equal(mismatchedSnapshot.type_fields, undefined)
  assert.equal(mismatchedSnapshot.detail_callouts, undefined)
  source.metadata.reference_pack.private_output = false
  assert.equal(getProjectReferenceRetrySettings(source, {
    ...settings,
    private_output: true,
  }).private_output, false)
  source.metadata.reference_pack.private_output = true

  const reviewOffSource = variant([output('review-off')], 'reference_pack')
  reviewOffSource.metadata.reference_pack = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    mode: 'production',
    reference_type: 'character',
    depth: 'standard',
    preset: 'identity',
    anchor_basis: 'primary_outfit',
    generation_model: 'flux2_dev',
    editor_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step',
    max_repair_attempts: 5,
    review: { requested_model: 'off', resolved_model: 'off', resolved_provider: null },
    additional_loras: { applied: [], skipped: [] },
  }
  const reviewOffSettings = getProjectReferenceRetrySettings(reviewOffSource, {
    mode: 'production', model_type: 'current', editor_model_type: 'current-editor',
    private_output: false, explicit_output: false, review: true, max_repair_attempts: 5,
    additional_loras: [{ id: 'current-form.safetensors', multiplier: 1, scope: 'auto' }],
  })
  assert.equal(reviewOffSettings.review_model, 'off')
  assert.equal(reviewOffSettings.review, false)
  assert.equal(reviewOffSettings.max_repair_attempts, 0)
  assert.deepEqual(reviewOffSettings.additional_loras, [])
})

test('retry settings preserve recorded source policy and locks are asset-scoped', () => {
  const sheet = output('sheet', 'sheet')
  sheet.metadata.private = true
  sheet.metadata.explicit = true
  const source = variant([sheet])
  source.metadata.reference_sheet = {
    mode: 'hybrid',
    model: 'legacy-source-model',
    generation_model: 'source-model',
    editor_model: 'source-editor',
    review_status: 'review_unavailable',
    max_repair_attempts: 5,
  }
  assert.deepEqual(getProjectReferenceRetrySettings(source, {
    mode: 'draft',
    model_type: 'current-model',
    editor_model_type: 'current-editor',
    private_output: false,
    explicit_output: false,
    review: true,
    max_repair_attempts: 1,
  }), {
    mode: 'hybrid',
    model_type: 'source-model',
    editor_model_type: 'source-editor',
    private_output: true,
    explicit_output: true,
    review: true,
    max_repair_attempts: 5,
  })

  source.metadata.reference_sheet = { mode: 'production', model: 'legacy-source-model' }
  assert.equal(getProjectReferenceRetrySettings(source, {
    mode: 'draft',
    model_type: 'current-model',
    editor_model_type: 'current-editor',
    private_output: false,
    explicit_output: false,
    review: false,
    max_repair_attempts: 4,
  }).model_type, 'legacy-source-model')

  const locks = new Set()
  const first = lockProjectAssetVariantOperation(locks, 'project', 'asset-a', 'same-variant')
  assert.equal(first, projectAssetVariantOperationKey('project', 'asset-a', 'same-variant'))
  assert.equal(lockProjectAssetVariantOperation(locks, 'project', 'asset-a', 'same-variant'), null)
  assert.notEqual(lockProjectAssetVariantOperation(locks, 'project', 'asset-b', 'same-variant'), null)
})

test('v2 retry never fabricates unavailable private custom labels after refresh', () => {
  const source = variant([output('identity')], 'reference_pack')
  source.metadata.reference_pack = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    mode: 'production',
    reference_type: 'character',
    depth: 'standard',
    preset: 'identity',
    anchor_basis: 'primary_outfit',
    authored_settings: {
      seal: 'sealed-private-authoring',
      style_present: true,
      style_commitment: 'a'.repeat(64),
      type_fields: [{ field: 'poses', items: [{ id: 'custom:abcdefghijkl', custom: true, group: 'expressions' }] }],
      detail_callouts: [{
        custom_id: 'custom:mnopqrstuvwx', kind: 'custom', requested_operation: 'crop',
        source_role: 'canonical_identity', target_role: 'detail_callout:custom:mnopqrstuvwx',
        label_digest: 'private-digest',
      }],
    },
  }
  assert.equal(projectReferenceRetryNeedsPrivateAuthoring(source), true)
  assert.equal(isProjectReferenceStyleReplayReady(
    source.metadata.reference_pack.authored_settings, 'hand-painted stop motion',
  ), true)
  assert.equal(isProjectReferenceStyleReplayReady(
    source.metadata.reference_pack.authored_settings, '',
  ), false)
  assert.equal(isProjectReferenceStyleReplayReady({ style_present: false }, undefined), false)
  assert.equal(isProjectReferenceStyleReplayReady({ style_commitment: 'a'.repeat(64) }, undefined), false)
  assert.equal(isProjectReferenceStyleReplayReady({
    style_present: false,
    style_commitment: 'a'.repeat(64),
  }, undefined), true)
  assert.equal(isProjectReferenceStyleReplayReady(undefined, undefined), true)
  const settings = getProjectReferenceRetrySettings(source, {
    mode: 'production', model_type: 'flux2_dev', editor_model_type: 'qwen-edit',
    private_output: false, explicit_output: false, review: false, max_repair_attempts: 0,
    asset_type: 'character', type_fields: {}, detail_callouts: [],
  }, {
    reference_types: [{ id: 'character', type_fields: [], detail_kinds: [] }],
  })
  assert.equal('type_fields' in settings, false)
  assert.equal('detail_callouts' in settings, false)
  assert.equal('style' in settings, false)

  const exactSnapshot = getProjectReferenceRetrySettings(source, {
    mode: 'production', model_type: 'flux2_dev', editor_model_type: 'qwen-edit',
    private_output: false, explicit_output: false, review: false, max_repair_attempts: 0,
    asset_type: 'character', authored_settings_seal: 'sealed-private-authoring',
    style: 'hand-painted stop motion',
    type_fields: { poses: [{
      id: 'custom:abcdefghijkl', label: 'Source expression', custom: true, group: 'expressions',
    }] },
    detail_callouts: [{
      custom_id: 'custom:mnopqrstuvwx', label: 'Source ring engraving', kind: 'custom',
      operation: 'enhance', source_role: 'turnaround',
    }],
  }, {
    reference_types: [{ id: 'character', type_fields: [], detail_kinds: [] }],
  })
  assert.deepEqual(exactSnapshot.type_fields, { poses: [{
    id: 'custom:abcdefghijkl', label: 'Source expression', custom: true, group: 'expressions',
  }] })
  assert.deepEqual(exactSnapshot.detail_callouts, [{
    custom_id: 'custom:mnopqrstuvwx', label: 'Source ring engraving', kind: 'custom',
    operation: 'crop', source_role: 'canonical_identity',
  }])
  assert.equal(exactSnapshot.style, 'hand-painted stop motion')

  source.metadata.reference_pack.authored_settings = {
    seal: 'built-in-only',
    type_fields: [{ field: 'poses', items: [{ id: 'views:front', custom: false, group: 'views' }] }],
    detail_callouts: [{
      custom_id: 'builtin:face', kind: 'face', requested_operation: 'crop',
      source_role: 'canonical_identity', target_role: 'detail_callout:builtin:face',
      label_digest: 'public-digest',
    }],
  }
  assert.equal(projectReferenceRetryNeedsPrivateAuthoring(source), false)
})

test('Character Retry and Edit replay semantic profile data without server-managed identities', () => {
  const source = variant([output('identity')], 'reference_pack')
  const publicProfile = {
    schema_version: 1,
    gender: { present: true, commitment: '1'.repeat(64) },
    age: { present: true, commitment: '2'.repeat(64) },
    explicit_anatomy: { count: 2, commitments: ['3'.repeat(64), '4'.repeat(64)] },
  }
  const publicManaged = {
    schema_version: 1,
    active_count: 2,
    tombstone_count: 1,
    rename_count: 1,
    commitments: ['5'.repeat(64), '6'.repeat(64), '7'.repeat(64)],
  }
  source.metadata.reference_pack = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    reference_type: 'character',
    mode: 'production',
    explicit_output: true,
    explicit_convenience: true,
    authored_settings: {
      seal: 'profile-seal',
      type_fields: [],
      detail_callouts: [
        { managed: true, requested_operation: 'auto' },
        { managed: true, requested_operation: 'enhance' },
        {
          custom_id: 'custom:mnopqrstuvwx', kind: 'custom', requested_operation: 'crop',
          source_role: 'canonical_identity', target_role: 'detail_callout:custom:mnopqrstuvwx',
          label_digest: 'private-digest',
        },
      ],
      character_profile: publicProfile,
      managed_character_callouts: publicManaged,
    },
  }
  const characterProfile = {
    gender: 'non_binary',
    age: 29,
    explicit_anatomy: ['breasts', 'penis'],
  }
  const privateDetailCallouts = [{
    custom_id: 'custom:mnopqrstuvwx', label: 'Owner wording', kind: 'custom',
    operation: 'crop', source_role: 'canonical_identity',
  }]
  const snapshot = {
    character_profile: characterProfile,
    explicit_convenience: true,
  }
  assert.equal(projectReferenceRetryNeedsPrivateAuthoring(source), true)
  assert.equal(isProjectReferenceCharacterReplayReady(source.metadata.reference_pack, snapshot), true)
  assert.equal(isProjectReferenceCharacterReplayReady(source.metadata.reference_pack, {
    character_profile: characterProfile,
    explicit_convenience: false,
  }), false, 'private semantic convenience must match the public contract')
  assert.equal(isProjectReferenceCharacterReplayReady(source.metadata.reference_pack, {
    character_profile: { ...characterProfile, commitment_nonce: '8'.repeat(64) },
    explicit_convenience: true,
  }), false, 'server-only sealing fields never enter the client replay snapshot')
  assert.equal(isProjectReferenceCharacterReplayReady(source.metadata.reference_pack, {
    character_profile: { ...characterProfile, explicit_anatomy: ['penis', 'breasts'] },
    explicit_convenience: true,
  }), false, 'semantic anatomy order must stay canonical')
  const driftedSource = structuredClone(source)
  driftedSource.metadata.reference_pack.authored_settings.managed_character_callouts.active_count = 3
  assert.equal(isProjectReferenceCharacterReplayReady(driftedSource.metadata.reference_pack, snapshot), false)
  delete driftedSource.metadata.reference_pack.explicit_convenience
  assert.equal(isProjectReferenceCharacterReplayReady(driftedSource.metadata.reference_pack, snapshot), false)

  const replay = getProjectReferenceRetrySettings(source, {
    mode: 'production', model_type: 'generator', editor_model_type: 'editor',
    private_output: true, explicit_output: false, review: true, max_repair_attempts: 1,
    schema_version: 2, asset_type: 'character', authored_settings_seal: 'profile-seal',
    type_fields: {}, detail_callouts: privateDetailCallouts,
    character_profile: characterProfile,
    explicit_convenience: true,
  }, { reference_types: [{ id: 'character', type_fields: [], detail_kinds: [] }] })
  assert.equal(replay.explicit_output, true)
  assert.equal(replay.explicit_convenience, true)
  assert.deepEqual(replay.character_profile, characterProfile)
  assert.equal('managed_character_callouts' in replay, false)
  assert.deepEqual(replay.detail_callouts, privateDetailCallouts)
})

test('project operation adoption requires both project and lifecycle epoch', () => {
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'one', 3), true)
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'two', 3), false)
  assert.equal(isProjectAssetOperationCurrent('one', 3, 'one', 4), false)
})

test('Reference Studio model helpers filter the server catalog and preserve local selections', () => {
  const catalog = [
    { model_type: 'video', name: 'Video', image_outputs: false, supports_ref_images: true },
    { model_type: 'image', name: 'Image', image_outputs: true, supports_ref_images: false },
    { model_type: 'editor', name: 'Editor', image_outputs: true, supports_ref_images: true },
    { model_type: 'unknown', name: 'Unknown' },
  ]
  const generation = getProjectReferenceGenerationModels(catalog)
  const editors = getProjectReferenceEditorModels(catalog)

  assert.deepEqual(generation.map(model => model.model_type), ['image', 'editor'])
  assert.deepEqual(editors.map(model => model.model_type), ['editor'])
  assert.equal(selectProjectReferenceModel(generation, 'editor', 'image'), 'editor')
  assert.equal(selectProjectReferenceModel(generation, 'missing', 'image'), 'image')
  assert.equal(selectProjectReferenceModel(editors, 'missing'), 'editor')
  assert.equal(selectProjectReferenceModel([], 'missing'), '')
})

test('Character profile serialization keeps gender age anatomy and output handling independent', () => {
  const blank = serializeProjectReferenceCharacterProfile({
    gender: 'unspecified', ageInput: '', explicitAnatomy: [],
  }, false)
  assert.deepEqual(blank, { age: null, blocker: null })

  const authored = serializeProjectReferenceCharacterProfile({
    gender: 'non_binary', ageInput: '18', explicitAnatomy: ['penis', 'breasts', 'vulva'],
  }, true)
  assert.deepEqual(authored, {
    age: 18,
    blocker: null,
    profile: {
      gender: 'non_binary',
      age: 18,
      explicit_anatomy: ['breasts', 'vulva', 'penis'],
    },
  })
  assert.equal('explicit_output' in authored, false)

  const omittedAge = serializeProjectReferenceCharacterProfile({
    gender: 'woman', ageInput: '  ', explicitAnatomy: ['vulva'],
  }, true)
  assert.deepEqual(omittedAge.profile, {
    gender: 'woman', explicit_anatomy: ['vulva'],
  })
  assert.equal(omittedAge.age, null, 'blank age is not inferred as adult')

  for (const ageInput of ['-1', '1000', '18.0', 'adult', '1e2']) {
    assert.equal(serializeProjectReferenceCharacterProfile({
      gender: 'unspecified', ageInput, explicitAnatomy: [],
    }, false).blocker, PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER)
  }
  assert.equal(serializeProjectReferenceCharacterProfile({
    gender: 'man', ageInput: '17', explicitAnatomy: ['penis'],
  }, true).blocker, PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER)
  assert.equal(serializeProjectReferenceCharacterProfile({
    gender: 'man', ageInput: '17', explicitAnatomy: ['penis'],
  }, false).blocker, null, 'the narrow gate belongs only to convenience')
})

test('Moody Krea 2 recipes are generation-selectable but never presented as editors', () => {
  const catalog = [
    { model_type: 'krea2_moody_mix_v7_fp8', name: 'Moody Mix', image_outputs: true, supports_ref_images: false },
    { model_type: 'krea2_moody_cutie_v4_fp8', name: 'Moody Cutie', image_outputs: true, supports_ref_images: false },
  ]
  assert.deepEqual(
    getProjectReferenceGenerationModels(catalog).map(model => model.model_type),
    ['krea2_moody_mix_v7_fp8', 'krea2_moody_cutie_v4_fp8'],
  )
  assert.deepEqual(getProjectReferenceEditorModels(catalog), [])
})

test('verified server preference defaults untouched non-Draft explicit Reference flows to Moody only', () => {
  const capabilities = {
    explicit_generation_model: {
      preferred_order: ['krea2_moody_mix_v7_fp8', 'krea2_moody_cutie_v4_fp8'],
      resolved_model: 'krea2_moody_mix_v7_fp8',
      fallback_model: 'flux2_dev',
      selection_source: 'verified_manual_preference',
      candidates: [
        { model_type: 'krea2_moody_mix_v7_fp8', ready: true },
        { model_type: 'krea2_moody_cutie_v4_fp8', ready: true },
      ],
    },
    default_models: { generation_model: 'flux2_dev', editor_model: 'qwen_image_edit_2511' },
  }
  assert.equal(getProjectReferencePreferredGenerationModel(
    'production', true, 'unrestricted_local', capabilities,
  ), 'krea2_moody_mix_v7_fp8')
  assert.equal(getProjectReferencePreferredGenerationModel(
    'production', false, 'unrestricted_local', capabilities,
  ), 'krea2_moody_mix_v7_fp8')
  assert.equal(getProjectReferencePreferredGenerationModel(
    'hybrid', true, 'standard', capabilities,
  ), 'krea2_moody_mix_v7_fp8')
  const hybridPreferred = getProjectReferencePreferredGenerationModel(
    'hybrid', false, 'unrestricted_local', capabilities,
  )
  assert.equal(hybridPreferred, 'krea2_moody_mix_v7_fp8')
  assert.equal(getProjectReferencePreferredGenerationModel(
    'production', false, 'standard', capabilities,
  ), 'flux2_dev')
  assert.equal(getProjectReferencePreferredGenerationModel(
    'draft', true, 'unrestricted_local', capabilities,
  ), 'flux2_klein_9b')

  const choices = getProjectReferenceGenerationModels([
    { model_type: 'flux2_dev', image_outputs: true },
    { model_type: 'krea2_moody_mix_v7_fp8', image_outputs: true },
  ])
  assert.equal(
    selectProjectReferenceModel(choices, 'flux2_dev', hybridPreferred),
    'flux2_dev',
    'an existing Hybrid user choice must win over the automatic explicit preference',
  )
})

test('LoRA parameter defaults and validation remain server-schema authoritative', () => {
  const schema = {
    schema_version: 1,
    schema_digest: 'schema-1',
    parameters: [
      { id: 'category', label: 'Category', type: 'enum', required: true, default: 'medium', scopes: ['generation'], roles: [], options: [{ value: 'small', label: 'Small' }, { value: 'medium', label: 'Medium' }] },
      { id: 'amount', label: 'Amount', type: 'number', required: true, scopes: ['generation'], roles: [], minimum: 0, maximum: 2, step: 0.1 },
      { id: 'count', label: 'Count', type: 'integer', required: false, scopes: ['editing'], roles: [], minimum: 1, maximum: 5, step: 2 },
      { id: 'enabled', label: 'Enabled', type: 'boolean', required: true, default: false, scopes: ['generation'], roles: [] },
      { id: 'note', label: 'Note', type: 'text', required: false, scopes: ['generation'], roles: [], min_length: 2, max_length: 5 },
    ],
  }
  assert.deepEqual(getLoraParameterDefaults(schema), { category: 'medium', enabled: false })
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'small', amount: 1.5, count: 3, enabled: true, note: 'short',
  }), [])
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'large', amount: 3, count: 2.5, enabled: 'true', note: 'too long', extra: 1,
  }), [
    'Unknown parameter: extra.',
    'Category must use one of the published choices.',
    'Amount must be at most 2.',
    'Count must be a whole number.',
    'Count must follow the published step of 2.',
    'Enabled must be Yes or No.',
    'Note must be at most 5 characters.',
  ])
  assert.deepEqual(validateLoraParameterValues(undefined, { amount: 1 }), [
    'This LoRA no longer publishes a parameter schema.',
  ])
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'small', amount: 0.15, count: 3, enabled: true, note: 'a',
  }), [
    'Amount must follow the published step of 0.1.',
    'Note must be at least 2 characters.',
  ])
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'small', amount: 0.2, count: 3, enabled: true, note: 'a\n',
  }), ['Note cannot contain control characters.'])
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'small', amount: 0.2, count: 3, enabled: true, note: '😀😀',
  }), [])
  assert.deepEqual(validateLoraParameterValues(schema, {
    category: 'small', amount: 0.2, count: 3, enabled: true, note: '😀😀😀😀😀😀',
  }), ['Note must be at most 5 characters.'])
  const category = schema.parameters[0]
  assert.equal(getLoraParameterValue(category, {}), 'medium')
  assert.equal(getLoraParameterValue(category, { category: 'small' }), 'small')
  const note = schema.parameters[4]
  assert.equal(getLoraParameterValue(note, { note: '' }), '')
})

test('Breast Size known contract keeps required controls, defaults, triggers, and multiplier separate', async t => {
  const exactRoles = [
    'canonical_identity', 'turnaround', 'expressions', 'wardrobe', 'identity_details',
  ]
  const schema = {
    schema_version: 1,
    schema_digest: 'known-breast-size-contract',
    schema_source: 'server_known_contract',
    trigger_disclosure: {
      source: 'server_known_contract',
      activation_phrases: [
        { parameter_id: 'breast_size', value: 'tiny', text: 'tiny breasts' },
        { parameter_id: 'breast_size', value: 'small', text: 'small breasts' },
        { parameter_id: 'breast_size', value: 'saggy', text: 'saggy breasts' },
        { parameter_id: 'breast_size', value: 'implants', text: 'breast implants' },
        { parameter_id: 'breast_size', value: 'huge', text: 'huge breasts' },
        { parameter_id: 'skin_detail', value: true, text: 'skin detail' },
      ],
      scopes: ['generation'],
      roles: exactRoles,
    },
    parameters: [
      {
        id: 'breast_size', label: 'Breast size', type: 'enum', required: true,
        scopes: ['generation'], roles: exactRoles,
        options: [
          { value: 'tiny', label: 'Tiny' }, { value: 'small', label: 'Small' },
          { value: 'saggy', label: 'Saggy' }, { value: 'implants', label: 'Implants' },
          { value: 'huge', label: 'Huge' },
        ],
      },
      {
        id: 'skin_detail', label: 'Skin detail', type: 'boolean', required: false,
        default: true, scopes: ['generation'], roles: exactRoles,
      },
    ],
  }
  assert.deepEqual(getLoraParameterDefaults(schema), { skin_detail: true })
  assert.deepEqual(validateLoraParameterValues(schema, { skin_detail: true }), [
    'Breast size is required.',
  ])
  assert.deepEqual(validateLoraParameterValues(schema, {
    breast_size: 'huge', skin_detail: true,
  }), [])
  assert.deepEqual(schema.trigger_disclosure.activation_phrases.map(item => item.text), [
    'tiny breasts', 'small breasts', 'saggy breasts', 'breast implants',
    'huge breasts', 'skin detail',
  ])
  const sexGodSchema = {
    schema_version: 1,
    schema_digest: 'known-sexgod-contract',
    schema_source: 'server_known_contract',
    trigger_disclosure: {
      source: 'server_known_contract',
      activation_phrases: [{
        parameter_id: 'activation_keyword', value: true, text: 'femalenudestyle',
      }],
      scopes: ['generation'],
      roles: exactRoles,
    },
    parameters: [{
      id: 'activation_keyword', label: 'Activation keyword', type: 'boolean',
      required: false, default: true, scopes: ['generation'], roles: exactRoles,
    }],
  }
  assert.deepEqual(getLoraParameterDefaults(sexGodSchema), { activation_keyword: true })
  assert.deepEqual(validateLoraParameterValues(sexGodSchema, {}), [])
  assert.equal(sexGodSchema.trigger_disclosure.activation_phrases[0].text, 'femalenudestyle')

  const originalFetch = globalThis.fetch
  const bodies = []
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(String(init?.body)))
    return new Response(JSON.stringify({ job_id: `job-${bodies.length}`, asset: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  await generateProjectAssetReferences('project', {
    name: 'Known controls',
    additional_loras: [{
      id: 'BreastSize-000001.safetensors', multiplier: 0.65, scope: 'generation',
      parameter_schema_digest: schema.schema_digest,
      parameter_values: { breast_size: 'huge', skin_detail: true },
    }],
  })
  await generateProjectAssetReferences('project', {
    name: 'Strength only',
    additional_loras: [{
      id: 'unknown-local.safetensors', multiplier: 1.2, scope: 'generation',
    }],
  })
  assert.deepEqual(bodies[0].additional_loras, [{
    id: 'BreastSize-000001.safetensors', multiplier: 0.65, scope: 'generation',
    parameter_schema_digest: schema.schema_digest,
    parameter_values: { breast_size: 'huge', skin_detail: true },
  }])
  assert.deepEqual(bodies[1].additional_loras, [{
    id: 'unknown-local.safetensors', multiplier: 1.2, scope: 'generation',
  }])
})

test('ambiguous JSON-equivalent enum choices block Queue while distinct numeric choices remain valid', () => {
  const enumSchema = options => ({
    schema_version: 1,
    schema_digest: 'numeric-enum',
    parameters: [{
      id: 'size', label: 'Size', type: 'enum', required: true, scopes: ['generation'], roles: [], options,
    }],
  })
  const ambiguousErrors = validateLoraParameterValues(enumSchema([
    { value: 1, label: 'Integer one' },
    { value: 1.0, label: 'Float one after JSON parsing' },
  ]), { size: 1 })
  assert.deepEqual(ambiguousErrors, ['Size publishes ambiguous duplicate choices.'])
  const optionalAmbiguousSchema = enumSchema([
    { value: 1, label: 'Integer one' },
    { value: 1.0, label: 'Float one after JSON parsing' },
  ])
  optionalAmbiguousSchema.parameters[0].required = false
  assert.deepEqual(validateLoraParameterValues(optionalAmbiguousSchema, {}), [
    'Size publishes ambiguous duplicate choices.',
  ])
  const blockers = getProjectReferenceQueueBlockers({
    submitting: false, project_locked: false, loading: false, name_missing: false,
    capabilities_unavailable: false, deliverables_unavailable: false,
    generation_model_missing: false, editor_model_missing: false, terms_pending: false,
    manual_verification_pending: false, incompatible_lora: false,
    invalid_lora_multiplier: false, invalid_lora_parameters: ambiguousErrors.length > 0,
    invalid_authored_settings: false, invalid_character_age: false,
    explicit_convenience_age: false, too_many_detail_callouts: false,
    review_unavailable: false,
  })
  assert.deepEqual(blockers.map(blocker => blocker.id), ['invalid_lora_parameters'])

  const validSchema = enumSchema([
    { value: 1, label: 'One' },
    { value: 1.5, label: 'One and a half' },
  ])
  assert.deepEqual(validateLoraParameterValues(validSchema, { size: 1 }), [])
  assert.deepEqual(validateLoraParameterValues(validSchema, { size: 1.5 }), [])
})

test('zero-value parameter contracts, auto-scope conflicts, and LAN-hidden visibility stay explicit', () => {
  assert.equal(hasProjectReferenceLoraParameterSummary({
    parameters: {
      count: 0, ids: [], schema_digest: 'schema', values_digest: 'values', expansion_digest: 'expansion',
    },
  }), true)
  const generation = { schema_version: 1, schema_digest: 'generation', parameters: [] }
  const editing = { schema_version: 1, schema_digest: 'editing', parameters: [] }
  assert.equal(loraParameterSchemasConflict(generation, editing, 'auto', true, true), true)
  assert.equal(loraParameterSchemasConflict(generation, undefined, 'auto', true, true), true)
  assert.equal(loraParameterSchemasConflict(generation, undefined, 'auto', true, false), false)
  assert.equal(loraParameterSchemasConflict(generation, editing, 'generation', true, true), false)
  assert.deepEqual(getProjectReferenceVisibilityHints(
    ['krea2_moody_mix_v7_fp8', 'krea2_moody_cutie_v4_fp8'],
    new Set(),
    [],
    true,
  ), {
    disabled: ['krea2_moody_mix_v7_fp8', 'krea2_moody_cutie_v4_fp8'],
    enabled_missing: [],
  })
  assert.deepEqual(getProjectReferenceVisibilityHints(
    ['krea2_moody_mix_v7_fp8'],
    new Set(['krea2_moody_mix_v7_fp8']),
    [],
    true,
  ), { disabled: [], enabled_missing: ['krea2_moody_mix_v7_fp8'] })
})

test('Queue blockers are the executable source of every disabled reason', () => {
  const clear = {
    submitting: false, project_locked: false, loading: false, name_missing: false,
    capabilities_unavailable: false, deliverables_unavailable: false,
    generation_model_missing: false, editor_model_missing: false, terms_pending: false,
    manual_verification_pending: false, incompatible_lora: false,
    invalid_lora_multiplier: false, invalid_lora_parameters: false,
    invalid_authored_settings: false, invalid_character_age: false,
    explicit_convenience_age: false, too_many_detail_callouts: false,
    review_unavailable: false,
  }
  assert.deepEqual(getProjectReferenceQueueBlockers(clear), [])
  const all = getProjectReferenceQueueBlockers(Object.fromEntries(
    Object.keys(clear).map(key => [key, true]),
  ))
  assert.deepEqual(all.map(blocker => blocker.id), Object.keys(clear))
  assert.ok(all.every(blocker => blocker.message.endsWith('.')))
})

test('panel repair policy is bounded and disabled for Draft or review-off', () => {
  assert.equal(getEffectiveProjectReferenceRepairAttempts('production', true, 1), 1)
  assert.equal(getEffectiveProjectReferenceRepairAttempts('hybrid', true, 5), 5)
  assert.equal(getEffectiveProjectReferenceRepairAttempts('hybrid', true, 99), 5)
  assert.equal(getEffectiveProjectReferenceRepairAttempts('production', true, Number.NaN), 1)
  assert.equal(getEffectiveProjectReferenceRepairAttempts('draft', true, 5), 0)
  assert.equal(getEffectiveProjectReferenceRepairAttempts('production', false, 5), 0)
})

test('repair result copy uses recorded attempts with correct pluralization', () => {
  assert.equal(getProjectReferenceRepairCopy({
    repair_attempts_used: 1,
    roles: { repaired: ['identity_front'] },
  }), '1 bounded repair attempt regenerated Identity Front.')
  assert.equal(getProjectReferenceRepairCopy({
    repair_attempts_used: 2,
    roles: { repaired: ['identity_front', 'identity_profile'] },
  }), '2 bounded repair attempts regenerated Identity Front, Identity Profile.')
  assert.equal(getProjectReferenceRepairCopy({
    repair_attempts_used: 2,
    roles: { repaired: [] },
  }), '2 bounded repair attempts regenerated the requested panels.')
  assert.equal(getProjectReferenceRepairCopy({
    roles: { repaired: ['legacy_panel', 'legacy_palette'] },
  }), '2 bounded repair attempts regenerated Legacy Panel, Legacy Palette.')
})

test('legacy Hybrid retry prefers a valid current editor and otherwise omits it exactly', async t => {
  const source = variant([output('legacy-sheet', 'sheet')])
  source.metadata.reference_sheet = {
    mode: 'hybrid',
    model: 'legacy-generator',
    max_repair_attempts: 1,
  }
  const fallback = {
    mode: 'production',
    model_type: 'current-generator',
    editor_model_type: 'current-editor',
    private_output: false,
    explicit_output: false,
    review: true,
    max_repair_attempts: 1,
    asset_type: 'location',
    preset: 'spatial',
    anchor_basis: 'least_occluded',
  }
  assert.equal(getProjectReferenceRetrySettings(source, fallback).editor_model_type, 'current-editor')

  const settings = getProjectReferenceRetrySettings(source, {
    ...fallback,
    editor_model_type: '',
  })
  assert.equal(settings.editor_model_type, undefined)

  const originalFetch = globalThis.fetch
  let requestBody
  globalThis.fetch = async (_url, init) => {
    requestBody = JSON.parse(String(init?.body))
    return new Response(JSON.stringify({ job_id: 'legacy-job', asset: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  await generateProjectAssetReferences('project', {
    asset_id: 'legacy-asset',
    parent_variant_id: 'legacy-variant',
    mode: settings.mode,
    model_type: settings.model_type,
    editor_model_type: settings.editor_model_type,
    review: settings.review,
    max_repair_attempts: settings.max_repair_attempts,
    asset_type: settings.asset_type,
    preset: settings.preset,
    anchor_basis: settings.anchor_basis,
  })
  assert.deepEqual(requestBody, {
    asset_id: 'legacy-asset',
    parent_variant_id: 'legacy-variant',
    mode: 'hybrid',
    model_type: 'legacy-generator',
    review: true,
    max_repair_attempts: 1,
    asset_type: 'location',
    preset: 'spatial',
    anchor_basis: 'least_occluded',
  })
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
      max_repair_attempts: mode === 'draft' ? 0 : 1,
      model_type: 'reference-generator',
      editor_model_type: mode === 'hybrid' ? 'reference-editor' : undefined,
    })
  }
  await generateProjectAssetReferences('My Project', {
    asset_id: 'asset-1',
    parent_variant_id: 'kept-source',
    edit_instruction: 'change only the coat',
    style: 'hand-painted stop motion',
    mode: 'hybrid',
    candidate_count: 1,
  })

  assert.deepEqual(requests.map(request => request.body.mode), ['production', 'hybrid', 'draft', 'hybrid'])
  assert.equal(requests[0].url, '/api/v1/projects/My%20Project/assets/generate')
  assert.deepEqual(requests[0].body, {
    name: 'A',
    asset_type: 'character',
    description: '',
    mode: 'production',
    candidate_count: 2,
    columns: 3,
    palette_swatches: 7,
    review: true,
    max_repair_attempts: 1,
    model_type: 'reference-generator',
  })
  assert.deepEqual(requests[1].body, {
    ...requests[0].body,
    mode: 'hybrid',
    editor_model_type: 'reference-editor',
  })
  assert.equal(requests[2].body.max_repair_attempts, 0)
  assert.equal('editor_model_type' in requests[2].body, false)
  assert.deepEqual(requests[3].body, {
    asset_id: 'asset-1',
    parent_variant_id: 'kept-source',
    edit_instruction: 'change only the coat',
    style: 'hand-painted stop motion',
    mode: 'hybrid',
    candidate_count: 1,
  })
})

test('fresh Character profile and convenience serialize independently without managed identities', async t => {
  const originalFetch = globalThis.fetch
  const bodies = []
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(String(init?.body)))
    return new Response(JSON.stringify({ job_id: `profile-${bodies.length}`, asset: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  await generateProjectAssetReferences('project', {
    name: 'Character', asset_type: 'character', mode: 'production',
    explicit_output: true,
    explicit_convenience: true,
    character_profile: {
      gender: 'non_binary', age: 24, explicit_anatomy: ['breasts', 'penis'],
    },
  })
  await generateProjectAssetReferences('project', {
    name: 'Location', asset_type: 'location', mode: 'production', explicit_output: true,
  })

  assert.equal(bodies[0].explicit_output, true)
  assert.equal(bodies[0].explicit_convenience, true)
  assert.deepEqual(bodies[0].character_profile, {
    gender: 'non_binary', age: 24, explicit_anatomy: ['breasts', 'penis'],
  })
  assert.equal('managed_character_callouts' in bodies[0], false)
  for (const key of ['character_profile', 'explicit_convenience', 'managed_character_callouts']) {
    assert.equal(key in bodies[1], false, `non-Character request omits ${key}`)
  }
})

test('Reference submission idempotency retries only ambiguous transport with one exact body and parent', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })

  const deterministic = projectReferenceRequestIdFromRandomBytes(
    Uint8Array.from({ length: 18 }, (_, index) => index),
  )
  assert.equal(deterministic, 'ref_ABCDEFGHIJKLMNOPQR')
  assert.match(deterministic, PROJECT_REFERENCE_REQUEST_ID_PATTERN)
  const deliberateFreshId = createProjectReferenceRequestId()
  const deliberateRetryId = createProjectReferenceRequestId()
  assert.match(deliberateFreshId, PROJECT_REFERENCE_REQUEST_ID_PATTERN)
  assert.match(deliberateRetryId, PROJECT_REFERENCE_REQUEST_ID_PATTERN)
  assert.notEqual(deliberateFreshId, deliberateRetryId, 'separate deliberate submissions mint separate random IDs')

  const postedBodies = []
  const serializedParents = new Map()
  globalThis.fetch = async (_url, init) => {
    const encodedBody = String(init?.body)
    const body = JSON.parse(encodedBody)
    postedBodies.push(encodedBody)
    if (!serializedParents.has(body.request_id)) {
      serializedParents.set(body.request_id, `parent-${serializedParents.size + 1}`)
    }
    if (postedBodies.length === 1) throw new TypeError('response connection was lost after acceptance')
    return new Response(JSON.stringify({
      job_id: serializedParents.get(body.request_id),
      asset: {},
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  const accepted = await generateProjectAssetReferences('project', {
    request_id: deliberateFreshId,
    name: 'Same visible name is not an idempotency key',
    asset_type: 'character',
    mode: 'production',
  })
  assert.equal(accepted.job_id, 'parent-1')
  assert.equal(postedBodies.length, 2)
  assert.equal(postedBodies[0], postedBodies[1], 'ambiguous retry reuses the byte-exact body and request ID')
  assert.equal(serializedParents.size, 1, 'lost response plus retry serializes one logical parent')

  const separateClick = await generateProjectAssetReferences('project', {
    request_id: deliberateRetryId,
    name: 'Same visible name is not an idempotency key',
    asset_type: 'character',
    mode: 'production',
  })
  assert.equal(separateClick.job_id, 'parent-2')
  assert.equal(serializedParents.size, 2, 'a separate deliberate ID creates a separate parent even with the same name')
  assert.equal('logical_job_kind' in JSON.parse(postedBodies[2]), false, 'logical kind is server-authored, not an operation marker')

  let conflictCalls = 0
  globalThis.fetch = async () => {
    conflictCalls += 1
    return new Response(JSON.stringify({ detail: 'request_id body mismatch' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  await assert.rejects(
    generateProjectAssetReferences('project', {
      request_id: deliberateFreshId,
      asset_id: 'asset-1',
      parent_variant_id: 'candidate-1',
      edit_instruction: 'retry with a changed body',
    }),
    error => error instanceof ProjectAssetRequestError && error.status === 409,
  )
  assert.equal(conflictCalls, 1, 'HTTP 409 mismatch is terminal and never transport-retried')

  let legacyCalls = 0
  globalThis.fetch = async () => {
    legacyCalls += 1
    throw new TypeError('ambiguous legacy transport')
  }
  await assert.rejects(generateProjectAssetReferences('project', {
    name: 'Legacy request',
    asset_type: 'character',
  }))
  assert.equal(legacyCalls, 1, 'legacy requests without an idempotency key are never automatically replayed')

  let invalidCalls = 0
  globalThis.fetch = async () => {
    invalidCalls += 1
    throw new Error('must not fetch')
  }
  await assert.rejects(generateProjectAssetReferences('project', {
    request_id: 'short',
    name: 'Invalid ID',
    asset_type: 'character',
  }), /Invalid Reference request ID/)
  assert.equal(invalidCalls, 0)
})

test('public Reference fidelity presentation is compact, advisory, and legacy-safe', () => {
  const pass = projectReferenceQualityPresentation({
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    quality: {
      status: 'pass',
      warning: null,
      review_deferred: false,
      assessment: {
        version: 'fidelity_assessment_v2',
        assessment_class: 'exact',
        worst_severity: 'exact',
        residual_count: 0,
        score_basis_points: 10000,
        status: 'pass',
        dimension_checks: { style: true },
        failed_roles: [],
        reason_codes: [],
      },
      recommended: true,
      recommendation_basis: 'accepted_assessment',
    },
  })
  assert.deepEqual(pass, {
    stateLabel: 'Fidelity passed',
    gradeLabel: 'Exact',
    scoreLabel: '100%',
    residualSummary: null,
    correctionAvailable: false,
    recommended: true,
    preliminary: false,
    notice: null,
    tone: 'pass',
  })

  const residualMetadata = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    review: {
      requested_model: 'local-reviewer',
      resolved_model: 'local-reviewer',
      resolved_provider: 'local',
      final_correction: {
        template_id: 'reference-residual-correction',
        severity: 'minor_residual',
        affected_roles: ['turnaround'],
        reason_codes: ['style_mismatch'],
        score_basis_points: 8345,
      },
    },
    quality: {
      status: 'residual',
      warning: 'PRIVATE_FREE_FORM_REVIEW_MUST_NOT_RENDER',
      review_deferred: false,
      assessment: {
        version: 'fidelity_assessment_v2',
        assessment_class: 'minor_residual',
        worst_severity: 'minor_residual',
        residual_count: 2,
        score_basis_points: 8345,
        status: 'fail',
        dimension_checks: { style: false },
        failed_roles: ['turnaround'],
        reason_codes: ['style_mismatch', 'PRIVATE_REASON_MUST_NOT_RENDER'],
      },
      recommended: true,
      recommendation_basis: 'residual_assessment',
    },
  }
  const residual = projectReferenceQualityPresentation(residualMetadata)
  assert.equal(residual?.stateLabel, 'Fidelity reviewed')
  assert.equal(residual?.gradeLabel, 'Minor residuals')
  assert.equal(residual?.scoreLabel, '83.5%')
  assert.equal(residual?.residualSummary, 'Differences: style')
  assert.equal(residual?.correctionAvailable, true)
  assert.doesNotMatch(JSON.stringify(residual), /PRIVATE|commitment|rendered_brief/)

  const deferredMetadata = {
    schema_version: 2,
    planner_version: 'reference-pack-v2',
    quality: {
      status: 'review_unavailable',
      warning: 'PRIVATE_PROVIDER_FAILURE',
      review_deferred: true,
      assessment: null,
      recommended: true,
      recommendation_basis: 'preliminary_ungraded',
    },
  }
  const deferred = projectReferenceQualityPresentation(deferredMetadata)
  assert.equal(deferred?.stateLabel, 'Fidelity review deferred')
  assert.equal(deferred?.gradeLabel, 'Ungraded')
  assert.equal(deferred?.preliminary, true)
  assert.match(deferred?.notice ?? '', /remains usable/)
  assert.doesNotMatch(JSON.stringify(deferred), /PRIVATE_PROVIDER_FAILURE/)
  assert.equal(projectReferenceQualityPresentation({
    schema_version: 2,
    planner_version: 'reference-pack-v2',
  }), null, 'legacy records omit the new presentation cleanly')

  const makeCandidate = (id, label, metadata) => ({
    id,
    variant_type: 'reference_pack',
    label,
    status: 'candidate',
    outputs: [],
    metadata: { reference_pack: metadata, job: { id: 'job-1' } },
  })
  const assets = [{
    id: 'asset', asset_type: 'character', name: 'Character', description: '', tags: [], metadata: {},
    variants: [
      makeCandidate('candidate-1', 'Candidate 1', { ...residualMetadata, quality: { ...residualMetadata.quality, recommended: false, recommendation_basis: null } }),
      makeCandidate('candidate-2', 'Candidate 2', deferredMetadata),
    ],
  }]
  assert.deepEqual(projectReferenceJobQualitySummary(assets, 'job-1'), {
    candidateCount: 2,
    variantLabel: 'Candidate 2',
    presentation: deferred,
  })
  assert.equal(projectReferenceJobQualitySummary(assets, 'missing-job'), null)
  const duplicateRecommendation = structuredClone(assets)
  duplicateRecommendation[0].variants[0].metadata.reference_pack.quality.recommended = true
  duplicateRecommendation[0].variants[0].metadata.reference_pack.quality.recommendation_basis = 'residual_assessment'
  assert.equal(projectReferenceJobQualitySummary(duplicateRecommendation, 'job-1'), null,
    'an invalid multiple-recommendation set fails closed')
})

test('Reference candidate cards consume only the closed public fidelity projection', async () => {
  const [source, clientSource, referenceTypes] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
    readFile(typesUrl, 'utf8'),
  ])
  assert.match(clientSource, /quality\?: ProjectReferencePublicQuality/)
  assert.match(clientSource, /status: ProjectReferenceQualityStatus/)
  assert.match(clientSource, /recommendation_basis: ProjectReferenceRecommendationBasis \| null/)
  assert.match(referenceTypes, /final_correction\?: \{/)
  assert.match(source, /const qualityPresentation = projectReferenceQualityPresentation\(packMetadata\)/)
  assert.match(source, />Recommended<\/span>/)
  assert.match(source, /Preliminary recommendation · ungraded/)
  assert.match(source, /Structured correction guidance is available for Retry or Edit\./)
  assert.match(clientSource, /This candidate remains usable/)
  assert.doesNotMatch(source, /quality\.warning|rendered_brief|private_authored_settings/)
  const actions = source.slice(
    source.indexOf("onClick={() => void updateStatus(asset.id, variant.id, 'kept')"),
    source.indexOf("{(variant.variant_type === 'reference_sheet' || variant.variant_type === 'reference_pack')", source.indexOf("onClick={() => void updateStatus(asset.id, variant.id, 'kept')")),
  )
  assert.match(actions, /Keep/)
  assert.match(actions, /Reject/)
  assert.match(actions, /Delete candidate and copied media/)
  assert.doesNotMatch(actions, /qualityPresentation/)
})

test('v2 generation keeps pack candidates separate from custom sheet deliverables', async t => {
  const originalFetch = globalThis.fetch
  let requestBody
  globalThis.fetch = async (_url, init) => {
    requestBody = JSON.parse(String(init?.body))
    return new Response(JSON.stringify({
      job_id: 'pack-job', asset: {},
      plan: {
        schema_version: 2, planner_version: 'reference-pack-v2', intent: 'exact_spec',
        reference_type: 'character', depth: 'custom', preset: 'anatomy',
        anchor_basis: 'anatomy', anchor_privacy: 'private_blurred', sheet_count: 4,
        detail_callout_count: 1,
        private_output: true,
        ordered_sheet_roles: ['identity', 'turnaround', 'expression', 'detail'],
        ordered_output_roles: ['identity', 'turnaround', 'expression', 'detail', 'detail_callout:custom:mnopqrstuvwx'],
        mode: 'production', candidate_count: 2, anchor_strategy: 'canonical_anchor',
        operation_routing: {
          requested_capability: 'standard',
          operations: {
            generation: { status: 'standard', requested_model: 'flux2_dev', resolved_model: 'flux2_dev', schedule: null },
            edit: { status: 'standard', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null },
            repair: { status: 'standard', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null },
            callout: { status: 'standard', requested_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', resolved_model: 'qwen_image_edit_2511_20B_fp8_lightning_8step', schedule: null },
          },
        },
        managed_layout_assist: { schema_version: 1, mode: 'off', id: null, provenance: { kind: 'server_allowlist', version: 'managed-layout-v1' } },
        authored_settings: {
          seal: 'authored-seal',
          style_present: true,
          style_commitment: 'b'.repeat(64),
          type_fields: [{ field: 'poses', items: [{ id: 'custom:abcdefghijkl', custom: true, group: 'expressions' }] }],
          detail_callouts: [{
            custom_id: 'custom:mnopqrstuvwx', kind: 'custom', requested_operation: 'enhance',
            source_role: 'canonical_identity', target_role: 'detail_callout:custom:mnopqrstuvwx',
            label_digest: 'private-digest',
          }],
        },
        plan_seal: 'sealed-plan',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const response = await generateProjectAssetReferences('project', {
    schema_version: 2,
    name: 'Ari',
    asset_type: 'character',
    style: 'hand-painted stop motion',
    intent: 'exact_spec',
    depth: 'custom',
    sheet_count: 4,
    candidate_count: 2,
    preset: 'anatomy',
    anchor_basis: 'anatomy',
    type_fields: {
      poses: [
        { id: 'views:front', label: 'front', custom: false, group: 'views' },
        { id: 'custom:abcdefghijkl', label: 'Wry half-smile', custom: true, group: 'expressions' },
      ],
      outfits: [{ id: 'wardrobe:primary-outfit', label: 'primary outfit', custom: false, group: 'wardrobe' }],
    },
    detail_callouts: [{
      custom_id: 'custom:mnopqrstuvwx', label: 'Signet ring engraving', kind: 'custom',
      operation: 'enhance', source_role: 'canonical_identity',
    }],
    managed_layout_assist: 'off',
    planning_model: 'deterministic',
    review_model: 'auto_local',
    mode: 'production',
    model_type: 'flux2_dev',
    editor_model_type: 'qwen_image_edit_2511_20B_fp8_lightning_8step',
    max_repair_attempts: 5,
    explicit_output: true,
    content_capability: 'unrestricted_local',
    initial_blur: true,
    intelligence_policy: 'uncensored_auto',
    additional_loras: [
      { id: 'anchor.safetensors', multiplier: 0.75, scope: 'generation' },
      { id: 'auto.safetensors', multiplier: 1.1, scope: 'auto' },
    ],
  })
  assert.equal(response.plan.sheet_count, 4)
  assert.equal(response.plan.detail_callout_count, 1)
  assert.equal('label' in response.plan.authored_settings.detail_callouts[0], false)
  assert.equal(response.plan.plan_seal, 'sealed-plan')
  assert.equal(response.plan.anchor_privacy, 'private_blurred')
  assert.equal(response.plan.private_output, true)
  assert.equal(response.plan.operation_routing.operations.generation.status, 'standard')
  assert.equal(requestBody.candidate_count, 2)
  assert.equal(requestBody.sheet_count, 4)
  assert.equal(requestBody.planning_provider, undefined)
  assert.equal(requestBody.review_provider, undefined)
  assert.equal('num_inference_steps' in requestBody, false)
  assert.equal('guidance_scale' in requestBody, false)
  assert.deepEqual(requestBody.type_fields.poses, [
    { id: 'views:front', label: 'front', custom: false, group: 'views' },
    { id: 'custom:abcdefghijkl', label: 'Wry half-smile', custom: true, group: 'expressions' },
  ])
  assert.deepEqual(requestBody.detail_callouts, [{
    custom_id: 'custom:mnopqrstuvwx', label: 'Signet ring engraving', kind: 'custom',
    operation: 'enhance', source_role: 'canonical_identity',
  }])
  assert.equal(requestBody.explicit_output, true)
  assert.equal(requestBody.content_capability, 'unrestricted_local')
  assert.equal(requestBody.initial_blur, true)
  assert.equal(requestBody.intelligence_policy, 'uncensored_auto')
  assert.deepEqual(requestBody.additional_loras, [
    { id: 'anchor.safetensors', multiplier: 0.75, scope: 'generation' },
    { id: 'auto.safetensors', multiplier: 1.1, scope: 'auto' },
  ])
})

test('capabilities helper returns authoritative ordered roles for prequeue preview', async t => {
  const originalFetch = globalThis.fetch
  let requestedUrl = ''
  globalThis.fetch = async url => {
    requestedUrl = String(url)
    return new Response(JSON.stringify({
      schema_version: 2,
      planner_version: 'reference-pack-v2',
      lora_scopes: ['auto', 'generation', 'editing'],
      content_capabilities: ['standard', 'unrestricted_local'],
      intelligence_policies: ['standard_auto', 'uncensored_auto'],
      uncensored_auto_review: {
        requested_model: 'auto_local',
        resolved_model: 'local-abliterated-vision',
        resolved_provider: 'local',
        vision_required: true,
        required_projector: 'local-vision-mmproj',
        installed: true,
        projector_available: true,
        vision_capable: true,
        resident: false,
        vision_available: null,
        loading: false,
        loading_phase: null,
        setup_state: 'ready_unloaded',
        queue_ready: true,
      },
      explicit_generation_model: {
        preferred_order: ['krea2_moody_mix_v7_fp8', 'krea2_moody_cutie_v4_fp8'],
        resolved_model: 'krea2_moody_mix_v7_fp8',
        fallback_model: 'flux2_dev',
        selection_source: 'verified_manual_preference',
        candidates: [{
          model_type: 'krea2_moody_mix_v7_fp8', enabled: true,
          manual_checkpoint_verified: true, terms_accepted: true, downloaded: true, ready: true,
        }],
      },
      review_policy: {
        mandatory_for_content_capabilities: ['unrestricted_local'],
        mandatory_when_explicit_output: true,
        off_allowed_for_content_capabilities: ['standard'],
        mandatory_contract: 'explicit_unrestricted_fidelity_v1',
      },
      reference_types: [{
        id: 'character',
        presets: [{
          id: 'identity', label: 'Identity',
          ordered_roles: ['canonical_identity', 'turnaround', 'expressions'],
          valid_source_roles: ['canonical_identity', 'turnaround', 'expressions'],
          detail_operations: ['auto', 'crop', 'enhance', 'reconstruct'],
        }],
        type_fields: [{ id: 'poses', groups: [{
          id: 'views', label: 'Views', options: [{ id: 'views:front', label: 'front' }],
        }] }],
        detail_kinds: [{ id: 'face', label: 'Face' }],
        supports_custom_details: true,
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const capabilities = await fetchProjectReferenceCapabilities('My Project')
  assert.equal(requestedUrl, '/api/v1/projects/My%20Project/assets/reference-capabilities')
  assert.deepEqual(capabilities.reference_types[0].presets[0].ordered_roles, [
    'canonical_identity', 'turnaround', 'expressions',
  ])
  assert.equal(capabilities.reference_types[0].type_fields[0].groups[0].options[0].id, 'views:front')
  assert.deepEqual(capabilities.reference_types[0].presets[0].valid_source_roles, [
    'canonical_identity', 'turnaround', 'expressions',
  ])
  assert.deepEqual(capabilities.lora_scopes, ['auto', 'generation', 'editing'])
  assert.deepEqual(capabilities.content_capabilities, ['standard', 'unrestricted_local'])
  assert.deepEqual(capabilities.intelligence_policies, ['standard_auto', 'uncensored_auto'])
  assert.deepEqual(capabilities.uncensored_auto_review, {
    requested_model: 'auto_local',
    resolved_model: 'local-abliterated-vision',
    resolved_provider: 'local',
    vision_required: true,
    required_projector: 'local-vision-mmproj',
    installed: true,
    projector_available: true,
    vision_capable: true,
    resident: false,
    vision_available: null,
    loading: false,
    loading_phase: null,
    setup_state: 'ready_unloaded',
    queue_ready: true,
  })
  assert.equal(capabilities.explicit_generation_model.resolved_model, 'krea2_moody_mix_v7_fp8')
  assert.equal(capabilities.explicit_generation_model.candidates[0].ready, true)
  assert.deepEqual(capabilities.review_policy, {
    mandatory_for_content_capabilities: ['unrestricted_local'],
    mandatory_when_explicit_output: true,
    off_allowed_for_content_capabilities: ['standard'],
    mandatory_contract: 'explicit_unrestricted_fidelity_v1',
  })
})

test('mandatory retry review fails closed for recorded Off or unavailable reviewers', () => {
  const capabilities = {
    uncensored_auto_review: {
      requested_model: 'auto_local',
      resolved_model: 'local-abliterated-vision',
      resolved_provider: 'local',
      vision_required: true,
      queue_ready: true,
    },
    review_policy: {
      mandatory_for_content_capabilities: ['unrestricted_local'],
      mandatory_when_explicit_output: true,
      off_allowed_for_content_capabilities: ['standard'],
      mandatory_contract: 'explicit_unrestricted_fidelity_v1',
    },
  }
  const exactLocalModels = [{ id: 'local-abliterated-vision', provider: 'local' }]
  assert.equal(isProjectReferenceReviewMandatory('standard', false, capabilities.review_policy), false)
  assert.equal(isProjectReferenceReviewMandatory('unrestricted_local', false, capabilities.review_policy), true)
  assert.equal(isProjectReferenceReviewMandatory('standard', true, capabilities.review_policy), true)
  assert.equal(isProjectReferenceReviewerEligible(
    'uncensored_auto', 'missing-recorded-reviewer', 'local', exactLocalModels, capabilities,
  ), false)

  const recordedOff = resolveProjectReferenceRetryReview({
    content_capability: 'unrestricted_local',
    explicit_output: false,
    intelligence_policy: 'uncensored_auto',
    review: false,
    review_model: 'off',
  }, { review_model: 'off' }, exactLocalModels, capabilities)
  assert.deepEqual(recordedOff, {
    ready: false,
    use_current_reviewer: false,
    intelligence_policy: 'uncensored_auto',
  })

  const recordedUnavailable = resolveProjectReferenceRetryReview({
    content_capability: 'standard',
    explicit_output: true,
    intelligence_policy: 'uncensored_auto',
    review: true,
    review_model: 'missing-recorded-reviewer',
    review_provider: 'local',
  }, { review_model: 'off' }, exactLocalModels, capabilities)
  assert.equal(recordedUnavailable.ready, false)
  assert.equal(recordedUnavailable.use_current_reviewer, false)

  const safelySubstituted = resolveProjectReferenceRetryReview({
    content_capability: 'unrestricted_local',
    explicit_output: false,
    intelligence_policy: 'uncensored_auto',
    review: true,
    review_model: 'missing-recorded-reviewer',
    review_provider: 'local',
  }, { review_model: 'auto_local' }, exactLocalModels, capabilities)
  assert.equal(safelySubstituted.ready, true)
  assert.equal(safelySubstituted.use_current_reviewer, true)
})

test('required Paperscarecrow reviewer is queue-ready while installed but unloaded', () => {
  const base = {
    requested_model: 'auto_local',
    resolved_model: 'paperscarecrow/Gemma-4-31B-it-abliterated-gguf',
    resolved_provider: 'local',
    vision_required: true,
    required_projector: 'ggml-org/Gemma-4-31B-IT-GGUF:BF16-mmproj',
    installed: true,
    projector_available: true,
    vision_capable: true,
    resident: false,
    vision_available: null,
    loading: false,
    loading_phase: null,
    setup_state: 'ready_unloaded',
    queue_ready: true,
  }
  const capabilities = { uncensored_auto_review: base }
  assert.equal(isProjectReferenceReviewerEligible(
    'uncensored_auto', 'auto_local', undefined, [], capabilities,
  ), true)
  assert.equal(isProjectReferenceReviewerEligible(
    'uncensored_auto', base.resolved_model, 'local', [], capabilities,
  ), true)
  assert.equal(isProjectReferenceReviewerEligible(
    'uncensored_auto', base.resolved_model, 'remote', [], capabilities,
  ), false)
  assert.equal(
    getProjectReferenceReviewerSetupCopy(base),
    'Paperscarecrow and its MMProj are installed. They will load automatically when local fidelity review starts.',
  )
  assert.equal(getProjectReferenceReviewerAction(base.setup_state), null)

  for (const [setup_state, patch, expected] of [
    ['missing_model', { installed: false, queue_ready: false }, /checkpoint is not installed/],
    ['missing_projector', { projector_available: false, queue_ready: false }, /required MMProj is missing/],
    ['loading', { loading: true, loading_phase: 'loading projector', queue_ready: false }, /loading \(loading projector\)/],
    ['loaded_without_vision', { resident: true, vision_available: false, queue_ready: false }, /MMProj did not initialize/],
    ['ready_resident', { resident: true, vision_available: true, queue_ready: true }, /loaded with its MMProj/],
  ]) {
    const contract = { ...base, ...patch, setup_state }
    assert.equal(isProjectReferenceReviewerEligible(
      'uncensored_auto', 'auto_local', undefined,
      [{ id: base.resolved_model, provider: 'local' }],
      { uncensored_auto_review: contract },
    ), contract.queue_ready)
    assert.match(getProjectReferenceReviewerSetupCopy(contract), expected)
  }
  assert.deepEqual(getProjectReferenceReviewerAction('missing_model'), {
    kind: 'load', label: 'Install / load required reviewer',
  })
  assert.deepEqual(getProjectReferenceReviewerAction('missing_projector'), {
    kind: 'load', label: 'Install / load required reviewer',
  })
  assert.deepEqual(getProjectReferenceReviewerAction('loaded_without_vision'), {
    kind: 'reload', label: 'Reload required reviewer',
  })
  assert.equal(getProjectReferenceReviewerAction('loading'), null)
  assert.equal(getProjectReferenceReviewerAction('ready_resident'), null)
})

test('required reviewer load action submits only the exact model id', async t => {
  const originalFetch = globalThis.fetch
  let request
  globalThis.fetch = async (url, init) => {
    request = { url: String(url), init }
    return new Response(JSON.stringify({ status: 'ready', loaded: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  await loadLlm({
    model_id: 'paperscarecrow/Gemma-4-31B-it-abliterated-gguf',
    provider: 'local',
  })
  assert.equal(request.url, '/api/v1/llm/load')
  assert.equal(request.init.method, 'POST')
  assert.deepEqual(JSON.parse(request.init.body), {
    model_id: 'paperscarecrow/Gemma-4-31B-it-abliterated-gguf',
    provider: 'local',
  })
})

test('private authored settings use the exact owner route and no-store request', async t => {
  const originalFetch = globalThis.fetch
  const expected = {
    schema_version: 2,
    asset_id: 'asset/one',
    variant_id: 'variant two',
    authored_settings: {
      seal: 'sealed-private-authoring',
      style: 'hand-painted stop motion',
      type_fields: { poses: [{
        id: 'custom:abcdefghijkl', label: 'Private expression', custom: true, group: 'expressions',
      }] },
      detail_callouts: [{
        custom_id: 'custom:mnopqrstuvwx', label: 'Private ring detail', kind: 'custom',
        operation: 'enhance', source_role: 'canonical_identity',
      }],
      character_profile: {
        gender: 'non_binary', age: 31, explicit_anatomy: ['breasts'],
      },
      explicit_convenience: true,
    },
    additional_loras: [{
      id: 'shape.safetensors', multiplier: 0.8, scope: 'generation',
      parameter_schema_digest: 'schema-digest', parameter_values: { amount: 1.25, enabled: false },
      parameter_values_digest: 'values-digest', parameter_expansion_digest: 'expansion-digest',
    }],
  }
  let request
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options }
    return new Response(JSON.stringify(expected), {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'private, no-store' },
    })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  assert.deepEqual(
    await fetchProjectReferenceAuthoring('My Project', 'asset/one', 'variant two'),
    expected,
  )
  assert.equal(
    request.url,
    '/api/v1/projects/My%20Project/assets/asset%2Fone/variants/variant%20two/reference-authoring',
  )
  assert.equal(request.options.cache, 'no-store')

  globalThis.fetch = async () => new Response(null, { status: 409 })
  await assert.rejects(
    fetchProjectReferenceAuthoring('My Project', 'asset/one', 'variant two'),
    error => error instanceof ProjectAssetRequestError
      && error.status === 409
      && !/private expression|ring detail/i.test(error.message),
  )
})

test('project reference list errors preserve numeric status and never reflect response detail', async t => {
  const originalFetch = globalThis.fetch
  const cases = [
    [403, 'Project reference access was denied (HTTP 403)'],
    [404, 'Project references are unavailable for this project (HTTP 404)'],
    [423, 'Project reference access is locked (HTTP 423)'],
    [500, 'Project reference service failed (HTTP 500)'],
    [503, 'Project reference storage is unavailable (HTTP 503)'],
  ]
  t.after(() => { globalThis.fetch = originalFetch })

  for (const [status, message] of cases) {
    globalThis.fetch = async () => new Response(
      status === 500
        ? null
        : JSON.stringify({ detail: 'private filesystem and authentication detail' }),
      { status, headers: { 'Content-Type': 'application/json' } },
    )
    await assert.rejects(fetchProjectAssets('My Project'), error => {
      assert.equal(error instanceof ProjectAssetRequestError, true)
      assert.equal(error.status, status)
      assert.equal(error.message, message)
      assert.doesNotMatch(error.message, /private|filesystem|authentication/)
      return true
    })
  }
})

test('project reference mutations share the same fixed storage failure boundary', async t => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: 'private manifest path and parser detail' }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  )
  t.after(() => { globalThis.fetch = originalFetch })

  const operations = [
    () => createProjectAsset('project', { name: 'Card' }),
    () => generateProjectAssetReferences('project', { name: 'Card', asset_type: 'character' }),
    () => addProjectAssetVariant('project', 'asset', { outputs: [] }),
    () => setProjectAssetVariantStatus('project', 'asset', 'variant', 'kept'),
    () => deleteProjectAssetVariant('project', 'asset', 'variant'),
  ]
  for (const operation of operations) {
    await assert.rejects(operation(), error => {
      assert.equal(error instanceof ProjectAssetRequestError, true)
      assert.equal(error.status, 503)
      assert.equal(error.message, 'Project reference storage is unavailable (HTTP 503)')
      assert.doesNotMatch(error.message, /private|manifest|path|parser/)
      return true
    })
  }
})

test('project reference display errors scrub arbitrary exception details', () => {
  const privateReason = new Error('private prompt /media/secret.png provider-key')
  assert.equal(
    projectReferenceSafeErrorMessage(privateReason, 'Could not queue reference generation.'),
    'Could not queue reference generation.',
  )
  const fixed = new ProjectAssetRequestError(503, 'Project reference storage is unavailable (HTTP 503)')
  assert.equal(
    projectReferenceSafeErrorMessage(fixed, 'fallback'),
    'Project reference storage is unavailable (HTTP 503)',
  )
})

test('Reference authoring controls expose 44px mobile targets and compact at 768px', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const authoringStart = source.indexOf('Create reference candidates')
  const authoringEnd = source.indexOf('<div className="overflow-visible p-4">', authoringStart)
  assert.ok(authoringStart >= 0 && authoringEnd > authoringStart)
  const sourceFile = ts.createSourceFile(
    'ProjectReferenceLibrary.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
  )
  const interactiveTags = new Set(['button', 'input', 'select', 'textarea', 'summary', 'a'])
  const missingTargets = []
  const tagName = node => node.tagName?.getText(sourceFile)
  const inspectControl = node => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
      && node.pos >= authoringStart && node.end <= authoringEnd
      && interactiveTags.has(tagName(node))) {
      const openingTag = node.getText(sourceFile)
      let targetOwner = openingTag
      const type = node.attributes.properties.find(property => (
        property.name?.getText(sourceFile) === 'type'
      ))?.initializer?.getText(sourceFile) ?? ''
      if (tagName(node) === 'input' && /checkbox|radio/.test(type)) {
        let parent = node.parent
        while (parent && parent.pos >= authoringStart) {
          if (ts.isJsxElement(parent) && tagName(parent.openingElement) === 'label') {
            targetOwner = parent.openingElement.getText(sourceFile)
            break
          }
          parent = parent.parent
        }
      }
      if (!targetOwner.includes('min-h-11') || !targetOwner.includes('md:min-h-0')) {
        missingTargets.push(`${tagName(node)} at line ${sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1}`)
      }
      if (tagName(node) === 'button'
        && (!openingTag.includes('min-w-11') || !openingTag.includes('md:min-w-0'))) {
        missingTargets.push(`button width at line ${sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1}`)
      }
    }
    ts.forEachChild(node, inspectControl)
  }
  inspectControl(sourceFile)
  assert.deepEqual(missingTargets, [], `all Reference authoring controls need mobile targets: ${missingTargets.join(', ')}`)

  const directControlMarkers = [
    'aria-label="Reference name"',
    'aria-label="Reference description"',
    'aria-label="Reference visual style"',
    'aria-label="Custom reference visual style"',
    'aria-label="Custom sheets per reference pack"',
    'id="project-reference-character-gender"',
    'id="project-reference-character-age"',
    'id="project-reference-content-capability"',
    'id="project-reference-initial-blur"',
    'id="project-reference-intelligence-policy"',
    'aria-label={`${item.label} detail source`}',
    'aria-label={`${item.label} detail operation`}',
    'aria-label={`Add custom ${definition.label.toLowerCase()} callout`}',
    'aria-label="Reference generation model"',
    'aria-label="Reference editor model"',
    'aria-label="Additional LoRA scope"',
    'aria-label="Additional LoRA multiplier"',
    'aria-label="Additional compatible LoRA"',
    'aria-label={`${lora.id} multiplier`}',
    'aria-label={`${lora.id} scope`}',
    'aria-label="Reference candidate packs"',
    'aria-label="Reference sheet collage columns"',
    'aria-label="Reference sheet palette swatches"',
    'aria-label="Reference planning model"',
    'aria-label="Reference visual review model"',
    'aria-label="Maximum panel repair attempts"',
  ]
  for (const marker of directControlMarkers) {
    const markerIndex = source.indexOf(marker)
    assert.notEqual(markerIndex, -1, `found ${marker}`)
    const nearby = source.slice(markerIndex, markerIndex + 1_200)
    assert.match(nearby, /className="[^"]*min-h-11[^"]*md:min-h-0/, `${marker} has a mobile-only 44px minimum`)
  }

  for (const template of [
    /ASSET_TYPES\.map[\s\S]*?className=\{`flex min-h-11[\s\S]*?md:min-h-0/,
    /INTENT_OPTIONS\.map[\s\S]*?className=\{`min-h-11[\s\S]*?md:min-h-0/,
    /DEPTH_OPTIONS\.map[\s\S]*?className=\{`min-h-11[\s\S]*?md:min-h-0/,
    /visiblePresets\.map[\s\S]*?className=\{`min-h-11[\s\S]*?md:min-h-0/,
    /definition\.options\.map[\s\S]*?className=\{`min-h-11[\s\S]*?md:min-h-0/,
    /SHEET_MODES\.map[\s\S]*?className=\{`block min-h-11[\s\S]*?md:min-h-0/,
    /MOODY_MODEL_TYPES\.map[\s\S]*?className=\{`min-h-11[\s\S]*?md:min-h-0/,
  ]) assert.match(source, template)

  for (const copy of [
    'Explicit convenience',
    'Explicit output',
    'Keep anatomy anchor private and blurred',
    'Review exact terms',
    'Open source page',
    'Open exact manual download',
    'Verify local checkpoint',
    'Refresh reviewer status',
    'Automatic · unavailable',
    '>Off</button>',
    'Queue reference packs',
  ]) {
    const copyIndex = source.indexOf(copy)
    assert.notEqual(copyIndex, -1, `found ${copy}`)
    const controlStart = Math.max(
      source.lastIndexOf('<button', copyIndex),
      source.lastIndexOf('<label', copyIndex),
      source.lastIndexOf('<a ', copyIndex),
    )
    const nearby = source.slice(controlStart, copyIndex + 400)
    assert.match(nearby, /min-h-11/)
    assert.match(nearby, /md:min-h-0/)
  }

  assert.match(source, /<summary className="flex min-h-11[^\"]*md:min-h-0">Advanced<\/summary>/)
  assert.match(source, /grid grid-cols-1 gap-1 md:grid-cols-2/)
  assert.match(source, /mt-1\.5 flex flex-col gap-1 md:flex-row/)
  assert.match(source, /mt-1\.5 grid grid-cols-1 gap-1 md:grid-cols-\[1fr_auto\]/)
  assert.match(source, /grid grid-cols-1 gap-1 md:grid-cols-\[minmax\(0,1fr\)_auto_auto\]/)
  assert.match(source, /aria-label="Reference candidate packs"/)

  const compiler = await compile('@theme { --spacing: 0.25rem; --breakpoint-md: 48rem; } @tailwind utilities;')
  const css = compiler.build(['min-h-11', 'min-w-11', 'md:min-h-0', 'md:min-w-0'])
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /min-width: calc\(var\(--spacing\) \* 11\)/)
  assert.match(css, /@media \(width >= 48rem\)/)
  assert.match(css, /min-height: calc\(var\(--spacing\) \* 0\)/)
})

test('component source guards lifecycle, accessibility, mobile flow, and sheet-only apply', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const clientSource = await readFile(clientUrl, 'utf8')
  const referenceTypes = await readFile(typesUrl, 'utf8')

  assert.match(source, /id="project-reference-title"[^>]*>Reference Studio<\/h2>/)

  assert.match(referenceTypes, /'private_blurred' \| 'private_visible' \| 'project_blurred' \| 'project_visible'/)
  assert.match(referenceTypes, /LogicalJobKind = 'reference_pack_parent' \| 'reference_pack_child'/)
  assert.match(referenceTypes, /logicalJobKind\?: LogicalJobKind/)
  assert.equal(clientSource.match(/logical_job_kind\?: LogicalJobKind/g)?.length, 2)
  assert.match(referenceTypes, /ProjectReferenceLegacyAnchorPrivacy = ProjectReferenceAnchorPrivacy \| 'standard'/)
  assert.doesNotMatch(referenceTypes, /anchor_privacy: 'private_blurred' \| 'standard'/)
  assert.match(referenceTypes, /operation_routing: ProjectReferenceOperationRouting/)
  assert.match(referenceTypes, /private_output: boolean/)
  assert.match(referenceTypes, /'server_known_contract'/)
  assert.match(referenceTypes, /activation_phrases: Array/)
  assert.match(referenceTypes, /custom_id: string[\s\S]*?label: string[\s\S]*?operation: ProjectReferenceDetailOperation[\s\S]*?source_role: string/)
  assert.match(referenceTypes, /ProjectReferenceTypeFieldItem\[\]/)
  assert.match(referenceTypes, /detail_callout_count: number/)
  assert.match(referenceTypes, /ordered_output_roles: string\[\]/)
  assert.match(referenceTypes, /ProjectReferenceCharacterGender = 'woman' \| 'man' \| 'non_binary' \| 'unspecified'/)
  assert.match(referenceTypes, /ProjectReferenceCharacterAnatomy = 'breasts' \| 'vulva' \| 'penis'/)
  assert.match(referenceTypes, /ProjectReferenceManagedDetailCalloutSummary[\s\S]*?managed: true/)
  assert.match(clientSource, /detail\?: \{[\s\S]*?managed: true[\s\S]*?\} \| \{[\s\S]*?custom_id: string[\s\S]*?label_digest: string[\s\S]*?seal: string/)
  assert.doesNotMatch(clientSource, /detail_kind\?: ProjectReferenceDetailKind/)

  for (const copy of [
    'Establishes one canonical anchor',
    'targeted reference-guided edits',
    'fast unanchored one-shot',
    'Candidate count creates alternatives',
    'Draft creates each requested sheet as an unanchored one-shot and does not use panel repair',
    'Canonical anchor:',
    'Depth changes update untouched sections only',
    'Subject and content LoRAs are never enabled automatically',
    'Wardrobe & underlayers',
    'Explicit convenience',
    'Anatomy / Nude anchor',
    'Gender never selects anatomy or establishes age',
    'does not scan or infer age from text, appearance, or gender',
    'Uncensored-capable Auto',
    'Auto never sends data remotely',
  ]) assert.match(source, new RegExp(copy))

  assert.match(source, /asset_id: asset\.id/)
  assert.match(source, /parent_variant_id: variant\.id/)
  assert.match(source, /edit_instruction: instruction\?\.trim\(\) \|\| undefined/)
  assert.match(source, /lockProjectAssetVariantOperation\(/)
  assert.match(source, /projectAssetVariantOperationKey\(project, asset\.id, variant\.id\)/)
  assert.match(source, /isProjectAssetOperationCurrent\(submittedProject, epoch, currentProject\.current, projectEpoch\.current\)/)
  assert.match(source, /setPendingSheetActions\(\{\}\)/)
  assert.match(source, /setPendingFreshJobIds\(\[\]\)/)
  assert.match(source, /jobId => confirmReconnectedJob\(/)
  assert.doesNotMatch(source, /setInterval/)
  assert.match(source, /workspace\.name === project && workspace\.unlocked === false/)
  assert.match(source, /enabled: open && !browsingUploads && !projectExplicitlyLocked/)
  assert.match(source, /Unlock this project to create or manage references/)
  assert.match(source, /useLayoutEffect\(\(\) => \{\s+if \(!projectExplicitlyLocked\) return\s+projectEpoch\.current \+= 1/)
  assert.match(source, /export function ProjectReferenceLibrary\(\{ active \}: \{ active: boolean \}\)/)
  assert.match(source, /aria-hidden=\{!active\}/)
  assert.match(source, /hidden=\{!active\}/)
  assert.match(source, /inert=\{!active \? true : undefined\}/)
  assert.doesNotMatch(source, /createPortal\(|aria-modal="true"|installModalFocus/)
  assert.match(source, /aria-pressed=\{assetType === option\.value\}/)
  assert.match(source, /aria-label="Reference name"/)
  for (const [id, label] of [
    ['project-reference-name', 'Name'],
    ['project-reference-description', 'Description'],
  ]) {
    assert.match(source, new RegExp(`htmlFor="${id}"[^>]*>${label}`))
    assert.match(source, new RegExp(`id="${id}"`))
  }
  assert.match(source, /assetType === 'character' && \([\s\S]*?Character profile · optional/)
  assert.match(source, /id="project-reference-character-gender"/)
  assert.match(source, /id="project-reference-character-age"[\s\S]*?type="number"[\s\S]*?min=\{0\}[\s\S]*?max=\{999\}[\s\S]*?step=\{1\}/)
  assert.match(source, /aria-describedby="project-reference-character-profile-help"/)
  assert.match(source, /grid grid-cols-1 gap-2 sm:grid-cols-2/)
  assert.match(source, /grid grid-cols-1 gap-1 sm:grid-cols-3/)
  assert.match(source, /Breasts · front \+ profile/)
  assert.match(source, /\['vulva', 'Vulva'\]/)
  assert.match(source, /\['penis', 'Penis'\]/)
  assert.doesNotMatch(source, /cpref000000|breasts_front|breasts_profile|commitment_nonce|tombstoned/)
  assert.match(source, /aria-label="Editable reference sections"/)
  assert.match(source, /Customized · pinned/)
  assert.match(source, /section\.values\.some\(value => value\.id === item\.id\)/)
  assert.match(source, /changeDepth\(option\.value\)/)
  assert.match(source, /if \(section\.pinned\) return section/)
  assert.match(source, /id="project-reference-sheet-count"[^>]*min=\{1\} max=\{5\}/)
  assert.match(source, /aria-label="Reference pack plan preview"/)
  assert.match(source, /fetchProjectReferenceCapabilities\(project\)/)
  assert.match(source, /authoritativeTypeCapabilities\?\.presets\.map\(option => \(/)
  assert.match(source, /authoritativeTypeCapabilities\.type_fields\.flatMap/)
  assert.match(source, /authoritativePreset\?\.ordered_roles/)
  assert.match(source, /Authoritative ordered roles are unavailable; generation is disabled/)
  assert.match(source, /Anatomy \/ Nude/)
  assert.match(source, /underwear \/ underlayers/)
  assert.match(source, /individual garments/)
  assert.match(source, /Keep anatomy anchor private and blurred/)
  assert.match(source, /Exact intent never reconstructs absent identity detail/)
  assert.match(source, /operation === 'reconstruct' && intent === 'exact_spec'/)
  assert.match(source, /crypto\.randomUUID\(\)\.replaceAll\('-', ''\)/)
  assert.match(source, /aria-label=\{`Edit custom detail:/)
  assert.match(source, /Remove custom detail/)
  assert.match(source, /item\.label\.toLowerCase\(\) === value\.toLowerCase\(\)/)
  assert.match(source, /orderSectionValues\(definition/)
  assert.match(source, /role="status" aria-live="polite"/)
  assert.match(source, /Source sheet/)
  assert.match(source, /breasts \(front\) and breasts \(profile\)/)
  assert.match(source, /Source Sheet chooses the authored pack sheet to crop from/)
  assert.match(source, /Operation chooses whether Maestro auto-selects, crops, enhances, or reconstructs/)
  assert.match(source, /validDetailSourceRoles\.map/)
  assert.match(source, /Draft does not create editor-dependent detail outputs/)
  assert.match(source, /detailCallouts\.length} detail/)
  assert.doesNotMatch(source, /function detailSourceRole/)
  assert.match(source, /const detailCallouts = sheetMode === 'draft' \? \[\] : authoredDetailCallouts/)
  assert.match(source, /sourceAuthoredSnapshot\?\.detailCallouts[\s\S]*?authoredDetailCallouts/)
  assert.match(source, /authored_settings_seal: sourceAuthoredSnapshot \? sourceAuthoredSeal : undefined/)
  assert.match(source, /authoredSettingsSnapshots\.current\.clear\(\)/)
  assert.match(source, /fetchModels\(\)/)
  assert.match(source, /getProjectReferenceGenerationModels\(catalogModels\)/)
  assert.match(source, /getProjectReferenceEditorModels\(catalogModels\)/)
  assert.match(source, /aria-label="Reference generation model"/)
  assert.match(source, /aria-label="Reference editor model"/)
  assert.match(source, /Open Settings → System → Enabled Models/)
  assert.match(source, /model\.manual_installation\.filename/)
  assert.match(source, /manualInstallationDestination\(model\.manual_installation\)/)
  assert.match(source, /model\.manual_installation\.sha256/)
  assert.match(source, /model\.manual_installation\.local_verification_required/)
  assert.match(source, /Local host only · required/)
  assert.match(source, /Open exact manual download/)
  assert.match(source, /Verification is intentionally unavailable from LAN sessions/)
  assert.match(source, /fetchLoraDetails\(referenceModelType\)/)
  assert.match(source, /parameter_schema_digest: schema\?\.schema_digest/)
  assert.match(source, /parameter_values: schema \? getLoraParameterDefaults\(schema\)/)
  assert.match(source, /<LoraParameterFields/)
  assert.match(source, /schema\.trigger_disclosure\.activation_phrases\.map/)
  assert.match(source, /Known activation phrases/)
  assert.match(source, /LoRA multiplier remains a separate strength control/)
  assert.match(source, /aria-invalid=\{fieldErrors\.length > 0\}/)
  assert.match(source, /const describedBy = \[parameter\.description \? helpId/)
  assert.match(source, /loraParameterSnapshots\.current\.set/)
  assert.match(source, /response\.additional_loras/)
  assert.match(source, /privateLora\?\.parameter_schema_digest === recorded\.schemaDigest/)
  assert.match(source, /privateLora\.parameter_values_digest === recorded\.valuesDigest/)
  assert.match(source, /privateLora\.parameter_expansion_digest === recorded\.expansionDigest/)
  assert.match(source, /privateIds\.every\(\(id, index\) => id === recorded\.ids\[index\]\)/)
  assert.match(source, /Retry private replay/)
  assert.match(source, /its published parameter schema changed/)
  assert.match(source, /values will not be guessed or migrated/)
  assert.match(source, /aria-label="Reference planning model"/)
  assert.match(source, /aria-label="Reference visual review model"/)
  assert.match(source, /Auto \(local only\)/)
  assert.match(source, /Auto local/)
  assert.match(source, /llmCatalogModels\.filter\(model => model\.loaded === true\)/)
  assert.match(source, /model\.vision_capable === true && model\.vision_available === true/)
  assert.match(source, /referenceCapabilities\?\.uncensored_auto_review/)
  assert.match(source, /getProjectReferencePreferredGenerationModel\(/)
  assert.doesNotMatch(source, /getProjectReferenceExplicitConvenienceState|initialExplicitConvenience/)
  assert.match(source, /const \[referenceExplicitOutput, setReferenceExplicitOutput\]/)
  assert.match(source, /const \[explicitConvenience, setExplicitConvenience\] = useState\(false\)/)
  assert.match(source, /const \[characterGender, setCharacterGender\]/)
  assert.match(source, /const \[characterAge, setCharacterAge\] = useState\(''\)/)
  assert.match(source, /const \[characterExplicitAnatomy, setCharacterExplicitAnatomy\]/)
  assert.match(source, /const applyExplicitConvenience[\s\S]*?setReferenceExplicitOutput\(true\)/)
  assert.match(source, /setReferenceExplicitOutput\(enabled\)[\s\S]*?if \(!enabled\) setExplicitConvenience\(false\)/)
  assert.match(source, /const changeDepth[\s\S]*?assetType === 'character' && explicitConvenience[\s\S]*?selectCanonicalCharacterAnatomy/)
  assert.match(source, /const changeCustomSheetCount[\s\S]*?assetType === 'character' && explicitConvenience[\s\S]*?selectCanonicalCharacterAnatomy/)
  assert.match(source, /selectCanonicalCharacterAnatomy\(/)
  assert.match(source, /serializeProjectReferenceCharacterProfile\(/)
  assert.match(source, /invalid_character_age:/)
  assert.match(source, /explicit_convenience_age:/)
  assert.match(source, /too_many_detail_callouts:/)
  assert.match(source, /setExplicitConvenience\(false\)/)
  assert.match(source, /referenceModelCustomized\) return selectProjectReferenceModel\(referenceModels, current\)/)
  assert.match(source, /referenceCapabilities\?\.review_policy/)
  assert.match(source, /isProjectReferenceReviewMandatory\(/)
  assert.match(source, /mandatoryReview && reviewModel === 'off'/)
  assert.match(source, /value="off" disabled=\{mandatoryReview\}/)
  assert.match(source, /Vision fidelity review is required for unrestricted or explicit output and cannot be turned off/)
  assert.match(source, /model\.id === uncensoredReviewContract\?\.resolved_model/)
  assert.match(source, /\(model\.provider \?\? 'local'\) === uncensoredReviewContract\?\.resolved_provider/)
  assert.match(source, /intelligencePolicy === 'uncensored_auto'[\s\S]*?uncensoredReviewCatalogModel \? \[uncensoredReviewCatalogModel\] : \[\]/)
  assert.match(source, /!uncensoredReviewContract\?\.queue_ready \|\| !uncensoredReviewSelectionValid/)
  assert.match(source, /reviewModel !== 'auto_local' && reviewModel !== 'off' && !exactLocalSelection/)
  assert.match(source, /aria-label="Required visual reviewer setup"/)
  assert.match(source, /MMProj: \{uncensoredReviewContract\.projector_available/)
  assert.match(source, /not loaded; automatic load available/)
  assert.match(source, /no generic or remote fallback/)
  assert.match(source, /getProjectReferenceReviewerAction\(/)
  assert.match(source, /loadRequired && \(!machineControls \|\| !modelId\)/)
  assert.match(source, /await loadLlm\(\{ model_id: modelId, provider: 'local' \}\)/)
  assert.match(source, /Promise\.all\(\[\s*fetchLlmModels\(project\),\s*fetchProjectReferenceCapabilities\(project\)/)
  assert.match(source, /isProjectAssetOperationCurrent\(\s*submittedProject, epoch, currentProject\.current, projectEpoch\.current/)
  assert.match(clientSource, /Install \/ load required reviewer/)
  assert.match(clientSource, /Reload required reviewer/)
  assert.match(source, /Refresh reviewer status/)
  assert.match(source, /LAN sessions can refresh status but cannot change the local model runtime/)
  assert.match(source, /Could not install or load the required reviewer/)
  assert.match(source, /intelligencePolicy === 'standard_auto' && selectedReviewModel/)
  assert.match(source, /const queueBlockers = getProjectReferenceQueueBlockers\(/)
  assert.match(source, /const visibleQueueBlockers = queueBlockers\.filter\(blocker => blocker\.id !== 'submitting'\)/)
  assert.match(source, /disabled=\{queueBlockers\.length > 0\}/)
  assert.match(source, /aria-describedby=\{visibleQueueBlockers\.length > 0 \? 'project-reference-queue-blockers'/)
  assert.match(source, />Queue blocked by</)
  assert.match(source, /Automatic · unavailable/)
  assert.match(source, /content_capability: contentCapability/)
  assert.match(source, /review: mandatoryReview \|\| reviewModel !== 'off'/)
  assert.match(source, /initial_blur: initialBlur/)
  assert.match(source, /intelligence_policy: intelligencePolicy/)
  assert.match(source, /additional_loras: additionalLoras/)
  assert.match(source, /character_profile: assetType === 'character'[\s\S]*?characterProfileSerialization\.profile/)
  assert.match(source, /explicit_convenience: assetType === 'character'[\s\S]*?explicitConvenience/)
  const freshRequest = source.slice(source.indexOf('const generate = async'), source.indexOf('const generateFromVariant = async'))
  assert.doesNotMatch(freshRequest, /managed_character_callouts:/)
  assert.match(source, /normalizeProjectReferenceAnchorPrivacy\(/)
  assert.match(source, /Anchor privacy:/)
  assert.match(source, /Operation routing:/)
  assert.match(source, /route\.requested_model/)
  assert.match(source, /route\.resolved_model/)
  assert.match(source, /requested_capability/)
  assert.match(source, /route\.schedule/)
  assert.match(source, /route\.recipe_id/)
  assert.match(source, /route\.verification_status/)
  assert.match(source, /route\.reason/)
  assert.match(source, /fetchLoraDetails\(referenceModelType\)/)
  assert.match(source, /fetchLoraDetails\(editorModelType\)/)
  assert.match(source, /Auto compatible/)
  assert.match(source, /Create \/ anchor/)
  assert.match(source, /Edit \/ derivative/)
  assert.doesNotMatch(source, /selectedModelPerMode/)
  assert.match(source, /aria-label=\{`Import media for \$\{asset\.name\}`\}/)
  assert.doesNotMatch(source, /accept="image\/\*,video\/\*"\s+className="hidden"/)
  assert.match(source, /aria-expanded=\{editing\}/)
  assert.match(source, /htmlFor=\{`reference-sheet-edit-instruction-/)
  assert.match(source, /className="grid min-h-0 grid-cols-1"/)
  assert.doesNotMatch(source, /md:grid-cols-\[320px_1fr\]/)

  assert.match(source, /const applyOutputs = getProjectAssetApplyOutputs\(variant\)/)
  assert.match(source, /void applyReference\(asset, variant\)/)
  assert.doesNotMatch(source, /applyReference\(asset, variant, /)
  assert.match(source, /getProjectAssetComponentOutputs\(variant\)/)
  assert.match(source, /const repair = getProjectReferenceRepairCopy\(metadata\)/)
  assert.match(source, /getProjectReferenceRetrySettings\(variant/)
  assert.match(source, /projectReferenceRetryNeedsPrivateAuthoring\(variant\)/)
  assert.match(source, /fetchProjectReferenceAuthoring\(/)
  assert.match(source, /response\.authored_settings\.seal !== target\.authoredSeal/)
  assert.match(source, /isProjectReferenceCharacterReplayReady\(/)
  assert.match(source, /response\.authored_settings\.character_profile/)
  assert.match(source, /response\.authored_settings\.explicit_convenience/)
  assert.doesNotMatch(source, /response\.authored_settings\.managed_character_callouts/)
  assert.match(source, /Exact private authoring is unavailable for this candidate/)
  assert.match(source, /disabled=\{Boolean\(pendingAction\) \|\| !exactRetryReady\}/)
  assert.match(source, /resolveProjectReferenceRetryReview\(/)
  assert.match(source, /if \(!retryReview\.ready\)/)
  assert.match(source, /The recorded reviewer is unavailable; Retry or Edit will use the current compatible reviewer/)
  assert.match(source, /style, profile, custom fields, and details are never silently dropped/)
  assert.match(source, /const sourcePreset = sourceAssetType === assetType/)
  assert.match(source, /asset_type: sourceSettings\.asset_type/)
  assert.match(source, /mode: sourceSettings\.mode/)
  assert.match(source, /max_repair_attempts: sourceSettings\.max_repair_attempts/)
  assert.match(source, /sourceSettings\.schema_version === 2[\s\S]*?sourceSettings\.mode !== 'draft'/)
  assert.match(source, /private_output: sourceSettings\.private_output/)
  assert.match(source, /character_profile: sourceSettings\.character_profile/)
  assert.doesNotMatch(source, /managed_character_callouts: sourceSettings\.managed_character_callouts/)
  assert.match(source, /explicit_convenience: sourceSettings\.explicit_convenience/)
  assert.match(source, /provenance: 'imported'/)
  assert.match(source, /max_repair_attempts: effectiveMaxRepairAttempts/)
  assert.match(source, /id="project-reference-max-repairs" aria-label="Maximum panel repair attempts" type="number" min=\{1\} max=\{5\}/)
  assert.match(source, /disabled=\{queueBlockers\.length > 0\}/)
  assert.doesNotMatch(source, /!name\.trim\(\) \|\| !description\.trim\(\)/)
  assert.match(source, /const \[loadError, setLoadError\] = useState\(''\)/)
  assert.match(source, /const \[actionError, setActionError\] = useState\(''\)/)
  assert.match(source, /setAssets\(next\)[\s\S]*?setLoadError\(''\)/)
  assert.doesNotMatch(source, /setAssets\(next\)[\s\S]{0,800}setActionError\(''\)/)
  assert.match(source, /role="alert"/)
  assert.match(source, /aria-label="Dismiss project reference error"/)
  assert.match(source, /modelLoadError && <p role="status"/)
  assert.match(source, /source mode, model, privacy, and repair policy were preserved;/)
  assert.match(source, /preserves recorded style, source mode, resolved model pair, privacy, repair, planning, and review policy/)
  assert.doesNotMatch(source, /One bounded repair/)
  assert.equal(source.match(/setActionError\(''\)/g)?.length, 3)
  assert.doesNotMatch(source, /job\?\.error/)
  assert.match(source, /projectReferenceSafeErrorMessage\(reason/)
  assert.doesNotMatch(source, /localStorage/)
})

test('Reference peer, catalog races, Moody cards, manifests, and Blender contract stay explicit', async () => {
  const [source, sidebar, selector, blender, store, manualInstallation] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(sidebarUrl, 'utf8'),
    readFile(modelSelectorUrl, 'utf8'),
    readFile(blenderUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(manualInstallationUrl, 'utf8'),
  ])

  assert.equal(sidebar.match(/<ProjectReferenceLibrary active=\{isReference && sidebarOpen\} \/>/g)?.length, 1)
  assert.equal(sidebar.match(/<ProjectReferenceLibrary active=\{isReference\} \/>/g)?.length, 1)
  assert.doesNotMatch(sidebar, /sidebarOpen && <ProjectReferenceLibrary/)
  assert.match(source, /const open = active/)
  for (const label of ['Generate', 'Director', 'Reference']) {
    assert.match(sidebar, new RegExp(`>\\s*${label}\\s*<`))
  }
  assert.match(sidebar, /role="group" aria-label="Creative workspace"/)
  assert.match(sidebar, /disabled=\{!activeWorkspace \|\| browsingUploads \|\| referenceLocked\}/)
  assert.match(sidebar, /w-\[clamp\(460px,24vw,560px\)\]/)
  assert.match(sidebar, /grid-cols-\[minmax\(0,1fr\)_auto\]/)
  assert.match(sidebar, /col-span-2 min-w-0/)
  assert.match(source, /export function ProjectReferenceLibrary\(\{ active \}: \{ active: boolean \}\)/)
  assert.match(source, /enabledModelsSignature/)
  assert.match(source, /catalogRequestSequence/)
  assert.match(source, /enabledModelsSignature, modelsLoaded, open, project, projectExplicitlyLocked/)
  assert.match(source, /catalogSequence !== catalogRequestSequence\.current/)
  assert.match(source, /if \(active && projectExplicitlyLocked\) setSidebarMode\(referenceReturnMode\)/)
  assert.doesNotMatch(source, /createPortal\(|installModalFocus\(|aria-haspopup="dialog"/)
  assert.match(source, /create and manage reusable reference packs/)

  const nameIndex = source.indexOf('id="project-reference-name"')
  const intentIndex = source.indexOf('>Intent</legend>')
  assert.ok(nameIndex >= 0 && intentIndex > nameIndex, 'name must precede intent controls')
  assert.match(source, /aria-label="Moody Krea 2 quick select"/)
  assert.match(source, /Disabled in Enabled Models/)
  assert.match(source, /Missing from current catalog/)
  assert.match(source, /Install and verify locally/)
  assert.match(source, /setReferenceModelCustomized\(true\)/)
  assert.match(source, /getProjectReferencePreferredGenerationModel\(/)
  assert.match(source, /referenceExplicitOutput, contentCapability, referenceCapabilities/)

  const freshGenerationStart = source.indexOf('const generate = async () =>')
  const retryGenerationStart = source.indexOf('const generateFromVariant = async (')
  const freshGeneration = source.slice(freshGenerationStart, retryGenerationStart)
  const retryGeneration = source.slice(retryGenerationStart, source.indexOf('const importVariant = async (', retryGenerationStart))
  for (const [label, flow] of [['fresh generation', freshGeneration], ['Retry/Edit', retryGeneration]]) {
    const postIndex = flow.indexOf('const response = await generateProjectAssetReferences(')
    const queueViewIndex = flow.indexOf('requestQueueView()', postIndex)
    const reconnectIndex = flow.indexOf('await confirmAcceptedProjectReferenceJob(')
    const acceptedRefreshIndex = flow.indexOf('requestRefresh()', queueViewIndex)
    assert.ok(postIndex >= 0 && queueViewIndex > postIndex && acceptedRefreshIndex > queueViewIndex && reconnectIndex > acceptedRefreshIndex,
      `${label} must open Queue and publish accepted state before read-only confirmation`)
    assert.equal(flow.match(/requestQueueView\(\)/g)?.length, 1, `${label} must request Queue exactly once`)
    assert.doesNotMatch(flow, /setSidebarMode\(/, `${label} must keep the Reference peer mounted`)
  }
  const freshAccepted = freshGeneration.slice(
    freshGeneration.indexOf('requestQueueView()'),
    freshGeneration.indexOf('await confirmAcceptedProjectReferenceJob('),
  )
  assert.match(freshAccepted, /setName\(''\)/)
  assert.match(freshAccepted, /setDescription\(''\)/)
  for (const retainedSetter of [
    'setVisualStyle', 'setCustomVisualStyle', 'setCandidateKind', 'setAssetType',
    'setSheetMode', 'setIntent', 'setDepth', 'setCustomSheetCount', 'setPreset',
    'setSections', 'setPlanningModel', 'setReviewModel', 'setReferenceExplicitOutput',
    'setExplicitConvenience', 'setCharacterGender', 'setCharacterAge', 'setCharacterExplicitAnatomy',
    'setContentCapability', 'setInitialBlur', 'setIntelligencePolicy',
    'setGenerationLoras', 'setEditingLoras', 'setAdditionalLoras',
    'setAnatomyPrivate', 'setCandidateCount', 'setColumns', 'setPaletteSwatches',
    'setMaxRepairAttempts', 'setReferenceModelType', 'setEditorModelType',
  ]) {
    assert.doesNotMatch(freshAccepted, new RegExp(`${retainedSetter}\\(`), `${retainedSetter} must remain the next-run default`)
  }
  assert.doesNotMatch(retryGeneration, /setName\(|setDescription\(|setVisualStyle\(|setCustomVisualStyle\(/)
  assert.match(source, /import \{ requestQueueView \} from '\.\.\/\.\.\/lib\/mainViewNavigation'/)
  assert.match(source, /manualInstallationDestination\(model\.manual_installation\)/)
  assert.match(selector, /manualInstallationDestination\(currentModel\.manual_installation\)/)
  assert.match(manualInstallation, /formatManualInstallationBytes/)
  assert.match(manualInstallation, /manualInstallationDestination/)
  assert.match(selector, /Open exact manual download/)
  assert.match(selector, /Local-only verification:/)
  assert.match(store, /manual_installation: m\.manual_installation/)

  assert.match(source, /aria-label="Reference creation method"/)
  assert.match(source, /Image Reference Pack/)
  assert.match(source, /Blender Motion Video/)
  assert.match(source, /Create, preview, Keep, and apply a structured motion\/camera reference to Generate/)
  assert.match(source, /referenceName=\{name\}/)
  assert.match(source, /referenceDescription=\{description\}/)
  assert.match(source, /privateOutput=\{referenceExplicitOutput \|\| privateOutput\}/)
  assert.match(source, /separate from the durable Character, Location, Wardrobe/)
  assert.match(blender, /reference_name: resolvedReferenceName/)
  assert.match(blender, /private_output: privateOutput/)
  assert.match(blender, /statusRequest/)
  assert.match(blender, /operationSequence/)
  assert.match(blender, /isOperationCurrent\(operation\)/)
  assert.match(blender, /await refreshOutputs\(\)/)
  assert.match(blender, /setDirectorPlan\(null\)/)
  assert.match(blender, /workspaceRef\.current === operation\.workspace/)
  assert.match(blender, /separate reference contract/)
  assert.match(blender, /Keep motion video/)
})

test('Reference reviewer readiness auto-refresh is bounded, exact, and lifecycle-fenced', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const helperStart = source.indexOf('interface ProjectReferenceReviewerAutoRefreshInput')
  const helperEnd = source.indexOf('\nconst REVIEWER_AUTO_REFRESH_DELAYS_MS', helperStart)
  assert.ok(helperStart >= 0 && helperEnd > helperStart, 'reviewer auto-refresh helper must remain extractable')
  const compiledHelper = ts.transpileModule(`${source.slice(helperStart, helperEnd)}\nexport { shouldAutoRefreshProjectReferenceReviewer }`, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  const helperModule = await import(`data:text/javascript;base64,${Buffer.from(compiledHelper).toString('base64')}`)
  const shouldRefresh = helperModule.shouldAutoRefreshProjectReferenceReviewer
  const loading = { setup_state: 'loading', queue_ready: false }
  const loadedWithoutVision = { setup_state: 'loaded_without_vision', queue_ready: false }
  const base = {
    active: true,
    pageVisible: true,
    projectLocked: false,
    intelligencePolicy: 'uncensored_auto',
    reviewerAction: null,
    contract: loading,
  }
  assert.equal(shouldRefresh(base), true)
  assert.equal(shouldRefresh({ ...base, contract: loadedWithoutVision }), true)
  assert.equal(shouldRefresh({ ...base, active: false }), false)
  assert.equal(shouldRefresh({ ...base, pageVisible: false }), false)
  assert.equal(shouldRefresh({ ...base, projectLocked: true }), false)
  assert.equal(shouldRefresh({ ...base, intelligencePolicy: 'standard_auto' }), false)
  assert.equal(shouldRefresh({ ...base, reviewerAction: 'loading' }), false)
  assert.equal(shouldRefresh({ ...base, contract: undefined }), false)
  for (const setup_state of ['missing_model', 'missing_projector', 'ready_unloaded', 'ready_resident']) {
    assert.equal(shouldRefresh({ ...base, contract: { setup_state, queue_ready: setup_state.startsWith('ready_') } }), false)
  }

  assert.match(source, /const REVIEWER_AUTO_REFRESH_DELAYS_MS = \[750, 1_500, 3_000, 6_000, 12_000\] as const/)
  assert.match(source, /document\.visibilityState !== 'hidden'/)
  assert.match(source, /document\.addEventListener\('visibilitychange', syncVisibility\)/)
  assert.match(source, /document\.removeEventListener\('visibilitychange', syncVisibility\)/)
  const effectStart = source.indexOf('if (!reviewerNeedsAutomaticRefresh) return')
  const effectEnd = source.indexOf('}, [project, reviewerNeedsAutomaticRefresh])', effectStart)
  const effect = source.slice(effectStart, effectEnd)
  assert.match(effect, /fetchLlmModels\(submittedProject\)/)
  assert.match(effect, /fetchProjectReferenceCapabilities\(submittedProject\)/)
  assert.match(effect, /sequence === reviewerAutoRefreshSequence\.current/)
  assert.match(effect, /isProjectAssetOperationCurrent\([\s\S]*?submittedProject, epoch, currentProject\.current, projectEpoch\.current/)
  assert.match(effect, /attempt >= REVIEWER_AUTO_REFRESH_DELAYS_MS\.length/)
  assert.match(effect, /missing_model[\s\S]*?missing_projector[\s\S]*?ready_unloaded[\s\S]*?ready_resident/)
  assert.match(effect, /clearTimeout\(timeoutId\)/)
  assert.doesNotMatch(effect, /setReviewerAction\(|setReviewerActionError\(/)
  assert.match(source, /Refresh reviewer status/)
})

test('accepted Reference submissions retry read-only confirmation without duplicate POSTs or red busy copy', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const previousWindow = globalThis.window
  const navigationTarget = new EventTarget()
  globalThis.window = navigationTarget
  try {
    const { OPEN_QUEUE_VIEW_EVENT, requestQueueView } = await import(mainViewNavigationUrl.href)
    let queueRequests = 0
    navigationTarget.addEventListener(OPEN_QUEUE_VIEW_EVENT, event => {
      queueRequests += 1
      assert.equal(event.constructor, Event, 'Queue navigation stays fixed and payload-free')
    })
    requestQueueView()
    assert.equal(queueRequests, 1)
  } finally {
    globalThis.window = previousWindow
  }
  const helperStart = source.indexOf('const PROJECT_REFERENCE_CONFIRMATION_DELAYS_MS')
  const helperEnd = source.indexOf('\nconst REFERENCE_TYPE_DEFINITIONS', helperStart)
  assert.ok(helperStart >= 0 && helperEnd > helperStart, 'accepted-job confirmation helper must remain extractable')
  const compiledHelper = ts.transpileModule(`${source.slice(helperStart, helperEnd)}\nexport { confirmAcceptedProjectReferenceJob }`, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  const helperModule = await import(`data:text/javascript;base64,${Buffer.from(compiledHelper).toString('base64')}`)
  const confirmAccepted = helperModule.confirmAcceptedProjectReferenceJob

  let attempts = 0
  const waits = []
  assert.equal(await confirmAccepted(
    'accepted-job',
    async jobId => {
      assert.equal(jobId, 'accepted-job')
      attempts += 1
      if (attempts < 3) throw new Error('Queue has not caught up yet')
    },
    async delayMs => { waits.push(delayMs) },
  ), true)
  assert.equal(attempts, 3)
  assert.deepEqual(waits, [250, 750])

  attempts = 0
  waits.length = 0
  assert.equal(await confirmAccepted(
    'accepted-but-confirming',
    async () => {
      attempts += 1
      throw new Error('still reconnecting')
    },
    async delayMs => { waits.push(delayMs) },
  ), false)
  assert.equal(attempts, 4)
  assert.deepEqual(waits, [250, 750, 1_500])

  attempts = 0
  assert.equal(await confirmAccepted(
    'accepted-with-hung-reconnect',
    async () => {
      attempts += 1
      return new Promise(() => {})
    },
    async () => {},
    1,
  ), false)
  assert.equal(attempts, 4, 'a hung reconnect remains attempt- and wall-clock-bounded')

  const freshStart = source.indexOf('const generate = async () =>')
  const retryStart = source.indexOf('const generateFromVariant = async (', freshStart)
  const retryEnd = source.indexOf('const importVariant = async (', retryStart)
  const freshFlow = source.slice(freshStart, retryStart)
  const retryFlow = source.slice(retryStart, retryEnd)
  for (const [label, flow] of [['fresh generation', freshFlow], ['Retry/Edit', retryFlow]]) {
    const requestIdIndex = flow.indexOf('const requestId = createProjectReferenceRequestId()')
    const postIndex = flow.indexOf('const response = await generateProjectAssetReferences(')
    assert.ok(requestIdIndex >= 0 && postIndex > requestIdIndex, `${label} must mint one request ID per deliberate submission`)
    assert.equal(flow.match(/createProjectReferenceRequestId\(\)/g)?.length, 1)
    assert.match(flow, /request_id: requestId/)
    assert.equal(flow.match(/generateProjectAssetReferences\(/g)?.length, 1, `${label} must POST exactly once`)
    assert.equal(flow.match(/confirmAcceptedProjectReferenceJob\(/g)?.length, 1, `${label} must use one bounded confirmation loop`)
    assert.match(flow, /jobId => confirmReconnectedJob\(/)
    assert.match(flow, /Queue confirmation is still catching up/)
  }
  assert.match(source, /const PROJECT_REFERENCE_CONFIRMATION_ATTEMPT_TIMEOUT_MS = 1_500/)
  assert.match(source, /Promise\.race\(\[/)
  assert.match(source, /if \(timeoutId !== null\) clearTimeout\(timeoutId\)/)
  assert.doesNotMatch(source, /A reference pack is already being submitted\./)
  assert.match(source, /visibleQueueBlockers\.map\(blocker => <li/)
  assert.match(source, /disabled=\{queueBlockers\.length > 0\}/)
  assert.match(source, /\{submitting \? <Loader2/)
})

test('Reference creation methods are reversible across every semantic type and sheet mode', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const helperStart = source.indexOf('type ProjectReferenceCreationMethod')
  const helperEnd = source.indexOf('\nconst REFERENCE_TYPE_DEFINITIONS', helperStart)
  assert.ok(helperStart >= 0 && helperEnd > helperStart, 'creation transition helper must remain extractable')
  const compiledHelper = ts.transpileModule(`${source.slice(helperStart, helperEnd)}\nexport { getProjectReferenceCreationTransition, getProjectReferenceCreationPanelStates }`, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  const helperModule = await import(`data:text/javascript;base64,${Buffer.from(compiledHelper).toString('base64')}`)
  const transition = helperModule.getProjectReferenceCreationTransition
  const panelStates = helperModule.getProjectReferenceCreationPanelStates
  assert.equal(typeof transition, 'function')
  assert.deepEqual(panelStates('image_pack'), {
    image_pack: { hidden: false, inert: undefined },
    blender_motion: { hidden: true, inert: true },
  })
  assert.deepEqual(panelStates('blender_motion'), {
    image_pack: { hidden: true, inert: true },
    blender_motion: { hidden: false, inert: undefined },
  })

  const assetTypes = ['character', 'location', 'prop', 'vehicle', 'creature', 'wardrobe', 'world']
  const sheetModes = ['production', 'hybrid', 'draft']
  for (const [assetIndex, assetType] of assetTypes.entries()) {
    for (const sheetMode of sheetModes) {
      const authoredState = {
        candidateKind: 'image_pack',
        assetType,
        sheetMode,
        preset: `authored-${assetType}`,
        sections: [{ id: 'custom', values: [`${assetType}-${sheetMode}`] }],
        name: `${assetType} reference`,
        description: `${sheetMode} authored description`,
        visualStyle: 'cinematic',
      }
      const apply = (state, event) => ({
        ...state,
        ...transition(
          { candidateKind: state.candidateKind, assetType: state.assetType },
          event,
        ),
      })

      const blender = apply(authoredState, { kind: 'select_method', candidateKind: 'blender_motion' })
      assert.equal(blender.candidateKind, 'blender_motion')
      assert.equal(blender.assetType, assetType)
      for (const field of ['sheetMode', 'preset', 'sections', 'name', 'description', 'visualStyle']) {
        assert.deepEqual(blender[field], authoredState[field], `${assetType}/${sheetMode} preserves ${field}`)
      }

      const sameTypeReturnsToImages = apply(blender, { kind: 'select_asset_type', assetType })
      assert.equal(sameTypeReturnsToImages.candidateKind, 'image_pack')
      assert.equal(sameTypeReturnsToImages.assetTypeChanged, false)
      assert.equal(sameTypeReturnsToImages.assetType, assetType)

      const backToBlender = apply(sameTypeReturnsToImages, { kind: 'select_method', candidateKind: 'blender_motion' })
      const differentType = assetTypes[(assetIndex + 1) % assetTypes.length]
      const changedType = apply(backToBlender, { kind: 'select_asset_type', assetType: differentType })
      assert.equal(changedType.candidateKind, 'image_pack')
      assert.equal(changedType.assetTypeChanged, true)
      assert.equal(changedType.assetType, differentType)

      const imageAgain = apply(sameTypeReturnsToImages, { kind: 'select_method', candidateKind: 'image_pack' })
      assert.equal(imageAgain.candidateKind, 'image_pack')
      assert.equal(imageAgain.assetTypeChanged, false)
      assert.deepEqual(authoredState, { ...authoredState }, 'leaving and returning without a reset preserves authored state')
    }
  }

  const methodFieldStart = source.indexOf('<fieldset aria-label="Reference creation method"')
  const methodFieldEnd = source.indexOf('</fieldset>', methodFieldStart)
  const methodField = source.slice(methodFieldStart, methodFieldEnd)
  assert.doesNotMatch(methodField, /<details|<summary/)
  assert.equal(methodField.match(/type="button"/g)?.length, 2)
  assert.match(methodField, /aria-pressed=\{candidateKind === 'image_pack'\}/)
  assert.match(methodField, /aria-pressed=\{candidateKind === 'blender_motion'\}/)
  assert.match(source, /id="project-reference-blender-motion-method"[\s\S]*?hidden=\{creationPanelStates\.blender_motion\.hidden\}[\s\S]*?inert=\{creationPanelStates\.blender_motion\.inert\}/)
  assert.match(source, /id="project-reference-image-pack-method"[\s\S]*?hidden=\{creationPanelStates\.image_pack\.hidden\}[\s\S]*?inert=\{creationPanelStates\.image_pack\.inert\}/)
  assert.doesNotMatch(source, /candidateKind === 'blender_motion' \? \(/)
  assert.equal(source.match(/<BlenderSceneTool/g)?.length, 1)
  assert.match(source, /setCandidateKind\('image_pack'\)[\s\S]*?setAssetType\('character'\)/)
  assert.equal(source.match(/setCandidateKind\('image_pack'\)/g)?.length, 2, 'only project and lock resets canonicalize the method')
  assert.match(source, /setCandidateKind\(transition\.candidateKind\)\s+if \(!transition\.assetTypeChanged\) return/)
  assert.match(source, /The visual fidelity reviewer checks identity, anatomy, layout, style adherence, and retry quality\./)
  assert.match(source, /It does not classify or censor content or decide whether a request is allowed\./)
})

test('Reference and Director expose style, skill, flow, and truthful Blender choices', async () => {
  const [reference, director, advancedSettings, store, client] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(directorUrl, 'utf8'),
    readFile(advancedSettingsUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(clientUrl, 'utf8'),
  ])

  const construction = reference.indexOf('>Sheet construction mode</legend>')
  const output = reference.indexOf('>Output handling</legend>')
  assert.ok(construction >= 0 && output > construction, 'Construction must precede Output handling')
  assert.match(reference, /aria-label="Reference visual style"/)
  assert.match(reference, />Realistic \(default\)<\/option>/)
  assert.match(reference, /const authoredStyle = visualStyle === 'custom'/)
  assert.match(reference, /style: authoredStyle \|\| undefined/)
  assert.match(reference, /styleCommitment: packMetadata\?\.authored_settings\?\.style_commitment \?\? ''/)
  assert.match(reference, /response\.authored_settings\.style\.trim\(\)\.length === 0/)
  assert.match(reference, /style: sourceAuthoredSnapshot\?\.style/)
  assert.match(reference, /style: sourceSettings\.style \|\| undefined/)
  assert.match(reference, /isProjectReferenceStyleReplayReady\(/)
  assert.match(reference, /Exact private style is unavailable or its commitment changed/)
  assert.match(reference, /use Custom when your own freeform style should be authoritative/)
  assert.match(reference, /section\.values\.length >= 8/)

  assert.match(director, /Welcome to Maestro Director\. Choose a Skill below/)
  assert.match(director, /<fieldset aria-label="Director Skills"/)
  assert.match(director, /<legend[^>]*>Skills<\/legend>/)
  assert.match(director, /aria-label="Director visual style"/)
  assert.match(director, /use Custom when your own freeform style should be authoritative/)
  assert.match(director, /<legend[^>]*>Additional references<\/legend>/)
  assert.match(director, /aria-label="Additional reference methods"/)
  assert.match(director, /Character photos/)
  assert.match(director, /Scene photos/)
  assert.doesNotMatch(director, /reference_video_paths|blender_motion_video_path/)
  assert.match(advancedSettings, /md:left-\[clamp\(460px,24vw,560px\)\]/)
  assert.match(advancedSettings, /md:w-\[min\(380px,calc\(100vw-clamp\(460px,24vw,560px\)\)\)\]/)
  assert.doesNotMatch(advancedSettings, /md:left-\[420px\]/)
  const simulatedAdvancedPanel = viewportWidth => {
    const sidebarWidth = Math.min(560, Math.max(460, viewportWidth * 0.24))
    const panelWidth = Math.min(380, viewportWidth - sidebarWidth)
    return { left: sidebarWidth, width: panelWidth, right: sidebarWidth + panelWidth }
  }
  for (const viewportWidth of [768, 769, 800, 839, 840, 1920, 3840]) {
    const panel = simulatedAdvancedPanel(viewportWidth)
    assert.ok(panel.width > 0, `Advanced panel must remain visible at ${viewportWidth}px`)
    assert.ok(panel.right <= viewportWidth, `Advanced panel must not clip at ${viewportWidth}px`)
  }

  assert.equal(client.match(/visual_style\?: string/g)?.length, 4)
  assert.equal(store.match(/visual_style:/g)?.length, 9)
  assert.match(client, /skill_type: string\s+video_model\?: string\s+\/\*\* Null is the explicit new-role automatic-creator sentinel\. \*\/\s+image_creator_model\?: string \| null\s+image_editor_model\?: string\s+image_creator_loras\?: DirectorImageRoleLoraSelection\[\]\s+image_editor_loras\?: DirectorImageRoleLoraSelection\[\]/)
  assert.match(client, /Legacy combined image wire; never mix with the role fields above\. \*\/\s+image_model\?: string/)
  const v2PlanBodies = [...store.matchAll(/api\.directorV2Plan\(\{([\s\S]*?)\}, \{ signal:/g)].map(match => match[1])
  assert.equal(v2PlanBodies.length, 3)
  for (const body of v2PlanBodies) {
    assert.match(body, /video_model: get\(\)\.selectedModelPerMode\.video/)
    assert.match(body, /\.\.\.imageRoleRequest\.wire/)
    assert.doesNotMatch(body, /image_model:/)
  }
  assert.match(store, /resolveSidebarNavigation\(current, mode\)/)
  assert.match(store, /!current\.directorAudioFile && !transition\.preserveDirectorState/)
  assert.match(reference, /setSidebarMode\(referenceReturnMode\)/)
  assert.match(reference, /destination = 'studio'/)
  assert.match(reference, /setSidebarMode\(destination\)/)
})

test('Reference navigation round trips preserve the originating workspace state', () => {
  const fromDirector = resolveSidebarNavigation({
    sidebarMode: 'director',
    referenceReturnMode: 'studio',
  }, 'reference')
  assert.deepEqual(fromDirector, {
    sidebarMode: 'reference',
    referenceReturnMode: 'director',
    preserveDirectorState: false,
  })

  const backToDirector = resolveSidebarNavigation(fromDirector, 'director')
  assert.deepEqual(backToDirector, {
    sidebarMode: 'director',
    referenceReturnMode: 'director',
    preserveDirectorState: true,
  })

  const fromGenerate = resolveSidebarNavigation({
    sidebarMode: 'studio',
    referenceReturnMode: 'director',
  }, 'reference')
  assert.equal(fromGenerate.referenceReturnMode, 'studio')
  assert.equal(resolveSidebarNavigation(fromGenerate, 'studio').preserveDirectorState, false)
  assert.deepEqual(resolveSidebarNavigation(fromDirector, 'reference'), fromDirector)
})

test('Director reference routing accepts only its currently supported semantic types', () => {
  assert.equal(getDirectorProjectReferenceKind('character'), 'character')
  assert.equal(getDirectorProjectReferenceKind('location'), 'location')
  assert.equal(getDirectorProjectReferenceKind('setting'), 'location')
  for (const unsupported of ['creature', 'wardrobe', 'prop', 'item', 'vehicle', 'world', 'style']) {
    assert.equal(getDirectorProjectReferenceKind(unsupported), null)
  }
})

test('Director v2 client sends selected model and authored style fields to the route', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input)
    calls.push({ url, init })
    if (url.endsWith('/api/v1/llm/prepare')) {
      return new Response(JSON.stringify({ operation_id: 'prepare-model-wire', status: 'ready' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/api/v1/director/v2/plan')) {
      return new Response(JSON.stringify({ clip_plans: [], production_plan: {}, skill_type: 'short_film' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  }

  try {
    await directorV2Plan({
      workspace: 'wire-project',
      skill_type: 'short_film',
      video_model: 'minimax_h3',
      image_model: 'flux2_klein_9b',
      visual_style: 'hand-painted stop motion',
    })
  } finally {
    globalThis.fetch = originalFetch
  }

  const routeCall = calls.find(({ url }) => url.endsWith('/api/v1/director/v2/plan'))
  assert.ok(routeCall, 'Director v2 route must be called')
  assert.deepEqual(JSON.parse(routeCall.init.body), {
    workspace: 'wire-project',
    skill_type: 'short_film',
    video_model: 'minimax_h3',
    image_model: 'flux2_klein_9b',
    visual_style: 'hand-painted stop motion',
  })
})

test('Reference queue confirmation rejects reconnect failures and missing job rediscovery', async () => {
  const { confirmReconnectedJob } = await import(referenceQueueUrl.href)
  await assert.rejects(
    confirmReconnectedJob('job-failure', async () => { throw new Error('network down') }, () => []),
    /network down/,
  )
  await assert.rejects(
    confirmReconnectedJob('job-missing', async () => {}, () => [{ id: 'other-job' }]),
    /could not be confirmed after reconnect/,
  )
  await assert.doesNotReject(
    confirmReconnectedJob('job-present', async () => {}, () => [{ id: 'job-present' }]),
  )
})

test('Blender async work is fenced after unmount and project switches', async () => {
  const blender = await readFile(blenderUrl, 'utf8')

  assert.match(blender, /const mountedRef = useRef\(false\)/)
  assert.match(blender, /mountedRef\.current\s+&& operation/)
  assert.match(blender, /mountedRef\.current = true/)
  assert.match(
    blender,
    /return \(\) => \{\s+mountedRef\.current = false\s+statusRequest\.current \+= 1\s+operationSequence\.current \+= 1\s+activeOperation\.current = null\s+\}/,
  )

  const finalizeGuard = blender.indexOf('if (!isOperationCurrent(operation)) return false', blender.indexOf('const result = await api.finalizeBlenderScene'))
  const finalPlanWrite = blender.indexOf('setDirectorPlan(result.final_plan)', finalizeGuard)
  const finalResultWrite = blender.indexOf('setDirectorFinal(result)', finalPlanWrite)
  const refreshGuard = blender.indexOf('if (!isOperationCurrent(operation)) return false', finalResultWrite)
  const refreshCall = blender.indexOf('await refreshOutputs()', refreshGuard)
  assert.ok(
    finalizeGuard >= 0
      && finalPlanWrite > finalizeGuard
      && finalResultWrite > finalPlanWrite
      && refreshGuard > finalResultWrite
      && refreshCall > refreshGuard,
    'final Director state and output refresh must remain behind the active mounted operation fence',
  )
})
