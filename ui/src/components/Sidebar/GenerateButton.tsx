import { useState, useEffect } from 'react'
import { Play, AlertTriangle, ListPlus } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { fetchH3AccelerationStatus } from '../../api/client'
import { H3EstimateBadge } from './H3PerformanceProfiles'
import { projectLogicalQueue } from '../../lib/queueProjection'
import { h3ActiveCheckpoints, h3AdaptiveSelectionError } from '../../lib/h3Submission'

export function GenerateButton() {
  const jobs = useStore(s => s.jobs)
  const startGeneration = useStore(s => s.startGeneration)
  const setSidebarOpen = useStore(s => s.setSidebarOpen)
  const modelOptionsLoading = useStore(s => s.modelOptionsLoading)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const h3SelectionError = useStore(s => h3AdaptiveSelectionError(s.params))
  const missingH3Checkpoint = useStore(s => {
    const h3Selected = s.generationMode === 'video'
      && (
        s.params.model_type.startsWith('minimax_h3')
        || String(s.modelOptions?.architecture || '').startsWith('minimax_h3')
      )
    if (!h3Selected || !s.modelsLoaded) return null
    return h3ActiveCheckpoints(s.params).find(modelType => (
      !s.models.some(candidate => candidate.model_type === modelType)
    )) || null
  })
  const usesW4a8 = useStore(s => (
    h3ActiveCheckpoints(s.params).includes('minimax_h3_w4a8_fl2va')
  ))
  const h3LocationRequired = useStore(s => {
    const types = h3ActiveCheckpoints(s.params)
    return types.some(modelType => (
      s.models.find(candidate => candidate.model_type === modelType)
        ?.availability_status === 'location_declaration_required'
    ))
  })
  const legalBlocked = useStore(s => {
    const types = h3ActiveCheckpoints(s.params)
    return types.some(modelType => {
      const model = s.models.find(candidate => candidate.model_type === modelType)
      return model?.availability_status === 'location_declaration_required'
        || model?.availability_status === 'legal_blocked'
        || model?.execution_allowed === false
    })
  })
  const needsModelTerms = useStore(s => {
    const types = h3ActiveCheckpoints(s.params)
    return types.some(modelType => {
      const model = s.models.find(candidate => candidate.model_type === modelType)
      if (
        model?.availability_status === 'location_declaration_required'
        || model?.availability_status === 'legal_blocked'
        || model?.execution_allowed === false
      ) return false
      return (model?.required_host_terms || []).some(
        requirement => s.hostTerms?.[requirement.term]?.accepted !== true,
      )
    })
  })
  const needsManualCheckpointVerification = useStore(s => {
    const types = h3ActiveCheckpoints(s.params)
    return types.some(modelType => {
      const model = s.models.find(candidate => candidate.model_type === modelType)
      return Boolean(
        model?.downloadable === false
        && !model.manual_checkpoint_verified
      )
    })
  })
  const isH3 = useStore(s => (
    s.generationMode === 'video'
    && (
      s.params.model_type.startsWith('minimax_h3')
      || String(s.modelOptions?.architecture || '').startsWith('minimax_h3')
    )
  ))
  const h3Estimate = useStore(s => s.h3CurrentEstimate)
  const h3EstimateLoading = useStore(s => s.h3EstimateLoading)
  const h3DownloadRequired = useStore(s => {
    const types = h3ActiveCheckpoints(s.params)
    if (types.some(modelType => s.models.find(model => model.model_type === modelType)?.is_downloaded === false)) {
      return true
    }
    const turboProfile = (s.params.custom_settings || {}).h3_turbo_profile
    return !!turboProfile && s.h3PerformanceProfiles.some(profile => (
      profile.download_required
      && types.includes(profile.settings.model_type)
      && profile.settings.custom_settings.h3_turbo_profile === turboProfile
    ))
  })
  const [cooldown, setCooldown] = useState(false)
  const [w4a8Capability, setW4a8Capability] = useState<{
    available: boolean
    reason: string
  } | null>(null)

  useEffect(() => {
    if (!usesW4a8) return
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
          reason: 'W4A8 runtime support could not be checked.',
        })
      })
    return () => { current = false }
  }, [usesW4a8])

  // Check if i2v-only model needs a start image. Video mode only: edit
  // sub-modes supply their own source media (Recast runs the i2v-only
  // SCAIL-2 against a source video + reference image, no start image).
  const generationMode = useStore(s => s.generationMode)
  const isI2vOnly = useStore(s => s.modelOptions?.i2v_class && !s.modelOptions?.t2v_class)
  const hasStartImage = useStore(s => !!(s.startImage || s.params.image_start))
  const needsImage = generationMode === 'video' && isI2vOnly && !hasStartImage
  const editSubMode = useStore(s => s.editSubMode)
  const editVideoPath = useStore(s => s.editVideoPath)
  const outpaintVideoBox = useStore(s => s.outpaintVideoBox)
  const isOutpaint = generationMode === 'avatar' && editSubMode === 'outpaint'
  const needsOutpaintSource = isOutpaint && !editVideoPath
  const hasOutpaintArea = (
    outpaintVideoBox.x > 0.0005
    || outpaintVideoBox.y > 0.0005
    || outpaintVideoBox.x + outpaintVideoBox.w < 0.9995
    || outpaintVideoBox.y + outpaintVideoBox.h < 0.9995
  )
  const needsOutpaintArea = isOutpaint && !!editVideoPath && !hasOutpaintArea
  const needsProject = !activeWorkspace
  const w4a8RuntimeBlocked = usesW4a8 && w4a8Capability?.available !== true
  const h3CheckpointBlocked = Boolean(h3SelectionError || missingH3Checkpoint || w4a8RuntimeBlocked)
  const blocked = modelOptionsLoading || needsProject || h3CheckpointBlocked || legalBlocked || needsModelTerms || needsManualCheckpointVerification || needsImage || needsOutpaintSource || needsOutpaintArea

  // Brief gray flash after clicking
  useEffect(() => {
    if (!cooldown) return
    const timer = setTimeout(() => setCooldown(false), 1000)
    return () => clearTimeout(timer)
  }, [cooldown])

  const imageMode = useStore(s => s.params.image_mode)
  const queueSupported = generationMode !== 'avatar' && imageMode !== 4

  const handleClick = (mode: 'now' | 'queue' = 'now') => {
    if (blocked) return
    if (mode === 'queue' && !queueSupported) return
    setCooldown(true)
    startGeneration(mode)
    if (mode === 'now') setSidebarOpen(false)
  }

  const queueCount = projectLogicalQueue(jobs).visibleJobs.filter(job =>
    job.status === 'queued' || job.status === 'running' || job.held
  ).length

  if (blocked) {
    const label = modelOptionsLoading
      ? 'Loading model'
      : needsProject
      ? 'Select project'
      : h3SelectionError
      ? 'Choose H3 models'
      : missingH3Checkpoint
      ? 'Model unavailable'
      : w4a8RuntimeBlocked
      ? w4a8Capability ? 'Model unavailable' : 'Checking model'
      : legalBlocked
      ? h3LocationRequired ? 'Location needed' : 'License required'
      : needsModelTerms
      ? 'Review terms'
      : needsManualCheckpointVerification
      ? 'Model file needed'
      : needsImage
      ? 'Need image'
      : needsOutpaintSource
        ? 'Need source'
        : 'Choose canvas'
    const title = modelOptionsLoading
      ? 'Loading the settings available for this model.'
      : needsOutpaintArea
      ? 'Choose a larger output aspect or resize the source to create an area for Outpaint to generate.'
      : needsProject
      ? 'Choose or create a project first.'
      : h3SelectionError
      ? h3SelectionError
      : missingH3Checkpoint
      ? 'The saved H3 checkpoint is no longer in the model catalog. Open the matching model group and choose an available checkpoint.'
      : w4a8RuntimeBlocked
      ? w4a8Capability?.reason || 'Checking whether this computer can run the selected W4A8 checkpoint.'
      : legalBlocked
      ? h3LocationRequired
        ? 'Choose the country where this computer will actually run MiniMax H3. Maestro does not use IP or VPN location.'
        : 'MiniMax H3 is not licensed in the owner-declared operating country. Separate written MiniMax authorization is required.'
      : needsModelTerms
      ? 'Read and accept the selected model\'s terms for this Maestro installation.'
      : needsManualCheckpointVerification
      ? 'Download the required model file yourself, then use the model selector to check it on the computer running Maestro. Maestro will not download this file.'
      : undefined
    return (
      <div className="flex flex-col items-end gap-0.5">
        <div className="grid grid-cols-[1fr_auto] overflow-hidden rounded-lg">
          <button
            disabled
            title={title}
            className="px-4 py-2 flex items-center gap-1.5 bg-amber-500/20 text-indicator-warning cursor-not-allowed text-xs font-medium whitespace-nowrap"
          >
            <AlertTriangle size={13} />
            {label}
          </button>
          <button
            disabled
            title={title || 'Need a complete request before adding to the queue'}
            className="px-2.5 py-2 bg-amber-500/15 text-indicator-warning cursor-not-allowed border-l border-amber-500/20"
          >
            <ListPlus size={13} />
          </button>
        </div>
        {isH3 && <H3EstimateBadge estimate={h3Estimate} loading={h3EstimateLoading} downloadRequired={h3DownloadRequired} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col items-end gap-0.5">
      <div className="grid grid-cols-[1fr_auto] overflow-hidden rounded-lg shadow-accent-glow">
        <button
          onClick={() => handleClick('now')}
          disabled={cooldown}
          className={`px-4 py-2 flex items-center gap-1.5 font-medium text-xs transition-all whitespace-nowrap ${
            cooldown
              ? 'bg-bg-active text-text-muted cursor-not-allowed'
              : 'bg-cta text-cta-foreground hover:ring-2 hover:ring-accent-blue/40'
          }`}
        >
          <Play size={13} fill="currentColor" />
          {cooldown ? 'Queued' : queueCount > 0 ? `Go (${queueCount})` : 'Generate'}
        </button>
        <button
          onClick={() => handleClick('queue')}
          disabled={cooldown || !queueSupported}
          title={queueSupported
            ? 'Hold current Studio settings in the queue without starting generation'
            : 'Hold is unavailable for this Avatar or edit workflow'}
          className={`px-2.5 py-2 border-l border-white/10 ${
            cooldown || !queueSupported
              ? 'bg-bg-active text-text-muted cursor-not-allowed'
              : 'bg-cta text-cta-foreground hover:ring-2 hover:ring-accent-blue/40'
          }`}
        >
          <ListPlus size={13} />
        </button>
      </div>
      {isH3 && <H3EstimateBadge estimate={h3Estimate} loading={h3EstimateLoading} downloadRequired={h3DownloadRequired} />}
    </div>
  )
}
