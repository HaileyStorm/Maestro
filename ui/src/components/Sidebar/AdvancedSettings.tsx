import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X, Save, Trash2, FolderOpen, SlidersHorizontal } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import { PostProcessing } from './PostProcessing'
import { ControlVideoSection } from './ControlVideoSection'
import { LoraSelector } from '../SettingsDrawer/LoraSelector'
import { WindowSettings } from './DurationSlider'
import {
  fetchH3AccelerationStatus,
  fetchH3BenchmarkReport,
  type H3AccelerationStatus,
  type H3BenchmarkRecord,
  type H3BenchmarkReport,
} from '../../api/client'

interface H3CustomSettings {
  h3_attention_engine?: 'sdpa' | 'sol_attn' | 'sage2'
  h3_sol_tau?: number
  h3_sol_dense_steps?: number
}

function benchmarkEngineLabel(record: H3BenchmarkRecord): string {
  const requested = record.spec.engine.id
  const effective = record.spec.engine.effective_id || requested
  return effective === requested ? effective : `${requested} → ${effective}`
}

function customSettingInputValue(
  settings: Record<string, unknown> | undefined,
  settingId: string,
): string {
  return String(settings?.[settingId] ?? '')
}

function withNumericCustomSetting(
  settings: Record<string, unknown> | undefined,
  settingId: string,
  input: string,
): Record<string, unknown> | undefined {
  const next = { ...(settings || {}) }
  if (input === '') delete next[settingId]
  else next[settingId] = Number.parseFloat(input)
  return Object.keys(next).length > 0 ? next : undefined
}

function PresetManager() {
  const presets = useStore(s => s.presets)
  const loadPresets = useStore(s => s.loadPresets)
  const savePreset = useStore(s => s.savePreset)
  const loadPresetFn = useStore(s => s.loadPreset)
  const deletePreset = useStore(s => s.deletePreset)
  const generationMode = useStore(s => s.generationMode)
  const currentModel = useStore(s => s.params.model_type)
  const [saveName, setSaveName] = useState('')
  const [showSave, setShowSave] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveNotice, setSaveNotice] = useState<{
    kind: 'success' | 'error'
    text: string
  } | null>(null)
  const saveInFlight = useRef(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  useEffect(() => { loadPresets() }, [loadPresets])

  const modePresets = presets.filter(p => p.mode === generationMode && p.model_type === currentModel)

  const handleSave = async () => {
    const name = saveName.trim()
    if (!name || saveInFlight.current) return
    saveInFlight.current = true
    setSaving(true)
    setSaveNotice(null)
    try {
      await savePreset(name)
      setSaveName('')
      setShowSave(false)
      setSaveNotice({ kind: 'success', text: 'Preset saved.' })
    } catch {
      setSaveNotice({
        kind: 'error',
        text: 'Preset save could not be confirmed. Check your connection and try again.',
      })
    } finally {
      saveInFlight.current = false
      setSaving(false)
    }
  }

  const handleDelete = (id: string) => {
    if (confirmDelete === id) {
      deletePreset(id)
      setConfirmDelete(null)
    } else {
      setConfirmDelete(id)
      setTimeout(() => setConfirmDelete(null), 3000)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[11px] text-text-muted uppercase tracking-wider">Presets</span>
        <button
          type="button"
          onClick={() => {
            const opening = !showSave
            setShowSave(opening)
            if (opening) setSaveNotice(null)
          }}
          disabled={saving}
          aria-expanded={showSave}
          aria-controls="advanced-preset-save-form"
          className="mobile-control-target text-[10px] text-accent-blue hover:text-accent-blue-hover flex items-center gap-0.5 disabled:cursor-wait disabled:opacity-60"
        >
          <Save aria-hidden="true" size={10} /> Save Current
        </button>
      </div>

      {showSave && (
        <div id="advanced-preset-save-form" className="flex gap-1.5 mb-2">
          <input
            type="text"
            aria-label="Preset name"
            value={saveName}
            onChange={e => setSaveName(e.target.value)}
            onKeyDown={e => {
              if (e.key !== 'Enter') return
              e.preventDefault()
              void handleSave()
            }}
            disabled={saving}
            placeholder="Preset name..."
            className="mobile-control-target min-w-0 flex-1 bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue"
            autoFocus
          />
          <button
            type="button"
            onClick={() => { void handleSave() }}
            disabled={!saveName.trim() || saving}
            aria-busy={saving}
            className="mobile-control-target px-2 py-1 text-xs bg-accent-blue text-white rounded hover:bg-accent-blue-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      )}

      <p
        role="status"
        aria-live="polite"
        className={`mb-1.5 min-h-4 text-[10px] ${
          saveNotice?.kind === 'error' ? 'text-red-400' : 'text-text-muted'
        }`}
      >
        {saveNotice?.text || ''}
      </p>

      {modePresets.length > 0 ? (
        <div className="space-y-1 max-h-[120px] overflow-y-auto">
          {modePresets.map(p => (
            <div key={p.id} className="flex items-center gap-1.5 group">
              <button
                type="button"
                onClick={() => loadPresetFn(p)}
                className="mobile-control-target flex-1 text-left px-2 py-1.5 rounded text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors truncate flex items-center gap-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                title={`${p.name}\n${p.activated_loras.length} LoRA(s) - ${p.model_type}`}
              >
                <FolderOpen size={10} className="shrink-0 text-text-muted" />
                <span className="truncate">{p.name}</span>
              </button>
              <button
                type="button"
                onClick={() => handleDelete(p.id)}
                aria-label={`${confirmDelete === p.id ? 'Confirm delete' : 'Delete'} preset ${p.name}`}
                className={`mobile-control-target flex shrink-0 items-center justify-center rounded p-1 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
                  confirmDelete === p.id
                    ? 'text-red-400 bg-red-500/20'
                    : 'text-text-muted opacity-100 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100 focus-visible:opacity-100 hover:text-red-400'
                }`}
              >
                <Trash2 aria-hidden="true" size={10} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[10px] text-text-muted">No {generationMode} presets for this model</p>
      )}
    </div>
  )
}

/** Active advanced features as human-readable labels. Drives the badge
 *  count AND its hover tooltip, so a surprising number names its source
 *  instead of sending the user hunting through every section. */
function useAdvancedActiveItems(): string[] {
  const params = useStore(s => s.params)
  const modelOptions = useStore(s => s.modelOptions)
  const spatialUpsampling = useStore(s => s.spatialUpsampling)
  const filmGrainIntensity = useStore(s => s.filmGrainIntensity)
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const isScailEdit = (
    generationMode === 'avatar'
    && (editSubMode === 'recast' || editSubMode === 'restyle')
  )
  const isScailHq = isScailEdit && params.model_type === 'scail2_14B'

  const items: string[] = []
  if (params.seed !== -1) items.push(`Seed ${params.seed}`)
  if (
    (params.negative_prompt?.length ?? 0) > 0
    && (!isScailEdit || isScailHq)
  ) items.push('Negative prompt')
  for (const l of params.activated_loras) items.push(`LoRA: ${l.replace(/\.(safetensors|sft)$/i, '')}`)
  if (!isScailEdit && spatialUpsampling) items.push(`Upscaling (${spatialUpsampling})`)
  if (!isScailEdit && filmGrainIntensity > 0) items.push('Film grain')
  if (!isScailEdit && (params.self_refiner_setting ?? 0) > 0) items.push('Self refiner')
  if ((params.custom_settings as H3CustomSettings | undefined)?.h3_attention_engine === 'sol_attn') {
    items.push('H3 Sol-Attn (approximate)')
  } else if ((params.custom_settings as H3CustomSettings | undefined)?.h3_attention_engine === 'sage2') {
    items.push('H3 SageAttention2++')
  } else if ((params.custom_settings as H3CustomSettings | undefined)?.h3_attention_engine === 'sdpa') {
    items.push('H3 Dense SDPA')
  }
  // injection_strength only matters when injected frames actually exist.
  // The persisted snapshot strips image_refs (file paths are ephemeral)
  // but kept the strength value — counting it alone produced a ghost
  // badge with nothing visibly active in the panel.
  const refCount = Array.isArray(params.image_refs) ? params.image_refs.length : (params.image_refs ? 1 : 0)
  if (
    !isScailEdit
    && params.injection_strength != null
    && params.injection_strength !== 1.0
    && refCount > 0
  ) items.push('Injection strength')
  // Process letter codes persist by design (the dropdown remembers the
  // user's choice across sessions), but their REQUIRED inputs are
  // ephemeral and stripped from persistence: frames injection ("F")
  // needs image refs, control-video letters ("V") need a guide file.
  // A remembered choice with no input does nothing at generation time,
  // so it must not count — this was the refresh-surviving ghost. Strip only
  // a TRAILING "T" (the extend-alignment flag); an internal "T" is a real
  // process letter (depth_temporal: TVG/PTVG/TEVG) and must survive.
  const vptVisible = (params.video_prompt_type || '').replace(/T$/, '')
  if (!isScailEdit && modelOptions?.guide_custom_choices && vptVisible) {
    const effective = vptVisible.includes('F')
      ? refCount > 0
      : vptVisible.includes('V')
        ? !!params.video_guide
        : true
    if (effective) items.push(`Process: ${vptVisible}`)
  }
  return items
}

export function AdvancedSettings() {
  const [open, setOpen] = useState(false)
  const closeDrawer = useCallback(() => setOpen(false), [])
  const params = useStore(s => s.params)
  const setParam = useStore(s => s.setParam)
  const modelOptions = useStore(s => s.modelOptions)
  const hostMaxSteps = Number(
    (useStore(s => s.h3CurrentEstimate) as { host_max_steps?: number } | null)
      ?.host_max_steps,
  )
  const inferenceStepCeiling = (
    Number.isFinite(hostMaxSteps) && hostMaxSteps >= 2
      ? Math.min(50, Math.trunc(hostMaxSteps))
      : 50
  )
  useEffect(() => {
    if (params.num_inference_steps > inferenceStepCeiling) {
      setParam('num_inference_steps', inferenceStepCeiling)
    }
  }, [inferenceStepCeiling, params.num_inference_steps, setParam])
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const audioSubMode = useStore(s => s.audioSubMode)
  const openQueueAfterSubmit = useStore(s => s.openQueueAfterSubmit)
  const setOpenQueueAfterSubmit = useStore(s => s.setOpenQueueAfterSubmit)
  const isAudio = generationMode === 'audio'
  const isSfx = isAudio && audioSubMode === 'sfx'
  const isAudioOnly = modelOptions?.audio_only || isSfx
  const isVideo = generationMode === 'video'
  const isAvatar = generationMode === 'avatar'
  const isOutpaint = isAvatar && editSubMode === 'outpaint'
  const isRecast = isAvatar && editSubMode === 'recast'
  const isRepaint = isAvatar && editSubMode === 'restyle'
  const isScailEdit = isRecast || isRepaint
  const scailModelType = String(params.model_type || '')
  const isScailFast = (
    isScailEdit
    && (
      scailModelType === 'scail2_14B_fast'
      || scailModelType === 'scail2_14B_recast_fast'
    )
  )
  const isScailHq = isScailEdit && scailModelType === 'scail2_14B'
  // Studio must always expose the primary denoise count. Distilled models
  // previously hid it behind lock_inference_steps while still showing only
  // Stage 2/3 refinement counts, which made the Stage 1 schedule invisible.
  // Unlocked models allow a 1..50 override; locked/distilled schedules stay
  // read-only but visible so Stage 2/3 never appear to replace Stage 1.
  const showInferenceSteps = !isAudioOnly
  const showGuidanceScale = (
    !isAudioOnly
    && (
      isScailEdit
        ? isScailHq
        : !modelOptions?.lock_guidance_scale
    )
  )
  const showNegativePrompt = (
    !modelOptions?.no_negative_prompt
    && (!isScailEdit || isScailHq)
  )
  const hasStartImage = useStore(s => !!(s.startImage || s.params.image_start))
  const hasEndImage = useStore(s => !!(s.endImage || s.params.image_end))
  const hasImageRefs = useStore(s => {
    const refs = s.params.image_refs
    return refs && refs.length > 0
  })
  const durationSeconds = useStore(s => s.durationSeconds)
  const setDurationSeconds = useStore(s => s.setDurationSeconds)
  const selectModel = useStore(s => s.selectModel)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const advancedItems = useAdvancedActiveItems()
  const advancedCount = advancedItems.length
  const isH3 = ['minimax_h3', 'minimax_h3_ref2va'].includes(
    String(modelOptions?.architecture || ''),
  )
  const minimumInferenceSteps = isH3 ? 2 : 1
  const [h3Acceleration, setH3Acceleration] = useState<H3AccelerationStatus | null>(null)
  const [h3Benchmark, setH3Benchmark] = useState<H3BenchmarkReport | null>(null)
  const h3Custom = (params.custom_settings || {}) as H3CustomSettings
  const h3Engine = String(h3Custom.h3_attention_engine || 'sol_attn')
  const setH3Custom = <Key extends keyof H3CustomSettings>(
    key: Key,
    value: H3CustomSettings[Key],
  ) => {
    const next = { ...(params.custom_settings || {}) } as Record<string, unknown>
    if (value === undefined) delete next[key]
    else next[key] = value
    setParam('custom_settings', Object.keys(next).length ? next : undefined)
  }

  useEffect(() => {
    if (!open || !isH3) return
    let current = true
    fetchH3AccelerationStatus(false)
      .then(status => { if (current) setH3Acceleration(status) })
      .catch(() => { if (current) setH3Acceleration(null) })
    fetchH3BenchmarkReport()
      .then(report => { if (current) setH3Benchmark(report) })
      .catch(() => { if (current) setH3Benchmark(null) })
    return () => { current = false }
  }, [open, isH3])

  // The drawer is portalled outside #root so the shared modal controller can
  // inert the rest of the app without also disabling the open drawer.
  useEffect(() => {
    if (!open || !panelRef.current || !closeRef.current) return
    const nativeControls = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
      'input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    ))
    const annotatedControls = nativeControls.filter(control => !control.hasAttribute('tabindex'))
    for (const control of annotatedControls) control.setAttribute('tabindex', '0')
    const uninstall = installModalFocus({
      document,
      dialog: panelRef.current,
      initialFocus: closeRef.current,
      restoreFocus: triggerRef.current,
      appRoot: document.getElementById('root'),
      onClose: closeDrawer,
      priority: 80,
    })
    return () => {
      for (const control of annotatedControls) control.removeAttribute('tabindex')
      uninstall()
    }
  }, [closeDrawer, open])

  return (
    <>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        aria-controls="advanced-settings-drawer"
        aria-expanded={open}
        aria-label={open ? 'Close Advanced Settings' : 'Open Advanced Settings'}
        onClick={() => setOpen(current => !current)}
        className={`mobile-control-target flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-colors border focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
          open ? 'border-accent-blue text-accent-blue' : 'border-border text-text-secondary hover:text-text-primary hover:border-border-light'
        }`}
      >
        <SlidersHorizontal size={13} />
        <span className="hidden md:inline">Advanced</span>
        {advancedCount > 0 && (
          <span
            title={advancedItems.join('\n')}
            className="min-w-[16px] h-4 rounded-full bg-accent-blue text-white text-[9px] font-bold flex items-center justify-center px-1"
          >
            {advancedCount}
          </span>
        )}
      </button>

      {/* Popup overlay — always mounted to preserve state (frames injection, etc.) */}
      {createPortal(
        <>
          {open && (
            <button
              type="button"
              tabIndex={-1}
              aria-label="Close Advanced Settings"
              className="fixed inset-0 z-[70] appearance-none border-0 bg-black/30 p-0"
              onClick={() => closeModalIfTop(document, panelRef.current, closeDrawer)}
            />
          )}
          <div
            id="advanced-settings-drawer"
            ref={panelRef}
            role="dialog"
            aria-modal={open ? true : undefined}
            aria-labelledby="advanced-settings-title"
            aria-hidden={!open}
            inert={!open}
            className={`fixed top-0 h-[100vh] supports-[height:100dvh]:h-[100dvh] bg-bg-secondary border-r border-border z-[80] flex flex-col shadow-2xl overflow-hidden transition-transform duration-200 pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)]
              left-0 w-full md:left-[clamp(460px,24vw,560px)] md:w-[min(380px,calc(100vw-clamp(460px,24vw,560px)))] md:max-w-[90vw] ${
              open ? 'translate-x-0' : '-translate-x-full md:-translate-x-[100vw] pointer-events-none'
            }`}
          >
            {/* Header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
              <span id="advanced-settings-title" className="text-sm font-semibold text-text-primary">Advanced Settings</span>
              <button
                ref={closeRef}
                type="button"
                aria-label="Close Advanced Settings"
                onClick={closeDrawer}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-auto md:w-auto md:p-1"
              >
                <X aria-hidden="true" size={16} />
              </button>
            </div>

            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
              {/* Window Settings */}
              {(isVideo || (isAvatar && !isScailEdit))
                && modelOptions?.sliding_window
                && <WindowSettings />}

              {isH3 && (
                <div className="space-y-3 rounded-lg border border-border bg-bg-tertiary/35 p-3">
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-[11px] text-text-muted uppercase tracking-wider">H3 Performance</label>
                      <span className="text-[9px] text-text-muted">For this generation</span>
                    </div>
                    <p className="mt-1 text-[9px] text-text-muted">
                      Quality favors speed with Sol-Attn. Tested Base speed presets use SageAttention2++. Ultra favors accuracy with Dense SDPA.
                    </p>
                  </div>

                  <div>
                    <span id="h3-attention-engine-label" className="mb-1 block text-[10px] text-text-muted">Attention engine</span>
                    <select
                      id="h3-attention-engine"
                      aria-labelledby="h3-attention-engine-label"
                      value={h3Engine}
                      onChange={event => setH3Custom(
                        'h3_attention_engine',
                        event.target.value === 'sdpa'
                          ? 'sdpa'
                          : event.target.value === 'sage2' ? 'sage2' : 'sol_attn',
                      )}
                      className="mobile-control-target w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                    >
                      <option value="sdpa">Dense SDPA · most accurate</option>
                      <option value="sol_attn" disabled={h3Acceleration?.sol_attn.available === false}>
                        Kijai Sol-Attn · faster, small quality tradeoff
                      </option>
                      <option
                        value="sage2"
                        disabled={h3Acceleration?.sage2.available !== true || params.model_type !== 'minimax_h3'}
                      >
                        Official SageAttention2++ · {h3Acceleration?.sage2.validated ? 'tested for Base H3' : 'not yet tested'}
                      </option>
                    </select>
                    {h3Acceleration?.sol_attn.available === false && (
                      <p className="mt-1 text-[9px] text-amber-400">
                        Sol-Attn is unavailable: {h3Acceleration.sol_attn.error || (!h3Acceleration.sol_attn.hardware_ok ? 'it requires an NVIDIA SM80+ GPU with BF16 support' : 'the required Sol-Attn package is not installed')}.
                      </p>
                    )}
                    {h3Acceleration?.sage2.available !== true && (
                      <p className="mt-1 text-[9px] text-amber-400">
                        SageAttention2++ is unavailable: {h3Acceleration?.sage2.reason || 'it requires the official Linux CUDA 12.8+ package for SM120 GPUs'}.
                      </p>
                    )}
                    {h3Acceleration?.sage2.available === true && params.model_type !== 'minimax_h3' && (
                      <p className="mt-1 text-[9px] text-amber-400">
                        SageAttention2++ has only been tested with Base H3. Choose Base H3 to use it; W4A8, PinkCherry, and Ref2VA are not supported.
                      </p>
                    )}
                    {h3Engine === 'sage2' && (
                      <p className="mt-2 border-l border-amber-500/30 pl-2 text-[9px] text-amber-300">
                        Usually faster than Dense SDPA on the tested Base H3 profiles. If a run switches to SDPA, Continuum leaves it out of benchmark comparisons. Base Draft at 608×352 and Fast at 864×480 have been tested for video and audio; other H3 models have not.
                      </p>
                    )}
                    {h3Engine === 'sol_attn' && (
                      <div className="mt-2 space-y-2 border-l border-amber-500/30 pl-2">
                        <p className="text-[9px] text-amber-300">
                          Faster approximate attention with a small quality tradeoff. Opening steps and reference-image setup use Dense SDPA, and unsupported operations switch back to Dense SDPA automatically.
                        </p>
                        <div>
                          <div className="flex justify-between text-[10px] text-text-muted"><span>Routing tau</span><span>{Number(h3Custom.h3_sol_tau ?? 1).toFixed(1)}</span></div>
                          <input type="range" min={0.5} max={2.5} step={0.1} value={Number(h3Custom.h3_sol_tau ?? 1)} onChange={event => setH3Custom('h3_sol_tau', Number(event.target.value))} className="w-full" />
                        </div>
                        <div>
                          <div className="flex justify-between text-[10px] text-text-muted"><span>Dense warm-up steps</span><span>{Number(h3Custom.h3_sol_dense_steps ?? 10)}</span></div>
                          <input type="range" min={0} max={20} step={1} value={Number(h3Custom.h3_sol_dense_steps ?? 10)} onChange={event => setH3Custom('h3_sol_dense_steps', Number(event.target.value))} className="w-full" />
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="rounded border border-border/70 bg-bg-primary/40 p-2 text-[9px] text-text-muted">
                    <div className="font-medium text-text-secondary">Reference-image model</div>
                    <div className="mt-0.5">
                      {params.model_type === 'minimax_h3_pinkcherry_fl2va'
                        ? 'Heretic Qwen3-VL-32B INT8 ConvRot · explicit PinkCherry profile'
                        : 'Official Qwen3-VL-32B NVFP4-AWQ · base fidelity/performance profile'}
                    </div>
                    <div className="mt-1">Helps H3 follow reference images. It does not rewrite or enhance your prompt.</div>
                  </div>

                  <label className="flex items-start gap-2 text-[10px] text-text-muted">
                    <input
                      type="checkbox"
                      disabled={
                        h3Acceleration?.w4a8.available !== true
                        || !['minimax_h3', 'minimax_h3_w4a8_fl2va'].includes(params.model_type)
                      }
                      checked={params.model_type === 'minimax_h3_w4a8_fl2va'}
                      onChange={event => void selectModel(
                        event.target.checked ? 'minimax_h3_w4a8_fl2va' : 'minimax_h3',
                      )}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-text-secondary">Kijai W4A8 FL2VA transformer · experimental, may use less memory</span>
                      <span className="block text-[9px]">{h3Acceleration?.w4a8.reason || 'Checking merged W4A8 runtime…'} Compatible only with base FL2VA text/first/last-frame segments; PinkCherry and Ref2VA use their own weights.</span>
                    </span>
                  </label>

                  <p className="text-[9px] text-text-muted">
                    {h3Acceleration?.sol_attn.available ? 'Sol-Attn is available on this computer.' : 'Sol-Attn is unavailable, so generations use Dense SDPA.'} Published speed claims are not measurements from this computer.
                  </p>

                  <div className="space-y-2 border-t border-border pt-2">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="text-[10px] font-medium text-text-secondary">Benchmark on this computer</div>
                        <div className="text-[9px] text-text-muted">Each result is one measured run: 608×352 · 124 frames · 4 steps</div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setParam('resolution', '608x352')
                          setDurationSeconds(124 / 24)
                          setParam('video_length', 124)
                          setParam('num_inference_steps', 4)
                          setH3Custom('h3_sol_dense_steps', 0)
                        }}
                        className="mobile-control-target rounded border border-accent-blue/50 px-2 py-1 text-[9px] text-accent-blue hover:bg-accent-blue/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                      >
                        Apply benchmark settings
                      </button>
                    </div>
                    <p className="text-[9px] text-text-muted">
                      Successful H3 generations improve time estimates on this computer. Use these standard settings with text only, a first frame, first and last frames, or Ref2VA references for comparable results.
                    </p>
                    {(h3Benchmark?.records.length || 0) > 0 ? (
                      <div className="max-h-32 space-y-1 overflow-y-auto">
                        {h3Benchmark!.records.slice(-8).reverse().map(record => (
                          <div key={record.cache_key} className="grid grid-cols-[1fr_auto] gap-x-2 rounded bg-bg-primary/50 px-2 py-1 text-[9px]">
                            <span className="truncate text-text-secondary">{record.spec.case_id.replaceAll('_', ' ')} · {record.spec.model.id} · {benchmarkEngineLabel(record)}</span>
                            <span className="text-text-primary">{record.generation_wall_time_seconds.toFixed(1)}s · {record.effective_output_fps.toFixed(2)} fps</span>
                            <span className="text-text-muted">{record.spec.task.profile === 'quick' ? 'standard benchmark' : 'regular generation'}</span>
                            <span className="text-text-muted">{record.normalized_speed_index == null ? 'needs a Dense SDPA comparison' : `${record.normalized_speed_index.toFixed(0)} relative speed score`}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[9px] text-text-muted">No measurements from this computer yet. Published speed claims are shown separately and are not treated as local results.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TTS Settings */}
              {isAudioOnly && (
                <>
                  {/* Max Duration */}
                  {modelOptions?.duration_slider && (
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[11px] text-text-muted uppercase tracking-wider">
                          {modelOptions.duration_slider.label || 'Max Duration'}
                        </label>
                        <span className="text-xs text-text-secondary">{Math.round(durationSeconds)}s</span>
                      </div>
                      <input
                        type="range"
                        min={modelOptions.duration_slider.min} max={modelOptions.duration_slider.max} step={modelOptions.duration_slider.increment}
                        value={durationSeconds}
                        onChange={e => setDurationSeconds(parseFloat(e.target.value))}
                        className="w-full"
                      />
                    </div>
                  )}

                  {/* Speaker Pause */}
                  {modelOptions?.pause_between_sentences && (
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[11px] text-text-muted uppercase tracking-wider">Speaker Pause</label>
                        <span className="text-xs text-text-secondary">{(params.pause_seconds ?? 0.5).toFixed(2)}s</span>
                      </div>
                      <input
                        type="range" min={0} max={2} step={0.05}
                        value={params.pause_seconds ?? 0.5}
                        onChange={e => setParam('pause_seconds', parseFloat(e.target.value))}
                        className="w-full"
                      />
                    </div>
                  )}

                  {/* Temperature */}
                  {modelOptions?.temperature_enabled && (
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[11px] text-text-muted uppercase tracking-wider">Temperature</label>
                        <span className="text-xs text-text-secondary">{(params.temperature ?? 1.0).toFixed(2)}</span>
                      </div>
                      <input
                        type="range" min={0.1} max={1.5} step={0.01}
                        value={params.temperature ?? 1.0}
                        onChange={e => setParam('temperature', parseFloat(e.target.value))}
                        className="w-full"
                      />
                    </div>
                  )}

                  {/* Guidance Scale */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] text-text-muted uppercase tracking-wider">Guidance (CFG)</label>
                      <span className="text-xs text-text-secondary">{(params.guidance_scale ?? 3.0).toFixed(1)}</span>
                    </div>
                    <input
                      type="range" min={1} max={20} step={0.1}
                      value={params.guidance_scale ?? 3.0}
                      onChange={e => setParam('guidance_scale', parseFloat(e.target.value))}
                      className="w-full"
                    />
                  </div>

                  {/* Auto-Split */}
                  {modelOptions?.custom_settings_def?.map(setting => (
                    <div key={setting.id}>
                      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">{setting.name}</label>
                      <input
                        type="number"
                        placeholder="Empty = disabled"
                        value={customSettingInputValue(params.custom_settings, setting.id)}
                        onChange={e => {
                          const val = e.target.value.trim()
                          setParam('custom_settings', withNumericCustomSetting(
                            params.custom_settings,
                            setting.id,
                            val,
                          ))
                        }}
                        className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
                      />
                      <p className="text-[10px] text-text-muted mt-1">{setting.label}</p>
                    </div>
                  ))}

                  {/* Compressor Settings — shown when Smooth Speaker Volumes is enabled */}
                  {params.tts_dynaudnorm && (
                    <div className="space-y-3 p-2.5 bg-bg-tertiary/50 rounded-lg border border-border/50">
                      <label className="text-[10px] text-text-muted uppercase tracking-wider block">Speaker Transition Compressor</label>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Threshold</label>
                          <span className="text-[10px] text-text-secondary">{params.tts_comp_threshold || -25}dB</span>
                        </div>
                        <input type="range" min={-50} max={-10} step={1}
                          value={params.tts_comp_threshold || -25}
                          onChange={e => setParam('tts_comp_threshold', parseInt(e.target.value))}
                          className="w-full" />
                        <p className="text-[9px] text-text-muted">Volume level where boosting kicks in. Lower = catches quieter parts.</p>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Attack</label>
                          <span className="text-[10px] text-text-secondary">{params.tts_comp_attack || 5}ms</span>
                        </div>
                        <input type="range" min={1} max={50} step={1}
                          value={params.tts_comp_attack || 5}
                          onChange={e => setParam('tts_comp_attack', parseInt(e.target.value))}
                          className="w-full" />
                        <p className="text-[9px] text-text-muted">How fast the compressor reacts. Low = catches brief dips at speaker transitions.</p>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Release</label>
                          <span className="text-[10px] text-text-secondary">{params.tts_comp_release || 100}ms</span>
                        </div>
                        <input type="range" min={20} max={500} step={10}
                          value={params.tts_comp_release || 100}
                          onChange={e => setParam('tts_comp_release', parseInt(e.target.value))}
                          className="w-full" />
                        <p className="text-[9px] text-text-muted">How fast it returns to normal after boosting. Higher = smoother.</p>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Makeup Gain</label>
                          <span className="text-[10px] text-text-secondary">{params.tts_comp_makeup || 4}dB</span>
                        </div>
                        <input type="range" min={0} max={12} step={1}
                          value={params.tts_comp_makeup || 4}
                          onChange={e => setParam('tts_comp_makeup', parseInt(e.target.value))}
                          className="w-full" />
                        <p className="text-[9px] text-text-muted">How much to boost the quiet parts. Higher = louder transitions.</p>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Post Processing */}
              {!isAudio && !isScailEdit && <PostProcessing />}

              {/* Seed */}
              <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span id="advanced-seed-label" className="text-[11px] text-text-muted uppercase tracking-wider">Seed</span>
                    <button type="button" onClick={() => setParam('seed', -1)} className="mobile-control-target text-[10px] text-accent-blue hover:text-accent-blue-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
                      Random
                    </button>
                  </div>
                  <input
                    type="number"
                    id="advanced-seed"
                    aria-labelledby="advanced-seed-label"
                    value={params.seed}
                    onChange={e => setParam('seed', Number(e.target.value))}
                    className="mobile-control-target w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue"
                    placeholder="-1 for random"
                  />
              </div>

              {/* Self Refiner */}
              {!isScailEdit && Boolean(modelOptions?.self_refiner) ? (
                <div>
                  <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Self Refiner</label>
                  <select
                    value={params.self_refiner_setting ?? 0}
                    onChange={e => setParam('self_refiner_setting', Number(e.target.value))}
                    className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
                  >
                    <option value={0}>Disabled</option>
                    <option value={1}>Enabled with P1-Norm</option>
                    <option value={2}>Enabled with P2-Norm</option>
                  </select>
                </div>
              ) : null}

              {/* Stage 2 Steps */}
              {/* Pipeline Mode Toggle — distilled LTX models only */}
              {!isScailEdit && modelOptions?.lock_inference_steps && (
                <div className="space-y-3">
                  {/* Single / 2-Stage / 3-Stage segmented control — mutually exclusive */}
                  <div>
                    <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Pipeline Mode</label>
                    <div className="flex bg-bg-tertiary rounded-lg p-0.5 border border-border">
                      <button
                        onClick={() => { setParam('progressive_pipeline', false); setParam('single_stage_pipeline', true) }}
                        className={`flex-1 text-[10px] py-1.5 rounded-md transition-all ${
                          !!params.single_stage_pipeline && !params.progressive_pipeline
                            ? 'bg-bg-active text-text-primary'
                            : 'text-text-secondary hover:text-text-primary'
                        }`}
                        title="Run at full target resolution in one pass. No upscale, no refine. Higher VRAM."
                      >
                        Single
                      </button>
                      <button
                        onClick={() => { setParam('progressive_pipeline', false); setParam('single_stage_pipeline', false) }}
                        className={`flex-1 text-[10px] py-1.5 rounded-md transition-all ${
                          !params.progressive_pipeline && !params.single_stage_pipeline
                            ? 'bg-bg-active text-text-primary'
                            : 'text-text-secondary hover:text-text-primary'
                        }`}
                        title="Half-res denoise, then 2x spatial upscale + refine. Balanced speed/quality."
                      >
                        Standard (2-Stage)
                      </button>
                      <button
                        onClick={() => { setParam('progressive_pipeline', true); setParam('single_stage_pipeline', false) }}
                        className={`flex-1 text-[10px] py-1.5 rounded-md transition-all ${
                          params.progressive_pipeline
                            ? 'bg-bg-active text-text-primary'
                            : 'text-text-secondary hover:text-text-primary'
                        }`}
                        title="Progressive 1/4 → 1/2 → full. Smoother motion, slower."
                      >
                        Progressive (3-Stage)
                      </button>
                    </div>
                  </div>

                  {/* Single-Stage: no extra controls — stage 1 runs at full res */}
                  {!!params.single_stage_pipeline && !params.progressive_pipeline && (
                    <div className="text-[10px] text-text-muted px-1">
                      Runs the distilled denoise at full target resolution in one pass. No stage-2 upscale or refine.
                      Uses ~4× the stage-1 VRAM of 2-Stage mode; drop to a smaller resolution preset if you OOM.
                    </div>
                  )}

                  {/* Standard 2-Stage: Stage 2 steps only */}
                  {!params.progressive_pipeline && !params.single_stage_pipeline && (
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[11px] text-text-muted uppercase tracking-wider">Stage 2 Steps</label>
                        <span className="text-xs text-text-secondary">{params.stage2_steps || 3}</span>
                      </div>
                      <input
                        type="range" min={2} max={7} step={1}
                        value={params.stage2_steps || 3}
                        onChange={e => setParam('stage2_steps', Number(e.target.value))}
                        className="w-full accent-accent-blue"
                      />
                      <div className="flex justify-between text-[10px] text-text-muted mt-0.5">
                        <span>2 (faster)</span><span>7 (more detail)</span>
                      </div>
                    </div>
                  )}

                  {/* Progressive 3-Stage controls */}
                  {!!params.progressive_pipeline && (
                    <div className="space-y-3 pt-1 border-t border-border/30">
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 1 Image Weight</label>
                          <span className="text-[10px] text-text-secondary">{(params.progressive_stage1_image_weight ?? 0.7).toFixed(2)}</span>
                        </div>
                        <input type="range" min={0.3} max={1.0} step={0.05}
                          value={params.progressive_stage1_image_weight ?? 0.7}
                          onChange={e => setParam('progressive_stage1_image_weight', parseFloat(e.target.value))}
                          className="w-full accent-accent-blue" />
                        <div className="flex justify-between text-[9px] text-text-muted mt-0.5">
                          <span>0.30 (more motion)</span><span>1.00 (match start image)</span>
                        </div>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 2 Steps (half res)</label>
                          <span className="text-[10px] text-text-secondary">{params.progressive_stage2_steps ?? 5}</span>
                        </div>
                        <input type="range" min={1} max={8} step={1}
                          value={params.progressive_stage2_steps ?? 5}
                          onChange={e => setParam('progressive_stage2_steps', Number(e.target.value))}
                          className="w-full accent-accent-blue" />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 3 Steps (full res)</label>
                          <span className="text-[10px] text-text-secondary">{params.progressive_stage3_steps ?? 3}</span>
                        </div>
                        <input type="range" min={1} max={8} step={1}
                          value={params.progressive_stage3_steps ?? 3}
                          onChange={e => setParam('progressive_stage3_steps', Number(e.target.value))}
                          className="w-full accent-accent-blue" />
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 2 Sigma</label>
                          <span className="text-[10px] text-text-secondary">{(params.progressive_stage2_sigma ?? 0.85).toFixed(2)}</span>
                        </div>
                        <input type="range" min={0.5} max={1.0} step={0.05}
                          value={params.progressive_stage2_sigma ?? 0.85}
                          onChange={e => setParam('progressive_stage2_sigma', parseFloat(e.target.value))}
                          className="w-full accent-accent-blue" />
                        <div className="flex justify-between text-[9px] text-text-muted mt-0.5">
                          <span>0.50 (preserve)</span><span>1.00 (regenerate)</span>
                        </div>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 3 Sigma</label>
                          <span className="text-[10px] text-text-secondary">{(params.progressive_stage3_sigma ?? 0.85).toFixed(2)}</span>
                        </div>
                        <input type="range" min={0.5} max={1.0} step={0.05}
                          value={params.progressive_stage3_sigma ?? 0.85}
                          onChange={e => setParam('progressive_stage3_sigma', parseFloat(e.target.value))}
                          className="w-full accent-accent-blue" />
                        <div className="flex justify-between text-[9px] text-text-muted mt-0.5">
                          <span>0.50 (preserve)</span><span>1.00 (regenerate)</span>
                        </div>
                      </div>
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-[10px] text-text-muted">Stage 3 Image Weight (full res)</label>
                          <span className="text-[10px] text-text-secondary">{(params.progressive_stage3_image_weight ?? 0.7).toFixed(2)}</span>
                        </div>
                        <input type="range" min={0.3} max={1.0} step={0.05}
                          value={params.progressive_stage3_image_weight ?? 0.7}
                          onChange={e => setParam('progressive_stage3_image_weight', parseFloat(e.target.value))}
                          className="w-full accent-accent-blue" />
                        <div className="flex justify-between text-[9px] text-text-muted mt-0.5">
                          <span>0.30 (more detail freedom)</span><span>1.00 (match start image)</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Reference Pipeline (10Eros — runs the author's published
                  ComfyUI workflow config: 9+3 eased steps, per-step CFG
                  2.0/1.5 then off, STG on blocks 14+19 for the first 4
                  steps, RF euler_ancestral). Shown only for models whose
                  def declares reference_pipeline support. */}
              {!isScailEdit && modelOptions?.reference_pipeline && (
                <div className="space-y-1">
                  <label className="flex items-center gap-2 cursor-pointer group">
                    <input type="checkbox"
                      checked={!!params.reference_pipeline}
                      onChange={e => setParam('reference_pipeline', e.target.checked ? true : undefined)}
                      className="accent-accent-blue" />
                    <span className="text-[11px] text-text-muted uppercase tracking-wider group-hover:text-text-secondary transition-colors">
                      Reference Pipeline (10Eros)
                    </span>
                  </label>
                  <p className="text-[9px] text-text-muted">
                    Runs the model author&apos;s ComfyUI workflow config: 9+3 steps on hand-tuned sigmas,
                    CFG only on the first 2 steps, STG on the first 4, ancestral sampling.
                    Steps / CFG / STG sliders below are ignored while this is on.
                  </p>
                </div>
              )}

              {/* Dedicated SCAIL edit endpoints honor this value for both
                  Fast and HQ; other distilled models retain their lock. */}
              {showInferenceSteps && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span id="advanced-inference-steps-label" className="text-[11px] text-text-muted uppercase tracking-wider">
                      {modelOptions?.lock_inference_steps && !isScailEdit
                        ? 'Stage 1 / Primary Steps (Fixed)'
                        : 'Inference Steps'}
                    </span>
                    <input
                      type="number"
                      id="advanced-inference-steps"
                      aria-labelledby="advanced-inference-steps-label"
                      min={minimumInferenceSteps}
                      max={inferenceStepCeiling}
                      value={params.num_inference_steps}
                      onChange={e => setParam('num_inference_steps', Math.max(minimumInferenceSteps, Math.min(inferenceStepCeiling, Number(e.target.value) || minimumInferenceSteps)))}
                      disabled={!!modelOptions?.lock_inference_steps && !isScailEdit}
                      className="mobile-control-target w-16 bg-bg-tertiary border border-border rounded px-2 py-0.5 text-xs text-text-primary text-center focus:outline-none focus:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-60"
                    />
                  </div>
                  <input
                    type="range" min={minimumInferenceSteps} max={inferenceStepCeiling} step={1}
                    aria-labelledby="advanced-inference-steps-label"
                    value={params.num_inference_steps}
                    onChange={e => setParam('num_inference_steps', Number(e.target.value))}
                    disabled={!!modelOptions?.lock_inference_steps && !isScailEdit}
                    className="w-full disabled:cursor-not-allowed disabled:opacity-60"
                  />
                  {inferenceStepCeiling < 50 && (
                    <p className="text-[9px] text-amber-400 mt-0.5">
                      This computer could not finish MiniMax H3 above {inferenceStepCeiling} steps at the current size. Higher counts will be offered again if a compatible attention mode or a memory-saving update comes online.
                    </p>
                  )}
                  {modelOptions?.lock_inference_steps && !isScailEdit && (
                    <p className="text-[9px] text-text-muted mt-0.5">
                      This distilled model uses a fixed {modelOptions.default_num_inference_steps ?? params.num_inference_steps}-step recipe.
                      Choose a non-distilled model to change the main step count. Stage 2/3 settings add refinement.
                    </p>
                  )}
                  {isScailFast && (
                    <p className="text-[9px] text-text-muted mt-0.5">
                      Fast keeps its distilled CFG 1 recipe; guidance and
                      negative-prompt controls do not apply.
                    </p>
                  )}
                </div>
              )}

              {/* Guidance Scale (hidden for TTS — shown in TTS section above) */}
              {showGuidanceScale && (
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span id="advanced-guidance-scale-label" className="text-[11px] text-text-muted uppercase tracking-wider">Guidance Scale</span>
                    <input
                      type="number"
                      id="advanced-guidance-scale"
                      aria-labelledby="advanced-guidance-scale-label"
                      value={params.guidance_scale}
                      onChange={e => setParam('guidance_scale', Number(e.target.value))}
                      step={0.1}
                      className="mobile-control-target w-16 bg-bg-tertiary border border-border rounded px-2 py-0.5 text-xs text-text-primary text-center focus:outline-none focus:border-accent-blue focus-visible:ring-2 focus-visible:ring-accent-blue"
                    />
                  </div>
                  <input
                    type="range" min={0} max={20} step={0.1}
                    aria-labelledby="advanced-guidance-scale-label"
                    value={params.guidance_scale}
                    onChange={e => setParam('guidance_scale', Number(e.target.value))}
                    className="w-full"
                  />
                </div>
              )}

              {/* LTX-2 Dev Pipeline Controls — only for models with perturbation/CFG-Star support */}
              {!isScailEdit && modelOptions?.perturbation && (
                <>
                  {/* STG Scale */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] text-text-muted uppercase tracking-wider">STG Scale</label>
                      <span className="text-xs text-text-secondary">{(params.stg_scale ?? 0) > 0 ? (params.stg_scale as number).toFixed(1) : 'Off'}</span>
                    </div>
                    <input type="range" min={0} max={3} step={0.1}
                      value={params.stg_scale ?? 0}
                      onChange={e => setParam('stg_scale', parseFloat(e.target.value))}
                      className="w-full" />
                    <p className="text-[9px] text-text-muted mt-0.5">Spatio-temporal guidance. 0 = off. Sharpens structure &amp; motion via a third denoising pass (~50% slower). Try 1.0.</p>
                  </div>

                  {/* CFG Rescale */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] text-text-muted uppercase tracking-wider">CFG Rescale</label>
                      <span className="text-xs text-text-secondary">{(params.cfg_rescale ?? 0).toFixed(2)}</span>
                    </div>
                    <input type="range" min={0} max={1} step={0.05}
                      value={params.cfg_rescale ?? 0}
                      onChange={e => setParam('cfg_rescale', parseFloat(e.target.value))}
                      className="w-full" />
                    <p className="text-[9px] text-text-muted mt-0.5">Reduces over-saturation. 0.7 recommended.</p>
                  </div>

                  {/* Gradient Estimation */}
                  <div className="space-y-1.5">
                    <label className="flex items-center gap-2 cursor-pointer group">
                      <input type="checkbox"
                        checked={!!params.use_gradient_estimation}
                        onChange={e => setParam('use_gradient_estimation', e.target.checked ? true : undefined)}
                        className="accent-accent-blue" />
                      <span className="text-[11px] text-text-muted uppercase tracking-wider group-hover:text-text-secondary transition-colors">
                        Gradient Estimation
                      </span>
                    </label>
                    {params.use_gradient_estimation && (
                      <div className="pl-1 border-l border-border ml-1 space-y-1.5">
                        <p className="text-[9px] text-accent-blue/80">Use 20-25 steps instead of 30-40 for comparable quality.</p>
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-[10px] text-text-muted">Gamma</span>
                            <span className="text-[9px] text-text-muted">{(params.ge_gamma ?? 2.0).toFixed(1)}</span>
                          </div>
                          <input type="range" min={1} max={4} step={0.1}
                            value={params.ge_gamma ?? 2.0}
                            onChange={e => setParam('ge_gamma', parseFloat(e.target.value))}
                            className="w-full" />
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Keyframe Conditioning Mode — Start/End frames */}
              {!isScailEdit && (isVideo || isAvatar) && (hasStartImage || hasEndImage) && (
                <div>
                  <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Start/End Frame Mode</label>
                  <select
                    value={params.keyframe_conditioning_mode || 'replace'}
                    onChange={e => setParam('keyframe_conditioning_mode', e.target.value)}
                    className="w-full bg-bg-tertiary border border-border rounded px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                  >
                    <option value="replace">Replace (Default)</option>
                    <option value="additive">Additive (Smooth)</option>
                  </select>
                  <p className="text-[9px] text-text-muted mt-0.5">Replace: exact adherence to source image. Additive: smoother blending.</p>
                </div>
              )}

              {/* Keyframe Conditioning Mode — Injected keyframes */}
              {!isScailEdit && (isVideo || isAvatar) && hasImageRefs && (
                <div>
                  <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Injected Keyframe Mode</label>
                  <select
                    value={params.keyframe_inject_mode || 'additive'}
                    onChange={e => setParam('keyframe_inject_mode', e.target.value)}
                    className="w-full bg-bg-tertiary border border-border rounded px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-accent-blue"
                  >
                    <option value="additive">Additive (Default)</option>
                    <option value="replace">Replace (Strict)</option>
                  </select>
                  <p className="text-[9px] text-text-muted mt-0.5">Additive: smooth transitions at injected frames. Replace: strict adherence.</p>
                </div>
              )}

              {/* Negative Prompt */}
              {showNegativePrompt && (
                <div>
                  <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Negative Prompt</label>
                  <textarea
                    value={params.negative_prompt || ''}
                    onChange={e => setParam('negative_prompt', e.target.value)}
                    placeholder="What to avoid..."
                    rows={2}
                    className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
                    style={{ resize: 'vertical', minHeight: 48 }}
                  />
                </div>
              )}

              {/* MMAudio — video models only */}
              {isVideo && (
                <div className="space-y-2">
                  <label className="mobile-control-target flex items-center gap-2 cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={params.MMAudio_setting === 1}
                      onChange={e => setParam('MMAudio_setting', e.target.checked ? 1 : 0)}
                      className="accent-accent-blue"
                    />
                    <span className="text-[11px] text-text-muted uppercase tracking-wider group-hover:text-text-secondary transition-colors">
                      MMAudio (Soundtrack)
                    </span>
                  </label>
                  {params.MMAudio_setting === 1 && (
                    <div className="space-y-2 pl-1 border-l border-border ml-1">
                      <div>
                        <label className="text-[10px] text-text-muted block mb-1">Prompt (1-2 keywords)</label>
                        <input
                          type="text"
                          value={(params.MMAudio_prompt) || ''}
                          onChange={e => setParam('MMAudio_prompt', e.target.value)}
                          placeholder="e.g. rain, thunder"
                          className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-text-muted block mb-1">Negative Prompt (1-2 keywords)</label>
                        <input
                          type="text"
                          value={(params.MMAudio_neg_prompt) || ''}
                          onChange={e => setParam('MMAudio_neg_prompt', e.target.value)}
                          placeholder="e.g. talking, speech"
                          className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue"
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Presets */}
              <PresetManager />

              {/* Official Outpaint owns its stage-one-only IC-LoRA schedule. */}
              {!isOutpaint && <LoraSelector />}

              {/* Dedicated SCAIL edit endpoints own their source video,
                  edited/reference frames, masks, and process selection. */}
              {(modelOptions?.guide_preprocessing || modelOptions?.guide_custom_choices) &&
                !isScailEdit && (
                <ControlVideoSection />
              )}

              {/* Dedicated Recast/Repaint submissions create one edit job. */}
              {!isScailEdit && <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-[11px] text-text-muted uppercase tracking-wider">Output Count</label>
                  <span className="text-xs text-text-secondary">{params.repeat_generation || 1}</span>
                </div>
                <input
                  type="range" min={1} max={10} step={1}
                  aria-label="Output Count"
                  value={params.repeat_generation || 1}
                  onChange={e => setParam('repeat_generation', Number(e.target.value))}
                  className="w-full"
                />
              </div>}

              <label className="flex items-start gap-2 rounded-lg border border-border bg-bg-tertiary/50 p-2.5 text-[11px] text-text-secondary">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-accent-blue"
                  checked={openQueueAfterSubmit}
                  onChange={event => setOpenQueueAfterSubmit(event.target.checked)}
                />
                <span>
                  <span className="font-medium text-text-primary">Open Queue after submit</span>
                  <span className="mt-0.5 block text-[9px] text-text-muted">Enabled by default. Turn off to stay in the current Gallery view after a job is accepted.</span>
                </span>
              </label>
            </div>
          </div>
        </>,
        document.body,
      )}
    </>
  )
}
