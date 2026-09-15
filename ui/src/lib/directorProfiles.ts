import type { DirectorProfileSettings, GenerationPreset } from '../api/client'
import type { DirectorImageRoleLoraSelection, DirectorShotImageGuidance } from '../types'
import {
  captureGenerationProfileSettings,
  GenerationProfileSettingsError,
  projectGenerationProfileParameters,
} from './generationProfiles'

type Role = 'creator' | 'editor'

export interface DirectorProfileState {
  directorResolution: string
  directorAspectRatio: string
  directorSeamless: boolean
  directorShotImageGuidance: DirectorShotImageGuidance
  directorVideoInferenceStepsByModel: Record<string, number>
  directorVideoMaxShotFramesByModel: Record<string, number>
  directorVideoFilmGrainIntensity: number
  directorVideoFilmGrainSaturation: number
  directorVideoSelfRefiner: number
  directorAudioScale: number
  directorIdentityGuidanceScale: number
  h3StyleWorkflow: string
  directorImageCreatorModelOverride: string
  directorImageEditorModelOverride: string
  directorImageRoleLoras: Record<Role, DirectorImageRoleLoraSelection[]>
}

const scalarFields = {
  resolution: 'directorResolution',
  aspect_ratio: 'directorAspectRatio',
  seamless: 'directorSeamless',
  shot_image_guidance: 'directorShotImageGuidance',
  video_film_grain_intensity: 'directorVideoFilmGrainIntensity',
  video_film_grain_saturation: 'directorVideoFilmGrainSaturation',
  video_self_refiner: 'directorVideoSelfRefiner',
  audio_scale: 'directorAudioScale',
  identity_guidance_scale: 'directorIdentityGuidanceScale',
  h3_style_workflow: 'h3StyleWorkflow',
} as const

export const directorProfileStateKeys = Object.freeze([
  ...Object.values(scalarFields),
  'directorVideoSpatialUpsampling',
  'directorVideoInferenceStepsByModel', 'directorVideoMaxShotFramesByModel',
  'directorImageCreatorModelOverride', 'directorImageEditorModelOverride', 'directorImageRoleLoras',
])

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value, (_key, item) => {
    if (item === undefined || ['function', 'symbol', 'bigint'].includes(typeof item)
      || (typeof item === 'number' && !Number.isFinite(item))) {
      throw new GenerationProfileSettingsError('Director settings could not all be saved.')
    }
    return item
  })) as T
}

function copyLoras(selections: readonly DirectorImageRoleLoraSelection[]): DirectorImageRoleLoraSelection[] {
  return selections.map(selection => {
    const projected: DirectorImageRoleLoraSelection = {
      id: selection.id,
      multiplier: selection.multiplier,
    }
    if (selection.parameter_schema_digest !== undefined || selection.parameter_values !== undefined) {
      if (selection.parameter_schema_digest === undefined || selection.parameter_values === undefined) {
        throw new GenerationProfileSettingsError('A Director LoRA needs its parameter settings checked.')
      }
      projected.parameter_schema_digest = selection.parameter_schema_digest
      projected.parameter_values = selection.parameter_values
    }
    return copy(projected)
  })
}

export function isDirectorProfile(profile: GenerationPreset): profile is GenerationPreset & {
  profile_version: 3
  profile_context: 'director'
  director_settings: DirectorProfileSettings
} {
  return profile.profile_version === 3 && profile.profile_context === 'director'
    && profile.mode === 'video' && !!profile.director_settings
}

/** Freeze applicable video defaults and bound overrides, excluding mode-envelope metadata. */
export function captureDirectorVideoParameters(
  defaults: object,
  snapshot: Record<string, unknown> | undefined,
  videoModel: string,
): Record<string, unknown> {
  const projectedDefaults = Object.fromEntries(Object.entries(projectGenerationProfileParameters(defaults))
    .filter(([, value]) => value !== undefined))
  if (!snapshot || snapshot.model_type !== videoModel) return projectedDefaults
  const overrides = { ...snapshot }
  for (const field of ['uiSettings', 'spatialUpsampling', 'filmGrainIntensity', 'filmGrainSaturation', 'durationSeconds']) {
    delete overrides[field]
  }
  return {
    ...projectedDefaults,
    ...captureGenerationProfileSettings(overrides, {}).params,
  }
}

/** Capture explicit choices; automatic image roles remain automatic. */
export function captureDirectorProfileSettings(
  state: DirectorProfileState,
  videoModel: string,
  resolvedRoles: Record<Role, string>,
): DirectorProfileSettings {
  const scalars = Object.fromEntries(Object.entries(scalarFields).map(([key, field]) => [key, state[field]]))
  const imageRoles = Object.fromEntries((['creator', 'editor'] as const).map(role => {
    const loras = copyLoras(state.directorImageRoleLoras[role])
    if (loras.length && !resolvedRoles[role]) {
      throw new GenerationProfileSettingsError('A Director LoRA needs its model checked.')
    }
    return [role, {
      model_override: role === 'creator'
        ? state.directorImageCreatorModelOverride : state.directorImageEditorModelOverride,
      lora_model_type: loras.length ? resolvedRoles[role] : null,
      loras,
    }]
  }))
  return copy({
    ...scalars,
    video_inference_steps: state.directorVideoInferenceStepsByModel[videoModel] ?? null,
    video_max_shot_frames: state.directorVideoMaxShotFramesByModel[videoModel] ?? null,
    image_roles: imageRoles,
  }) as DirectorProfileSettings
}

/** Resolve capabilities before applying; identical filenames are not model identity. */
export function directorProfileRoleMismatch(
  settings: DirectorProfileSettings,
  resolvedRoles: Record<Role, string>,
): Role | null {
  for (const role of ['creator', 'editor'] as const) {
    const selected = settings.image_roles[role]
    if (selected.loras.length && selected.lora_model_type !== resolvedRoles[role]) return role
  }
  return null
}

/** Return technical choices without job prompts, media, mode switches or authority. */
export function restoreDirectorProfileSettings(
  settings: DirectorProfileSettings,
  videoModel: string,
  current: DirectorProfileState,
): DirectorProfileState {
  const scalars = Object.fromEntries(Object.entries(scalarFields).map(([key, field]) => [field, settings[key as keyof typeof scalarFields]]))
  const steps = { ...current.directorVideoInferenceStepsByModel }
  const frames = { ...current.directorVideoMaxShotFramesByModel }
  if (settings.video_inference_steps === null) delete steps[videoModel]
  else steps[videoModel] = settings.video_inference_steps
  if (settings.video_max_shot_frames === null) delete frames[videoModel]
  else frames[videoModel] = settings.video_max_shot_frames
  return copy({
    ...scalars,
    directorVideoInferenceStepsByModel: steps,
    directorVideoMaxShotFramesByModel: frames,
    directorImageCreatorModelOverride: settings.image_roles.creator.model_override,
    directorImageEditorModelOverride: settings.image_roles.editor.model_override,
    directorImageRoleLoras: {
      creator: copyLoras(settings.image_roles.creator.loras),
      editor: copyLoras(settings.image_roles.editor.loras),
    },
  }) as DirectorProfileState
}
