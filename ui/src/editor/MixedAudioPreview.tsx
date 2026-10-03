import { useEffect, useRef, useState, type RefObject } from 'react'
import { getEditorPreviewUrl, type EditorProject } from '../api/client'
import { privatePreviewIdentity, privatePreviewWasRevealed, subscribePrivatePreviewReveal } from '../lib/privatePreview'
import { audioPreviewTarget } from './audioPreviewClock'

type Clip = EditorProject['tracks'][number]['items'][number]

export function MixedAudioPreview({ project, clip, clipOffset, clipDuration, video, playing, busy }: {
  project: EditorProject; clip: Clip; clipOffset: number; clipDuration: number
  video: RefObject<HTMLVideoElement | null>; playing: boolean; busy: boolean
}) {
  const layer = project.tracks.find(track => track.id === 'audio-main')?.items[0]
  const asset = project.assets[layer?.asset_id ?? '']
  const identity = privatePreviewIdentity(project.workspace, asset?.output_id ?? '', asset?.output_revision ?? '')
  const [enabled, setEnabled] = useState(true)
  const [, refreshReveal] = useState(0)
  const [errorIdentity, setErrorIdentity] = useState('')
  const revealed = asset?.private === false || privatePreviewWasRevealed(identity)
  const error = errorIdentity === identity
  const audio = useRef<HTMLAudioElement>(null)
  const control = useRef<HTMLInputElement>(null)
  const wanted = useRef(false)
  useEffect(() => subscribePrivatePreviewReveal(identity, () => refreshReveal(value => value + 1)), [identity])

  useEffect(() => {
    const element = audio.current, picture = video.current
    if (!element || !picture || !layer) return
    let frame = 0, alive = true, pending = false
    const stop = () => { wanted.current = false; element.pause() }
    const sync = () => {
      const target = audioPreviewTarget(layer, picture.currentTime, clip.source_in, clipDuration, clipOffset)
      if (!enabled || !revealed || busy || error || !target || !playing
        || picture.paused || picture.ended || picture.seeking || picture.readyState < 3) {
        stop()
        return
      }
      wanted.current = true
      if (element.readyState < 1) return
      // Follow the media clock, including seeks and stalls, rather than a wall timer.
      if (Math.abs(element.currentTime - target.sourceTime) > 0.08) element.currentTime = target.sourceTime
      element.volume = target.gain
      element.playbackRate = picture.playbackRate
      if (element.paused && !pending) {
        pending = true
        void element.play().then(() => {
          if (!wanted.current || audio.current !== element) element.pause()
        }).catch(() => {
          if (alive && wanted.current) { stop(); setErrorIdentity(identity) }
        }).finally(() => { pending = false })
      }
    }
    const tick = () => { sync(); frame = window.requestAnimationFrame(tick) }
    const events = ['seeking', 'seeked', 'pause', 'ended', 'waiting', 'playing', 'ratechange']
    events.forEach(event => picture.addEventListener(event, sync))
    if (playing && enabled && revealed && !busy && !error) tick()
    else stop()
    return () => {
      alive = false
      window.cancelAnimationFrame(frame)
      events.forEach(event => picture.removeEventListener(event, sync))
      stop()
    }
  }, [project, layer, clip, clipDuration, clipOffset, video, playing, enabled, revealed, busy, error, identity])

  if (!layer || !asset) return null
  return <div className="space-y-2 border-t border-border px-4 py-3">
    <label className="flex min-h-11 items-center gap-3 text-sm">
      <input ref={control} type="checkbox" checked={enabled} disabled={busy} onChange={event => setEnabled(event.target.checked)}
        className="h-5 w-5 rounded accent-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
      Include audio layer in preview
    </label>
    <p className="text-xs leading-relaxed text-text-secondary">Preview uses this layer’s timing, volume and fades alongside the selected clip’s sound.</p>
    {!revealed && <p className="text-xs text-text-secondary">Reveal private audio below to include it in preview.</p>}
    {revealed && <audio key={identity} ref={audio} data-testid="editor-mixed-audio" preload="metadata"
      src={getEditorPreviewUrl(asset.output_id, project.workspace, asset.output_revision)} onError={() => setErrorIdentity(identity)} />}
    {error && <div role="alert" className="space-y-2 text-sm text-red-400">
      <p>The audio layer could not play in this preview. Retry, then press Play, or export the mix to watch it.</p>
      <button type="button" disabled={busy} onClick={() => {
        video.current?.pause(); audio.current?.load(); setErrorIdentity(''); control.current?.focus()
      }} className="min-h-11 rounded-lg border border-border px-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">Retry audio preview</button>
    </div>}
  </div>
}
