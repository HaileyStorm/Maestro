import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Eye, Film, Loader2, Pause, Play, RotateCcw, Save } from 'lucide-react'
import { getEditorPreviewUrl, openOutputInEditor, projectReferenceSafeErrorMessage, saveEditorProject, type EditorProject } from '../api/client'
import { privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview, subscribePrivatePreviewReveal } from '../lib/privatePreview'
import { useStore } from '../stores/useStore'
import type { OutputFile } from '../types'

type SaveState = 'saved' | 'unsaved' | 'saving' | 'error'

function displayTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${(safe % 60).toFixed(1).padStart(4, '0')}`
}

function sourceClip(project: EditorProject) {
  return project.tracks.find(track => track.id === 'video-main')?.items[0]
}

function changeTrim(project: EditorProject, start: number, end: number): EditorProject {
  const clip = sourceClip(project)
  const source = project.assets['source-video']
  if (!clip || !source) return project
  const total = source.duration
  const minimum = Math.min(0.1, total)
  const nextStart = Math.min(Math.max(0, start), Math.max(0, total - minimum))
  const nextEnd = Math.min(Math.max(nextStart + minimum, end), total)
  return {
    ...project,
    tracks: project.tracks.map(track => track.id === 'video-main'
      ? { ...track, items: track.items.map((item, index) => index === 0
        ? { ...item, source_in: nextStart, duration: nextEnd - nextStart }
        : item) }
      : track),
  }
}

export function EditorWorkspace({ source }: { source: OutputFile }) {
  const closeEditor = useStore(state => state.closeEditor)
  const [project, setProject] = useState<EditorProject | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [error, setError] = useState('')
  const [playbackError, setPlaybackError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [playing, setPlaying] = useState(false)
  const preview = useRef<HTMLVideoElement>(null)
  const saving = useRef(false)
  const editVersion = useRef(0)
  const sourceAsset = project?.assets['source-video']
  const privateIdentity = privatePreviewIdentity(source.workspace, source.name, sourceAsset?.output_revision ?? source.revision)
  const privateSource = sourceAsset?.private !== false
  const [revealedIdentity, setRevealedIdentity] = useState<string | null>(null)
  const revealed = !privateSource || revealedIdentity === privateIdentity || privatePreviewWasRevealed(privateIdentity)

  useEffect(() => {
    if (!privateSource) return
    return subscribePrivatePreviewReveal(privateIdentity, value => {
      if (value) setRevealedIdentity(privateIdentity)
    })
  }, [privateSource, privateIdentity])

  useEffect(() => {
    let active = true
    setLoading(true)
    openOutputInEditor(source.workspace, source.name, source.revision).then(
      opened => {
        if (!active) return
        setProject(opened)
        setLoading(false)
      },
      reason => {
        if (!active) return
        setError(projectReferenceSafeErrorMessage(reason, 'This video could not be opened in Editor.'))
        setLoading(false)
      },
    )
    return () => { active = false }
  }, [source.workspace, source.name, source.revision])

  const save = useCallback(async (snapshot: EditorProject): Promise<boolean> => {
    if (saving.current) return false
    saving.current = true
    const version = editVersion.current
    setSaveState('saving')
    try {
      const saved = await saveEditorProject(source.workspace, snapshot)
      setProject(current => current === snapshot ? saved : current && { ...current, revision: saved.revision })
      const allEditsSaved = editVersion.current === version
      setSaveState(allEditsSaved ? 'saved' : 'unsaved')
      setError('')
      // Back may be awaiting this save. Keep the Editor open if a newer edit
      // was made while the request was in flight so its follow-up save can run.
      return allEditsSaved
    } catch (reason) {
      setError(projectReferenceSafeErrorMessage(reason, 'Your edit could not be saved. Try again.'))
      setSaveState('error')
      return false
    } finally {
      saving.current = false
    }
  }, [source.workspace])

  useEffect(() => {
    if (!project || saveState !== 'unsaved' || saving.current) return
    const timer = window.setTimeout(() => { void save(project) }, 700)
    return () => window.clearTimeout(timer)
  }, [project, saveState, save])

  const updateTrim = (start: number, end: number) => {
    setProject(current => current ? changeTrim(current, start, end) : current)
    editVersion.current += 1
    setSaveState('unsaved')
    setError('')
    preview.current?.pause()
    setPlaying(false)
  }

  const clip = project && sourceClip(project)
  const duration = project?.assets['source-video']?.duration ?? 0
  const trimStart = clip?.source_in ?? 0
  const trimEnd = trimStart + (clip?.duration ?? 0)
  const percentStart = duration > 0 ? trimStart / duration * 100 : 0
  const percentWidth = duration > 0 ? (trimEnd - trimStart) / duration * 100 : 0
  const canTrim = duration >= 0.1 && Boolean(clip)

  const handleBack = async () => {
    if (saving.current || saveState === 'saving') return
    if (saveState === 'unsaved' && project && !(await save(project))) return
    if (saveState === 'error') return
    closeEditor()
  }

  const togglePlayback = () => {
    const video = preview.current
    if (!video) return
    if (video.paused) {
      if (video.currentTime < trimStart || video.currentTime >= trimEnd) video.currentTime = trimStart
      void video.play().catch(() => { setPlaybackError(true) })
    } else video.pause()
  }

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto bg-bg-primary text-text-primary" aria-label="Video Editor">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-bg-secondary px-4 py-3 md:px-7">
        <div className="flex min-w-0 items-center gap-3">
          <button type="button" onClick={() => { void handleBack() }} disabled={saving.current || saveState === 'saving'}
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
            <section className="overflow-hidden rounded-xl border border-border bg-bg-secondary" aria-label="Video preview">
              <div className="relative flex aspect-video items-center justify-center bg-black">
                {revealed ? (
                  <video ref={preview} src={getEditorPreviewUrl(source.name, source.workspace, sourceAsset?.output_revision ?? '')} preload="metadata" playsInline
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
                <button type="button" onClick={togglePlayback} disabled={!revealed || playbackError}
                  className="flex min-h-11 items-center gap-2 rounded-lg bg-accent-blue px-4 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-50">
                  {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />} {playing ? 'Pause' : 'Play cut'}
                </button>
                <span className="text-xs tabular-nums text-text-secondary">{displayTime(trimStart)}–{displayTime(trimEnd)}</span>
              </div>
              {playbackError && <p className="px-4 pb-3 text-sm text-red-400" role="alert">Preview unavailable. The saved cut is still available.</p>}
            </section>

            <aside className="rounded-xl border border-border bg-bg-secondary p-5" aria-label="Edit details">
              <div className="flex items-center gap-2"><Film size={17} aria-hidden="true" /><h2 className="font-semibold">Source clip</h2></div>
              <p className="mt-3 break-all text-sm text-text-secondary">{source.name}</p>
              <dl className="mt-5 grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-text-muted">Original</dt><dd className="tabular-nums">{displayTime(duration)}</dd></div>
                <div><dt className="text-text-muted">Selected</dt><dd className="tabular-nums">{displayTime(trimEnd - trimStart)}</dd></div>
              </dl>
              <p className="mt-6 border-t border-border pt-5 text-sm leading-relaxed text-text-secondary">Your original video stays intact. This cut is saved as an editable draft in the project.</p>
              <p className="mt-3 text-xs text-text-muted">Video export will be added in a later Editor update.</p>
            </aside>
          </div>

          <section className="rounded-xl border border-border bg-bg-secondary p-4 md:p-6" aria-label="Video timeline">
            <div className="mb-5 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold">Timeline</h2><span className="text-xs text-text-muted">One source clip</span></div>
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
              {saveState === 'error' && <button type="button" onClick={() => { if (project) void save(project) }} className="min-h-11 rounded-lg border border-border px-3 text-sm hover:bg-bg-hover">Retry save</button>}
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
