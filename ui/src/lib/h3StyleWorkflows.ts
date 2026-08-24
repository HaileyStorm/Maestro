import type { H3StyleWorkflowCatalog, H3StyleWorkflowCatalogStyle } from '../api/client'

export const H3_STYLE_WORKFLOW_PREF_KEY = 'maestro:h3-prepared-style'
export const H3_STYLE_PREFIX_MIGRATION_KEY = 'maestro:h3-style-prefix-migration-v1'

// One bounded migration for the exact prefix emitted by the retired client
// Apply button. The authored bytes after its two-newline boundary are kept
// verbatim; broader H3-looking prose is never rewritten.
const LEGACY_H3_STYLE_PREFIX = /^H3 prepared style \[[^\]\r\n]{1,120}\]:[^\r\n]{0,500}(?:\r?\n){2}/

export function stripLegacyH3StylePrefix(prompt: string): string {
  return prompt.replace(LEGACY_H3_STYLE_PREFIX, '')
}

export function h3StyleWorkflowSupportsModel(
  catalog: H3StyleWorkflowCatalog | null | undefined,
  modelType: string | null | undefined,
): boolean {
  return Boolean(modelType && catalog?.supported_model_types.includes(modelType))
}

export function h3StyleWorkflowSelectionIsCurrent(
  catalog: H3StyleWorkflowCatalog | null | undefined,
  selection: string,
): boolean {
  return !selection || Boolean(catalog?.styles.some(style => style.id === selection))
}

export function resolveH3StyleWorkflowRequest(
  catalog: H3StyleWorkflowCatalog | null | undefined,
  modelType: string | null | undefined,
  selection: string,
): string | undefined {
  if (!selection || !h3StyleWorkflowSupportsModel(catalog, modelType)) return undefined
  return h3StyleWorkflowSelectionIsCurrent(catalog, selection) ? selection : undefined
}

export function captureH3StyleWorkflowRequest(
  catalog: H3StyleWorkflowCatalog | null | undefined,
  modelType: string,
  selection: string,
): { video_model: string; h3_style_workflow?: string } {
  const workflow = resolveH3StyleWorkflowRequest(catalog, modelType, selection)
  return {
    video_model: modelType,
    ...(workflow ? { h3_style_workflow: workflow } : {}),
  }
}

export function h3StyleWorkflowCatalogStateLabel(catalog: H3StyleWorkflowCatalog): string {
  switch (catalog.update_status) {
    case 'updated': return 'Updated official catalog'
    case 'cached': return 'Cached official catalog'
    case 'bundled_fallback': return 'Bundled fallback catalog'
    case 'offline_fallback': return 'Offline fallback catalog'
  }
}

export type H3StyleWorkflowSwatch = 'paper' | 'dimensional' | 'polished' | 'rhythmic' | 'drawn'

const KNOWN_STYLE_SWATCHES: Readonly<Record<string, H3StyleWorkflowSwatch>> = {
  'papercraft-stop-motion-explainer': 'paper',
  'paper-collage-explainer-generator': 'paper',
  '3d-animation-short-generator': 'dimensional',
  'co-op-game-intro-generator': 'dimensional',
  'minimalist-product-ad-generator': 'polished',
  'brand-promo-video-generator': 'polished',
  'music-video-subtitle-generator': 'rhythmic',
  'handdrawn-live-video-generator': 'drawn',
}

function stableTextHash(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function h3StyleWorkflowSwatch(id: string): H3StyleWorkflowSwatch {
  const known = KNOWN_STYLE_SWATCHES[id]
  if (known) return known
  const fallback: readonly H3StyleWorkflowSwatch[] = ['paper', 'dimensional', 'polished', 'rhythmic', 'drawn']
  return fallback[stableTextHash(id) % fallback.length]
}

function greatestCommonDivisor(left: number, right: number): number {
  let a = Math.abs(left)
  let b = Math.abs(right)
  while (b) [a, b] = [b, a % b]
  return a
}

/**
 * Pick the next server-owned style without touching prompt text. The revision
 * fixes both the first pick and a full-cycle stride, so repeated click events
 * feel varied while remaining reproducible for the exact catalog revision.
 */
export function nextH3StyleWorkflowSurprise(
  styles: readonly Pick<H3StyleWorkflowCatalogStyle, 'id'>[],
  currentId: string,
  revision: string,
): string {
  if (styles.length === 0) return ''
  if (styles.length === 1) return styles[0].id

  const revisionHash = stableTextHash(revision)
  const start = revisionHash % styles.length
  let stride = 1 + ((revisionHash >>> 8) % (styles.length - 1))
  while (greatestCommonDivisor(stride, styles.length) !== 1) {
    stride = (stride % (styles.length - 1)) + 1
  }

  const currentIndex = styles.findIndex(style => style.id === currentId)
  return styles[currentIndex < 0 ? start : (currentIndex + stride) % styles.length].id
}
