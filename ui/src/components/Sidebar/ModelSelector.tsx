import { ChevronDown, Check, HardDrive, Loader2, Plus } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import { useStore, getFamiliesForMode, getModelsForFamily } from '../../stores/useStore'
import { fetchH3AccelerationStatus, verifyManualCheckpoint } from '../../api/client'
import { InfoTooltip } from './InfoTooltip'
import { formatManualInstallationBytes, manualInstallationDestination } from '../../lib/manualInstallation'

export function ModelSelector() {
  const models = useStore(s => s.models)
  const families = useStore(s => s.families)
  const enabledModels = useStore(s => s.enabledModels)
  const generationMode = useStore(s => s.generationMode)
  const editSubMode = useStore(s => s.editSubMode)
  const currentModelType = useStore(s => s.params.model_type)
  const selectModel = useStore(s => s.selectModel)
  const h3SelectedProfile = useStore(s => s.h3SelectedProfile)
  const h3Profiles = useStore(s => s.h3PerformanceProfiles)
  const pinkCompatibility = useStore(
    s => s.h3ModelProfileCompatibility.minimax_h3_pinkcherry_fl2va,
  )
  const refreshH3Compatibility = useStore(s => s.refreshH3ModelProfileCompatibility)
  const openModelVisibility = useStore(s => s.openModelVisibility)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const loadModels = useStore(s => s.loadModels)

  const [open, setOpen] = useState(false)
  const [verifyingManualCheckpoint, setVerifyingManualCheckpoint] = useState(false)
  const [manualVerificationError, setManualVerificationError] = useState('')
  const [w4a8Capability, setW4a8Capability] = useState<{ available: boolean; reason: string } | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)

  // Treat the model list as a non-modal dialog: move focus into it, close on
  // Escape/outside press, and restore focus when keyboard selection closes it.
  useEffect(() => {
    if (!open) return
    const focusFrame = window.requestAnimationFrame(() => {
      popupRef.current?.querySelector<HTMLElement>('button:not([disabled]), a[href]')?.focus()
    })
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setOpen(false)
      window.requestAnimationFrame(() => triggerRef.current?.focus())
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const h3CompatibilitySignature = useStore(s => JSON.stringify([
    s.params.model_type,
    s.params.num_inference_steps,
    s.params.resolution,
    s.params.custom_settings || {},
    s.params.activated_loras || [],
    s.params.loras_multipliers || '',
    s.params.tea_cache,
    s.spatialUpsampling,
    s.params.delivery_resolution || '',
    s.params.delivery_fit || '',
    s.explicitOutput,
    s.h3SelectedProfile,
  ]))

  useEffect(() => {
    if (!open || h3SelectedProfile === 'custom') return
    void refreshH3Compatibility('minimax_h3_pinkcherry_fl2va')
  }, [open, h3SelectedProfile, h3CompatibilitySignature, refreshH3Compatibility])

  useEffect(() => {
    if (!open) return
    let current = true
    fetchH3AccelerationStatus(false)
      .then(status => {
        if (current) setW4a8Capability({
          available: status.w4a8.available,
          reason: status.w4a8.reason,
        })
      })
      .catch(() => {
        if (current) setW4a8Capability({
          available: false,
          reason: 'Runtime capability check failed',
        })
      })
    return () => { current = false }
  }, [open])

  const audioSubMode = useStore(s => s.audioSubMode)

  const currentModel = models.find(m => m.model_type === currentModelType)
  const currentModelLegalBlocked = currentModel?.availability_status === 'legal_blocked'
    || currentModel?.execution_allowed === false
  const pendingRequirements = currentModelLegalBlocked
    ? []
    : (currentModel?.required_host_terms || []).filter(
        requirement => hostTerms?.[requirement.term]?.accepted !== true,
      )
  const manualVerificationPending = Boolean(
    !currentModelLegalBlocked
    && currentModel?.downloadable === false
    && currentModel.manual_checkpoint_verification_required
    && !currentModel.manual_checkpoint_verified,
  )

  useEffect(() => {
    setManualVerificationError('')
  }, [currentModelType])

  const verifyCurrentManualCheckpoint = async () => {
    if (!currentModel || pendingRequirements.length > 0) return
    setVerifyingManualCheckpoint(true)
    setManualVerificationError('')
    try {
      await verifyManualCheckpoint(currentModel.model_type)
      await loadModels()
    } catch (error) {
      setManualVerificationError(
        error instanceof Error ? error.message : 'The model file could not be checked.',
      )
    } finally {
      setVerifyingManualCheckpoint(false)
    }
  }

  useEffect(() => {
    if (
      activeWorkspace
      && (currentModel?.required_host_terms?.length || 0) > 0
      && !hostTerms
      && !hostTermsLoading
    ) void loadHostTerms()
  }, [activeWorkspace, currentModel, hostTerms, hostTermsLoading, loadHostTerms])
  const effectiveSubMode = generationMode === 'avatar' ? editSubMode : undefined
  const effectiveAudioSubMode = generationMode === 'audio' ? audioSubMode : undefined
  const modeFamilies = getFamiliesForMode(generationMode, families, effectiveSubMode, effectiveAudioSubMode)

  // Build grouped model list from the user's model-visibility choices.
  const groups = modeFamilies.map(family => ({
    family,
    models: getModelsForFamily(family.id, models, generationMode, effectiveSubMode)
      .filter(m => enabledModels.has(m.model_type))
      .sort((left, right) => left.name.localeCompare(right.name)),
  })).filter(g => g.models.length > 0)

  // How many models are available for this mode but NOT enabled — powers the
  // "+N" hint that nudges users toward Settings → Enabled Models.
  const disabledCount = modeFamilies.reduce((n, family) => {
    const avail = getModelsForFamily(family.id, models, generationMode, effectiveSubMode)
    return n + avail.filter(m => (
      !enabledModels.has(m.model_type)
      && m.availability_status !== 'legal_blocked'
      && m.execution_allowed !== false
    )).length
  }, 0)

  return (
    <div className="relative flex-1 min-w-0" ref={containerRef}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(!open)}
        title={currentModel?.selector_help || currentModel?.description}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls="model-selector-menu"
        className="mobile-control-target flex w-full items-center gap-1.5 rounded-lg border border-border bg-bg-tertiary px-2.5 py-2 text-left transition-colors hover:border-border-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
      >
        <span className="flex-1 min-w-0 truncate text-xs text-text-primary">
          {currentModel?.name ?? 'Select model'}
        </span>
        <ChevronDown size={14} className={`shrink-0 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {currentModelLegalBlocked && (
        <div role="status" className="mt-1 rounded border border-red-500/35 bg-red-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-red-100">
          MiniMax H3 cannot run on this installation because its current license excludes the United States. Accepting model terms does not grant access; a separate written MiniMax license is required.
        </div>
      )}

      {pendingRequirements.map(requirement => (
        <div key={requirement.term} role="status" className="mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
          <p>{requirement.notice}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <a href={requirement.license_url} target="_blank" rel="noreferrer" className="mobile-control-target inline-flex items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Read model terms</a>
            <button
              type="button"
              disabled={hostTermsLoading || !hostTerms}
              onClick={() => { void acceptHostTerm(requirement.term) }}
              className="mobile-control-target rounded border border-amber-400/40 px-2 py-0.5 text-[9px] font-medium text-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40"
            >
              Accept for this Maestro installation
            </button>
          </div>
        </div>
      ))}
      {pendingRequirements.length > 0 && hostTermsError && (
        <p role="status" className="mt-1 text-[9px] text-red-300">{hostTermsError}</p>
      )}
      {currentModel?.downloadable === false && !currentModelLegalBlocked && (
        <div role="status" className="mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
          {currentModel.manual_installation && (
            <dl className="mb-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">
              <dt className="text-amber-200">Filename</dt>
              <dd className="break-all font-mono select-all">{currentModel.manual_installation.filename}</dd>
              <dt className="text-amber-200">Save to</dt>
              <dd className="break-all font-mono select-all">{manualInstallationDestination(currentModel.manual_installation)}</dd>
              <dt className="text-amber-200">Size</dt>
              <dd>{formatManualInstallationBytes(currentModel.manual_installation.size_bytes)}</dd>
              <dt className="text-amber-200">File fingerprint (SHA-256)</dt>
              <dd className="break-all font-mono select-all">{currentModel.manual_installation.sha256}</dd>
            </dl>
          )}
          {currentModel.manual_installation && (
            <div className="mb-1 flex flex-wrap gap-2">
              <a href={currentModel.manual_installation.source_url} target="_blank" rel="noreferrer" className="mobile-control-target inline-flex items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">About this model</a>
              <a href={currentModel.manual_installation.download_url} target="_blank" rel="noreferrer" className="mobile-control-target inline-flex items-center rounded text-accent-blue hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Download the required file</a>
            </div>
          )}
          {currentModel.manual_checkpoint_verified ? (
            <p>The computer running Maestro has verified the required model file. Maestro will not repeatedly check it during normal model updates.</p>
          ) : (
            <>
              <p>Download the required model file yourself and save it in the folder above. Maestro will check its size and SHA-256 fingerprint on the computer where it runs; it will not download this file for you.</p>
              {manualVerificationPending && machineControls && (
                <button
                  type="button"
                  disabled={verifyingManualCheckpoint || pendingRequirements.length > 0}
                  onClick={() => { void verifyCurrentManualCheckpoint() }}
                  className="mobile-control-target mt-1 inline-flex items-center gap-1 rounded border border-amber-400/40 px-2 py-0.5 font-medium text-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40"
                >
                  {verifyingManualCheckpoint ? <Loader2 size={9} className="animate-spin" /> : <HardDrive size={9} />}
                  {verifyingManualCheckpoint ? 'Checking model file…' : 'Check model file'}
                </button>
              )}
              {manualVerificationPending && !machineControls && (
                <p className="mt-1 text-amber-200">To check the file, open Maestro on the computer where it is installed and choose Check model file.</p>
              )}
              {!currentModel.manual_checkpoint_verification_required && (
                <p className="mt-1 text-red-300">Maestro does not have the file details needed to verify this model.</p>
              )}
            </>
          )}
          {manualVerificationError && <p className="mt-1 text-red-300">{manualVerificationError}</p>}
        </div>
      )}

      {/* Dropdown (opens upward) */}
      {open && (
        <div
          ref={popupRef}
          id="model-selector-menu"
          role="dialog"
          aria-label="Models"
          className="absolute left-0 top-0 z-50 flex max-h-[min(404px,calc(100dvh-2rem))] w-[360px] max-w-[calc(100vw-2rem)] -translate-y-[calc(100%+0.25rem)] flex-col overflow-hidden rounded-lg border border-border bg-bg-secondary shadow-xl"
        >
          {/* Enable-more entry — sits above the enabled model list; opens
              Settings → Enabled Models expanded to this mode. */}
          {disabledCount > 0 && (
            <button
              type="button"
              onClick={() => { openModelVisibility(generationMode); setOpen(false) }}
              className="mobile-control-target flex w-full items-center gap-2 border-b border-border px-3 py-2 text-left text-text-secondary transition-colors hover:bg-bg-hover hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
            >
              <Plus size={13} className="shrink-0" />
              <span className="flex-1 text-xs">Enable more models</span>
              <span className="text-[10px] text-text-muted shrink-0">{disabledCount} available</span>
            </button>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1">
            {groups.map(({ family, models: famModels }) => (
              <div key={family.id}>
                {/* Family header */}
                <div className="px-3 pt-2 pb-1 text-[10px] text-text-muted uppercase tracking-wider font-medium">
                  {family.label}
                </div>
                {/* Models in family */}
                {famModels.map(model => {
                  const isSelected = model.model_type === currentModelType
                  const w4a8Unavailable = (
                    model.model_type === 'minimax_h3_w4a8_fl2va'
                    && w4a8Capability?.available !== true
                  )
                  const legalBlocked = model.availability_status === 'legal_blocked'
                    || model.execution_allowed === false
                  const isPinkCherry = model.model_type === 'minimax_h3_pinkcherry_fl2va'
                  const pinkProfileIncompatible = isPinkCherry
                    && pinkCompatibility?.requestedProfileId === h3SelectedProfile
                    && pinkCompatibility.loading === false
                    && !pinkCompatibility.compatible
                  const requestedProfileLabel = h3Profiles.find(
                    profile => profile.id === pinkCompatibility?.requestedProfileId,
                  )?.label || pinkCompatibility?.requestedProfileId
                  const pinkReconciliationLabel = pinkProfileIncompatible
                    ? `${requestedProfileLabel || 'Current profile'} incompatible · selects ${pinkCompatibility?.fallbackProfileLabel || pinkCompatibility?.fallbackProfileId || 'server fallback'}`
                    : null
                  return (
                    <div
                      key={model.model_type}
                      className={`group w-full flex items-center transition-colors ${
                        isSelected
                          ? 'bg-accent-blue/10 text-text-primary'
                          : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                      }`}
                    >
                      <button
                        type="button"
                        disabled={w4a8Unavailable || legalBlocked}
                        aria-pressed={isSelected}
                        title={
                          legalBlocked
                            ? 'A separate written MiniMax H3 license is required on this installation.'
                            : w4a8Unavailable
                            ? (w4a8Capability?.reason || 'Checking W4A8 runtime support…')
                            : pinkReconciliationLabel || model.selector_help || model.description
                        }
                        onClick={async () => {
                          if (await selectModel(model.model_type)) {
                            setOpen(false)
                            window.requestAnimationFrame(() => triggerRef.current?.focus())
                          }
                        }}
                        className="mobile-control-target flex min-w-0 flex-1 flex-wrap items-center gap-2 px-3 py-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        <span className="flex-1 min-w-0 text-xs truncate">{model.name}</span>
                        <ModelBadges model={model} />
                        {pinkReconciliationLabel && (
                          <span className="text-[9px] text-amber-300">{pinkReconciliationLabel}</span>
                        )}
                        {w4a8Unavailable && <span className="text-[9px] text-amber-300">Unavailable</span>}
                        {legalBlocked && <span className="text-[9px] text-red-300">License required</span>}
                        {isSelected && <Check size={12} className="shrink-0 text-accent-blue" />}
                      </button>
                      {(model.selector_help || model.description) && (
                        <span className="shrink-0 pr-1 [&_button]:min-h-11 [&_button]:min-w-11 [&_button]:focus-visible:outline-none [&_button]:focus-visible:ring-2 [&_button]:focus-visible:ring-accent-blue md:pr-2 md:[&_button]:min-h-0 md:[&_button]:min-w-0">
                          <InfoTooltip text={model.selector_help || model.description || ''} />
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ModelBadges({ model }: {
  model: { model_type: string; is_i2v: boolean; is_t2v: boolean; supports_end_frame?: boolean; supports_audio?: boolean; supports_audio_input?: boolean; generates_audio?: boolean; supports_ref_images?: boolean; downloadable?: boolean; manual_checkpoint_verified?: boolean; availability_status?: string; execution_allowed?: boolean }
}) {
  const badges: Array<{ label: string; title?: string }> = []
  if (model.model_type === 'minimax_h3_pinkcherry_fl2va') badges.push({ label: 'Explicit' })
  else if (model.model_type === 'minimax_h3_w4a8_fl2va') badges.push({ label: 'Experimental' })
  else if (model.model_type === 'minimax_h3_ref2va') badges.push({ label: 'Reference media' })
  else if (model.model_type.startsWith('minimax_h3')) badges.push({ label: 'H3' })
  if (model.availability_status === 'legal_blocked' || model.execution_allowed === false) badges.push({
    label: 'License required',
    title: 'This installation needs a separate written MiniMax H3 license before it can run this model',
  })
  if (model.is_i2v && model.supports_end_frame) badges.push({ label: 'Start + end', title: 'Uses start and end images to guide the video' })
  else if (model.is_i2v) badges.push({ label: 'Image to video', title: 'Creates video from an image' })
  if (model.generates_audio) badges.push({ label: 'Makes audio', title: 'Creates audio with the video' })
  if (model.supports_audio_input) badges.push({ label: 'Uses audio', title: 'Can follow an audio reference' })
  if (model.supports_audio && !model.generates_audio && !model.supports_audio_input) badges.push({ label: 'Audio' })
  if (model.supports_ref_images) badges.push({ label: 'Reference images', title: 'Can follow reference images' })
  if (model.downloadable === false) badges.push({
    label: model.manual_checkpoint_verified ? 'File checked' : 'File needed',
    title: model.manual_checkpoint_verified
      ? 'The required model file was checked on the computer running Maestro'
      : 'Download and check the required model file on the computer running Maestro; Maestro will not download it',
  })
  if (badges.length === 0) return null
  return (
    <span className="flex shrink-0 flex-wrap justify-end gap-0.5">
      {badges.map(b => (
        <span key={b.label} title={b.title} className="text-[9px] px-1 py-0.5 rounded bg-bg-tertiary text-text-muted leading-none">
          {b.label}
        </span>
      ))}
    </span>
  )
}
