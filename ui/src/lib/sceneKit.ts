import type { ProjectAssetGenerateReference } from '../types'

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

export function projectAssetGenerateOutputsAreImages(mediaTypes: string[]) {
  return mediaTypes.length > 0 && mediaTypes.every(mediaType => mediaType.startsWith('image/'))
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

export function projectAssetGenerateReferenceFromChoice(
  choice: Pick<SceneKitChoice, 'assetId' | 'variantId' | 'outputIds'>,
): ProjectAssetGenerateReference {
  return {
    asset_id: choice.assetId,
    variant_id: choice.variantId,
    output_ids: [...choice.outputIds],
  }
}

export function sameProjectAssetGenerateReference(
  left: ProjectAssetGenerateReference,
  right: ProjectAssetGenerateReference,
) {
  return left.asset_id === right.asset_id
    && left.variant_id === right.variant_id
    && left.output_ids.length === right.output_ids.length
    && left.output_ids.every((outputId, index) => outputId === right.output_ids[index])
}

export function sameOrderedProjectAssetGenerateReferences(
  left: ProjectAssetGenerateReference[],
  right: ProjectAssetGenerateReference[],
) {
  return left.length === right.length && left.every((reference, index) => (
    sameProjectAssetGenerateReference(reference, right[index])
  ))
}

export function reconcileProjectAssetGenerateReferences(
  staged: ProjectAssetGenerateReference[],
  available: ProjectAssetGenerateReference[],
): ProjectAssetGenerateReference[] {
  return staged.filter(reference => available.some(candidate => (
    sameProjectAssetGenerateReference(reference, candidate)
  )))
}
