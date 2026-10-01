import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Download, Eye, Film, Loader2, Pause, Play, Plus, RotateCcw, Save } from 'lucide-react'
import { appendEditorClip, exportEditorProject, getEditorPreviewUrl, openOutputInEditor, projectReferenceSafeErrorMessage, saveEditorProject, type EditorProject } from '../api/client'
import { privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview, subscribePrivatePreviewReveal } from '../lib/privatePreview'
import { useStore } from '../stores/useStore'
import type { OutputFile } from '../types'

type SaveState = 'saved' | 'unsaved' | 'saving' | 'error'

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
  const source = clip && project.assets[clip.asset_id]
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

export function EditorWorkspace({ source }: { source: OutputFile }) {
  const closeEditor = useStore(state => state.closeEditor)
  const outputs = useStore(state => state.outputs)
  const [draft, setProject] = useState<EditorProject | null>(null)
  const [loadedSource, setLoadedSource] = useState('')
  const sourceKey = privatePreviewIdentity(source.workspace, source.name, source.revision)
  const project = loadedSource === sourceKey && draft?.workspace === source.workspace ? draft : null
  const [activeClipId, setActiveClipId] = useState('')
  const [appendName, setAppendName] = useState('')
  const [appendPending, setAppendPending] = useState(false)
  const [appendError, setAppendError] = useState('')
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
  const sourceAsset = clip && project?.assets[clip.asset_id]
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
  }

  const reorder = (direction: -1 | 1) => {
    if (!projectRef.current || !clip || saving.current || appending.current || exporting.current || !isCurrent(scope.current)) return
    updateDraft(moveClip(projectRef.current, clip.id, direction))
  }

  const handleAppend = async () => {
    const epoch = scope.current
    if (!projectRef.current || !isCurrent(epoch) || appending.current || exporting.current || saveState === 'error') return
    const output = availableVideos(projectRef.current, useStore.getState().outputs).find(item => item.name === appendName)
    if (!output || sequenceClips(projectRef.current).length >= 8) return
    appending.current = true
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
      const currentOutput = availableVideos(projectRef.current, useStore.getState().outputs)
        .find(item => item.name === output.name && item.revision === output.revision)
      if (!currentOutput) {
        setAppendError('This Gallery video changed. Choose it again before adding it.')
        setAppendName('')
        return
      }
      const appended = await appendEditorClip(source.workspace, projectRef.current, output.name, output.revision)
      if (!isCurrent(epoch)) return
      projectRef.current = appended
      setProject(appended)
      selectClip(sequenceClips(appended).at(-1)?.id ?? '')
      setSaveState('saved')
      setAppendName('')
      setExportState('idle')
      setExportError('')
    } catch (reason) {
      if (isCurrent(epoch)) setAppendError(projectReferenceSafeErrorMessage(reason, 'This video could not be added. Try again.'))
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
  const sequenceDuration = clips.reduce((total, item) => total + item.duration, 0)
  const selectedIndex = clips.findIndex(item => item.id === clip?.id)
  const candidates = project ? availableVideos(project, outputs) : []
  const busy = appendPending || exportState === 'submitting'
  const canReorder = !busy && !savePending

  const handleExport = async () => {
    const epoch = scope.current
    if (!project || !canTrim || saveState !== 'saved' || saving.current || exporting.current || appending.current || !isCurrent(epoch)) return
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
              <div className="relative flex aspect-video items-center justify-center bg-black">
                {revealed ? (
                  <video key={privateIdentity} ref={preview} src={getEditorPreviewUrl(sourceAsset?.output_id ?? source.name, source.workspace, sourceAsset?.output_revision ?? '')} preload="metadata" playsInline
                    onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
                    onTimeUpdate={event => { if (event.currentTarget.currentTime >= trimEnd) event.currentTarget.pause() }}
                    onError={() => setPlaybackError(true)} className="h-full w-full object-contain" />
                ) : (
                  <button type="button" onClick={() => { revealPrivatePreview(privateIdentity); setRevealedIdentity(privateIdentity) }}
                    className="flex min-h-11 items-center gap-2 rounded-lg border border-border bg-bg-secondary px-4 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
                    <Eye size={16} aria-hidden="true" /> Reveal private preview
                  </button>
                )}
              </div>
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
                disabled={!canTrim || saveState !== 'saved' || exportState !== 'idle' || appendPending}
                className="mt-5 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-border bg-bg-primary px-4 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50">
                {exportState === 'submitting' ? <Loader2 size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
                {exportState === 'submitting' ? 'Queuing export…' : exportState === 'queued' ? 'Export queued' : 'Export MP4'}
              </button>
              {exportState === 'queued' && <p className="mt-3 text-xs leading-relaxed text-text-secondary" role="status">Track the export in Queue. The finished MP4 will appear in Gallery.</p>}
              {exportError && <p className="mt-3 text-sm text-red-400" role="alert">{exportError}</p>}
              {saveState !== 'saved' && <p className="mt-3 text-xs text-text-muted">The {clips.length === 1 ? 'cut' : 'sequence'} must finish saving before export.</p>}
            </aside>
          </div>

          <section className="rounded-xl border border-border bg-bg-secondary p-4 md:p-6" aria-label="Video timeline">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="text-sm font-semibold">Timeline</h2><span className="text-xs tabular-nums text-text-muted">{displayTime(sequenceDuration)} total · {clips.length}/8 clips</span></div>
            <ol className="mb-4 flex min-w-0 gap-3 overflow-x-auto pb-2" aria-label="Clips in playback order">
              {clips.map((item, index) => {
                const asset = project.assets[item.asset_id]
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
                  {appendPending ? <Loader2 size={15} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />}
                  {appendPending ? 'Adding clip…' : 'Add clip'}
                </button>
              </div>
              <p className="mt-2 text-xs text-text-muted">{clips.length >= 8 ? 'This sequence has reached its 8-clip limit.'
                : candidates.length === 0 ? 'No other loaded Gallery videos are available. Return to Gallery to load more videos from this project.'
                : 'The selected video is added after the last clip. Pending edits save before it is added.'}</p>
              {appendPending && <p className="mt-2 text-xs text-text-secondary" role="status">Saving pending edits and adding the selected video…</p>}
              {appendError && <p className="mt-3 text-sm text-red-400" role="alert">{appendError}</p>}
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
        </div>
      ) : null}
    </main>
  )
}
