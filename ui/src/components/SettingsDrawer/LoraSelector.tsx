import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Search, X, Loader2, Globe, Sparkles, BookOpen, Info, ArrowUpCircle, RefreshCw, ArrowDownAZ, Clock } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { generateLoraGuide, fetchLoraGuide, fetchLoraDetails, checkLoraUpdates } from '../../api/client'
import { formatAge } from '../../lib/format'
import type { LoraRecommendedWeights, LoraUpdateStatus } from '../../types'
import { sortLoraNames } from './loraSort'
import type { LoraDates, LoraPickerSort } from './loraSort'

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
  const setLoraWeight = useStore(s => s.setLoraWeight)
  const loadLoras = useStore(s => s.loadLoras)
  const openBrowser = useStore(s => s.setLoraBrowserOpen)

  const [search, setSearch] = useState('')
  const [guideStatus, setGuideStatus] = useState<Record<string, 'none' | 'exists' | 'generating' | 'done'>>({})
  const [guideTexts, setGuideTexts] = useState<Record<string, string>>({})
  const [loraWeightRecs, setLoraWeightRecs] = useState<Record<string, LoraRecommendedWeights>>({})
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

  // Trigger a fresh CivitAI check, then refetch /details so the manifest's
  // newly-updated entries flow back into our updateStatuses map.
  const handleCheckUpdates = useCallback(async () => {
    if (!modelType || checking) return
    setChecking(true)
    try {
      await checkLoraUpdates(true) // force=true: bypass 24h staleness window
      const r = await fetchLoraDetails(modelType)
      const next: Record<string, LoraUpdateStatus> = {}
      // check-updates backfills publishedAt into sidecars that predate its
      // capture, so this refetch is exactly when release dates appear —
      // refresh the age-chip map too, not just update statuses.
      const dates: Record<string, LoraDates> = {}
      for (const info of r.loras) {
        if (info.update_status) next[info.filename] = info.update_status
        if (info.released_at || info.downloaded_at) {
          dates[info.filename] = { released: info.released_at, downloaded: info.downloaded_at }
        }
      }
      setUpdateStatuses(next)
      setLoraDates(dates)
      setLastCheckedAt(r.manifest_last_check_at ?? null)
    } catch (e) {
      console.error('LoRA update check failed:', e)
    } finally {
      setChecking(false)
    }
  }, [modelType, checking])

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
          title={lastCheckedAt
            ? `Check CivitAI for newer LoRA versions (last checked ${formatRelative(lastCheckedAt)})`
            : 'Check CivitAI for newer LoRA versions'}
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
    if (!modelType) return
    // Check guide status for activated LoRAs
    for (const lora of activatedLoras) {
      if (guideStatus[lora]) continue
      fetchLoraGuide(modelType, lora).then(r => {
        setGuideStatus(s => ({ ...s, [lora]: r.guide ? 'exists' : 'none' }))
      }).catch(() => {})
    }
    // Load weight recommendations, guides, and apply defaults to newly activated LoRAs
    const detailsRequest = ++loraDetailsRequest.current
    fetchLoraDetails(modelType).then(r => {
      if (detailsRequest !== loraDetailsRequest.current) return
      const recs: Record<string, LoraRecommendedWeights> = {}
      const guides: Record<string, string> = {}
      const statuses: Record<string, 'exists' | 'none'> = {}
      const updates: Record<string, LoraUpdateStatus> = {}
      const dates: Record<string, LoraDates> = {}
      for (const info of r.loras) {
        if (info.recommended_weights) recs[info.filename] = info.recommended_weights
        if (info.guide) { guides[info.filename] = info.guide; statuses[info.filename] = 'exists' }
        else if (info.has_guide) statuses[info.filename] = 'exists'
        if (info.update_status) updates[info.filename] = info.update_status
        if (info.released_at || info.downloaded_at) {
          dates[info.filename] = { released: info.released_at, downloaded: info.downloaded_at }
        }
      }
      setLoraWeightRecs(recs)
      setGuideTexts(prev => ({ ...prev, ...guides }))
      setGuideStatus(prev => ({ ...prev, ...statuses }))
      setUpdateStatuses(updates)
      setLoraDates(dates)
      setLastCheckedAt(r.manifest_last_check_at ?? null)

      // Apply recommended defaults to LoRAs that are still at the initial 1.0 fill
      for (const lora of activatedLoras) {
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
    })
  }, [modelType, activatedLoras]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleGenerateGuide = async (filename: string) => {
    if (!modelType) return
    setGuideStatus(s => ({ ...s, [filename]: 'generating' }))
    try {
      await generateLoraGuide(modelType, filename)
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

  // Filter by search term and the optional update-status filter. Activated LoRAs are always
  // shown so the user can deactivate them without first turning off the
  // current filter — otherwise the checkbox becomes confusing when a
  // selected item suddenly vanishes from the list.
  const filtered = sortLoraNames(availableLoras.filter(name => {
    if (!displayName(name).toLowerCase().includes(search.toLowerCase())) return false
    const isActivated = activatedLoras.includes(name)
    if (updatableOnly && !isActivated && updateStatuses[name] !== 'available') return false
    return true
  }), sortMode, loraDates)

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

  if (availableLoras.length === 0) {
    return (
      <div>
        {loraHeader}
        {compatibilityNotice}
        <div className="text-xs text-text-muted bg-bg-tertiary border border-border rounded-lg px-3 py-4 text-center">
          No LoRAs found for this model
        </div>
      </div>
    )
  }

  return (
    <div>
      {loraHeader}
      {compatibilityNotice}

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

      {/* Available LoRAs list */}
      <div className="max-h-[120px] overflow-y-auto border border-border rounded-lg bg-bg-tertiary">
        {filtered.map(filename => {
          const isActive = activatedLoras.includes(filename)
          return (
            <button
              key={filename}
              onClick={() => toggleLora(filename)}
              className={`w-full text-left px-2.5 py-1.5 text-xs flex items-center gap-2 hover:bg-bg-hover transition-colors ${
                isActive ? 'text-accent-blue' : 'text-text-secondary'
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
                  // Use functional indicator tokens (NOT accent-green) so
                  // the CivitAI vs default distinction stays visually
                  // distinct across themes — see --color-indicator-success
                  // / --color-indicator-warning in index.css.
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    loraWeightRecs[filename].source === 'civitai' ? 'bg-indicator-success' : 'bg-indicator-warning'
                  }`}
                  title={loraWeightRecs[filename].source === 'civitai' ? 'CivitAI recommended settings' : 'Default settings'}
                />
              )}
              {/* Update-available badge — only when CivitAI reports a newer
                  version than what the user has installed. The icon is small
                  but distinct from the recommended-weights dot above so they
                  can coexist on the same row without confusion. */}
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
            No matches
          </div>
        )}
      </div>

      {/* Selected LoRAs with weight sliders */}
      {activatedLoras.length > 0 && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-[10px] text-text-muted uppercase tracking-wider">
              Selected ({activatedLoras.length})
            </div>
            <button
              onClick={() => { for (const l of [...activatedLoras]) toggleLora(l) }}
              className="text-[10px] text-text-muted hover:text-red-400 transition-colors"
            >
              Clear all
            </button>
          </div>
          {activatedLoras.map(filename => {
            const storedWeights = loraWeights[filename] || [1.0]
            const weights = Array.from(
              { length: phases },
              (_, i) => storedWeights[i] ?? storedWeights[storedWeights.length - 1] ?? 1.0,
            )
            return (
              <div key={filename} className="bg-bg-tertiary border border-border rounded-lg px-2.5 py-2">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-text-primary truncate flex-1 mr-2 flex items-center gap-1">
                    {displayName(filename)}
                    {/* Update-available indicator on the activated card —
                        same icon as in the picker so the user can scan
                        both views consistently. */}
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
                        onClick={(e) => { e.stopPropagation(); handleGenerateGuide(filename) }}
                        className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-accent-blue transition-colors"
                        title="Generate AI guide for this LoRA"
                      >
                        <Sparkles size={11} />
                      </button>
                    )}
                    <button
                      onClick={() => toggleLora(filename)}
                      className="p-0.5 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                    >
                      <X size={12} />
                    </button>
                  </div>
                </div>
                {weights.map((w, i) => {
                  const rec = loraWeightRecs[filename]
                  const phaseRec = rec?.phases?.find(p => p.phase === i + 1)
                  // Use CivitAI data if available, otherwise fallback defaults
                  const fallbackMin = 0.6, fallbackMax = 1.0
                  const recMin = phaseRec?.min ?? rec?.min ?? fallbackMin
                  const recMax = phaseRec?.max ?? rec?.max ?? fallbackMax
                  const recDefault = phaseRec?.default ?? rec?.default ?? 0.8
                  const isCivitai = rec?.source === 'civitai' || (rec != null && rec.source !== 'default')
                  const sliderMax = 2
                  const zoneLeft = (recMin / sliderMax) * 100
                  const zoneWidth = ((recMax - recMin) / sliderMax) * 100
                  const inZone = w >= recMin && w <= recMax

                  // Green = CivitAI recommendation, Yellow = fallback defaults.
                  // Uses functional indicator tokens (--color-indicator-success
                  // and --color-indicator-warning) so the green-vs-yellow
                  // distinction stays meaningful across themes — Golden Hour
                  // remaps accent-green to amber, which would collide with
                  // the amber fallback color and erase the distinction.
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
                        onChange={e => setLoraWeight(filename, i, parseFloat(e.target.value))}
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
    </div>
  )
}
