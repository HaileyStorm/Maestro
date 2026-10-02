import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Download, Eye, Film, Loader2, Pause, Play, Plus, RotateCcw, Save, Trash2 } from 'lucide-react'
import { addEditorAudio, appendEditorClip, exportEditorProject, getEditorPreviewUrl, openOutputInEditor, projectReferenceSafeErrorMessage, saveEditorProject, type EditorProject } from '../api/client'
import { privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview, subscribePrivatePreviewReveal } from '../lib/privatePreview'
import { useStore } from '../stores/useStore'
import type { OutputFile } from '../types'

type SaveState = 'saved' | 'unsaved' | 'saving' | 'error'
type TextLayer = EditorProject['tracks'][number]['items'][number] & { text: string; position: 'top' | 'center' | 'bottom' }

function textLayers(project: EditorProject): TextLayer[] {
  return (project.tracks.find(track => track.id === 'titles-main')?.items ?? [])
    .filter((item): item is TextLayer => typeof item.text === 'string' && Boolean(item.position))
}

function changeText(project: EditorProject, id: string, change: Partial<TextLayer> | null): EditorProject {
  return { ...project, tracks: project.tracks.map(track => track.id === 'titles-main'
    ? { ...track, items: change === null ? track.items.filter(item => item.id !== id)
      : track.items.map(item => item.id === id ? { ...item, ...change, id } : item) } : track) }
}

function addText(project: EditorProject, id: string, start: number, duration: number): EditorProject {
  if (textLayers(project).length >= 8) return project
  const item: TextLayer = { id, text: 'Your text', start, duration, position: 'bottom', source_in: 0, speed: 1 }
  const exists = project.tracks.some(track => track.id === 'titles-main')
  return { ...project, tracks: exists ? project.tracks.map(track => track.id === 'titles-main'
    ? { ...track, items: [...track.items, item] } : track)
    : [...project.tracks, { id: 'titles-main', name: 'Titles', type: 'text', items: [item] }] }
}

// Python round uses ties-to-even; mirror the sequence export's frame clock.
function clipFrames(duration: number, fps: number) {
  const value = duration * fps
  const floor = Math.floor(value)
  return Math.max(1, value - floor === 0.5 ? floor + (floor % 2) : Math.round(value))
}

function renderedDuration(project: EditorProject) {
  const clips = sequenceClips(project)
  return clips.reduce((sum, item) => sum + (clips.length > 1
    ? clipFrames(item.duration, project.canvas.fps) / project.canvas.fps : item.duration), 0)
}

function titleLayout(text: string, width: number, height: number, position: TextLayer['position']) {
  const lines = text.split('\n')
  const context = document.createElement('canvas').getContext('2d')
  let size = Math.max(1, Math.round(height * 0.05))
  const padding = Math.max(1, Math.round(height * 0.015))
  let measured = 0
  do {
    if (context) context.font = `${size}px "Maestro Editor"`
    measured = Math.max(...lines.map(line => context?.measureText(line).width ?? line.length * size * 0.6))
    if (measured + padding * 2 <= width * 0.9 || size === 1) break
    size -= 1
  } while (size > 0)
  const lineHeight = Math.max(1, Math.round(size * 1.2))
  const naturalWidth = Math.ceil(measured) + 2 * padding
  const boxWidth = Math.min(Math.floor(width * 0.9), naturalWidth)
  const boxHeight = lines.length * lineHeight + 2 * padding
  const margin = Math.round(height * 0.06)
  return { lines, size, padding, lineHeight, boxWidth, boxHeight, naturalWidth, scaleX: boxWidth / naturalWidth, x: (width - boxWidth) / 2,
    y: position === 'top' ? margin : position === 'center' ? (height - boxHeight) / 2 : height - margin - boxHeight }
}

function displayTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${(safe % 60).toFixed(1).padStart(4, '0')}`
}

function sequenceClips(project: EditorProject) {
  return project.tracks.find(track => track.id === 'video-main')?.items ?? []
}

function sequenceStarts(items: EditorProject['tracks'][number]['items']) {
  let start = 0
  return items.map(item => {
    const next = { ...item, start }
    start += item.duration
    return next
  })
}

function changeTrim(project: EditorProject, clipId: string, start: number, end: number): EditorProject {
  const clip = sequenceClips(project).find(item => item.id === clipId)
  const source = clip && project.assets[clip.asset_id ?? '']
  if (!clip || !source) return project
  const total = source.duration
  const minimum = Math.min(0.1, total)
  const nextStart = Math.min(Math.max(0, start), Math.max(0, total - minimum))
  const nextEnd = Math.min(Math.max(nextStart + minimum, end), total)
  return {
    ...project,
    tracks: project.tracks.map(track => track.id === 'video-main'
      ? { ...track, items: sequenceStarts(track.items.map(item => item.id === clipId
        ? { ...item, source_in: nextStart, duration: nextEnd - nextStart }
        : item)) }
      : track),
  }
}

function moveClip(project: EditorProject, clipId: string, direction: -1 | 1): EditorProject {
  const items = sequenceClips(project)
  const index = items.findIndex(item => item.id === clipId)
  const destination = index + direction
  if (index < 0 || destination < 0 || destination >= items.length) return project
  const reordered = [...items]
  ;[reordered[index], reordered[destination]] = [reordered[destination], reordered[index]]
  return { ...project, tracks: project.tracks.map(track => track.id === 'video-main'
    ? { ...track, items: sequenceStarts(reordered) } : track) }
}

function availableVideos(project: EditorProject, outputs: OutputFile[]): OutputFile[] {
  const imported = new Set(Object.values(project.assets).map(asset => asset.output_id))
  return outputs.filter(output => output.workspace === project.workspace && output.type === 'video'
    && Boolean(output.revision) && !imported.has(output.name))
}

function audioLayer(project: EditorProject) {
  return project.tracks.find(track => track.id === 'audio-main')?.items[0]
}

function availableAudio(project: EditorProject, outputs: OutputFile[]) {
  return outputs.filter(output => output.workspace === project.workspace && output.type === 'audio' && Boolean(output.revision))
}

function changeAudio(project: EditorProject, change: Partial<NonNullable<ReturnType<typeof audioLayer>>> | null): EditorProject {
  return { ...project, tracks: project.tracks.map(track => track.id === 'audio-main'
    ? { ...track, items: change === null ? [] : track.items.map(item => ({ ...item, ...change, id: item.id, asset_id: item.asset_id })) } : track) }
}

const audioInputClass = 'min-h-11 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50'
const audioButtonClass = 'flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50'

function AudioLayerPanel({ project, outputs, busy, error, stopKey, videoPlaying, onAudition, onImport, onChange }: {
  project: EditorProject; outputs: OutputFile[]; busy: boolean; error: string; stopKey?: string; videoPlaying: boolean;
  onAudition: () => void; onImport: (name: string) => void; onChange: (project: EditorProject) => void
}) {
  const layer = audioLayer(project)
  const asset = project.assets[layer?.asset_id ?? '']
  const choices = availableAudio(project, outputs)
  const [name, setName] = useState('')
  const [playing, setPlaying] = useState(false)
  const [mediaErrorIdentity, setMediaErrorIdentity] = useState('')
  const [revealedIdentity, setRevealedIdentity] = useState('')
  const audio = useRef<HTMLAudioElement>(null)
  const picker = useRef<HTMLSelectElement>(null)
  const identity = privatePreviewIdentity(project.workspace, asset?.output_id ?? '', asset?.output_revision ?? '')
  const revealed = asset?.private === false || revealedIdentity === identity || privatePreviewWasRevealed(identity)
  useEffect(() => subscribePrivatePreviewReveal(identity, value => { if (value) setRevealedIdentity(identity) }), [identity])
  const mediaError = mediaErrorIdentity === identity
  useEffect(() => {
    const element = audio.current
    element?.pause()
    if (element) element.volume = layer?.volume ?? 1
    return () => element?.pause()
  }, [project, stopKey, revealed, layer?.volume])
  useEffect(() => { if (videoPlaying || busy) audio.current?.pause() }, [videoPlaying, busy])

  const update = (change: Parameters<typeof changeAudio>[1]) => onChange(changeAudio(project, change))
  const audition = () => {
    const element = audio.current
    if (!element || !layer) return
    if (!element.paused) { element.pause(); return }
    onAudition()
    element.currentTime = layer.source_in
    element.volume = layer.volume ?? 1
    void element.play().catch(() => { if (audio.current === element) setMediaErrorIdentity(identity) })
  }
  return <section className="rounded-xl border border-border bg-bg-secondary p-4 md:p-6" aria-label="Audio layer">
    <h2 className="mb-3 text-sm font-semibold">Audio layer <span className="ml-2 font-normal text-text-secondary">{layer ? '1/1' : '0/1'}</span></h2>
    <p className="mb-5 text-xs leading-relaxed text-text-secondary">Add one audio file from this project’s Gallery. It mixes with the source sound during its chosen interval, without looping or extending the video. Lower the volume if the mix distorts. Video preview plays source sound; audition below plays only this audio layer.</p>
    {!layer || !asset ? <div className="rounded-lg border border-dashed border-border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-0 flex-1 text-sm"><span className="mb-2 block">Audio from this project’s Gallery</span>
          <select ref={picker} value={choices.some(item => item.name === name) ? name : ''} disabled={busy || choices.length === 0}
            onChange={event => setName(event.target.value)} className={audioInputClass}>
            <option value="">Choose audio…</option>{choices.map(output => <option key={output.name} value={output.name}>{output.name}</option>)}
          </select>
        </label>
        <button type="button" disabled={busy || !choices.some(item => item.name === name)} onClick={() => onImport(name)} className={audioButtonClass}><Plus size={15} aria-hidden="true" /> Add audio</button>
      </div>
      <p className="mt-3 text-xs text-text-secondary">{choices.length === 0 ? 'No loaded Gallery audio is available. Return to Gallery, show Audio and load audio from this project, then reopen this edit.' : 'Pending edits save before the selected audio is added.'}</p>
    </div> : <div className="grid min-w-0 gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div className="min-w-0 space-y-3">
        <div className="rounded-lg border border-accent-blue bg-accent-blue/15 p-3">
          <p className="truncate text-sm font-medium" title={asset.name}>{asset.name}</p>
          <p className="mt-2 text-xs tabular-nums text-text-secondary">Source {displayTime(asset.duration)} · Timeline {displayTime(layer.start)}–{displayTime(layer.start + layer.duration)}</p>
          <p className="mt-1 text-xs text-text-secondary">{layer.muted ? 'Muted' : `${Math.round((layer.volume ?? 1) * 100)}% volume`}</p>
        </div>
        <div className="relative h-8 overflow-hidden rounded bg-bg-primary" aria-hidden="true"><div className="absolute inset-y-1 rounded border border-accent-blue bg-accent-blue/25"
          style={{ left: `${Math.min(100, layer.start / Math.max(0.01, renderedDuration(project)) * 100)}%`, width: `${Math.max(0, Math.min(layer.duration, renderedDuration(project) - layer.start)) / Math.max(0.01, renderedDuration(project)) * 100}%` }} /></div>
        {!revealed ? <button type="button" className={audioButtonClass} disabled={busy} onClick={() => { revealPrivatePreview(identity); setRevealedIdentity(identity) }}><Eye size={15} aria-hidden="true" /> Reveal private audio</button> : <>
          <audio key={identity} ref={audio} src={getEditorPreviewUrl(asset.output_id, project.workspace, asset.output_revision)} preload="metadata"
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setMediaErrorIdentity(identity)}
            onTimeUpdate={event => { if (event.currentTarget.currentTime >= layer.source_in + layer.duration) event.currentTarget.pause() }} />
          <button type="button" disabled={busy || mediaError || layer.muted || layer.volume === 0} onClick={audition} className={audioButtonClass}>
            {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}{playing ? 'Pause audio' : 'Audition audio'}</button>
          {mediaError && <p role="alert" className="text-sm text-red-400">This browser could not play the pinned audio. Return to Gallery and reopen the edit, or export and play the MP4.</p>}
        </>}
        <button type="button" disabled={busy} onClick={() => { audio.current?.pause(); update(null); window.requestAnimationFrame(() => picker.current?.focus()) }} className={audioButtonClass}><Trash2 size={15} aria-hidden="true" /> Remove audio layer</button>
      </div>
      <div className="min-w-0 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm"><span className="mb-2 block">Audio source start (seconds)</span>
            <input type="number" min={0} max={asset.duration - 1 / 240} step={0.1} value={layer.source_in} disabled={busy} className={audioInputClass}
              onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 0 && value <= asset.duration - 1 / 240) update({ source_in: value, duration: Math.min(layer.duration, asset.duration - value) }) }} />
          </label>
          <label className="block text-sm"><span className="mb-2 block">Audio source end (seconds)</span>
            <input type="number" min={layer.source_in + 1 / 240} max={asset.duration} step={0.1} value={Number((layer.source_in + layer.duration).toFixed(6))} disabled={busy} className={audioInputClass}
              onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= layer.source_in + 1 / 240 && value <= asset.duration) update({ duration: value - layer.source_in }) }} />
          </label>
        </div>
        <label className="block text-sm"><span className="mb-2 block">Audio timeline start (seconds)</span>
          <input aria-label="Audio timeline start (seconds)" aria-describedby="audio-timeline-help" type="number" min={0} max={86400 - layer.duration} step={0.1} value={layer.start} disabled={busy} className={audioInputClass}
            onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value >= 0 && value + layer.duration <= 86400) update({ start: value }) }} />
          <span id="audio-timeline-help" className="mt-1 block text-xs text-text-secondary">Where the trimmed audio begins in the sequence. This time stays fixed when video clips move.</span>
        </label>
        <label className="block text-sm"><span className="mb-2 flex justify-between"><span>Audio volume</span><span>{Math.round((layer.volume ?? 1) * 100)}%</span></span>
          <input type="range" min={0} max={100} step={1} value={(layer.volume ?? 1) * 100} disabled={busy} onChange={event => update({ volume: Number(event.target.value) / 100 })}
            className="min-h-11 w-full rounded accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
        </label>
        <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={layer.muted ?? false} disabled={busy} onChange={event => update({ muted: event.target.checked })}
          className="h-5 w-5 rounded accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />Mute audio layer</label>
      </div>
    </div>}
    {busy && <p role="status" className="mt-3 text-xs text-text-secondary">Wait for the current Editor operation to finish.</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-400">{error}</p>}
  </section>
}

export function EditorWorkspace({ source }: { source: OutputFile }) {
  const closeEditor = useStore(state => state.closeEditor)
  const outputs = useStore(state => state.outputs)
  const [draft, setProject] = useState<EditorProject | null>(null)
  const [loadedSource, setLoadedSource] = useState('')
  const sourceKey = privatePreviewIdentity(source.workspace, source.name, source.revision)
  const project = loadedSource === sourceKey && draft?.workspace === source.workspace ? draft : null
  const [activeClipId, setActiveClipId] = useState('')
  const [activeTextId, setActiveTextId] = useState('')
  const [previewTime, setPreviewTime] = useState(0)
  const [fontReady, setFontReady] = useState(false)
  const addTextButton = useRef<HTMLButtonElement>(null)
  const textField = useRef<HTMLTextAreaElement>(null)
  const [appendName, setAppendName] = useState('')
  const [appendPending, setAppendPending] = useState(false)
  const [appendError, setAppendError] = useState('')
  const [importKind, setImportKind] = useState<'video' | 'audio'>('video')
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [savePending, setSavePending] = useState(false)
  const [error, setError] = useState('')
  const [playbackError, setPlaybackError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [exportState, setExportState] = useState<'idle' | 'submitting' | 'queued'>('idle')
  const [exportError, setExportError] = useState('')
  const preview = useRef<HTMLVideoElement>(null)
  const saving = useRef(false)
  const savingPromise = useRef<Promise<{ project: EditorProject; allEditsSaved: boolean } | null> | null>(null)
  const exporting = useRef(false)
  const appending = useRef(false)
  const projectRef = useRef<EditorProject | null>(null)
  const scope = useRef(0)
  const editVersion = useRef(0)
  const savedVersion = useRef(0)
  const clips = project ? sequenceClips(project) : []
  const clip = clips.find(item => item.id === activeClipId) ?? clips[0]
  const sourceAsset = clip && project?.assets[clip.asset_id ?? '']
  const titles = project ? textLayers(project) : []
  const selectedTitle = titles.find(item => item.id === activeTextId) ?? titles[0]
  const clipIndex = clips.findIndex(item => item.id === clip?.id)
  const clipOffset = project ? clips.slice(0, Math.max(0, clipIndex)).reduce((sum, item) =>
    sum + clipFrames(item.duration, project.canvas.fps) / project.canvas.fps, 0) : 0
  const titleTime = clipOffset + previewTime
  const privateIdentity = privatePreviewIdentity(source.workspace, sourceAsset?.output_id ?? source.name, sourceAsset?.output_revision ?? source.revision)
  const privateSource = sourceAsset?.private !== false
  const [revealedIdentity, setRevealedIdentity] = useState<string | null>(null)
  const revealed = !privateSource || revealedIdentity === privateIdentity || privatePreviewWasRevealed(privateIdentity)

  const isCurrent = useCallback((epoch: number) => {
    const state = useStore.getState()
    return scope.current === epoch && state.activeWorkspace === source.workspace
      && state.editorSource?.workspace === source.workspace && state.editorSource.name === source.name
      && state.editorSource.revision === source.revision
  }, [source.workspace, source.name, source.revision])

  useEffect(() => {
    void document.fonts.load('16px "Maestro Editor"').then(() => setFontReady(true))
  }, [])

  useEffect(() => {
    if (!privateSource) return
    return subscribePrivatePreviewReveal(privateIdentity, value => {
      if (value) setRevealedIdentity(privateIdentity)
    })
  }, [privateSource, privateIdentity])

  useEffect(() => {
    const epoch = ++scope.current
    projectRef.current = null
    setProject(null)
    setActiveClipId('')
    setActiveTextId('')
    setPreviewTime(0)
    setAppendName('')
    setAppendPending(false)
    setAppendError('')
    appending.current = false
    saving.current = false
    savingPromise.current = null
    exporting.current = false
    editVersion.current = 0
    savedVersion.current = 0
    setSaveState('saved')
    setSavePending(false)
    setError('')
    setExportState('idle')
    setExportError('')
    setPlaybackError(false)
    setPlaying(false)
    setLoading(true)
    openOutputInEditor(source.workspace, source.name, source.revision).then(
      opened => {
        if (!isCurrent(epoch)) return
        projectRef.current = opened
        setProject(opened)
        setLoadedSource(privatePreviewIdentity(source.workspace, source.name, source.revision))
        setActiveClipId(sequenceClips(opened)[0]?.id ?? '')
        setLoading(false)
      },
      reason => {
        if (!isCurrent(epoch)) return
        setError(projectReferenceSafeErrorMessage(reason, 'This video could not be opened in Editor.'))
        setLoading(false)
      },
    )
    return () => { scope.current += 1 }
  }, [source.workspace, source.name, source.revision, isCurrent])

  const save = useCallback((snapshot: EditorProject) => {
    const epoch = scope.current
    if (saving.current || !isCurrent(epoch)) return Promise.resolve(null)
    saving.current = true
    const version = editVersion.current
    setSaveState('saving')
    setSavePending(true)
    const pending = (async () => {
      try {
        const saved = await saveEditorProject(source.workspace, snapshot)
        if (!isCurrent(epoch)) return null
        const current = projectRef.current
        const next = current === snapshot ? saved : current && { ...current, revision: saved.revision }
        projectRef.current = next
        setProject(next)
        savedVersion.current = version
        const allEditsSaved = editVersion.current === version
        setSaveState(allEditsSaved ? 'saved' : 'unsaved')
        setError('')
        // Back stays open if a newer trim needs its follow-up save.
        return { project: saved, allEditsSaved }
      } catch (reason) {
        if (!isCurrent(epoch)) return null
        setError(projectReferenceSafeErrorMessage(reason, 'Your edit could not be saved. Try again.'))
        setSaveState('error')
        return null
      } finally {
        if (isCurrent(epoch)) {
          saving.current = false
          savingPromise.current = null
          setSavePending(false)
        }
      }
    })()
    savingPromise.current = pending
    return pending
  }, [source.workspace, isCurrent])

  useEffect(() => {
    if (!project || saveState !== 'unsaved' || saving.current || appendPending) return
    const timer = window.setTimeout(() => { void save(project) }, 700)
    return () => window.clearTimeout(timer)
  }, [project, saveState, save, appendPending])

  const updateDraft = (next: EditorProject) => {
    projectRef.current = next
    setProject(next)
    editVersion.current += 1
    setSaveState('unsaved')
    setError('')
    setExportState('idle')
    setExportError('')
    setAppendError('')
    preview.current?.pause()
    setPlaying(false)
  }

  const updateTrim = (start: number, end: number) => {
    if (!projectRef.current || !clip || appending.current || exporting.current || !isCurrent(scope.current)) return
    updateDraft(changeTrim(projectRef.current, clip.id, start, end))
  }

  const selectClip = (id: string) => {
    preview.current?.pause()
    setPlaying(false)
    setPlaybackError(false)
    setActiveClipId(id)
    setPreviewTime(0)
  }

  const updateText = (change: Partial<TextLayer>) => {
    if (!projectRef.current || !selectedTitle || appending.current || exporting.current || !isCurrent(scope.current)) return
    updateDraft(changeText(projectRef.current, selectedTitle.id, change))
  }

  const handleAddText = () => {
    if (!projectRef.current || busy || !isCurrent(scope.current)) return
    const id = `text-${crypto.randomUUID()}`
    updateDraft(addText(projectRef.current, id, clipOffset, Math.min(3, clip?.duration ?? 3)))
    setActiveTextId(id)
    window.requestAnimationFrame(() => textField.current?.focus())
  }

  const removeText = () => {
    if (!projectRef.current || !selectedTitle || busy || !isCurrent(scope.current)) return
    const remaining = titles.filter(item => item.id !== selectedTitle.id)
    updateDraft(changeText(projectRef.current, selectedTitle.id, null))
    setActiveTextId(remaining[0]?.id ?? '')
    window.requestAnimationFrame(() => remaining.length ? textField.current?.focus() : addTextButton.current?.focus())
  }

  const reorder = (direction: -1 | 1) => {
    if (!projectRef.current || !clip || saving.current || appending.current || exporting.current || !isCurrent(scope.current)) return
    updateDraft(moveClip(projectRef.current, clip.id, direction))
  }

  const handleAppend = async (kind: 'video' | 'audio' = 'video', requestedName = appendName) => {
    const epoch = scope.current
    if (!projectRef.current || !isCurrent(epoch) || appending.current || exporting.current || saveState === 'error') return
    const choices = kind === 'audio' ? availableAudio : availableVideos
    const output = choices(projectRef.current, useStore.getState().outputs).find(item => item.name === requestedName)
    if (!output || (kind === 'video' && sequenceClips(projectRef.current).length >= 8) || (kind === 'audio' && audioLayer(projectRef.current))) return
    appending.current = true
    setImportKind(kind)
    setAppendPending(true)
    setAppendError('')
    preview.current?.pause()
    setPlaying(false)
    try {
      if (savingPromise.current && !(await savingPromise.current)) return
      if (!isCurrent(epoch)) return
      if (savedVersion.current !== editVersion.current) {
        const snapshot = projectRef.current
        if (!snapshot || !(await save(snapshot))?.allEditsSaved) return
      }
      if (!isCurrent(epoch) || !projectRef.current) return
      // Gallery may refresh while a save is pending. Pin exactly the selected
      // current output, and require another selection if its identity changed.
      const currentOutput = choices(projectRef.current, useStore.getState().outputs)
        .find(item => item.name === output.name && item.revision === output.revision)
      if (!currentOutput) {
        setAppendError(`This Gallery ${kind} changed. Choose it again before adding it.`)
        setAppendName('')
        return
      }
      const appended = await (kind === 'audio' ? addEditorAudio : appendEditorClip)(source.workspace, projectRef.current, output.name, output.revision)
      if (!isCurrent(epoch)) return
      projectRef.current = appended
      setProject(appended)
      if (kind === 'video') selectClip(sequenceClips(appended).at(-1)?.id ?? '')
      setSaveState('saved')
      setAppendName('')
      setExportState('idle')
      setExportError('')
    } catch (reason) {
      if (isCurrent(epoch)) setAppendError(projectReferenceSafeErrorMessage(reason, `This ${kind} could not be added. Try again.`))
    } finally {
      if (isCurrent(epoch)) {
        appending.current = false
        setAppendPending(false)
      }
    }
  }

  const duration = sourceAsset?.duration ?? 0
  const trimStart = clip?.source_in ?? 0
  const trimEnd = trimStart + (clip?.duration ?? 0)
  const percentStart = duration > 0 ? trimStart / duration * 100 : 0
  const percentWidth = duration > 0 ? (trimEnd - trimStart) / duration * 100 : 0
  const canTrim = duration >= 0.1 && Boolean(clip) && !appendPending && exportState !== 'submitting'
  const sequenceDuration = project ? renderedDuration(project) : 0
  const bed = project ? audioLayer(project) : undefined
  const audioOutOfRange = Boolean(bed && bed.start + bed.duration > sequenceDuration + 1e-6)
  const titlesOutOfRange = titles.some(item => item.start + item.duration > sequenceDuration + 1e-6)
  const titleFps = clips.length > 1 ? project?.canvas.fps ?? 30 : sourceAsset?.fps || project?.canvas.fps || 30
  const titlesTooShort = titles.some(item => item.duration < 1 / titleFps - 1e-9)
  const selectedIndex = clips.findIndex(item => item.id === clip?.id)
  const candidates = project ? availableVideos(project, outputs) : []
  const busy = appendPending || exportState === 'submitting'
  const canReorder = !busy && !savePending

  const handleExport = async () => {
    const epoch = scope.current
    if (!project || !canTrim || titlesOutOfRange || titlesTooShort || audioOutOfRange || saveState !== 'saved' || saving.current || exporting.current || appending.current || !isCurrent(epoch)) return
    exporting.current = true
    const version = editVersion.current
    setExportState('submitting')
    setExportError('')
    try {
      await exportEditorProject(source.workspace, project)
      if (!isCurrent(epoch)) return
      // The Queue renders cards from /jobs; its /queue poll only updates cards
      // already in the store. Discover this newly submitted job before sending
      // the user there, including when the global queue is paused.
      await useStore.getState().reconnectJobs()
      if (!isCurrent(epoch)) return
      setExportState(editVersion.current === version ? 'queued' : 'idle')
    } catch (reason) {
      if (!isCurrent(epoch)) return
      setExportState('idle')
      if (editVersion.current === version) {
        setExportError(projectReferenceSafeErrorMessage(reason, 'Could not queue this export. Try again.'))
      }
    } finally {
      if (isCurrent(epoch)) exporting.current = false
    }
  }

  const handleBack = async () => {
    const epoch = scope.current
    if (saving.current || saveState === 'saving' || appending.current || exporting.current || !isCurrent(epoch)) return
    if (saveState === 'unsaved' && project && !(await save(project))?.allEditsSaved) return
    if (saveState === 'error') return
    if (isCurrent(epoch)) closeEditor()
  }

  const togglePlayback = () => {
    const video = preview.current
    if (!video) return
    if (video.paused) {
      if (video.currentTime < trimStart || video.currentTime >= trimEnd) video.currentTime = trimStart
      void video.play().catch(() => { if (preview.current === video) setPlaybackError(true) })
    } else video.pause()
  }

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-bg-primary text-text-primary" aria-label="Video Editor">
      <style>{'@font-face{font-family:"Maestro Editor";src:url("/editor-fonts/DejaVuSans.ttf") format("truetype");font-display:block}'}</style>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-bg-secondary px-4 py-3 md:px-7">
        <div className="flex min-w-0 items-center gap-3">
          <button type="button" onClick={() => { void handleBack() }} disabled={savePending || busy}
            className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
            <ArrowLeft size={16} aria-hidden="true" /> Gallery
          </button>
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.18em] text-text-muted">Editor · {source.workspace}</p>
            <h1 className="truncate text-base font-semibold" title={source.name}>{source.name}</h1>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-secondary" role="status" aria-live="polite">
          {saveState === 'saving' ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : <Save size={14} />}
          {saveState === 'saved' ? 'Draft saved' : saveState === 'saving' ? 'Saving draft…' : saveState === 'error' ? 'Save failed' : 'Unsaved changes'}
        </div>
      </header>

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-3 text-text-secondary" role="status"><Loader2 className="animate-spin motion-reduce:animate-none" size={18} /> Opening Editor…</div>
      ) : error && !project ? (
        <div className="mx-auto mt-12 max-w-lg rounded-xl border border-border bg-bg-secondary p-6" role="alert">
          <h2 className="font-semibold">Could not open this video</h2>
          <p className="mt-2 text-sm text-text-secondary">{error}</p>
          <button type="button" onClick={closeEditor} className="mt-5 min-h-11 rounded-lg border border-border px-4 text-sm hover:bg-bg-hover">Return to Gallery</button>
        </div>
      ) : project ? (
        <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 p-4 md:p-7">
          <div className="grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_16rem]">
            <section className="overflow-hidden rounded-xl border border-border bg-bg-secondary" aria-label={clips.length > 1 ? 'Selected clip preview' : 'Video preview'}>
              <div className="relative flex items-center justify-center bg-black" style={{ aspectRatio: `${project.canvas.width}/${project.canvas.height}` }}>
                {revealed ? (
                  <>
                  <video key={privateIdentity} ref={preview} src={getEditorPreviewUrl(sourceAsset?.output_id ?? source.name, source.workspace, sourceAsset?.output_revision ?? '')} preload="metadata" playsInline
                    onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
                    onLoadedMetadata={event => { event.currentTarget.currentTime = trimStart; setPreviewTime(0) }}
                    onSeeked={event => setPreviewTime(Math.max(0, event.currentTarget.currentTime - trimStart))}
                    onTimeUpdate={event => { setPreviewTime(Math.max(0, event.currentTarget.currentTime - trimStart)); if (event.currentTarget.currentTime >= trimEnd) event.currentTarget.pause() }}
                    onError={() => setPlaybackError(true)} className="h-full w-full object-contain" />
                  {fontReady && <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${project.canvas.width} ${project.canvas.height}`} aria-hidden="true" data-testid="text-layer-preview">
                    {titles.filter(item => item.text.trim() && titleTime >= item.start && titleTime < item.start + item.duration).map(item => {
                      const layout = titleLayout(item.text, project.canvas.width, project.canvas.height, item.position)
                      return <g key={item.id}>
                        <rect x={layout.x} y={layout.y} width={layout.boxWidth} height={layout.boxHeight} fill="#000" fillOpacity={0.6} />
                        <text transform={`translate(${layout.x} 0) scale(${layout.scaleX} 1)`} fill="#fff" textAnchor="middle" fontFamily="Maestro Editor" fontSize={layout.size} dominantBaseline="hanging">
                          {layout.lines.map((line, index) => <tspan key={index} x={layout.naturalWidth / 2} y={layout.y + layout.padding + index * layout.lineHeight}>{line}</tspan>)}
                        </text>
                      </g>
                    })}
                  </svg>}
                  </>
                ) : (
                  <button type="button" onClick={() => { revealPrivatePreview(privateIdentity); setRevealedIdentity(privateIdentity) }}
                    className="flex min-h-11 items-center gap-2 rounded-lg border border-border bg-bg-secondary px-4 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
                    <Eye size={16} aria-hidden="true" /> Reveal private preview
                  </button>
                )}
              </div>
              <label className="block px-4 pb-3 text-xs text-text-secondary"><span className="mb-1 block">Preview position · {displayTime(titleTime)} in sequence</span>
                <input aria-label="Preview position" type="range" min={0} max={clip?.duration ?? 0} step={1 / project.canvas.fps} value={Math.min(previewTime, clip?.duration ?? 0)} disabled={!revealed || playbackError || busy}
                  onChange={event => { const local = Number(event.target.value); preview.current?.pause(); if (preview.current) preview.current.currentTime = trimStart + local; setPreviewTime(local) }}
                  className="min-h-11 w-full accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50" />
              </label>
              <div className="flex items-center justify-between gap-3 px-4 py-3">
                <button type="button" onClick={togglePlayback} disabled={!revealed || playbackError || busy}
                  className="flex min-h-11 items-center gap-2 rounded-lg bg-cta px-4 text-sm font-medium text-cta-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                  {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />} {playing ? 'Pause' : clips.length > 1 ? 'Play clip' : 'Play cut'}
                </button>
                <span className="text-xs tabular-nums text-text-secondary">{displayTime(trimStart)}–{displayTime(trimEnd)}</span>
              </div>
              {playbackError && <p className="px-4 pb-3 text-sm text-red-400" role="alert">Preview unavailable. The saved cut is still available.</p>}
            </section>

            <aside className="rounded-xl border border-border bg-bg-secondary p-5" aria-label="Edit details">
              <div className="flex items-center gap-2"><Film size={17} aria-hidden="true" /><h2 className="font-semibold">{clips.length > 1 ? `Clip ${selectedIndex + 1}` : 'Source clip'}</h2></div>
              <p className="mt-3 break-all text-sm text-text-secondary">{sourceAsset?.name ?? source.name}</p>
              <dl className="mt-5 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-text-muted">Original</dt><dd className="tabular-nums">{displayTime(duration)}</dd></div>
                <div><dt className="text-text-muted">Selected clip</dt><dd className="tabular-nums">{displayTime(trimEnd - trimStart)}</dd></div>
                <div className="col-span-2"><dt className="text-text-muted">Total sequence</dt><dd className="tabular-nums">{displayTime(sequenceDuration)} · {clips.length} {clips.length === 1 ? 'clip' : 'clips'}</dd></div>
              </dl>
              <p className="mt-6 border-t border-border pt-5 text-sm leading-relaxed text-text-secondary">{clips.length === 1
                ? 'Your original video stays intact. This cut is saved as an editable draft in the project.'
                : 'Your original videos stay intact. Clips play one after another, in timeline order. The sequence is saved as an editable draft in the project.'}</p>
              {clips.length > 1 && <p className="mt-3 text-xs leading-relaxed text-text-secondary">Export uses the sequence canvas set from the first video ({project.canvas.width} × {project.canvas.height}, {project.canvas.fps} fps). Odd source dimensions are rounded up to an even canvas. Other shapes receive black bars. Each clip uses its first audio track; clips without audio use silence.</p>}
              <button type="button" onClick={() => { void handleExport() }}
                disabled={!canTrim || titlesOutOfRange || titlesTooShort || audioOutOfRange || saveState !== 'saved' || exportState !== 'idle' || appendPending}
                className="mt-5 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-primary px-4 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                {exportState === 'submitting' ? <Loader2 size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
                {exportState === 'submitting' ? 'Queuing export…' : exportState === 'queued' ? 'Export queued' : 'Export MP4'}
              </button>
              {exportState === 'queued' && <p className="mt-3 text-xs leading-relaxed text-text-secondary" role="status">Track the export in Queue. The finished MP4 will appear in Gallery.</p>}
              {exportError && <p className="mt-3 text-sm text-red-400" role="alert">{exportError}</p>}
              {audioOutOfRange && <p className="mt-3 text-sm text-red-400" role="alert">Audio ends after this cut. Shorten the audio layer or move its timeline start before export.</p>}
              {titlesOutOfRange && <p className="mt-3 text-sm text-red-400" role="alert">Text ends after this cut. Shorten or remove those text layers before export.</p>}
              {titlesTooShort && <p className="mt-3 text-sm text-red-400" role="alert">Text must last at least one video frame ({(1 / titleFps).toFixed(3)} seconds). Move its start or end before export.</p>}
              {saveState !== 'saved' && <p className="mt-3 text-xs text-text-muted">The {clips.length === 1 ? 'cut' : 'sequence'} must finish saving before export.</p>}
            </aside>
          </div>

          <section className="rounded-xl border border-border bg-bg-secondary p-4 md:p-6" aria-label="Video timeline">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="text-sm font-semibold">Timeline</h2><span className="text-xs tabular-nums text-text-muted">{displayTime(sequenceDuration)} total · {clips.length}/8 clips</span></div>
            <ol className="mb-4 flex min-w-0 gap-3 overflow-x-auto pb-2" aria-label="Clips in playback order">
              {clips.map((item, index) => {
                const asset = project.assets[item.asset_id ?? '']
                const selected = item.id === clip?.id
                return <li key={item.id} className="w-44 shrink-0">
                  <button type="button" aria-label={`Select clip ${index + 1}: ${asset?.name ?? 'Video'}`} aria-pressed={selected}
                    disabled={busy} onClick={() => selectClip(item.id)}
                    className={`min-h-24 w-full rounded-lg border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50 ${selected ? 'border-accent-blue bg-accent-blue/15' : 'border-border bg-bg-primary hover:bg-bg-hover'}`}>
                    <span className="mb-2 flex items-center justify-between gap-2 text-xs text-text-secondary"><span>Clip {index + 1}</span><span className="tabular-nums">{displayTime(item.duration)}</span></span>
                    <span className="block truncate text-sm font-medium" title={asset?.name}>{asset?.name ?? 'Video'}</span>
                    <span className="mt-1 block text-xs tabular-nums text-text-muted">Starts at {displayTime(item.start)}</span>
                  </button>
                </li>
              })}
            </ol>
            <div className="mb-5 flex flex-wrap items-center gap-3">
              <button type="button" disabled={!canReorder || selectedIndex <= 0} onClick={() => reorder(-1)}
                className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"><ArrowLeft size={14} aria-hidden="true" /> Move earlier</button>
              <button type="button" disabled={!canReorder || selectedIndex >= clips.length - 1} onClick={() => reorder(1)}
                className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"><ArrowRight size={14} aria-hidden="true" /> Move later</button>
            </div>
            <div className="mb-6 border-y border-border py-4">
              <div className="flex flex-wrap items-end gap-3">
                <label className="block min-w-0 flex-1 text-sm"><span className="mb-2 block">Add a video from this project’s Gallery</span>
                  <select value={candidates.some(item => item.name === appendName) ? appendName : ''}
                    disabled={busy || clips.length >= 8 || candidates.length === 0 || saveState === 'error'}
                    onChange={event => { setAppendName(event.target.value); setAppendError('') }}
                    className="min-h-11 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                    <option value="">Choose a video…</option>
                    {candidates.map(output => <option key={output.name} value={output.name}>{output.name}</option>)}
                  </select>
                </label>
                <button type="button" disabled={busy || savePending || saveState === 'error' || clips.length >= 8 || !candidates.some(item => item.name === appendName)}
                  onClick={() => { void handleAppend() }}
                  className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border bg-bg-primary px-4 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                  {appendPending && importKind === 'video' ? <Loader2 size={15} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />}
                  {appendPending && importKind === 'video' ? 'Adding clip…' : 'Add clip'}
                </button>
              </div>
              <p className="mt-2 text-xs text-text-muted">{clips.length >= 8 ? 'This sequence has reached its 8-clip limit.'
                : candidates.length === 0 ? 'No other loaded Gallery videos are available. Return to Gallery to load more videos from this project.'
                : 'The selected video is added after the last clip. Pending edits save before it is added.'}</p>
              {appendPending && importKind === 'video' && <p className="mt-2 text-xs text-text-secondary" role="status">Saving pending edits and adding the selected video…</p>}
              {appendError && importKind === 'video' && <p className="mt-3 text-sm text-red-400" role="alert">{appendError}</p>}
            </div>
            <h3 className="mb-3 text-sm font-medium">Trim clip {selectedIndex + 1}</h3>
            <div className="relative mb-6 h-16 overflow-hidden rounded-lg bg-bg-primary" aria-hidden="true">
              <div className="absolute inset-y-2 rounded-md border border-accent-blue bg-accent-blue/25" style={{ left: `${percentStart}%`, width: `${percentWidth}%` }} />
              <span className="absolute bottom-1 left-2 text-[10px] tabular-nums text-text-muted">0:00</span>
              <span className="absolute bottom-1 right-2 text-[10px] tabular-nums text-text-muted">{displayTime(duration)}</span>
            </div>
            <div className="grid gap-6 md:grid-cols-2">
              <label className="block text-sm"><span className="mb-2 flex justify-between"><span>Start</span><span className="tabular-nums">{displayTime(trimStart)}</span></span>
                <input type="range" min={0} max={Math.max(0, duration - 0.1)} step={0.1} value={trimStart} disabled={!canTrim}
                  onChange={event => updateTrim(Number(event.target.value), trimEnd)} className="min-h-11 w-full rounded accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
              </label>
              <label className="block text-sm"><span className="mb-2 flex justify-between"><span>End</span><span className="tabular-nums">{displayTime(trimEnd)}</span></span>
                <input type="range" min={Math.min(0.1, duration)} max={duration} step={0.1} value={trimEnd} disabled={!canTrim}
                  onChange={event => updateTrim(trimStart, Number(event.target.value))} className="min-h-11 w-full rounded accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
              </label>
            </div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
              <button type="button" disabled={!canTrim || (trimStart === 0 && trimEnd === duration)} onClick={() => updateTrim(0, duration)}
                className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover disabled:opacity-50"><RotateCcw size={14} aria-hidden="true" /> Reset cut</button>
              {saveState === 'error' && <button type="button" disabled={busy} onClick={() => { if (project) void save(project) }} className="min-h-11 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover">Retry save</button>}
            </div>
            {error && <p className="mt-3 text-sm text-red-400" role="alert">{error}</p>}
            {saveState === 'error' && <button type="button" onClick={() => {
              if (window.confirm('Leave Editor and discard the unsaved changes?')) closeEditor()
            }} className="mt-3 min-h-11 rounded-lg px-3 text-sm text-text-secondary underline hover:text-text-primary">Leave without saving</button>}
          </section>
          <AudioLayerPanel key={project.id} project={project} outputs={outputs} busy={busy || saveState === 'error'} error={importKind === 'audio' ? appendError : ''}
            stopKey={clip?.id} videoPlaying={playing} onAudition={() => { preview.current?.pause(); setPlaying(false) }}
            onImport={name => { void handleAppend('audio', name) }}
            onChange={next => { if (!appending.current && !exporting.current && isCurrent(scope.current)) updateDraft(next) }} />
          <section className="rounded-xl border border-border bg-bg-secondary p-4 md:p-6" aria-label="Text layers">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold">Text layers <span className="ml-2 font-normal text-text-secondary">{titles.length}/8</span></h2>
              <button ref={addTextButton} type="button" onClick={handleAddText} disabled={busy || titles.length >= 8}
                className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"><Plus size={15} aria-hidden="true" /> Add text</button>
            </div>
            <p className="mb-5 text-xs leading-relaxed text-text-secondary">Text uses sequence times and stays at those times when clips are trimmed or reordered. Layers can overlap; later rows appear on top. Preview plays the selected clip.</p>
            {titles.length === 0 ? <p className="rounded-lg border border-dashed border-border p-5 text-sm text-text-secondary">Add a title, caption or credit over your video.</p> : <div className="grid min-w-0 gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <ol className="space-y-2" aria-label="Text layer order">
                {titles.map((item, index) => <li key={item.id}><button type="button" aria-pressed={item.id === selectedTitle?.id} disabled={busy} onClick={() => setActiveTextId(item.id)}
                  className={`min-h-16 w-full rounded-lg border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50 ${item.id === selectedTitle?.id ? 'border-accent-blue bg-accent-blue/15' : 'border-border bg-bg-primary hover:bg-bg-hover'}`}>
                  <span className="block truncate text-sm">Text {index + 1} · {item.text || 'Empty text'}</span>
                  <span className="mt-1 block text-xs tabular-nums text-text-secondary">{displayTime(item.start)}–{displayTime(item.start + item.duration)} · {item.position}</span>
                </button></li>)}
              </ol>
              {selectedTitle && <div className="min-w-0 space-y-4">
                <label className="block text-sm"><span className="mb-2 block">Text</span>
                  <textarea aria-label="Text" ref={textField} rows={3} maxLength={160} value={selectedTitle.text} disabled={busy}
                    onChange={event => updateText({ text: event.target.value.split('\n').slice(0, 3).join('\n') })}
                    className="w-full resize-y rounded-lg border border-border bg-bg-primary p-3 text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50" />
                  <span className="mt-1 block text-xs text-text-secondary">{selectedTitle.text.length}/160 characters · up to three lines. White text, fitted to the canvas.</span>
                </label>
                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="block text-sm"><span className="mb-2 block">Starts at (seconds)</span>
                    <input type="number" min={0} max={86400 - selectedTitle.duration} step={0.1} value={selectedTitle.start} disabled={busy}
                      onChange={event => { const start = Number(event.target.value); if (Number.isFinite(start) && start >= 0 && start + selectedTitle.duration <= 86400) updateText({ start }) }}
                      className="min-h-11 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50" />
                  </label>
                  <label className="block text-sm"><span className="mb-2 block">Ends at (seconds)</span>
                    <input type="number" min={selectedTitle.start + 1 / 240} max={86400} step={0.1} value={Number((selectedTitle.start + selectedTitle.duration).toFixed(6))} disabled={busy}
                      onChange={event => { const end = Number(event.target.value); if (Number.isFinite(end) && end - selectedTitle.start >= 1 / 240 && end <= 86400) updateText({ duration: end - selectedTitle.start }) }}
                      className="min-h-11 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50" />
                  </label>
                  <label className="block text-sm"><span className="mb-2 block">Position</span>
                    <select value={selectedTitle.position} disabled={busy} onChange={event => updateText({ position: event.target.value as TextLayer['position'] })}
                      className="min-h-11 w-full rounded-lg border border-border bg-bg-primary px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                      <option value="top">Top</option><option value="center">Center</option><option value="bottom">Bottom</option>
                    </select>
                  </label>
                </div>
                <button type="button" onClick={removeText} disabled={busy} className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"><Trash2 size={15} aria-hidden="true" /> Remove text</button>
              </div>}
            </div>}
          </section>
        </div>
      ) : null}
    </main>
  )
}
