import { useStore } from '../../stores/useStore'
import type { ResolutionPreset } from '../../types'

export function ResolutionPresets() {
  const resolutionPreset = useStore(s => s.resolutionPreset)
  const setResolutionPreset = useStore(s => s.setResolutionPreset)
  const resolution = useStore(s => s.params.resolution)
  const deliveryResolution = useStore(s => s.params.delivery_resolution)
  const deliveryFit = useStore(s => s.params.delivery_fit)
  const modelType = useStore(s => s.params.model_type)
  const setH3NativeResolution = useStore(s => s.setH3NativeResolution)
  const modelOptions = useStore(s => s.modelOptions)
  const generationMode = useStore(s => s.generationMode)
  const spatialUpsampling = useStore(s => s.spatialUpsampling)
  const isEdit = generationMode === 'avatar'
  const isH3 = generationMode === 'video' && (
    modelType.startsWith('minimax_h3')
    || String(modelOptions?.architecture || '').startsWith('minimax_h3')
    || String(modelOptions?.model_type || '').startsWith('minimax_h3')
  )
  const h3OptionsReady = String(modelOptions?.model_type || '').startsWith('minimax_h3')
  const nativeResolutions = h3OptionsReady ? (modelOptions?.resolutions || []) : []

  if (isH3 && nativeResolutions.length === 0) {
    return (
      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Resolution</label>
        <select disabled aria-label="Resolution" className="mobile-control-target w-full rounded-lg border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-muted">
          <option>Loading H3-native canvases…</option>
        </select>
      </div>
    )
  }

  if (isH3 && nativeResolutions.length > 0) {
    const selectedIsNative = nativeResolutions.some(option => option.value === resolution)
    const [width, height] = resolution.split('x').map(Number)
    const aspect = width > 0 && height > 0 ? width / height : 0
    const orientation = aspect > 1.05 ? 'landscape' : aspect < 0.95 ? 'portrait' : 'square'
    const flashMatch = /^flashvsr(2pass)?(1(?:\.5)?|2(?:\.5)?|3(?:\.5)?|4)$/.exec(spatialUpsampling)
    const deliveryScale = flashMatch ? Number(flashMatch[2]) : 0
    const learnedWidth = Math.trunc(width * deliveryScale)
    const learnedHeight = Math.trunc(height * deliveryScale)
    const exactDelivery = deliveryResolution?.replace('x', '×')
    const delivery = deliveryScale > 0 && width > 0 && height > 0
      ? exactDelivery && deliveryResolution !== `${learnedWidth}x${learnedHeight}`
        ? `${width}×${height} native → ${learnedWidth}×${learnedHeight} learned upscale → ${exactDelivery} exact delivery · FlashVSR ${flashMatch?.[1] ? 'two-pass ' : ''}${deliveryScale}x + ${deliveryFit === 'center_crop' ? 'center crop/downsample' : deliveryFit}`
        : `${width}×${height} native → ${learnedWidth}×${learnedHeight} delivery · FlashVSR ${flashMatch?.[1] ? 'two-pass ' : ''}${deliveryScale}x`
      : null
    return (
      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Resolution</label>
        <select
          aria-label="Resolution"
          value={resolution}
          onChange={event => setH3NativeResolution(event.target.value)}
          className="mobile-control-target w-full rounded-lg border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
        >
          {!selectedIsNative && (
            <option value={resolution} disabled>{resolution} · choose an H3-native canvas</option>
          )}
          {nativeResolutions.map(option => (
            <option key={option.value} value={option.value}>
              {option.value} · native{option.value === '1344x768' ? ' max' : ''}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[9px] text-text-muted">
          {delivery || (!selectedIsNative
            ? 'This carried-over size is not H3-native; choose one of the exact canvases above.'
            : width > 0 && height > 0
            ? `${width} × ${height} · ${orientation} · ${aspect.toFixed(2)}:1 · exact H3-native canvas`
            : 'Exact H3-native canvas')}
        </p>
      </div>
    )
  }

  const isImage = generationMode === 'image'
  // Prefer model-authored labels/order where available, while retaining the
  // legacy fallback for older model definitions.
  const presets: ResolutionPreset[] = modelOptions?.resolution_preset_order?.length
    ? modelOptions.resolution_preset_order
    : (isEdit || isImage)
      ? ['auto', '480p', '540p', '720p', '1080p']
      : ['480p', '540p', '720p', '1080p']
  const selectedPreset = modelOptions?.resolution_presets?.[resolutionPreset]

  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Resolution</label>
      <div role="group" aria-label="Resolution presets" className="flex max-w-full overflow-x-auto rounded-lg border border-border bg-bg-tertiary p-0.5">
        {presets.map(p => (
          <button
            type="button"
            key={p}
            onClick={() => setResolutionPreset(p)}
            aria-pressed={resolutionPreset === p}
            className={`mobile-control-target min-w-11 flex-1 rounded-md px-1.5 py-1.5 text-xs capitalize transition-all md:min-w-0 ${
              resolutionPreset === p
                ? 'bg-bg-active text-text-primary'
                : 'text-text-secondary hover:text-text-primary'
            } focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue`}
          >
            {p === 'auto' ? 'Auto' : modelOptions?.resolution_presets?.[p]?.label || p}
          </button>
        ))}
      </div>
      {resolutionPreset === 'auto' && (
        <p className="text-[9px] text-text-muted mt-0.5">
          {isEdit ? 'Uses source clip resolution' : isImage ? 'Matches reference image aspect ratio' : 'Auto resolution'}
        </p>
      )}
      {resolutionPreset !== 'auto' && selectedPreset?.hint && (
        <p className={`text-[9px] mt-0.5 ${selectedPreset.experimental ? 'text-amber-300' : 'text-text-muted'}`}>
          {selectedPreset.experimental ? 'Experimental · ' : ''}{selectedPreset.hint}
        </p>
      )}
    </div>
  )
}
