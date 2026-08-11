import type { H3StyleWorkflowCatalog } from '../api/client'

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
