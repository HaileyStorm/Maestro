import { useEffect, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { fetchPreflight, type PreflightCheck } from '../api/client'

/**
 * PreflightBanner — one-time environment sanity check shown at the top
 * of the app on first load. Surfaces the three failures that otherwise
 * only appear as a cryptic traceback mid-generation: ffmpeg missing, no
 * CUDA GPU, and low disk on the output drive.
 *
 * Renders nothing when everything is fine. Dismissible; stays dismissed
 * for the session (sessionStorage) so it doesn't nag after the user has
 * acknowledged it, but returns next launch if the problem persists.
 */
export function PreflightBanner() {
  const [checks, setChecks] = useState<PreflightCheck[]>([])
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem('maestro_preflight_dismissed') === '1'
  )

  useEffect(() => {
    let cancelled = false
    fetchPreflight()
      .then(r => { if (!cancelled) setChecks(r.checks || []) })
      .catch(() => { /* older backend / transient — say nothing */ })
    return () => { cancelled = true }
  }, [])

  if (dismissed || checks.length === 0) return null

  const hasError = checks.some(c => c.level === 'error')

  return (
    <div
      className="pointer-events-none fixed inset-0 z-50 flex max-h-[100vh] items-start supports-[height:100dvh]:max-h-[100dvh]"
      style={{
        paddingTop: 'env(safe-area-inset-top, 0px)',
        paddingRight: 'env(safe-area-inset-right, 0px)',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        paddingLeft: 'env(safe-area-inset-left, 0px)',
      }}
    >
      <div
        role={hasError ? 'alert' : 'status'}
        aria-live={hasError ? 'assertive' : 'polite'}
        aria-atomic="true"
        aria-label={hasError ? 'Environment preflight errors' : 'Environment preflight warnings'}
        className={`pointer-events-auto flex max-h-full w-full items-start gap-2.5 overflow-y-auto overscroll-contain border-b px-4 py-2 backdrop-blur-sm ${
          hasError
            ? 'bg-red-500/20 border-red-500/40'
            : 'bg-amber-500/20 border-amber-500/40'
        }`}
      >
        <AlertTriangle
          size={15}
          aria-hidden="true"
          className={`mt-3.5 shrink-0 ${hasError ? 'text-chip-red' : 'text-indicator-warning'}`}
        />
        <div className="min-w-0 flex-1 space-y-0.5 py-3">
          {checks.map(c => (
            <div key={c.id} className="break-words text-[11px] leading-snug text-text-primary">
              {c.message}
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => {
            sessionStorage.setItem('maestro_preflight_dismissed', '1')
            setDismissed(true)
          }}
          className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary motion-reduce:transition-none"
          aria-label="Dismiss environment preflight notice"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
