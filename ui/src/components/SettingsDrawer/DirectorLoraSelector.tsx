import { useState, useEffect, useRef, useCallback } from 'react'
import { Search, X, Loader2, FolderOpen, Globe, Sparkles, BookOpen } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import { generateLoraGuide, fetchLoraGuide, fetchLoraDetails } from '../../api/client'
import { LoraGuideTooltip, LoraAgeChip, LoraSortToggle } from './LoraSelector'
import type { LoraDates } from './LoraSelector'
import { sortLoraNames } from './loraSort'
import type {
  DirectorImageRole,
  DirectorImageRoleLoraSelection,
  LoraInfo,
  LoraParameterDefinition,
  LoraParameterValue,
  LoraRecommendedWeights,
} from '../../types'

function serializeMultipliers(loras: string[], weights: Record<string, number[]>): string {
  return loras.map(name => {
    const values = weights[name] || [1.0]
    return values.map(value => value.toFixed(2)).join(';')
  }).join(' ')
}

function directorRoleLabel(role: DirectorImageRole): string {
  return role === 'creator' ? 'Creator' : 'Editor'
}

function updateDirectorRoleSelection(
  selections: readonly DirectorImageRoleLoraSelection[],
  id: string,
  update: Partial<DirectorImageRoleLoraSelection>,
): DirectorImageRoleLoraSelection[] {
  return selections.map(selection => selection.id === id
    ? { ...selection, ...update }
    : selection)
}

function DirectorRoleLoraParameterField({
  loraId, parameter, values, onChange, onClear,
}: {
  loraId: string
  parameter: LoraParameterDefinition
  values: Record<string, LoraParameterValue>
  onChange: (value: LoraParameterValue) => void
  onClear: () => void
}) {
  const value = api.getLoraParameterValue(parameter, values)
  const id = `director-${loraId}-${parameter.id}`.replace(/[^a-z0-9_-]/gi, '-')
  const label = `${parameter.label}${parameter.required ? ' (required)' : ''}`
  if (parameter.type === 'boolean') {
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <select id={id} value={value === true ? 'true' : value === false ? 'false' : ''} onChange={event => {
          if (event.target.value === '') onClear()
          else onChange(event.target.value === 'true')
        }} className="mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px]">
          <option value="">Choose…</option>
          <option value="false">No</option>
          <option value="true">Yes</option>
        </select>
      </label>
    )
  }
  if (parameter.type === 'enum') {
    const token = value === undefined ? '' : api.getLoraParameterOptionToken(value)
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <select id={id} value={token} onChange={event => {
          const option = parameter.options?.find(candidate => (
            api.getLoraParameterOptionToken(candidate.value) === event.target.value
          ))
          if (option) onChange(option.value)
        }} className="mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px]">
          {value === undefined && <option value="">Choose…</option>}
          {(parameter.options ?? []).map(option => (
            <option key={api.getLoraParameterOptionToken(option.value)} value={api.getLoraParameterOptionToken(option.value)}>{option.label}</option>
          ))}
        </select>
      </label>
    )
  }
  if (parameter.type === 'number' || parameter.type === 'integer') {
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <input id={id} type="number" value={typeof value === 'number' ? value : ''} min={parameter.minimum} max={parameter.maximum} step={parameter.step ?? (parameter.type === 'integer' ? 1 : 'any')} onChange={event => {
          if (Number.isFinite(event.target.valueAsNumber)) onChange(event.target.valueAsNumber)
        }} className="mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px]" />
      </label>
    )
  }
  return (
    <label htmlFor={id} className="block text-[9px] text-text-secondary">
      {label}
      <input id={id} type="text" value={typeof value === 'string' ? value : ''} minLength={parameter.min_length} maxLength={parameter.max_length} onChange={event => onChange(event.target.value)} className="mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px]" />
    </label>
  )
}

/** Role-scoped Director LoRAs use the exact model details catalog and wire. */
export function DirectorImageRoleLoraSelector({
  role, modelType, selections, onChange,
}: {
  role: DirectorImageRole
  modelType: string
  selections: DirectorImageRoleLoraSelection[]
  onChange: (selections: DirectorImageRoleLoraSelection[]) => void
}) {
  const openBrowser = useStore(s => s.setLoraBrowserOpen)
  const [catalog, setCatalog] = useState<LoraInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      if (!modelType) {
        setCatalog([])
        setLoading(false)
        return
      }
      setLoading(true)
      setError('')
      void api.fetchLoraDetails(modelType).then(result => {
        if (!cancelled) setCatalog(result.loras)
      }).catch(() => {
        if (!cancelled) {
          setCatalog([])
          setError(`${directorRoleLabel(role)} LoRA catalog is unavailable.`)
        }
      }).finally(() => {
        if (!cancelled) setLoading(false)
      })
    })
    return () => { cancelled = true }
  }, [modelType, role])

  const selectedIds = new Set(selections.map(selection => selection.id))
  const available = catalog.filter(lora => (
    !selectedIds.has(lora.filename)
    && lora.filename.toLowerCase().includes(search.trim().toLowerCase())
  ))
  const errors = api.validateDirectorImageRoleLoraSelections(selections, catalog)

  const setParameter = (selection: DirectorImageRoleLoraSelection, parameterId: string, value: LoraParameterValue) => {
    onChange(updateDirectorRoleSelection(selections, selection.id, {
      parameter_values: { ...(selection.parameter_values ?? {}), [parameterId]: value },
    }))
  }

  const clearParameter = (selection: DirectorImageRoleLoraSelection, parameterId: string) => {
    const next = { ...(selection.parameter_values ?? {}) }
    delete next[parameterId]
    onChange(updateDirectorRoleSelection(selections, selection.id, { parameter_values: next }))
  }

  return (
    <div className="space-y-2">
      <p className="text-[9px] leading-relaxed text-text-muted">
        Only LoRAs found for the selected {role === 'creator' ? 'creator' : 'continuity editor'} model are listed, including read-only linked roots. Published inputs are sealed to their current schema.
      </p>
      {loading ? (
        <div role="status" className="flex items-center gap-1.5 text-[10px] text-text-muted"><Loader2 size={11} className="animate-spin" /> Loading role LoRAs…</div>
      ) : (
        <>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-1.5">
            <div className="relative">
              <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted" />
              <input aria-label={`Search ${directorRoleLabel(role)} LoRAs`} value={search} onChange={event => setSearch(event.target.value)} placeholder="Search compatible LoRAs" className="w-full rounded border border-border bg-bg-tertiary py-1 pl-6 pr-2 text-[10px]" />
            </div>
            <button type="button" onClick={() => openBrowser(true, modelType)} className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[9px] text-accent-blue"><Globe size={10} /> Browse</button>
          </div>
          <div className="max-h-28 overflow-y-auto rounded border border-border bg-bg-tertiary">
            {available.slice(0, 100).map(lora => (
              <button key={lora.filename} type="button" disabled={selections.length >= 64} onClick={() => onChange([...selections, api.createDirectorImageRoleLoraSelection(lora)])} className="flex w-full items-center justify-between gap-2 border-b border-border/40 px-2 py-1 text-left text-[10px] text-text-secondary last:border-b-0 hover:bg-bg-hover disabled:opacity-40">
                <span className="truncate">{lora.filename.replace(/\.(safetensors|sft)$/i, '')}</span>
                <span className="shrink-0 text-[8px] text-text-muted">{lora.parameter_schema ? 'Inputs' : 'Strength'}</span>
              </button>
            ))}
            {available.length === 0 && <p className="px-2 py-1.5 text-center text-[9px] text-text-muted">{catalog.length === 0 ? 'No compatible LoRAs found' : 'No matches'}</p>}
          </div>
        </>
      )}
      {selections.map(selection => {
        const info = catalog.find(lora => lora.filename === selection.id)
        const schema = info?.parameter_schema
        const selectionErrors = errors.filter(message => message.startsWith(`${selection.id}:`))
        return (
          <div key={selection.id} className="rounded border border-border bg-bg-tertiary/50 p-2">
            <div className="grid grid-cols-[minmax(0,1fr)_4.5rem_auto] items-center gap-1.5">
              <span className="truncate text-[10px] text-text-secondary" title={selection.id}>{selection.id}</span>
              <input aria-label={`${selection.id} multiplier`} type="number" min={-10} max={10} step={0.05} value={selection.multiplier} onChange={event => {
                if (Number.isFinite(event.target.valueAsNumber)) onChange(updateDirectorRoleSelection(selections, selection.id, { multiplier: event.target.valueAsNumber }))
              }} className="w-full rounded border border-border bg-bg-primary px-1 py-0.5 text-right text-[9px]" />
              <button type="button" aria-label={`Remove ${selection.id}`} onClick={() => onChange(selections.filter(candidate => candidate.id !== selection.id))} className="rounded p-0.5 text-text-muted hover:text-red-300"><X size={11} /></button>
            </div>
            {schema && selection.parameter_schema_digest === schema.schema_digest && (
              <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {schema.parameters.map(parameter => (
                  <DirectorRoleLoraParameterField key={parameter.id} loraId={selection.id} parameter={parameter} values={selection.parameter_values ?? {}} onChange={value => setParameter(selection, parameter.id, value)} onClear={() => clearParameter(selection, parameter.id)} />
                ))}
              </div>
            )}
            {selectionErrors.map(message => <p key={message} role="status" className="mt-1 text-[8px] text-red-300">{message.slice(selection.id.length + 2)}</p>)}
          </div>
        )
      })}
      {error && <p role="status" className="text-[9px] text-red-300">{error}</p>}
      {selections.length >= 64 && <p role="status" className="text-[9px] text-amber-200">Maximum 64 LoRAs for this role.</p>}
    </div>
  )
}

/**
 * Compact preset picker for Director mode LoRA sections.
 */
function DirectorPresetPicker({ mode, modelType }: {
  mode: 'image' | 'video'
  modelType: string
}) {
  const presets = useStore(s => s.presets)
  const loadPresets = useStore(s => s.loadPresets)
  const directorSetLora = useStore(s => s.directorSetLora)
  const savedLora = useStore(s => s.savedLoraPerMode[mode])

  useEffect(() => { loadPresets() }, [loadPresets])

  const modePresets = presets.filter(p =>
    p.mode === mode
    && p.model_type === modelType
    && p.activated_loras.length > 0
  )

  if (modePresets.length === 0) return null

  const applyPreset = (preset: typeof modePresets[0]) => {
    directorSetLora(
      mode,
      preset.activated_loras,
      preset.loras_multipliers,
      preset.lora_weights || {},
      savedLora?.availableLoras || [],
    )
  }

  return (
    <div className="mb-2">
      <label className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Presets</label>
      <div className="flex flex-wrap gap-1">
        {modePresets.map(p => (
          <button
            key={p.id}
            onClick={() => applyPreset(p)}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] border border-border text-text-secondary hover:bg-bg-hover hover:text-text-primary hover:border-accent-blue transition-colors"
            title={`${p.activated_loras.length} LoRA(s): ${p.activated_loras.map(l => l.replace(/\.(safetensors|sft)$/i, '')).join(', ')}`}
          >
            <FolderOpen size={9} className="shrink-0" />
            <span className="truncate max-w-[100px]">{p.name}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

/**
 * Standalone LoRA selector for Director mode with recommended weight zones,
 * guide indicators, CivitAI browser trigger, and auto-apply defaults.
 */
export function DirectorLoraSelector({ mode, modelType }: {
  mode: 'image' | 'video'
  modelType: string
}) {
  const savedLora = useStore(s => s.savedLoraPerMode[mode])
  const directorSetLora = useStore(s => s.directorSetLora)
  const openBrowser = useStore(s => s.setLoraBrowserOpen)

  const [availableLoras, setAvailableLoras] = useState<string[]>(savedLora?.availableLoras || [])
  const [activatedLoras, setActivatedLoras] = useState<string[]>(savedLora?.activated_loras || [])
  const [loraWeights, setLoraWeights] = useState<Record<string, number[]>>(savedLora?.loraWeights || {})
  const [phases, setPhases] = useState(1)
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [loraWeightRecs, setLoraWeightRecs] = useState<Record<string, LoraRecommendedWeights>>({})
  const [guideStatus, setGuideStatus] = useState<Record<string, 'none' | 'exists' | 'generating' | 'done'>>({})
  const [guideTexts, setGuideTexts] = useState<Record<string, string>>({})
  const [loraDates, setLoraDates] = useState<Record<string, LoraDates>>({})
  const loraDetailsRequest = useRef(0)
  // Sticky list order shared with the Studio picker via the store.
  const sortMode = useStore(s => s.loraPickerSort)
  const setSortSticky = useStore(s => s.setLoraPickerSort)

  const persist = useCallback((newLoras: string[], newWeights: Record<string, number[]>) => {
    const multipliers = serializeMultipliers(newLoras, newWeights)
    directorSetLora(mode, newLoras, multipliers, newWeights, availableLoras)
  }, [mode, availableLoras, directorSetLora])

  const updateWeight = useCallback((filename: string, phaseIndex: number, value: number) => {
    if (!Number.isFinite(value)) return
    const safeValue = Math.round(Math.max(0, Math.min(2, value)) * 100) / 100
    setLoraWeights(prev => {
      const next = { ...prev }
      if (!next[filename]) return prev
      next[filename] = [...next[filename]]
      next[filename][phaseIndex] = safeValue
      persist(activatedLoras, next)
      return next
    })
  }, [activatedLoras, persist])

  // Load available LoRAs when model changes
  useEffect(() => {
    if (!modelType) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setLoading(true)
      api.fetchLoras(modelType).then(data => {
        if (cancelled) return
        const newPhases = data.guidance_max_phases ?? 1
        setAvailableLoras(data.loras)
        setPhases(newPhases)
        setActivatedLoras(prev => {
          const valid = prev.filter(l => data.loras.includes(l))
          const adjustedWeights: Record<string, number[]> = {}
          valid.forEach(l => {
            const existing = loraWeights[l] || Array(newPhases).fill(1.0)
            if (existing.length < newPhases) {
              adjustedWeights[l] = [...existing, ...Array(newPhases - existing.length).fill(1.0)]
            } else {
              adjustedWeights[l] = existing.slice(0, newPhases)
            }
          })
          if (valid.length !== prev.length || newPhases !== (loraWeights[valid[0]]?.length ?? 1)) {
            const multipliers = serializeMultipliers(valid, adjustedWeights)
            directorSetLora(mode, valid, multipliers, adjustedWeights, data.loras)
          }
          setLoraWeights(adjustedWeights)
          return valid
        })
        setLoading(false)
      }).catch(() => {
        if (!cancelled) {
          setAvailableLoras([])
          setLoading(false)
        }
      })
    })
    return () => { cancelled = true }
  }, [modelType]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load weight recommendations and guide status
  useEffect(() => {
    if (!modelType) return
    const detailsRequest = ++loraDetailsRequest.current
    fetchLoraDetails(modelType).then(r => {
      if (detailsRequest !== loraDetailsRequest.current) return
      const recs: Record<string, LoraRecommendedWeights> = {}
      const guides: Record<string, string> = {}
      const statuses: Record<string, 'exists' | 'none'> = {}
      const dates: Record<string, LoraDates> = {}
      for (const info of r.loras) {
        if (info.recommended_weights) recs[info.filename] = info.recommended_weights
        if (info.guide) { guides[info.filename] = info.guide; statuses[info.filename] = 'exists' }
        else if (info.has_guide) statuses[info.filename] = 'exists'
        if (info.released_at || info.downloaded_at) {
          dates[info.filename] = { released: info.released_at, downloaded: info.downloaded_at }
        }
      }
      setLoraWeightRecs(recs)
      setGuideTexts(prev => ({ ...prev, ...guides }))
      setGuideStatus(prev => ({ ...prev, ...statuses }))
      setLoraDates(dates)

      // Auto-apply recommended defaults to newly activated LoRAs at 1.0 fill
      for (const lora of activatedLoras) {
        const rec = recs[lora]
        if (!rec) continue
        const currentWeights = loraWeights[lora]
        if (!currentWeights || !currentWeights.every(w => w === 1.0)) continue
        const newWeights = currentWeights.map((_, i) => {
          const phaseRec = rec.phases?.find(p => p.phase === i + 1)
          const d = phaseRec?.default ?? rec.default
          const min = phaseRec?.min ?? rec.min
          const max = phaseRec?.max ?? rec.max
          if (d != null && d >= min && d <= max) return d
          if (min != null && max != null) return Math.round(((min + max) / 2) * 20) / 20
          return d ?? 0.8
        })
        for (let i = 0; i < newWeights.length; i++) {
          updateWeight(lora, i, newWeights[i])
        }
      }
    }).catch(error => {
      if (detailsRequest !== loraDetailsRequest.current) return
      console.error('Could not load Director LoRA details:', error)
    })

    // Check guide status for activated LoRAs
    for (const lora of activatedLoras) {
      if (guideStatus[lora]) continue
      fetchLoraGuide(modelType, lora).then(r => {
        setGuideStatus(s => ({ ...s, [lora]: r.guide ? 'exists' : 'none' }))
      }).catch(() => {})
    }
  }, [modelType, activatedLoras]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync from store when savedLora changes externally
  useEffect(() => {
    if (!savedLora) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setActivatedLoras(savedLora.activated_loras || [])
      setLoraWeights(savedLora.loraWeights || {})
      if (savedLora.availableLoras?.length) setAvailableLoras(savedLora.availableLoras)
    })
    return () => { cancelled = true }
  }, [savedLora])

  const toggleLora = useCallback((filename: string) => {
    setActivatedLoras(prev => {
      const idx = prev.indexOf(filename)
      const newWeights = { ...loraWeights }
      let next: string[]
      if (idx >= 0) {
        next = prev.filter((_, i) => i !== idx)
        delete newWeights[filename]
      } else {
        next = [...prev, filename]
        // Apply recommended default or fallback
        const rec = loraWeightRecs[filename]
        const defaultWeight = rec?.default ?? 0.8
        newWeights[filename] = Array(phases).fill(defaultWeight)
        if (rec?.phases) {
          newWeights[filename] = newWeights[filename].map((_, i) => {
            const pr = rec.phases?.find(p => p.phase === i + 1)
            return pr?.default ?? rec.default ?? 0.8
          })
        }
      }
      setLoraWeights(newWeights)
      persist(next, newWeights)
      return next
    })
  }, [loraWeights, phases, persist, loraWeightRecs])

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

  const clearAll = () => {
    setActivatedLoras([])
    setLoraWeights({})
    persist([], {})
  }

  const displayName = (filename: string) =>
    filename.replace(/\.(safetensors|sft)$/i, '')

  const filtered = sortLoraNames(
    availableLoras.filter(name =>
      displayName(name).toLowerCase().includes(search.toLowerCase())
    ),
    sortMode,
    loraDates,
  )

  if (loading) {
    return (
      <div className="text-xs text-text-muted bg-bg-tertiary border border-border rounded-lg px-3 py-3 text-center flex items-center justify-center gap-2">
        <Loader2 size={12} className="animate-spin" />
        Loading LoRAs...
      </div>
    )
  }

  if (availableLoras.length === 0) {
    return (
      <div className="flex items-center justify-between">
        <div className="text-xs text-text-muted">No LoRAs found</div>
        <button
          onClick={() => openBrowser(true, modelType)}
          className="text-[10px] text-accent-blue hover:text-accent-blue-hover flex items-center gap-0.5"
        >
          <Globe size={10} /> Browse
        </button>
      </div>
    )
  }

  return (
    <div>
      {/* Preset picker */}
      <DirectorPresetPicker mode={mode} modelType={modelType} />

      {/* Header with Browse */}
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-[10px] text-text-muted uppercase tracking-wider">LoRAs</label>
        <div className="flex items-center gap-2">
          <LoraSortToggle sort={sortMode} onChange={setSortSticky} />
          <button
            onClick={() => openBrowser(true, modelType)}
            className="text-[10px] text-accent-blue hover:text-accent-blue-hover flex items-center gap-0.5 transition-colors"
            title="Browse CivitAI"
          >
            <Globe size={10} /> Browse
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-2">
        <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search LoRAs..."
          className="w-full bg-bg-tertiary border border-border rounded-lg pl-7 pr-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
        />
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
                  // Functional indicator tokens, not accent-green: Golden
                  // Hour remaps accent-green to amber, which made every
                  // CivitAI dot (and weight zone) look like the fallback
                  // orange. Mirrors LoraSelector.
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    loraWeightRecs[filename].source === 'civitai' ? 'bg-indicator-success' : 'bg-indicator-warning'
                  }`}
                  title={loraWeightRecs[filename].source === 'civitai' ? 'CivitAI recommended settings' : 'Default settings'}
                />
              )}
            </button>
          )
        })}
        {filtered.length === 0 && (
          <div className="px-3 py-2 text-xs text-text-muted text-center">No matches</div>
        )}
      </div>

      {/* Selected LoRAs with weight sliders */}
      {activatedLoras.length > 0 && (
        <div className="mt-2 space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="text-[10px] text-text-muted uppercase tracking-wider">
              Selected ({activatedLoras.length})
            </div>
            <button
              onClick={clearAll}
              className="text-[10px] text-text-muted hover:text-red-400 transition-colors"
            >
              Clear all
            </button>
          </div>
          {activatedLoras.map(filename => {
            const weights = loraWeights[filename] || Array(phases).fill(1.0)
            return (
              <div key={filename} className="bg-bg-tertiary border border-border rounded-lg px-2.5 py-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-text-primary truncate flex-1 mr-2">
                    {displayName(filename)}
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
                  const fallbackMin = 0.6, fallbackMax = 1.0
                  const recMin = phaseRec?.min ?? rec?.min ?? fallbackMin
                  const recMax = phaseRec?.max ?? rec?.max ?? fallbackMax
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
                          title={`${isCivitai ? 'CivitAI' : 'Default'}: ${recMin}-${recMax}`}
                        />
                        <input
                          type="range"
                          aria-label={`${filename} phase ${i + 1} weight`}
                          min={0}
                          max={sliderMax}
                          step={0.05}
                          value={w}
                          onChange={e => updateWeight(filename, i, parseFloat(e.target.value))}
                          className="w-full relative z-10"
                        />
                      </div>
                      <input
                        type="number"
                        aria-label={`${filename} phase ${i + 1} numeric weight`}
                        min={0}
                        max={sliderMax}
                        step={0.05}
                        value={w}
                        onChange={e => updateWeight(filename, i, Number(e.target.value))}
                        className={`w-12 shrink-0 rounded border border-border bg-bg-tertiary px-1 py-0.5 text-right text-[10px] tabular-nums outline-none focus:border-accent-blue ${valueColor}`}
                      />
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
