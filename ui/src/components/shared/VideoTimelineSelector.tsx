import { useState, useRef, useEffect, useCallback } from 'react'

const TIME_STEP_SECONDS = 0.1
const PAGE_STEP_SECONDS = 1
const VALUE_EPSILON = 0.0001

type Handle = 'start' | 'end'

interface TimelineValues {
  start: number
  end: number
}

interface ActivePointer {
  element: HTMLDivElement
  handle: Handle
  pointerId: number
  timeOffset: number
}

interface Props {
  videoUrl: string
  duration: number
  startTime: number
  endTime: number
  onStartChange: (t: number) => void
  onEndChange: (t: number) => void
  height?: number
}

/**
 * Visual timeline selector with thumbnail filmstrip and draggable handles.
 * Video scrubs to the handle position as you drag.
 */
export function VideoTimelineSelector({
  videoUrl, duration, startTime, endTime,
  onStartChange, onEndChange, height = 56,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const [thumbnails, setThumbnails] = useState<string[]>([])
  const [dragging, setDragging] = useState<Handle | null>(null)
  const [previewTime, setPreviewTime] = useState<number | null>(null)
  const activePointerRef = useRef<ActivePointer | null>(null)
  const thumbCount = 10

  const normalizedDuration = Number.isFinite(duration) && duration > 0 ? duration : 0
  const rangeIsValid = Number.isFinite(startTime)
    && Number.isFinite(endTime)
    && startTime >= 0
    && endTime >= startTime
    && endTime <= normalizedDuration
    && (normalizedDuration < TIME_STEP_SECONDS
      || startTime <= endTime - TIME_STEP_SECONDS + VALUE_EPSILON)
  const normalizedValues = normalizeTimelineValues(startTime, endTime, normalizedDuration)
  const startBounds = getHandleBounds('start', normalizedValues, normalizedDuration)
  const endBounds = getHandleBounds('end', normalizedValues, normalizedDuration)
  const canAdjust = rangeIsValid
    && (isAdjustable(startBounds) || isAdjustable(endBounds))
  const valuesRef = useRef<TimelineValues>(normalizedValues)
  const externalValuesRef = useRef({ duration, endTime, startTime })
  const externalValues = externalValuesRef.current
  if (externalValues.duration !== duration
    || externalValues.endTime !== endTime
    || externalValues.startTime !== startTime) {
    externalValuesRef.current = { duration, endTime, startTime }
    valuesRef.current = normalizedValues
  }

  // Generate thumbnail filmstrip
  useEffect(() => {
    if (!videoUrl || normalizedDuration <= 0) return
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    const video = document.createElement('video')
    video.crossOrigin = 'anonymous'
    video.muted = true
    video.src = videoUrl

    const frames: string[] = []
    let idx = 0

    video.onloadeddata = () => {
      canvas.width = 96
      canvas.height = Math.round(96 * (video.videoHeight / video.videoWidth))
      captureNext()
    }

    function captureNext() {
      if (idx >= thumbCount) {
        setThumbnails(frames)
        return
      }
      const t = (idx / (thumbCount - 1)) * normalizedDuration
      video.currentTime = t
      video.onseeked = () => {
        ctx!.drawImage(video, 0, 0, canvas.width, canvas.height)
        frames.push(canvas.toDataURL('image/jpeg', 0.6))
        idx++
        captureNext()
      }
    }
  }, [videoUrl, normalizedDuration])

  // Scrub video preview to handle position
  useEffect(() => {
    const v = videoRef.current
    if (v && previewTime !== null && isFinite(previewTime)) {
      v.currentTime = previewTime
    }
  }, [previewTime])

  const getTimeFromX = useCallback((clientX: number, clampToTrack = true): number | null => {
    const track = trackRef.current
    if (!track || !canAdjust || !Number.isFinite(clientX)) return null
    const rect = track.getBoundingClientRect()
    if (!Number.isFinite(rect.left) || !Number.isFinite(rect.width) || rect.width <= 0) return null
    const rawPct = (clientX - rect.left) / rect.width
    const pct = clampToTrack ? Math.max(0, Math.min(1, rawPct)) : rawPct
    if (pct === 1) return normalizedDuration
    return roundToStep(pct * normalizedDuration)
  }, [canAdjust, normalizedDuration])

  const commitValue = useCallback((handle: Handle, candidate: number) => {
    if (!canAdjust || !Number.isFinite(candidate)) return
    const current = valuesRef.current
    const bounds = getHandleBounds(handle, current, normalizedDuration)
    const next = snapWithinBounds(candidate, bounds.min, bounds.max)
    setPreviewTime(next)
    if (Math.abs(next - current[handle]) <= VALUE_EPSILON) return

    valuesRef.current = { ...current, [handle]: next }
    if (handle === 'start') onStartChange(next)
    else onEndChange(next)
  }, [canAdjust, normalizedDuration, onStartChange, onEndChange])

  const handlePointerDown = useCallback((targetHandle: Handle, e: React.PointerEvent<HTMLDivElement>) => {
    if (!canAdjust || activePointerRef.current) return
    const values = valuesRef.current
    if (!isAdjustable(getHandleBounds(targetHandle, values, normalizedDuration))) return
    const t = getTimeFromX(e.clientX)
    if (t === null) return
    e.preventDefault()
    e.stopPropagation()
    let handle = nearestHandle(t, values, targetHandle)
    if (!isAdjustable(getHandleBounds(handle, values, normalizedDuration))) {
      handle = targetHandle
    }
    const element = e.currentTarget
    try {
      element.setPointerCapture(e.pointerId)
    } catch {
      return
    }
    activePointerRef.current = {
      element,
      handle,
      pointerId: e.pointerId,
      timeOffset: t - values[handle],
    }
    setDragging(handle)
    setPreviewTime(values[handle])
  }, [canAdjust, getTimeFromX, normalizedDuration])

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const active = activePointerRef.current
    if (!active || active.pointerId !== e.pointerId) return
    const t = getTimeFromX(e.clientX, false)
    if (t === null) return
    e.preventDefault()
    commitValue(active.handle, t - active.timeOffset)
  }, [commitValue, getTimeFromX])

  const finishPointer = useCallback((e: React.PointerEvent<HTMLDivElement>, releaseCapture: boolean) => {
    const active = activePointerRef.current
    if (!active || active.pointerId !== e.pointerId) return
    activePointerRef.current = null
    setDragging(null)
    setPreviewTime(null)
    if (releaseCapture) {
      try {
        if (active.element.hasPointerCapture(e.pointerId)) active.element.releasePointerCapture(e.pointerId)
      } catch {
        // The browser may already have released capture during cancellation.
      }
    }
  }, [])

  const handleKeyDown = useCallback((handle: Handle, e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!canAdjust) return
    const current = valuesRef.current
    const bounds = getHandleBounds(handle, current, normalizedDuration)
    if (!isAdjustable(bounds)) return
    let next: number | null = null
    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowDown':
        next = current[handle] - TIME_STEP_SECONDS
        break
      case 'ArrowRight':
      case 'ArrowUp':
        next = current[handle] + TIME_STEP_SECONDS
        break
      case 'PageDown':
        next = current[handle] - PAGE_STEP_SECONDS
        break
      case 'PageUp':
        next = current[handle] + PAGE_STEP_SECONDS
        break
      case 'Home':
        next = bounds.min
        break
      case 'End':
        next = bounds.max
        break
      default:
        return
    }
    e.preventDefault()
    e.stopPropagation()
    commitValue(handle, next)
  }, [canAdjust, commitValue, normalizedDuration])

  const startPct = normalizedDuration > 0 ? (normalizedValues.start / normalizedDuration) * 100 : 0
  const endPct = normalizedDuration > 0 ? (normalizedValues.end / normalizedDuration) * 100 : 100

  const handleProps = (handle: Handle) => {
    const bounds = handle === 'start' ? startBounds : endBounds
    const value = normalizedValues[handle]
    const pct = handle === 'start' ? startPct : endPct
    const label = handle === 'start' ? 'Start time' : 'End time'
    const handleCanAdjust = canAdjust && isAdjustable(bounds)
    return {
      'aria-disabled': !handleCanAdjust,
      'aria-invalid': !rangeIsValid || undefined,
      'aria-label': label,
      'aria-orientation': 'horizontal' as const,
      'aria-valuemax': bounds.max,
      'aria-valuemin': bounds.min,
      'aria-valuenow': value,
      'aria-valuetext': formatAriaTime(value),
      'data-timeline-handle': handle,
      role: 'slider',
      tabIndex: handleCanAdjust ? 0 : -1,
      className: `absolute top-1/2 ${handleCanAdjust ? 'z-20' : 'z-10'} flex h-full min-h-11 w-11 min-w-11 -translate-y-1/2 touch-none cursor-col-resize items-center justify-center rounded-sm select-none focus-visible:z-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-primary ${
        dragging === handle ? 'bg-accent-blue/40' : 'hover:bg-accent-blue/20'
      }`,
      style: { left: `clamp(0px, calc(${pct}% - 22px), calc(100% - 44px))` },
      onBlur: () => setPreviewTime(null),
      onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => handleKeyDown(handle, e),
      onLostPointerCapture: (e: React.PointerEvent<HTMLDivElement>) => finishPointer(e, false),
      onPointerCancel: (e: React.PointerEvent<HTMLDivElement>) => finishPointer(e, false),
      onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => handlePointerDown(handle, e),
      onPointerMove: handlePointerMove,
      onPointerUp: (e: React.PointerEvent<HTMLDivElement>) => finishPointer(e, true),
    }
  }

  return (
    <div className="space-y-2">
      {/* Video preview — scrubs to handle position */}
      <div className="rounded-lg overflow-hidden bg-black aspect-video">
        <video ref={videoRef} src={videoUrl} className="w-full h-full object-contain" muted />
      </div>

      {/* Timeline bar */}
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1 text-[9px] text-text-muted">
        <span>{formatTime(normalizedValues.start)}</span>
        <span className="min-w-fit flex-1 text-center text-accent-blue">{formatTime(normalizedValues.end - normalizedValues.start)} selected</span>
        <span>{formatTime(normalizedDuration)}</span>
      </div>

      {/* Filmstrip + handles */}
      <div
        ref={trackRef}
        data-timeline-track
        className="relative min-w-0 select-none touch-none"
        style={{ height }}
      >
        {/* Thumbnail filmstrip */}
        <div className="absolute inset-0 flex rounded-md overflow-hidden">
          {thumbnails.length > 0 ? thumbnails.map((src, i) => (
            <img key={i} src={src} alt="" aria-hidden="true" className="h-full min-w-0 flex-1 object-cover" draggable={false} />
          )) : (
            <div className="w-full h-full bg-bg-tertiary flex items-center justify-center">
              <span className="text-[9px] text-text-muted">Loading thumbnails...</span>
            </div>
          )}
        </div>

        {/* Dimmed regions outside selection */}
        <div className="absolute inset-y-0 left-0 bg-black/60 rounded-l-md pointer-events-none"
          style={{ width: `${startPct}%` }} />
        <div className="absolute inset-y-0 right-0 bg-black/60 rounded-r-md pointer-events-none"
          style={{ width: `${100 - endPct}%` }} />

        {/* Selection border */}
        <div className="absolute inset-y-0 border-2 border-accent-blue rounded-sm pointer-events-none"
          style={{ left: `${startPct}%`, width: `${endPct - startPct}%` }} />

        {/* Start handle */}
        <div {...handleProps('start')} />
        <div
          aria-hidden="true"
          data-timeline-grip="start"
          className="pointer-events-none absolute top-1/2 z-20 h-8 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent-blue"
          style={{ left: `clamp(2px, ${startPct}%, calc(100% - 2px))` }}
        />

        {/* End handle */}
        <div {...handleProps('end')} />
        <div
          aria-hidden="true"
          data-timeline-grip="end"
          className="pointer-events-none absolute top-1/2 z-20 h-8 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent-blue"
          style={{ left: `clamp(2px, ${endPct}%, calc(100% - 2px))` }}
        />
      </div>
    </div>
  )
}

function formatTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const tenths = Math.round(safeSeconds * 10)
  const m = Math.floor(tenths / 600)
  const s = Math.floor((tenths % 600) / 10)
  const ms = tenths % 10
  return m > 0 ? `${m}:${s.toString().padStart(2, '0')}.${ms}` : `${s}.${ms}s`
}

function formatAriaTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const tenths = Math.round(safeSeconds * 10)
  const minutes = Math.floor(tenths / 600)
  const remainingTenths = tenths % 600
  const formattedSeconds = (remainingTenths / 10).toFixed(1)
  if (minutes === 0) return `${formattedSeconds} seconds`
  return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}, ${formattedSeconds} seconds`
}

function roundToStep(value: number): number {
  return Math.round(value * 10) / 10
}

function snapWithinBounds(value: number, min: number, max: number): number {
  if (value <= min + VALUE_EPSILON) return min
  if (value >= max - VALUE_EPSILON) return max
  return clamp(roundToStep(value), min, max)
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max))
}

function normalizeTimelineValues(start: number, end: number, duration: number): TimelineValues {
  if (duration < TIME_STEP_SECONDS) return { start: 0, end: duration }
  const normalizedEnd = clamp(Number.isFinite(end) ? end : duration, TIME_STEP_SECONDS, duration)
  const normalizedStart = clamp(Number.isFinite(start) ? start : 0, 0, normalizedEnd - TIME_STEP_SECONDS)
  return { start: normalizedStart, end: normalizedEnd }
}

function getHandleBounds(handle: Handle, values: TimelineValues, duration: number) {
  if (handle === 'start') {
    return { min: 0, max: Math.max(0, values.end - TIME_STEP_SECONDS) }
  }
  return { min: Math.min(duration, values.start + TIME_STEP_SECONDS), max: duration }
}

function isAdjustable(bounds: { min: number; max: number }): boolean {
  return bounds.max - bounds.min > VALUE_EPSILON
}

function nearestHandle(time: number, values: TimelineValues, fallback: Handle): Handle {
  const startDistance = Math.abs(time - values.start)
  const endDistance = Math.abs(time - values.end)
  if (Math.abs(startDistance - endDistance) <= VALUE_EPSILON) return fallback
  return startDistance < endDistance ? 'start' : 'end'
}
