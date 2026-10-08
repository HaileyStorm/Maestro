import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Loader2, RefreshCw, Square } from 'lucide-react'
import * as api from '../../api/client'
import { currentAccountIdentityEpoch, useStore } from '../../stores/useStore'
import { yue2SubmissionScope } from '../../lib/yue2SubmissionIntent'
import { matchingYue2TrainingJob, yue2TrainingAttempts } from './yue2TrainingAttempts'

type Props = { workspace: string; tracks: api.Yue2Track[]; gpuBlocked: boolean; onJobs?: (jobs: api.Yue2TrainingJob[]) => void }
const inputClass = 'w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-[10px] text-text-primary focus:border-accent-blue focus:outline-none'
const jobStateLabel: Record<api.Yue2TrainingJob['state'], string> = {
  queued: 'Queued',
  preparing: 'Preparing',
  'waiting-for-resource': 'Waiting for GPU',
  running: 'Training',
  succeeded: 'Checkpoints ready',
  failed: 'Stopped with an error',
  cancelled: 'Cancelled',
}

export function Yue2Training({ workspace, tracks, gpuBlocked, onJobs }: Props) {
  const accountEpoch = useStore(() => currentAccountIdentityEpoch())
  const accountScope = useStore(yue2SubmissionScope)
  const scope = useMemo(() => ({ workspace, accountEpoch, accountScope }), [workspace, accountEpoch, accountScope])
  const scopeRef = useRef(scope)
  const [jobs, setJobs] = useState<api.Yue2TrainingJob[]>([])
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [captions, setCaptions] = useState<Record<string, string>>({})
  const [lyrics, setLyrics] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'artist' | 'style'>('artist')
  const [trigger, setTrigger] = useState('')
  const [steps, setSteps] = useState(400)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const sequence = useRef(0)
  const operationSequence = useRef(0)
  const mounted = useRef(false)
  const submitting = useRef(false)
  const submissionOperation = useRef<{ requestId: string; sequence: number } | null>(null)
  const onJobsRef = useRef(onJobs)
  onJobsRef.current = onJobs
  const workspaceRef = useRef(workspace)
  workspaceRef.current = workspace
  const [, setAttemptVersion] = useState(0)
  const pending = yue2TrainingAttempts.current(accountEpoch, workspace)
  const recoveryUnavailable = yue2TrainingAttempts.unavailable(accountEpoch)

  const ready = useMemo(() => tracks.filter(track => track.project === workspace && track.status === 'succeeded'), [tracks, workspace])
  const selectedTracks = ready.filter(track => selected[track.id])
  const valid = selectedTracks.length > 0 && selectedTracks.length <= 32 && name.trim().length > 0
    && /^sv_[a-z0-9_]{3,40}$/.test(trigger)
    && selectedTracks.every(track => !!captions[track.id]?.trim() && !/[\r\n]/.test(captions[track.id]))

  const isCurrent = useCallback((operation: number, requestWorkspace: string, requestEpoch: number) =>
    scope.accountScope !== null && mounted.current && scopeRef.current === scope && operationSequence.current === operation && workspaceRef.current === requestWorkspace
      && currentAccountIdentityEpoch() === requestEpoch && yue2SubmissionScope() === scope.accountScope, [scope])

  useLayoutEffect(() => {
    mounted.current = true
    scopeRef.current = scope
    operationSequence.current += 1
    submitting.current = false
    submissionOperation.current = null
    yue2TrainingAttempts.prune()
    return () => { mounted.current = false; operationSequence.current += 1; sequence.current += 1 }
  }, [scope])

  const refresh = useCallback(async () => {
    if (!isCurrent(operationSequence.current, workspace, accountEpoch)) return
    const current = ++sequence.current
    const operation = operationSequence.current
    try {
      const result = await api.fetchYue2Training(workspace)
      if (!isCurrent(operation, workspace, accountEpoch) || current !== sequence.current) return
      const projectJobs = result.jobs.filter(job => job.project === workspace)
      const attempt = yue2TrainingAttempts.current(accountEpoch, workspace)
      if (attempt && projectJobs.some(job => matchingYue2TrainingJob(attempt, job))) {
        yue2TrainingAttempts.clear(accountEpoch, workspace, attempt.requestId)
        setAttemptVersion(value => value + 1)
        setError(null)
        setMessage('Training submission confirmed. Its current status is shown below.')
        const active = submissionOperation.current
        if (active?.sequence === operation && active.requestId === attempt.requestId) {
          operationSequence.current += 1
          submissionOperation.current = null
          submitting.current = false
          setBusy(false)
        }
      }
      setJobs(projectJobs)
      onJobsRef.current?.(projectJobs)
    } catch (cause) {
      if (isCurrent(operation, workspace, accountEpoch) && current === sequence.current) {
        setError(cause instanceof Error ? cause.message : 'Training jobs are unavailable.')
      }
    }
  }, [workspace, accountEpoch, isCurrent])

  useLayoutEffect(() => {
    setJobs([]); setSelected({}); setCaptions({}); setLyrics({})
    setName(''); setKind('artist'); setTrigger(''); setSteps(400)
    setBusy(false); setError(null); setMessage(null)
  }, [workspace, accountEpoch, accountScope])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    if (!jobs.some(job => ['queued', 'preparing', 'waiting-for-resource', 'running'].includes(job.state))) return
    const timer = window.setInterval(() => { void refresh() }, 5000)
    return () => window.clearInterval(timer)
  }, [jobs, refresh])

  const submit = async () => {
    if (!isCurrent(operationSequence.current, workspace, accountEpoch)
      || (!valid && !pending) || busy || submitting.current || gpuBlocked || recoveryUnavailable) return
    const requestWorkspace = workspace
    const requestEpoch = accountEpoch
    let attempt: api.Yue2TrainingRequest | undefined
    try {
      attempt = yue2TrainingAttempts.next(requestEpoch, requestWorkspace, () => ({
        workspace: requestWorkspace, requestId: `train-${crypto.randomUUID()}`,
        name: name.trim(), kind, trigger, steps,
        tracks: selectedTracks.map(track => ({ takeId: track.id, caption: captions[track.id].trim(), lyrics: lyrics[track.id] || '' })),
      }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Training recovery is unavailable in this tab.')
      return
    }
    if (!attempt) return
    const operation = ++operationSequence.current
    submitting.current = true
    submissionOperation.current = { requestId: attempt.requestId, sequence: operation }
    const current = () => isCurrent(operation, requestWorkspace, requestEpoch)
    setBusy(true); setError(null); setMessage(null)
    setAttemptVersion(value => value + 1)
    try {
      const response = await api.submitYue2Training(attempt)
      if (!current()) return
      if (!response || typeof response.reused !== 'boolean' || !matchingYue2TrainingJob(attempt, response.job)) {
        throw new Error('Training did not return a valid submission confirmation.')
      }
      yue2TrainingAttempts.clear(requestEpoch, requestWorkspace, attempt.requestId)
      setAttemptVersion(value => value + 1)
      setMessage('Training queued. Finished checkpoints will stay private to this project for audition.')
      setSelected({})
      await refresh()
    } catch (cause) {
      if (!current()) return
      const rejected = !yue2TrainingAttempts.ambiguous(requestEpoch, requestWorkspace, attempt.requestId)
        && cause instanceof api.Yue2RequestError && [400, 422].includes(cause.status)
      if (rejected) {
        yue2TrainingAttempts.clear(requestEpoch, requestWorkspace, attempt.requestId)
        setAttemptVersion(value => value + 1)
        setError(cause.message)
        return
      }
      yue2TrainingAttempts.markAmbiguous(requestEpoch, requestWorkspace, attempt.requestId)
      let found = false
      const reconciliation = ++sequence.current
      try {
        const result = await api.fetchYue2Training(requestWorkspace)
        if (!current() || reconciliation !== sequence.current) return
        const projectJobs = result.jobs.filter(job => job.project === requestWorkspace)
        found = projectJobs.some(job => matchingYue2TrainingJob(attempt, job))
        setJobs(projectJobs)
        onJobsRef.current?.(projectJobs)
      } catch { /* A failed status check cannot settle an uncertain submission. */ }
      if (!current()) return
      if (found) {
        yue2TrainingAttempts.clear(requestEpoch, requestWorkspace, attempt.requestId)
        setAttemptVersion(value => value + 1)
        setMessage('Training was queued. The first response was lost, but the job is now visible below.')
        setSelected({})
      } else {
        setError('Could not confirm whether training was queued. Retry sends the same request, so it will not create a duplicate job.')
      }
    } finally {
      if (current()) {
        submitting.current = false
        submissionOperation.current = null
        setBusy(false)
      }
    }
  }

  const cancel = async (job: api.Yue2TrainingJob) => {
    if (!isCurrent(operationSequence.current, workspace, accountEpoch)) return
    const operation = operationSequence.current
    try {
      await api.cancelYue2Training(job.id, workspace)
      if (!isCurrent(operation, workspace, accountEpoch)) return
      setMessage(null); setError(null)
      await refresh()
    } catch (cause) {
      if (isCurrent(operation, workspace, accountEpoch)) {
        setError(cause instanceof Error ? cause.message : 'Cancellation failed.')
      }
    }
  }

  return (
    <details className="rounded-lg border border-border p-2 text-[10px]">
      <summary className="cursor-pointer font-semibold text-text-primary">Train a YuE2 artist or style LoRA</summary>
      <div className="mt-2 space-y-2">
        <p className="text-text-muted">Choose finished takes from this project and write one style caption for each. Finished checkpoints can be auditioned in this project without installing them globally. Training waits for an available GPU and may take a while.</p>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-text-muted">Name<input value={name} onChange={event => setName(event.target.value)} maxLength={120} className={`${inputClass} mt-1`} /></label>
          <label className="text-text-muted">Type<select value={kind} onChange={event => setKind(event.target.value as 'artist' | 'style')} className={`${inputClass} mt-1`}><option value="artist">Artist</option><option value="style">Style</option></select></label>
          <label className="text-text-muted">Trigger word<input value={trigger} onChange={event => setTrigger(event.target.value)} placeholder="sv_myvoice" className={`${inputClass} mt-1`} /></label>
          <label className="text-text-muted">Training steps<select value={steps} onChange={event => setSteps(Number(event.target.value))} className={`${inputClass} mt-1`}>{[200, 400, 600, 800, 1000, 1200, 1400, 1600].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        </div>
        <p className="text-text-muted">Use a trigger beginning with <code>sv_</code>, followed by 3–40 lowercase letters, digits, or underscores.</p>
        {recoveryUnavailable && <p role="alert" className="text-red-400">Training recovery is unavailable in this tab. Check training jobs before trying again.</p>}
        {pending && !busy && <p role="status" className="text-amber-300">A previous request is unconfirmed. Retry will send its original inputs and request ID.</p>}
        {gpuBlocked && <p role="alert" className="text-red-400">GPU work is paused because a YuE2 worker could not be confirmed stopped. Check the local Sound/Vision service before starting another job.</p>}
        {ready.length === 0 && <p className="text-text-muted">Finish a YuE2 take in this project to use it for training.</p>}
        <div className="max-h-72 space-y-1 overflow-y-auto">
          {ready.map(track => <div key={track.id} className="rounded border border-border p-1.5">
            <label className="flex gap-1.5 text-text-secondary"><input type="checkbox" checked={!!selected[track.id]} onChange={event => setSelected(current => ({ ...current, [track.id]: event.target.checked }))} className="accent-accent-blue" />{track.title}</label>
            {selected[track.id] && <div className="mt-1 space-y-1">
              <label className="block text-text-muted">Style caption<input value={captions[track.id] || ''} onChange={event => setCaptions(current => ({ ...current, [track.id]: event.target.value }))} maxLength={2000} className={`${inputClass} mt-0.5`} /></label>
              <label className="block text-text-muted">Lyrics, if present<textarea value={lyrics[track.id] || ''} onChange={event => setLyrics(current => ({ ...current, [track.id]: event.target.value }))} maxLength={10000} rows={3} className={`${inputClass} mt-0.5 resize-y`} /></label>
            </div>}
          </div>)}
        </div>
        <button type="button" onClick={() => void submit()} disabled={(!valid && !pending) || busy || gpuBlocked || recoveryUnavailable} className="mobile-control-target flex w-full items-center justify-center rounded bg-cta px-2 text-[10px] font-semibold text-cta-foreground hover:ring-2 hover:ring-accent-blue/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-40">{busy ? <Loader2 size={12} className="animate-spin" /> : pending ? 'Retry original training request' : `Queue training with ${selectedTracks.length} ${selectedTracks.length === 1 ? 'take' : 'takes'}`}</button>
        <div className="flex items-center justify-between"><strong className="text-text-primary">Training jobs</strong><button type="button" aria-label="Refresh YuE2 training jobs" onClick={() => void refresh()} className="text-text-muted hover:text-text-primary"><RefreshCw size={12} /></button></div>
        {jobs.length === 0 && <p className="text-text-muted">No training jobs in this project yet.</p>}
        {jobs.map(job => <div key={job.id} className="rounded border border-border p-1.5 text-text-secondary">
          <div className="flex justify-between gap-2"><span>{job.name} · {job.kind}</span><span>{jobStateLabel[job.state]}</span></div>
          <p className="text-text-muted">Trigger {job.trigger} · {job.sourceTakeIds.length} {job.sourceTakeIds.length === 1 ? 'take' : 'takes'}</p>
          {job.error && <p className="text-red-400">{job.error}</p>}
          {job.state === 'succeeded' && <p className="text-text-muted">{job.checkpoints?.length || 0} checkpoint{job.checkpoints?.length === 1 ? '' : 's'} saved. Select one under YuE2 LoRAs above to make an audition take. Nothing is installed globally.</p>}
          {['queued', 'preparing', 'waiting-for-resource', 'running'].includes(job.state) && <button type="button" onClick={() => void cancel(job)} className="flex items-center gap-1 text-red-300"><Square size={10} /> Cancel</button>}
        </div>)}
        {message && <p role="status" className="text-emerald-300">{message}</p>}
        {error && <p role="alert" className="text-red-400">{error}</p>}
      </div>
    </details>
  )
}
