import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, AlertTriangle } from 'lucide-react'
import { fetchActiveDownloads, type ActiveDownload } from '../api/client'
import { useStore } from '../stores/useStore'
import { boundedBackoffDelay, DOWNLOAD_REFRESH_EVENT, POLL_INTERVAL_MS, useVisibilityPolling } from '../lib/useVisibilityPolling'

/**
 * DownloadStatusBanner — fixed-position overlay shown while
 * model files are being downloaded from HuggingFace (or other CDNs).
 *
 * Polls /api/v1/downloads/active every 2s during a transfer and every
 * 30s while idle. When downloads are active, shows a compact
 * banner with the current file's progress + a "stalled / retrying"
 * badge if the byte counter hasn't advanced in >30s.
 *
 * Polling is not gated on "is a job running" because
 * model downloads can fire from several paths in Maestro: job
 * submission, model selection, etc. Hidden tabs pause the loop entirely.
 *
 * Pairs with services/safe_download.py — that module patches HF
 * downloads to detect mid-stream stalls and recover automatically.
 * This banner just makes the recovery visible to the user instead
 * of the previous "frozen progress bar in console" UX.
 */
export function DownloadStatusBanner() {
  const [downloads, setDownloads] = useState<ActiveDownload[]>([])
  const [emptyPollAttempt, setEmptyPollAttempt] = useState(0)
  const mounted = useRef(false)
  const downloadsRef = useRef<ActiveDownload[]>([])
  const workActivity = useStore(s => (
    s.jobs.some(job => job.status === 'queued' || job.status === 'running')
    || s.llmLoading
    || s.isEnhancing
    || s.llmStatus?.loading === true
  ))

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const refreshDownloads = useCallback(async () => {
    try {
      const result = await fetchActiveDownloads()
      if (!mounted.current) return
      const wasActive = downloadsRef.current.length > 0
      downloadsRef.current = result.downloads
      setDownloads(result.downloads)
      if (result.downloads.length > 0 || wasActive) setEmptyPollAttempt(0)
      else setEmptyPollAttempt(attempt => Math.min(attempt + 1, 16))
    } catch {
      // Preserve an active banner through transient failures. The 2s latch
      // remains until a successful response authoritatively reports empty.
    }
  }, [])

  const refreshNow = useVisibilityPolling(
    refreshDownloads,
    downloads.length > 0
      ? POLL_INTERVAL_MS.downloadsActiveVisible
      : workActivity
        ? POLL_INTERVAL_MS.downloadsActiveVisible
        : boundedBackoffDelay(
            emptyPollAttempt,
            POLL_INTERVAL_MS.accessContextInitial,
            POLL_INTERVAL_MS.downloadsIdleVisible,
          ),
    { immediate: false },
  )

  useEffect(() => {
    refreshNow()
    const onDownloadRefresh = () => {
      setEmptyPollAttempt(0)
      refreshNow()
    }
    window.addEventListener(DOWNLOAD_REFRESH_EVENT, onDownloadRefresh)
    return () => window.removeEventListener(DOWNLOAD_REFRESH_EVENT, onDownloadRefresh)
  }, [refreshNow])

  if (downloads.length === 0) return null

  // Pick the highest-priority download to feature: any "stalled"
  // first, then the one with the most progress (closest to done).
  // Show a count when there are multiple.
  //
  // Threshold for "stalled" is 30s — covers genuinely-broken
  // connections without false-positiving on the brief lulls that
  // show up routinely in healthy CDN downloads (especially during
  // chunk boundary handoffs).
  // An interrupted download (byte count fell short of the expected size)
  // takes top priority — the file is likely truncated and the user should
  // know rather than hit a mysterious load failure next generation.
  const incomplete = downloads.find(d => d.status === 'incomplete')
  const stalled = downloads.find(d => d.seconds_since_progress > 30 && d.status !== 'incomplete')
  const featured = incomplete ?? stalled ?? downloads.reduce((best, cur) => {
    const bestPct = best.total_bytes ? best.downloaded_bytes / best.total_bytes : 0
    const curPct = cur.total_bytes ? cur.downloaded_bytes / cur.total_bytes : 0
    return curPct > bestPct ? cur : best
  }, downloads[0])
  const featuredLabel = safeDownloadLabel(featured.filename)
  const featuredIncomplete = featured.status === 'incomplete'
  const featuredStalled = !featuredIncomplete && featured.seconds_since_progress > 30
  const liveSummary = featuredIncomplete
    ? `Model download interrupted for ${featuredLabel}. Automatic recovery stopped; re-run the request that needed this file to retry.`
    : featuredStalled
      ? `Model download is slow for ${featuredLabel}. Maestro will retry automatically.`
      : `Model download in progress. ${downloads.length} ${downloads.length === 1 ? 'file' : 'files'}.`

  return (
    <div
      className="pointer-events-none fixed inset-0 z-40 flex max-h-[100vh] items-end justify-end supports-[height:100dvh]:max-h-[100dvh]"
      style={{
        paddingTop: 'max(1rem, env(safe-area-inset-top, 0px))',
        paddingRight: 'max(1rem, env(safe-area-inset-right, 0px))',
        paddingBottom: 'max(1rem, env(safe-area-inset-bottom, 0px))',
        paddingLeft: 'max(1rem, env(safe-area-inset-left, 0px))',
      }}
    >
      {/* Outer container is always solid bg-bg-secondary so text
          stays readable over images/videos in the main feed.
          Border color switches to amber on stall to draw the eye
          without sacrificing contrast. */}
      <div className={`pointer-events-auto max-h-full w-full max-w-md overflow-y-auto overscroll-contain rounded-lg border bg-bg-secondary shadow-2xl ${
        featuredIncomplete ? 'border-red-500/60' : featuredStalled ? 'border-amber-500/60' : 'border-border'
      }`}>
        <div
          className="sr-only"
          role={featuredIncomplete ? 'alert' : 'status'}
          aria-live={featuredIncomplete ? 'assertive' : 'polite'}
          aria-atomic="true"
        >
          {liveSummary}
        </div>
        {/* Interrupted download — red strip. The file is probably truncated;
            re-running the download/generation fetches the rest. */}
        {featuredIncomplete && (
          <div className="flex flex-wrap items-center gap-2 border-b border-red-500/30 bg-red-500/15 px-4 py-2">
            <AlertTriangle size={14} aria-hidden="true" className="shrink-0 text-red-400" />
            <div className="text-xs font-medium text-text-primary">
              Download interrupted — re-run the request that needed it
            </div>
          </div>
        )}
        {/* Optional amber accent strip on stall — semi-transparent
            tint over the solid backdrop, same pattern as OomRecoveryBanner. */}
        {featuredStalled && (
          <div className="flex flex-wrap items-center gap-2 border-b border-amber-500/30 bg-amber-500/15 px-4 py-2">
            <AlertTriangle size={14} aria-hidden="true" className="shrink-0 text-indicator-warning" />
            <div className="text-xs font-medium text-text-primary">
              Download is slow — Maestro will retry automatically
            </div>
          </div>
        )}

        <div className="px-4 py-3">
          <div className="flex items-start gap-2.5">
            {!featuredStalled && !featuredIncomplete && (
              <Download size={16} aria-hidden="true" className="mt-0.5 shrink-0 animate-pulse text-accent-blue motion-reduce:animate-none" />
            )}
            <div className="flex-1 min-w-0">
              {!featuredStalled && !featuredIncomplete && (
                <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                  <div className="text-xs font-medium text-text-primary">
                    Downloading model files
                  </div>
                  {downloads.length > 1 && (
                    <div className="text-[10px] text-text-muted shrink-0">
                      {downloads.length} files
                    </div>
                  )}
                </div>
              )}
              {featuredStalled && downloads.length > 1 && (
                <div className="text-[10px] text-text-muted text-right -mt-0.5 mb-0.5">
                  {downloads.length} files
                </div>
              )}
              <div className="break-all text-[10px] text-text-muted sm:truncate" title={featuredLabel}>
                {featuredLabel}
              </div>
              <DownloadProgressBar
                download={featured}
                interrupted={featuredIncomplete}
                label={featuredLabel}
                stalled={featuredStalled}
              />
              {featuredIncomplete && (
                <div className="mt-1.5 text-[11px] leading-snug text-text-secondary">
                  Automatic recovery stopped for this file. Re-run the request
                  that needed it to retry the download.
                </div>
              )}
              {featuredStalled && (
                <div className="text-[11px] text-text-secondary mt-1.5 leading-snug">
                  No progress for {Math.round(featured.seconds_since_progress)}s.
                  The download will resume from where it left off as soon as
                  the connection recovers — no action needed from you.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function safeDownloadLabel(filename: string): string {
  const basename = filename.split(/[\\/]/).pop()?.split(/[?#]/, 1)[0]?.trim()
  return basename ? basename.slice(0, 120) : 'model file'
}

function _formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function DownloadProgressBar({
  download,
  interrupted,
  label,
  stalled,
}: {
  download: ActiveDownload
  interrupted: boolean
  label: string
  stalled: boolean
}) {
  const pct = download.total_bytes
    ? Math.round((download.downloaded_bytes / download.total_bytes) * 100)
    : null

  return (
    <div className="mt-1.5">
      <div
        className="h-1 overflow-hidden rounded-full bg-bg-tertiary"
        role="progressbar"
        aria-label={`Download progress for ${label}`}
        aria-valuemin={pct !== null ? 0 : undefined}
        aria-valuemax={pct !== null ? 100 : undefined}
        aria-valuenow={pct ?? undefined}
        aria-valuetext={pct !== null
          ? `${pct} percent, ${_formatBytes(download.downloaded_bytes)} of ${_formatBytes(download.total_bytes!)}`
          : `${_formatBytes(download.downloaded_bytes)} downloaded`}
      >
        <div
          className={`h-full transition-all duration-500 motion-reduce:transition-none ${
            interrupted ? 'bg-red-400' : stalled ? 'bg-indicator-warning' : 'bg-accent-blue'
          }`}
          aria-hidden="true"
          style={{ width: pct !== null ? `${pct}%` : '15%' }}
        />
      </div>
      <div className="flex items-center justify-between mt-1">
        <span className="text-[10px] text-text-muted">
          {_formatBytes(download.downloaded_bytes)}
          {download.total_bytes !== null && (
            <> / {_formatBytes(download.total_bytes)}</>
          )}
        </span>
        {pct !== null && (
          <span className={`text-[10px] tabular-nums ${
            stalled ? 'text-indicator-warning' : 'text-text-secondary'
          }`}>
            {pct}%
          </span>
        )}
      </div>
    </div>
  )
}
