import { useCallback, useEffect, useRef } from 'react'

export const DOWNLOAD_REFRESH_EVENT = 'maestro:downloads-refresh'

export const POLL_INTERVAL_MS = {
  hardwareVisible: 5_000,
  queueActiveVisible: 15_000,
  queueIdleVisible: 60_000,
  downloadsActiveVisible: 2_000,
  downloadsIdleVisible: 30_000,
  llmActiveVisible: 2_000,
  llmIdleVisible: 60_000,
  researchVisible: 5_000,
  referencesVisible: 3_000,
  accessContextInitial: 2_500,
  accessContextMaximum: 30_000,
} as const

export function boundedBackoffDelay(
  attempt: number,
  initialMs = POLL_INTERVAL_MS.accessContextInitial,
  maximumMs = POLL_INTERVAL_MS.accessContextMaximum,
): number {
  const exponent = Math.max(0, Math.min(16, Math.floor(attempt)))
  return Math.min(maximumMs, initialMs * (2 ** exponent))
}

type PollCallback = (signal: AbortSignal) => void | Promise<void>

type VisibilityPollingOptions = {
  enabled?: boolean
  immediate?: boolean
}

/**
 * Runs one non-overlapping poll loop only while the page is visible.
 * Returning to the page always refreshes immediately, even when the first
 * mount deliberately waits because bootstrap already fetched the same state.
 */
export function useVisibilityPolling(
  callback: PollCallback,
  intervalMs: number,
  { enabled = true, immediate = true }: VisibilityPollingOptions = {},
): () => void {
  const callbackRef = useRef(callback)
  const requestImmediateRef = useRef<() => void>(() => {})
  const requestImmediate = useCallback(() => requestImmediateRef.current(), [])

  useEffect(() => {
    callbackRef.current = callback
  }, [callback])

  useEffect(() => {
    if (!enabled) {
      requestImmediateRef.current = () => {}
      return
    }

    let cancelled = false
    let running = false
    let pendingImmediate = false
    let timeout: number | undefined
    let controller: AbortController | null = null

    const clearTimer = () => {
      if (timeout !== undefined) {
        window.clearTimeout(timeout)
        timeout = undefined
      }
    }

    const schedule = () => {
      clearTimer()
      if (cancelled || document.hidden) return
      timeout = window.setTimeout(() => { void run() }, intervalMs)
    }

    const run = async () => {
      if (cancelled || running || document.hidden) return
      running = true
      controller = new AbortController()
      try {
        await callbackRef.current(controller.signal)
      } catch {
        // Polling consumers retain their existing visible-error policy.
      } finally {
        controller = null
        running = false
        if (pendingImmediate && !cancelled && !document.hidden) {
          pendingImmediate = false
          void run()
        } else {
          schedule()
        }
      }
    }

    const refreshImmediately = () => {
      clearTimer()
      if (cancelled || document.hidden) return
      if (running) {
        pendingImmediate = true
      } else {
        void run()
      }
    }

    const onVisibilityChange = () => {
      clearTimer()
      if (document.hidden) {
        pendingImmediate = false
        controller?.abort()
        return
      }
      refreshImmediately()
    }

    requestImmediateRef.current = refreshImmediately
    document.addEventListener('visibilitychange', onVisibilityChange)
    if (!document.hidden) {
      if (immediate) void run()
      else schedule()
    }

    return () => {
      cancelled = true
      pendingImmediate = false
      clearTimer()
      controller?.abort()
      if (requestImmediateRef.current === refreshImmediately) {
        requestImmediateRef.current = () => {}
      }
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [enabled, immediate, intervalMs])

  return requestImmediate
}
