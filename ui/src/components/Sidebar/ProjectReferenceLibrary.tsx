import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, EyeOff, FileUp, ImagePlus, Library, Loader2, MapPin, Package, Pencil, RotateCcw, Trash2, UserRound, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import {
  fetchProjectAssets,
  fetchProjectReferenceAuthoring,
  fetchProjectReferenceCapabilities,
  addProjectAssetVariant,
  createProjectReferenceRequestId,
  deleteProjectAssetVariant,
  generateProjectAssetReferences,
  fetchModels,
  fetchLoraDetails,
  fetchLlmModels,
  getEffectiveProjectReferenceRepairAttempts,
  getProjectAssetApplyOutputs,
  getProjectAssetComponentOutputs,
  getProjectAssetMediaUrl,
  getProjectReferenceEditorModels,
  getProjectReferenceGenerationModels,
  getDirectorProjectReferenceKind,
  getProjectReferencePreferredGenerationModel,
  getProjectReferenceModelAvailabilityCopy,
  getProjectReferenceQueueBlockers,
  getProjectReferenceVisibilityHints,
  getLoraParameterDefaults,
  getLoraParameterOptionToken,
  getLoraParameterValue,
  getProjectReferenceRepairCopy,
  getProjectReferenceReviewerAction,
  getProjectReferenceRetrySettings,
  hasProjectReferenceLoraParameterSummary,
  isProjectReferenceReviewMandatory,
  isProjectReferenceCharacterReplayReady,
  isProjectReferenceStyleReplayReady,
  isProjectReferenceReviewerEligible,
  isAccountProjectAccessActive,
  isProjectAssetOperationCurrent,
  lockProjectAssetVariantOperation,
  loadLlm,
  loraParameterSchemasConflict,
  projectAssetRequestError,
  projectAssetOutputNeedsInitialBlur,
  projectAssetVariantOperationKey,
  projectReferenceQualityPresentation,
  projectReferenceRetryNeedsPrivateAuthoring,
  projectReferenceSafeErrorMessage,
  PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER,
  PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER,
  resolveProjectReferenceRetryReview,
  normalizeProjectReferenceAssetType,
  normalizeProjectReferenceAnchorPrivacy,
  selectProjectReferenceModel,
  serializeProjectReferenceCharacterProfile,
  setProjectAssetVariantStatus,
  uploadImage,
  validateLoraParameterValues,
  verifyManualCheckpoint,
  type ApiModel,
  type ProjectAsset,
  type ProjectAssetOutput,
  type ProjectAssetVariant,
  type ProjectReferenceAssetType,
  type ProjectReferenceCharacterAnatomy,
  type ProjectReferenceCharacterGender,
  type ProjectReferenceCharacterProfileInput,
  type ProjectReferenceAdditionalLora,
  type ProjectReferenceAnchorBasis,
  type ProjectReferenceDepth,
  type ProjectReferenceDetailCallout,
  type ProjectReferenceDetailKind,
  type ProjectReferenceDetailOperation,
  type ProjectReferenceIntent,
  type ProjectReferenceLoraScope,
  type ProjectReferencePreset,
  type ProjectReferenceCapabilities,
  type ProjectReferenceSheetMode,
  type ProjectReferenceTypeFields,
  type ProjectReferenceTypeFieldItem,
} from '../../api/client'
import type { LlmModelOption, LoraParameterSchema, LoraParameterValue } from '../../types'
import { BlenderSceneTool } from './BlenderSceneTool'
import { HOST_TERM_NOTICES } from '../../lib/hostTerms'
import { hidePrivatePreview, privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview, subscribePrivatePreviewReveal } from '../../lib/privatePreview'
import { POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'
import { confirmReconnectedJob } from '../../lib/referenceQueue'
import { formatManualInstallationBytes, manualInstallationDestination } from '../../lib/manualInstallation'
import { requestQueueView } from '../../lib/mainViewNavigation'

const ASSET_TYPES = [
  { value: 'character', label: 'Character', icon: UserRound },
  { value: 'location', label: 'Setting / Location', icon: MapPin },
  { value: 'prop', label: 'Item / Prop', icon: Package },
  { value: 'vehicle', label: 'Vehicle / Machine', icon: Package },
  { value: 'creature', label: 'Creature', icon: UserRound },
  { value: 'wardrobe', label: 'Wardrobe / Accessory', icon: Package },
  { value: 'world', label: 'Style / World', icon: ImagePlus },
] as const

const INTENT_OPTIONS: Array<{ value: ProjectReferenceIntent; label: string; description: string }> = [
  { value: 'exact_spec', label: 'Follow my description', description: 'Stick closely to the details I provide.' },
  { value: 'generic', label: 'Balanced', description: 'Fill in practical details when needed.' },
  { value: 'brainstorming', label: 'Explore ideas', description: 'Try clearly marked visual alternatives.' },
]

const DEPTH_OPTIONS: Array<{ value: ProjectReferenceDepth; label: string; description: string }> = [
  { value: 'compact', label: 'Compact', description: 'Normally 1 sheet' },
  { value: 'standard', label: 'Standard', description: '2–3 sheets; resolves to 3' },
  { value: 'comprehensive', label: 'Comprehensive', description: '3–5 sheets; resolves to 5' },
  { value: 'custom', label: 'Custom', description: 'Choose 1–5 sheets' },
]

const MOODY_MODEL_TYPES = [
  'krea2_moody_mix_v7_fp8',
  'krea2_moody_cutie_v4_fp8',
] as const
const MOODY_MODEL_NAMES: Record<typeof MOODY_MODEL_TYPES[number], string> = {
  krea2_moody_mix_v7_fp8: 'Moody Krea 2 Mix v7 FP8',
  krea2_moody_cutie_v4_fp8: 'Moody Cutie Mix Krea 2 v4 FP8',
}

function cloneAdditionalLoras(
  loras: ProjectReferenceAdditionalLora[],
): ProjectReferenceAdditionalLora[] {
  return loras.map(lora => ({
    id: lora.id,
    multiplier: lora.multiplier,
    scope: lora.scope,
    ...(lora.parameter_schema_digest ? {
      parameter_schema_digest: lora.parameter_schema_digest,
      parameter_values: { ...(lora.parameter_values ?? {}) },
    } : {}),
  }))
}

function LoraParameterFields({
  loraId,
  schema,
  values,
  errors,
  onChange,
}: {
  loraId: string
  schema: LoraParameterSchema
  values: Record<string, LoraParameterValue>
  errors: string[]
  onChange: (id: string, value: LoraParameterValue | undefined) => void
}) {
  return (
    <fieldset className="mt-1.5 space-y-1.5 rounded border border-accent-blue/20 bg-bg-primary/40 p-1.5">
      <legend className="px-1 text-[8px] font-medium text-accent-blue">LoRA inputs</legend>
      {schema.trigger_disclosure && (
        <div className="rounded border border-amber-400/20 bg-amber-400/5 p-1.5 text-[8px] text-text-muted" aria-label={`${loraId} published activation phrases`}>
          <p className="font-medium text-amber-200">Known activation phrases</p>
          <ul className="mt-0.5 space-y-0.5">
            {schema.trigger_disclosure.activation_phrases.map(phrase => {
              const parameter = schema.parameters.find(item => item.id === phrase.parameter_id)
              const option = parameter?.options?.find(item => item.value === phrase.value)
              const valueLabel = option?.label ?? (typeof phrase.value === 'boolean'
                ? phrase.value ? 'Yes' : 'No'
                : phrase.value)
              return (
                <li key={`${phrase.parameter_id}:${String(phrase.value)}`}>
                  <span className="text-text-secondary">{parameter?.label ?? phrase.parameter_id} · {valueLabel}:</span>{' '}
                  <span className="font-mono">{phrase.text}</span>
                </li>
              )
            })}
          </ul>
          <p className="mt-1">These choices add the shown phrases only when creating matching Character images. LoRA strength is controlled separately.</p>
        </div>
      )}
      {schema.parameters.map(parameter => {
        const value = getLoraParameterValue(parameter, values)
        const helpId = `${loraId}-${parameter.id}-help`.replace(/[^a-zA-Z0-9_-]/g, '-')
        const errorId = `${loraId}-${parameter.id}-error`.replace(/[^a-zA-Z0-9_-]/g, '-')
        const fieldErrors = errors.filter(error => error.startsWith(`${parameter.label} `))
        const describedBy = [parameter.description ? helpId : '', fieldErrors.length > 0 ? errorId : '']
          .filter(Boolean).join(' ') || undefined
        const commonLabel = `${parameter.label}${parameter.required ? ' (required)' : ''}`
        return (
          <label key={parameter.id} className="block text-[8px] text-text-secondary">
            <span>{commonLabel}</span>
            {parameter.type === 'enum' ? (
              <select
                aria-label={`${loraId} ${parameter.label}`}
                aria-describedby={describedBy}
                aria-invalid={fieldErrors.length > 0}
                value={value === undefined ? '' : getLoraParameterOptionToken(value)}
                onChange={event => {
                  const option = parameter.options?.find(candidate => (
                    getLoraParameterOptionToken(candidate.value) === event.target.value
                  ))
                  onChange(parameter.id, option?.value)
                }}
                className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary md:min-h-0"
              >
                <option value="">Choose…</option>
                {parameter.options?.map(option => (
                  <option key={getLoraParameterOptionToken(option.value)} value={getLoraParameterOptionToken(option.value)}>{option.label}</option>
                ))}
              </select>
            ) : parameter.type === 'boolean' ? (
              <select
                aria-label={`${loraId} ${parameter.label}`}
                aria-describedby={describedBy}
                aria-invalid={fieldErrors.length > 0}
                value={typeof value === 'boolean' ? String(value) : ''}
                onChange={event => onChange(
                  parameter.id,
                  event.target.value === '' ? undefined : event.target.value === 'true',
                )}
                className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary md:min-h-0"
              >
                <option value="">Choose…</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            ) : parameter.type === 'text' ? (
              <input
                aria-label={`${loraId} ${parameter.label}`}
                aria-describedby={describedBy}
                aria-invalid={fieldErrors.length > 0}
                type="text"
                value={typeof value === 'string' ? value : ''}
                onChange={event => onChange(parameter.id, event.target.value)}
                className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary md:min-h-0"
              />
            ) : (
              <input
                aria-label={`${loraId} ${parameter.label}`}
                aria-describedby={describedBy}
                aria-invalid={fieldErrors.length > 0}
                type="number"
                min={parameter.minimum}
                max={parameter.maximum}
                step={parameter.step}
                value={typeof value === 'number' ? value : ''}
                onChange={event => onChange(
                  parameter.id,
                  event.target.value === '' || !Number.isFinite(event.target.valueAsNumber)
                    ? undefined
                    : event.target.valueAsNumber,
                )}
                className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary md:min-h-0"
              />
            )}
            {parameter.description && <span id={helpId} className="mt-0.5 block text-[8px] text-text-muted">{parameter.description}</span>}
            {fieldErrors.length > 0 && <span id={errorId} role="status" className="mt-0.5 block text-[8px] text-red-300">{fieldErrors.join(' ')}</span>}
            {(parameter.scopes.length > 0 || parameter.roles.length > 0) && (
              <span className="mt-0.5 block text-[7px] text-text-muted">
                Applies to {parameter.scopes.join(' / ') || 'compatible operations'}{parameter.roles.length > 0 ? ` · ${parameter.roles.join(', ')}` : ''}
              </span>
            )}
          </label>
        )
      })}
      {errors.filter(error => !schema.parameters.some(parameter => (
        error.startsWith(`${parameter.label} `)
      ))).map(error => <p key={error} role="status" className="text-[8px] text-red-300">{error}</p>)}
    </fieldset>
  )
}

type ReferenceSectionId = 'views' | 'poses' | 'expressions' | 'wardrobe' | 'details'
  | 'zones' | 'lighting' | 'functions' | 'scale' | 'mechanisms' | 'anatomy'
  | 'materials' | 'composition'

interface ReferenceSectionDefinition {
  id: ReferenceSectionId
  label: string
  wireField: string
  options: string[]
}

interface ReferenceSectionState {
  id: ReferenceSectionId
  values: ProjectReferenceTypeFieldItem[]
  pinned: boolean
}

interface DetailCalloutSetting {
  operation: ProjectReferenceDetailOperation
  sourceRole: string
}

interface ReferenceAuthoredSnapshot {
  style: string
  typeFields: ProjectReferenceTypeFields
  detailCallouts: ProjectReferenceDetailCallout[]
  characterProfile?: ProjectReferenceCharacterProfileInput
  explicitConvenience: boolean
}

type ReferenceAuthoringAvailability = 'loading' | 'ready' | 'unavailable'

interface ReferenceTypeDefinition {
  defaultPreset: ProjectReferencePreset
  presets: Array<{ value: ProjectReferencePreset; label: string }>
  sections: ReferenceSectionDefinition[]
}

type ProjectReferenceCreationMethod = 'image_pack' | 'blender_motion'

interface ProjectReferenceCreationSelection {
  candidateKind: ProjectReferenceCreationMethod
  assetType: ProjectReferenceAssetType
}

type ProjectReferenceCreationEvent =
  | { kind: 'select_method'; candidateKind: ProjectReferenceCreationMethod }
  | { kind: 'select_asset_type'; assetType: ProjectReferenceAssetType }

interface ProjectReferenceCreationTransition extends ProjectReferenceCreationSelection {
  assetTypeChanged: boolean
}

interface ProjectReferenceCreationPanelState {
  hidden: boolean
  inert: true | undefined
}

function getProjectReferenceCreationTransition(
  current: ProjectReferenceCreationSelection,
  event: ProjectReferenceCreationEvent,
): ProjectReferenceCreationTransition {
  if (event.kind === 'select_method') {
    return {
      ...current,
      candidateKind: event.candidateKind,
      assetTypeChanged: false,
    }
  }
  return {
    candidateKind: 'image_pack',
    assetType: event.assetType,
    assetTypeChanged: current.assetType !== event.assetType,
  }
}

function getProjectReferenceCreationPanelStates(
  candidateKind: ProjectReferenceCreationMethod,
): Record<ProjectReferenceCreationMethod, ProjectReferenceCreationPanelState> {
  const imagePackHidden = candidateKind !== 'image_pack'
  const blenderMotionHidden = candidateKind !== 'blender_motion'
  return {
    image_pack: { hidden: imagePackHidden, inert: imagePackHidden ? true : undefined },
    blender_motion: { hidden: blenderMotionHidden, inert: blenderMotionHidden ? true : undefined },
  }
}

interface ProjectReferenceReviewerAutoRefreshInput {
  active: boolean
  pageVisible: boolean
  projectLocked: boolean
  intelligencePolicy: 'standard_auto' | 'uncensored_auto'
  reviewerAction: 'refreshing' | 'loading' | null
  contract: ProjectReferenceCapabilities['uncensored_auto_review'] | undefined
}

function shouldAutoRefreshProjectReferenceReviewer({
  active,
  pageVisible,
  projectLocked,
  intelligencePolicy,
  reviewerAction,
  contract,
}: ProjectReferenceReviewerAutoRefreshInput): boolean {
  if (!active || !pageVisible || projectLocked
    || intelligencePolicy !== 'uncensored_auto' || reviewerAction !== null) {
    return false
  }
  if (!contract || contract.queue_ready) return false
  return contract.setup_state === 'loading' || contract.setup_state === 'loaded_without_vision'
}

const REVIEWER_AUTO_REFRESH_DELAYS_MS = [750, 1_500, 3_000, 6_000, 12_000] as const

const PROJECT_REFERENCE_CONFIRMATION_DELAYS_MS = [250, 750, 1_500] as const
const PROJECT_REFERENCE_CONFIRMATION_ATTEMPT_TIMEOUT_MS = 1_500

async function confirmProjectReferenceJobAttempt(
  jobId: string,
  confirm: (jobId: string) => Promise<void>,
  timeoutMs: number,
): Promise<boolean> {
  let timeoutId: ReturnType<typeof setTimeout> | null = null
  try {
    return await Promise.race([
      confirm(jobId).then(() => true, () => false),
      new Promise<boolean>(resolve => {
        timeoutId = setTimeout(() => resolve(false), timeoutMs)
      }),
    ])
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
  }
}

async function confirmAcceptedProjectReferenceJob(
  jobId: string,
  confirm: (jobId: string) => Promise<void>,
  wait: (delayMs: number) => Promise<void> = delayMs => new Promise(resolve => {
    window.setTimeout(resolve, delayMs)
  }),
  attemptTimeoutMs = PROJECT_REFERENCE_CONFIRMATION_ATTEMPT_TIMEOUT_MS,
): Promise<boolean> {
  for (let attempt = 0; attempt <= PROJECT_REFERENCE_CONFIRMATION_DELAYS_MS.length; attempt += 1) {
    if (await confirmProjectReferenceJobAttempt(jobId, confirm, attemptTimeoutMs)) return true
    const delayMs = PROJECT_REFERENCE_CONFIRMATION_DELAYS_MS[attempt]
    if (delayMs === undefined) return false
    await wait(delayMs)
  }
  return false
}

const REFERENCE_TYPE_DEFINITIONS: Record<ProjectReferenceAssetType, ReferenceTypeDefinition> = {
  character: {
    defaultPreset: 'identity',
    presets: [
      { value: 'identity', label: 'Identity' }, { value: 'wardrobe', label: 'Wardrobe' },
      { value: 'underlayers', label: 'Wardrobe & underlayers' },
      { value: 'anatomy', label: 'Anatomy / Nude' }, { value: 'performance', label: 'Performance' },
    ],
    sections: [
      { id: 'views', label: 'Views', wireField: 'poses', options: ['front', 'profile', 'three-quarter', 'back'] },
      { id: 'poses', label: 'Poses', wireField: 'poses', options: ['neutral', 'action', 'seated', 'movement'] },
      { id: 'expressions', label: 'Expressions', wireField: 'poses', options: ['neutral', 'joy', 'anger', 'fear'] },
      { id: 'wardrobe', label: 'Wardrobe', wireField: 'outfits', options: ['primary outfit', 'underwear / underlayers', 'individual garments', 'accessories', 'alternate outfit'] },
      { id: 'details', label: 'Details', wireField: 'poses', options: ['face', 'hands', 'markings', 'garment', 'accessory'] },
      { id: 'anatomy', label: 'Anatomy anchor', wireField: 'poses', options: ['anatomy', 'nude anatomy'] },
    ],
  },
  location: {
    defaultPreset: 'spatial',
    presets: [{ value: 'spatial', label: 'Spatial' }, { value: 'lighting', label: 'Lighting' }, { value: 'materials', label: 'Materials' }],
    sections: [
      { id: 'views', label: 'Views', wireField: 'zones', options: ['establishing', 'entry', 'reverse', 'overhead'] },
      { id: 'zones', label: 'Zones', wireField: 'zones', options: ['primary zone', 'secondary zone', 'transitions', 'boundaries'] },
      { id: 'lighting', label: 'Lighting', wireField: 'lighting', options: ['day', 'night', 'practical lights', 'weather variation'] },
      { id: 'details', label: 'Details', wireField: 'zones', options: ['material', 'fixture', 'prop', 'signage'] },
    ],
  },
  prop: {
    defaultPreset: 'product',
    presets: [{ value: 'product', label: 'Product' }, { value: 'functional', label: 'Functional' }, { value: 'construction', label: 'Construction' }],
    sections: [
      { id: 'views', label: 'Views', wireField: 'functions', options: ['front', 'side', 'back', 'top'] },
      { id: 'functions', label: 'Functions', wireField: 'functions', options: ['closed', 'in use', 'open', 'moving parts'] },
      { id: 'scale', label: 'Scale', wireField: 'scale', options: ['in hand', 'beside person', 'dimension callout', 'environment context'] },
      { id: 'details', label: 'Details', wireField: 'functions', options: ['mechanism', 'control', 'material', 'marking'] },
    ],
  },
  vehicle: {
    defaultPreset: 'exterior',
    presets: [{ value: 'exterior', label: 'Exterior' }, { value: 'interior', label: 'Interior' }, { value: 'mechanical', label: 'Mechanical' }],
    sections: [
      { id: 'views', label: 'Views', wireField: 'views', options: ['front', 'side', 'rear', 'three-quarter'] },
      { id: 'mechanisms', label: 'Mechanisms', wireField: 'mechanisms', options: ['cockpit', 'controls', 'powertrain', 'moving parts'] },
      { id: 'details', label: 'Details', wireField: 'mechanisms', options: ['mechanism', 'control', 'interior', 'marking'] },
    ],
  },
  creature: {
    defaultPreset: 'identity',
    presets: [{ value: 'identity', label: 'Identity' }, { value: 'anatomy', label: 'Anatomy / Nude' }, { value: 'behavior', label: 'Behavior' }],
    sections: [
      { id: 'views', label: 'Views', wireField: 'poses', options: ['front', 'profile', 'three-quarter', 'back'] },
      { id: 'poses', label: 'Poses', wireField: 'poses', options: ['neutral', 'locomotion', 'attack', 'resting'] },
      { id: 'expressions', label: 'Expressions', wireField: 'poses', options: ['neutral', 'alert', 'aggressive', 'relaxed'] },
      { id: 'anatomy', label: 'Anatomy anchor', wireField: 'anatomy', options: ['anatomy', 'nude anatomy', 'skeletal landmarks', 'limb detail'] },
      { id: 'details', label: 'Details', wireField: 'anatomy', options: ['face', 'limb', 'markings', 'surface'] },
    ],
  },
  wardrobe: {
    defaultPreset: 'look',
    presets: [{ value: 'look', label: 'Look' }, { value: 'construction', label: 'Construction' }, { value: 'accessories', label: 'Accessories' }],
    sections: [
      { id: 'views', label: 'Views', wireField: 'views', options: ['front', 'back', 'side', 'styled look'] },
      { id: 'materials', label: 'Materials', wireField: 'materials', options: ['fabric', 'hardware', 'seams', 'surface detail'] },
      { id: 'details', label: 'Details', wireField: 'materials', options: ['closure', 'seam', 'material', 'accessory'] },
    ],
  },
  world: {
    defaultPreset: 'visual_language',
    presets: [{ value: 'visual_language', label: 'Visual language' }, { value: 'environment', label: 'Environment' }, { value: 'cinematography', label: 'Cinematography' }],
    sections: [
      { id: 'composition', label: 'Composition', wireField: 'composition', options: ['wide', 'medium', 'close', 'graphic layout'] },
      { id: 'lighting', label: 'Lighting', wireField: 'lighting', options: ['key lighting', 'practical light', 'day', 'night'] },
      { id: 'details', label: 'Details', wireField: 'composition', options: ['material', 'lighting', 'composition', 'motion'] },
    ],
  },
}

function detailOperationLabel(operation: ProjectReferenceDetailOperation): string {
  return operation === 'auto' ? 'Auto' : friendlyRole(operation)
}

function resolvedSheetCount(depth: ProjectReferenceDepth, customSheetCount: number): number {
  if (depth === 'compact') return 1
  if (depth === 'standard') return 3
  if (depth === 'comprehensive') return 5
  return Math.max(1, Math.min(5, customSheetCount))
}

function defaultSectionValues(
  section: ReferenceSectionDefinition,
  depth: ProjectReferenceDepth,
  customSheetCount: number,
): ProjectReferenceTypeFieldItem[] {
  const count = depth === 'compact' ? 1 : depth === 'standard' ? 2 : depth === 'comprehensive'
    ? section.options.length : Math.min(section.options.length, Math.max(1, customSheetCount))
  // Anatomy/Nude is never inferred by depth. It is added only by an authored
  // preset/chip or by the explicit Character convenience action.
  if (section.id === 'anatomy') return []
  return section.options.slice(0, count).map(label => ({
    id: section.id === 'details'
      ? `builtin:${label}`
      : `${section.id}:${label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 96)}`,
    label: section.id === 'details' ? friendlyRole(label) : label,
    custom: false,
    group: section.id,
  }))
}

function createSectionState(
  assetType: ProjectReferenceAssetType,
  depth: ProjectReferenceDepth,
  customSheetCount: number,
): ReferenceSectionState[] {
  return REFERENCE_TYPE_DEFINITIONS[assetType].sections.map(section => ({
    id: section.id,
    values: defaultSectionValues(section, depth, customSheetCount),
    pinned: false,
  }))
}

function newCustomAuthoredId(): string {
  return `custom:${crypto.randomUUID().replaceAll('-', '')}`
}

function sectionOptionItem(
  capability: ProjectReferenceCapabilities['reference_types'][number] | undefined,
  definition: ReferenceSectionDefinition,
  label: string,
): ProjectReferenceTypeFieldItem | null {
  if (definition.id === 'details') {
    const kind = capability?.detail_kinds.find(item => item.label === label)
    if (capability && !kind) return null
    const id = kind?.id ?? label.toLowerCase().replaceAll(' ', '_')
    return { id: `builtin:${id}`, label: kind?.label ?? friendlyRole(label), custom: false, group: 'details' }
  }
  const option = capability?.type_fields.find(field => field.id === definition.wireField)
    ?.groups.find(group => group.id === definition.id)
    ?.options.find(item => item.label === label)
  if (capability && !option) return null
  return {
    id: option?.id ?? `${definition.id}:${label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 96)}`,
    label: option?.label ?? label,
    custom: false,
    group: definition.id,
  }
}

function orderSectionValues(
  definition: ReferenceSectionDefinition,
  values: ProjectReferenceTypeFieldItem[],
): ProjectReferenceTypeFieldItem[] {
  return [
    ...definition.options.flatMap(label => {
      const item = values.find(value => !value.custom && value.label === label)
      return item ? [item] : []
    }),
    ...values.filter(value => value.custom),
  ]
}

function resolveAnchorBasis(
  assetType: ProjectReferenceAssetType,
  preset: ProjectReferencePreset,
  sections: ReferenceSectionState[],
): ProjectReferenceAnchorBasis {
  if (assetType !== 'character' && assetType !== 'creature') return 'least_occluded'
  const anatomySelected = preset === 'anatomy' || sections.some(section => (
    section.id === 'anatomy' && section.values.some(value => (
      value.label.toLowerCase() === 'anatomy' || value.label.toLowerCase() === 'nude anatomy'
    ))
  ))
  return anatomySelected ? 'anatomy' : 'primary_outfit'
}

function selectCanonicalCharacterAnatomy(
  sections: ReferenceSectionState[],
  capability: ProjectReferenceCapabilities['reference_types'][number] | undefined,
): ReferenceSectionState[] {
  const definition = REFERENCE_TYPE_DEFINITIONS.character.sections
    .find(section => section.id === 'anatomy')
  if (!definition) return sections
  const nudeAnatomy = sectionOptionItem(capability, definition, 'nude anatomy')
  return sections.map(section => section.id === 'anatomy'
    ? { ...section, values: nudeAnatomy ? [nudeAnatomy] : [], pinned: false }
    : section)
}

function buildTypeFields(
  sections: ReferenceSectionState[],
  definitions: ReferenceSectionDefinition[],
): ProjectReferenceTypeFields {
  const grouped: Record<string, ProjectReferenceTypeFieldItem[]> = {}
  for (const section of sections) {
    if (section.id === 'details') continue
    const definition = definitions.find(candidate => candidate.id === section.id)
    if (!definition || section.values.length === 0) continue
    ;(grouped[definition.wireField] ??= []).push(...orderSectionValues(definition, section.values))
  }
  return grouped as ProjectReferenceTypeFields
}

function cloneReferenceAuthoredSnapshot(
  typeFields: ProjectReferenceTypeFields | undefined,
  detailCallouts: ProjectReferenceDetailCallout[] | undefined,
  style: string | undefined,
  characterProfile?: ProjectReferenceCharacterProfileInput,
  explicitConvenience = false,
): ReferenceAuthoredSnapshot {
  return {
    style: style ?? '',
    typeFields: Object.fromEntries(
      Object.entries(typeFields ?? {}).map(([field, items]) => [
        field,
        items?.map(item => ({ ...item })) ?? [],
      ]),
    ) as ProjectReferenceTypeFields,
    detailCallouts: (detailCallouts ?? []).map(callout => ({ ...callout })),
    explicitConvenience,
    ...(characterProfile ? {
      characterProfile: {
        ...characterProfile,
        explicit_anatomy: [...characterProfile.explicit_anatomy],
      },
    } : {}),
  }
}

const SHEET_MODES: Array<{
  value: ProjectReferenceSheetMode
  label: string
  description: string
}> = [
  {
    value: 'production',
    label: 'Production',
    description: 'Creates one main image, then builds and checks a consistent pack.',
  },
  {
    value: 'hybrid',
    label: 'Hybrid',
    description: 'Creates one main image, then uses it to make focused variations.',
  },
  {
    value: 'draft',
    label: 'Draft',
    description: 'Creates each sheet independently for speed. Automatic fixes are off.',
  },
]

function friendlyRole(role: string): string {
  return role.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

const PROJECT_REFERENCE_PROVIDER_LABELS: Readonly<Record<string, string>> = {
  local: 'Local',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
}

const PROJECT_REFERENCE_MODEL_LABELS: Readonly<Record<string, string>> = {
  auto: 'Automatic local model',
  auto_local: 'Automatic local model',
  deterministic: 'Built-in planner',
  off: 'Off',
}

const PROJECT_REFERENCE_VARIANT_STATUS_LABELS: Readonly<Record<string, string>> = {
  candidate: 'Candidate',
  kept: 'Kept',
  rejected: 'Rejected',
}

const PROJECT_REFERENCE_ROUTE_STATUS_LABELS: Readonly<Record<string, string>> = {
  standard: 'Standard settings',
  applied: 'Adjusted for this model',
  skipped: 'Not used',
}

const PROJECT_REFERENCE_OPERATION_LABELS: Readonly<Record<string, string>> = {
  generation: 'Main image',
  edit: 'Variations',
  repair: 'Automatic fixes',
  callout: 'Detail views',
}

const PROJECT_REFERENCE_LORA_SCOPE_LABELS: Readonly<Record<string, string>> = {
  auto: 'Best fit',
  generation: 'Main image',
  editing: 'Variations',
}

const PROJECT_REFERENCE_REVIEWER_SETUP_LABELS: Readonly<Record<string, string>> = {
  missing_model: 'Visual review model setup is required.',
  missing_projector: 'Image understanding setup is required.',
  loaded_without_vision: 'The visual review model is loaded, but image understanding is unavailable.',
  ready_unloaded: 'Visual review is ready and will load automatically when needed.',
  ready_resident: 'Visual review is ready.',
}

const PROJECT_REFERENCE_REVIEWER_ACTION_LABELS: Readonly<Record<string, string>> = {
  load: 'Prepare visual review model',
  reload: 'Reload visual review model',
}

function projectReferenceProviderLabel(provider: string | null | undefined): string {
  if (!provider) return 'Local'
  return PROJECT_REFERENCE_PROVIDER_LABELS[provider.toLowerCase()] ?? 'External service'
}

function projectReferenceModelLabel(
  modelId: string | null | undefined,
  catalog: readonly Pick<LlmModelOption, 'id' | 'label'>[] = [],
): string {
  if (!modelId) return 'Model unavailable'
  return PROJECT_REFERENCE_MODEL_LABELS[modelId]
    ?? catalog.find(model => model.id === modelId)?.label
    ?? 'Model unavailable'
}

function projectReferenceVariantStatusLabel(status: string | null | undefined): string {
  return status ? PROJECT_REFERENCE_VARIANT_STATUS_LABELS[status] ?? 'Status unavailable' : 'Status unavailable'
}

function projectReferenceRouteStatusLabel(status: string | null | undefined): string {
  return status ? PROJECT_REFERENCE_ROUTE_STATUS_LABELS[status] ?? 'Status unavailable' : 'Status unavailable'
}

function projectReferenceOperationLabel(operation: string | null | undefined): string {
  return operation ? PROJECT_REFERENCE_OPERATION_LABELS[operation] ?? 'Other step' : 'Other step'
}

function projectReferenceLoraScopeLabel(scope: string | null | undefined): string {
  return scope ? PROJECT_REFERENCE_LORA_SCOPE_LABELS[scope] ?? 'Other use' : 'Other use'
}

function projectReferencePendingPhaseLabel(phase: string | null | undefined): string {
  if (!phase) return 'Queued'
  const normalized = phase.trim().toLowerCase().replace(/[_-]+/g, ' ')
  if (normalized === 'reference generation held') return 'Waiting in the Queue'
  if (normalized === 'planning' || normalized.startsWith('planning ')
    || normalized.startsWith('freezing ')) return 'Preparing your pack'
  if (normalized.startsWith('generating ')) return 'Creating pack sheets'
  if (normalized.startsWith('deriving ')) return 'Creating variations'
  if (normalized.startsWith('reviewing ')) return 'Checking visual quality'
  if (normalized.startsWith('repairing ')) return 'Improving pack sheets'
  if (normalized.startsWith('staging ') || normalized.startsWith('publishing ')
    || normalized === 'publication recovery') return 'Finishing your pack'
  return 'Status unavailable'
}

function projectReferenceReviewerLoadingLabel(phase: string | null | undefined): string {
  if (!phase) return 'Preparing visual review'
  const normalized = phase.trim().toLowerCase().replace(/[_-]+/g, ' ')
  if (normalized === 'downloading' || normalized === 'downloading model') return 'Downloading visual review model'
  if (normalized === 'downloading runtime') return 'Downloading visual review support'
  if (normalized === 'building runtime') return 'Preparing visual review support'
  if (normalized === 'downloading projector' || normalized === 'downloading vision projector') return 'Downloading image understanding'
  if (normalized === 'building projector' || normalized === 'building vision projector') return 'Preparing image understanding'
  if (normalized === 'loading model') return 'Loading visual review model'
  if (normalized === 'loading projector' || normalized === 'loading vision projector'
    || normalized === 'loading mmproj') return 'Loading image understanding'
  return 'Preparation status unavailable'
}

function projectReferenceReviewerSetupLabel(
  contract: ProjectReferenceCapabilities['uncensored_auto_review'] | null | undefined,
): string {
  if (!contract) return 'Visual review setup is unavailable. Refresh its status and try again.'
  if (contract.setup_state === 'loading') {
    return `${projectReferenceReviewerLoadingLabel(contract.loading_phase)}.`
  }
  return PROJECT_REFERENCE_REVIEWER_SETUP_LABELS[contract.setup_state]
    ?? 'Visual review setup is unavailable. Refresh its status and try again.'
}

function projectReferenceReviewerActionLabel(kind: string | null | undefined): string {
  return kind ? PROJECT_REFERENCE_REVIEWER_ACTION_LABELS[kind] ?? 'Prepare visual review model' : 'Prepare visual review model'
}

function referenceSheetStatus(variant: ProjectAssetVariant): {
  label: string
  className: string
  repair: string
} | null {
  if (variant.variant_type !== 'reference_sheet' && variant.variant_type !== 'reference_pack') return null
  const metadata = variant.variant_type === 'reference_pack'
    ? variant.metadata.reference_pack
    : variant.metadata.reference_sheet
  if (variant.variant_type === 'reference_pack' && variant.metadata.reference_pack?.quality) return null
  const repaired = Array.isArray(metadata?.roles?.repaired)
    ? metadata.roles.repaired.filter(role => typeof role === 'string')
    : []
  const repair = getProjectReferenceRepairCopy(metadata)
  if (metadata?.review_status === 'pass') {
    return { label: repaired.length > 0 ? 'Quality check passed after a fix' : 'Quality check passed', className: 'text-accent-green', repair }
  }
  if (metadata?.review_status === 'fail') {
    return { label: 'Quality check found items to review', className: 'text-amber-300', repair }
  }
  if (metadata?.review_status === 'review_unavailable') {
    return { label: 'Quality check unavailable — candidate kept for you to review', className: 'text-text-muted', repair: '' }
  }
  return { label: 'Quality check was not requested', className: 'text-text-muted', repair: '' }
}

function ProjectAssetPreview({ project, assetId, output, label }: {
  project: string
  assetId: string
  output: ProjectAssetOutput
  label: string
}) {
  const isPrivate = output.metadata?.private === true
  const needsInitialBlur = projectAssetOutputNeedsInitialBlur(output)
  const identity = privatePreviewIdentity(project, `asset:${assetId}:${output.id}`, output.relative_path)
  const [revealedIdentity, setRevealedIdentity] = useState(() =>
    needsInitialBlur && privatePreviewWasRevealed(identity) ? identity : '',
  )
  const revealed = needsInitialBlur && revealedIdentity === identity
  // Compatibility name retained for the shared private-preview source
  // contract; public initial_blur outputs use the same session-only veil.
  const privateBlurred = needsInitialBlur && !revealed
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const syncReveal = (nextRevealed = privatePreviewWasRevealed(identity)) => {
      setRevealedIdentity(needsInitialBlur && nextRevealed ? identity : '')
    }
    syncReveal()
    return subscribePrivatePreviewReveal(identity, syncReveal)
  }, [identity, needsInitialBlur])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !privateBlurred) return
    video.pause()
    video.removeAttribute('src')
    video.load()
  }, [privateBlurred])

  const reveal = () => {
    revealPrivatePreview(identity)
    setRevealedIdentity(identity)
  }
  const hide = () => {
    hidePrivatePreview(identity)
    setRevealedIdentity('')
  }

  return (
    <div className="relative aspect-video w-full overflow-hidden bg-media-canvas">
      <div className={`h-full w-full transition-[filter] ${
        privateBlurred ? 'blur-xl' : ''
      }`} inert={privateBlurred}>
        {output.media_type?.startsWith('video/')
          ? <video
              ref={videoRef}
              src={privateBlurred ? undefined : getProjectAssetMediaUrl(project, output.relative_path)}
              preload={privateBlurred ? 'none' : 'metadata'}
              controls
              className="h-full w-full object-contain"
            />
          : <img
              src={privateBlurred ? undefined : getProjectAssetMediaUrl(project, output.relative_path)}
              alt={label}
              className="h-full w-full object-contain"
            />}
      </div>
      {privateBlurred && (
        <button
          type="button"
          onClick={reveal}
          className="absolute inset-0 z-10 flex items-center justify-center bg-black/25 text-white"
          title="Click, tap, or press Enter to reveal for this browser session"
        >
          <EyeOff size={18} />
          <span className="sr-only">Reveal reference preview</span>
        </button>
      )}
      {needsInitialBlur && revealed && (
        <button
          type="button"
          onClick={hide}
          className="absolute right-1 top-1 z-10 rounded-full bg-black/65 p-1 text-white/80 hover:text-white"
          title={isPrivate ? 'Blur this private reference preview again' : 'Blur this reference preview again'}
        >
          <EyeOff size={11} />
        </button>
      )}
    </div>
  )
}

export function ProjectReferenceLibrary({ active }: { active: boolean }) {
  const project = useStore(s => s.activeWorkspace)
  const workspaces = useStore(s => s.workspaces)
  const accessContext = useStore(s => s.accessContext)
  const accountProjectMigration = useStore(s => s.accountProjectMigration)
  const jobs = useStore(s => s.jobs)
  const browsingUploads = useStore(s => s.browsingUploads)
  const privateOutput = useStore(s => s.privateOutput)
  const explicitOutput = useStore(s => s.explicitOutput)
  const explicitOutputRef = useRef(explicitOutput)
  const privateOutputRef = useRef(privateOutput)
  const generationMode = useStore(s => s.generationMode)
  const setGenerationMode = useStore(s => s.setGenerationMode)
  const selectModel = useStore(s => s.selectModel)
  const setParam = useStore(s => s.setParam)
  const setSidebarMode = useStore(s => s.setSidebarMode)
  const referenceReturnMode = useStore(s => s.referenceReturnMode)
  const setGuideVideoFps = useStore(s => s.setGuideVideoFps)
  const setGuideVideoFrameCount = useStore(s => s.setGuideVideoFrameCount)
  const addImageRef = useStore(s => s.addImageRef)
  const addCharacterRef = useStore(s => s.directorAddCharacterRef)
  const addLocationRef = useStore(s => s.directorAddLocationRef)
  const reconnectJobs = useStore(s => s.reconnectJobs)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const enabledModels = useStore(s => s.enabledModels)
  const modelsLoaded = useStore(s => s.modelsLoaded)
  const openModelVisibility = useStore(s => s.openModelVisibility)
  const open = active
  const [assets, setAssets] = useState<ProjectAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [modelLoadError, setModelLoadError] = useState('')
  const [reviewerAction, setReviewerAction] = useState<'refreshing' | 'loading' | null>(null)
  const [reviewerActionError, setReviewerActionError] = useState('')
  const [reviewerPageVisible, setReviewerPageVisible] = useState(() => (
    typeof document === 'undefined' || document.visibilityState !== 'hidden'
  ))
  const [verifyingManualModel, setVerifyingManualModel] = useState('')
  const [actionError, setActionError] = useState('')
  const [catalogModels, setCatalogModels] = useState<ApiModel[]>([])
  const [referenceCapabilities, setReferenceCapabilities] = useState<ProjectReferenceCapabilities | null>(null)
  const [capabilitiesLoadError, setCapabilitiesLoadError] = useState('')
  const [llmCatalogModels, setLlmCatalogModels] = useState<LlmModelOption[]>([])
  const [referenceModelType, setReferenceModelType] = useState('')
  const [editorModelType, setEditorModelType] = useState('')
  const [referenceModelCustomized, setReferenceModelCustomized] = useState(false)
  const [editorModelCustomized, setEditorModelCustomized] = useState(false)
  const [assetType, setAssetType] = useState<ProjectReferenceAssetType>('character')
  const [candidateKind, setCandidateKind] = useState<ProjectReferenceCreationMethod>('image_pack')
  const [sheetMode, setSheetMode] = useState<ProjectReferenceSheetMode>('production')
  const [intent, setIntent] = useState<ProjectReferenceIntent>('generic')
  const [depth, setDepth] = useState<ProjectReferenceDepth>('standard')
  const [customSheetCount, setCustomSheetCount] = useState(3)
  const [preset, setPreset] = useState<ProjectReferencePreset>('identity')
  const [sections, setSections] = useState<ReferenceSectionState[]>(() => (
    createSectionState('character', 'standard', 3)
  ))
  const [customSectionInputs, setCustomSectionInputs] = useState<Partial<Record<ReferenceSectionId, string>>>({})
  const [authoringStatus, setAuthoringStatus] = useState('')
  const [detailSettings, setDetailSettings] = useState<Record<string, DetailCalloutSetting>>({})
  const [planningModel, setPlanningModel] = useState('auto')
  const [reviewModel, setReviewModel] = useState('auto_local')
  const [referenceExplicitOutput, setReferenceExplicitOutput] = useState(explicitOutput)
  const [explicitConvenience, setExplicitConvenience] = useState(false)
  const [characterGender, setCharacterGender] = useState<ProjectReferenceCharacterGender>('unspecified')
  const [characterAge, setCharacterAge] = useState('')
  const [characterExplicitAnatomy, setCharacterExplicitAnatomy] = useState<ProjectReferenceCharacterAnatomy[]>([])
  const [contentCapability, setContentCapability] = useState<'standard' | 'unrestricted_local'>(
    explicitOutput ? 'unrestricted_local' : 'standard',
  )
  const [initialBlur, setInitialBlur] = useState(explicitOutput || privateOutput)
  const [intelligencePolicy, setIntelligencePolicy] = useState<'standard_auto' | 'uncensored_auto'>(
    explicitOutput ? 'uncensored_auto' : 'standard_auto',
  )
  const [intelligenceCustomized, setIntelligenceCustomized] = useState(false)
  const [generationLoras, setGenerationLoras] = useState<string[]>([])
  const [editingLoras, setEditingLoras] = useState<string[]>([])
  const [generationLoraSchemas, setGenerationLoraSchemas] = useState<Record<string, LoraParameterSchema>>({})
  const [editingLoraSchemas, setEditingLoraSchemas] = useState<Record<string, LoraParameterSchema>>({})
  const [additionalLoras, setAdditionalLoras] = useState<ProjectReferenceAdditionalLora[]>([])
  const [pendingLoraScope, setPendingLoraScope] = useState<ProjectReferenceLoraScope>('auto')
  const [pendingLoraId, setPendingLoraId] = useState('')
  const [pendingLoraMultiplier, setPendingLoraMultiplier] = useState(1)
  const [loraLoadError, setLoraLoadError] = useState('')
  const [anatomyPrivate, setAnatomyPrivate] = useState(true)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [visualStyle, setVisualStyle] = useState('')
  const [customVisualStyle, setCustomVisualStyle] = useState('')
  const [candidateCount, setCandidateCount] = useState(1)
  const [columns, setColumns] = useState(2)
  const [paletteSwatches, setPaletteSwatches] = useState(8)
  const [maxRepairAttempts, setMaxRepairAttempts] = useState(1)
  const [importing, setImporting] = useState<{ assetId: string; message: string } | null>(null)
  const [editVariantId, setEditVariantId] = useState<string | null>(null)
  const [editInstruction, setEditInstruction] = useState('')
  const [queuedMessage, setQueuedMessage] = useState('')
  const [pendingFreshJobIds, setPendingFreshJobIds] = useState<string[]>([])
  const [pendingSheetActions, setPendingSheetActions] = useState<Record<string, {
    project: string
    assetId: string
    variantId: string
    jobId: string | null
  }>>({})
  const [authoringAvailability, setAuthoringAvailability] = useState<Record<string, ReferenceAuthoringAvailability>>({})
  const [privateReplayRetry, setPrivateReplayRetry] = useState(0)
  const requestSequence = useRef(0)
  const catalogRequestSequence = useRef(0)
  const reviewerAutoRefreshSequence = useRef(0)
  const projectEpoch = useRef(0)
  const previousProject = useRef(project)
  const currentProject = useRef(project)
  const enabledModelsSignature = useMemo(
    () => [...enabledModels].sort().join('\u001f'),
    [enabledModels],
  )
  const pendingSheetActionLocks = useRef(new Set<string>())
  const authoredSettingsSnapshots = useRef(new Map<string, ReferenceAuthoredSnapshot>())
  const loraParameterSnapshots = useRef(new Map<string, ProjectReferenceAdditionalLora[]>())
  const authoringAvailabilityRef = useRef(new Map<string, ReferenceAuthoringAvailability>())
  const accountProjectAccessActive = isAccountProjectAccessActive(accessContext, accountProjectMigration)
  const projectExplicitlyLocked = !accountProjectAccessActive && workspaces.some(workspace => (
    workspace.name === project && workspace.unlocked === false
  ))
  const privateAuthoringTargets = useMemo(() => assets.flatMap(asset => (
    asset.variants.flatMap(variant => {
      const packMetadata = variant.metadata.reference_pack
      const summarizedLoras = [
        ...(packMetadata?.additional_loras?.applied ?? []),
        ...(packMetadata?.additional_loras?.skipped ?? []),
      ]
      const needsAuthoring = projectReferenceRetryNeedsPrivateAuthoring(variant)
      const needsStyle = packMetadata?.authored_settings?.style_present === true
      const needsLoraParameters = summarizedLoras.some(hasProjectReferenceLoraParameterSummary)
      if (!needsAuthoring && !needsLoraParameters) return []
      return [{
        assetId: asset.id,
        variantId: variant.id,
        authoredSeal: packMetadata?.authored_settings?.seal ?? '',
        planSeal: packMetadata?.plan_seal ?? '',
        needsAuthoring,
        needsStyle,
        styleCommitment: packMetadata?.authored_settings?.style_commitment ?? '',
        characterReplayContract: {
          explicit_convenience: packMetadata?.explicit_convenience,
          authored_settings: {
            character_profile: packMetadata?.authored_settings?.character_profile,
            managed_character_callouts: packMetadata?.authored_settings?.managed_character_callouts,
          },
        },
        needsLoraParameters,
        parameterRecords: summarizedLoras.flatMap(lora => lora.parameters
          ? [{
              id: lora.id,
              count: lora.parameters.count,
              ids: lora.parameters.ids,
              schemaDigest: lora.parameters.schema_digest,
              valuesDigest: lora.parameters.values_digest,
              expansionDigest: lora.parameters.expansion_digest,
            }]
          : []),
      }]
    })
  )), [assets])
  const privateAuthoringTargetSignature = JSON.stringify(privateAuthoringTargets)
  const referenceModels = useMemo(
    () => getProjectReferenceGenerationModels(catalogModels),
    [catalogModels],
  )
  const editorModels = useMemo(
    () => getProjectReferenceEditorModels(catalogModels),
    [catalogModels],
  )
  const selectedRecipeTermRequirements = useMemo(() => {
    const modelTypes = [
      referenceModelType,
      ...(sheetMode !== 'draft' ? [editorModelType] : []),
    ]
    const requirements = modelTypes.flatMap(modelType => (
      catalogModels.find(model => model.model_type === modelType)?.required_host_terms || []
    ))
    return requirements.filter((requirement, index) => (
      requirements.findIndex(candidate => candidate.term === requirement.term) === index
    ))
  }, [catalogModels, editorModelType, referenceModelType, sheetMode])
  const pendingRecipeTermRequirements = selectedRecipeTermRequirements.filter(
    requirement => hostTerms?.[requirement.term]?.accepted !== true,
  )
  const pendingManualModels = useMemo(() => {
    const selected = [
      referenceModelType,
      ...(sheetMode !== 'draft' ? [editorModelType] : []),
    ].flatMap(modelType => {
      const model = catalogModels.find(candidate => candidate.model_type === modelType)
      return model?.downloadable === false && !model.manual_checkpoint_verified
        ? [model]
        : []
    })
    return selected.filter((model, index) => (
      selected.findIndex(candidate => candidate.model_type === model.model_type) === index
    ))
  }, [catalogModels, editorModelType, referenceModelType, sheetMode])
  const reviewPolicy = referenceCapabilities?.review_policy
  const mandatoryReview = isProjectReferenceReviewMandatory(
    contentCapability, referenceExplicitOutput, reviewPolicy,
  )
  const effectiveMaxRepairAttempts = getEffectiveProjectReferenceRepairAttempts(
    sheetMode,
    mandatoryReview || reviewModel !== 'off',
    maxRepairAttempts,
  )
  const sheetCount = resolvedSheetCount(depth, customSheetCount)
  const typeDefinition = REFERENCE_TYPE_DEFINITIONS[assetType]
  const authoritativeTypeCapabilities = referenceCapabilities?.reference_types
    .find(referenceType => referenceType.id === assetType)
  const authoritativePreset = authoritativeTypeCapabilities?.presets
    .find(option => option.id === preset)
  const sectionDefinitions = useMemo<ReferenceSectionDefinition[]>(() => {
    if (!authoritativeTypeCapabilities) return typeDefinition.sections
    const authored = authoritativeTypeCapabilities.type_fields.flatMap(field => (
      field.groups.map(group => ({
        id: group.id as ReferenceSectionId,
        label: group.label,
        wireField: field.id,
        options: group.options.map(option => option.label),
      }))
    ))
    return [...authored, {
      id: 'details' as const,
      label: 'Details',
      wireField: '',
      options: authoritativeTypeCapabilities.detail_kinds.map(kind => kind.label),
    }]
  }, [authoritativeTypeCapabilities, typeDefinition.sections])
  const anchorBasis = resolveAnchorBasis(assetType, preset, sections)
  const characterProfileSerialization = serializeProjectReferenceCharacterProfile(
    {
      gender: characterGender,
      ageInput: characterAge,
      explicitAnatomy: characterExplicitAnatomy,
    },
    explicitConvenience,
  )
  const managedCharacterCalloutCount = explicitConvenience && sheetMode !== 'draft'
    ? characterExplicitAnatomy.reduce((total, item) => total + (item === 'breasts' ? 2 : 1), 0)
    : 0
  const typeFields = useMemo(
    () => buildTypeFields(sections, sectionDefinitions),
    [sectionDefinitions, sections],
  )
  const validDetailSourceRoles = (authoritativePreset?.valid_source_roles ?? []).slice(0, sheetCount)
  const selectedDetailItems = useMemo(
    () => sections.find(section => section.id === 'details')?.values ?? [],
    [sections],
  )
  const authoredDetailCallouts = useMemo(() => {
    return selectedDetailItems
      .slice(0, 8)
      .map((item): ProjectReferenceDetailCallout => {
        const kind = item.custom
          ? 'custom'
          : item.id.replace(/^builtin:/, '') as ProjectReferenceDetailKind
        const setting = detailSettings[item.id]
        const currentSource = setting?.sourceRole ?? ''
        const requestedOperation = setting?.operation ?? 'auto'
        return {
          custom_id: item.id,
          label: item.label,
          kind,
          operation: intent === 'exact_spec' && requestedOperation === 'reconstruct'
            ? 'auto'
            : requestedOperation,
          source_role: validDetailSourceRoles.includes(currentSource)
            ? currentSource
            : validDetailSourceRoles[0] ?? '',
        }
      })
  }, [detailSettings, intent, selectedDetailItems, validDetailSourceRoles])
  const detailCallouts = sheetMode === 'draft' ? [] : authoredDetailCallouts
  const hasInvalidAuthoredSettings = sections.some(section => (
    section.values.some(item => (
      !item.label.trim() || item.label !== item.label.trim() || item.label.length > 500 || item.label.includes('\0')
    ))
  )) || sections.filter(section => section.id !== 'details')
    .reduce((total, section) => total + section.values.length, 0) > 128
    || Object.values(typeFields).some(items => (items?.length ?? 0) > 64)
    || selectedDetailItems.length > 8
    || (selectedDetailItems.some(item => item.custom)
      && authoritativeTypeCapabilities?.supports_custom_details !== true)
    || (sheetMode !== 'draft' && selectedDetailItems.length > 0 && validDetailSourceRoles.length === 0)
  const selectedPlanningModel = llmCatalogModels.find(model => model.id === planningModel)
  const selectedReviewModel = llmCatalogModels.find(model => model.id === reviewModel)
  const visiblePresets = authoritativeTypeCapabilities?.presets.map(option => ({
    value: option.id,
    label: option.label,
  })) ?? []
  const authoritativeRoles = authoritativePreset?.ordered_roles ?? []
  const deliverables = authoritativeRoles
    .slice(0, sheetCount)
    .map((role, index) => `Sheet ${index + 1}: ${friendlyRole(role)}`)
  const planningModels = useMemo(
    () => llmCatalogModels.filter(model => model.loaded === true),
    [llmCatalogModels],
  )
  const reviewModels = useMemo(
    () => planningModels.filter(model => (
      model.vision_capable === true && model.vision_available === true
    )),
    [planningModels],
  )
  const uncensoredReviewContract = referenceCapabilities?.uncensored_auto_review
  const uncensoredReviewCatalogModel = llmCatalogModels.find(model => (
    model.id === uncensoredReviewContract?.resolved_model
    && (model.provider ?? 'local') === uncensoredReviewContract?.resolved_provider
  ))
  const selectableReviewModels = intelligencePolicy === 'uncensored_auto'
    ? (uncensoredReviewCatalogModel ? [uncensoredReviewCatalogModel] : [])
    : reviewModels
  const uncensoredReviewSelectionValid = reviewModel === 'off'
    || reviewModel === 'auto_local'
    || (reviewModel === uncensoredReviewContract?.resolved_model
      && (selectedReviewModel?.provider ?? 'local') === uncensoredReviewContract?.resolved_provider)
  const uncensoredReviewUnavailable = intelligencePolicy === 'uncensored_auto'
    && reviewModel !== 'off'
    && (!uncensoredReviewContract?.queue_ready || !uncensoredReviewSelectionValid)
  const mandatoryReviewSelectionEligible = isProjectReferenceReviewerEligible(
    intelligencePolicy, reviewModel, selectedReviewModel?.provider,
    reviewModels, referenceCapabilities,
  )
  const mandatoryReviewUnavailable = mandatoryReview && !mandatoryReviewSelectionEligible
  const reviewSelectionUnavailable = uncensoredReviewUnavailable || mandatoryReviewUnavailable
  const reviewerSetupCopy = projectReferenceReviewerSetupLabel(uncensoredReviewContract)
  const reviewerSetupAction = getProjectReferenceReviewerAction(
    uncensoredReviewContract?.setup_state,
  )
  const reviewerNeedsAutomaticRefresh = shouldAutoRefreshProjectReferenceReviewer({
    active: open,
    pageVisible: reviewerPageVisible,
    projectLocked: projectExplicitlyLocked,
    intelligencePolicy,
    reviewerAction,
    contract: uncensoredReviewContract,
  })
  explicitOutputRef.current = explicitOutput
  privateOutputRef.current = privateOutput
  const availablePendingLoras = useMemo(() => {
    const source = pendingLoraScope === 'generation'
      ? generationLoras
      : pendingLoraScope === 'editing'
        ? editingLoras
        : [...generationLoras, ...editingLoras]
    const alreadySelected = new Set(additionalLoras.map(lora => lora.id))
    return [...new Set(source)].filter(id => !alreadySelected.has(id)).sort((left, right) => left.localeCompare(right))
  }, [additionalLoras, editingLoras, generationLoras, pendingLoraScope])
  const resolveLoraSchema = useCallback((id: string, scope: ProjectReferenceLoraScope) => {
    const generationSchema = generationLoraSchemas[id]
    const editingSchema = editingLoraSchemas[id]
    if (scope === 'generation') return generationSchema
    if (scope === 'editing') return editingSchema
    if (generationSchema && editingSchema
      && generationSchema.schema_digest !== editingSchema.schema_digest) return undefined
    return generationSchema ?? editingSchema
  }, [editingLoraSchemas, generationLoraSchemas])
  const hasLoraSchemaConflict = useCallback((id: string, scope: ProjectReferenceLoraScope) => (
    loraParameterSchemasConflict(
      generationLoraSchemas[id],
      editingLoraSchemas[id],
      scope,
      generationLoras.includes(id),
      sheetMode !== 'draft' && editingLoras.includes(id),
    )
  ), [editingLoraSchemas, editingLoras, generationLoraSchemas, generationLoras, sheetMode])
  const loraScopes = referenceCapabilities?.lora_scopes ?? []
  const contentCapabilities = referenceCapabilities?.content_capabilities ?? []
  const intelligencePolicies = referenceCapabilities?.intelligence_policies ?? []
  const hasInvalidLoraMultiplier = additionalLoras.some(lora => (
    !Number.isFinite(lora.multiplier) || lora.multiplier < -10 || lora.multiplier > 10
  ))
  const hasInvalidExplicitLora = additionalLoras.some(lora => (
    (lora.scope === 'generation' && !generationLoras.includes(lora.id))
    || (lora.scope === 'editing' && (sheetMode === 'draft' || !editingLoras.includes(lora.id)))
  ))
  const loraParameterErrors = additionalLoras.flatMap(lora => {
    if (hasLoraSchemaConflict(lora.id, lora.scope)) {
      return [`${lora.id}: this LoRA uses different settings for the main image and variations. Choose where to use it.`]
    }
    if (!lora.parameter_schema_digest) return []
    const schema = resolveLoraSchema(lora.id, lora.scope)
    if (!schema) return [`${lora.id}: its settings are unavailable for the selected model and use.`]
    if (schema.schema_digest !== lora.parameter_schema_digest) {
      return [`${lora.id}: its available settings have changed. Remove and add it again to review them.`]
    }
    return validateLoraParameterValues(schema, lora.parameter_values)
      .map(error => `${lora.id}: ${error}`)
  })
  const hasInvalidLoraParameters = loraParameterErrors.length > 0
  const pendingLoraSchemaConflict = Boolean(
    pendingLoraId && hasLoraSchemaConflict(pendingLoraId, pendingLoraScope),
  )
  const moodyVisibilityHints = getProjectReferenceVisibilityHints(
    MOODY_MODEL_TYPES, enabledModels, catalogModels, modelsLoaded,
  )
  const disabledMoodyModels = moodyVisibilityHints.disabled as Array<typeof MOODY_MODEL_TYPES[number]>
  const enabledMissingMoodyModels = moodyVisibilityHints.enabled_missing as Array<typeof MOODY_MODEL_TYPES[number]>
  const queueBlockers = getProjectReferenceQueueBlockers({
    submitting,
    project_locked: projectExplicitlyLocked,
    loading: loading && assets.length === 0,
    name_missing: !name.trim(),
    capabilities_unavailable: !referenceCapabilities,
    deliverables_unavailable: deliverables.length !== sheetCount,
    generation_model_missing: !referenceModelType,
    editor_model_missing: sheetMode !== 'draft' && !editorModelType,
    terms_pending: pendingRecipeTermRequirements.length > 0,
    manual_verification_pending: pendingManualModels.length > 0,
    incompatible_lora: hasInvalidExplicitLora,
    invalid_lora_multiplier: hasInvalidLoraMultiplier,
    invalid_lora_parameters: hasInvalidLoraParameters,
    invalid_authored_settings: hasInvalidAuthoredSettings,
    invalid_character_age: assetType === 'character'
      && characterProfileSerialization.blocker === PROJECT_REFERENCE_CHARACTER_AGE_BLOCKER,
    explicit_convenience_age: assetType === 'character'
      && characterProfileSerialization.blocker === PROJECT_REFERENCE_EXPLICIT_CONVENIENCE_AGE_BLOCKER,
    too_many_detail_callouts: assetType === 'character'
      && selectedDetailItems.length + managedCharacterCalloutCount > 8,
    review_unavailable: reviewSelectionUnavailable,
  })
  const visibleQueueBlockers = queueBlockers.filter(blocker => blocker.id !== 'submitting')
  const creationPanelStates = getProjectReferenceCreationPanelStates(candidateKind)
  currentProject.current = project

  useEffect(() => {
    if (!authoritativeTypeCapabilities) return
    setSections(current => sectionDefinitions.map(definition => {
      const existing = current.find(section => section.id === definition.id)
      if (existing?.pinned) {
        const values = existing.values.flatMap(item => {
          if (item.custom) return item.group === definition.id ? [item] : []
          const authoritative = definition.options
            .map(label => sectionOptionItem(authoritativeTypeCapabilities, definition, label))
            .find(option => option?.id === item.id || option?.label === item.label)
          return authoritative ? [authoritative] : []
        })
        return { ...existing, values: orderSectionValues(definition, values) }
      }
      const existingNudeAnatomy = definition.id === 'anatomy' && existing?.values.some(
        item => item.label.toLowerCase() === 'nude anatomy',
      )
      const defaults = definition.id === 'anatomy' && preset === 'anatomy'
        ? definition.options.filter(label => label.toLowerCase() === (
          existingNudeAnatomy ? 'nude anatomy' : 'anatomy'
        ))
        : defaultSectionValues(definition, depth, customSheetCount).map(item => item.label)
      return {
        id: definition.id,
        pinned: false,
        values: defaults.flatMap(label => {
          const option = sectionOptionItem(authoritativeTypeCapabilities, definition, label)
          return option ? [option] : []
        }),
      }
    }))
  }, [authoritativeTypeCapabilities, customSheetCount, depth, preset, sectionDefinitions])

  useEffect(() => {
    if (previousProject.current === project) return
    previousProject.current = project
    projectEpoch.current += 1
    requestSequence.current += 1
    const resetSections = createSectionState('character', 'standard', 3)
    setAssets([])
    setCatalogModels([])
    setReferenceCapabilities(null)
    setCapabilitiesLoadError('')
    setLlmCatalogModels([])
    setReferenceModelType('')
    setEditorModelType('')
    setReferenceModelCustomized(false)
    setEditorModelCustomized(false)
    setCandidateKind('image_pack')
    setAssetType('character')
    setSheetMode('production')
    setIntent('generic')
    setDepth('standard')
    setCustomSheetCount(3)
    setPreset('identity')
    setSections(resetSections)
    setCustomSectionInputs({})
    setAuthoringStatus('')
    setDetailSettings({})
    setPlanningModel('auto')
    setReviewModel('auto_local')
    setReferenceExplicitOutput(explicitOutputRef.current)
    setExplicitConvenience(false)
    setCharacterGender('unspecified')
    setCharacterAge('')
    setCharacterExplicitAnatomy([])
    setContentCapability(explicitOutputRef.current ? 'unrestricted_local' : 'standard')
    setInitialBlur(explicitOutputRef.current || privateOutputRef.current)
    setIntelligencePolicy(explicitOutputRef.current ? 'uncensored_auto' : 'standard_auto')
    setIntelligenceCustomized(false)
    setGenerationLoras([])
    setEditingLoras([])
    setGenerationLoraSchemas({})
    setEditingLoraSchemas({})
    setAdditionalLoras([])
    setPendingLoraScope('auto')
    setPendingLoraId('')
    setPendingLoraMultiplier(1)
    setLoraLoadError('')
    setAnatomyPrivate(true)
    setName('')
    setDescription('')
    setVisualStyle('')
    setCustomVisualStyle('')
    setSubmitting(false)
    setImporting(null)
    pendingSheetActionLocks.current.clear()
    authoredSettingsSnapshots.current.clear()
    loraParameterSnapshots.current.clear()
    authoringAvailabilityRef.current.clear()
    setAuthoringAvailability({})
    setPendingSheetActions({})
    setPendingFreshJobIds([])
    setEditVariantId(null)
    setEditInstruction('')
    setQueuedMessage('')
    setLoadError('')
    setModelLoadError('')
    setReviewerAction(null)
    setReviewerActionError('')
    setActionError('')
  }, [project])

  useLayoutEffect(() => {
    if (!projectExplicitlyLocked) return
    projectEpoch.current += 1
    requestSequence.current += 1
    const resetSections = createSectionState('character', 'standard', 3)
    pendingSheetActionLocks.current.clear()
    authoredSettingsSnapshots.current.clear()
    loraParameterSnapshots.current.clear()
    authoringAvailabilityRef.current.clear()
    setAuthoringAvailability({})
    setAssets([])
    setCatalogModels([])
    setReferenceCapabilities(null)
    setCapabilitiesLoadError('')
    setLlmCatalogModels([])
    setReferenceModelType('')
    setEditorModelType('')
    setReferenceModelCustomized(false)
    setEditorModelCustomized(false)
    setCandidateKind('image_pack')
    setAssetType('character')
    setSheetMode('production')
    setIntent('generic')
    setDepth('standard')
    setCustomSheetCount(3)
    setPreset('identity')
    setSections(resetSections)
    setCustomSectionInputs({})
    setAuthoringStatus('')
    setDetailSettings({})
    setPlanningModel('auto')
    setReviewModel('auto_local')
    setReferenceExplicitOutput(explicitOutputRef.current)
    setExplicitConvenience(false)
    setCharacterGender('unspecified')
    setCharacterAge('')
    setCharacterExplicitAnatomy([])
    setContentCapability(explicitOutputRef.current ? 'unrestricted_local' : 'standard')
    setInitialBlur(explicitOutputRef.current || privateOutputRef.current)
    setIntelligencePolicy(explicitOutputRef.current ? 'uncensored_auto' : 'standard_auto')
    setIntelligenceCustomized(false)
    setGenerationLoras([])
    setEditingLoras([])
    setGenerationLoraSchemas({})
    setEditingLoraSchemas({})
    setAdditionalLoras([])
    setPendingLoraScope('auto')
    setPendingLoraId('')
    setPendingLoraMultiplier(1)
    setLoraLoadError('')
    setAnatomyPrivate(true)
    setName('')
    setDescription('')
    setVisualStyle('')
    setCustomVisualStyle('')
    setLoading(false)
    setSubmitting(false)
    setImporting(null)
    setPendingSheetActions({})
    setPendingFreshJobIds([])
    setEditVariantId(null)
    setEditInstruction('')
    setQueuedMessage('')
    setLoadError('')
    setModelLoadError('')
    setReviewerAction(null)
    setReviewerActionError('')
    setActionError('')
  }, [projectExplicitlyLocked])

  useEffect(() => {
    if (active && projectExplicitlyLocked) setSidebarMode(referenceReturnMode)
  }, [active, projectExplicitlyLocked, referenceReturnMode, setSidebarMode])

  useEffect(() => {
    const preferred = getProjectReferencePreferredGenerationModel(
      sheetMode, referenceExplicitOutput, contentCapability, referenceCapabilities,
    )
    setReferenceModelType(current => {
      if (referenceModelCustomized) return selectProjectReferenceModel(referenceModels, current)
      return referenceModels.some(model => model.model_type === preferred) ? preferred : ''
    })
  }, [contentCapability, referenceCapabilities, referenceExplicitOutput, referenceModelCustomized, referenceModels, sheetMode])

  useEffect(() => {
    const preferred = referenceCapabilities?.default_models.editor_model ?? ''
    setEditorModelType(current => {
      if (editorModelCustomized) return selectProjectReferenceModel(editorModels, current)
      return editorModels.some(model => model.model_type === preferred) ? preferred : ''
    })
  }, [editorModelCustomized, editorModels, referenceCapabilities])

  useEffect(() => {
    if (planningModel !== 'auto' && planningModel !== 'deterministic'
      && !planningModels.some(model => model.id === planningModel)) setPlanningModel('auto')
  }, [planningModel, planningModels])

  useEffect(() => {
    if (mandatoryReview && reviewModel === 'off') {
      setReviewModel('auto_local')
      return
    }
    if (intelligencePolicy === 'uncensored_auto') {
      const exactLocalSelection = reviewModel === uncensoredReviewContract?.resolved_model
        && (selectedReviewModel?.provider ?? 'local') === uncensoredReviewContract?.resolved_provider
      if (reviewModel !== 'auto_local' && reviewModel !== 'off' && !exactLocalSelection) {
        setReviewModel('auto_local')
      }
      return
    }
    if (reviewModel !== 'auto_local' && reviewModel !== 'off'
      && !reviewModels.some(model => model.id === reviewModel)) setReviewModel('auto_local')
  }, [intelligencePolicy, mandatoryReview, reviewModel, reviewModels, selectedReviewModel?.provider, uncensoredReviewContract?.resolved_model, uncensoredReviewContract?.resolved_provider])

  useEffect(() => {
    let active = true
    setGenerationLoras([])
    setGenerationLoraSchemas({})
    if (!referenceModelType) return () => { active = false }
    void fetchLoraDetails(referenceModelType).then(result => {
      if (!active) return
      setGenerationLoras(result.loras.map(lora => lora.filename))
      setGenerationLoraSchemas(Object.fromEntries(result.loras.flatMap(lora => (
        lora.parameter_schema ? [[lora.filename, lora.parameter_schema]] : []
      ))))
      setLoraLoadError('')
    }).catch(() => {
      if (!active) return
      setLoraLoadError('Could not load LoRAs compatible with the creation model.')
    })
    return () => { active = false }
  }, [referenceModelType])

  useEffect(() => {
    let active = true
    setEditingLoras([])
    setEditingLoraSchemas({})
    if (!editorModelType || sheetMode === 'draft') return () => { active = false }
    void fetchLoraDetails(editorModelType).then(result => {
      if (!active) return
      setEditingLoras(result.loras.map(lora => lora.filename))
      setEditingLoraSchemas(Object.fromEntries(result.loras.flatMap(lora => (
        lora.parameter_schema ? [[lora.filename, lora.parameter_schema]] : []
      ))))
      setLoraLoadError('')
    }).catch(() => {
      if (!active) return
      setLoraLoadError('Could not load LoRAs compatible with the editor model.')
    })
    return () => { active = false }
  }, [editorModelType, sheetMode])

  useEffect(() => {
    if (pendingLoraId && !availablePendingLoras.includes(pendingLoraId)) setPendingLoraId('')
  }, [availablePendingLoras, pendingLoraId])

  useEffect(() => {
    if (!open || projectExplicitlyLocked) return
    const epoch = projectEpoch.current
    const submittedProject = project
    const catalogSequence = ++catalogRequestSequence.current
    let active = true
    setModelLoadError('')
    void fetchModels().then(data => {
      if (
        !active
        || catalogSequence !== catalogRequestSequence.current
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setCatalogModels(data.models)
    }).catch(() => {
      if (
        !active
        || catalogSequence !== catalogRequestSequence.current
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setCatalogModels([])
      setModelLoadError('Could not load Reference model choices.')
    })
    return () => {
      active = false
      if (catalogSequence === catalogRequestSequence.current) catalogRequestSequence.current += 1
    }
  }, [enabledModelsSignature, modelsLoaded, open, project, projectExplicitlyLocked])

  useEffect(() => {
    if (
      open
      && project
      && selectedRecipeTermRequirements.length > 0
      && !hostTerms
      && !hostTermsLoading
    ) void loadHostTerms()
  }, [hostTerms, hostTermsLoading, loadHostTerms, open, project, selectedRecipeTermRequirements])

  useEffect(() => {
    if (!open || projectExplicitlyLocked) return
    const epoch = projectEpoch.current
    const submittedProject = project
    let active = true
    setCapabilitiesLoadError('')
    void fetchProjectReferenceCapabilities(project).then(capabilities => {
      if (
        !active
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setReferenceCapabilities(capabilities)
    }).catch(reason => {
      if (
        !active
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setReferenceCapabilities(null)
      setCapabilitiesLoadError(projectReferenceSafeErrorMessage(
        reason,
        'Could not load the Reference Studio plan.',
      ))
    })
    return () => { active = false }
  }, [open, project, projectExplicitlyLocked])

  useEffect(() => {
    if (!open || projectExplicitlyLocked) return
    const epoch = projectEpoch.current
    const submittedProject = project
    let active = true
    void fetchLlmModels(project).then(data => {
      if (
        !active
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setLlmCatalogModels(data.models)
    }).catch(() => {
      if (
        !active
        || !isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )
      ) return
      setLlmCatalogModels([])
    })
    return () => { active = false }
  }, [open, project, projectExplicitlyLocked])

  const refresh = useCallback(async (signal: AbortSignal) => {
    if (!open || browsingUploads || projectExplicitlyLocked) return
    const sequence = ++requestSequence.current
    try {
      const next = await fetchProjectAssets(project)
      if (signal.aborted || sequence !== requestSequence.current) return
      setAssets(next)
      const completedJobIds = new Set(next.flatMap(asset => (
        asset.variants.map(variant => variant.metadata.job?.id).filter((id): id is string => Boolean(id))
      )))
      setPendingSheetActions(current => {
        const entries = Object.entries(current).filter(([key, action]) => {
          const keep = action.project === project && (!action.jobId || !completedJobIds.has(action.jobId))
          if (!keep) pendingSheetActionLocks.current.delete(key)
          return keep
        })
        return entries.length === Object.keys(current).length ? current : Object.fromEntries(entries)
      })
      setLoadError('')
    } catch (reason) {
      if (signal.aborted || sequence !== requestSequence.current) return
      setLoadError(projectReferenceSafeErrorMessage(reason, 'Failed to load project references.'))
    } finally {
      if (!signal.aborted && sequence === requestSequence.current) setLoading(false)
    }
  }, [open, browsingUploads, project, projectExplicitlyLocked])

  const refreshNow = useVisibilityPolling(
    refresh,
    POLL_INTERVAL_MS.referencesVisible,
    { enabled: open && !browsingUploads && !projectExplicitlyLocked, immediate: false },
  )

  const requestRefresh = useCallback(() => {
    requestSequence.current += 1
    refreshNow()
  }, [refreshNow])

  const refreshReviewerSetup = useCallback(async (loadRequired = false) => {
    const modelId = uncensoredReviewContract?.resolved_model
    if (loadRequired && (!machineControls || !modelId)) return
    const epoch = projectEpoch.current
    const submittedProject = project
    setReviewerAction(loadRequired ? 'loading' : 'refreshing')
    setReviewerActionError('')
    try {
      if (loadRequired) {
        await loadLlm({ model_id: modelId, provider: 'local' })
        if (!isProjectAssetOperationCurrent(
          submittedProject, epoch, currentProject.current, projectEpoch.current,
        )) return
      }
      const [llmCatalog, capabilities] = await Promise.all([
        fetchLlmModels(project),
        fetchProjectReferenceCapabilities(project),
      ])
      if (!isProjectAssetOperationCurrent(
        submittedProject, epoch, currentProject.current, projectEpoch.current,
      )) return
      setLlmCatalogModels(llmCatalog.models)
      setReferenceCapabilities(capabilities)
    } catch {
      if (!isProjectAssetOperationCurrent(
        submittedProject, epoch, currentProject.current, projectEpoch.current,
      )) return
      setReviewerActionError(loadRequired
        ? 'Could not prepare the required visual review model. Check the local model service, then refresh its status.'
        : 'Could not refresh the visual review model. Check the local model service and try again.')
    } finally {
      if (isProjectAssetOperationCurrent(
        submittedProject, epoch, currentProject.current, projectEpoch.current,
      )) setReviewerAction(null)
    }
  }, [machineControls, project, uncensoredReviewContract?.resolved_model])

  useEffect(() => {
    if (!open) return
    const syncVisibility = () => setReviewerPageVisible(document.visibilityState !== 'hidden')
    syncVisibility()
    document.addEventListener('visibilitychange', syncVisibility)
    return () => document.removeEventListener('visibilitychange', syncVisibility)
  }, [open])

  useEffect(() => {
    if (!reviewerNeedsAutomaticRefresh) return
    const epoch = projectEpoch.current
    const submittedProject = project
    const sequence = ++reviewerAutoRefreshSequence.current
    let cancelled = false
    let attempt = 0
    let timeoutId: ReturnType<typeof setTimeout> | null = null

    const isCurrent = () => (
      !cancelled
      && sequence === reviewerAutoRefreshSequence.current
      && isProjectAssetOperationCurrent(
        submittedProject, epoch, currentProject.current, projectEpoch.current,
      )
    )
    const scheduleNext = () => {
      if (!isCurrent() || attempt >= REVIEWER_AUTO_REFRESH_DELAYS_MS.length) return
      const delay = REVIEWER_AUTO_REFRESH_DELAYS_MS[attempt]
      attempt += 1
      timeoutId = setTimeout(() => { void pollReviewerSetup() }, delay)
    }
    const pollReviewerSetup = async () => {
      if (!isCurrent()) return
      try {
        const [llmCatalog, capabilities] = await Promise.all([
          fetchLlmModels(submittedProject),
          fetchProjectReferenceCapabilities(submittedProject),
        ])
        if (!isCurrent()) return
        setLlmCatalogModels(llmCatalog.models)
        setReferenceCapabilities(capabilities)
        const next = capabilities.uncensored_auto_review
        if (next.queue_ready
          || next.setup_state === 'missing_model'
          || next.setup_state === 'missing_projector'
          || next.setup_state === 'ready_unloaded'
          || next.setup_state === 'ready_resident') return
        if (next.setup_state !== 'loading' && next.setup_state !== 'loaded_without_vision') return
      } catch {
        if (!isCurrent()) return
        // Automatic refresh is best-effort; the visible manual Refresh action
        // remains the explicit error-reporting fallback.
      }
      scheduleNext()
    }

    scheduleNext()
    return () => {
      cancelled = true
      if (sequence === reviewerAutoRefreshSequence.current) reviewerAutoRefreshSequence.current += 1
      if (timeoutId !== null) clearTimeout(timeoutId)
    }
  }, [project, reviewerNeedsAutomaticRefresh])

  useEffect(() => {
    if (!open || browsingUploads || projectExplicitlyLocked || privateAuthoringTargetSignature === '[]') return
    const targets = JSON.parse(privateAuthoringTargetSignature) as Array<{
      assetId: string
      variantId: string
      authoredSeal: string
      planSeal: string
      needsAuthoring: boolean
      needsStyle: boolean
      styleCommitment: string
      characterReplayContract: Pick<NonNullable<ProjectAssetVariant['metadata']['reference_pack']>,
        'explicit_convenience' | 'authored_settings'>
      needsLoraParameters: boolean
      parameterRecords: Array<{
        id: string
        count: number
        ids: string[]
        schemaDigest: string
        valuesDigest: string
        expansionDigest: string
      }>
    }>
    const controller = new AbortController()
    const epoch = projectEpoch.current
    const startedKeys: string[] = []
    const availabilityCache = authoringAvailabilityRef.current
    const publish = (key: string, availability: ReferenceAuthoringAvailability) => {
      availabilityCache.set(key, availability)
      setAuthoringAvailability(current => current[key] === availability
        ? current
        : { ...current, [key]: availability })
    }
    for (const target of targets) {
      const key = projectAssetVariantOperationKey(project, target.assetId, target.variantId)
      if ((target.needsAuthoring && !target.authoredSeal)
        || (target.needsStyle && !/^[0-9a-f]{64}$/.test(target.styleCommitment))
        || (target.needsLoraParameters && !target.planSeal)) {
        publish(key, 'unavailable')
        continue
      }
      const authoredSnapshot = authoredSettingsSnapshots.current.get(target.authoredSeal)
      const characterReplayReady = isProjectReferenceCharacterReplayReady(
        target.characterReplayContract,
        authoredSnapshot ? {
          character_profile: authoredSnapshot.characterProfile,
          explicit_convenience: authoredSnapshot.explicitConvenience,
        } : undefined,
      )
      const authoringReady = !target.needsAuthoring
        || Boolean(authoredSnapshot
          && characterReplayReady
          && (!target.needsStyle || authoredSnapshot.style.trim().length > 0))
      const loraParametersReady = !target.needsLoraParameters
        || loraParameterSnapshots.current.has(target.planSeal)
      if (authoringReady && loraParametersReady) {
        publish(key, 'ready')
        continue
      }
      if (availabilityCache.get(key) === 'loading') continue
      publish(key, 'loading')
      startedKeys.push(key)
      void fetchProjectReferenceAuthoring(
        project, target.assetId, target.variantId, controller.signal,
      ).then(response => {
        if (controller.signal.aborted
          || !isProjectAssetOperationCurrent(project, epoch, currentProject.current, projectEpoch.current)) return
        if (response.schema_version !== 2
          || response.asset_id !== target.assetId
          || response.variant_id !== target.variantId
          || (target.needsAuthoring && response.authored_settings.seal !== target.authoredSeal)) {
          publish(key, 'unavailable')
          return
        }
        if (target.needsAuthoring) {
          if (target.needsStyle
            && (typeof response.authored_settings.style !== 'string'
              || response.authored_settings.style.trim().length === 0)) {
            publish(key, 'unavailable')
              return
            }
          if (!isProjectReferenceCharacterReplayReady(
            target.characterReplayContract,
            response.authored_settings,
          )) {
            publish(key, 'unavailable')
            return
          }
          authoredSettingsSnapshots.current.set(
            target.authoredSeal,
            cloneReferenceAuthoredSnapshot(
              response.authored_settings.type_fields,
              response.authored_settings.detail_callouts,
              response.authored_settings.style,
              response.authored_settings.character_profile,
              response.authored_settings.explicit_convenience,
            ),
          )
        }
        if (target.needsLoraParameters) {
          const privateLoras = response.additional_loras
          const privateById = new Map((privateLoras ?? []).map(lora => [lora.id, lora]))
          const exactReplay = target.parameterRecords.every(recorded => {
            const privateLora = privateById.get(recorded.id)
            const privateIds = Object.keys(privateLora?.parameter_values ?? {})
            return privateLora?.parameter_schema_digest === recorded.schemaDigest
              && privateLora.parameter_values_digest === recorded.valuesDigest
              && privateLora.parameter_expansion_digest === recorded.expansionDigest
              && privateIds.length === recorded.count
              && privateIds.every((id, index) => id === recorded.ids[index])
          })
          if (!privateLoras || !exactReplay) {
            publish(key, 'unavailable')
            return
          }
          loraParameterSnapshots.current.set(target.planSeal, cloneAdditionalLoras(privateLoras))
        }
        publish(key, 'ready')
      }).catch(() => {
        if (controller.signal.aborted
          || !isProjectAssetOperationCurrent(project, epoch, currentProject.current, projectEpoch.current)) return
        publish(key, 'unavailable')
      })
    }
    return () => {
      controller.abort()
      for (const key of startedKeys) {
        if (availabilityCache.get(key) === 'loading') {
          availabilityCache.delete(key)
        }
      }
    }
  }, [browsingUploads, open, privateAuthoringTargetSignature, privateReplayRetry, project, projectExplicitlyLocked])

  useEffect(() => {
    requestSequence.current += 1
    if (!open || browsingUploads || projectExplicitlyLocked) {
      setLoading(false)
      return
    }
    setLoading(true)
    if (!document.hidden) refreshNow()
  }, [open, browsingUploads, project, projectExplicitlyLocked, refreshNow])

  useEffect(() => () => { requestSequence.current += 1 }, [])

  useEffect(() => {
    if (projectExplicitlyLocked) return
    const failed = Object.entries(pendingSheetActions).find(([, action]) => {
      if (action.project !== project || !action.jobId) return false
      const job = jobs.find(candidate => candidate.id === action.jobId)
      return job?.status === 'failed' || job?.status === 'cancelled'
    })
    if (!failed) return
    const [key, action] = failed
    const job = jobs.find(candidate => candidate.id === action.jobId)
    pendingSheetActionLocks.current.delete(key)
    setPendingSheetActions(current => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setActionError(job?.status === 'cancelled'
      ? 'Reference-sheet generation was cancelled.'
      : 'Reference-sheet generation failed. Review the job status and try again.')
  }, [jobs, pendingSheetActions, project, projectExplicitlyLocked])

  useEffect(() => {
    if (projectExplicitlyLocked || pendingFreshJobIds.length === 0) return
    const terminal = pendingFreshJobIds.filter(jobId => {
      const status = jobs.find(candidate => candidate.id === jobId)?.status
      return status === 'completed' || status === 'failed' || status === 'cancelled'
    })
    if (terminal.length === 0) return
    const failed = terminal.some(jobId => {
      const status = jobs.find(candidate => candidate.id === jobId)?.status
      return status === 'failed' || status === 'cancelled'
    })
    setPendingFreshJobIds(current => current.filter(jobId => !terminal.includes(jobId)))
    if (failed) {
      setActionError('Reference-sheet generation did not complete. Review the job status and try again.')
    }
  }, [jobs, pendingFreshJobIds, projectExplicitlyLocked])

  const changeAssetType = (nextType: ProjectReferenceAssetType) => {
    const transition = getProjectReferenceCreationTransition(
      { candidateKind, assetType },
      { kind: 'select_asset_type', assetType: nextType },
    )
    setCandidateKind(transition.candidateKind)
    if (!transition.assetTypeChanged) return
    const definition = REFERENCE_TYPE_DEFINITIONS[nextType]
    const usesCharacterConvenience = nextType === 'character' && explicitConvenience
    const nextPreset = usesCharacterConvenience ? 'anatomy' : definition.defaultPreset
    const nextSections = createSectionState(nextType, depth, customSheetCount)
    setAssetType(nextType)
    setPreset(nextPreset)
    setSections(usesCharacterConvenience
      ? selectCanonicalCharacterAnatomy(
        nextSections,
        referenceCapabilities?.reference_types.find(capability => capability.id === nextType),
      )
      : nextSections)
    setCustomSectionInputs({})
    setAuthoringStatus('')
    setDetailSettings({})
    if (!intelligenceCustomized) {
      setIntelligencePolicy('standard_auto')
    }
  }

  const changeCreationMethod = (nextMethod: ProjectReferenceCreationMethod) => {
    const transition = getProjectReferenceCreationTransition(
      { candidateKind, assetType },
      { kind: 'select_method', candidateKind: nextMethod },
    )
    setCandidateKind(transition.candidateKind)
  }

  const changeIntent = (nextIntent: ProjectReferenceIntent) => {
    setIntent(nextIntent)
    if (nextIntent === 'exact_spec') {
      setDetailSettings(current => Object.fromEntries(
        Object.entries(current).map(([id, setting]) => [
          id, { ...setting, operation: setting.operation === 'reconstruct' ? 'auto' : setting.operation },
        ]),
      ))
    }
  }

  const changeDepth = (nextDepth: ProjectReferenceDepth) => {
    setDepth(nextDepth)
    const definitions = sectionDefinitions
    setSections(current => {
      const next = current.map(section => {
        if (section.pinned) return section
        const definition = definitions.find(candidate => candidate.id === section.id)
        return definition
          ? { ...section, values: defaultSectionValues(definition, nextDepth, customSheetCount) }
          : section
      })
      return assetType === 'character' && explicitConvenience
        ? selectCanonicalCharacterAnatomy(next, authoritativeTypeCapabilities)
        : next
    })
  }

  const changeCustomSheetCount = (nextCount: number) => {
    const bounded = Math.max(1, Math.min(5, nextCount || 1))
    setCustomSheetCount(bounded)
    if (depth !== 'custom') return
    const definitions = sectionDefinitions
    setSections(current => {
      const next = current.map(section => {
        if (section.pinned) return section
        const definition = definitions.find(candidate => candidate.id === section.id)
        return definition
          ? { ...section, values: defaultSectionValues(definition, 'custom', bounded) }
          : section
      })
      return assetType === 'character' && explicitConvenience
        ? selectCanonicalCharacterAnatomy(next, authoritativeTypeCapabilities)
        : next
    })
  }

  const changePreset = (nextPreset: ProjectReferencePreset) => {
    const exitsCharacterConvenience = assetType === 'character'
      && explicitConvenience && nextPreset !== 'anatomy'
    if (exitsCharacterConvenience) setExplicitConvenience(false)
    setPreset(nextPreset)
    if (nextPreset === 'anatomy') setContentCapability('unrestricted_local')
    setSections(current => current.map(section => {
      if (exitsCharacterConvenience && section.id === 'anatomy') {
        return { ...section, values: [], pinned: false }
      }
      if (section.pinned) return section
      const definition = sectionDefinitions.find(item => item.id === section.id)
      if (!definition) return section
      if (section.id === 'anatomy') {
        const anatomyLabel = assetType === 'character' && explicitConvenience
          ? 'nude anatomy'
          : 'anatomy'
        const anatomy = sectionOptionItem(authoritativeTypeCapabilities, definition, anatomyLabel)
        return { ...section, values: nextPreset === 'anatomy' && anatomy ? [anatomy] : [] }
      }
      if (section.id === 'wardrobe' && nextPreset === 'underlayers') {
        return {
          ...section,
          values: ['primary outfit', 'underwear / underlayers', 'individual garments', 'accessories']
            .flatMap(label => {
              const option = sectionOptionItem(authoritativeTypeCapabilities, definition, label)
              return option ? [option] : []
            }),
        }
      }
      return section
    }))
    if (!intelligenceCustomized) {
      setIntelligencePolicy(nextPreset === 'anatomy' || referenceExplicitOutput
        ? 'uncensored_auto'
        : 'standard_auto')
    }
  }

  const applyExplicitConvenience = (enabled: boolean) => {
    setExplicitConvenience(enabled)
    if (!enabled) return
    setReferenceExplicitOutput(true)
    setPreset('anatomy')
    setSections(current => selectCanonicalCharacterAnatomy(
      current, authoritativeTypeCapabilities,
    ))
  }

  const addAdditionalLora = () => {
    if (!pendingLoraId || !Number.isFinite(pendingLoraMultiplier)
      || pendingLoraMultiplier < -10 || pendingLoraMultiplier > 10
      || pendingLoraSchemaConflict
      || additionalLoras.length >= 64) return
    const schema = resolveLoraSchema(pendingLoraId, pendingLoraScope)
    setAdditionalLoras(current => [...current, {
      id: pendingLoraId,
      multiplier: pendingLoraMultiplier,
      scope: pendingLoraScope,
      parameter_schema_digest: schema?.schema_digest,
      parameter_values: schema ? getLoraParameterDefaults(schema) : undefined,
    }])
    setPendingLoraId('')
    setPendingLoraMultiplier(1)
  }

  const updateAdditionalLora = (id: string, patch: Partial<ProjectReferenceAdditionalLora>) => {
    setAdditionalLoras(current => current.map(lora => lora.id === id ? { ...lora, ...patch } : lora))
  }

  const updateAdditionalLoraParameter = (
    id: string,
    parameterId: string,
    value: LoraParameterValue | undefined,
  ) => {
    setAdditionalLoras(current => current.map(lora => {
      if (lora.id !== id) return lora
      const parameterValues = { ...(lora.parameter_values ?? {}) }
      if (value === undefined) delete parameterValues[parameterId]
      else parameterValues[parameterId] = value
      return { ...lora, parameter_values: parameterValues }
    }))
  }

  const loraCompatibilityCopy = (lora: ProjectReferenceAdditionalLora) => {
    const generationCompatible = generationLoras.includes(lora.id)
    const editingCompatible = sheetMode !== 'draft' && editingLoras.includes(lora.id)
    if (lora.scope === 'generation') return generationCompatible ? 'Used for the main image' : 'Does not work with the creation model'
    if (lora.scope === 'editing') return editingCompatible ? 'Used for variations' : 'Does not work with the editor model'
    const applied = [generationCompatible ? 'Main image' : '', editingCompatible ? 'Variations' : ''].filter(Boolean)
    return applied.length > 0 ? `Best fit: ${applied.join(' + ')}` : 'Not used by the selected models'
  }

  const toggleSectionValue = (sectionId: ReferenceSectionId, value: string) => {
    const definition = sectionDefinitions.find(item => item.id === sectionId)
    if (!definition) return
    const option = sectionOptionItem(authoritativeTypeCapabilities, definition, value)
    if (!option) return
    const selectedSection = sections.find(section => section.id === sectionId)
    if (sectionId === 'details'
      && !selectedSection?.values.some(item => item.id === option.id)
      && (selectedSection?.values.length ?? 0) >= 8) {
      setAuthoringStatus('A reference pack can contain at most 8 detail outputs.')
      return
    }
    if (sectionId === 'anatomy' && !intelligenceCustomized) {
      const section = sections.find(candidate => candidate.id === 'anatomy')
      const willContainAnatomy = !(section?.values.some(item => item.id === option.id) ?? false)
        && (option.label.toLowerCase().includes('anatomy') || option.label.toLowerCase().includes('nude'))
      const keepsAnatomy = (section?.values ?? []).some(candidate => (
        candidate.id !== option.id && (candidate.label.toLowerCase().includes('anatomy') || candidate.label.toLowerCase().includes('nude'))
      ))
      setIntelligencePolicy(preset === 'anatomy' || willContainAnatomy || keepsAnatomy || referenceExplicitOutput
        ? 'uncensored_auto'
        : 'standard_auto')
      if (willContainAnatomy) setContentCapability('unrestricted_local')
    }
    setSections(current => current.map(section => section.id === sectionId
      ? {
          ...section,
          pinned: true,
          values: orderSectionValues(definition, section.values.some(item => item.id === option.id)
            ? section.values.filter(item => item.id !== option.id)
            : [...section.values, option]),
        }
      : section))
    if (sectionId === 'details') {
      setDetailSettings(current => ({
        ...current,
        [option.id]: current[option.id] ?? {
          operation: 'auto',
          sourceRole: validDetailSourceRoles[0] ?? '',
        },
      }))
    }
  }

  const addCustomSectionValue = (sectionId: ReferenceSectionId) => {
    const value = customSectionInputs[sectionId]?.trim()
    if (!value) return
    const section = sections.find(item => item.id === sectionId)
    if (section?.values.some(item => item.label.toLowerCase() === value.toLowerCase())) {
      setAuthoringStatus('That value is already selected in this section.')
      return
    }
    if ((section?.values.length ?? 0) >= (sectionId === 'details' ? 8 : 64)) {
      setAuthoringStatus(sectionId === 'details'
        ? 'A reference pack can contain at most 8 detail outputs.'
        : 'This section has reached its authored-value limit.')
      return
    }
    const authored: ProjectReferenceTypeFieldItem = {
      id: newCustomAuthoredId(),
      label: value,
      custom: true,
      group: sectionId,
    }
    setSections(current => current.map(section => section.id === sectionId
      ? { ...section, pinned: true, values: [...section.values, authored] }
      : section))
    if (sectionId === 'details') {
      setDetailSettings(current => ({
        ...current,
        [authored.id]: { operation: 'auto', sourceRole: validDetailSourceRoles[0] ?? '' },
      }))
    }
    setCustomSectionInputs(current => ({ ...current, [sectionId]: '' }))
    setAuthoringStatus('Custom value added. You can edit or remove it below.')
  }

  const updateCustomSectionValue = (sectionId: ReferenceSectionId, id: string, label: string) => {
    const duplicate = sections.find(section => section.id === sectionId)?.values.some(item => (
      item.id !== id && item.label.toLowerCase() === label.trim().toLowerCase()
    ))
    if (duplicate) {
      setAuthoringStatus('That value is already selected in this section.')
      return
    }
    setSections(current => current.map(section => section.id === sectionId
      ? {
          ...section,
          pinned: true,
          values: section.values.map(item => item.id === id ? { ...item, label } : item),
        }
      : section))
    setAuthoringStatus(label.trim() ? 'Custom value updated.' : 'Custom values need a label before queueing.')
  }

  const removeSectionValue = (sectionId: ReferenceSectionId, id: string) => {
    setSections(current => current.map(section => section.id === sectionId
      ? { ...section, pinned: true, values: section.values.filter(item => item.id !== id) }
      : section))
    if (sectionId === 'details') {
      setDetailSettings(current => Object.fromEntries(
        Object.entries(current).filter(([itemId]) => itemId !== id),
      ))
    }
    setAuthoringStatus('Custom value removed.')
  }

  const resetSection = (definition: ReferenceSectionDefinition) => {
    setSections(current => current.map(section => section.id === definition.id
      ? {
          ...section,
          pinned: false,
          values: definition.id === 'anatomy' && preset === 'anatomy'
            ? [sectionOptionItem(authoritativeTypeCapabilities, definition, 'anatomy')].filter(
                (item): item is ProjectReferenceTypeFieldItem => item !== null,
              )
            : defaultSectionValues(definition, depth, customSheetCount).flatMap(item => {
                const option = sectionOptionItem(authoritativeTypeCapabilities, definition, item.label)
                return option ? [option] : []
              }),
        }
      : section))
    if (definition.id === 'anatomy' && !intelligenceCustomized) {
      setIntelligencePolicy(preset === 'anatomy' || referenceExplicitOutput
        ? 'uncensored_auto'
        : 'standard_auto')
    }
    if (definition.id === 'details') setDetailSettings({})
    setAuthoringStatus(`${definition.label} reset to defaults.`)
  }

  const generate = async () => {
    if (queueBlockers.length > 0) return
    const requestId = createProjectReferenceRequestId()
    const epoch = projectEpoch.current
    const submittedProject = project
    setSubmitting(true)
    setQueuedMessage('')
    const authoredStyle = visualStyle === 'custom'
      ? customVisualStyle.trim()
      : visualStyle
    try {
      const response = await generateProjectAssetReferences(project, {
        request_id: requestId,
        schema_version: 2,
        name: name.trim(),
        asset_type: assetType,
        description: description.trim(),
        style: authoredStyle || undefined,
        mode: sheetMode,
        intent,
        depth,
        sheet_count: depth === 'custom' ? sheetCount : undefined,
        preset,
        anchor_basis: anchorBasis,
        type_fields: typeFields,
        detail_callouts: detailCallouts,
        managed_layout_assist: 'off',
        planning_model: planningModel,
        planning_provider: planningModel === 'auto' || planningModel === 'deterministic'
          ? undefined
          : selectedPlanningModel?.provider,
        review_model: reviewModel,
        review_provider: reviewModel === 'auto_local' || reviewModel === 'off'
          ? undefined
          : selectedReviewModel?.provider,
        review: mandatoryReview || reviewModel !== 'off',
        content_capability: contentCapability,
        initial_blur: initialBlur,
        intelligence_policy: intelligencePolicy,
        additional_loras: additionalLoras,
        candidate_count: candidateCount,
        columns,
        palette_swatches: paletteSwatches,
        max_repair_attempts: effectiveMaxRepairAttempts,
        model_type: referenceModelType,
        editor_model_type: sheetMode !== 'draft' ? editorModelType : undefined,
        private_output: anchorBasis === 'anatomy' ? anatomyPrivate : privateOutput,
        explicit_output: referenceExplicitOutput,
        character_profile: assetType === 'character'
          ? characterProfileSerialization.profile
          : undefined,
        explicit_convenience: assetType === 'character'
          ? explicitConvenience
          : undefined,
      })
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestQueueView()
      setName('')
      setDescription('')
      setPendingFreshJobIds(current => [...new Set([...current, response.job_id])])
      const queuedSheets = response.plan?.sheet_count ?? sheetCount
      setQueuedMessage('Reference submission accepted; confirming it with the Queue.')
      requestRefresh()
      const authoredSeal = response.plan?.authored_settings?.seal
      const acceptedSnapshot = cloneReferenceAuthoredSnapshot(
        typeFields,
        detailCallouts,
        authoredStyle,
        characterProfileSerialization.profile,
        explicitConvenience,
      )
      if (authoredSeal && isProjectReferenceStyleReplayReady(
        response.plan?.authored_settings, authoredStyle,
      ) && isProjectReferenceCharacterReplayReady(response.plan, {
        character_profile: acceptedSnapshot.characterProfile,
        explicit_convenience: acceptedSnapshot.explicitConvenience,
      })) {
        authoredSettingsSnapshots.current.set(
          authoredSeal,
          acceptedSnapshot,
        )
      }
      if (response.plan?.plan_seal) {
        loraParameterSnapshots.current.set(
          response.plan.plan_seal,
          cloneAdditionalLoras(additionalLoras),
        )
      }
      const jobConfirmed = await confirmAcceptedProjectReferenceJob(
        response.job_id,
        jobId => confirmReconnectedJob(
          jobId,
          reconnectJobs,
          () => useStore.getState().jobs,
        ),
      )
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setQueuedMessage(jobConfirmed
        ? `Queued ${candidateCount} ${candidateCount === 1 ? 'candidate pack' : 'candidate packs'} with ${queuedSheets} ${queuedSheets === 1 ? 'sheet' : 'sheets'} each. They will appear here when complete.`
        : 'Reference submission accepted; Queue confirmation is still catching up. The accepted pack will appear here when the Queue reconnects.')
      // Queue navigation and accepted-state updates happen before confirmation;
      // the Reference peer remains mounted with the retained next-run defaults.
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setActionError(projectReferenceSafeErrorMessage(reason, 'Could not queue reference generation.'))
    } finally {
      if (isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) setSubmitting(false)
    }
  }

  const generateFromVariant = async (
    asset: ProjectAsset,
    variant: ProjectAssetVariant,
    instruction?: string,
  ) => {
    const sourceAuthoredSeal = variant.metadata.reference_pack?.authored_settings?.seal
    const requiresPrivateAuthoring = projectReferenceRetryNeedsPrivateAuthoring(variant)
    if (requiresPrivateAuthoring && (!sourceAuthoredSeal
      || !authoredSettingsSnapshots.current.has(sourceAuthoredSeal))) {
      setActionError('The private creation settings for this candidate are unavailable. Retry and Edit stay off so none of your style, profile, custom fields, or details are lost. Create a new pack instead.')
      return
    }
    const sourceAssetType = normalizeProjectReferenceAssetType(asset.asset_type) ?? assetType
    const sourcePreset = sourceAssetType === assetType
      ? preset
      : REFERENCE_TYPE_DEFINITIONS[sourceAssetType].defaultPreset
    const sourceAnchorBasis = sourceAssetType === assetType
      ? anchorBasis
      : sourceAssetType === 'character' || sourceAssetType === 'creature'
        ? 'primary_outfit'
        : 'least_occluded'
    const sourceAuthoredSnapshot = sourceAuthoredSeal
      ? authoredSettingsSnapshots.current.get(sourceAuthoredSeal)
      : undefined
    const sourcePackMetadata = variant.metadata.reference_pack
    if (!isProjectReferenceStyleReplayReady(
      sourcePackMetadata?.authored_settings, sourceAuthoredSnapshot?.style,
    )) {
      setActionError('The private style settings are unavailable or have changed. Retry and Edit remain off; create a new pack instead.')
      return
    }
    if (!isProjectReferenceCharacterReplayReady(
      sourcePackMetadata,
      sourceAuthoredSnapshot ? {
        character_profile: sourceAuthoredSnapshot.characterProfile,
        explicit_convenience: sourceAuthoredSnapshot.explicitConvenience,
      } : undefined,
    )) {
      setActionError('The private Character profile or anatomy-detail settings are unavailable or have changed. Retry and Edit remain off; create a new pack instead.')
      return
    }
    const sourcePlanSeal = sourcePackMetadata?.plan_seal
    const summarizedParameterizedLoras = [
      ...(sourcePackMetadata?.additional_loras?.applied ?? []),
      ...(sourcePackMetadata?.additional_loras?.skipped ?? []),
    ].filter(hasProjectReferenceLoraParameterSummary)
    const privateLoraSnapshot = sourcePlanSeal
      ? loraParameterSnapshots.current.get(sourcePlanSeal)
      : undefined
    if (summarizedParameterizedLoras.length > 0 && !privateLoraSnapshot) {
      setActionError('The private LoRA settings for this candidate are unavailable. Retry and Edit stay off so no values are guessed or lost. Create a new pack instead.')
      return
    }
    const sourceSettings = getProjectReferenceRetrySettings(variant, {
      mode: sheetMode,
      model_type: referenceModelType,
      editor_model_type: editorModelType,
      private_output: privateOutput,
      explicit_output: referenceExplicitOutput,
      content_capability: contentCapability,
      initial_blur: initialBlur,
      intelligence_policy: intelligencePolicy,
      additional_loras: additionalLoras,
      review: reviewModel !== 'off',
      max_repair_attempts: effectiveMaxRepairAttempts,
      schema_version: variant.variant_type === 'reference_pack' ? 2 : undefined,
      asset_type: sourceAssetType,
      intent,
      depth,
      sheet_count: depth === 'custom' ? sheetCount : undefined,
      preset: sourcePreset,
      anchor_basis: sourceAnchorBasis,
      detail_callouts: sourceAuthoredSnapshot?.detailCallouts
        ?? (sourceAssetType === assetType ? authoredDetailCallouts : []),
      type_fields: sourceAuthoredSnapshot?.typeFields
        ?? (sourceAssetType === assetType ? typeFields : {}),
      authored_settings_seal: sourceAuthoredSnapshot ? sourceAuthoredSeal : undefined,
      style: sourceAuthoredSnapshot?.style,
      character_profile: sourceAuthoredSnapshot?.characterProfile,
      explicit_convenience: sourceAuthoredSnapshot?.explicitConvenience
        ?? sourcePackMetadata?.explicit_convenience,
      managed_layout_assist: 'off',
      planning_model: planningModel,
      planning_provider: planningModel === 'auto' || planningModel === 'deterministic'
        ? undefined
        : selectedPlanningModel?.provider,
      review_model: reviewModel,
      review_provider: reviewModel === 'auto_local' || reviewModel === 'off'
        ? undefined
        : selectedReviewModel?.provider,
    }, referenceCapabilities ?? undefined)
    if (privateLoraSnapshot) {
      sourceSettings.additional_loras = cloneAdditionalLoras(privateLoraSnapshot)
    }
    const parameterizedSelections = (sourceSettings.additional_loras ?? []).filter(
      lora => Boolean(lora.parameter_schema_digest),
    )
    if (parameterizedSelections.length > 0) {
      const sourceModels = [...new Set([
        sourceSettings.model_type,
        ...(sourceSettings.mode !== 'draft' && sourceSettings.editor_model_type
          ? [sourceSettings.editor_model_type]
          : []),
      ])]
      try {
        const detailResponses = await Promise.all(sourceModels.map(async modelType => ({
          modelType,
          details: await fetchLoraDetails(modelType),
        })))
        const generationDetails = detailResponses.find(
          response => response.modelType === sourceSettings.model_type,
        )?.details
        const editingDetails = sourceSettings.mode !== 'draft' && sourceSettings.editor_model_type
          ? detailResponses.find(response => (
              response.modelType === sourceSettings.editor_model_type
            ))?.details
          : undefined
        for (const lora of parameterizedSelections) {
          const generationLora = generationDetails?.loras.find(candidate => candidate.filename === lora.id)
          const editingLora = editingDetails?.loras.find(candidate => candidate.filename === lora.id)
          if (loraParameterSchemasConflict(
            generationLora?.parameter_schema,
            editingLora?.parameter_schema,
            lora.scope,
            Boolean(generationLora),
            Boolean(editingLora),
          )) {
            throw new Error(`${lora.id} no longer offers one set of settings that works with both saved models.`)
          }
          const schema = lora.scope === 'generation'
            ? generationLora?.parameter_schema
            : lora.scope === 'editing'
              ? editingLora?.parameter_schema
              : generationLora?.parameter_schema ?? editingLora?.parameter_schema
          if (!schema || schema.schema_digest !== lora.parameter_schema_digest) {
            throw new Error(`${lora.id} no longer has the saved settings. Retry and Edit are off until the LoRA is restored; Maestro will not guess or change values.`)
          }
          const errors = validateLoraParameterValues(schema, lora.parameter_values)
          if (errors.length > 0) {
            throw new Error(`${lora.id} has invalid recorded inputs: ${errors.join(' ')}`)
          }
        }
      } catch (error) {
        setActionError(error instanceof Error
          ? `Could not verify exact LoRA inputs: ${error.message}`
          : 'Could not verify exact LoRA inputs for Retry or Edit.')
        return
      }
    }
    const retryReview = resolveProjectReferenceRetryReview(
      sourceSettings,
      { review_model: reviewModel, review_provider: selectedReviewModel?.provider },
      reviewModels,
      referenceCapabilities,
    )
    if (!retryReview.ready) {
      setActionError('Retry and Edit require a compatible vision reviewer for this unrestricted or explicit source pack. Load and select an eligible reviewer first.')
      return
    }
    if (retryReview.use_current_reviewer) {
      sourceSettings.review = true
      sourceSettings.review_model = reviewModel
      sourceSettings.review_provider = reviewModel === 'auto_local'
        ? undefined
        : selectedReviewModel?.provider
      sourceSettings.max_repair_attempts = getEffectiveProjectReferenceRepairAttempts(
        sourceSettings.mode, true, sourceSettings.max_repair_attempts,
      )
    }
    const key = lockProjectAssetVariantOperation(
      pendingSheetActionLocks.current, project, asset.id, variant.id,
    )
    if (!key) return
    const requestId = createProjectReferenceRequestId()
    const epoch = projectEpoch.current
    const submittedProject = project
    setPendingSheetActions(current => ({
      ...current,
      [key]: { project, assetId: asset.id, variantId: variant.id, jobId: null },
    }))
    setQueuedMessage('')
    try {
      const response = await generateProjectAssetReferences(project, {
        request_id: requestId,
        asset_id: asset.id,
        parent_variant_id: variant.id,
        edit_instruction: instruction?.trim() || undefined,
        schema_version: sourceSettings.schema_version,
        asset_type: sourceSettings.asset_type,
        intent: sourceSettings.intent,
        depth: sourceSettings.depth,
        sheet_count: sourceSettings.depth === 'custom' ? sourceSettings.sheet_count : undefined,
        preset: sourceSettings.preset,
        anchor_basis: sourceSettings.anchor_basis,
        type_fields: sourceSettings.type_fields,
        detail_callouts: sourceSettings.detail_callouts,
        style: sourceSettings.style || undefined,
        managed_layout_assist: sourceSettings.managed_layout_assist,
        planning_model: sourceSettings.planning_model,
        planning_provider: sourceSettings.planning_provider,
        review_model: sourceSettings.review_model,
        review_provider: sourceSettings.review_provider,
        content_capability: sourceSettings.content_capability,
        initial_blur: sourceSettings.initial_blur,
        intelligence_policy: sourceSettings.intelligence_policy,
        additional_loras: sourceSettings.additional_loras,
        mode: sourceSettings.mode,
        candidate_count: 1,
        columns,
        palette_swatches: paletteSwatches,
        review: sourceSettings.review,
        max_repair_attempts: sourceSettings.max_repair_attempts,
        model_type: sourceSettings.model_type,
        editor_model_type: (sourceSettings.schema_version === 2
          ? sourceSettings.mode !== 'draft'
          : sourceSettings.mode === 'hybrid') && sourceSettings.editor_model_type
          ? sourceSettings.editor_model_type
          : undefined,
        private_output: sourceSettings.private_output,
        explicit_output: sourceSettings.explicit_output,
        character_profile: sourceSettings.character_profile,
        explicit_convenience: sourceSettings.explicit_convenience,
      })
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestQueueView()
      setPendingSheetActions(current => ({
        ...current,
        [key]: { project, assetId: asset.id, variantId: variant.id, jobId: response.job_id },
      }))
      setQueuedMessage(`${instruction?.trim() ? 'Edit' : 'Retry'} accepted; confirming it with the Queue.`)
      setEditVariantId(null)
      setEditInstruction('')
      requestRefresh()
      const authoredSeal = response.plan?.authored_settings?.seal
      const replaySnapshot = cloneReferenceAuthoredSnapshot(
        sourceSettings.type_fields,
        sourceSettings.detail_callouts,
        sourceSettings.style,
        sourceSettings.character_profile,
        sourceSettings.explicit_convenience,
      )
      if (authoredSeal && isProjectReferenceStyleReplayReady(
        response.plan?.authored_settings, sourceSettings.style,
      ) && isProjectReferenceCharacterReplayReady(
        response.plan,
        {
          character_profile: replaySnapshot.characterProfile,
          explicit_convenience: replaySnapshot.explicitConvenience,
        },
      )) {
        authoredSettingsSnapshots.current.set(
          authoredSeal,
          replaySnapshot,
        )
      }
      if (response.plan?.plan_seal) {
        loraParameterSnapshots.current.set(
          response.plan.plan_seal,
          cloneAdditionalLoras(sourceSettings.additional_loras ?? []),
        )
      }
      const jobConfirmed = await confirmAcceptedProjectReferenceJob(
        response.job_id,
        jobId => confirmReconnectedJob(
          jobId,
          reconnectJobs,
          () => useStore.getState().jobs,
        ),
      )
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setQueuedMessage(jobConfirmed
        ? `${instruction?.trim() ? 'Edit' : 'Retry'} queued. Available source mode, model, privacy, and repair policy were preserved; ${retryReview.use_current_reviewer ? 'the current compatible reviewer replaced an unavailable recorded reviewer' : 'current layout and review intent were used'}. The original and any kept source stay unchanged.`
        : `${instruction?.trim() ? 'Edit' : 'Retry'} accepted; Queue confirmation is still catching up. The original and any kept source stay unchanged.`)
      // Queue navigation and accepted-state updates happen before confirmation;
      // the Reference peer remains mounted with its existing asset identity.
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      pendingSheetActionLocks.current.delete(key)
      setPendingSheetActions(current => {
        const next = { ...current }
        delete next[key]
        return next
      })
      setActionError(projectReferenceSafeErrorMessage(
        reason,
        'Could not queue the reference-sheet variant.',
      ))
    }
  }

  const updateStatus = async (assetId: string, variantId: string, status: 'kept' | 'rejected') => {
    const epoch = projectEpoch.current
    const submittedProject = project
    try {
      await setProjectAssetVariantStatus(project, assetId, variantId, status)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setActionError(projectReferenceSafeErrorMessage(reason, 'Could not update the candidate.'))
    }
  }

  const deleteVariant = async (assetId: string, variantId: string, label: string) => {
    if (!window.confirm(`Permanently delete reference candidate “${label}” and its copied media?`)) return
    const epoch = projectEpoch.current
    const submittedProject = project
    try {
      await deleteProjectAssetVariant(project, assetId, variantId)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setActionError(projectReferenceSafeErrorMessage(reason, 'Could not delete the candidate.'))
    }
  }

  const importVariant = async (assetId: string, file: File) => {
    const epoch = projectEpoch.current
    const submittedProject = project
    setImporting({ assetId, message: `Uploading ${file.name}…` })
    try {
      const uploaded = await uploadImage(file)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setImporting({ assetId, message: 'Adding to project references…' })
      await addProjectAssetVariant(project, assetId, {
        source_workspace: project,
        variant_type: 'reference',
        label: file.name,
        status: 'kept',
        provenance: 'imported',
        outputs: [{ path: uploaded.path, label: file.name }],
        metadata: {
          original_filename: file.name,
          media_type: file.type,
          size_bytes: file.size,
        },
      })
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setActionError(projectReferenceSafeErrorMessage(reason, 'Could not import reference media.'))
    } finally {
      if (isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) setImporting(null)
    }
  }

  const applyReference = async (asset: ProjectAsset, variant: ProjectAsset['variants'][number]) => {
    const epoch = projectEpoch.current
    const submittedProject = project
    let destination: 'director' | 'studio' = referenceReturnMode
    try {
      const directorReferenceKind = getDirectorProjectReferenceKind(asset.asset_type)
      const outputs = getProjectAssetApplyOutputs(variant)
      if (outputs.length === 0) throw new Error('This reference candidate has no usable media')
      if (referenceReturnMode === 'director'
        && !directorReferenceKind
        && !outputs[0].media_type?.startsWith('video/')) {
        throw new Error('Director currently accepts only Character and Location references. Apply this candidate from Generate until expanded Director reference types are available.')
      }
      const files: Array<{ output: ProjectAssetOutput; file: File }> = []
      for (const output of outputs) {
        const url = getProjectAssetMediaUrl(submittedProject, output.relative_path)
        const response = await fetch(url)
        if (!response.ok) throw projectAssetRequestError(response.status, 'Could not load reference media')
        const blob = await response.blob()
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        files.push({ output, file: new File([blob], output.filename, { type: blob.type || output.media_type }) })
      }
      const [{ output, file }] = files
      if (output.media_type?.startsWith('video/')) {
        // A Director-approved Blender animation is a full-rate Studio control
        // reference with a paired semantic prompt, even when the library was
        // opened from Director mode.
        const setStudioParam = setParam as (key: string, value: unknown) => void
        const metadata = variant.metadata || {}
        const semanticMapping = metadata.semantic_mapping
        const semanticPrompt = typeof metadata.conditioned_prompt === 'string'
          ? metadata.conditioned_prompt
          : semanticMapping && typeof semanticMapping === 'object' && 'conditioned_prompt' in semanticMapping
            ? String((semanticMapping as { conditioned_prompt?: unknown }).conditioned_prompt || '')
            : ''
        const uploaded = await uploadImage(file)
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        destination = 'studio'
        setGenerationMode('video')
        const recommendedModel = String(metadata.recommended_model_type || 'ltx2_22B_1_1')
        const controlMode = String(metadata.recommended_video_prompt_type || 'TVG')
        selectModel(recommendedModel)
        setStudioParam('video_guide', uploaded.path)
        setStudioParam('video_prompt_type', controlMode)
        setStudioParam('force_fps', 'control')
        setStudioParam('ic_lora_attention_strength', Number(metadata.attention_strength ?? 1))
        setStudioParam('ic_lora_reference_downscale', Number(metadata.reference_downscale_factor ?? 2))
        if (semanticPrompt) setStudioParam('prompt', semanticPrompt)
        setGuideVideoFps(uploaded.fps && uploaded.fps > 0 ? uploaded.fps : null)
        setGuideVideoFrameCount(uploaded.frame_count && uploaded.frame_count > 0 ? uploaded.frame_count : null)
        if (uploaded.frame_count && uploaded.frame_count > 0) {
          setStudioParam('video_length', uploaded.frame_count)
        }
      } else if (referenceReturnMode === 'director') {
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        for (const item of files) {
          if (directorReferenceKind === 'character') addCharacterRef(item.file)
          else addLocationRef(item.file)
        }
      } else {
        // Project asset cards are semantic identity/setting/item references in
        // Studio video mode. Route them to the separate non-distilled Ref2VA
        // checkpoint automatically; FL2VA treats images as timeline anchors.
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        if (generationMode === 'video') {
          selectModel('minimax_h3_ref2va')
        }
        for (const item of files) addImageRef(item.file)
      }
      setSidebarMode(destination)
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setActionError(projectReferenceSafeErrorMessage(reason, 'Could not use this reference.'))
    }
  }

  return (
    <section
      aria-labelledby="project-reference-title"
      aria-hidden={!active}
      hidden={!active}
      inert={!active ? true : undefined}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain [-webkit-overflow-scrolling:touch]"
    >
      <div className="sticky top-0 z-20 flex items-center gap-2 border-b border-border bg-bg-secondary px-4 py-3">
        <Library size={14} className="shrink-0 text-accent-blue" aria-hidden="true" />
        <div className="min-w-0">
          <h2 id="project-reference-title" className="text-sm font-semibold text-text-primary">Reference Studio</h2>
          <p className="truncate text-[10px] text-text-muted">{project || 'Choose a project'} · create and manage reusable visual references</p>
        </div>
      </div>

      {projectExplicitlyLocked ? (
        <div className="m-4 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4 text-xs text-amber-100">
          Unlock this project to create or manage references.
        </div>
      ) : (
        <>

            {actionError && (
              <div role="alert" className="mx-4 mt-3 flex shrink-0 items-start justify-between gap-3 rounded-lg border border-red-500/60 bg-red-500/15 px-3 py-2 text-[11px] leading-relaxed text-red-200 shadow-lg">
                <p>{actionError}</p>
                <button type="button" aria-label="Dismiss project reference error" onClick={() => setActionError('')} className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded p-0.5 text-red-200 hover:bg-red-500/20 hover:text-white md:min-h-0 md:min-w-0"><X size={14} /></button>
              </div>
            )}

            <div className="grid min-h-0 grid-cols-1">
              <div className="overflow-visible border-b border-border p-4">
                <h3 className="mb-3 text-xs font-medium text-text-primary">Create reference candidates</h3>
                <div className="grid grid-cols-2 gap-1.5">
                  {ASSET_TYPES.map(option => {
                    const Icon = option.icon
                    return (
                      <button type="button" key={option.value} aria-pressed={assetType === option.value} onClick={() => changeAssetType(option.value)} className={`flex min-h-11 min-w-11 items-center gap-1.5 rounded-md border px-2 py-1.5 text-[10px] md:min-h-0 md:min-w-0 ${assetType === option.value ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary'}`}>
                        <Icon size={11} /> {option.label}
                      </button>
                    )
                  })}
                </div>
                <label htmlFor="project-reference-name" className="mt-3 block text-[10px] text-text-secondary">Name
                  <input id="project-reference-name" aria-label="Reference name" value={name} onChange={event => setName(event.target.value)} placeholder="Name" className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary md:min-h-0" />
                </label>
                <label htmlFor="project-reference-description" className="mt-2 block text-[10px] text-text-secondary">Description
                  <textarea id="project-reference-description" aria-label="Reference description" value={description} onChange={event => setDescription(event.target.value)} placeholder="Detailed description / card (optional)" rows={5} className="mt-1 min-h-11 w-full resize-y rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary md:min-h-0" />
                </label>
                <fieldset className="mt-3 rounded-md border border-border bg-bg-tertiary/40 p-2">
                  <legend className="px-1 text-[10px] font-medium text-text-secondary">Visual style</legend>
                  <select aria-label="Reference visual style" value={visualStyle} onChange={event => setVisualStyle(event.target.value)} className="min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-[10px] text-text-primary md:min-h-0">
                    <option value="">Realistic (default)</option>
                    <option value="cinematic">Cinematic</option>
                    <option value="stylized 3D animation">Stylized 3D animation</option>
                    <option value="illustration">Illustration</option>
                    <option value="anime">Anime</option>
                    <option value="custom">Custom…</option>
                  </select>
                  {visualStyle === 'custom' && <input aria-label="Custom reference visual style" value={customVisualStyle} onChange={event => setCustomVisualStyle(event.target.value)} placeholder="e.g. 1970s editorial watercolor" className="mt-1.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-[10px] text-text-primary md:min-h-0" />}
                  <p className="mt-1 text-[8px] leading-relaxed text-text-muted">Realistic is used by default. Choose a preset, or select Custom to describe your own style.</p>
                </fieldset>
                <fieldset aria-label="Reference creation method" className="mt-3 rounded-md border border-accent-blue/30 bg-accent-blue/5 p-2">
                  <legend className="px-1 text-[10px] font-medium text-text-primary">Creation method</legend>
                  <p className="text-[8px] leading-relaxed text-text-muted">Choose what you want to create. Your reference category above stays the same.</p>
                  <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                    <button
                      type="button"
                      aria-pressed={candidateKind === 'image_pack'}
                      aria-controls="project-reference-image-pack-method"
                      onClick={() => changeCreationMethod('image_pack')}
                      className={`min-h-11 min-w-11 rounded border p-2 text-left md:min-h-0 md:min-w-0 ${candidateKind === 'image_pack' ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-bg-secondary'}`}
                    >
                      <span className="block text-[10px] font-medium text-accent-blue">Image Reference Pack</span>
                      <span className="mt-0.5 block text-[8px] text-text-muted">Create image references for the selected category using the options below.</span>
                    </button>
                    <button
                      type="button"
                      aria-pressed={candidateKind === 'blender_motion'}
                      aria-controls="project-reference-blender-motion-method"
                      onClick={() => changeCreationMethod('blender_motion')}
                      className={`min-h-11 min-w-11 rounded border p-2 text-left md:min-h-0 md:min-w-0 ${candidateKind === 'blender_motion' ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-bg-secondary'}`}
                    >
                      <span className="block text-[10px] font-medium text-accent-blue">Blender Motion Video</span>
                      <span className="mt-0.5 block text-[8px] text-text-muted">Create a structured motion and camera reference.</span>
                    </button>
                  </div>
                </fieldset>
                <section
                  id="project-reference-blender-motion-method"
                  aria-label="Blender Motion Video creation panel"
                  aria-hidden={creationPanelStates.blender_motion.hidden}
                  hidden={creationPanelStates.blender_motion.hidden}
                  inert={creationPanelStates.blender_motion.inert}
                  className="mt-3 rounded-md border border-accent-blue/30 bg-accent-blue/5 p-2"
                >
                    <p className="text-[8px] text-text-muted">Create, preview, keep, and use a motion and camera reference in Generate. Blender also remains available under Tools.</p>
                    <BlenderSceneTool
                      compact
                      referenceName={name}
                      referenceDescription={description}
                      privateOutput={referenceExplicitOutput || privateOutput}
                    />
                </section>
                <div
                  id="project-reference-image-pack-method"
                  aria-label="Image Reference Pack creation panel"
                  aria-hidden={creationPanelStates.image_pack.hidden}
                  hidden={creationPanelStates.image_pack.hidden}
                  inert={creationPanelStates.image_pack.inert}
                >
                <fieldset className="mt-3">
                  <legend className="mb-1.5 text-[10px] font-medium text-text-secondary">Intent</legend>
                  <div className="grid grid-cols-3 gap-1">
                    {INTENT_OPTIONS.map(option => (
                      <button key={option.value} type="button" aria-pressed={intent === option.value} title={option.description} onClick={() => changeIntent(option.value)} className={`min-h-11 min-w-11 rounded border px-1.5 py-1.5 text-[9px] md:min-h-0 md:min-w-0 ${intent === option.value ? 'border-accent-blue bg-accent-blue/10 text-accent-blue' : 'border-border text-text-secondary'}`}>
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1 text-[9px] text-text-muted">{INTENT_OPTIONS.find(option => option.value === intent)?.description}</p>
                </fieldset>
                <fieldset className="mt-3">
                  <legend className="mb-1.5 text-[10px] font-medium text-text-secondary">Reference depth</legend>
                  <div className="grid grid-cols-2 gap-1">
                    {DEPTH_OPTIONS.map(option => (
                      <button key={option.value} type="button" aria-pressed={depth === option.value} onClick={() => changeDepth(option.value)} className={`min-h-11 min-w-11 rounded border px-2 py-1.5 text-left md:min-h-0 md:min-w-0 ${depth === option.value ? 'border-accent-blue bg-accent-blue/10' : 'border-border'}`}>
                        <span className="block text-[9px] font-medium text-text-primary">{option.label}</span>
                        <span className="block text-[8px] text-text-muted">{option.description}</span>
                      </button>
                    ))}
                  </div>
                  {depth === 'custom' && (
                    <label htmlFor="project-reference-sheet-count" className="mt-2 flex items-center justify-between text-[10px] text-text-secondary">Sheets per pack
                      <input id="project-reference-sheet-count" aria-label="Custom sheets per reference pack" type="number" min={1} max={5} value={customSheetCount} onChange={event => changeCustomSheetCount(Number(event.target.value))} className="min-h-11 w-16 rounded border border-border bg-bg-tertiary px-2 py-1 text-right md:min-h-0" />
                    </label>
                  )}
                </fieldset>
                <fieldset className="mt-3">
                  <legend className="mb-1.5 text-[10px] font-medium text-text-secondary">Preset</legend>
                  <div className="flex flex-wrap gap-1">
                    {visiblePresets.map(option => (
                      <button key={option.value} type="button" aria-pressed={preset === option.value} onClick={() => changePreset(option.value)} className={`min-h-11 min-w-11 rounded-full border px-3 py-1 text-[9px] md:min-h-0 md:min-w-0 md:px-2 ${preset === option.value ? 'border-accent-blue bg-accent-blue/10 text-accent-blue' : 'border-border text-text-secondary'}`}>
                        {option.label}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset className="mt-3">
                  <legend className="mb-1.5 text-[10px] font-medium text-text-secondary">Sheet construction mode</legend>
                  <div className="space-y-1.5">
                    {SHEET_MODES.map(option => (
                      <label key={option.value} className={`block min-h-11 cursor-pointer rounded-md border p-2 md:min-h-0 ${sheetMode === option.value ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-bg-tertiary/40'}`}>
                        <span className="flex items-center gap-1.5 text-[10px] font-medium text-text-primary">
                          <input type="radio" name="reference-sheet-mode" value={option.value} checked={sheetMode === option.value} onChange={() => setSheetMode(option.value)} className="h-5 w-5 shrink-0 md:h-auto md:w-auto" />
                          {option.label}
                        </span>
                        <span className="mt-0.5 block pl-5 text-[9px] leading-relaxed text-text-muted">{option.description}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                {assetType === 'character' && (
                  <fieldset className="mt-3 rounded-md border border-border bg-bg-tertiary/40 p-2">
                    <legend className="px-1 text-[10px] font-medium text-text-secondary">Character profile · optional</legend>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label htmlFor="project-reference-character-gender" className="text-[9px] text-text-muted">Gender
                        <select
                          id="project-reference-character-gender"
                          value={characterGender}
                          onChange={event => setCharacterGender(event.target.value as ProjectReferenceCharacterGender)}
                          className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-secondary md:min-h-0"
                        >
                          <option value="unspecified">Unspecified</option>
                          <option value="woman">Woman</option>
                          <option value="man">Man</option>
                          <option value="non_binary">Non-binary</option>
                        </select>
                      </label>
                      <label htmlFor="project-reference-character-age" className="text-[9px] text-text-muted">Age
                        <input
                          id="project-reference-character-age"
                          aria-describedby="project-reference-character-profile-help"
                          type="number"
                          inputMode="numeric"
                          min={0}
                          max={999}
                          step={1}
                          value={characterAge}
                          placeholder="Optional"
                          onChange={event => setCharacterAge(event.target.value)}
                          className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-secondary md:min-h-0"
                        />
                      </label>
                    </div>
                    <p id="project-reference-character-profile-help" className="mt-1.5 text-[8px] leading-relaxed text-text-muted">These optional details help Maestro create and check the Character. Age is separate from gender and from any age in the description; if you use both, keep them consistent. Maestro does not guess age from text, appearance, or gender.</p>
                    <fieldset className="mt-2 rounded border border-border/70 p-1.5">
                      <legend className="px-1 text-[9px] text-text-secondary">Anatomy details</legend>
                      <div className="grid grid-cols-1 gap-1 sm:grid-cols-3">
                        {([
                          ['breasts', 'Breasts · front + profile'],
                          ['vulva', 'Vulva'],
                          ['penis', 'Penis'],
                        ] as Array<[ProjectReferenceCharacterAnatomy, string]>).map(([value, label]) => (
                          <label key={value} className="flex min-h-11 items-center gap-2 rounded border border-border/60 px-2 py-1 text-[8px] text-text-secondary md:min-h-0 md:items-start md:gap-1.5 md:px-1.5">
                            <input
                              type="checkbox"
                              checked={characterExplicitAnatomy.includes(value)}
                              className="h-5 w-5 shrink-0 md:h-auto md:w-auto"
                              onChange={event => setCharacterExplicitAnatomy(current => event.target.checked
                                ? [...current.filter(item => item !== value), value]
                                : current.filter(item => item !== value))}
                            />
                            {label}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                    <label className="mt-2 flex min-h-11 items-start gap-2 py-1 text-[9px] text-text-secondary md:min-h-0 md:py-0">
                      <input type="checkbox" checked={explicitConvenience} onChange={event => applyExplicitConvenience(event.target.checked)} className="h-5 w-5 shrink-0 md:h-auto md:w-auto" />
                      <span><span className="font-medium">Create anatomy detail views</span><span className="mt-0.5 block text-[8px] text-text-muted">Uses the Anatomy / Nude main image and creates detail views for the anatomy selected above. This also turns on Allow explicit output. Turning this option off does not turn that separate permission off. Breasts creates front and profile views; vulva and penis remain separate choices. Draft saves the profile but does not create detail sheets.</span></span>
                    </label>
                    <p className="mt-1 text-[8px] leading-relaxed text-text-muted">Anatomy detail views are for adult characters. An age below 18 blocks queueing; leaving age blank does not confirm adulthood. Gender never chooses anatomy or sets age.</p>
                    {explicitConvenience && <p role="status" className="mt-1 text-[8px] text-text-muted">{managedCharacterCalloutCount} anatomy {managedCharacterCalloutCount === 1 ? 'detail view' : 'detail views'} will be requested for this {sheetMode === 'draft' ? 'profile; Draft creates no detail sheets' : 'pack'}.</p>}
                  </fieldset>
                )}
                <fieldset className="mt-3 rounded-md border border-border bg-bg-tertiary/40 p-2">
                  <legend className="px-1 text-[10px] font-medium text-text-secondary">Output handling</legend>
                  <label className="flex min-h-11 items-center gap-2 text-[9px] text-text-secondary md:min-h-0">
                    <input type="checkbox" checked={referenceExplicitOutput} className="h-5 w-5 shrink-0 md:h-auto md:w-auto" onChange={event => {
                      const enabled = event.target.checked
                      setReferenceExplicitOutput(enabled)
                      if (!enabled) setExplicitConvenience(false)
                    }} />
                    Allow explicit output
                  </label>
                  <p className="mt-1 text-[8px] text-text-muted">This permission is separate from the Character profile. Anatomy detail views require it, so turning it off also turns that option off. You can choose the content mode, initial blur, and automatic model behavior below.</p>
                  <p className="mt-1 text-[8px] text-text-muted">The visual quality check looks for consistent identity, anatomy, layout, and style. It does not classify or censor content or decide whether a request is allowed.</p>
                  <div className="mt-2 grid grid-cols-1 gap-1.5">
                    <label htmlFor="project-reference-content-capability" className="text-[9px] text-text-muted">Content mode
                      <select id="project-reference-content-capability" value={contentCapability} onChange={event => setContentCapability(event.target.value as 'standard' | 'unrestricted_local')} className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-secondary md:min-h-0">
                        {contentCapabilities.includes('standard') && <option value="standard">Standard</option>}
                        {contentCapabilities.includes('unrestricted_local') && <option value="unrestricted_local">Unrestricted · local only</option>}
                      </select>
                    </label>
                    <label htmlFor="project-reference-initial-blur" className="text-[9px] text-text-muted">Initial output
                      <select id="project-reference-initial-blur" value={initialBlur ? 'blur' : 'reveal'} onChange={event => setInitialBlur(event.target.value === 'blur')} className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-secondary md:min-h-0">
                        <option value="blur">Blur</option>
                        <option value="reveal">Reveal</option>
                      </select>
                    </label>
                    <label htmlFor="project-reference-intelligence-policy" className="text-[9px] text-text-muted">Automatic model choice
                      <select id="project-reference-intelligence-policy" value={intelligencePolicy} onChange={event => { setIntelligenceCustomized(true); setIntelligencePolicy(event.target.value as 'standard_auto' | 'uncensored_auto') }} className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-secondary md:min-h-0">
                        {intelligencePolicies.includes('standard_auto') && <option value="standard_auto">Standard automatic</option>}
                        {intelligencePolicies.includes('uncensored_auto') && <option value="uncensored_auto">Unrestricted local automatic</option>}
                      </select>
                    </label>
                  </div>
                </fieldset>
                <div className="mt-3 space-y-2" aria-label="Editable reference sections">
                  {sectionDefinitions.map(definition => {
                    const section = sections.find(candidate => candidate.id === definition.id)
                    if (!section) return null
                    return (
                      <fieldset key={definition.id} className="rounded-md border border-border bg-bg-tertiary/40 p-2">
                        <legend className="px-1 text-[10px] font-medium text-text-secondary">
                          {definition.label}
                          {section.pinned && <span className="ml-1 text-accent-blue">· Customized</span>}
                        </legend>
                        {definition.id === 'details' && (
                          <p className="mb-1.5 text-[8px] leading-relaxed text-text-muted">
                            Add up to 8 custom details. Create separate entries for different views when needed—for example, breasts (front) and breasts (profile). Source sheet chooses which image to use. Action chooses whether Maestro decides, crops, enhances, or rebuilds the detail. Your custom labels stay private to the project owner.
                          </p>
                        )}
                        <div className="flex flex-wrap gap-1">
                          {definition.options.map(option => {
                            const item = sectionOptionItem(authoritativeTypeCapabilities, definition, option)
                            const selected = item ? section.values.some(value => value.id === item.id) : false
                            return (
                              <button key={option} type="button" aria-pressed={selected} onClick={() => toggleSectionValue(definition.id, option)} className={`min-h-11 min-w-11 rounded-full border px-3 py-1 text-[9px] md:min-h-0 md:min-w-0 md:px-2 ${selected ? 'border-accent-blue bg-accent-blue/10 text-accent-blue' : 'border-border text-text-muted'}`}>
                                {option}
                              </button>
                            )
                          })}
                        </div>
                        {definition.id !== 'details' && section.values.some(item => item.custom) && (
                          <div className="mt-1.5 space-y-1" aria-label={`Custom ${definition.label.toLowerCase()} values`}>
                            {section.values.filter(item => item.custom).map(item => (
                              <div key={item.id} className="flex flex-col gap-1 md:flex-row md:items-center">
                                <input aria-label={`Edit custom ${definition.label.toLowerCase()}: ${item.label || item.id}`} maxLength={500} value={item.label} onChange={event => updateCustomSectionValue(definition.id, item.id, event.target.value)} className="min-h-11 min-w-0 flex-1 rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-primary md:min-h-0" />
                                <button type="button" aria-label={`Remove custom ${definition.label.toLowerCase()} ${item.label}`} onClick={() => removeSectionValue(definition.id, item.id)} className="min-h-11 min-w-11 rounded border border-border px-3 py-1 text-[8px] text-text-muted md:min-h-0 md:min-w-0 md:px-2">Remove</button>
                              </div>
                            ))}
                          </div>
                        )}
                        {definition.id === 'details' && selectedDetailItems.length > 0 && (
                          <div className="mt-1.5 space-y-1">
                            {selectedDetailItems.map(item => {
                              const callout = detailCallouts.find(candidate => candidate.custom_id === item.id)
                              const setting = detailSettings[item.id] ?? { operation: 'auto' as const, sourceRole: validDetailSourceRoles[0] ?? '' }
                              return (
                              <div key={item.id} className="rounded border border-border/60 px-1.5 py-1 text-[8px] text-text-muted">
                                <div className="flex flex-col gap-1 md:flex-row md:items-center">
                                  {item.custom
                                    ? <input aria-label={`Edit custom detail: ${item.label || item.id}`} maxLength={500} value={item.label} onChange={event => updateCustomSectionValue('details', item.id, event.target.value)} className="min-h-11 min-w-0 flex-1 rounded border border-border bg-bg-primary px-1.5 py-1 text-[8px] text-text-primary md:min-h-0" />
                                    : <span className="min-w-0 flex-1">{item.label}</span>}
                                  {item.custom && <button type="button" aria-label={`Remove custom detail ${item.label}`} onClick={() => removeSectionValue('details', item.id)} className="min-h-11 min-w-11 rounded border border-border px-3 py-1 text-[8px] text-text-muted md:min-h-0 md:min-w-0 md:px-1.5">Remove</button>}
                                </div>
                                <div className="mt-1 grid grid-cols-1 gap-1 md:grid-cols-2">
                                  <label className="text-[8px] text-text-muted">Source sheet
                                    <select aria-label={`${item.label} detail source`} disabled={sheetMode === 'draft'} value={callout?.source_role ?? setting.sourceRole} onChange={event => setDetailSettings(current => ({ ...current, [item.id]: { ...setting, sourceRole: event.target.value } }))} className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary disabled:opacity-50 md:min-h-0">
                                      {validDetailSourceRoles.map(role => <option key={role} value={role}>{friendlyRole(role)}</option>)}
                                    </select>
                                  </label>
                                  <label className="text-[8px] text-text-muted">Action
                                    <select aria-label={`${item.label} detail operation`} disabled={sheetMode === 'draft'} value={callout?.operation ?? setting.operation} onChange={event => setDetailSettings(current => ({ ...current, [item.id]: { ...setting, operation: event.target.value as ProjectReferenceDetailOperation } }))} className="mt-0.5 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary disabled:opacity-50 md:min-h-0">
                                      {(authoritativePreset?.detail_operations ?? referenceCapabilities?.detail_operations ?? []).map(operation => <option key={operation} value={operation} disabled={operation === 'reconstruct' && intent === 'exact_spec'}>{detailOperationLabel(operation)}</option>)}
                                    </select>
                                  </label>
                                </div>
                              </div>
                              )
                            })}
                            {intent === 'exact_spec' && <p className="text-[8px] text-amber-300">Follow my description will not invent a missing identity detail. Use Auto, Crop, or Enhance.</p>}
                            {sheetMode === 'draft' && <p className="text-[8px] text-amber-300">Draft does not create details that require the editor model. These choices will be used when you select Production or Hybrid.</p>}
                          </div>
                        )}
                        <div className="mt-1.5 flex flex-col gap-1 md:flex-row">
                          <input aria-label={`Add custom ${definition.label.toLowerCase()} callout`} disabled={definition.id === 'details' && (authoritativeTypeCapabilities?.supports_custom_details !== true || section.values.length >= 8)} maxLength={500} value={customSectionInputs[definition.id] ?? ''} onChange={event => setCustomSectionInputs(current => ({ ...current, [definition.id]: event.target.value }))} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addCustomSectionValue(definition.id) } }} placeholder="Add callout" className="min-h-11 min-w-0 flex-1 rounded border border-border bg-bg-primary px-2 py-1 text-[9px] text-text-primary disabled:opacity-50 md:min-h-0" />
                          <button type="button" disabled={definition.id === 'details' && (authoritativeTypeCapabilities?.supports_custom_details !== true || section.values.length >= 8)} onClick={() => addCustomSectionValue(definition.id)} className="min-h-11 min-w-11 rounded border border-border px-3 py-1 text-[9px] text-text-secondary disabled:opacity-50 md:min-h-0 md:min-w-0 md:px-2">Add</button>
                          {section.pinned && <button type="button" onClick={() => resetSection(definition)} className="min-h-11 min-w-11 rounded border border-border px-3 py-1 text-[9px] text-text-muted md:min-h-0 md:min-w-0 md:px-2">Reset</button>}
                        </div>
                      </fieldset>
                    )
                  })}
                  {authoringStatus && <p role="status" aria-live="polite" className="text-[9px] text-text-muted">{authoringStatus}</p>}
                </div>
                <section aria-label="Reference pack plan preview" className="mt-3 rounded-md border border-accent-blue/30 bg-accent-blue/5 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-[10px] font-medium text-text-primary">Pack preview</h4>
                    <span className="text-[9px] text-accent-blue">{sheetCount} base {sheetCount === 1 ? 'sheet' : 'sheets'}{detailCallouts.length > 0 ? ` + ${detailCallouts.length} detail ${detailCallouts.length === 1 ? 'output' : 'outputs'}` : ''} × {candidateCount} {candidateCount === 1 ? 'candidate' : 'candidates'}</span>
                  </div>
                  <p className="mt-1 text-[9px] text-text-secondary">Main image: {anchorBasis === 'anatomy' ? 'Anatomy / Nude' : anchorBasis === 'primary_outfit' ? 'Primary outfit' : 'Clearest available view'}</p>
                  {anchorBasis === 'anatomy' && (
                    <label className="mt-1 flex min-h-11 items-center gap-2 text-[9px] text-text-secondary md:min-h-0">
                      <input type="checkbox" checked={anatomyPrivate} onChange={event => setAnatomyPrivate(event.target.checked)} className="h-5 w-5 shrink-0 md:h-auto md:w-auto" />
                      Keep the anatomy main image private and blurred
                    </label>
                  )}
                  <ol className="mt-1 space-y-0.5 text-[9px] text-text-muted">
                    {deliverables.map(deliverable => <li key={deliverable}>{deliverable}</li>)}
                  </ol>
                  {deliverables.length !== sheetCount && <p role="status" className="mt-1 text-[9px] text-red-300">The sheet plan is unavailable, so generation is disabled. Refresh Reference Studio or choose another preset.</p>}
                  {detailCallouts.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {detailCallouts.map(callout => (
                        <span key={callout.custom_id} className="rounded-full border border-border px-1.5 py-0.5 text-[8px] text-text-muted">
                          Detail output: {callout.label} · {friendlyRole(callout.source_role)} · {detailOperationLabel(callout.operation)}
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="mt-1 text-[8px] text-text-muted">Changing reference depth updates only sections you have not customized.</p>
                </section>
                <label htmlFor="project-reference-generation-model" className="mt-3 block text-[10px] text-text-secondary">Generation model
                  <select id="project-reference-generation-model" aria-label="Reference generation model" value={referenceModelType} onChange={event => { setReferenceModelCustomized(true); setReferenceModelType(event.target.value) }} disabled={referenceModels.length === 0} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px] text-text-primary disabled:opacity-50 md:min-h-0">
                    {!referenceModelType && <option value="">{sheetMode === 'draft' ? 'Fast Draft model unavailable' : 'FLUX.2-dev unavailable'}</option>}
                    {referenceModels.length === 0 && <option value="">No image-output models available</option>}
                    {referenceModels.map(model => <option key={model.model_type} value={model.model_type}>{model.name}{getProjectReferenceModelAvailabilityCopy(model)}</option>)}
                  </select>
                </label>
                {sheetMode !== 'draft' && (
                  <label htmlFor="project-reference-editor-model" className="mt-2 block text-[10px] text-text-secondary">Editor model
                    <select id="project-reference-editor-model" aria-label="Reference editor model" value={editorModelType} onChange={event => { setEditorModelCustomized(true); setEditorModelType(event.target.value) }} disabled={editorModels.length === 0} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px] text-text-primary disabled:opacity-50 md:min-h-0">
                      {!editorModelType && <option value="">Qwen-Image-Edit-2511 unavailable</option>}
                      {editorModels.length === 0 && <option value="">No reference-image editors available</option>}
                      {editorModels.map(model => <option key={model.model_type} value={model.model_type}>{model.name}{getProjectReferenceModelAvailabilityCopy(model)}</option>)}
                    </select>
                  </label>
                )}
                <section aria-label="Moody Krea 2 quick select" className="mt-2 rounded border border-accent-blue/25 bg-accent-blue/5 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-[10px] font-medium text-text-primary">Moody Krea 2 quick select</h4>
                    <span className="text-[8px] text-text-muted">manual setup required</span>
                  </div>
                  <div className="mt-1.5 grid grid-cols-1 gap-1 sm:grid-cols-2">
                    {MOODY_MODEL_TYPES.map(modelType => {
                      const model = catalogModels.find(candidate => candidate.model_type === modelType)
                      const enabled = enabledModels.has(modelType)
                      const verified = Boolean(model) && (
                        model?.downloadable !== false || model?.manual_checkpoint_verified === true
                      )
                      const selectable = enabled && Boolean(model) && verified
                      const status = !enabled
                        ? 'Disabled in Enabled Models'
                        : !model
                          ? 'Missing from current catalog'
                          : !verified
                            ? 'Install and verify locally'
                            : 'Select for generation'
                      return (
                        <button
                          key={modelType}
                          type="button"
                          disabled={!selectable}
                          aria-label={`${MOODY_MODEL_NAMES[modelType]} · ${status}`}
                          onClick={() => {
                            if (!selectable) return
                            setReferenceModelCustomized(true)
                            setReferenceModelType(modelType)
                          }}
                          className={`min-h-11 min-w-11 rounded border px-2 py-1.5 text-left text-[9px] md:min-h-0 md:min-w-0 ${referenceModelType === modelType ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary'} disabled:cursor-not-allowed disabled:opacity-50`}
                        >
                          <span className="block font-medium">{MOODY_MODEL_NAMES[modelType]}</span>
                          <span className="mt-0.5 block text-[8px] text-text-muted">{status}</span>
                        </button>
                      )
                    })}
                  </div>
                </section>
                {disabledMoodyModels.length > 0 && (
                  <div role="status" className="mt-2 rounded border border-border bg-bg-tertiary/60 px-2 py-1.5 text-[9px] text-text-secondary">
                    <p>These Moody Krea 2 models are available but not enabled on this computer: {disabledMoodyModels.map(modelType => MOODY_MODEL_NAMES[modelType]).join(', ')}.</p>
                    {machineControls ? (
                      <button type="button" onClick={() => openModelVisibility('image')} className="mt-1 min-h-11 min-w-11 rounded border border-accent-blue/40 px-3 py-0.5 text-accent-blue hover:bg-accent-blue/10 md:min-h-0 md:min-w-0 md:px-1.5">
                        Open Settings → System → Enabled Models
                      </button>
                    ) : (
                      <p className="mt-1 text-amber-200">Open Maestro at localhost on the host machine, then enable them under Settings → System → Enabled Models → Image → Krea 2.</p>
                    )}
                  </div>
                )}
                {enabledMissingMoodyModels.length > 0 && (
                  <div role="status" className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] text-amber-100">
                    <p>{enabledMissingMoodyModels.map(modelType => MOODY_MODEL_NAMES[modelType]).join(', ')} {enabledMissingMoodyModels.length === 1 ? 'is' : 'are'} enabled on this computer but not available in Reference Studio yet.</p>
                    <p className="mt-1">Refresh Reference Studio after the model list finishes loading. From a LAN session, accept terms, install, and verify models at localhost; those computer-wide controls are hidden remotely.</p>
                  </div>
                )}
                {modelLoadError && <p role="status" className="mt-2 text-[10px] text-red-300">{modelLoadError}</p>}
                {pendingRecipeTermRequirements.map(requirement => {
                  const notice = HOST_TERM_NOTICES[requirement.term]
                  return (
                    <div key={requirement.term} role="status" className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
                      <p>{requirement.notice}</p>
                      <div className="mt-1 flex flex-col gap-1 md:flex-row md:items-center md:gap-2">
                        <a href={requirement.license_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-accent-blue hover:underline md:min-h-0">{notice.linkLabel || 'Review terms'}</a>
                        <button type="button" disabled={hostTermsLoading || !hostTerms} onClick={() => { void acceptHostTerm(requirement.term) }} className="min-h-11 min-w-11 rounded border border-amber-400/40 px-3 py-0.5 font-medium text-amber-100 disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-1.5">Accept for this host</button>
                      </div>
                    </div>
                  )
                })}
                {pendingRecipeTermRequirements.length > 0 && hostTermsError && <p role="status" className="mt-1 text-[9px] text-red-300">{hostTermsError}</p>}
                {pendingManualModels.map(model => (
                  <div key={model.model_type} role="status" className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
                    <p>{model.name} requires its model file to be installed and verified on this computer. Maestro will not download it.</p>
                    {model.manual_installation ? (
                      <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-[8px]">
                        <dt className="text-amber-200">Filename</dt><dd className="break-all font-mono select-all">{model.manual_installation.filename}</dd>
                        <dt className="text-amber-200">Place in</dt><dd className="break-all font-mono select-all">{manualInstallationDestination(model.manual_installation)}</dd>
                        <dt className="text-amber-200">Size</dt><dd>{formatManualInstallationBytes(model.manual_installation.size_bytes)}</dd>
                        <dt className="text-amber-200">SHA-256</dt><dd className="break-all font-mono select-all">{model.manual_installation.sha256}</dd>
                        <dt className="text-amber-200">Verification</dt><dd>{model.manual_installation.local_verification_required ? 'Required at localhost' : 'Not required for this model'}</dd>
                      </dl>
                    ) : (
                      <p className="mt-1 text-red-300">The required download and verification details are unavailable. Maestro cannot safely verify or use this model.</p>
                    )}
                    {model.manual_installation && (
                      <div className="mt-1 flex flex-col gap-1 md:flex-row md:flex-wrap md:gap-2">
                        <a href={model.manual_installation.source_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-accent-blue hover:underline md:min-h-0">Open source page</a>
                        <a href={model.manual_installation.download_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center text-accent-blue hover:underline md:min-h-0">Open required download</a>
                      </div>
                    )}
                    {model.manual_checkpoint_verification_required && machineControls ? (
                      <button
                        type="button"
                        disabled={Boolean(verifyingManualModel) || pendingRecipeTermRequirements.length > 0}
                        onClick={() => {
                          const epoch = projectEpoch.current
                          setVerifyingManualModel(model.model_type)
                          setModelLoadError('')
                          void verifyManualCheckpoint(model.model_type)
                            .then(() => fetchModels())
                            .then(data => {
                              if (projectEpoch.current === epoch) setCatalogModels(data.models)
                            })
                            .catch(error => {
                              if (projectEpoch.current === epoch) {
                                setModelLoadError(error instanceof Error ? error.message : 'Model file verification failed.')
                              }
                            })
                            .finally(() => {
                              if (projectEpoch.current === epoch) setVerifyingManualModel('')
                            })
                        }}
                        className="mt-1 inline-flex min-h-11 min-w-11 items-center gap-1 rounded border border-amber-400/40 px-3 py-0.5 font-medium text-amber-100 disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-1.5"
                      >
                        {verifyingManualModel === model.model_type && <Loader2 size={9} className="animate-spin" />}
                        {verifyingManualModel === model.model_type ? 'Verifying model file…' : 'Verify model file'}
                      </button>
                    ) : !model.manual_checkpoint_verification_required ? (
                      <p className="mt-1 text-red-300">The required verification method is unavailable, so Maestro cannot safely use this model.</p>
                    ) : (
                      <p className="mt-1 text-amber-200">After placing the required file, open Maestro at localhost on the host computer and choose Verify model file. Verification is unavailable from LAN sessions.</p>
                    )}
                  </div>
                ))}
                <fieldset className="mt-3 rounded-md border border-border p-2">
                  <legend className="px-1 text-[10px] font-medium text-text-secondary">Additional LoRAs</legend>
                  <p className="text-[8px] text-text-muted">Only LoRAs that work with the selected creation and editor models are shown. Choose Best fit, Main image, or Variations.</p>
                  <div className="mt-1.5 grid grid-cols-1 gap-1 md:grid-cols-[1fr_auto]">
                    <select aria-label="Additional LoRA scope" value={pendingLoraScope} onChange={event => { setPendingLoraScope(event.target.value as ProjectReferenceLoraScope); setPendingLoraId('') }} className="min-h-11 rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[9px] text-text-secondary md:min-h-0">
                      {loraScopes.includes('auto') && <option value="auto">Best fit</option>}
                      {loraScopes.includes('generation') && <option value="generation">Main image</option>}
                      {loraScopes.includes('editing') && sheetMode !== 'draft' && <option value="editing">Variations</option>}
                    </select>
                    <input aria-label="Additional LoRA multiplier" type="number" min={-10} max={10} step="0.05" value={pendingLoraMultiplier} onChange={event => setPendingLoraMultiplier(Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : 1)} className="min-h-11 w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-right text-[9px] text-text-secondary md:min-h-0 md:w-20" />
                    <select aria-label="Additional compatible LoRA" value={pendingLoraId} onChange={event => setPendingLoraId(event.target.value)} disabled={availablePendingLoras.length === 0} className="min-h-11 rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[9px] text-text-secondary disabled:opacity-50 md:min-h-0">
                      <option value="">{availablePendingLoras.length > 0 ? 'Select a compatible LoRA' : 'No compatible LoRAs'}</option>
                      {availablePendingLoras.map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                    <button type="button" onClick={addAdditionalLora} disabled={!pendingLoraId || pendingLoraMultiplier < -10 || pendingLoraMultiplier > 10 || pendingLoraSchemaConflict || additionalLoras.length >= 64} className="min-h-11 min-w-11 rounded border border-border px-3 py-1 text-[9px] text-text-secondary disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-2">Add</button>
                  </div>
                  {pendingLoraSchemaConflict && <p role="status" className="mt-1 text-[8px] text-red-300">This LoRA uses different settings for the main image and variations. Choose one before adding it.</p>}
                  {additionalLoras.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {additionalLoras.map(lora => {
                        const parameterSchema = resolveLoraSchema(lora.id, lora.scope)
                        const parameterErrors = lora.parameter_schema_digest
                          ? loraParameterErrors.filter(error => error.startsWith(`${lora.id}:`))
                          : []
                        const parameterFieldsReady = Boolean(
                          lora.parameter_schema_digest
                          && parameterSchema
                          && parameterSchema.schema_digest === lora.parameter_schema_digest,
                        )
                        return (
                        <div key={lora.id} className="rounded border border-border/70 bg-bg-tertiary/40 p-1.5">
                          <div className="grid grid-cols-1 gap-1 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center">
                            <span className="truncate text-[8px] text-text-secondary" title={lora.id}>{lora.id}</span>
                            <input aria-label={`${lora.id} multiplier`} type="number" min={-10} max={10} step="0.05" value={lora.multiplier} onChange={event => updateAdditionalLora(lora.id, { multiplier: Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : 1 })} className="min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-right text-[8px] text-text-secondary md:min-h-0 md:w-16" />
                            <button type="button" aria-label={`Remove ${lora.id}`} onClick={() => setAdditionalLoras(current => current.filter(candidate => candidate.id !== lora.id))} className="flex min-h-11 min-w-11 items-center justify-center rounded p-0.5 text-text-muted hover:text-red-300 md:min-h-0 md:min-w-0"><X size={10} /></button>
                          </div>
                          <select aria-label={`${lora.id} scope`} value={lora.scope} onChange={event => updateAdditionalLora(lora.id, { scope: event.target.value as ProjectReferenceLoraScope })} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-[8px] text-text-secondary md:min-h-0">
                            {loraScopes.includes('auto') && <option value="auto">Best fit</option>}
                            {loraScopes.includes('generation') && <option value="generation" disabled={!generationLoras.includes(lora.id)}>Main image</option>}
                            {loraScopes.includes('editing') && <option value="editing" disabled={sheetMode === 'draft' || !editingLoras.includes(lora.id)}>Variations</option>}
                          </select>
                          <p className={`mt-0.5 text-[8px] ${lora.scope !== 'auto' && loraCompatibilityCopy(lora).startsWith('Incompatible') ? 'text-red-300' : 'text-text-muted'}`}>{loraCompatibilityCopy(lora)}</p>
                          {parameterFieldsReady && parameterSchema && (
                            <LoraParameterFields
                              loraId={lora.id}
                              schema={parameterSchema}
                              values={lora.parameter_values ?? {}}
                              errors={parameterErrors.map(error => error.slice(lora.id.length + 2))}
                              onChange={(parameterId, value) => updateAdditionalLoraParameter(lora.id, parameterId, value)}
                            />
                          )}
                          {!parameterFieldsReady && parameterErrors.map(error => (
                            <p key={error} role="status" className="mt-1 text-[8px] text-red-300">{error.slice(lora.id.length + 2)}</p>
                          ))}
                        </div>
                        )
                      })}
                    </div>
                  )}
                  {loraLoadError && <p role="status" className="mt-1 text-[8px] text-red-300">{loraLoadError}</p>}
                  {hasInvalidExplicitLora && <p role="status" className="mt-1 text-[8px] text-red-300">A LoRA does not work with the selected model. Change where it is used or remove it.</p>}
                  {hasInvalidLoraMultiplier && <p role="status" className="mt-1 text-[8px] text-red-300">LoRA multipliers must be between -10 and 10.</p>}
                  {hasInvalidLoraParameters && <p role="status" className="mt-1 text-[8px] text-red-300">Fix the LoRA settings above before queueing.</p>}
                </fieldset>
                <label className="mt-3 flex items-center justify-between text-[10px] text-text-secondary">
                  Candidate packs
                  <input aria-label="Reference candidate packs" type="number" min={1} max={8} value={candidateCount} onChange={event => setCandidateCount(Math.max(1, Math.min(8, Number(event.target.value) || 1)))} className="min-h-11 w-16 rounded border border-border bg-bg-tertiary px-2 py-1 text-right md:min-h-0" />
                </label>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label className="text-[9px] text-text-muted">Collage columns
                    <input aria-label="Reference sheet collage columns" type="number" min={1} max={4} value={columns} onChange={event => setColumns(Math.max(1, Math.min(4, Number(event.target.value) || 1)))} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-text-secondary md:min-h-0" />
                  </label>
                  <label className="text-[9px] text-text-muted">Palette swatches
                    <input aria-label="Reference sheet palette swatches" type="number" min={3} max={12} value={paletteSwatches} onChange={event => setPaletteSwatches(Math.max(3, Math.min(12, Number(event.target.value) || 3)))} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-text-secondary md:min-h-0" />
                  </label>
                </div>
                <label htmlFor="project-reference-planning-model" className="mt-2 block text-[10px] text-text-secondary">Idea planning
                  <select id="project-reference-planning-model" aria-label="Reference planning model" value={planningModel} onChange={event => setPlanningModel(event.target.value)} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px] text-text-primary md:min-h-0">
                    <option value="auto">Auto (local only)</option>
                    <option value="deterministic">Built-in planner</option>
                    {planningModels.map(model => <option key={model.id} value={model.id}>{model.label} · {projectReferenceProviderLabel(model.provider)} · Ready</option>)}
                  </select>
                </label>
                {selectedPlanningModel && (selectedPlanningModel.provider ?? 'local') !== 'local' && (
                  <div className="mt-1 text-[9px] text-amber-300">
                    <p>Selected remote planning sends the reference text to {projectReferenceProviderLabel(selectedPlanningModel.provider)}; that provider’s terms and privacy policy apply.</p>
                    <details className="mt-1 text-text-muted">
                      <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                      <p>Model ID: {selectedPlanningModel.id} · Provider ID: {selectedPlanningModel.provider}</p>
                    </details>
                  </div>
                )}
                <label htmlFor="project-reference-review-model" className="mt-2 block text-[10px] text-text-secondary">Visual quality check
                  <select id="project-reference-review-model" aria-label="Reference visual review model" value={reviewModel} onChange={event => setReviewModel(event.target.value)} className="mt-1 min-h-11 w-full rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px] text-text-primary md:min-h-0">
                    <option value="auto_local">{intelligencePolicy === 'uncensored_auto' && uncensoredReviewContract ? `Auto local · ${projectReferenceModelLabel(uncensoredReviewContract.resolved_model, llmCatalogModels)}` : 'Auto local'}</option>
                    <option value="off" disabled={mandatoryReview}>{mandatoryReview ? 'Off · unavailable for unrestricted / explicit output' : 'Off'}</option>
                    {selectableReviewModels.map(model => <option key={model.id} value={model.id}>{model.label} · {projectReferenceProviderLabel(model.provider)} · {intelligencePolicy !== 'uncensored_auto' || uncensoredReviewContract?.setup_state === 'ready_resident' ? 'Ready' : uncensoredReviewContract?.setup_state === 'ready_unloaded' ? 'Installed; loads when needed' : 'Setup required'}</option>)}
                  </select>
                </label>
                <p className="mt-1 text-[8px] text-text-muted">Automatic choices never send data remotely. Standard automatic uses an available local vision model. Unrestricted local automatic uses the required Paperscarecrow model and its vision projector; both may load when the quality check starts.</p>
                {mandatoryReview && <p role="status" className="mt-1 text-[9px] text-amber-200">A visual quality check is required for unrestricted or explicit output and cannot be turned off.</p>}
                {intelligencePolicy === 'uncensored_auto' && uncensoredReviewContract && (
                  <div aria-label="Required visual reviewer setup" className="mt-1.5 rounded border border-border bg-bg-primary/50 p-1.5 text-[8px] leading-relaxed text-text-muted">
                    <p>Model: {projectReferenceModelLabel(uncensoredReviewContract.resolved_model, llmCatalogModels)} · Local only · No remote fallback</p>
                    <p>Model file: {uncensoredReviewContract.installed ? 'Installed' : 'Missing'} · Image understanding: {uncensoredReviewContract.projector_available ? 'Installed' : 'Missing'}</p>
                    <p>Status: {uncensoredReviewContract.loading ? projectReferenceReviewerLoadingLabel(uncensoredReviewContract.loading_phase) : uncensoredReviewContract.resident ? 'Loaded' : uncensoredReviewContract.queue_ready ? 'Ready to load automatically' : 'Not loaded'} · Image understanding: {!uncensoredReviewContract.vision_capable ? 'Not configured' : uncensoredReviewContract.vision_available === true ? 'Ready' : uncensoredReviewContract.vision_available === false ? 'Unavailable' : 'Checked after loading'}</p>
                    <p role="status" className={uncensoredReviewContract.queue_ready ? 'mt-0.5 text-accent-green' : 'mt-0.5 text-red-300'}>{reviewerSetupCopy}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {reviewerSetupAction && machineControls && (
                        <button type="button" disabled={reviewerAction !== null} onClick={() => { void refreshReviewerSetup(true) }} className="min-h-11 min-w-11 rounded border border-accent-blue/40 px-3 py-0.5 text-accent-blue disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-1.5">
                          {reviewerAction === 'loading' ? 'Preparing visual review model…' : projectReferenceReviewerActionLabel(reviewerSetupAction.kind)}
                        </button>
                      )}
                      <button type="button" disabled={reviewerAction !== null} onClick={() => { void refreshReviewerSetup(false) }} className="min-h-11 min-w-11 rounded border border-border px-3 py-0.5 text-text-secondary disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-1.5">
                        {reviewerAction === 'refreshing' ? 'Refreshing visual review status…' : 'Refresh visual review status'}
                      </button>
                    </div>
                    {reviewerSetupAction && !machineControls && <p className="mt-1 text-amber-200">Open Maestro at localhost on the host computer to install, load, or reload the required visual review model. LAN sessions can refresh its status but cannot change models running on the host.</p>}
                    {reviewerActionError && <p role="status" className="mt-1 text-red-300">{reviewerActionError}</p>}
                    <details className="mt-1 rounded border border-border/70 px-1.5">
                      <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                      <p>Model ID: {uncensoredReviewContract.resolved_model}</p>
                      <p>Provider ID: {uncensoredReviewContract.resolved_provider}</p>
                      <p>Projector ID: {uncensoredReviewContract.required_projector}</p>
                      {uncensoredReviewContract.loading_phase && <p>Loading phase ID: {uncensoredReviewContract.loading_phase}</p>}
                    </details>
                  </div>
                )}
                {intelligencePolicy === 'uncensored_auto' && !uncensoredReviewContract && (
                  <div className="mt-1 text-[9px]">
                    <p role="status" className="text-red-300">{reviewerSetupCopy}</p>
                    <button type="button" disabled={reviewerAction !== null} onClick={() => { void refreshReviewerSetup(false) }} className="mt-1 min-h-11 min-w-11 rounded border border-border px-3 py-0.5 text-text-secondary disabled:opacity-40 md:min-h-0 md:min-w-0 md:px-1.5">{reviewerAction === 'refreshing' ? 'Refreshing visual review status…' : 'Refresh visual review status'}</button>
                    {reviewerActionError && <p role="status" className="mt-1 text-red-300">{reviewerActionError}</p>}
                  </div>
                )}
                {intelligencePolicy === 'standard_auto' && selectedReviewModel && (selectedReviewModel.provider ?? 'local') !== 'local' && (
                  <div className="mt-1 text-[9px] text-amber-300">
                    <p>Selected remote visual review sends generated reference images to {projectReferenceProviderLabel(selectedReviewModel.provider)}; that provider’s terms and privacy policy apply.</p>
                    <details className="mt-1 text-text-muted">
                      <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                      <p>Model ID: {selectedReviewModel.id} · Provider ID: {selectedReviewModel.provider}</p>
                    </details>
                  </div>
                )}
                <details className="mt-2 rounded border border-border px-2 py-1.5">
                  <summary className="flex min-h-11 cursor-pointer items-center text-[10px] text-text-secondary md:min-h-0">Advanced</summary>
                  <fieldset className="mt-2">
                    <legend className="text-[9px] text-text-muted">Automatic layout help</legend>
                    <div className="mt-1 grid grid-cols-2 gap-1">
                      <button type="button" disabled aria-pressed={false} title="No approved automatic layout helper is installed" className="min-h-11 min-w-11 rounded border border-border px-2 py-1 text-[9px] text-text-muted opacity-50 md:min-h-0 md:min-w-0">Automatic · unavailable</button>
                      <button type="button" aria-pressed className="min-h-11 min-w-11 rounded border border-accent-blue bg-accent-blue/10 px-2 py-1 text-[9px] text-accent-blue md:min-h-0 md:min-w-0">Off</button>
                    </div>
                    <p className="mt-1 text-[8px] text-text-muted">Only approved layout helpers appear here. Subject and content LoRAs are never turned on automatically.</p>
                  </fieldset>
                </details>
                {sheetMode === 'draft' ? (
                  <p className="mt-1.5 text-[9px] leading-relaxed text-text-muted">Draft creates each sheet independently and does not use automatic fixes.</p>
                ) : mandatoryReview || reviewModel !== 'off' ? (
                  <label htmlFor="project-reference-max-repairs" className="mt-2 flex items-center justify-between gap-2 text-[10px] text-text-secondary">
                    Maximum panel repairs
                    <input id="project-reference-max-repairs" aria-label="Maximum panel repair attempts" type="number" min={1} max={5} value={maxRepairAttempts} onChange={event => setMaxRepairAttempts(Math.max(1, Math.min(5, Number(event.target.value) || 1)))} className="min-h-11 w-16 rounded border border-border bg-bg-tertiary px-2 py-1 text-right md:min-h-0" />
                  </label>
                ) : (
                  <p className="mt-1.5 text-[9px] leading-relaxed text-text-muted">Visual review is off, so automatic fixes are also off.</p>
                )}
                {capabilitiesLoadError && <p role="status" className="mt-2 text-[10px] text-red-300">{capabilitiesLoadError}</p>}
                {hasInvalidAuthoredSettings && <p role="status" className="mt-2 text-[9px] text-red-300">Remove duplicate values, stay within the allowed limits, and trim extra spaces. Every detail also needs an available source sheet.</p>}
                {visibleQueueBlockers.length > 0 && (
                  <section id="project-reference-queue-blockers" aria-label="Queue blocked by" className="mt-3 rounded border border-red-400/30 bg-red-400/5 px-2 py-1.5">
                    <h4 className="text-[9px] font-medium text-red-200">Queue blocked by</h4>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[8px] text-red-200/90">
                      {visibleQueueBlockers.map(blocker => <li key={blocker.id}>{blocker.message}</li>)}
                    </ul>
                  </section>
                )}
                <button
                  onClick={() => void generate()}
                  disabled={queueBlockers.length > 0}
                  aria-disabled={queueBlockers.length > 0}
                  aria-describedby={visibleQueueBlockers.length > 0 ? 'project-reference-queue-blockers' : undefined}
                  title={visibleQueueBlockers.length > 0 ? `Queue blocked: ${visibleQueueBlockers.map(blocker => blocker.message).join(' ')}` : 'Queue reference packs'}
                  className="mt-3 flex min-h-11 min-w-11 w-full items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 py-2 text-xs font-medium text-white disabled:opacity-40 md:min-h-0 md:min-w-0"
                >
                  {submitting ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Queue reference packs
                </button>
                <p className="mt-2 text-[9px] leading-relaxed text-text-muted">Each candidate is a complete reference pack with the selected number of sheets. Candidate count creates alternatives; Sheets per pack controls how many images each alternative contains. Keep one or more. Other candidates remain available until you delete them.</p>
                {queuedMessage && <p role="status" className="mt-2 text-[10px] text-accent-blue">{queuedMessage}</p>}
                {pendingFreshJobIds.map((jobId, index) => {
                  const job = jobs.find(candidate => candidate.id === jobId)
                  return <p key={jobId} role="status" className="mt-1 text-[9px] text-text-muted">Pack {index + 1}: {projectReferencePendingPhaseLabel(job?.phase)}</p>
                })}
                {pendingFreshJobIds.length > 0 && (
                  <details className="mt-1 text-[8px] text-text-muted">
                    <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                    {pendingFreshJobIds.map(jobId => {
                      const job = jobs.find(candidate => candidate.id === jobId)
                      return <p key={jobId}>Job ID: {jobId} · Phase: {job?.phase || 'queued'}</p>
                    })}
                  </details>
                )}
                </div>
              </div>

              <div className="overflow-visible p-4">
                {loadError && <p role="status" className="mb-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-[10px] text-red-300">{loadError}</p>}
                {loading && !assets.length ? (
                  <div className="flex h-48 items-center justify-center"><Loader2 size={20} className="animate-spin text-accent-blue" /></div>
                ) : assets.length === 0 ? (
                  <div className="flex h-48 items-center justify-center text-xs text-text-muted">No reference cards in this project yet.</div>
                ) : (
                  <div className="space-y-4">
                    {assets.map(asset => (
                      <section key={asset.id} className="rounded-lg border border-border bg-bg-tertiary/60 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div><h3 className="text-xs font-medium text-text-primary">{asset.name}</h3><p className="text-[9px] uppercase tracking-wide text-text-muted">{asset.asset_type}</p></div>
                          <div className="flex items-center gap-2">
                            <span className="text-[9px] text-text-muted">{asset.variants.length} variants</span>
                            <label className={`flex cursor-pointer items-center gap-1 rounded border border-accent-blue/40 px-2 py-1 text-[9px] text-accent-blue hover:bg-accent-blue/10 ${importing ? 'pointer-events-none opacity-50' : ''}`}>
                              {importing?.assetId === asset.id ? <Loader2 size={10} className="animate-spin" /> : <FileUp size={10} />}
                              Import media
                              <input
                                type="file"
                                accept="image/*,video/*"
                                aria-label={`Import media for ${asset.name}`}
                                className="sr-only"
                                disabled={Boolean(importing)}
                                onChange={event => {
                                  const file = event.currentTarget.files?.[0]
                                  event.currentTarget.value = ''
                                  if (file) void importVariant(asset.id, file)
                                }}
                              />
                            </label>
                          </div>
                        </div>
                        <p className="mt-1 text-[10px] text-text-secondary">{asset.description}</p>
                        {importing?.assetId === asset.id && <p className="mt-2 flex items-center gap-1 text-[9px] text-accent-blue"><Loader2 size={9} className="animate-spin" /> {importing.message}</p>}
                        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {asset.variants.map(variant => {
                            const applyOutputs = getProjectAssetApplyOutputs(variant)
                            const applyOutput = applyOutputs[0]
                            const componentOutputs = getProjectAssetComponentOutputs(variant)
                            const supportingOutputs = variant.variant_type === 'reference_pack'
                              ? applyOutputs.slice(1)
                              : componentOutputs
                            const packMetadata = variant.metadata.reference_pack
                            const qualityPresentation = projectReferenceQualityPresentation(packMetadata)
                            const anchorPrivacy = normalizeProjectReferenceAnchorPrivacy(
                              packMetadata?.anchor_privacy,
                              packMetadata?.schema_version,
                            )
                            const operationRoutes = packMetadata?.operation_routing?.operations
                            const sheetStatus = referenceSheetStatus(variant)
                            const pendingKey = projectAssetVariantOperationKey(project, asset.id, variant.id)
                            const pendingAction = pendingSheetActions[pendingKey]
                            const requiresPrivateAuthoring = projectReferenceRetryNeedsPrivateAuthoring(variant)
                            const authoredSeal = packMetadata?.authored_settings?.seal
                            const privateAuthoredSnapshot = authoredSeal
                              ? authoredSettingsSnapshots.current.get(authoredSeal)
                              : undefined
                            const exactStyleReady = isProjectReferenceStyleReplayReady(
                              packMetadata?.authored_settings, privateAuthoredSnapshot?.style,
                            )
                            const exactCharacterReplayReady = isProjectReferenceCharacterReplayReady(
                              packMetadata,
                              privateAuthoredSnapshot ? {
                                character_profile: privateAuthoredSnapshot.characterProfile,
                                explicit_convenience: privateAuthoredSnapshot.explicitConvenience,
                              } : undefined,
                            )
                            const exactAuthoringReady = exactStyleReady && exactCharacterReplayReady && (
                              !requiresPrivateAuthoring || (
                                authoringAvailability[pendingKey] === 'ready'
                                && Boolean(authoredSeal && privateAuthoredSnapshot)
                              )
                            )
                            const exactAuthoringCopy = !exactStyleReady
                              ? 'The saved style settings are incomplete or have changed. Retry and Edit remain off; create a new pack instead.'
                              : !exactCharacterReplayReady
                                ? 'The saved Character profile or anatomy-detail settings are incomplete or have changed. Retry and Edit remain off; create a new pack instead.'
                              : authoringAvailability[pendingKey] === 'unavailable'
                                ? 'Private creation settings are unavailable. Reload them to enable Retry and Edit; Maestro will not guess or omit your settings.'
                                : 'Loading the private settings needed for Retry and Edit…'
                            const summarizedLoras = [
                              ...(packMetadata?.additional_loras?.applied ?? []),
                              ...(packMetadata?.additional_loras?.skipped ?? []),
                            ]
                            const requiresPrivateLoraInputs = summarizedLoras.some(
                              hasProjectReferenceLoraParameterSummary,
                            )
                            const exactLoraInputsReady = !requiresPrivateLoraInputs || Boolean(
                              packMetadata?.plan_seal
                              && loraParameterSnapshots.current.has(packMetadata.plan_seal),
                            )
                            const recordedReviewModel = packMetadata?.review?.resolved_model
                              ?? packMetadata?.review?.requested_model
                              ?? 'off'
                            const retryReview = resolveProjectReferenceRetryReview(
                              {
                                content_capability: packMetadata?.content_capability,
                                explicit_output: packMetadata?.explicit_output === true
                                  || applyOutput?.metadata.explicit === true,
                                intelligence_policy: packMetadata?.intelligence_policy,
                                review: recordedReviewModel !== 'off',
                                review_model: recordedReviewModel,
                                review_provider: packMetadata?.review?.resolved_provider ?? undefined,
                              },
                              {
                                review_model: reviewModel,
                                review_provider: selectedReviewModel?.provider,
                              },
                              reviewModels,
                              referenceCapabilities,
                            )
                            const exactRetryReady = exactAuthoringReady && exactLoraInputsReady && retryReview.ready
                            const pendingJob = pendingAction?.jobId
                              ? jobs.find(candidate => candidate.id === pendingAction.jobId)
                              : undefined
                            const planningModelId = packMetadata?.planning?.resolved_model
                              ?? packMetadata?.planning?.requested_model
                            const reviewModelId = packMetadata?.review?.resolved_model
                              ?? packMetadata?.review?.requested_model
                            const editing = editVariantId === variant.id
                            const directorReferenceKind = getDirectorProjectReferenceKind(asset.asset_type)
                            const directorApplyUnsupported = referenceReturnMode === 'director'
                              && directorReferenceKind === null
                              && !applyOutput?.media_type?.startsWith('video/')
                            const applyLabel = applyOutput?.media_type?.startsWith('video/')
                              ? 'Use in Generate as an LTX-2.3 control and prompt'
                              : variant.variant_type === 'reference_pack'
                                ? `Use ${applyOutputs.length} pack ${applyOutputs.length === 1 ? 'sheet' : 'sheets'} as ${referenceReturnMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 references (automatic)' : 'Generate references'}`
                              : variant.variant_type === 'reference_sheet'
                                ? `Use complete sheet as ${referenceReturnMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 reference (automatic)' : 'Generate reference'}`
                                : `Use as ${referenceReturnMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 reference (automatic)' : 'Generate reference'}`
                            return (
                              <div key={variant.id} className={`overflow-hidden rounded-md border ${variant.status === 'kept' ? 'border-accent-green/60' : variant.status === 'rejected' ? 'border-border opacity-60' : 'border-border'}`}>
                                {applyOutput && (
                                  <ProjectAssetPreview
                                    project={project}
                                    assetId={asset.id}
                                    output={applyOutput}
                                    label={variant.variant_type === 'reference_pack' ? `${variant.label} first ordered pack sheet` : variant.variant_type === 'reference_sheet' ? `${variant.label} complete reference sheet` : variant.label}
                                  />
                                )}
                                <div className="p-2">
                                  <div className="flex items-center justify-between gap-1 text-[9px]">
                                    <span className="truncate text-text-secondary">{variant.label}</span>
                                    <span className="flex shrink-0 items-center gap-1">
                                      {qualityPresentation?.recommended && (
                                        <span className="rounded-full border border-accent-blue/40 bg-accent-blue/10 px-1.5 py-0.5 text-[8px] font-medium text-accent-blue" title={qualityPresentation.preliminary ? 'Preliminary recommendation; the visual quality check is still pending.' : 'Recommended from the available candidate checks.'}>Recommended</span>
                                      )}
                                      <span className="text-text-muted">{projectReferenceVariantStatusLabel(variant.status)}</span>
                                    </span>
                                  </div>
                                  {sheetStatus && (
                                    <div className="mt-1 text-[9px] leading-relaxed">
                                      <p className={sheetStatus.className}>{sheetStatus.label}</p>
                                      {sheetStatus.repair && <p className="text-text-muted">{sheetStatus.repair}</p>}
                                    </div>
                                  )}
                                  {qualityPresentation && (
                                    <div className={`mt-1 rounded border px-1.5 py-1 text-[8px] leading-relaxed ${qualityPresentation.tone === 'pass' ? 'border-accent-green/30 bg-accent-green/10 text-accent-green' : qualityPresentation.tone === 'residual' ? 'border-amber-400/30 bg-amber-400/10 text-amber-200' : 'border-border bg-bg-secondary/60 text-text-muted'}`} data-reference-fidelity={qualityPresentation.tone}>
                                      <p className="font-medium">
                                        {qualityPresentation.stateLabel}
                                        {qualityPresentation.gradeLabel ? ` · ${qualityPresentation.gradeLabel}` : ''}
                                        {qualityPresentation.scoreLabel ? ` · ${qualityPresentation.scoreLabel}` : ''}
                                      </p>
                                      {qualityPresentation.preliminary && <p>Preliminary recommendation · not yet graded</p>}
                                      {qualityPresentation.residualSummary && <p>{qualityPresentation.residualSummary}</p>}
                                      {qualityPresentation.correctionAvailable && <p>Suggested fixes are available for Retry or Edit.</p>}
                                      {qualityPresentation.notice && <p>{qualityPresentation.notice}</p>}
                                    </div>
                                  )}
                                  {packMetadata && (
                                    <div className="mt-1 text-[8px] leading-relaxed text-text-muted">
                                      <p>{friendlyRole(packMetadata.intent ?? 'generic')} · {friendlyRole(packMetadata.depth ?? 'standard')} · {packMetadata.sheet_count ?? applyOutputs.length} sheets</p>
                                      <p>
                                        Planner: {planningModelId ? projectReferenceModelLabel(planningModelId, llmCatalogModels) : 'Built-in planner'}
                                        {packMetadata.planning?.resolved_provider ? ` · ${projectReferenceProviderLabel(packMetadata.planning.resolved_provider)}` : ''}
                                        {' · '}Quality check: {reviewModelId ? projectReferenceModelLabel(reviewModelId, llmCatalogModels) : 'Off'}
                                        {packMetadata.review?.resolved_provider ? ` · ${projectReferenceProviderLabel(packMetadata.review.resolved_provider)}` : ''}
                                      </p>
                                      {anchorPrivacy && <p>Main image visibility: {friendlyRole(anchorPrivacy)} · {packMetadata.private_output ? 'private access' : 'project access'}</p>}
                                      {operationRoutes && <p>Processing: {Object.entries(operationRoutes).map(([operation, route]) => `${projectReferenceOperationLabel(operation)} · ${projectReferenceRouteStatusLabel(route.status)}`).join(' · ')}</p>}
                                      {packMetadata.additional_loras && (
                                        <p>
                                          LoRAs: {packMetadata.additional_loras.applied.length} used
                                          {packMetadata.additional_loras.applied.length > 0 ? ` (${packMetadata.additional_loras.applied.map(lora => `${lora.id}: ${lora.resolved_scope.map(projectReferenceLoraScopeLabel).join(' + ')}`).join('; ')})` : ''}
                                          {packMetadata.additional_loras.skipped.length > 0 ? ` · ${packMetadata.additional_loras.skipped.length} not used` : ''}
                                          {summarizedLoras.some(hasProjectReferenceLoraParameterSummary)
                                            ? ` · ${summarizedLoras.filter(hasProjectReferenceLoraParameterSummary).length} saved LoRA ${summarizedLoras.filter(hasProjectReferenceLoraParameterSummary).length === 1 ? 'setup' : 'setups'} (${summarizedLoras.reduce((count, lora) => count + (lora.parameters?.count ?? 0), 0)} private values)`
                                            : ''}
                                        </p>
                                      )}
                                      <details className="mt-1 rounded border border-border/70 px-1.5">
                                        <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                                        <p>Variant status ID: {variant.status}</p>
                                        <p>Planning model ID: {planningModelId ?? 'none'} · Provider ID: {packMetadata.planning?.resolved_provider ?? 'none'}</p>
                                        <p>Review model ID: {reviewModelId ?? 'none'} · Provider ID: {packMetadata.review?.resolved_provider ?? 'none'}</p>
                                        {packMetadata.additional_loras && (
                                          <ul>
                                            {packMetadata.additional_loras.applied.map(lora => <li key={`applied:${lora.id}`}>LoRA ID: {lora.id} · Resolved scope IDs: {lora.resolved_scope.join(' + ')}</li>)}
                                            {packMetadata.additional_loras.skipped.map(lora => <li key={`skipped:${lora.id}`}>LoRA ID: {lora.id} · Not used reason ID: {lora.reason}</li>)}
                                          </ul>
                                        )}
                                        {operationRoutes && (
                                          <ul>
                                            <li>Requested capability ID: {packMetadata.operation_routing?.requested_capability ?? 'unknown'}</li>
                                            {Object.entries(operationRoutes).map(([operation, route]) => (
                                              <li key={operation}>
                                                {operation}: status {route.status}; requested model {route.requested_model ?? 'none'}; resolved model {route.resolved_model ?? 'none'}
                                                {route.schedule ? `; schedule ${route.schedule.steps} steps / ${route.schedule.guidance} ${route.schedule.guidance_key} / ${route.schedule.source}` : '; schedule none'}
                                                {route.recipe_id ? `; recipe ${route.recipe_id}` : ''}
                                                {route.verification_status ? `; verification ${route.verification_status}` : ''}
                                                {route.reason ? `; reason ${route.reason}` : ''}
                                              </li>
                                            ))}
                                          </ul>
                                        )}
                                      </details>
                                    </div>
                                  )}
                                  {supportingOutputs.length > 0 && (
                                    <details className="mt-1.5 rounded border border-border/70 bg-bg-secondary/50">
                                      <summary className="flex cursor-pointer list-none items-center justify-between px-2 py-1 text-[9px] text-text-secondary">
                                        {supportingOutputs.length} {variant.variant_type === 'reference_pack' ? 'more pack sheets' : 'detail panels'} <ChevronDown size={10} aria-hidden="true" />
                                      </summary>
                                      <div className="grid grid-cols-2 gap-1 border-t border-border p-1.5">
                                        {supportingOutputs.map(output => {
                                          const role = output.metadata.reference_pack?.role || output.metadata.reference_sheet?.role || output.label || 'component'
                                          return (
                                            <figure key={output.id} className="overflow-hidden rounded border border-border bg-bg-tertiary">
                                              <ProjectAssetPreview project={project} assetId={asset.id} output={output} label={`${variant.label}: ${friendlyRole(role)}`} />
                                              <figcaption className="truncate px-1 py-0.5 text-[8px] text-text-muted">{friendlyRole(role)}</figcaption>
                                            </figure>
                                          )
                                        })}
                                      </div>
                                    </details>
                                  )}
                                  <div className="mt-1.5 flex gap-1">
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void updateStatus(asset.id, variant.id, 'kept')} className="flex flex-1 items-center justify-center gap-1 rounded bg-accent-green/15 px-1 py-1 text-[9px] text-accent-green disabled:opacity-40"><Check size={9} /> Keep</button>
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void updateStatus(asset.id, variant.id, 'rejected')} className="rounded border border-border px-2 py-1 text-[9px] text-text-muted disabled:opacity-40">Reject</button>
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void deleteVariant(asset.id, variant.id, variant.label)} className="rounded border border-red-500/30 px-2 py-1 text-red-400 disabled:opacity-40" title="Delete candidate and copied media" aria-label={`Delete ${variant.label}`}><Trash2 size={9} /></button>
                                  </div>
                                  {(variant.variant_type === 'reference_sheet' || variant.variant_type === 'reference_pack') && (
                                    <div className="mt-1.5 grid grid-cols-2 gap-1">
                                      <button type="button" disabled={Boolean(pendingAction) || !exactRetryReady} onClick={() => void generateFromVariant(asset, variant)} className="flex items-center justify-center gap-1 rounded border border-border px-1 py-1 text-[9px] text-text-secondary disabled:opacity-40">
                                        {pendingAction ? <Loader2 size={9} className="animate-spin" /> : <RotateCcw size={9} />} Retry
                                      </button>
                                      <button
                                        type="button"
                                        disabled={Boolean(pendingAction) || !exactRetryReady}
                                        aria-expanded={editing}
                                        aria-controls={`reference-sheet-edit-${variant.id}`}
                                        onClick={() => {
                                          setEditVariantId(current => current === variant.id ? null : variant.id)
                                          setEditInstruction('')
                                        }}
                                        className="flex items-center justify-center gap-1 rounded border border-border px-1 py-1 text-[9px] text-text-secondary disabled:opacity-40"
                                      >
                                        <Pencil size={9} /> Edit
                                      </button>
                                    </div>
                                  )}
                                      {(!exactStyleReady || (requiresPrivateAuthoring && !exactAuthoringReady)) && <p role="status" className="mt-1 text-[8px] leading-relaxed text-amber-200">{exactAuthoringCopy}</p>}
                                  {requiresPrivateLoraInputs && !exactLoraInputsReady && <p role="status" className="mt-1 text-[8px] leading-relaxed text-amber-200">Private LoRA settings are still loading or unavailable. Retry and Edit stay off so no values are guessed or lost.</p>}
                                  {authoringAvailability[pendingKey] === 'unavailable'
                                    && ((requiresPrivateAuthoring && !exactAuthoringReady) || (requiresPrivateLoraInputs && !exactLoraInputsReady)) && (
                                    <button type="button" onClick={() => setPrivateReplayRetry(current => current + 1)} className="mt-1 rounded border border-amber-400/40 px-1.5 py-0.5 text-[8px] text-amber-100">Reload private settings</button>
                                  )}
                                  {!retryReview.ready && <p role="status" className="mt-1 text-[8px] leading-relaxed text-amber-200">{retryReview.intelligence_policy === 'uncensored_auto' ? `Retry and Edit are waiting for the required local visual review model. ${reviewerSetupCopy}` : 'Retry and Edit need a loaded local visual review model for this pack. Load and select a compatible model first.'}</p>}
                                  {retryReview.use_current_reviewer && <p role="status" className="mt-1 text-[8px] leading-relaxed text-text-muted">The original visual review model is unavailable. Retry or Edit will use the current compatible model.</p>}
                                  {(variant.variant_type === 'reference_sheet' || variant.variant_type === 'reference_pack') && <p className="mt-1 text-[8px] leading-relaxed text-text-muted">Retry and Edit reuse the saved style, models, privacy, fixes, planning, and quality-check choices. The kept candidate does not change.</p>}
                                  {editing && (variant.variant_type === 'reference_sheet' || variant.variant_type === 'reference_pack') && (
                                    <div id={`reference-sheet-edit-${variant.id}`} className="mt-1.5 rounded border border-border p-1.5">
                                      <label htmlFor={`reference-sheet-edit-instruction-${variant.id}`} className="text-[9px] text-text-muted">What should change in the next candidate?</label>
                                      <textarea
                                        id={`reference-sheet-edit-instruction-${variant.id}`}
                                        value={editInstruction}
                                        onChange={event => setEditInstruction(event.target.value)}
                                        rows={3}
                                        className="mt-1 w-full resize-y rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] text-text-primary"
                                      />
                                      <div className="mt-1 flex gap-1">
                                        <button type="button" disabled={!editInstruction.trim() || Boolean(pendingAction) || !exactRetryReady} onClick={() => void generateFromVariant(asset, variant, editInstruction)} className="flex-1 rounded bg-accent-blue px-1 py-1 text-[9px] text-white disabled:opacity-40">Queue edited candidate</button>
                                        <button type="button" onClick={() => { setEditVariantId(null); setEditInstruction('') }} className="rounded border border-border px-2 py-1 text-[9px] text-text-muted">Cancel</button>
                                      </div>
                                    </div>
                                  )}
                                  {pendingAction && (
                                    <div className="mt-1.5 text-[9px] text-accent-blue">
                                      <p role="status" className="flex items-center gap-1"><Loader2 size={9} className="animate-spin" /> {pendingAction.jobId ? `${projectReferencePendingPhaseLabel(pendingJob?.phase)}; waiting for the new candidate…` : 'Submitting…'}</p>
                                      {pendingAction.jobId && (
                                        <details className="mt-1 text-[8px] text-text-muted">
                                          <summary className="flex min-h-11 cursor-pointer items-center md:min-h-0">Technical details</summary>
                                          <p>Job ID: {pendingAction.jobId} · Phase: {pendingJob?.phase || 'queued'}</p>
                                        </details>
                                      )}
                                    </div>
                                  )}
                                  {variant.status === 'kept' && applyOutput && (
                                    <>
                                      <button type="button" disabled={directorApplyUnsupported} onClick={() => void applyReference(asset, variant)} className="mt-1.5 w-full rounded border border-accent-blue/40 px-1 py-1 text-[9px] text-accent-blue disabled:cursor-not-allowed disabled:border-border disabled:text-text-muted">{directorApplyUnsupported ? 'Use from Generate' : applyLabel}</button>
                                      {directorApplyUnsupported && <p className="mt-1 text-[8px] leading-relaxed text-amber-200">Director currently accepts only Character and Location references. Use this candidate from Generate instead.</p>}
                                    </>
                                  )}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </div>
            </div>
        </>
      )}
    </section>
  )
}
