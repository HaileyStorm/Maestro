import type { Yue2LoraCheckpoint, Yue2LoraGroup } from '../../api/client'

export type Yue2CheckpointSelection = Pick<Yue2LoraCheckpoint, 'id' | 'sha256'>
export type Yue2CheckpointResolution = {
  checkpoint: Yue2LoraCheckpoint | null
  error: string | null
}

export const USE_PREFERRED_YUE2_CHECKPOINT = '__preferred_yue2_checkpoint__'

export function yue2CheckpointSelectionKey(checkpoint: Yue2CheckpointSelection): string {
  return JSON.stringify([checkpoint.id, checkpoint.sha256])
}

export type Yue2CotMode = 'full' | 'melody' | 'off'

export type Yue2GenerationSettings = {
  cot: Yue2CotMode
  cfgScale: number
  odeSteps: number
  maxTokens: number
}

export type Yue2ComposeDraft = {
  workspace: string
  description: string
  language: string
  instrumental: boolean
  style: string
  lyrics: string
  abc: string
}

export function sameYue2ComposeDraft(left: Yue2ComposeDraft, right: Yue2ComposeDraft): boolean {
  return left.workspace === right.workspace
    && left.description === right.description
    && left.language === right.language
    && left.instrumental === right.instrumental
    && left.style === right.style
    && left.lyrics === right.lyrics
    && left.abc === right.abc
}

export function reviewedAbcForContinuation(original: string, current: string): string | undefined {
  return current === original ? undefined : current
}

const DEFAULTS: Yue2GenerationSettings = {
  cot: 'full',
  cfgScale: 1,
  odeSteps: 100,
  maxTokens: 9000,
}

const labels = {
  cot: 'score-planning mode',
  cfg_scale: 'guidance setting',
  ode_steps: 'synthesis-step setting',
  max_tokens: 'duration ceiling',
} as const

type GenerationKey = keyof typeof labels

function explicitValue(groups: Yue2LoraGroup[], key: GenerationKey): unknown {
  const values = groups
    .map(group => group.generation?.[key])
    .filter(value => value !== undefined)
  const unique = [...new Set(values.map(value => JSON.stringify(value)))]
  if (unique.length > 1) {
    throw new Error(`The selected LoRAs require different ${labels[key]}s. Choose a compatible stack.`)
  }
  return values[0]
}

function numberSetting(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/** Merge compatible per-LoRA defaults onto this workspace's YuE2 defaults. */
export function resolveYue2GenerationSettings(groups: Yue2LoraGroup[]): Yue2GenerationSettings {
  const cotValue = explicitValue(groups, 'cot')
  const cot = cotValue === 'full' || cotValue === 'melody' || cotValue === 'off'
    ? cotValue
    : DEFAULTS.cot
  return {
    cot,
    cfgScale: numberSetting(explicitValue(groups, 'cfg_scale'), DEFAULTS.cfgScale),
    odeSteps: numberSetting(explicitValue(groups, 'ode_steps'), DEFAULTS.odeSteps),
    maxTokens: numberSetting(explicitValue(groups, 'max_tokens'), DEFAULTS.maxTokens),
  }
}

export function preferredYue2Checkpoint(group: Yue2LoraGroup) {
  const checkpoints = Array.isArray(group.checkpoints) ? group.checkpoints : []
  return checkpoints.find(checkpoint => checkpoint.step === group.preferredStep)
    || checkpoints.at(-1)
}

/** Resolve a LoRA to the exact installed ID and checksum, never a nearby fallback. */
export function resolveYue2CheckpointSelection(
  group: Yue2LoraGroup,
  selection?: Yue2CheckpointSelection,
): Yue2CheckpointResolution {
  const checkpoints = Array.isArray(group.checkpoints) ? group.checkpoints : []
  const checkpoint = selection
    ? checkpoints.find(candidate => candidate.id === selection.id && candidate.sha256 === selection.sha256)
    : preferredYue2Checkpoint(group)

  if (!checkpoint) {
    return {
      checkpoint: null,
      error: selection
        ? `The selected checkpoint for ${group.name} is no longer installed or its checksum changed. Choose an available checkpoint before generating.`
        : `No installed checkpoint is available for ${group.name}. Refresh YuE2 status before generating.`,
    }
  }

  if (typeof checkpoint.id !== 'string' || !checkpoint.id.trim()
    || typeof checkpoint.sha256 !== 'string' || !/^[a-f\d]{64}$/i.test(checkpoint.sha256)) {
    return {
      checkpoint: null,
      error: `The checkpoint for ${group.name} is missing a valid installed ID or SHA-256. Refresh YuE2 status before generating.`,
    }
  }

  return { checkpoint, error: null }
}
