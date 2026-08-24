export type SceneKitReferenceKind = 'character' | 'location'

export interface SceneKitChoice {
  key: string
  assetId: string
  variantId: string
  assetName: string
  variantLabel: string
  kind: SceneKitReferenceKind
  outputCount: number
  outputIds: string[]
}

export function sceneKitChoiceKey(assetId: string, variantId: string) {
  return `${assetId}\u001f${variantId}`
}

export function toggleSceneKitChoice(
  current: SceneKitChoice[],
  choice: SceneKitChoice,
): SceneKitChoice[] {
  const existing = current.findIndex(item => item.key === choice.key)
  if (existing < 0) return [...current, choice]
  return current.filter((_, index) => index !== existing)
}

export function sceneKitOutputCount(choices: SceneKitChoice[]) {
  return choices.reduce((count, choice) => count + choice.outputCount, 0)
}

export function groupSceneKitChoices(choices: SceneKitChoice[]) {
  return {
    characters: choices.filter(choice => choice.kind === 'character'),
    locations: choices.filter(choice => choice.kind === 'location'),
  }
}
