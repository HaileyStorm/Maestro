import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Check, Eye, Loader2, Play, RotateCcw, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'

type Primitive = 'cube' | 'sphere' | 'cylinder' | 'cone' | 'torus' | 'plane'
type LtxControlMode = 'VG' | 'TVG' | 'EVG' | 'PTVG' | 'TEVG'
type BlenderOperation = { sequence: number; workspace: string }

function parseVector(value: string): [number, number, number] {
  const parts = value.split(',').map(part => Number(part.trim()))
  if (parts.length !== 3 || parts.some(part => !Number.isFinite(part))) {
    throw new Error('Positions must contain three comma-separated numbers')
  }
  return [parts[0], parts[1], parts[2]]
}

function rgba(hex: string): [number, number, number, number] {
  const value = hex.replace('#', '')
  if (!/^[0-9a-fA-F]{6}$/.test(value)) throw new Error('Choose a valid color')
  return [0, 2, 4].map(index => parseInt(value.slice(index, index + 2), 16) / 255).concat(1) as [number, number, number, number]
}

export function BlenderSceneTool({
  compact = false,
  referenceName,
  referenceDescription,
  privateOutput = true,
}: {
  compact?: boolean
  /** Reference Studio passes these explicitly; Blender keeps its own contract. */
  referenceName?: string
  referenceDescription?: string
  privateOutput?: boolean
}) {
  const workspace = useStore(state => state.activeWorkspace)
  const refreshOutputs = useStore(state => state.refreshOutputs)
  const mountedRef = useRef(false)
  const workspaceRef = useRef(workspace)
  const statusRequest = useRef(0)
  const operationSequence = useRef(0)
  const activeOperation = useRef<BlenderOperation | null>(null)
  const [installed, setInstalled] = useState<boolean | null>(null)
  const [ready, setReady] = useState(false)
  const [readiness, setReadiness] = useState<api.BlenderStatus | null>(null)
  const [maxTotalFrames, setMaxTotalFrames] = useState(7200)
  const [name, setName] = useState('SceneBlock')
  const [primitive, setPrimitive] = useState<Primitive>('cube')
  const [start, setStart] = useState('0, 0, 0')
  const [end, setEnd] = useState('4, 0, 0')
  const [color, setColor] = useState('#4f8cff')
  const [duration, setDuration] = useState(10)
  const [fps, setFps] = useState(24)
  const [controlMode, setControlMode] = useState<LtxControlMode>('TVG')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [directorPrompt, setDirectorPrompt] = useState('')
  const [directorPlan, setDirectorPlan] = useState<api.BlenderDirectorPlan | null>(null)
  const [directorFinal, setDirectorFinal] = useState<api.BlenderDirectorFinal | null>(null)
  const [editPrompt, setEditPrompt] = useState('')
  const frameCount = useMemo(() => Math.max(1, Math.round(duration * fps)), [duration, fps])
  const endFrame = frameCount - 1
  const maxDuration = maxTotalFrames / fps
  const sampleFrames = useMemo(() => [0, Math.round(endFrame / 2), endFrame], [endFrame])
  workspaceRef.current = workspace
  const resolvedReferenceName = referenceName?.trim() || 'Blender scene guide'
  const blenderRecoveryMessage = (status: api.BlenderStatus | null): string => {
    if (!status?.mcp_attested) return 'Blender support needs setup. In Pinokio, open Maestro, run “Verify / Repair Blender MCP Support,” then restart Maestro.'
    if (!status.runtime_attested) return 'Blender needs setup. In Pinokio, open Maestro, run “Verify / Repair Blender Runtime,” then restart Maestro.'
    if (!status.bridge_ready) return 'Maestro cannot connect to Blender. Stop and start Maestro in Pinokio, then try again.'
    return 'Blender is not ready on this Maestro computer.'
  }
  const isOperationCurrent = (operation: BlenderOperation | null): boolean => Boolean(
    mountedRef.current
      && operation
      && activeOperation.current?.sequence === operation.sequence
      && activeOperation.current.workspace === operation.workspace
      && workspaceRef.current === operation.workspace,
  )

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      statusRequest.current += 1
      operationSequence.current += 1
      activeOperation.current = null
    }
  }, [])

  useEffect(() => {
    operationSequence.current += 1
    activeOperation.current = null
    setDirectorPlan(null)
    setDirectorFinal(null)
    setBusy('')
    setMessage('')
    setEditPrompt('')
  }, [workspace])

  useEffect(() => {
    const request = ++statusRequest.current
    if (!workspace) {
      setReadiness(null)
      setInstalled(false)
      setReady(false)
      return
    }
    let active = true
    void api.fetchBlenderStatus(workspace).then(status => {
      if (!active || request !== statusRequest.current || workspaceRef.current !== workspace) return
      setReadiness(status)
      setInstalled(status.installed)
      setReady(status.ready)
      setMaxTotalFrames(status.max_total_frames || 7200)
      setMessage(status.ready
        ? `Blender ready · ${status.blender_version || 'installed'}`
        : blenderRecoveryMessage(status))
    }).catch(error => {
      if (!active || request !== statusRequest.current || workspaceRef.current !== workspace) return
      setReadiness(null)
      setInstalled(false)
      setReady(false)
      setMessage(error instanceof Error
        ? 'Maestro could not check Blender. Try again, or open Pinokio to check the Blender setup.'
        : 'Blender is unavailable right now.')
    })
    return () => { active = false }
  }, [workspace])

  const run = async (label: string, task: () => Promise<unknown>) => {
    const operationWorkspace = workspace
    const operation = { sequence: ++operationSequence.current, workspace: operationWorkspace }
    activeOperation.current = operation
    if (!ready) {
      activeOperation.current = null
      setMessage(blenderRecoveryMessage(readiness))
      return
    }
    setBusy(label)
    setMessage('')
    try {
      await task()
      if (!isOperationCurrent(operation)) return
      setMessage(`${label} complete`)
    } catch {
      if (!isOperationCurrent(operation)) return
      setMessage(`${label} could not finish. Check the scene settings and try again.`)
    } finally {
      if (isOperationCurrent(operation)) {
        setBusy('')
        activeOperation.current = null
      }
    }
  }

  const create = () => run('Scene creation', () => api.createBlenderScene({
    workspace,
    clear_scene: true,
    objects: [{
      name,
      primitive,
      location: parseVector(start),
      scale: [1, 1, 1],
      material: { name: `${name}Material`, color: rgba(color) },
    }],
  }))

  const animate = () => run('Full-length animation', () => api.animateBlenderScene({
    workspace,
    frame_start: 0,
    frame_end: endFrame,
    objects: [{
      name,
      keyframes: [
        { frame: 0, location: parseVector(start), interpolation: 'BEZIER' },
        { frame: endFrame, location: parseVector(end), interpolation: 'BEZIER' },
      ],
    }],
  }))

  const inspect = () => run('Scene inspection', () => api.inspectBlenderScene({ workspace, objects: [name] }))

  const planWithDirector = async (): Promise<api.BlenderDirectorPlan | null> => {
    const operation = activeOperation.current
    if (!operation || !isOperationCurrent(operation)) return null
    const plan = await api.planBlenderScene({
      workspace,
      prompt: directorPrompt,
      duration_seconds: duration,
      frame_count: frameCount,
      fps,
      style: 'cinematic blocking reference',
    })
    if (!isOperationCurrent(operation)) return null
    setDirectorPlan(plan)
    return plan
  }

  const finalizePlan = async (plan: api.BlenderDirectorPlan, requestedEdits = ''): Promise<boolean> => {
    const operation = activeOperation.current
    if (!operation || !isOperationCurrent(operation)) return false
    const result = await api.finalizeBlenderScene({
      workspace,
      plan,
      edit_prompt: requestedEdits,
      reference_name: resolvedReferenceName,
      recommended_video_prompt_type: controlMode,
      private_output: privateOutput,
    })
    if (!isOperationCurrent(operation)) return false
    setDirectorPlan(result.final_plan)
    setDirectorFinal(result)
    if (!isOperationCurrent(operation)) return false
    await refreshOutputs()
    return isOperationCurrent(operation)
  }

  const runDirector = () => run('Director visual review', async () => {
    if (!isOperationCurrent(activeOperation.current)) return
    setDirectorFinal(null)
    const plan = await planWithDirector()
    if (!plan || !isOperationCurrent(activeOperation.current)) return
    await finalizePlan(plan)
  })

  const runManualDirector = () => run('Director visual review', async () => {
    if (!isOperationCurrent(activeOperation.current)) return
    setDirectorFinal(null)
    const plan: api.BlenderDirectorPlan = {
      workspace,
      director_prompt: `Animate ${name} from ${start} to ${end}`,
      scene: {
        clear_scene: true,
        objects: [{
          name,
          primitive,
          location: parseVector(start),
          scale: [1, 1, 1],
          material: { name: `${name}Material`, color: rgba(color) },
        }],
      },
      animation: {
        frame_start: 0,
        frame_end: endFrame,
        objects: [{
          name,
          keyframes: [
            { frame: 0, location: parseVector(start), interpolation: 'BEZIER' },
            { frame: endFrame, location: parseVector(end), interpolation: 'BEZIER' },
          ],
        }],
      },
      review_frames: sampleFrames,
      notes: 'Manual structured scene',
      duration_seconds: duration,
      frame_count: frameCount,
      fps,
      llm_model: 'Configured Director vision model',
      confirmation_required: false,
      review_strategy: 'Director reviews all sampled frames together before full render',
      semantic_mapping: {
        legend: [{
          object_name: name,
          primitive,
          color: rgba(color),
          subject: name,
          action: `moves from ${start} to ${end}`,
        }],
        conditioned_prompt: `${name} is represented by the ${color} ${primitive} and moves from ${start} to ${end}.`,
      },
    }
    setDirectorPlan(plan)
    if (!isOperationCurrent(activeOperation.current)) return
    await finalizePlan(plan)
  })

  const setFinalStatus = (status: 'kept' | 'rejected') => run(
    status === 'kept' ? 'Reference approval' : 'Reference rejection',
    async () => {
      if (!directorFinal) return
      const operation = activeOperation.current
      await api.setProjectAssetVariantStatus(
        workspace, directorFinal.asset_id, directorFinal.variant_id, status,
      )
      if (!isOperationCurrent(operation)) return
      setMessage(status === 'kept' ? 'Full video approved as a project reference' : 'Full video rejected')
    },
  )

  const requestEdits = () => run('Director edit review', async () => {
    if (!directorPlan || !editPrompt.trim()) return
    const operation = activeOperation.current
    await finalizePlan(directorPlan, editPrompt.trim())
    if (!isOperationCurrent(operation)) return
    setEditPrompt('')
  })

  const updateSemanticLegend = (index: number, field: 'subject' | 'action', value: string) => {
    setDirectorPlan(current => current ? {
      ...current,
      semantic_mapping: {
        ...current.semantic_mapping,
        legend: current.semantic_mapping.legend.map((item, itemIndex) => (
          itemIndex === index ? { ...item, [field]: value } : item
        )),
      },
    } : current)
  }

  const updateConditionedPrompt = (value: string) => {
    setDirectorPlan(current => current ? {
      ...current,
      semantic_mapping: { ...current.semantic_mapping, conditioned_prompt: value },
    } : current)
  }

  return (
    <div className={`space-y-3 ${compact ? 'mt-2 rounded-lg border border-border bg-bg-tertiary/40 p-2' : ''}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-text-muted"><Box size={12} /> Blender Motion Video</div>
        <span className={`text-[9px] ${ready ? 'text-accent-green' : 'text-amber-400'}`}>{installed == null ? 'checking' : ready ? 'ready' : installed ? 'not connected' : 'setup needed'}</span>
      </div>
      <p className="text-[9px] leading-relaxed text-text-muted">Build a scene, preview its motion and camera work, then keep the full video with your project. Blender runs on the Maestro computer, and previews stay in the selected project.</p>
      <p className="rounded border border-border/70 bg-bg-secondary/40 px-2 py-1 text-[8px] leading-relaxed text-text-muted">
        Saved as <span className="text-text-secondary">{resolvedReferenceName}</span> · {privateOutput ? 'preview starts blurred' : 'preview shown normally'}.
        {referenceDescription?.trim() ? ' Your Reference description stays with the original reference, while this Blender scene keeps its own description.' : ' Your Reference description stays with the original reference.'}
      </p>
      {readiness && (
        <div className="grid grid-cols-3 gap-1 text-center text-[8px] uppercase tracking-wide text-text-muted">
          <span className={`rounded border px-1 py-1 ${readiness.mcp_attested ? 'border-accent-green/30 text-accent-green' : 'border-amber-400/30 text-amber-400'}`}>Blender add-on {readiness.mcp_attested ? 'ready' : 'needs setup'}</span>
          <span className={`rounded border px-1 py-1 ${readiness.runtime_attested ? 'border-accent-green/30 text-accent-green' : 'border-amber-400/30 text-amber-400'}`}>Blender app {readiness.runtime_attested ? 'ready' : 'needs setup'}</span>
          <span className={`rounded border px-1 py-1 ${readiness.bridge_ready ? 'border-accent-green/30 text-accent-green' : 'border-amber-400/30 text-amber-400'}`}>Maestro link {readiness.bridge_ready ? 'connected' : 'offline'}</span>
        </div>
      )}
      <div className="rounded-lg border border-accent-blue/20 bg-accent-blue/5 p-2">
        <label className="text-[9px] uppercase tracking-wide text-text-muted">Director scene plan</label>
        <textarea value={directorPrompt} onChange={event => { setDirectorPrompt(event.target.value); setDirectorPlan(null) }} rows={compact ? 2 : 3} placeholder="Describe the scene, subjects, props, movement, and camera layout…" className="mt-1 w-full resize-y rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px]" />
        <button disabled={!ready || !!busy || !directorPrompt.trim()} onClick={runDirector} className="mt-1.5 w-full rounded bg-accent-blue px-2 py-1.5 text-[10px] text-white disabled:opacity-40">Plan, review, and render</button>
        <p className="mt-1.5 text-[9px] leading-relaxed text-text-muted">Director checks 2–8 moments from the animation together, can revise the scene up to three times, then renders the smooth full video for you.</p>
        {directorPlan && (
          <div className="mt-1.5 text-[9px] leading-relaxed text-text-muted">
            <p>Scene planned · {directorPlan.review_frames.length} moments selected for review</p>
            <details className="mt-1">
              <summary className="cursor-pointer text-text-secondary">Production details</summary>
              <p className="mt-1">Model: {directorPlan.llm_model}</p>
              {(directorPlan.notes || directorPlan.review_strategy) && (
                <p><span className="font-medium">Director notes:</span> {directorPlan.notes || directorPlan.review_strategy}</p>
              )}
            </details>
          </div>
        )}
        {directorPlan?.semantic_mapping && (
          <div className="mt-2 space-y-2 rounded border border-border bg-bg-secondary/50 p-2">
            <p className="text-[9px] font-medium text-text-secondary">Object-to-scene guide</p>
            {directorPlan.semantic_mapping.legend.map((entry, index) => (
              <div key={entry.object_name} className="grid grid-cols-[auto_1fr] gap-1.5 rounded border border-border/70 p-1.5">
                <div className="flex min-w-20 items-center gap-1 text-[8px] text-text-muted">
                  <span className="h-3 w-3 rounded-full border border-white/20" style={{ backgroundColor: `rgb(${entry.color.slice(0, 3).map(value => Math.round(value * 255)).join(',')})` }} />
                  <span>{entry.object_name}<br />{entry.primitive}</span>
                </div>
                <div className="space-y-1">
                  <input value={entry.subject} onChange={event => updateSemanticLegend(index, 'subject', event.target.value)} placeholder="Subject represented by this shape" className="w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[9px]" />
                  <input value={entry.action} onChange={event => updateSemanticLegend(index, 'action', event.target.value)} placeholder="Action controlled by this shape" className="w-full rounded border border-border bg-bg-tertiary px-1.5 py-1 text-[9px]" />
                </div>
              </div>
            ))}
            <textarea value={directorPlan.semantic_mapping.conditioned_prompt} onChange={event => updateConditionedPrompt(event.target.value)} rows={3} placeholder="Describe what each shape represents and how it should move…" className="w-full resize-y rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[9px]" />
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input value={name} onChange={event => setName(event.target.value)} placeholder="Object name" className="rounded border border-border bg-bg-tertiary px-2 py-1.5 text-xs" />
        <select value={primitive} onChange={event => setPrimitive(event.target.value as Primitive)} className="rounded border border-border bg-bg-tertiary px-2 py-1.5 text-xs">
          {(['cube', 'sphere', 'cylinder', 'cone', 'torus', 'plane'] as Primitive[]).map(value => <option key={value}>{value}</option>)}
        </select>
        <input value={start} onChange={event => setStart(event.target.value)} placeholder="Start x,y,z" className="rounded border border-border bg-bg-tertiary px-2 py-1.5 text-xs" />
        <input value={end} onChange={event => setEnd(event.target.value)} placeholder="End x,y,z" className="rounded border border-border bg-bg-tertiary px-2 py-1.5 text-xs" />
        <label className="flex items-center justify-between rounded border border-border bg-bg-tertiary px-2 py-1 text-[10px] text-text-muted">Color <input type="color" value={color} onChange={event => setColor(event.target.value)} /></label>
        <div className="grid grid-cols-2 gap-1">
          <label className="text-[9px] text-text-muted">Seconds<input type="number" min={1} max={maxDuration} step={1 / fps} value={duration} onChange={event => setDuration(Math.min(maxDuration, Math.max(1, Number(event.target.value) || 1)))} className="mt-0.5 w-full rounded border border-border bg-bg-tertiary px-1 py-1 text-xs" /></label>
          <label className="text-[9px] text-text-muted">FPS<input type="number" min={1} max={240} value={fps} onChange={event => {
            const nextFps = Math.min(240, Math.max(1, Number(event.target.value) || 1))
            setFps(nextFps)
            setDuration(value => Math.min(value, maxTotalFrames / nextFps))
          }} className="mt-0.5 w-full rounded border border-border bg-bg-tertiary px-1 py-1 text-xs" /></label>
        </div>
        <div className="col-span-2 text-[9px] text-text-muted">{frameCount} frames (0–{endFrame}) · Maestro limit: {maxTotalFrames} frames / {maxDuration.toFixed(1)}s at {fps} fps</div>
        <label className="col-span-2 text-[9px] text-text-muted">How the Blender animation guides LTX-2.3
          <select value={controlMode} onChange={event => setControlMode(event.target.value as LtxControlMode)} className="mt-0.5 w-full rounded border border-border bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary">
            <option value="TVG">Depth over time (recommended for Blender scenes)</option>
            <option value="VG">Rendered scene and motion</option>
            <option value="EVG">Object edges</option>
            <option value="PTVG">Motion and depth over time</option>
            <option value="TEVG">Depth and edges over time</option>
          </select>
          <span className="mt-1 block leading-relaxed">Director reviews sample moments, while the full animation guides the motion. The final polish may rely less on this guide.</span>
        </label>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <button disabled={!ready || !!busy} onClick={create} className="rounded border border-border px-2 py-1.5 text-[10px] text-text-secondary disabled:opacity-40"><Box size={10} className="mr-1 inline" />Create / reset</button>
        <button disabled={!ready || !!busy} onClick={animate} className="rounded border border-border px-2 py-1.5 text-[10px] text-text-secondary disabled:opacity-40"><Play size={10} className="mr-1 inline" />Animate {endFrame}f</button>
        <button disabled={!ready || !!busy} onClick={inspect} className="rounded border border-border px-2 py-1.5 text-[10px] text-text-secondary disabled:opacity-40"><Eye size={10} className="mr-1 inline" />Inspect</button>
        <button disabled={!ready || !!busy} onClick={runManualDirector} className="rounded border border-accent-blue/40 px-2 py-1.5 text-[10px] text-accent-blue disabled:opacity-40"><Play size={10} className="mr-1 inline" />Review and render</button>
      </div>
      {busy && <p className="flex items-center gap-1 text-[10px] text-accent-blue"><Loader2 size={10} className="animate-spin" />{busy}…</p>}
      {message && <p className="text-[9px] leading-relaxed text-text-muted">{message}</p>}
      {directorFinal && (
        <div className="space-y-2 rounded-lg border border-accent-green/30 bg-accent-green/5 p-2">
          <p className="text-[10px] text-accent-green">Director finished {directorFinal.director_reviews.length} review round{directorFinal.director_reviews.length === 1 ? '' : 's'}</p>
          <video src={directorFinal.video.url} controls className="aspect-video w-full rounded bg-media-canvas object-contain" />
          <div className="flex gap-1.5">
            <button disabled={!!busy} onClick={() => void setFinalStatus('kept')} className="flex flex-1 items-center justify-center gap-1 rounded bg-accent-green/20 px-2 py-1.5 text-[10px] text-accent-green"><Check size={10} />Keep motion video</button>
            <button disabled={!!busy} onClick={() => void setFinalStatus('rejected')} className="flex items-center justify-center gap-1 rounded border border-border px-2 py-1.5 text-[10px] text-text-muted"><X size={10} />Reject</button>
          </div>
          <textarea value={editPrompt} onChange={event => setEditPrompt(event.target.value)} rows={2} placeholder="Describe what Director should change, then it will review and render a new video…" className="w-full resize-y rounded border border-border bg-bg-tertiary px-2 py-1.5 text-[10px]" />
          <button disabled={!ready || !!busy || !editPrompt.trim()} onClick={requestEdits} className="flex w-full items-center justify-center gap-1 rounded border border-accent-blue/40 px-2 py-1.5 text-[10px] text-accent-blue disabled:opacity-40"><RotateCcw size={10} />Make changes and review again</button>
        </div>
      )}
    </div>
  )
}
