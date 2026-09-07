const H3_STUDIO_MODELS = new Set([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])

export const H3_FL2VA_MODELS = [
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
] as const

export const H3_REF2VA_MODEL = 'minimax_h3_ref2va'
export const H3_BASE_FL2VA_MODEL = 'minimax_h3'
export const DASIWA_FILENAME = 'dasiwa_ref2va_hybrid_v1_4step.safetensors'
const INVALID_H3_MODEL = 'invalid_h3_model_selection'

const H3_LORA_CONTRACTS: Record<string, {
  architectures: ReadonlyArray<'fl2va' | 'ref2va'>
  exclusive: boolean
  label: string
}> = {
  'dasiwa_ref2va_hybrid_v1_4step.safetensors': {
    architectures: ['ref2va'], exclusive: true, label: 'Dasiwa',
  },
  'h3_Better_NSFW_Motion_V1.safetensors': {
    architectures: ['ref2va'], exclusive: false, label: 'Better Motion',
  },
  'minimax_h3_turbo_sla_4step_comfyui_bf16.safetensors': {
    architectures: ['fl2va'], exclusive: true, label: 'Turbo SLA',
  },
}

export function isH3StudioModel(modelType: unknown): boolean {
  return typeof modelType === 'string' && H3_STUDIO_MODELS.has(modelType)
}

export function h3ArchitectureForModel(modelType: unknown): 'fl2va' | 'ref2va' | null {
  const value = typeof modelType === 'string' ? modelType : ''
  if (H3_FL2VA_MODELS.includes(value as typeof H3_FL2VA_MODELS[number])) return 'fl2va'
  if (value === H3_REF2VA_MODEL) return 'ref2va'
  return null
}

export function h3LoraContract(filename: string): {
  architectures: ReadonlyArray<'fl2va' | 'ref2va'>
  exclusive: boolean
  label: string
} {
  const key = loraFilename(filename)
  return Object.hasOwn(H3_LORA_CONTRACTS, key) ? H3_LORA_CONTRACTS[key] : {
    architectures: ['fl2va', 'ref2va'],
    exclusive: false,
    label: 'ordinary',
  }
}

export function defaultAdaptiveFl2vaModel(
  selectedModel: unknown,
  requestedFl2vaModel?: unknown,
): string {
  // Keep an invalid saved choice visible for repair rather than substituting
  // another checkpoint. Submission validates it before any model work.
  if (requestedFl2vaModel != null && requestedFl2vaModel !== '') {
    return typeof requestedFl2vaModel === 'string' ? requestedFl2vaModel : INVALID_H3_MODEL
  }
  const selected = typeof selectedModel === 'string' ? selectedModel : ''
  if (H3_FL2VA_MODELS.includes(selected as typeof H3_FL2VA_MODELS[number])) return selected
  return H3_BASE_FL2VA_MODEL
}

export function defaultAdaptiveRef2vaModel(requestedRef2vaModel?: unknown): string {
  if (requestedRef2vaModel != null && requestedRef2vaModel !== '') {
    return typeof requestedRef2vaModel === 'string' ? requestedRef2vaModel : INVALID_H3_MODEL
  }
  return H3_REF2VA_MODEL
}

export function h3AdaptivePairActive(
  modelType: unknown,
  adaptiveConditioning: unknown,
): boolean {
  return isH3StudioModel(modelType) && adaptiveConditioning !== false
}

export function h3AdaptivePickerModelCompatible(
  model: {
    model_type: string
    availability_status?: string
    execution_allowed?: boolean
  },
  options: {
    allowed: ReadonlySet<string>
    w4a8Available?: boolean | null
    selectedType?: string
  },
): boolean {
  if (!options.allowed.has(model.model_type)) return false
  if (options.selectedType && model.model_type === options.selectedType) return true
  if (
    model.availability_status === 'location_declaration_required'
    || model.availability_status === 'legal_blocked'
    || model.execution_allowed === false
  ) return false
  if (model.model_type === 'minimax_h3_w4a8_fl2va' && options.w4a8Available !== true) {
    return false
  }
  return true
}

type H3ModelSelections = {
  model_type?: unknown
  h3_adaptive_conditioning?: unknown
  h3_adaptive_fl2va_model?: unknown
  h3_adaptive_ref2va_model?: unknown
}

export function h3AdaptiveSelectionError(params: H3ModelSelections): string | null {
  if (!h3AdaptivePairActive(params.model_type, params.h3_adaptive_conditioning)) return null
  const fl = defaultAdaptiveFl2vaModel(params.model_type, params.h3_adaptive_fl2va_model)
  if (!H3_FL2VA_MODELS.includes(fl as typeof H3_FL2VA_MODELS[number])) {
    return 'Choose an available FL2VA model for text and frames.'
  }
  if (defaultAdaptiveRef2vaModel(params.h3_adaptive_ref2va_model) !== H3_REF2VA_MODEL) {
    return 'Choose an available Ref2VA model for reference media.'
  }
  return null
}

export function h3ActiveCheckpoints(params: H3ModelSelections): string[] {
  const modelType = typeof params.model_type === 'string'
    ? params.model_type : (params.model_type == null ? '' : INVALID_H3_MODEL)
  if (!h3AdaptivePairActive(modelType, params.h3_adaptive_conditioning)) {
    return modelType ? [modelType] : []
  }
  return [
    defaultAdaptiveFl2vaModel(modelType, params.h3_adaptive_fl2va_model),
    defaultAdaptiveRef2vaModel(params.h3_adaptive_ref2va_model),
  ]
}

export function loraFilename(value: unknown): string {
  const raw = String(value || '')
  const parts = raw.split(/[/\\]/)
  return parts[parts.length - 1] || raw
}

function loraPairs(namesValue: unknown, multipliers: unknown, split: boolean): [string, string][] {
  const names = namesValue == null || namesValue === ''
    ? []
    : (!split && typeof namesValue === 'string' ? [namesValue] : namesValue)
  if (!Array.isArray(names) || names.some(name => typeof name !== 'string' || !name.trim())) {
    throw new Error('LoRA selections require non-empty asset names.')
  }
  if (new Set(names).size !== names.length) throw new Error('LoRA selections must not contain duplicates.')
  if (multipliers != null && typeof multipliers !== 'string') throw new Error('LoRA weights must be text.')
  const parts = typeof multipliers === 'string' && multipliers.trim() ? multipliers.trim().split(/\s+/) : []
  if (parts.length > names.length) throw new Error('LoRA weights exceed the selected asset count.')
  const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/
  if (parts.some(part => part.split(';').some(value => !decimal.test(value) || !Number.isFinite(Number(value))))) {
    throw new Error('LoRA weights must be finite numbers.')
  }
  return names.map((name, index) => [name, parts[index] ?? '1.00'])
}

export function h3LorasForArchitecture(params: {
  activated_loras?: unknown
  loras_multipliers?: unknown
  h3_fl2va_loras?: unknown
  h3_fl2va_loras_multipliers?: unknown
  h3_ref2va_loras?: unknown
  h3_ref2va_loras_multipliers?: unknown
}, architecture: 'fl2va' | 'ref2va'): { loras: string[]; multipliers: string } {
  for (const side of ['fl2va', 'ref2va'] as const) {
    const names = params[`h3_${side}_loras`]
    if (names != null && !Array.isArray(names)) throw new Error('Architecture LoRA selections must be lists.')
  }
  const selected = params[`h3_${architecture}_loras`]
  const explicit = selected != null
  let pairs = loraPairs(
    explicit ? selected : params.activated_loras,
    explicit ? params[`h3_${architecture}_loras_multipliers`] : params.loras_multipliers,
    explicit,
  )
  const compatible = ([name]: [string, string]) => h3LoraContract(name).architectures.includes(architecture)
  if (explicit && pairs.some(pair => !compatible(pair))) {
    throw new Error('LoRA selection is incompatible with its architecture.')
  }
  if (!explicit) pairs = pairs.filter(compatible)
  return { loras: pairs.map(([name]) => name), multipliers: pairs.map(([, weight]) => weight).join(' ') }
}

export function parseLoraMultiplierMap(
  names: string[],
  multipliers: string,
  phases: number,
): Record<string, number[]> {
  const count = Math.max(1, phases)
  const map: Record<string, number[]> = Object.create(null)
  loraPairs(names, multipliers, true).forEach(([name, spec]) => {
    const values = spec.split(';').map(Number)
    map[name] = Array.from({ length: Math.max(count, values.length) }, (_, phase) => {
      const value = values[phase] ?? values[values.length - 1] ?? 1
      return value
    })
  })
  return map
}

export function serializeLoraMultipliers(
  names: string[],
  weights: Record<string, number[]>,
  phases: number,
): string {
  const count = Math.max(1, phases)
  return names.map(name => {
    const stored = weights[name] || [1]
    return Array.from(
      { length: Math.max(count, stored.length) },
      (_, phase) => {
        const value = stored[phase] ?? stored[stored.length - 1] ?? 1
        if (!Number.isFinite(value)) throw new Error('LoRA weights must be finite numbers.')
        return String(value)
      },
    ).join(';')
  }).join(' ')
}

export function h3LoraBlockReason(
  filename: string,
  architecture: 'fl2va' | 'ref2va',
  activated: string[],
): string | null {
  const contract = h3LoraContract(filename)
  if (!contract.architectures.includes(architecture)) {
    return `${contract.label} is not compatible with ${architecture}.`
  }
  if (activated.includes(filename)) return null
  const exclusive = activated.find(name => h3LoraContract(name).exclusive)
  if (exclusive) {
    return `${h3LoraContract(exclusive).label} cannot be stacked with another LoRA or accelerator`
  }
  if (contract.exclusive && activated.length > 0) {
    return `${contract.label} cannot be stacked with another LoRA or accelerator`
  }
  return null
}

export function filterLorasForArchitecture(
  filenames: string[],
  architecture: 'fl2va' | 'ref2va',
): string[] {
  return filenames.filter(name => h3LoraContract(name).architectures.includes(architecture))
}

/**
 * Keep an H3 segment ceiling only when the user explicitly locked it.
 * Presence is the server contract: omitting the field enables profile-owned
 * segment pressure, while a supplied value is an exact manual ceiling.
 */
export function applyH3SegmentCeilingPolicy<T extends Record<string, unknown>>(
  params: T,
  locked: boolean,
): T {
  if (isH3StudioModel(params.model_type) && !locked) {
    delete params.sliding_window_size
  }
  return params
}

export function hasManualH3SegmentCeiling(
  params: Record<string, unknown>,
  longform: Record<string, unknown> | null | undefined,
): boolean {
  if (!isH3StudioModel(params.model_type)) return false
  if (longform) return longform.manual_segment_ceiling === true
  return params.sliding_window_size !== undefined
    && params.sliding_window_size !== null
    && params.sliding_window_size !== ''
}
