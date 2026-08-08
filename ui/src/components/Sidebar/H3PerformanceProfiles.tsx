import { useEffect } from 'react'
import { Clock3, Gauge } from 'lucide-react'
import { h3ProfileMatches, useStore } from '../../stores/useStore'
import type {
  H3PerformanceEstimate,
  H3PerformanceProfileId,
} from '../../types'

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'calibrating'
  if (seconds < 60) return `~${Math.max(1, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return remainder ? `~${minutes}m ${remainder}s` : `~${minutes}m`
}

function modelLoadSuffix(estimate: H3PerformanceEstimate | null): string {
  if (!estimate || estimate.model_load_state === 'resident') return ''
  const loadSeconds = Number(estimate.model_load_seconds)
  return Number.isFinite(loadSeconds) && loadSeconds > 0
    ? ` + ${formatSeconds(loadSeconds)} load`
    : ' + load TBD'
}

function estimateLabel(estimate: H3PerformanceEstimate | null): string {
  if (!estimate) return 'calibrating'
  return `${formatSeconds(estimate.seconds)} run${modelLoadSuffix(estimate)}`
}

function estimateTitle(estimate: H3PerformanceEstimate | null): string {
  if (!estimate) return 'Collecting enough local timing data to estimate this output.'
  const range = `${formatSeconds(estimate.range_seconds.low)}–${formatSeconds(estimate.range_seconds.high)}`
  const samples = `${estimate.sample_count} local sample${estimate.sample_count === 1 ? '' : 's'}`
  const loadSeconds = Number(estimate.model_load_seconds)
  const modelLoad = estimate.model_load_state === 'resident'
    ? ' The model is resident, so no model-load allowance is added.'
    : Number.isFinite(loadSeconds) && loadSeconds > 0
      ? ` Model load adds about ${formatSeconds(loadSeconds)} separately.`
      : ' Model-load time is not calibrated yet and is shown separately.'
  const uncertainty = estimate.uncertainty_reasons.length
    ? ` Main uncertainty: ${estimate.uncertainty_reasons.join('; ')}.`
    : ''
  const delivery = estimate.postprocess_seconds && estimate.delivery_resolution
    ? ` Generation is about ${formatSeconds(estimate.generation_seconds || 0)}; ${estimate.postprocess_method || 'post-processing'} adds about ${formatSeconds(estimate.postprocess_seconds)} for ${estimate.delivery_resolution} delivery.`
    : ''
  return `Estimated generation time ${range}.${delivery}${modelLoad} ${samples}; source: ${estimate.source}.${uncertainty}`
}

export function H3EstimateBadge({
  estimate,
  loading = false,
  downloadRequired = false,
}: {
  estimate: H3PerformanceEstimate | null
  loading?: boolean
  downloadRequired?: boolean
}) {
  const confidence = estimate?.confidence || 'calibrating'
  const extrapolated = !!estimate && (
    estimate.source.toLowerCase().includes('extrapolat')
    || estimate.uncertainty_reasons.some(reason => reason.toLowerCase().includes('extrapolat'))
  )
  const color = extrapolated
    ? 'text-amber-400'
    : confidence === 'high'
    ? 'text-emerald-400'
    : confidence === 'medium'
      ? 'text-sky-400'
      : confidence === 'low'
        ? 'text-amber-400'
        : 'text-text-muted'
  return (
    <span
      title={estimateTitle(estimate)}
      className={`inline-flex items-center gap-1 text-[10px] whitespace-nowrap ${color}`}
    >
      <Clock3 size={10} />
      <span>{loading && !estimate ? 'calibrating' : estimateLabel(estimate)}</span>
      {downloadRequired && <span className="text-text-muted">+ model/adapter download</span>}
    </span>
  )
}

export function H3PerformanceProfiles() {
  const params = useStore(state => state.params)
  const loraWeights = useStore(state => state.loraWeights)
  const profiles = useStore(state => state.h3PerformanceProfiles)
  const currentEstimate = useStore(state => state.h3CurrentEstimate)
  const loading = useStore(state => state.h3EstimateLoading)
  const error = useStore(state => state.h3EstimateError)
  const selected = useStore(state => state.h3SelectedProfile)
  const applying = useStore(state => state.h3ProfileApplying)
  const spatialUpsampling = useStore(state => state.spatialUpsampling)
  const applyProfile = useStore(state => state.applyH3PerformanceProfile)
  const refresh = useStore(state => state.refreshH3PerformanceEstimates)
  const estimateSignature = useStore(state => JSON.stringify([
    state.params.model_type,
    state.params.num_inference_steps,
    state.params.resolution,
    state.params.custom_settings || {},
    state.params.activated_loras || [],
    state.params.loras_multipliers || '',
    state.params.tea_cache,
    state.spatialUpsampling,
    state.params.delivery_resolution || '',
    state.params.delivery_fit || '',
    state.durationSeconds,
    state.slidingWindowSeconds,
    state.slidingWindowOverlap,
    !!(state.startImage || state.params.image_start),
    !!(state.endImage || state.params.image_end),
    state.imageRefs.length,
    Array.isArray(state.params.image_refs) ? state.params.image_refs.length : 0,
    !!state.params.video_guide,
    !!state.params.video_guide2,
    !!state.params.video_guide3,
    !!state.params.audio_guide,
    !!state.params.audio_guide2,
    !!state.params.audio_guide3,
    state.explicitOutput,
  ]))

  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh() }, 250)
    return () => window.clearTimeout(timer)
  }, [estimateSignature, refresh])

  const selectedProfile = selected === 'custom'
    ? undefined
    : profiles.find(profile => profile.id === selected)
  const matchingProfile = (
    selectedProfile && h3ProfileMatches(selectedProfile, params, loraWeights, spatialUpsampling)
      ? selectedProfile
      : profiles.find(profile => h3ProfileMatches(profile, params, loraWeights, spatialUpsampling))
  )
  const visibleSelection = matchingProfile?.id || 'custom'
  const activeProfile = visibleSelection === 'custom'
    ? undefined
    : profiles.find(profile => profile.id === visibleSelection)
  const unavailableReasons = Array.from(
    profiles.filter(profile => !profile.available && profile.fallback_reason)
      .reduce((grouped, profile) => {
        const reason = profile.fallback_reason as string
        grouped.set(reason, [...(grouped.get(reason) || []), profile.label])
        return grouped
      }, new Map<string, string[]>()),
    ([reason, labels]) => `${labels.join('/')} unavailable: ${reason}`,
  )

  return (
    <div className="rounded-lg border border-border bg-bg-tertiary/35 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <label className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-text-muted">
          <Gauge size={12} /> H3 performance
        </label>
        <H3EstimateBadge estimate={currentEstimate} loading={loading} />
      </div>
      <select
        value={visibleSelection}
        disabled={profiles.length === 0}
        onChange={event => {
          if (event.target.value !== 'custom') {
            void applyProfile(event.target.value as H3PerformanceProfileId)
          }
        }}
        className="w-full rounded-lg border border-border bg-bg-primary px-2.5 py-2 text-xs text-text-primary disabled:opacity-60"
      >
        <option value="custom">Custom · {estimateLabel(currentEstimate)}</option>
        {profiles.map(profile => (
          <option key={profile.id} value={profile.id} disabled={!profile.available}>
            {profile.label} · {profile.available ? estimateLabel(profile.estimate) : 'unavailable'}
            {profile.download_required ? ' + download' : ''}
          </option>
        ))}
      </select>
      <p className="text-[9px] text-text-muted">
        {applying
          ? `Applying ${profiles.find(profile => profile.id === applying)?.label || applying}…`
          : activeProfile?.description || 'Choose a starting bundle, then freely override model, steps, attention, LoRAs, or resolution.'}
      </p>
      {activeProfile?.download_required && (
        <p className="text-[9px] text-amber-400">
          {(activeProfile.download_components || ['Model/adapter']).join(' + ')} download needed in the shared host cache · download time is separate from model loading and the run/upscale estimate.
        </p>
      )}
      {unavailableReasons.length > 0 && (
        <p className="text-[9px] text-amber-400">{unavailableReasons.join(' ')}</p>
      )}
      {error && <p className="text-[9px] text-red-400">{error}</p>}
    </div>
  )
}
