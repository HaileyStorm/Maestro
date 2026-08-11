import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  addProjectAssetVariant,
  createProjectAsset,
  deleteProjectAssetVariant,
  fetchProjectAssets,
  fetchProjectReferenceAuthoring,
  fetchProjectReferenceCapabilities,
  generateProjectAssetReferences,
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
  isProjectReferenceReviewerEligible,
  isProjectAssetOperationCurrent,
  lockProjectAssetVariantOperation,
  loadLlm,
  loraParameterSchemasConflict,
  normalizeProjectReferenceAssetType,
  normalizeProjectReferenceAnchorPrivacy,
  ProjectAssetRequestError,
  projectAssetOutputNeedsInitialBlur,
  projectAssetVariantOperationKey,
  projectReferenceRetryNeedsPrivateAuthoring,
  projectReferenceSafeErrorMessage,
  resolveProjectReferenceRetryReview,
  selectProjectReferenceModel,
  selectProjectAssetApplyOutput,
  setProjectAssetVariantStatus,
  validateLoraParameterValues,
} from '../src/api/client.ts'

const componentUrl = new URL('../src/components/Sidebar/ProjectReferenceLibrary.tsx', import.meta.url)
const sidebarUrl = new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url)
const modelSelectorUrl = new URL('../src/components/Sidebar/ModelSelector.tsx', import.meta.url)
const blenderUrl = new URL('../src/components/Sidebar/BlenderSceneTool.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)
const referenceQueueUrl = new URL('../src/lib/referenceQueue.ts', import.meta.url)
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
      type_fields: [{ field: 'poses', items: [{ id: 'custom:abcdefghijkl', custom: true, group: 'expressions' }] }],
      detail_callouts: [{
        custom_id: 'custom:mnopqrstuvwx', kind: 'custom', requested_operation: 'crop',
        source_role: 'canonical_identity', target_role: 'detail_callout:custom:mnopqrstuvwx',
        label_digest: 'private-digest',
      }],
    },
  }
  assert.equal(projectReferenceRetryNeedsPrivateAuthoring(source), true)
  const settings = getProjectReferenceRetrySettings(source, {
    mode: 'production', model_type: 'flux2_dev', editor_model_type: 'qwen-edit',
    private_output: false, explicit_output: false, review: false, max_repair_attempts: 0,
    asset_type: 'character', type_fields: {}, detail_callouts: [],
  }, {
    reference_types: [{ id: 'character', type_fields: [], detail_kinds: [] }],
  })
  assert.equal('type_fields' in settings, false)
  assert.equal('detail_callouts' in settings, false)

  const exactSnapshot = getProjectReferenceRetrySettings(source, {
    mode: 'production', model_type: 'flux2_dev', editor_model_type: 'qwen-edit',
    private_output: false, explicit_output: false, review: false, max_repair_attempts: 0,
    asset_type: 'character', authored_settings_seal: 'sealed-private-authoring',
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
    invalid_authored_settings: false, review_unavailable: false,
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
    invalid_authored_settings: false, review_unavailable: false,
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
    mode: 'hybrid',
    candidate_count: 1,
  })
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
      type_fields: { poses: [{
        id: 'custom:abcdefghijkl', label: 'Private expression', custom: true, group: 'expressions',
      }] },
      detail_callouts: [{
        custom_id: 'custom:mnopqrstuvwx', label: 'Private ring detail', kind: 'custom',
        operation: 'enhance', source_role: 'canonical_identity',
      }],
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

test('component source guards lifecycle, accessibility, mobile flow, and sheet-only apply', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const clientSource = await readFile(clientUrl, 'utf8')
  const referenceTypes = await readFile(typesUrl, 'utf8')

  assert.match(referenceTypes, /'private_blurred' \| 'private_visible' \| 'project_blurred' \| 'project_visible'/)
  assert.match(referenceTypes, /ProjectReferenceLegacyAnchorPrivacy = ProjectReferenceAnchorPrivacy \| 'standard'/)
  assert.doesNotMatch(referenceTypes, /anchor_privacy: 'private_blurred' \| 'standard'/)
  assert.match(referenceTypes, /operation_routing: ProjectReferenceOperationRouting/)
  assert.match(referenceTypes, /private_output: boolean/)
  assert.match(referenceTypes, /custom_id: string[\s\S]*?label: string[\s\S]*?operation: ProjectReferenceDetailOperation[\s\S]*?source_role: string/)
  assert.match(referenceTypes, /ProjectReferenceTypeFieldItem\[\]/)
  assert.match(referenceTypes, /detail_callout_count: number/)
  assert.match(referenceTypes, /ordered_output_roles: string\[\]/)
  assert.match(clientSource, /detail\?: \{[\s\S]*?custom_id: string[\s\S]*?source_digest: string[\s\S]*?normalized_crop: \[number, number, number, number\][\s\S]*?label_digest: string[\s\S]*?seal: string/)
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
    'Explicit convenience preset',
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
  assert.match(source, /await confirmReconnectedJob\(/)
  assert.doesNotMatch(source, /setInterval/)
  assert.match(source, /workspace\.name === project && workspace\.unlocked === false/)
  assert.match(source, /disabled=\{browsingUploads \|\| !project\}/)
  assert.match(source, /aria-disabled=\{projectExplicitlyLocked\}/)
  assert.match(source, /enabled: open && !browsingUploads && !projectExplicitlyLocked/)
  assert.match(source, /Unlock project to use references/)
  assert.match(source, /useLayoutEffect\(\(\) => \{\s+if \(!projectExplicitlyLocked\) return\s+projectEpoch\.current \+= 1/)
  assert.match(source, /\{open && !projectExplicitlyLocked && createPortal\(/)

  assert.match(source, /createPortal\(/)
  assert.match(source, /role="dialog" aria-modal="true"/)
  assert.match(source, /aria-label="Close project references"/)
  assert.match(source, /onKeyDown=\{handleDialogKeyDown\}/)
  assert.match(source, /event\.key === 'Escape'/)
  assert.match(source, /event\.key !== 'Tab'/)
  assert.match(source, /element\.getClientRects\(\)\.length > 0/)
  assert.match(source, /aria-pressed=\{assetType === option\.value\}/)
  assert.match(source, /aria-label="Reference name"/)
  for (const [id, label] of [
    ['project-reference-name', 'Name'],
    ['project-reference-description', 'Description'],
  ]) {
    assert.match(source, new RegExp(`htmlFor="${id}"[^>]*>${label}`))
    assert.match(source, new RegExp(`id="${id}"`))
  }
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
  assert.match(source, /aria-label="Reference Studio generation model"/)
  assert.match(source, /aria-label="Reference Studio editor model"/)
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
  assert.match(source, /aria-label="Reference Studio planning model"/)
  assert.match(source, /aria-label="Reference Studio visual review model"/)
  assert.match(source, /Auto \(local only\)/)
  assert.match(source, /Auto local/)
  assert.match(source, /llmCatalogModels\.filter\(model => model\.loaded === true\)/)
  assert.match(source, /model\.vision_capable === true && model\.vision_available === true/)
  assert.match(source, /referenceCapabilities\?\.uncensored_auto_review/)
  assert.match(source, /getProjectReferencePreferredGenerationModel\(/)
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
  assert.match(source, /disabled=\{queueBlockers\.length > 0\}/)
  assert.match(source, /aria-describedby=\{queueBlockers\.length > 0 \? 'project-reference-queue-blockers'/)
  assert.match(source, />Queue blocked by</)
  assert.match(source, /Automatic · unavailable/)
  assert.match(source, /content_capability: contentCapability/)
  assert.match(source, /review: mandatoryReview \|\| reviewModel !== 'off'/)
  assert.match(source, /initial_blur: initialBlur/)
  assert.match(source, /intelligence_policy: intelligencePolicy/)
  assert.match(source, /additional_loras: additionalLoras/)
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
  assert.match(source, /overflow-y-auto md:grid-cols/)
  assert.match(source, /md:overflow-y-auto/)

  assert.match(source, /const applyOutputs = getProjectAssetApplyOutputs\(variant\)/)
  assert.match(source, /void applyReference\(asset, variant\)/)
  assert.doesNotMatch(source, /applyReference\(asset, variant, /)
  assert.match(source, /getProjectAssetComponentOutputs\(variant\)/)
  assert.match(source, /const repair = getProjectReferenceRepairCopy\(metadata\)/)
  assert.match(source, /getProjectReferenceRetrySettings\(variant/)
  assert.match(source, /projectReferenceRetryNeedsPrivateAuthoring\(variant\)/)
  assert.match(source, /fetchProjectReferenceAuthoring\(/)
  assert.match(source, /response\.authored_settings\.seal !== target\.authoredSeal/)
  assert.match(source, /Exact custom authoring is unavailable for this candidate/)
  assert.match(source, /disabled=\{Boolean\(pendingAction\) \|\| !exactRetryReady\}/)
  assert.match(source, /resolveProjectReferenceRetryReview\(/)
  assert.match(source, /if \(!retryReview\.ready\)/)
  assert.match(source, /The recorded reviewer is unavailable; Retry or Edit will use the current compatible reviewer/)
  assert.match(source, /custom fields and details are never silently dropped/)
  assert.match(source, /const sourcePreset = sourceAssetType === assetType/)
  assert.match(source, /asset_type: sourceSettings\.asset_type/)
  assert.match(source, /mode: sourceSettings\.mode/)
  assert.match(source, /max_repair_attempts: sourceSettings\.max_repair_attempts/)
  assert.match(source, /sourceSettings\.schema_version === 2[\s\S]*?sourceSettings\.mode !== 'draft'/)
  assert.match(source, /private_output: sourceSettings\.private_output/)
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
  assert.match(source, /preserves recorded source mode, resolved model pair, privacy, repair, planning, and review policy/)
  assert.doesNotMatch(source, /One bounded repair/)
  assert.equal(source.match(/setActionError\(''\)/g)?.length, 3)
  assert.doesNotMatch(source, /job\?\.error/)
  assert.match(source, /projectReferenceSafeErrorMessage\(reason/)
  assert.doesNotMatch(source, /localStorage/)
})

test('Reference Studio header, catalog races, Moody cards, manifests, and Blender contract stay explicit', async () => {
  const [source, sidebar, selector, blender, store, manualInstallation] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(sidebarUrl, 'utf8'),
    readFile(modelSelectorUrl, 'utf8'),
    readFile(blenderUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
    readFile(manualInstallationUrl, 'utf8'),
  ])

  assert.match(sidebar, /<ProjectReferenceLibrary header compact \/>/)
  assert.match(sidebar, /<ProjectReferenceLibrary header \/>/)
  assert.doesNotMatch(sidebar, /<ProjectReferenceLibrary \/>/)
  assert.match(source, /header = false/)
  assert.match(source, /compact = false/)
  assert.match(source, /enabledModelsSignature/)
  assert.match(source, /catalogRequestSequence/)
  assert.match(source, /enabledModelsSignature, modelsLoaded, open, project, projectExplicitlyLocked/)
  assert.match(source, /catalogSequence !== catalogRequestSequence\.current/)
  assert.match(source, /createPortal\(/)
  assert.match(source, /installModalFocus\(/)
  assert.match(source, /event\.stopPropagation\(\)/)
  assert.match(source, /aria-haspopup="dialog"/)
  assert.match(source, /aria-disabled=\{projectExplicitlyLocked\}/)
  assert.match(source, /projectExplicitlyLocked && <span className="text-\[8px\] text-amber-200">Locked<\/span>/)

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

  const reconnectIndex = source.indexOf('await confirmReconnectedJob(')
  const closeAfterReconnect = source.indexOf('setOpen(false)', reconnectIndex)
  assert.ok(reconnectIndex >= 0 && closeAfterReconnect > reconnectIndex, 'successful Queue closes only after reconnect')
  assert.match(source, /manualInstallationDestination\(model\.manual_installation\)/)
  assert.match(selector, /manualInstallationDestination\(currentModel\.manual_installation\)/)
  assert.match(manualInstallation, /formatManualInstallationBytes/)
  assert.match(manualInstallation, /manualInstallationDestination/)
  assert.match(selector, /Open exact manual download/)
  assert.match(selector, /Local-only verification:/)
  assert.match(store, /manual_installation: m\.manual_installation/)

  assert.match(source, /aria-label="Reference creation method"/)
  assert.match(source, /referenceName=\{name\}/)
  assert.match(source, /referenceDescription=\{description\}/)
  assert.match(source, /privateOutput=\{referenceExplicitOutput \|\| privateOutput\}/)
  assert.match(source, /does not add a durable asset type/)
  assert.match(blender, /reference_name: resolvedReferenceName/)
  assert.match(blender, /private_output: privateOutput/)
  assert.match(blender, /statusRequest/)
  assert.match(blender, /operationSequence/)
  assert.match(blender, /isOperationCurrent\(operation\)/)
  assert.match(blender, /await refreshOutputs\(\)/)
  assert.match(blender, /setDirectorPlan\(null\)/)
  assert.match(blender, /workspaceRef\.current === operation\.workspace/)
  assert.match(blender, /separate reference contract/)
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
