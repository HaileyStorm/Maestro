import { useState, type FormEvent } from 'react'
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
  if (selectedKeys.length < 1 || selectedKeys.length > 3 || new Set(selectedKeys).size !== selectedKeys.length) return null
  const stills = selectedKeys.map(key => resolveH3GuideSelection(outputs, [key], activeWorkspace, canGenerate))
  return stills.every((still): still is OutputFile => still !== null) ? stills : null
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

interface Props {
  workspace: string
  still: OutputFile
  secondStill?: OutputFile
  thirdStill?: OutputFile
  models: readonly ModelDef[]
  enabledModels: ReadonlySet<string>
  modelsLoaded: boolean
  isCurrentSelection: () => boolean
  onQueued: () => Promise<void>
}

export function H3GuidePanel({
  workspace,
  still,
  secondStill,
  thirdStill,
  models,
  enabledModels,
  modelsLoaded,
  isCurrentSelection,
  onQueued,
}: Props) {
  const [open, setOpen] = useState(false)
  const [modelType, setModelType] = useState('')
  const [prompt, setPrompt] = useState('')
  const [frameIndexValue, setFrameIndexValue] = useState('')
  const [targetFrameCount, setTargetFrameCount] = useState(124)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [secondFrameIndexValue, setSecondFrameIndexValue] = useState('')
  const [seedValue, setSeedValue] = useState('')
  const [thirdFrameIndexValue, setThirdFrameIndexValue] = useState('')

  const compatibleModels = resolveH3GuideModels(models, enabledModels, modelsLoaded)
  const selectedModel = compatibleModels.find(model => model.model_type === modelType)
    ?? compatibleModels[0]
  const validFrame = isInteriorH3GuideFrame(frameIndexValue, targetFrameCount)
  const frameIndex = validFrame ? Number(frameIndexValue) : null
  const secondFrameIndex = isInteriorH3GuideFrame(secondFrameIndexValue, targetFrameCount)
    ? Number(secondFrameIndexValue) : null
  const validSecondFrame = !secondStill || (secondFrameIndex !== null && secondFrameIndex !== frameIndex)
  const thirdFrameIndex = isInteriorH3GuideFrame(thirdFrameIndexValue, targetFrameCount)
    ? Number(thirdFrameIndexValue) : null
  const validThirdFrame = !thirdStill || (Boolean(secondStill) && thirdFrameIndex !== null && thirdFrameIndex !== frameIndex && thirdFrameIndex !== secondFrameIndex)
  const validSeed = seedValue === '' || (
    /^(?:-1|\d+)$/.test(seedValue) && Number.isSafeInteger(Number(seedValue))
  )
  const canSubmit = Boolean(
    selectedModel
    && prompt.trim()
    && validFrame
    && validSecondFrame
    && validThirdFrame
    && validSeed
    && !pending,
  )

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canSubmit || !selectedModel || frameIndex === null) return
    if (!isCurrentSelection()) {
      setError('The selected Gallery image changed. Refresh Gallery and select it again.')
      return
    }
    setPending(true)
    setError(null)
    try {
      await api.submitH3GalleryStillGuide({
        workspace,
        name: still.name,
        revision: still.revision,
        frame_index: frameIndex,
        ...(secondStill && secondFrameIndex !== null ? {
          second_still: { name: secondStill.name, revision: secondStill.revision, frame_index: secondFrameIndex },
        } : {}),
        ...(thirdStill && thirdFrameIndex !== null ? {
          third_still: { name: thirdStill.name, revision: thirdStill.revision, frame_index: thirdFrameIndex },
        } : {}),
        model_type: selectedModel.model_type as typeof H3_GUIDE_MODEL_ORDER[number],
        prompt,
        settings: {
          video_length: targetFrameCount,
          ...(seedValue !== '' ? { seed: Number(seedValue) } : {}),
        },
        private_output: still.private || Boolean(secondStill?.private) || Boolean(thirdStill?.private),
        explicit_output: still.explicit || Boolean(secondStill?.explicit) || Boolean(thirdStill?.explicit),
      })
    } catch (reason) {
      if (isCurrentSelection()) {
        setError(reason instanceof Error ? reason.message : 'H3 Guide could not be queued. Try again from this project.')
      }
      if (isCurrentSelection()) setPending(false)
      return
    }

    try {
      if (isCurrentSelection()) await onQueued()
    } catch {
      if (isCurrentSelection()) {
        setError('Guide was added to Queue, but Queue could not refresh. Check Queue or reload before trying again.')
      }
    } finally {
      if (isCurrentSelection()) setPending(false)
    }
  }

  const frameLimit = targetFrameCount - 2

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
        {secondStill ? 'Use stills as guides' : 'Use still as guide'}
      </button>
      {open && (
        <form
          id="h3-gallery-still-guide-panel"
          className="mt-2 w-full rounded-lg border border-border bg-bg-secondary p-3"
          onSubmit={submit}
        >
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-text-primary">{thirdStill ? 'Guide three frames with H3' : secondStill ? 'Guide two frames with H3' : 'Guide one frame with H3'}</h3>
            <p className="mt-1 text-xs text-text-muted">
              {thirdStill
                ? 'Use three Gallery stills as visual guides for three different interior frames of a new clip. '
                : secondStill
                ? 'Use two Gallery stills as visual guides for two different interior frames of a new clip. '
                : 'Use one Gallery still as a visual guide for one interior frame of a new clip. '}
              This uses the base FL2VA model only; PinkCherry and W4A8 variants are not included. It does not use guide video or audio. Each still is cropped to fit the clip without stretching.
            </p>
          </div>
          <p className="mb-3 truncate text-xs text-text-secondary" title={still.name}>
            Guide still: <span className="text-text-primary">{still.name}</span>
          </p>
          {secondStill && (
            <p className="mb-3 truncate text-xs text-text-secondary" title={secondStill.name}>
              Second guide still: <span className="text-text-primary">{secondStill.name}</span>
            </p>
          )}
          {thirdStill && (
            <p className="mb-3 truncate text-xs text-text-secondary" title={thirdStill.name}>
              Third guide still: <span className="text-text-primary">{thirdStill.name}</span>
            </p>
          )}
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
          <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
            <span>Guide frame index (0-based)</span>
            <input
              aria-label="Guide frame index, 0-based"
              type="number"
              min={1}
              max={frameLimit}
              step={1}
              inputMode="numeric"
              value={frameIndexValue}
              onChange={event => setFrameIndexValue(event.target.value)}
              placeholder={`Enter a frame from 1 to ${frameLimit}`}
              disabled={pending}
              className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary placeholder:text-text-muted"
            />
          </label>
          <p className="mt-1 text-[11px] text-text-muted">
            {frameIndex === null
              ? `Choose an exact interior frame from 1 to ${frameLimit}; frame 0 and the final frame are not guide positions.`
              : `Frame ${frameIndex} is about ${(frameIndex / 24).toFixed(3)} seconds into the ${targetFrameCount}-frame clip at 24 fps.`}
          </p>
          {secondStill && (
            <>
              <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
                <span>Second guide frame index (0-based)</span>
                <input
                  aria-label="Second guide frame index, 0-based"
                  type="number"
                  min={1}
                  max={frameLimit}
                  step={1}
                  inputMode="numeric"
                  value={secondFrameIndexValue}
                  onChange={event => setSecondFrameIndexValue(event.target.value)}
                  placeholder={`Enter a different frame from 1 to ${frameLimit}`}
                  disabled={pending}
                  className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary placeholder:text-text-muted"
                />
              </label>
              <p className="mt-1 text-[11px] text-text-muted">
                {secondFrameIndex === null
                  ? 'Choose an exact interior frame for the second still.'
                  : secondFrameIndex === frameIndex
                    ? 'Choose two different guide frame indices.'
                    : `Second guide: frame ${secondFrameIndex}, about ${(secondFrameIndex / 24).toFixed(3)} seconds into the clip.`}
              </p>
            </>
          )}
          {thirdStill && (
            <>
              <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
                <span>Third guide frame index (0-based)</span>
                <input
                  aria-label="Third guide frame index, 0-based"
                  type="number"
                  min={1}
                  max={frameLimit}
                  step={1}
                  inputMode="numeric"
                  value={thirdFrameIndexValue}
                  onChange={event => setThirdFrameIndexValue(event.target.value)}
                  placeholder={`Enter a different frame from 1 to ${frameLimit}`}
                  disabled={pending}
                  className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary placeholder:text-text-muted"
                />
              </label>
              <p className="mt-1 text-[11px] text-text-muted">
                {thirdFrameIndex === null
                  ? 'Choose an exact interior frame for the third still.'
                  : !validThirdFrame
                    ? 'Choose three different guide frame indices.'
                    : `Third guide: frame ${thirdFrameIndex}, about ${(thirdFrameIndex / 24).toFixed(3)} seconds into the clip.`}
              </p>
            </>
          )}
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
              className="flex min-h-11 items-center gap-2 rounded-md bg-accent-blue px-3 text-xs font-medium text-white disabled:opacity-40"
            >
              {pending ? 'Adding to Queue…' : 'Create guided clip'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
