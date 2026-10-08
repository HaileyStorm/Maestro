import { useEffect, useId, useRef, useState, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import type { OutputFile } from '../../types'
import * as api from '../../api/client'
import { beginFaceRepairAdmission, buildFaceRepairRequest, faceRepairAdmissionKey, readFaceRepairAdmission, setFaceRepairAdmission, subscribeFaceRepairAdmission, reviewFaceRange, validFaceBox, validateFaceRepairSource, type FaceBox, type FaceRepairSource } from '../../lib/faceRepairReview'
import { installModalFocus } from '../../lib/modalFocus'
import { privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview, subscribePrivatePreviewReveal } from '../../lib/privatePreview'

const button = 'min-h-11 rounded-md border border-border px-3 py-2 text-sm hover:bg-bg-hover focus-visible:outline-2 focus-visible:outline-accent-blue disabled:opacity-50'
const input = 'min-h-11 w-full rounded-md border border-border bg-bg-primary px-2 py-2 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-accent-blue'

export function FaceRepairPanel({ source, accountScope, isCurrentSelection, onQueued }: {
  source: OutputFile
  accountScope: string
  isCurrentSelection: () => boolean
  onQueued: (isCurrent: () => boolean) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [restore, setRestore] = useState<HTMLElement | null>(null)
  return <>
    <button className={button} onClick={event => { if (isCurrentSelection()) { setRestore(event.currentTarget); setOpen(true) } }}>Repair face</button>
    {open && <FaceRepairEditor source={source} accountScope={accountScope} isCurrentSelection={isCurrentSelection} onQueued={onQueued}
      restoreFocus={restore} onClose={() => setOpen(false)} />}
  </>
}

function FaceRepairEditor({ source, accountScope, isCurrentSelection, onQueued, restoreFocus, onClose }: {
  source: OutputFile; isCurrentSelection: () => boolean
  accountScope: string
  onQueued: (isCurrent: () => boolean) => Promise<void>
  restoreFocus: HTMLElement | null; onClose: () => void
}) {
  const id = useId()
  const dialog = useRef<HTMLDivElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const alive = useRef(true)
  const submissionLatch = useRef(false)
  const closeCurrent = useRef(onClose)
  closeCurrent.current = onClose
  const current = useRef(isCurrentSelection)
  current.current = isCurrentSelection
  const isCurrent = () => alive.current && current.current()
  const identity = privatePreviewIdentity(source.workspace, source.name, source.revision)
  const admissionKey = faceRepairAdmissionKey(accountScope, source)
  const [admission, setAdmission] = useState(() => readFaceRepairAdmission(admissionKey))
  const [revealed, setRevealed] = useState(() => !source.private || privatePreviewWasRevealed(identity))
  const [facts, setFacts] = useState<FaceRepairSource | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [error, setError] = useState('')
  const [frame, setFrame] = useState(0)
  const [loadedFrame, setLoadedFrame] = useState<number | null>(null)
  const [failedFrame, setFailedFrame] = useState<number | null>(null)
  const [frameAttempt, setFrameAttempt] = useState(0)
  const [boxes, setBoxes] = useState<(FaceBox | null)[]>([])
  const [box, setBox] = useState<FaceBox>([0, 0, 1, 1])
  const [shots, setShots] = useState([0])
  const [start, setStart] = useState(1)
  const [end, setEnd] = useState(1)
  const [prompt, setPrompt] = useState('')
  const [strength, setStrength] = useState(0.5)
  const [canvas, setCanvas] = useState(384)
  const [audio, setAudio] = useState<number | null>(null)
  const [steps, setSteps] = useState(20)
  const [seed, setSeed] = useState(42)
  const pending = admission === 'pending', accepted = admission === 'accepted', uncertain = admission === 'uncertain'
  const [queuePending, setQueuePending] = useState(false)
  const drag = useRef<{ x: number; y: number; frame: number } | null>(null)
  const ready = Boolean(facts && revealed && loadedFrame === frame && failedFrame !== frame)
  const disabled = !ready || pending || accepted || uncertain
  const reviewed = boxes.filter(Boolean).length

  useEffect(() => {
    alive.current = true
    const cleanup = installModalFocus({ document, dialog: dialog.current!, initialFocus: close.current!, restoreFocus,
      appRoot: document.getElementById('root'), onClose: () => closeCurrent.current() })
    return () => { alive.current = false; cleanup() }
  }, [restoreFocus])
  useEffect(() => subscribePrivatePreviewReveal(identity, value => setRevealed(!source.private || value)), [identity, source.private])
  useEffect(() => subscribeFaceRepairAdmission(admissionKey, setAdmission), [admissionKey])
  useEffect(() => {
    const abort = new AbortController()
    let live = true
    if (!isCurrentSelection()) return
    void api.getFaceRepairSource({ workspace: source.workspace, name: source.name, revision: source.revision }, abort.signal)
      .then(value => {
        if (!live || !isCurrent()) return
        const next = validateFaceRepairSource(value, source)
        setFacts(next); setBoxes(Array.from({ length: next.frame_count }, () => null))
        const side = Math.max(1, Math.floor(Math.min(next.width, next.height) / 2))
        const left = Math.floor((next.width-side)/2), top = Math.floor((next.height-side)/2)
        setBox([left, top, left+side, top+side])
        setError('')
      }).catch(reason => {
        if (live && isCurrent() && !abort.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not read this source.')
      })
    return () => { live = false; abort.abort() }
    // The parent key binds immutable source, account and selection. Only an explicit read retry starts another GET.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])

  function chooseFrame(next: number) {
    if (!facts || pending || accepted || uncertain || !isCurrent()) return
    setFrame(next); setLoadedFrame(null); drag.current = null
    if (boxes[next]) setBox([...boxes[next]!])
  }
  function applyRange(region: FaceBox | null, from: number, to: number) {
    if (!facts || disabled || submissionLatch.current || !isCurrent()) return
    try { setBoxes(reviewFaceRange(boxes, region, from, to, shots, facts)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Check the frame range.') }
  }
  function point(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return { x: Math.max(0, Math.min(facts!.width, Math.round((event.clientX - bounds.left) / bounds.width * facts!.width))),
      y: Math.max(0, Math.min(facts!.height, Math.round((event.clientY - bounds.top) / bounds.height * facts!.height))) }
  }
  function drawnSquare(first: { x: number; y: number }, last: { x: number; y: number }): FaceBox {
    const side = Math.min(Math.abs(last.x-first.x), Math.abs(last.y-first.y))
    const left = last.x < first.x ? first.x-side : first.x
    const top = last.y < first.y ? first.y-side : first.y
    return [left,top,left+side,top+side]
  }
  async function openQueue() {
    if (!isCurrent() || queuePending) return
    setQueuePending(true)
    try { await onQueued(isCurrent) }
    catch { if (isCurrent()) setError('The repair may be in Queue. Queue refresh failed; try Open Queue again.') }
    finally { if (isCurrent()) setQueuePending(false) }
  }
  async function submit() {
    if (!facts || disabled || submissionLatch.current || !isCurrent()) return
    let request
    try { request = buildFaceRepairRequest(source, facts, boxes, shots, prompt, strength, canvas, audio, steps, seed) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Check the repair settings.'); return }
    submissionLatch.current = true
    if (!beginFaceRepairAdmission(admissionKey)) { setError('This source already has a submission receipt, or browser session storage is unavailable. Check Queue before continuing.'); return }
    setError('')
    try {
      await api.submitFaceRepair(request)
      setFaceRepairAdmission(admissionKey, 'accepted')
      if (!isCurrent()) return
      await openQueue()
    } catch (reason) {
      const ambiguous = !(reason instanceof api.FaceRepairSubmissionError) || reason.uncertain
      setFaceRepairAdmission(admissionKey, ambiguous ? 'uncertain' : null)
      if (!ambiguous) submissionLatch.current = false
      if (!isCurrent()) return
      setError(reason instanceof Error ? reason.message : 'Check Queue before submitting another repair.')
    }
  }

  return createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-2 sm:p-4">
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}
      className="flex max-h-[96dvh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary text-text-primary shadow-xl">
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="min-w-0"><h2 id={`${id}-title`} className="text-lg font-semibold">Repair face</h2>
          <p id={`${id}-description`} className="mt-1 text-sm text-text-secondary">Review the region in each shot. A repaired copy keeps the original audio; frames without a reviewed region stay unchanged.</p>
          <p className="mt-1 break-all text-xs text-text-secondary">{source.name} · MiniMax H3 Base</p></div>
        <button ref={close} className={button} onClick={onClose} aria-label="Close face repair">Close</button>
      </header>
      <div className="overflow-y-auto p-4">
        {!facts && !error && <p role="status">Reading source frames…</p>}
        {error && <p role="alert" className="mb-3 rounded-md border border-border p-3 text-sm">{error}</p>}
        {!facts && error && <button className={button} onClick={() => { setError(''); setAttempt(value => value + 1) }}>Read source again</button>}
        {facts && <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,1fr)]">
          <section aria-label="Review source frames" className="min-w-0 space-y-3">
            {!revealed ? <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-border bg-bg-primary p-4">
              <p className="text-sm">This video’s preview is hidden.</p><button className={button} onClick={() => { if (isCurrent()) revealPrivatePreview(identity) }}>Reveal source preview</button>
            </div> : <div className="relative touch-none overflow-hidden rounded-lg bg-bg-primary ring-1 ring-border" style={{ aspectRatio: `${facts.width}/${facts.height}` }}
              onPointerDown={event => { if (disabled || !isCurrent()) return; drag.current = { ...point(event), frame }; event.currentTarget.setPointerCapture(event.pointerId) }}
              onPointerMove={event => { const first = drag.current; if (!first || first.frame !== frame || disabled) return; setBox(drawnSquare(first,point(event))) }}
              onPointerUp={event => { if (!drag.current || disabled || !isCurrent()) { drag.current = null; return }; const first = drag.current; drag.current = null; setBox(drawnSquare(first,point(event))); event.currentTarget.releasePointerCapture(event.pointerId) }}
              onPointerCancel={() => { drag.current = null }}>
              <img key={`${frame}:${frameAttempt}`} src={`${api.faceRepairFrameUrl({ workspace: source.workspace, name: source.name, revision: source.revision }, frame)}&preview_attempt=${frameAttempt}`}
                alt={`Source frame ${frame + 1}`} draggable={false} className={`absolute inset-0 h-full w-full ${ready ? '' : 'invisible'}`}
                onLoad={event => { if (!isCurrent()) return; if (event.currentTarget.naturalWidth !== facts.width || event.currentTarget.naturalHeight !== facts.height) setFailedFrame(frame); else { setLoadedFrame(frame); setFailedFrame(null) } }}
                onError={() => { if (isCurrent()) setFailedFrame(frame) }} />
              {ready && validFaceBox(box, facts) && <div aria-hidden="true" className="pointer-events-none absolute border-2 border-cyan-300 bg-cyan-300/10"
                style={{ left: `${box[0]/facts.width*100}%`, top: `${box[1]/facts.height*100}%`, width: `${(box[2]-box[0])/facts.width*100}%`, height: `${(box[3]-box[1])/facts.height*100}%` }} />}
              {!ready && <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-3" role="status">
                {failedFrame === frame ? <><p>Could not read this frame.</p><button className={button} onClick={() => { setFailedFrame(null); setLoadedFrame(null); setFrameAttempt(value => value + 1) }}>Read frame again</button></> : 'Loading source frame…'}
              </div>}
            </div>}
            <p className="text-xs text-text-secondary">Draw a square region, or enter its coordinates below. Preview shows the original frame.</p>
            <div className="flex flex-wrap items-center gap-2"><button className={button} disabled={pending || accepted || uncertain || frame === 0} onClick={() => chooseFrame(frame-1)}>Previous</button>
              <label className="flex items-center gap-2 text-sm">Frame<input className={`${input} !w-20`} type="number" min={1} max={facts.frame_count} value={frame+1} disabled={pending || accepted || uncertain}
                onChange={event => { const next = Number(event.target.value)-1; if (Number.isInteger(next) && next >= 0 && next < facts.frame_count) chooseFrame(next) }} /></label>
              <span className="text-sm">of {facts.frame_count} · {boxes[frame] ? 'Region reviewed' : 'Unchanged'}</span>
              <button className={button} disabled={pending || accepted || uncertain || frame === facts.frame_count-1} onClick={() => chooseFrame(frame+1)}>Next</button></div>
            <label className="block text-sm">Browse frames<input aria-label="Browse frames" className="mt-2 min-h-11 w-full" type="range" min={0} max={facts.frame_count-1} value={frame}
              disabled={pending || accepted || uncertain} onChange={event => chooseFrame(Number(event.target.value))} /></label>
            <fieldset disabled={disabled} className="space-y-3"><legend className="mb-2 text-sm font-semibold">Region on this frame</legend>
              <div className="grid grid-cols-3 gap-2">{(['Left','Top','Size'] as const).map((label,index) => <label key={label} className="text-sm">{label}<input className={input} type="number" min={index===2?1:0} max={index===0?facts.width:index===1?facts.height:Math.min(facts.width,facts.height)} value={index===2?box[2]-box[0]:box[index]}
                onChange={event => { const value=Number(event.target.value); setBox(old => index===0?[value,old[1],value+old[2]-old[0],old[3]]:index===1?[old[0],value,old[2],value+old[3]-old[1]]:[old[0],old[1],old[0]+value,old[1]+value]) }} /></label>)}</div>
              <div className="flex flex-wrap gap-2"><button className={button} onClick={() => applyRange(box,frame,frame)}>Review this region</button><button className={button} onClick={() => applyRange(null,frame,frame)}>Leave frame unchanged</button></div>
              <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={shots.includes(frame)} disabled={frame===0}
                onChange={event => setShots(old => event.target.checked ? [...old,frame].sort((a,b)=>a-b) : old.filter(value=>value!==frame))} />A new shot starts at this frame</label>
              <div className="grid grid-cols-2 gap-2"><label className="text-sm">Range start<input className={input} type="number" min={1} max={facts.frame_count} value={start} onChange={event=>setStart(Number(event.target.value))} /></label>
                <label className="text-sm">Range end<input className={input} type="number" min={1} max={facts.frame_count} value={end} onChange={event=>setEnd(Number(event.target.value))} /></label></div>
              <button className={button} onClick={() => applyRange(box,start-1,end-1)}>Review this region for the range</button>
              <p className="text-xs text-text-secondary">Only use a range when the same square covers the intended face throughout. Ranges cannot cross a marked shot boundary.</p>
            </fieldset>
          </section>
          <fieldset disabled={pending || accepted || uncertain} className="min-w-0 space-y-4"><legend className="mb-3 text-sm font-semibold">Repair settings</legend>
            <label className="block text-sm">Describe the face repair<textarea className={`${input} mt-1 min-h-28`} maxLength={16384} value={prompt} onChange={event=>setPrompt(event.target.value)} /></label>
            <label className="block text-sm">Repair strength · {Math.round(strength*100)}%<input className="mt-2 min-h-11 w-full" type="range" min={0.05} max={1} step={0.05} value={strength} onChange={event=>setStrength(Number(event.target.value))} /></label>
            <p className="text-xs text-text-secondary">Stronger repair gives the model more freedom inside reviewed regions.</p>
            <label className="block text-sm">Source audio guidance<select className={`${input} mt-1`} value={audio===null?'none':audio} onChange={event=>setAudio(event.target.value==='none'?null:Number(event.target.value))}>
              <option value="none">No audio guidance</option>{facts.audio_streams.map(stream=><option key={stream.ordinal} value={stream.ordinal}>{stream.label || `Track ${stream.ordinal+1}`}</option>)}</select></label>
            <p className="text-xs text-text-secondary">Guidance affects the repair only. Every original audio track is kept in the copy.</p>
            <details className="rounded-md border border-border p-3"><summary className="min-h-11 cursor-pointer text-sm">Technical details</summary><div className="mt-2 space-y-3">
              <p className="text-xs">{facts.width} × {facts.height} · 24 fps · {facts.frame_count} frames. Square crops use hard rectangular edges; automatic tracking and feathering are unavailable.</p>
              <label className="block text-sm">Crop size<input className={input} type="number" min={64} max={1536} step={32} value={canvas} onChange={event=>setCanvas(Number(event.target.value))} /></label>
              <label className="block text-sm">Sampling steps<input className={input} type="number" min={2} max={100} value={steps} onChange={event=>setSteps(Number(event.target.value))} /></label>
              <label className="block text-sm">Seed<input className={input} type="number" min={0} max={Number.MAX_SAFE_INTEGER} value={seed} onChange={event=>setSeed(Number(event.target.value))} /></label>
            </div></details>
          </fieldset>
        </div>}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border p-4">
        <p className="text-sm" role="status">{accepted ? 'Repair accepted in Queue.' : pending ? 'Submitting repair…' : uncertain ? 'An earlier submission needs review in Queue.' : facts ? `${reviewed} frames reviewed · ${facts.frame_count-reviewed} unchanged` : 'Original video stays intact.'}</p>
        {accepted || uncertain ? <div className="flex flex-wrap gap-2"><button className={button} disabled={queuePending} onClick={()=>void openQueue()}>Open Queue</button>
          {accepted && <button className={button} onClick={()=>{ setFaceRepairAdmission(admissionKey,null); submissionLatch.current=false; setBoxes(Array.from({length:facts?.frame_count??0},()=>null));setError('') }}>Start another repair</button>}</div>
          : <button className={`${button} bg-cta text-cta-foreground hover:ring-2 hover:ring-accent-blue/40`} disabled={!facts || disabled || reviewed===0 || !prompt.trim()} onClick={()=>void submit()}>Queue repaired copy</button>}
      </footer>
    </div>
  </div>, document.body)
}
