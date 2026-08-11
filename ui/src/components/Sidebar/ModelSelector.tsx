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

  // Close on click outside
  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
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
  const pendingRequirements = (currentModel?.required_host_terms || []).filter(
    requirement => hostTerms?.[requirement.term]?.accepted !== true,
  )
  const manualVerificationPending = Boolean(
    currentModel?.downloadable === false
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
        error instanceof Error ? error.message : 'Manual checkpoint verification failed.',
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
    return n + avail.filter(m => !enabledModels.has(m.model_type)).length
  }, 0)

  return (
    <div className="relative flex-1 min-w-0" ref={containerRef}>
      {/* Trigger button */}
      <button
        onClick={() => setOpen(!open)}
        title={currentModel?.selector_help || currentModel?.description}
        className="w-full flex items-center gap-1.5 bg-bg-tertiary border border-border rounded-lg px-2.5 py-2 text-left hover:border-border-light transition-colors"
      >
        <span className="flex-1 min-w-0 truncate text-xs text-text-primary">
          {currentModel?.name ?? 'Select model'}
        </span>
        <ChevronDown size={14} className={`shrink-0 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {pendingRequirements.map(requirement => (
        <div key={requirement.term} role="status" className="mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
          <p>{requirement.notice}</p>
          <div className="mt-1 flex items-center gap-2">
            <a href={requirement.license_url} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">Review exact terms</a>
            <button
              type="button"
              disabled={hostTermsLoading || !hostTerms}
              onClick={() => { void acceptHostTerm(requirement.term) }}
              className="rounded border border-amber-400/40 px-1.5 py-0.5 text-[9px] font-medium text-amber-100 disabled:opacity-40"
            >
              Accept for this host
            </button>
          </div>
        </div>
      ))}
      {pendingRequirements.length > 0 && hostTermsError && (
        <p role="status" className="mt-1 text-[9px] text-red-300">{hostTermsError}</p>
      )}
      {currentModel?.downloadable === false && (
        <div role="status" className="mt-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[9px] leading-relaxed text-amber-100">
          {currentModel.manual_installation && (
            <dl className="mb-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">
              <dt className="text-amber-200">Filename</dt>
              <dd className="break-all font-mono select-all">{currentModel.manual_installation.filename}</dd>
              <dt className="text-amber-200">Place in</dt>
              <dd className="break-all font-mono select-all">{manualInstallationDestination(currentModel.manual_installation)}</dd>
              <dt className="text-amber-200">Size</dt>
              <dd>{formatManualInstallationBytes(currentModel.manual_installation.size_bytes)}</dd>
              <dt className="text-amber-200">SHA-256</dt>
              <dd className="break-all font-mono select-all">{currentModel.manual_installation.sha256}</dd>
            </dl>
          )}
          {currentModel.manual_installation && (
            <div className="mb-1 flex flex-wrap gap-x-2 gap-y-0.5">
              <a href={currentModel.manual_installation.source_url} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">Open source page</a>
              <a href={currentModel.manual_installation.download_url} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">Open exact manual download</a>
            </div>
          )}
          {currentModel.manual_checkpoint_verified ? (
            <p>Exact local checkpoint verified for this host. Routine catalog polling does not re-hash it.</p>
          ) : (
            <>
              <p>Manual install required. Place the exact published checkpoint in the destination above, then verify its byte size and SHA-256 locally on the host. Maestro will not download this checkpoint.</p>
              {manualVerificationPending && machineControls && (
                <button
                  type="button"
                  disabled={verifyingManualCheckpoint || pendingRequirements.length > 0}
                  onClick={() => { void verifyCurrentManualCheckpoint() }}
                  className="mt-1 inline-flex items-center gap-1 rounded border border-amber-400/40 px-1.5 py-0.5 font-medium text-amber-100 disabled:opacity-40"
                >
                  {verifyingManualCheckpoint ? <Loader2 size={9} className="animate-spin" /> : <HardDrive size={9} />}
                  {verifyingManualCheckpoint ? 'Verifying local checkpoint…' : 'Verify local checkpoint'}
                </button>
              )}
              {manualVerificationPending && !machineControls && (
                <p className="mt-1 text-amber-200">Local-only verification: open Maestro on the host machine and choose Verify local checkpoint.</p>
              )}
              {!currentModel.manual_checkpoint_verification_required && (
                <p className="mt-1 text-red-300">No supported exact verification contract is available for this recipe.</p>
              )}
            </>
          )}
          {manualVerificationError && <p className="mt-1 text-red-300">{manualVerificationError}</p>}
        </div>
      )}

      {/* Dropdown (opens upward) */}
      {open && (
        <div className="absolute bottom-full left-0 mb-1 w-[360px] max-w-[90vw] bg-bg-secondary border border-border rounded-lg shadow-xl overflow-hidden z-50">
          {/* Enable-more entry — sits above the enabled model list; opens
              Settings → Enabled Models expanded to this mode. */}
          {disabledCount > 0 && (
            <button
              onClick={() => { openModelVisibility(generationMode); setOpen(false) }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left border-b border-border text-text-secondary hover:bg-bg-hover hover:text-accent-blue transition-colors"
            >
              <Plus size={13} className="shrink-0" />
              <span className="flex-1 text-xs">Enable more models</span>
              <span className="text-[10px] text-text-muted shrink-0">{disabledCount} available</span>
            </button>
          )}
          <div className="max-h-[360px] overflow-y-auto py-1">
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
                        disabled={w4a8Unavailable}
                        title={
                          w4a8Unavailable
                            ? (w4a8Capability?.reason || 'Checking W4A8 runtime support…')
                            : pinkReconciliationLabel || model.selector_help || model.description
                        }
                        onClick={async () => {
                          if (await selectModel(model.model_type)) setOpen(false)
                        }}
                        className="min-w-0 flex-1 px-3 py-1.5 flex items-center gap-2 text-left disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        <span className="flex-1 min-w-0 text-xs truncate">{model.name}</span>
                        <ModelBadges model={model} />
                        {pinkReconciliationLabel && (
                          <span className="text-[9px] text-amber-300">{pinkReconciliationLabel}</span>
                        )}
                        {w4a8Unavailable && <span className="text-[9px] text-amber-300">Unavailable</span>}
                        {isSelected && <Check size={12} className="shrink-0 text-accent-blue" />}
                      </button>
                      {(model.selector_help || model.description) && (
                        <span className="pr-2 shrink-0">
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
  model: { model_type: string; is_i2v: boolean; is_t2v: boolean; supports_end_frame?: boolean; supports_audio?: boolean; supports_audio_input?: boolean; generates_audio?: boolean; supports_ref_images?: boolean; downloadable?: boolean; manual_checkpoint_verified?: boolean }
}) {
  const badges: Array<{ label: string; title?: string }> = []
  if (model.model_type === 'minimax_h3_pinkcherry_fl2va') badges.push({ label: 'Explicit FL2VA' })
  else if (model.model_type === 'minimax_h3_w4a8_fl2va') badges.push({ label: 'Experimental W4A8 FL2VA' })
  else if (model.model_type === 'minimax_h3_ref2va') badges.push({ label: 'Non-distilled Ref2VA' })
  else if (model.model_type.startsWith('minimax_h3')) badges.push({ label: 'Non-distilled FL2VA' })
  if (model.is_i2v && model.supports_end_frame) badges.push({ label: 'S/E Frame', title: 'Supports start and end frame guidance' })
  else if (model.is_i2v) badges.push({ label: 'I2V', title: 'Supports image-to-video generation' })
  if (model.generates_audio) badges.push({ label: 'Audio Out', title: 'Generates native audio with video' })
  if (model.supports_audio_input) badges.push({ label: 'Audio In', title: 'Accepts audio conditioning' })
  if (model.supports_audio && !model.generates_audio && !model.supports_audio_input) badges.push({ label: 'Audio' })
  if (model.supports_ref_images) badges.push({ label: 'Refs', title: 'Supports reference images' })
  if (model.downloadable === false) badges.push({
    label: model.manual_checkpoint_verified ? 'Manual · verified' : 'Manual install',
    title: model.manual_checkpoint_verified
      ? 'The exact local checkpoint was verified on this host'
      : 'Install and verify the exact checkpoint locally; Maestro will not download it',
  })
  if (badges.length === 0) return null
  return (
    <span className="flex gap-0.5 shrink-0">
      {badges.map(b => (
        <span key={b.label} title={b.title} className="text-[9px] px-1 py-0.5 rounded bg-bg-tertiary text-text-muted leading-none">
          {b.label}
        </span>
      ))}
    </span>
  )
}
