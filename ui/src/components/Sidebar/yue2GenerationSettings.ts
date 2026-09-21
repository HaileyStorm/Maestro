import type { Yue2LoraGroup } from '../../api/client'

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
  return group.checkpoints.find(checkpoint => checkpoint.step === group.preferredStep)
    || group.checkpoints.at(-1)
}
