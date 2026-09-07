import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { Search, X, Loader2, Globe, Sparkles, BookOpen, Info, ArrowUpCircle, RefreshCw, ArrowDownAZ, Clock } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { generateLoraGuide, fetchLoraGuide, fetchLoraDetails, checkLoraUpdates } from '../../api/client'
import { formatAge } from '../../lib/format'
import type { LoraRecommendedWeights, LoraUpdateStatus } from '../../types'
import { sortLoraNames } from './loraSort'
import type { LoraDates, LoraPickerSort } from './loraSort'
import {
  defaultAdaptiveFl2vaModel,
  defaultAdaptiveRef2vaModel,
  filterLorasForArchitecture,
  h3AdaptivePairActive,
  h3AdaptiveSelectionError,
  h3ArchitectureForModel,
  h3LorasForArchitecture,
  h3LoraBlockReason,
  parseLoraMultiplierMap,
} from '../../lib/h3Submission'

const EMPTY_LORAS: string[] = []
const EMPTY_LORA_WEIGHTS: Record<string, number[]> = Object.create(null)

type Architecture = 'fl2va' | 'ref2va'
type ArchitectureLoraState = {
  loras: string[]
  multipliers: string
  error: string | null
}

function architectureLoraState(
  params: Parameters<typeof h3LorasForArchitecture>[0],
  architecture: Architecture,
): ArchitectureLoraState {
  const otherKey = architecture === 'fl2va' ? 'h3_ref2va_loras' : 'h3_fl2va_loras'
  try {
    // Validate and render each side independently so a malformed saved value
    // on one side does not hide the other side's explicit repair controls.
    return {
      ...h3LorasForArchitecture({ ...params, [otherKey]: EMPTY_LORAS }, architecture),
      error: null,
    }
  } catch (error) {
    return {
      loras: EMPTY_LORAS,
      multipliers: '',
      error: error instanceof Error ? error.message : 'Saved LoRA settings could not be read.',
    }
  }
}

export function LoraGuideTooltip({ guide }: { guide: string }) {
  const [show, setShow] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState({ top: 0, left: 0 })

  const updatePos = () => {
    if (!btnRef.current) return
    const rect = btnRef.current.getBoundingClientRect()
    setPos({ top: rect.top - 4, left: Math.min(rect.right, window.innerWidth - 272) })
  }

  return (
    <>
      <button
        ref={btnRef}
        onMouseEnter={() => { updatePos(); setShow(true) }}
        onMouseLeave={() => setShow(false)}
        onClick={e => { e.stopPropagation(); updatePos(); setShow(!show) }}
        // Functional indicator (LoRA has a guide) — paired with the
        // BookOpen "guide available" badge below. Uses indicator-success
        // so the green meaning stays consistent across themes.
        className="p-0.5 text-indicator-success hover:text-indicator-success/80 transition-colors"
      >
        <Info size={11} />
      </button>
      {show && createPortal(
        <div
          className="fixed w-64 bg-bg-secondary border border-border rounded-lg shadow-xl z-[100] p-2.5"
          style={{ top: pos.top, left: pos.left, transform: 'translate(-100%, -100%)' }}
          onMouseEnter={() => setShow(true)}
          onMouseLeave={() => setShow(false)}
        >
          <div className="text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">{guide}</div>
        </div>,
        document.body,
      )}
    </>
  )
}

/** Per-file dates lifted from the /details response — shared shape between
 *  the Studio and Director LoRA pickers. */
export type { LoraDates } from './loraSort'

/** Compact age chip for LoRA picker rows. Prefers the CivitAI release date
 *  (answers "how new is this LoRA?"), falls back to the download/mtime date
 *  for hand-installed files. Full dates live in the tooltip. */
export function LoraAgeChip({ released, downloaded }: LoraDates) {
  const age = formatAge(released || downloaded)
  if (!age) return null
  const tip = [
    released ? `Released ${new Date(released).toLocaleDateString()}` : null,
    downloaded ? `Downloaded ${new Date(downloaded).toLocaleDateString()}` : null,
  ].filter(Boolean).join(' — ')
  return (
    <span className="text-[9px] text-text-muted shrink-0 tabular-nums" title={tip}>
      {age}
    </span>
  )
}

// Persistence and cross-picker sync live in the store (loraPickerSort /
// setLoraPickerSort) — per-component state would desync simultaneously
// mounted pickers, e.g. Director's Image + Video accordions.
export type { LoraPickerSort } from './loraSort'

/** Two-state sort toggle shared by both pickers: A-Z <-> newest first. */
export function LoraSortToggle({ sort, onChange }: { sort: LoraPickerSort; onChange: (s: LoraPickerSort) => void }) {
  const newest = sort === 'newest'
  return (
    <button
      type="button"
      onClick={() => onChange(newest ? 'name' : 'newest')}
      aria-label={newest ? 'Sort LoRAs by name' : 'Sort LoRAs by newest release'}
      className={`flex min-h-11 min-w-11 items-center justify-center gap-0.5 rounded text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:justify-start md:rounded-none ${
        newest ? 'text-accent-blue hover:text-accent-blue-hover' : 'text-text-muted hover:text-accent-blue'
      }`}
      title={newest
        ? 'Sorted by newest release first. Click to sort by name.'
        : 'Sorted by name. Click to sort by newest release first.'}
    >
      {newest ? <Clock size={10} /> : <ArrowDownAZ size={10} />}
      {newest ? 'New' : 'A-Z'}
    </button>
  )
}

export function LoraSelector() {
  const modelType = useStore(s => s.params.model_type)
  const loraCompatibilityNote = useStore(s => s.models.find(
    model => model.model_type === s.params.model_type,
  )?.lora_compatibility_note)
  const activatedLoras = useStore(s => s.params.activated_loras)
  const availableLoras = useStore(s => s.availableLoras)
  const lorasLoading = useStore(s => s.lorasLoading)
  const loraWeights = useStore(s => s.loraWeights)
  const modelOptions = useStore(s => s.modelOptions)
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const toggleLora = useStore(s => s.toggleLora)
  const toggleH3ArchitectureLora = useStore(s => s.toggleH3ArchitectureLora)
  const setLoraWeight = useStore(s => s.setLoraWeight)
  const setH3ArchitectureLoraWeight = useStore(s => s.setH3ArchitectureLoraWeight)
  const setParams = useStore(s => s.setParams)
  const loadLoras = useStore(s => s.loadLoras)
  const openBrowser = useStore(s => s.setLoraBrowserOpen)
  const h3Adaptive = useStore(s => h3AdaptivePairActive(
    s.params.model_type,
    s.params.h3_adaptive_conditioning,
  ))
  const h3SelectionError = useStore(s => h3AdaptiveSelectionError(s.params))
  const adaptiveFl2vaModel = useStore(s => defaultAdaptiveFl2vaModel(
    s.params.model_type,
    s.params.h3_adaptive_fl2va_model,
  ))
  const adaptiveRef2vaModel = useStore(s => defaultAdaptiveRef2vaModel(
    s.params.h3_adaptive_ref2va_model,
  ))
  const h3Fl2vaLoras = useStore(s => s.params.h3_fl2va_loras)
  const h3Ref2vaLoras = useStore(s => s.params.h3_ref2va_loras)
  const h3Fl2vaMultipliers = useStore(s => s.params.h3_fl2va_loras_multipliers)
  const h3Ref2vaMultipliers = useStore(s => s.params.h3_ref2va_loras_multipliers)
  const inheritedLoraMultipliers = useStore(s => s.params.loras_multipliers)
  const adaptiveParams = useMemo(() => ({
    activated_loras: activatedLoras,
    loras_multipliers: inheritedLoraMultipliers,
    h3_fl2va_loras: h3Fl2vaLoras,
    h3_fl2va_loras_multipliers: h3Fl2vaMultipliers,
    h3_ref2va_loras: h3Ref2vaLoras,
    h3_ref2va_loras_multipliers: h3Ref2vaMultipliers,
  }), [
    activatedLoras,
    h3Fl2vaLoras,
    h3Fl2vaMultipliers,
    h3Ref2vaLoras,
    h3Ref2vaMultipliers,
    inheritedLoraMultipliers,
  ])
  const fl2vaState = useMemo(
    () => architectureLoraState(adaptiveParams, 'fl2va'),
    [adaptiveParams],
  )
  const ref2vaState = useMemo(
    () => architectureLoraState(adaptiveParams, 'ref2va'),
    [adaptiveParams],
  )
  const detailModelTypes = useMemo(
    () => h3Adaptive
      ? h3SelectionError
        ? EMPTY_LORAS
        : Array.from(new Set([adaptiveFl2vaModel, adaptiveRef2vaModel]))
      : modelType ? [modelType] : EMPTY_LORAS,
    [adaptiveFl2vaModel, adaptiveRef2vaModel, h3Adaptive, h3SelectionError, modelType],
  )
  const activeGuideLoras = useMemo(
    () => h3Adaptive
      ? Array.from(new Set([...fl2vaState.loras, ...ref2vaState.loras]))
      : activatedLoras,
    [activatedLoras, fl2vaState.loras, h3Adaptive, ref2vaState.loras],
  )

  const [search, setSearch] = useState('')
  const [guideStatus, setGuideStatus] = useState<Record<string, 'none' | 'exists' | 'generating' | 'done'>>({})
  const [guideTexts, setGuideTexts] = useState<Record<string, string>>({})
  const [loraWeightRecs, setLoraWeightRecs] = useState<Record<string, LoraRecommendedWeights>>({})
  const [loraDetailsError, setLoraDetailsError] = useState('')
  const loraDetailsRequest = useRef(0)
  // Per-filename update_status from the cached LoRA-update manifest. The
  // backend embeds this on every /details response so we don't need to
  // fetch it separately; we just lift it into a lookup map.
  const [updateStatuses, setUpdateStatuses] = useState<Record<string, LoraUpdateStatus>>({})
  // Per-filename release/download dates from the /details sidecar data —
  // rendered as an age chip so similarly-named LoRAs can be told apart
  // by how new they are.
  const [loraDates, setLoraDates] = useState<Record<string, LoraDates>>({})
  // Sticky list order shared with the Director picker via the store.
  const sortMode = useStore(s => s.loraPickerSort)
  const setSortSticky = useStore(s => s.setLoraPickerSort)
  // ISO timestamp of the last full CivitAI check, used to render
  // "checked Xm ago" next to the manual refresh button.
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null)
  // True while a manual /check-updates call is in flight.
  const [checking, setChecking] = useState(false)
  // Filter that hides everything except LoRAs whose update_status is
  // 'available'. Activated LoRAs are still shown regardless so the user
  // can deactivate them without first turning the filter off.
  const [updatableOnly, setUpdatableOnly] = useState(false)

  const fetchCurrentLoraDetails = useCallback(async () => {
    if (h3SelectionError) throw new Error(h3SelectionError)
    const settled = await Promise.allSettled(
      detailModelTypes.map(type => fetchLoraDetails(type)),
    )
    const responses = settled.flatMap(result => (
      result.status === 'fulfilled' ? [result.value] : []
    ))
    if (responses.length === 0) throw new Error('LoRA details could not be loaded.')
    return {
      responses,
      incomplete: responses.length !== settled.length,
    }
  }, [detailModelTypes, h3SelectionError])

  // Trigger a fresh CivitAI check, then refetch /details so the manifest's
  // newly-updated entries flow back into our updateStatuses map.
  const handleCheckUpdates = useCallback(async () => {
    if (!modelType || checking) return
    if (h3SelectionError) {
      setLoraDetailsError(h3SelectionError)
      return
    }
    setChecking(true)
    try {
      await checkLoraUpdates(true) // force=true: bypass 24h staleness window
      const { responses, incomplete } = await fetchCurrentLoraDetails()
      const next: Record<string, LoraUpdateStatus> = {}
      // check-updates backfills publishedAt into sidecars that predate its
      // capture, so this refetch is exactly when release dates appear —
      // refresh the age-chip map too, not just update statuses.
      const dates: Record<string, LoraDates> = {}
      for (const response of responses) {
        for (const info of response.loras) {
          if (info.update_status) next[info.filename] = info.update_status
          if (info.released_at || info.downloaded_at) {
            dates[info.filename] = { released: info.released_at, downloaded: info.downloaded_at }
          }
        }
      }
      setUpdateStatuses(next)
      setLoraDates(dates)
      setLastCheckedAt(responses.map(response => response.manifest_last_check_at).find(Boolean) ?? null)
      setLoraDetailsError(incomplete ? 'Some model-specific LoRA details could not be loaded.' : '')
    } catch (e) {
      console.error('LoRA update check failed:', e)
      setLoraDetailsError(e instanceof Error ? e.message : 'LoRA details could not be loaded.')
    } finally {
      setChecking(false)
    }
  }, [checking, fetchCurrentLoraDetails, h3SelectionError, modelType])

  // Count of LoRAs with an available update — surfaced as a badge on the
  // refresh button so the user sees at a glance whether anything's
  // outdated without expanding the list.
  const updatableCount = Object.values(updateStatuses).filter(s => s === 'available').length
  const checkUpdatesLabel = checking
    ? 'Checking CivitAI updates'
    : updatableCount > 0
      ? `Check CivitAI updates, ${updatableCount} update${updatableCount === 1 ? '' : 's'} available`
      : 'Check CivitAI updates'

  // Render-only formatter — "5m ago" / "2h ago" / "3d ago" / "" for null.
  const formatRelative = (iso: string | null): string => {
    if (!iso) return ''
    const t = Date.parse(iso)
    if (Number.isNaN(t)) return ''
    const mins = Math.max(0, Math.round((Date.now() - t) / 60000))
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.round(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.round(hrs / 24)}d ago`
  }

  const loraHeader = (
    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-1">
      <label className="text-[11px] text-text-muted uppercase tracking-wider">LoRAs</label>
      <div className="ml-auto flex flex-wrap items-center justify-end gap-1 md:gap-2">
        <LoraSortToggle sort={sortMode} onChange={setSortSticky} />
        <button
          type="button"
          onClick={handleCheckUpdates}
          disabled={checking || !modelType}
          aria-label={checkUpdatesLabel}
          className="flex min-h-11 min-w-11 items-center justify-center gap-0.5 rounded px-1 text-[10px] text-text-muted transition-colors hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0 md:min-w-0 md:justify-start md:rounded-none md:px-0"
          title={h3SelectionError || (lastCheckedAt
            ? `Check CivitAI for newer LoRA versions (last checked ${formatRelative(lastCheckedAt)})`
            : 'Check CivitAI for newer LoRA versions')}
        >
          {checking
            ? <Loader2 size={10} className="animate-spin" />
            : <RefreshCw size={10} />}
          Check
          {updatableCount > 0 && (
            <span
              className="ml-0.5 px-1 rounded bg-amber-500/20 text-indicator-warning text-[9px] font-medium"
              title={`${updatableCount} update${updatableCount === 1 ? '' : 's'} available`}
            >
              {updatableCount}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={() => openBrowser(true, modelType)}
          aria-label="Browse CivitAI"
          className="flex min-h-11 min-w-11 items-center justify-center gap-0.5 rounded px-1 text-[10px] text-accent-blue transition-colors hover:text-accent-blue-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:justify-start md:rounded-none md:px-0"
          title="Browse CivitAI"
        >
          <Globe size={10} />
          Browse
        </button>
      </div>
    </div>
  )

  const compatibilityNotice = loraCompatibilityNote ? (
    <div className="mb-2 flex items-start gap-1.5 rounded-lg border border-border bg-bg-tertiary px-2.5 py-2 text-[10px] leading-relaxed text-text-secondary">
      <Info size={11} className="mt-0.5 shrink-0 text-accent-blue" />
      <span>{loraCompatibilityNote}</span>
    </div>
  ) : null

  // Load LoRA details (weight recommendations for the list, guides for activated)
  useEffect(() => {
    const detailsRequest = ++loraDetailsRequest.current
    if (!modelType || h3SelectionError) return
    // Check guide status for activated LoRAs
    for (const lora of activeGuideLoras) {
      if (guideStatus[lora]) continue
      const guideModelType = h3Adaptive
        ? ref2vaState.loras.includes(lora) && !fl2vaState.loras.includes(lora)
          ? adaptiveRef2vaModel
          : adaptiveFl2vaModel
        : modelType
      fetchLoraGuide(guideModelType, lora).then(r => {
        setGuideStatus(s => ({ ...s, [lora]: r.guide ? 'exists' : 'none' }))
      }).catch(() => {})
    }
    // Load weight recommendations, guides, and apply defaults to newly activated LoRAs
    fetchCurrentLoraDetails().then(({ responses, incomplete }) => {
      if (detailsRequest !== loraDetailsRequest.current) return
      const recs: Record<string, LoraRecommendedWeights> = {}
      const guides: Record<string, string> = {}
      const statuses: Record<string, 'exists' | 'none'> = {}
      const updates: Record<string, LoraUpdateStatus> = {}
      const dates: Record<string, LoraDates> = {}
      for (const response of responses) {
        for (const info of response.loras) {
          if (info.recommended_weights) recs[info.filename] = info.recommended_weights
          if (info.guide) { guides[info.filename] = info.guide; statuses[info.filename] = 'exists' }
          else if (info.has_guide) statuses[info.filename] = 'exists'
          if (info.update_status) updates[info.filename] = info.update_status
          if (info.released_at || info.downloaded_at) {
            dates[info.filename] = { released: info.released_at, downloaded: info.downloaded_at }
          }
        }
      }
      setLoraWeightRecs(recs)
      setGuideTexts(prev => ({ ...prev, ...guides }))
      setGuideStatus(prev => ({ ...prev, ...statuses }))
      setUpdateStatuses(updates)
      setLoraDates(dates)
      setLastCheckedAt(responses.map(response => response.manifest_last_check_at).find(Boolean) ?? null)
      setLoraDetailsError(incomplete ? 'Some model-specific LoRA details could not be loaded.' : '')

      // Apply recommended defaults to LoRAs that are still at the initial 1.0 fill
      for (const lora of activeGuideLoras) {
        // Architecture-specific weights are an exact saved contract. Show
        // recommendations in the UI, but do not rewrite them during detail
        // loading; the user can move the visible slider explicitly.
        if (h3Adaptive) continue
        const rec = recs[lora]
        if (!rec) continue
        const currentWeights = loraWeights[lora]
        if (!currentWeights) continue
        // Only apply if all phases are at exactly 1.0 (initial fill value)
        const allDefault = currentWeights.every(w => w === 1.0)
        if (!allDefault) continue
        const newWeights = currentWeights.map((_, i) => {
          const phaseRec = rec.phases?.find(p => p.phase === i + 1)
          const d = phaseRec?.default ?? rec.default
          const min = phaseRec?.min ?? rec.min
          const max = phaseRec?.max ?? rec.max
          // Use recommended default, or midpoint of range if default is outside range
          if (d != null && d >= min && d <= max) return d
          if (min != null && max != null) return Math.round(((min + max) / 2) * 20) / 20
          return d ?? 0.8
        })
        for (let i = 0; i < newWeights.length; i++) {
          setLoraWeight(lora, i, newWeights[i])
        }
      }
    }).catch(error => {
      if (detailsRequest !== loraDetailsRequest.current) return
      console.error('Could not load LoRA details:', error)
      setLoraDetailsError(error instanceof Error ? error.message : 'LoRA details could not be loaded.')
    })
  // The request token prevents stale writes. Including guideStatus or
  // loraWeights would re-run this detail hydration because this effect owns
  // those state updates.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeGuideLoras,
    adaptiveFl2vaModel,
    adaptiveRef2vaModel,
    fetchCurrentLoraDetails,
    fl2vaState.loras,
    h3Adaptive,
    h3SelectionError,
    modelType,
    ref2vaState.loras,
  ])

  const handleGenerateGuide = async (filename: string, architecture: Architecture | null) => {
    if (h3SelectionError) return
    const guideModelType = architecture === 'fl2va'
      ? adaptiveFl2vaModel
      : architecture === 'ref2va'
        ? adaptiveRef2vaModel
        : modelType
    if (!guideModelType) return
    setGuideStatus(s => ({ ...s, [filename]: 'generating' }))
    try {
      await generateLoraGuide(guideModelType, filename)
      setGuideStatus(s => ({ ...s, [filename]: 'done' }))
    } catch (e) {
      console.error('Guide generation failed:', e)
      setGuideStatus(s => ({ ...s, [filename]: 'none' }))
    }
  }

  // Recast owns a one-phase SCAIL-2 schedule. Showing the Wan family's
  // generic three phase sliders produced invalid `1;1;1` multipliers.
  const recastSinglePhase = generationMode === 'avatar' && editSubMode === 'recast'
  const phases = recastSinglePhase ? 1 : Math.max(1, modelOptions?.guidance_max_phases ?? 1)

  // Load LoRAs when model changes
  useEffect(() => {
    if (modelType) loadLoras(modelType)
  }, [modelType, loadLoras])

  const displayName = (filename: string) => {
    return filename.replace(/\.(safetensors|sft)$/i, '')
  }

  const namesFor = (
    architecture: 'fl2va' | 'ref2va' | null,
    activated: string[],
  ) => sortLoraNames((architecture
    ? filterLorasForArchitecture(availableLoras, architecture)
    : availableLoras
  ).filter(name => {
    if (!displayName(name).toLowerCase().includes(search.toLowerCase())) return false
    const isActivated = activated.includes(name)
    if (updatableOnly && !isActivated && updateStatuses[name] !== 'available') return false
    return true
  }), sortMode, loraDates)

  const pinnedArchitecture = h3ArchitectureForModel(modelType)
  const flWeightMap = fl2vaState.error
    ? EMPTY_LORA_WEIGHTS
    : parseLoraMultiplierMap(fl2vaState.loras, fl2vaState.multipliers, phases)
  const refWeightMap = ref2vaState.error
    ? EMPTY_LORA_WEIGHTS
    : parseLoraMultiplierMap(ref2vaState.loras, ref2vaState.multipliers, phases)

  const listPanel = (
    title: string,
    detail: string,
    architecture: 'fl2va' | 'ref2va' | null,
    activated: string[],
    onToggle: (filename: string) => void,
    onWeight: (filename: string, phase: number, value: number) => void,
    onClear: () => void,
    weightLookup: Record<string, number[]>,
  ) => {
    const filtered = namesFor(architecture, activated)
    return (
      <section
        role={title ? 'group' : undefined}
        aria-label={title ? `${title} · ${detail}` : undefined}
        className={title ? 'mt-3 rounded-lg border border-border bg-bg-tertiary/40 p-2.5' : undefined}
      >
        {title && (
          <div className="mb-1.5">
            <div className="text-[10px] font-medium uppercase tracking-wider text-text-primary">{title}</div>
            <div className="mt-0.5 text-[9px] leading-relaxed text-text-muted">{detail} · applies only to that model's shots</div>
          </div>
        )}
        <div className="max-h-[120px] overflow-y-auto border border-border rounded-lg bg-bg-tertiary">
          {filtered.map(filename => {
            const isActive = activated.includes(filename)
            const blockReason = architecture
              ? h3LoraBlockReason(filename, architecture, activated.filter(name => name !== filename))
              : pinnedArchitecture
                ? h3LoraBlockReason(filename, pinnedArchitecture, activated.filter(name => name !== filename))
                : null
            const blocked = Boolean(!isActive && blockReason)
            return (
              <button
                key={`${architecture || 'shared'}:${filename}`}
                type="button"
                disabled={blocked}
                aria-pressed={isActive}
                aria-label={`${isActive ? 'Remove' : 'Add'} ${displayName(filename)} ${architecture ? `from ${title}` : ''}`.trim()}
                title={blockReason || undefined}
                onClick={() => { if (!blocked) onToggle(filename) }}
                className={`w-full text-left px-2.5 py-1.5 text-xs flex items-center gap-2 hover:bg-bg-hover transition-colors ${
                  isActive ? 'text-accent-blue' : blocked ? 'text-text-muted opacity-45 cursor-not-allowed' : 'text-text-secondary'
                }`}
              >
                <div className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 ${
                  isActive ? 'bg-accent-blue border-accent-blue' : 'border-border'
                }`}>
                  {isActive && (
                    <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                      <path d="M1.5 4L3 5.5L6.5 2" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
                <span className="truncate flex-1">{displayName(filename)}</span>
                {loraDates[filename] && (
                  <LoraAgeChip
                    released={loraDates[filename].released}
                    downloaded={loraDates[filename].downloaded}
                  />
                )}
                {guideTexts[filename] && (
                  <span onClick={e => e.stopPropagation()}>
                    <LoraGuideTooltip guide={guideTexts[filename]} />
                  </span>
                )}
                {loraWeightRecs[filename] && (
                  <span
                    className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                      loraWeightRecs[filename].source === 'civitai' ? 'bg-indicator-success' : 'bg-indicator-warning'
                    }`}
                    title={loraWeightRecs[filename].source === 'civitai' ? 'CivitAI recommended settings' : 'Default settings'}
                  />
                )}
                {updateStatuses[filename] === 'available' && (
                  <ArrowUpCircle
                    size={11}
                    className="text-indicator-warning shrink-0"
                    aria-label="Update available"
                  />
                )}
              </button>
            )
          })}
          {filtered.length === 0 && (
            <div className="px-3 py-2 text-xs text-text-muted text-center">
              {search ? 'No matches' : architecture ? `No compatible ${detail.toLowerCase()} found` : 'No LoRAs found for this model'}
            </div>
          )}
        </div>
        {activated.length > 0 && (
          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="text-[10px] text-text-muted uppercase tracking-wider">
                Selected ({activated.length})
              </div>
              <button
                type="button"
                onClick={onClear}
                className="text-[10px] text-text-muted hover:text-red-400 transition-colors"
              >
                Clear
              </button>
            </div>
            {activated.map(filename => {
              const storedWeights = weightLookup[filename] || loraWeights[filename] || [1.0]
              const weights = Array.from(
                { length: phases },
                (_, i) => storedWeights[i] ?? storedWeights[storedWeights.length - 1] ?? 1.0,
              )
              return (
                <div key={`${architecture || 'shared'}-active-${filename}`} className="bg-bg-tertiary border border-border rounded-lg px-2.5 py-2">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs text-text-primary truncate flex-1 mr-2 flex items-center gap-1">
                      {displayName(filename)}
                      {updateStatuses[filename] === 'available' && (
                        <ArrowUpCircle
                          size={11}
                          className="text-indicator-warning shrink-0"
                          aria-label="Update available"
                        />
                      )}
                    </span>
                    <div className="flex items-center gap-0.5 shrink-0">
                      {guideStatus[filename] === 'exists' || guideStatus[filename] === 'done' ? (
                        <span className="p-0.5 text-indicator-success" title="LoRA guide available">
                          <BookOpen size={11} />
                        </span>
                      ) : guideStatus[filename] === 'generating' ? (
                        <span className="p-0.5 text-accent-blue">
                          <Loader2 size={11} className="animate-spin" />
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleGenerateGuide(filename, architecture) }}
                          disabled={Boolean(h3SelectionError)}
                          aria-label={`Generate guide for ${displayName(filename)}`}
                          className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-accent-blue transition-colors disabled:cursor-not-allowed disabled:opacity-45"
                          title={h3SelectionError || 'Generate AI guide for this LoRA'}
                        >
                          <Sparkles size={11} />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => onToggle(filename)}
                        aria-label={`Remove ${displayName(filename)} from ${title || 'LoRAs'}`}
                        className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  </div>
                  {weights.map((w, i) => {
                    const rec = loraWeightRecs[filename]
                    const phaseRec = rec?.phases?.find(p => p.phase === i + 1)
                    const fallbackMin = 0.6, fallbackMax = 1.0
                    const recMin = phaseRec?.min ?? rec?.min ?? fallbackMin
                    const recMax = phaseRec?.max ?? rec?.max ?? fallbackMax
                    const recDefault = phaseRec?.default ?? rec?.default ?? 0.8
                    const isCivitai = rec?.source === 'civitai' || (rec != null && rec.source !== 'default')
                    const sliderMax = 2
                    const zoneLeft = (recMin / sliderMax) * 100
                    const zoneWidth = ((recMax - recMin) / sliderMax) * 100
                    const inZone = w >= recMin && w <= recMax
                    const zoneColor = isCivitai
                      ? 'bg-indicator-success/20 border-indicator-success/30'
                      : 'bg-indicator-warning/15 border-indicator-warning/25'
                    const valueColor = inZone
                      ? (isCivitai ? 'text-indicator-success' : 'text-indicator-warning')
                      : 'text-text-muted'
                    return (
                      <div key={i} className="flex items-center gap-2">
                        {phases > 1 && (
                          <span className="text-[10px] text-text-muted w-12 shrink-0" title={phaseRec?.label || ''}>
                            Phase {i + 1}
                          </span>
                        )}
                        <div className="flex-1 relative">
                          <div
                            className={`absolute top-1/2 -translate-y-1/2 h-2 rounded-full ${zoneColor} pointer-events-none`}
                            style={{ left: `${zoneLeft}%`, width: `${zoneWidth}%` }}
                            title={`${isCivitai ? 'CivitAI' : 'Default'}: ${recMin}-${recMax} (${recDefault})`}
                          />
                          <input
                            type="range"
                            min={0}
                            max={sliderMax}
                            step={0.05}
                            value={w}
                            onChange={e => onWeight(filename, i, parseFloat(e.target.value))}
                            aria-label={`${displayName(filename)} ${architecture ? `${title} ` : ''}weight${phases > 1 ? ` phase ${i + 1}` : ''}`}
                            className="w-full relative z-10"
                          />
                        </div>
                        <span className={`text-[10px] w-8 text-right shrink-0 ${valueColor}`}>
                          {w.toFixed(2)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )}
      </section>
    )
  }

  const clearArchitectureLoras = (architecture: Architecture) => {
    if (architecture === 'fl2va') {
      setParams({
        h3_fl2va_loras: [],
        h3_fl2va_loras_multipliers: '',
      })
    } else {
      setParams({
        h3_ref2va_loras: [],
        h3_ref2va_loras_multipliers: '',
      })
    }
  }

  const repairPanel = (
    title: string,
    detail: string,
    architecture: Architecture,
    message: string,
  ) => (
    <section
      role="group"
      aria-label={`${title} · ${detail}`}
      className="mt-3 rounded-lg border border-red-500/35 bg-red-500/10 p-2.5"
    >
      <div className="text-[10px] font-medium uppercase tracking-wider text-text-primary">{title}</div>
      <div className="mt-0.5 text-[9px] text-text-muted">{detail}</div>
      <p role="status" className="mt-2 text-[10px] leading-relaxed text-red-100">
        Saved LoRA settings need repair. {message}
      </p>
      <button
        type="button"
        onClick={() => clearArchitectureLoras(architecture)}
        className="mobile-control-target mt-2 rounded border border-red-300/40 px-2 py-1 text-[10px] font-medium text-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
      >
        Clear saved {title} LoRAs
      </button>
    </section>
  )

  if (lorasLoading) {
    return (
      <div>
        {loraHeader}
        {compatibilityNotice}
        <div className="text-xs text-text-muted bg-bg-tertiary border border-border rounded-lg px-3 py-4 text-center flex items-center justify-center gap-2">
          <Loader2 size={12} className="animate-spin" />
          Loading LoRAs...
        </div>
      </div>
    )
  }

  if (availableLoras.length === 0 && !h3Adaptive) {
    return (
      <div>
        {loraHeader}
        {compatibilityNotice}
        <div className="text-xs text-text-muted bg-bg-tertiary border border-border rounded-lg px-3 py-4 text-center">
          {loraDetailsError || 'No LoRAs found for this model'}
        </div>
      </div>
    )
  }

  return (
    <div>
      {loraHeader}
      {compatibilityNotice}
      {loraDetailsError && (
        <p role="status" className="mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-[10px] text-amber-100">
          {loraDetailsError}
        </p>
      )}

      {/* Search + Updatable toggle */}
      <div className="flex items-center gap-2 mb-2">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search LoRAs..."
            className="w-full bg-bg-tertiary border border-border rounded-lg pl-7 pr-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
          />
        </div>
        {/* Updatable-only filter — only meaningful when at least one LoRA
            actually has an update available; we still render the toggle
            unconditionally so the affordance is discoverable, but the
            label dims when there's nothing to filter. */}
        <label
          className="flex items-center gap-1 cursor-pointer shrink-0 select-none"
          title={updatableCount > 0
            ? `${updatableCount} LoRA${updatableCount === 1 ? ' has' : 's have'} updates available — check to filter`
            : 'No updates available — check "Check" first to refresh from CivitAI'}
        >
          <input
            type="checkbox"
            checked={updatableOnly}
            onChange={e => setUpdatableOnly(e.target.checked)}
            disabled={updatableCount === 0}
            className="w-3 h-3 rounded border-border accent-amber-500 disabled:opacity-40"
          />
          <span className={`text-[10px] uppercase tracking-wider flex items-center gap-0.5 ${
            updatableOnly ? 'text-indicator-warning' : updatableCount > 0 ? 'text-text-muted' : 'text-text-muted'
          }`}>
            <ArrowUpCircle size={10} />
            Updates
          </span>
        </label>
      </div>

      {h3Adaptive ? (
        <>
          {fl2vaState.error
            ? repairPanel('Text & frames', 'FL2VA adapters', 'fl2va', fl2vaState.error)
            : listPanel(
                'Text & frames',
                'FL2VA adapters',
                'fl2va',
                fl2vaState.loras,
                filename => toggleH3ArchitectureLora('fl2va', filename),
                (filename, phase, value) => setH3ArchitectureLoraWeight('fl2va', filename, phase, value),
                () => { for (const name of [...fl2vaState.loras]) toggleH3ArchitectureLora('fl2va', name) },
                flWeightMap,
              )}
          {ref2vaState.error
            ? repairPanel('References', 'Ref2VA adapters', 'ref2va', ref2vaState.error)
            : listPanel(
                'References',
                'Ref2VA adapters',
                'ref2va',
                ref2vaState.loras,
                filename => toggleH3ArchitectureLora('ref2va', filename),
                (filename, phase, value) => setH3ArchitectureLoraWeight('ref2va', filename, phase, value),
                () => { for (const name of [...ref2vaState.loras]) toggleH3ArchitectureLora('ref2va', name) },
                refWeightMap,
              )}
        </>
      ) : listPanel(
        '',
        '',
        pinnedArchitecture,
        activatedLoras,
        toggleLora,
        setLoraWeight,
        () => { for (const name of [...activatedLoras]) toggleLora(name) },
        loraWeights,
      )}
    </div>
  )
}
