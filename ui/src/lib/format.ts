/** Shared byte formatting — one rounding rule everywhere a size shows
 *  (LoRA cards, CivitAI detail pane, future storage views). */
export function formatBytes(bytes: number): string {
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(0)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

/** Compact age for dense list rows: "today", "3d", "2w", "5mo", "1y".
 *  Returns '' for null/invalid input so call sites can render-and-forget. */
export function formatAge(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const days = Math.max(0, Math.floor((Date.now() - t) / 86400000))
  if (days < 1) return 'today'
  if (days < 7) return `${days}d`
  if (days < 30) return `${Math.floor(days / 7)}w`
  // Clamp at 11mo: days 360-364 would otherwise floor to "12mo" while
  // still failing the < 365 year cutoff.
  if (days < 365) return `${Math.min(11, Math.floor(days / 30))}mo`
  return `${Math.floor(days / 365)}y`
}

function formatRoundedDuration(totalSeconds: number, fractionDigits: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds - hours * 3600) / 60)
  const seconds = totalSeconds - hours * 3600 - minutes * 60
  const parts: string[] = []
  if (hours) parts.push(`${hours}h`)
  if (minutes) parts.push(`${minutes}m`)
  if (seconds || parts.length === 0) {
    const bounded = String(Number(seconds.toFixed(fractionDigits)))
    parts.push(`${bounded}s`)
  }
  return parts.join(' ')
}

/** Frame-derived media duration for Generate surfaces. This is display-only:
 * integer frame geometry remains authoritative for requests and recovery. */
export function formatMediaDuration(
  seconds: number | null | undefined,
  maximumFractionDigits = 2,
  fallback = '—',
): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return fallback
  const fractionDigits = Number.isFinite(maximumFractionDigits)
    ? Math.min(3, Math.max(0, Math.trunc(maximumFractionDigits)))
    : 2
  const scale = 10 ** fractionDigits
  const rounded = Math.round((seconds + Number.EPSILON) * scale) / scale
  return formatRoundedDuration(rounded, fractionDigits)
}

/** Whole-second formatter for explicitly approximate runtime and ETA values.
 * Round before choosing units so rollover edges never render as 60s/1m 60s. */
export function formatApproximateDuration(
  seconds: number | null | undefined,
  fallback = 'unknown',
): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return fallback
  return formatRoundedDuration(Math.max(1, Math.round(seconds)), 0)
}
