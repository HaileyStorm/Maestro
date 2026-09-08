import schema from '../../../app/services/generation_profile_fields.json' with { type: 'json' }

export class GenerationProfileSettingsError extends Error {}

type Settings = Record<string, unknown>
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key)
const parameterKeys = new Set(Object.keys(schema.params))
const excludedKeys = new Set(Object.keys(schema.excluded_params))
const customKeys = new Set(Object.keys(schema.custom_settings))

export const generationProfileParameterKeys = Object.freeze([...parameterKeys])
export const generationProfileUiKeys = Object.freeze(Object.keys(schema.ui))
const globalModeUiKeys = new Set(['h3StyleWorkflow', 'directorIdentityGuidanceScale'])
export const generationModeUiKeys = Object.freeze(
  generationProfileUiKeys.filter(key => !globalModeUiKeys.has(key)),
)

function copy(value: unknown): unknown {
  // A profile is detached from subsequent edits and contains JSON values only.
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value, (_key, item) => {
    if ((typeof item === 'number' && !Number.isFinite(item))
      || ['function', 'symbol', 'bigint'].includes(typeof item)) {
      throw new GenerationProfileSettingsError('A setting cannot be saved in a profile.')
    }
    return item
  }))
}

function technicalCustomSettings(value: unknown): unknown {
  if (value == null) return value
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new GenerationProfileSettingsError('The custom settings could not be saved in this profile.')
  }
  const result: Settings = {}
  for (const [key, item] of Object.entries(value)) {
    if (excludedKeys.has(`custom_settings.${key}`)) continue
    if (!customKeys.has(key)) {
      throw new GenerationProfileSettingsError('A custom setting is not supported by saved profiles yet.')
    }
    if (item !== undefined) result[key] = copy(item)
  }
  return result
}

/**
 * Mode switches use the same technical UI field catalog as saved profiles.
 * H3 style and Director identity guidance are workspace-wide preferences, so
 * they deliberately stay outside each mode's working set.
 */
export function captureGenerationModeUiSettings(state: object): Settings {
  const source = state as Settings
  const captured: Settings = {}
  for (const key of generationModeUiKeys) {
    if (own(source, key) && source[key] !== undefined) captured[key] = copy(source[key])
  }
  return captured
}

/** Restore a complete mode UI envelope, defaulting fields absent from legacy snapshots. */
export function restoreGenerationModeUiSettings(snapshot: unknown, initialUi: object): Settings {
  const stored = snapshot && typeof snapshot === 'object' && !Array.isArray(snapshot)
    ? snapshot as Settings
    : {}
  const defaults = initialUi as Settings
  const restored: Settings = {}
  for (const key of generationModeUiKeys) {
    restored[key] = copy(own(stored, key) ? stored[key] : defaults[key])
  }
  return restored
}

/** One complete configuration snapshot; current-job content stays job-local. */
export function captureGenerationProfileSettings(params: object, state: object) {
  const captured: Settings = {}
  for (const [key, value] of Object.entries(params)) {
    if (excludedKeys.has(key)) continue
    if (!parameterKeys.has(key)) {
      throw new GenerationProfileSettingsError('A generation setting is not supported by saved profiles yet.')
    }
    if (value !== undefined) captured[key] = key === 'custom_settings'
      ? technicalCustomSettings(value) : copy(value)
  }
  const ui = state as Settings
  const uiSettings: Settings = {}
  for (const key of generationProfileUiKeys) {
    if (own(ui, key) && ui[key] !== undefined) uiSettings[key] = copy(ui[key])
  }
  return { profile_version: 2 as const, params: captured, ui_settings: uiSettings }
}

/** Restore presence as well as values: omitted optional settings are cleared. */
export function restoreGenerationProfileSettings(
  profile: { profile_version?: number; params: Settings; ui_settings?: Settings },
  currentParams: object,
  initialUi: object,
): { params: Settings; uiSettings: Settings } {
  if (own(profile, 'profile_version') && profile.profile_version !== 2) {
    throw new GenerationProfileSettingsError('This profile needs a newer version of Maestro.')
  }
  const params: Settings = { ...currentParams }
  const uiSettings: Settings = {}
  if (profile.profile_version === 2) {
    for (const key of parameterKeys) delete params[key]
    const defaults = initialUi as Settings
    for (const key of generationProfileUiKeys) {
      uiSettings[key] = copy(own(profile.ui_settings || {}, key)
        ? profile.ui_settings![key] : defaults[key])
    }
  }
  for (const [key, value] of Object.entries(profile.params)) {
    if (!parameterKeys.has(key)) throw new GenerationProfileSettingsError('This profile contains an unsupported setting.')
    params[key] = key === 'custom_settings' ? technicalCustomSettings(value) : copy(value)
  }
  // Creative custom fields belong to the current job, just like prompt text.
  const previousCustom = (currentParams as Settings).custom_settings
  if (previousCustom && typeof previousCustom === 'object' && !Array.isArray(previousCustom)) {
    for (const [key, value] of Object.entries(previousCustom)) {
      if (!excludedKeys.has(`custom_settings.${key}`)) continue
      params.custom_settings = { ...(params.custom_settings as Settings || {}), [key]: value }
    }
  }
  return { params, uiSettings }
}
