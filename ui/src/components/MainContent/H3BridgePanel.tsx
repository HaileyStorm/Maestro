import { useState, type FormEvent } from 'react'
import type { OutputFile } from '../../types'
import * as api from '../../api/client'

export const H3_BRIDGE_GENERATED_FRAMES = Array.from(
  { length: 15 },
  (_, index) => 107 + index * 17,
)

export function resolveH3BridgeSelection(
  outputs: readonly OutputFile[],
  selectedKeys: readonly string[],
  activeWorkspace: string,
  canGenerate: boolean,
): readonly [OutputFile, OutputFile] | null {
  if (!canGenerate || !activeWorkspace || selectedKeys.length !== 2) return null
  const uniqueKeys = new Set(selectedKeys)
  if (uniqueKeys.size !== 2) return null

  const outputByKey = new Map(outputs.map(output => [
    `${output.workspace}\0${output.name}`,
    output,
  ]))
  const selected = [...uniqueKeys].map(key => outputByKey.get(key))
  if (selected.some(output => !output)) return null
  const [first, second] = selected as [OutputFile, OutputFile]
  if (
    first.type !== 'video'
    || second.type !== 'video'
    || first.workspace !== activeWorkspace
    || second.workspace !== activeWorkspace
    || !first.revision.trim()
    || !second.revision.trim()
  ) return null

  return [first, second]
}

interface Props {
  workspace: string
  clips: readonly [OutputFile, OutputFile]
  isCurrentSelection: () => boolean
  onQueued: () => Promise<void>
}

export function H3BridgePanel({ workspace, clips, isCurrentSelection, onQueued }: Props) {
  const [open, setOpen] = useState(false)
  const [clipAName, setClipAName] = useState('')
  const [clipBName, setClipBName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [generatedFrames, setGeneratedFrames] = useState(124)
  const [rerollIndex, setRerollIndex] = useState('0')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const clipA = clips.find(clip => clip.name === clipAName)
  const clipB = clips.find(clip => clip.name === clipBName)
  const validRerollIndex = /^\d+$/.test(rerollIndex)
    && Number.isSafeInteger(Number(rerollIndex))
  const canSubmit = Boolean(
    clipA
    && clipB
    && clipA.name !== clipB.name
    && prompt.trim()
    && validRerollIndex
    && !pending,
  )

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canSubmit || !clipA || !clipB) return
    setPending(true)
    setError(null)
    try {
      await api.submitH3Bridge({
        workspace,
        model_type: 'minimax_h3_ref2va',
        clip_a: { name: clipA.name, revision: clipA.revision },
        clip_b: { name: clipB.name, revision: clipB.revision },
        prompt,
        generated_frames: generatedFrames,
        reroll_index: Number(rerollIndex),
      })
    } catch (reason) {
      if (isCurrentSelection()) {
        setError(reason instanceof Error ? reason.message : 'H3 Bridge could not be queued. Try again from this project.')
      }
      if (isCurrentSelection()) setPending(false)
      return
    }
    try {
      if (isCurrentSelection()) await onQueued()
    } catch {
      if (isCurrentSelection()) {
        setError('Bridge was accepted, but Queue could not refresh. Check Queue or reload before submitting another bridge.')
      }
    } finally {
      if (isCurrentSelection()) setPending(false)
    }
  }

  return (
    <div className={open ? 'min-w-0 basis-full' : 'min-w-0'}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="h3-bridge-panel"
        onClick={() => setOpen(value => !value)}
        disabled={pending}
        className="rounded-md border border-accent-blue/50 px-2 py-1 text-[10px] font-medium text-accent-blue hover:bg-accent-blue/10 disabled:opacity-40"
      >
        Bridge two videos
      </button>
      {open && (
        <form
          id="h3-bridge-panel"
          className="mt-2 w-full rounded-lg border border-border bg-bg-secondary p-3"
          onSubmit={submit}
        >
          <div className="mb-3">
            <h3 className="text-sm font-semibold text-text-primary">Bridge videos with H3</h3>
            <p className="mt-1 text-xs text-text-muted">
              Choose which video comes first. The bridge uses the end of Clip A and the start of Clip B.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-end">
            <label className="flex min-w-0 flex-col gap-1 text-xs text-text-secondary">
              <span>Clip A · first</span>
              <select
                aria-label="Clip A, first video"
                value={clipAName}
                onChange={event => setClipAName(event.target.value)}
                disabled={pending}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              >
                <option value="">Choose the first video…</option>
                {clips.map(clip => (
                  <option key={clip.name} value={clip.name} disabled={clip.name === clipBName}>
                    {clip.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              aria-label="Swap Clip A and Clip B"
              title="Swap video order"
              onClick={() => {
                setClipAName(clipBName)
                setClipBName(clipAName)
              }}
              disabled={!clipAName || !clipBName || pending}
              className="min-h-11 rounded-md border border-border px-3 text-text-secondary hover:text-text-primary disabled:opacity-40 sm:min-h-0 sm:px-2 sm:py-2"
            >
              Swap order
            </button>
            <label className="flex min-w-0 flex-col gap-1 text-xs text-text-secondary">
              <span>Clip B · next</span>
              <select
                aria-label="Clip B, next video"
                value={clipBName}
                onChange={event => setClipBName(event.target.value)}
                disabled={pending}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              >
                <option value="">Choose the next video…</option>
                {clips.map(clip => (
                  <option key={clip.name} value={clip.name} disabled={clip.name === clipAName}>
                    {clip.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="mt-3 flex flex-col gap-1 text-xs text-text-secondary">
            <span>Describe the bridge</span>
            <textarea
              aria-label="Describe the bridge"
              value={prompt}
              onChange={event => setPrompt(event.target.value)}
              placeholder="Describe what should happen as the first video moves into the next."
              rows={3}
              disabled={pending}
              className="min-h-20 resize-y rounded-md border border-border bg-bg-tertiary px-2 py-1.5 text-sm text-text-primary placeholder:text-text-muted"
            />
          </label>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs text-text-secondary">
              <span>Added bridge duration</span>
              <select
                aria-label="Added bridge duration"
                value={generatedFrames}
                onChange={event => setGeneratedFrames(Number(event.target.value))}
                disabled={pending}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              >
                {H3_BRIDGE_GENERATED_FRAMES.map(frames => (
                  <option key={frames} value={frames}>
                    About {((frames - 44) / 24).toFixed(1)} seconds added · {frames - 44} frames ({frames} generated)
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-text-secondary">
              <span>Variation</span>
              <input
                aria-label="Variation"
                type="number"
                min={0}
                step={1}
                value={rerollIndex}
                onChange={event => setRerollIndex(event.target.value)}
                disabled={pending}
                className="min-h-11 rounded-md border border-border bg-bg-tertiary px-2 text-text-primary"
              />
            </label>
          </div>
          <p className="mt-1 text-[11px] text-text-muted">
            The 44 hidden conditioned frames (22 at each end) are trimmed; the duration above is what gets added between clips. Use variation 0 for the default render, or choose a higher number for another variation.
          </p>
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
              {pending ? 'Adding to Queue…' : 'Create bridge'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
