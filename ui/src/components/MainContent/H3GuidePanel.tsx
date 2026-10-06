import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { ModelDef, OutputFile } from '../../types'
import * as api from '../../api/client'

export const H3_GUIDE_TARGET_FRAMES = Array.from(
  { length: 14 },
  (_, index) => 124 + index * 17,
)

const H3_GUIDE_MODEL_ORDER = [
  'minimax_h3',
] as const

// eslint-disable-next-line react-refresh/only-export-components
export function resolveH3GuideSelection(
  outputs: readonly OutputFile[],
  selectedKeys: readonly string[],
  activeWorkspace: string,
  canGenerate: boolean,
): OutputFile | null {
  if (!canGenerate || !activeWorkspace || selectedKeys.length !== 1) return null
  const [selectedKey] = selectedKeys
  const output = outputs.find(item => `${item.workspace}\0${item.name}` === selectedKey)
  if (
    !output
    || output.type !== 'image'
    || output.workspace !== activeWorkspace
    || !output.revision.trim()
  ) return null
  return output
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolveH3GuideSelections(
  outputs: readonly OutputFile[],
  selectedKeys: readonly string[],
  activeWorkspace: string,
  canGenerate: boolean,
): OutputFile[] | null {
  if (selectedKeys.length < 1 || selectedKeys.length > 8 || new Set(selectedKeys).size !== selectedKeys.length) return null
  const stills = selectedKeys.map(key => resolveH3GuideSelection(outputs, [key], activeWorkspace, canGenerate))
  return stills.every((still): still is OutputFile => still !== null) ? stills : null
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolveH3AVGuideSelections(
  outputs: readonly OutputFile[], selectedKeys: readonly string[], activeWorkspace: string,
  canGenerate: boolean, models: readonly ModelDef[], enabledModels: ReadonlySet<string>, modelsLoaded: boolean,
): OutputFile[] | null {
  if (!canGenerate || !activeWorkspace || selectedKeys.length < 1 || selectedKeys.length > 8
    || new Set(selectedKeys).size !== selectedKeys.length
    || !resolveH3GuideModels(models, enabledModels, modelsLoaded).some(model => model.h3_gallery_av_guides === true)) return null
  const sources = selectedKeys.map(key => outputs.find(item => `${item.workspace}\0${item.name}` === key))
  return sources.every((item): item is OutputFile => Boolean(item
    && (item.type === 'video' || item.type === 'audio')
    && item.workspace === activeWorkspace && item.revision.trim())) ? sources : null
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolveH3GuideModels(
  models: readonly ModelDef[],
  enabledModels: ReadonlySet<string>,
  modelsLoaded: boolean,
): ModelDef[] {
  if (!modelsLoaded) return []
  const catalog = new Map(models.map(model => [model.model_type, model]))
  return H3_GUIDE_MODEL_ORDER.flatMap(modelType => {
    const model = catalog.get(modelType)
    if (
      !model
      || !enabledModels.has(modelType)
      || model.availability_status === 'location_declaration_required'
      || model.availability_status === 'legal_blocked'
      || model.execution_allowed === false
    ) return []
    return [model]
  })
}

// eslint-disable-next-line react-refresh/only-export-components
export function isInteriorH3GuideFrame(value: string, frameCount: number): boolean {
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(frameCount)) return false
  const frameIndex = Number(value)
  return Number.isSafeInteger(frameIndex) && frameIndex > 0 && frameIndex < frameCount - 1
}

// eslint-disable-next-line react-refresh/only-export-components
export function isIntervalH3GuideFrame(value: string, frameCount: number): boolean {
  return /^-?\d+$/.test(value) && Number.isSafeInteger(frameCount)
    && Number.isSafeInteger(Number(value)) && Number(value) >= -frameCount && Number(value) < frameCount
}

interface Props {
  workspace: string
  stills: readonly OutputFile[]
  models: readonly ModelDef[]
  enabledModels: ReadonlySet<string>
  modelsLoaded: boolean
  isCurrentSelection: () => boolean
  onQueued: (isCurrent: () => boolean) => Promise<void>
}

export function H3GuidePanel({
  workspace,
  stills,
  models,
  enabledModels,
  modelsLoaded,
  isCurrentSelection,
  onQueued,
}: Props) {
  const lifetime = useRef({ active: true, generation: 0 })
  useEffect(() => {
    lifetime.current.active = true
    return () => {
      lifetime.current.active = false
      lifetime.current.generation += 1
    }
  }, [])
  const [open, setOpen] = useState(false)
  const [modelType, setModelType] = useState('')
  const [prompt, setPrompt] = useState('')
  const [frameIndexValues, setFrameIndexValues] = useState(() => stills.map(() => ''))
  const [targetFrameCount, setTargetFrameCount] = useState(124)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [seedValue, setSeedValue] = useState('')
  const [attentionEngine, setAttentionEngine] = useState<'' | 'sdpa' | 'sol_attn'>('')

  const interval = stills[0]?.type !== 'image'
  const compatibleModels = resolveH3GuideModels(models, enabledModels, modelsLoaded)
  const selectedModel = compatibleModels.find(model => model.model_type === modelType)
    ?? compatibleModels[0]
  const frameIndices = frameIndexValues.map(value => (
    interval ? isIntervalH3GuideFrame(value, targetFrameCount) : isInteriorH3GuideFrame(value, targetFrameCount)
  ) ? Number(value) : null)
  const validFrames = stills.length >= 1 && stills.length <= 8
    && frameIndices.length === stills.length && frameIndices.every(value => value !== null)
    && (interval || new Set(frameIndices).size === stills.length)
  const validSeed = seedValue === '' || (
    /^(?:-1|\d+)$/.test(seedValue) && Number.isSafeInteger(Number(seedValue))
  )
  const canSubmit = Boolean(
    selectedModel
    && (!interval || selectedModel.h3_gallery_av_guides === true)
    && prompt.trim()
    && validFrames
    && validSeed
    && !pending,
  )

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const generation = lifetime.current.generation
    const isCurrent = () => lifetime.current.active
      && lifetime.current.generation === generation && isCurrentSelection()
    const still = stills[0]
    const frameIndex = frameIndices[0]
    if (!canSubmit || !selectedModel || !still || frameIndex == null) return
    if (!isCurrent()) {
      if (!lifetime.current.active) return
      setError(interval ? 'The selected Gallery media changed. Refresh Gallery and select it again.' : 'The selected Gallery image changed. Refresh Gallery and select it again.')
      return
    }
    setPending(true)
    setError(null)
    try {
      if (interval) {
        await api.submitH3GalleryAVGuide({
          workspace, model_type: 'minimax_h3', prompt,
          guides: stills.map((item, index) => ({
            name: item.name, revision: item.revision,
            kind: item.type as 'video' | 'audio', frame_index: frameIndices[index]!,
          })),
          settings: {
            video_length: targetFrameCount,
            attention_engine: attentionEngine || 'sdpa',
            ...(seedValue !== '' ? { seed: Number(seedValue) } : {}),
          },
          private_output: stills.some(item => item.private),
          explicit_output: stills.some(item => item.explicit),
        })
      } else await api.submitH3GalleryStillGuide({
        workspace,
        name: still.name,
        revision: still.revision,
        frame_index: frameIndex,
        additional_stills: stills.slice(1).map((item, index) => ({
          name: item.name, revision: item.revision, frame_index: frameIndices[index + 1]!,
        })),
        model_type: selectedModel.model_type as typeof H3_GUIDE_MODEL_ORDER[number],
        prompt,
        settings: {
          video_length: targetFrameCount,
          ...(attentionEngine !== '' ? { attention_engine: attentionEngine } : {}),
          ...(seedValue !== '' ? { seed: Number(seedValue) } : {}),
        },
        private_output: stills.some(item => item.private),
        explicit_output: stills.some(item => item.explicit),
      })
    } catch (reason) {
      if (isCurrent()) {
        setError(reason instanceof Error ? reason.message : 'H3 Guide could not be queued. Try again from this project.')
      }
      if (isCurrent()) setPending(false)
      return
    }

    try {
      if (isCurrent()) await onQueued(isCurrent)
    } catch {
      if (isCurrent()) {
        setError('Guide was added to Queue, but Queue could not refresh. Check Queue or reload before trying again.')
      }
    } finally {
      if (isCurrent()) setPending(false)
    }
  }

  const frameLimit = targetFrameCount - (interval ? 1 : 2)
  const countWord = ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'][stills.length - 1]
  const guideLabel = (index: number) => ['Guide', 'Second guide', 'Third guide'][index] ?? `Guide ${index + 1}`

  return (
    <div className={open ? 'min-w-0 basis-full' : 'min-w-0'}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="h3-gallery-still-guide-panel"
        onClick={() => setOpen(value => !value)}
        disabled={pending}
        className="rounded-md border border-accent-blue/50 px-2 py-1 text-[10px] font-medium text-accent-blue hover:bg-accent-blue/10 disabled:opacity-40"
      >
        {interval ? 'Use media as guides' : stills.length > 1 ? 'Use stills as guides' : 'Use still as guide'}
      </button>
      {open && (
        <form
          id="h3-gallery-still-guide-panel"
          className="mt-2 w-full rounded-lg border border-border bg-bg-secondary p-3"
          onSubmit={submit}
        >
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-text-primary">{interval ? 'Guide video and audio with H3' : `Guide ${countWord} ${stills.length === 1 ? 'frame' : 'frames'} with H3`}</h3>
            <p className="mt-1 text-xs text-text-muted">
              {interval ? 'Place selected Gallery video or audio into a new clip as timeline guides. Video contributes picture only; audio contributes sound only. Sources may be shortened to fit the clip. This experimental feature uses the base FL2VA model. '
                : stills.length === 1
                ? 'Use one Gallery still as a visual guide for one interior frame of a new clip. '
                : `Use ${countWord} Gallery stills as visual guides for ${countWord} different interior frames of a new clip. `}
              {!interval && 'This uses the base FL2VA model only; PinkCherry and W4A8 variants are not included. It does not use guide video or audio. Each still is cropped to fit the clip without stretching.'}
            </p>
          </div>
          {stills.map((still, index) => (
            <p key={still.name} className="mb-3 truncate text-xs text-text-secondary" title={still.name}>
              {guideLabel(index)} {interval ? still.type : 'still'}: <span className="text-text-primary">{still.name}</span>
            </p>
          ))}
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="flex min-w-0 flex-col gap-1 text-xs text-text-secondary">
              <span>FL2VA model</span>
              <select
                aria-label="FL2VA model"
                value={selectedModel?.model_type ?? ''}
                onChange={event => setModelType(event.target.value)}
                disabled={pending || !modelsLoaded || compatibleModels.length === 0}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              >
                {compatibleModels.length === 0 && <option value="">No enabled FL2VA model available</option>}
                {compatibleModels.map(model => (
                  <option key={model.model_type} value={model.model_type}>{model.name}</option>
                ))}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1 text-xs text-text-secondary">
              <span>Target clip length</span>
              <select
                aria-label="Target clip length"
                value={targetFrameCount}
                onChange={event => setTargetFrameCount(Number(event.target.value))}
                disabled={pending}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              >
                {H3_GUIDE_TARGET_FRAMES.map(frames => (
                  <option key={frames} value={frames}>
                    About {(frames / 24).toFixed(1)} seconds · {frames} frames
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
            <span>Attention</span>
            <select
              aria-label="Guide attention"
              value={attentionEngine}
              onChange={event => setAttentionEngine(event.target.value as '' | 'sdpa' | 'sol_attn')}
              disabled={pending}
              className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
            >
              <option value="">{interval ? 'Dense SDPA (guide default)' : 'Use model default'}</option>
              <option value="sdpa">Dense SDPA</option>
              <option value="sol_attn">Sol</option>
            </select>
          </label>
          <p className="mt-1 text-[11px] text-text-muted">
            Applies to this clip only. Dense SDPA uses dense attention throughout. Sol can use sparse attention after its initial dense steps, with dense fallback when needed.
          </p>
          <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
            <span>Seed (optional)</span>
            <input
              aria-label="Seed (optional)"
              type="number"
              min={-1}
              max={Number.MAX_SAFE_INTEGER}
              step={1}
              value={seedValue}
              onChange={event => setSeedValue(event.target.value)}
              placeholder="Leave blank for model default"
              disabled={pending}
              className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary placeholder:text-text-muted"
            />
          </label>
          <p className="mt-1 text-[11px] text-text-muted" aria-live="polite">
            {validSeed
              ? 'Reuse a whole-number seed to compare settings. Use -1 for a random seed.'
              : 'Enter -1 or a whole number from 0 to 9007199254740991, or leave this blank.'}
          </p>
          {stills.map((still, index) => {
            const frameIndex = frameIndices[index] ?? null
            const repeated = !interval && frameIndex !== null && frameIndices.filter(value => value === frameIndex).length > 1
            return (
              <div key={still.name}>
                <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
                  <span>{guideLabel(index)} frame index (0-based)</span>
                  <input
                    aria-label={`${guideLabel(index)} frame index, 0-based`}
                    type="number"
                    min={interval ? -targetFrameCount : 1}
                    max={frameLimit}
                    step={1}
                    inputMode="numeric"
                    value={frameIndexValues[index] ?? ''}
                    onChange={event => {
                      const frameValue = event.target.value
                      setFrameIndexValues(values => values.map((value, ordinal) => ordinal === index ? frameValue : value))
                    }}
                    placeholder={interval ? `Enter a frame from 0 to ${frameLimit}` : `Enter a different frame from 1 to ${frameLimit}`}
                    disabled={pending}
                    className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary placeholder:text-text-muted"
                  />
                </label>
                <p className="mt-1 text-[11px] text-text-muted">
                  {interval
                    ? frameIndex === null
                      ? `Choose a whole-number start frame from 0 to ${frameLimit}. Negative frames count backward from the end (${ -targetFrameCount } to -1).`
                      : `Starts about ${((frameIndex < 0 ? targetFrameCount + frameIndex : frameIndex) / 24).toFixed(3)} seconds into the clip. Guides may overlap; their selection order is retained.`
                    : frameIndex === null
                    ? `Choose an exact interior frame from 1 to ${frameLimit}; frame 0 and the final frame are not guide positions.`
                    : repeated
                      ? `Choose ${countWord} different guide frame indices.`
                      : `Frame ${frameIndex} is about ${(frameIndex / 24).toFixed(3)} seconds into the ${targetFrameCount}-frame clip at 24 fps.`}
                </p>
              </div>
            )
          })}
          <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
            <span>Describe the clip</span>
            <textarea
              aria-label="Describe the clip"
              value={prompt}
              onChange={event => setPrompt(event.target.value)}
              placeholder="Describe the clip you want to create."
              rows={3}
              disabled={pending}
              className="min-h-20 resize-y rounded-md border border-border bg-bg-tertiary px-2 py-1.5 text-sm text-text-primary placeholder:text-text-muted"
            />
          </label>
          {!modelsLoaded && <p className="mt-2 text-xs text-text-muted">Loading available FL2VA models…</p>}
          {modelsLoaded && compatibleModels.length === 0 && (
            <p className="mt-2 text-xs text-text-muted">No enabled FL2VA model is available. Check model settings and access, then refresh.</p>
          )}
          {error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              disabled={pending}
              className="min-h-11 rounded-md border border-border px-3 text-xs text-text-secondary hover:text-text-primary disabled:opacity-40"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="flex min-h-11 items-center gap-2 rounded-md bg-accent-blue px-3 text-xs font-medium text-bg-primary disabled:opacity-40"
            >
              {pending ? 'Adding to Queue…' : 'Create guided clip'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
