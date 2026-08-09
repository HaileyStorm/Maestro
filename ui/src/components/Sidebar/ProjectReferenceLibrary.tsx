import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown, EyeOff, FileUp, ImagePlus, Library, Loader2, MapPin, Package, Pencil, RotateCcw, Trash2, UserRound, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import {
  fetchProjectAssets,
  addProjectAssetVariant,
  deleteProjectAssetVariant,
  generateProjectAssetReferences,
  getProjectAssetComponentOutputs,
  getProjectAssetMediaUrl,
  getProjectReferenceRetrySettings,
  isProjectAssetOperationCurrent,
  lockProjectAssetVariantOperation,
  projectAssetVariantOperationKey,
  selectProjectAssetApplyOutput,
  setProjectAssetVariantStatus,
  uploadImage,
  type ProjectAsset,
  type ProjectAssetOutput,
  type ProjectAssetVariant,
  type ProjectReferenceAssetType,
  type ProjectReferenceSheetMode,
} from '../../api/client'
import { BlenderSceneTool } from './BlenderSceneTool'
import { hidePrivatePreview, privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview } from '../../lib/privatePreview'
import { POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'

const ASSET_TYPES = [
  { value: 'character', label: 'Character', icon: UserRound },
  { value: 'setting', label: 'Setting', icon: MapPin },
  { value: 'item', label: 'Item', icon: Package },
  { value: 'style', label: 'Style', icon: ImagePlus },
] as const

const SHEET_MODES: Array<{
  value: ProjectReferenceSheetMode
  label: string
  description: string
}> = [
  {
    value: 'production',
    label: 'Production',
    description: 'Generates independent panels, reviews them locally, then assembles a deterministic collage.',
  },
  {
    value: 'hybrid',
    label: 'Hybrid',
    description: 'Generates one identity anchor, derives targeted edits, then assembles a deterministic collage.',
  },
  {
    value: 'draft',
    label: 'Draft',
    description: 'Generates the complete sheet in one shot for the fastest exploratory result.',
  },
]

function friendlyRole(role: string): string {
  return role.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function referenceSheetStatus(variant: ProjectAssetVariant): {
  label: string
  className: string
  repair: string
} | null {
  if (variant.variant_type !== 'reference_sheet') return null
  const metadata = variant.metadata.reference_sheet
  const repaired = Array.isArray(metadata?.roles?.repaired)
    ? metadata.roles.repaired.filter(role => typeof role === 'string')
    : []
  const repair = repaired.length > 0
    ? `One bounded repair regenerated ${repaired.map(friendlyRole).join(', ')}.`
    : 'No repair was needed.'
  if (metadata?.review_status === 'pass') {
    return { label: repaired.length > 0 ? 'Local review passed after repair' : 'Local review passed', className: 'text-accent-green', repair }
  }
  if (metadata?.review_status === 'fail') {
    return { label: 'Local review still needs attention', className: 'text-amber-300', repair }
  }
  if (metadata?.review_status === 'review_unavailable') {
    return { label: 'Local review unavailable — candidate preserved for your review', className: 'text-text-muted', repair: '' }
  }
  return { label: 'Local review was not requested', className: 'text-text-muted', repair: '' }
}

function ProjectAssetPreview({ project, assetId, output, label }: {
  project: string
  assetId: string
  output: ProjectAssetOutput
  label: string
}) {
  const isPrivate = output.metadata?.private === true
  const identity = privatePreviewIdentity(project, `asset:${assetId}:${output.id}`, output.relative_path)
  const [revealedIdentity, setRevealedIdentity] = useState(() =>
    isPrivate && privatePreviewWasRevealed(identity) ? identity : '',
  )
  const revealed = isPrivate && revealedIdentity === identity

  const reveal = () => {
    revealPrivatePreview(identity)
    setRevealedIdentity(identity)
  }
  const hide = () => {
    hidePrivatePreview(identity)
    setRevealedIdentity('')
  }

  return (
    <div className="relative aspect-video w-full overflow-hidden bg-media-canvas">
      <div className={`h-full w-full transition-[filter] ${
        isPrivate && !revealed ? 'blur-xl' : ''
      }`} inert={isPrivate && !revealed}>
        {output.media_type?.startsWith('video/')
          ? <video src={getProjectAssetMediaUrl(project, output.relative_path)} controls className="h-full w-full object-contain" />
          : <img src={getProjectAssetMediaUrl(project, output.relative_path)} alt={label} className="h-full w-full object-contain" />}
      </div>
      {isPrivate && !revealed && (
        <button
          type="button"
          onClick={reveal}
          className="absolute inset-0 z-10 flex items-center justify-center bg-black/25 text-white"
          title="Click, tap, or press Enter to reveal for this browser session"
        >
          <EyeOff size={18} />
          <span className="sr-only">Reveal private reference preview</span>
        </button>
      )}
      {isPrivate && revealed && (
        <button
          type="button"
          onClick={hide}
          className="absolute right-1 top-1 z-10 rounded-full bg-black/65 p-1 text-white/80 hover:text-white"
          title="Blur this private reference preview again"
        >
          <EyeOff size={11} />
        </button>
      )}
    </div>
  )
}

export function ProjectReferenceLibrary() {
  const project = useStore(s => s.activeWorkspace)
  const jobs = useStore(s => s.jobs)
  const browsingUploads = useStore(s => s.browsingUploads)
  const privateOutput = useStore(s => s.privateOutput)
  const explicitOutput = useStore(s => s.explicitOutput)
  const selectedModelPerMode = useStore(s => s.selectedModelPerMode)
  const generationMode = useStore(s => s.generationMode)
  const setGenerationMode = useStore(s => s.setGenerationMode)
  const selectModel = useStore(s => s.selectModel)
  const setParam = useStore(s => s.setParam)
  const sidebarMode = useStore(s => s.sidebarMode)
  const setSidebarMode = useStore(s => s.setSidebarMode)
  const setGuideVideoFps = useStore(s => s.setGuideVideoFps)
  const setGuideVideoFrameCount = useStore(s => s.setGuideVideoFrameCount)
  const addImageRef = useStore(s => s.addImageRef)
  const addCharacterRef = useStore(s => s.directorAddCharacterRef)
  const addLocationRef = useStore(s => s.directorAddLocationRef)
  const reconnectJobs = useStore(s => s.reconnectJobs)
  const [open, setOpen] = useState(false)
  const [assets, setAssets] = useState<ProjectAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [assetType, setAssetType] = useState<ProjectReferenceAssetType>('character')
  const [sheetMode, setSheetMode] = useState<ProjectReferenceSheetMode>('production')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [style, setStyle] = useState('cinematic production reference sheet')
  const [poses, setPoses] = useState('front, profile, three-quarter')
  const [outfits, setOutfits] = useState('primary outfit')
  const [candidateCount, setCandidateCount] = useState(2)
  const [columns, setColumns] = useState(2)
  const [paletteSwatches, setPaletteSwatches] = useState(8)
  const [review, setReview] = useState(true)
  const [importing, setImporting] = useState<{ assetId: string; message: string } | null>(null)
  const [importErrors, setImportErrors] = useState<Record<string, string>>({})
  const [editVariantId, setEditVariantId] = useState<string | null>(null)
  const [editInstruction, setEditInstruction] = useState('')
  const [queuedMessage, setQueuedMessage] = useState('')
  const [pendingSheetActions, setPendingSheetActions] = useState<Record<string, {
    project: string
    assetId: string
    variantId: string
    jobId: string | null
  }>>({})
  const requestSequence = useRef(0)
  const projectEpoch = useRef(0)
  const previousProject = useRef(project)
  const currentProject = useRef(project)
  const pendingSheetActionLocks = useRef(new Set<string>())
  const openButtonRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  currentProject.current = project

  useEffect(() => {
    if (previousProject.current === project) return
    previousProject.current = project
    projectEpoch.current += 1
    requestSequence.current += 1
    setAssets([])
    setSubmitting(false)
    setImporting(null)
    setImportErrors({})
    pendingSheetActionLocks.current.clear()
    setPendingSheetActions({})
    setEditVariantId(null)
    setEditInstruction('')
    setQueuedMessage('')
    setError('')
  }, [project])

  useEffect(() => {
    if (!open) return
    const opener = openButtonRef.current
    window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    return () => { opener?.focus() }
  }, [open])

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
      return
    }
    if (event.key !== 'Tab') return
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter(element => (
      element.getAttribute('aria-hidden') !== 'true' && element.getClientRects().length > 0
    ))
    if (focusable.length === 0) {
      event.preventDefault()
      dialogRef.current?.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const refresh = useCallback(async (signal: AbortSignal) => {
    if (!open || browsingUploads) return
    const sequence = ++requestSequence.current
    try {
      const next = await fetchProjectAssets(project)
      if (signal.aborted || sequence !== requestSequence.current) return
      setAssets(next)
      const completedJobIds = new Set(next.flatMap(asset => (
        asset.variants.map(variant => variant.metadata.job?.id).filter((id): id is string => Boolean(id))
      )))
      setPendingSheetActions(current => {
        const entries = Object.entries(current).filter(([key, action]) => {
          const keep = action.project === project && (!action.jobId || !completedJobIds.has(action.jobId))
          if (!keep) pendingSheetActionLocks.current.delete(key)
          return keep
        })
        return entries.length === Object.keys(current).length ? current : Object.fromEntries(entries)
      })
      setError('')
    } catch (reason) {
      if (signal.aborted || sequence !== requestSequence.current) return
      setError(reason instanceof Error ? reason.message : 'Failed to load references')
    } finally {
      if (!signal.aborted && sequence === requestSequence.current) setLoading(false)
    }
  }, [open, browsingUploads, project])

  const refreshNow = useVisibilityPolling(
    refresh,
    POLL_INTERVAL_MS.referencesVisible,
    { enabled: open && !browsingUploads, immediate: false },
  )

  const requestRefresh = useCallback(() => {
    requestSequence.current += 1
    refreshNow()
  }, [refreshNow])

  useEffect(() => {
    requestSequence.current += 1
    if (!open || browsingUploads) return
    setLoading(true)
    if (!document.hidden) refreshNow()
  }, [open, browsingUploads, project, refreshNow])

  useEffect(() => () => { requestSequence.current += 1 }, [])

  useEffect(() => {
    const failed = Object.entries(pendingSheetActions).find(([, action]) => {
      if (action.project !== project || !action.jobId) return false
      const job = jobs.find(candidate => candidate.id === action.jobId)
      return job?.status === 'failed' || job?.status === 'cancelled'
    })
    if (!failed) return
    const [key, action] = failed
    const job = jobs.find(candidate => candidate.id === action.jobId)
    pendingSheetActionLocks.current.delete(key)
    setPendingSheetActions(current => {
      if (!current[key]) return current
      const next = { ...current }
      delete next[key]
      return next
    })
    setError(job?.error || (job?.status === 'cancelled'
      ? 'Reference-sheet job was cancelled'
      : 'Reference-sheet generation failed'))
  }, [jobs, pendingSheetActions, project])

  const generate = async () => {
    if (!name.trim()) return
    const epoch = projectEpoch.current
    const submittedProject = project
    setSubmitting(true)
    setError('')
    setQueuedMessage('')
    try {
      const response = await generateProjectAssetReferences(project, {
        name: name.trim(),
        asset_type: assetType,
        description: description.trim(),
        mode: sheetMode,
        style,
        poses: assetType === 'character' ? poses : undefined,
        outfits: assetType === 'character' ? outfits : undefined,
        candidate_count: candidateCount,
        columns,
        palette_swatches: paletteSwatches,
        review,
        model_type: selectedModelPerMode.image || 'flux2_klein_9b',
        private_output: privateOutput,
        explicit_output: explicitOutput,
      })
      await reconnectJobs()
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setName('')
      setDescription('')
      setQueuedMessage(`Queued ${candidateCount} ${candidateCount === 1 ? 'reference sheet' : 'reference sheets'} (${response.job_id}). They will appear here when complete.`)
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setError(reason instanceof Error ? reason.message : 'Reference generation failed')
    } finally {
      if (isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) setSubmitting(false)
    }
  }

  const generateFromVariant = async (
    asset: ProjectAsset,
    variant: ProjectAssetVariant,
    instruction?: string,
  ) => {
    const key = lockProjectAssetVariantOperation(
      pendingSheetActionLocks.current, project, asset.id, variant.id,
    )
    if (!key) return
    const epoch = projectEpoch.current
    const submittedProject = project
    const sourceSettings = getProjectReferenceRetrySettings(variant, {
      mode: sheetMode,
      model_type: selectedModelPerMode.image || 'flux2_klein_9b',
      private_output: privateOutput,
      explicit_output: explicitOutput,
      review,
    })
    setPendingSheetActions(current => ({
      ...current,
      [key]: { project, assetId: asset.id, variantId: variant.id, jobId: null },
    }))
    setError('')
    setQueuedMessage('')
    try {
      const response = await generateProjectAssetReferences(project, {
        asset_id: asset.id,
        parent_variant_id: variant.id,
        edit_instruction: instruction?.trim() || undefined,
        mode: sourceSettings.mode,
        candidate_count: 1,
        columns,
        palette_swatches: paletteSwatches,
        review: sourceSettings.review,
        model_type: sourceSettings.model_type,
        private_output: sourceSettings.private_output,
        explicit_output: sourceSettings.explicit_output,
      })
      await reconnectJobs()
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setPendingSheetActions(current => ({
        ...current,
        [key]: { project, assetId: asset.id, variantId: variant.id, jobId: response.job_id },
      }))
      setQueuedMessage(`${instruction?.trim() ? 'Edit' : 'Retry'} queued (${response.job_id}). Available source mode, model, and privacy settings were preserved; current layout and review controls were used. The original and any kept source stay unchanged.`)
      setEditVariantId(null)
      setEditInstruction('')
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      pendingSheetActionLocks.current.delete(key)
      setPendingSheetActions(current => {
        const next = { ...current }
        delete next[key]
        return next
      })
      setError(reason instanceof Error ? reason.message : 'Could not queue reference-sheet variant')
    }
  }

  const updateStatus = async (assetId: string, variantId: string, status: 'kept' | 'rejected') => {
    const epoch = projectEpoch.current
    const submittedProject = project
    try {
      await setProjectAssetVariantStatus(project, assetId, variantId, status)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setError(reason instanceof Error ? reason.message : 'Could not update candidate')
    }
  }

  const deleteVariant = async (assetId: string, variantId: string, label: string) => {
    if (!window.confirm(`Permanently delete reference candidate “${label}” and its copied media?`)) return
    const epoch = projectEpoch.current
    const submittedProject = project
    try {
      await deleteProjectAssetVariant(project, assetId, variantId)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setError(reason instanceof Error ? reason.message : 'Could not delete candidate')
    }
  }

  const importVariant = async (assetId: string, file: File) => {
    const epoch = projectEpoch.current
    const submittedProject = project
    setImportErrors(current => ({ ...current, [assetId]: '' }))
    setImporting({ assetId, message: `Uploading ${file.name}…` })
    try {
      const uploaded = await uploadImage(file)
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setImporting({ assetId, message: 'Adding to project references…' })
      await addProjectAssetVariant(project, assetId, {
        source_workspace: project,
        variant_type: 'reference',
        label: file.name,
        status: 'kept',
        provenance: 'imported',
        outputs: [{ path: uploaded.path, label: file.name }],
        metadata: {
          original_filename: file.name,
          media_type: file.type,
          size_bytes: file.size,
        },
      })
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      requestRefresh()
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setImportErrors(current => ({
        ...current,
        [assetId]: reason instanceof Error ? reason.message : 'Could not import reference media',
      }))
    } finally {
      if (isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) setImporting(null)
    }
  }

  const applyReference = async (asset: ProjectAsset, variant: ProjectAsset['variants'][number]) => {
    const epoch = projectEpoch.current
    const submittedProject = project
    try {
      const output = selectProjectAssetApplyOutput(variant)
      if (!output) throw new Error('This reference candidate has no usable media')
      const url = getProjectAssetMediaUrl(submittedProject, output.relative_path)
      const response = await fetch(url)
      if (!response.ok) throw new Error('Could not load reference media')
      const blob = await response.blob()
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      const file = new File([blob], output.filename, { type: blob.type || output.media_type })
      if (output.media_type?.startsWith('video/')) {
        // A Director-approved Blender animation is a full-rate Studio control
        // reference with a paired semantic prompt, even when the library was
        // opened from Director mode.
        const setStudioParam = setParam as (key: string, value: unknown) => void
        const metadata = variant.metadata || {}
        const semanticMapping = metadata.semantic_mapping
        const semanticPrompt = typeof metadata.conditioned_prompt === 'string'
          ? metadata.conditioned_prompt
          : semanticMapping && typeof semanticMapping === 'object' && 'conditioned_prompt' in semanticMapping
            ? String((semanticMapping as { conditioned_prompt?: unknown }).conditioned_prompt || '')
            : ''
        const uploaded = await uploadImage(file)
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        setSidebarMode('studio')
        setGenerationMode('video')
        const recommendedModel = String(metadata.recommended_model_type || 'ltx2_22B_1_1')
        const controlMode = String(metadata.recommended_video_prompt_type || 'TVG')
        selectModel(recommendedModel)
        setStudioParam('video_guide', uploaded.path)
        setStudioParam('video_prompt_type', controlMode)
        setStudioParam('force_fps', 'control')
        setStudioParam('ic_lora_attention_strength', Number(metadata.attention_strength ?? 1))
        setStudioParam('ic_lora_reference_downscale', Number(metadata.reference_downscale_factor ?? 2))
        if (semanticPrompt) setStudioParam('prompt', semanticPrompt)
        setGuideVideoFps(uploaded.fps && uploaded.fps > 0 ? uploaded.fps : null)
        setGuideVideoFrameCount(uploaded.frame_count && uploaded.frame_count > 0 ? uploaded.frame_count : null)
        if (uploaded.frame_count && uploaded.frame_count > 0) {
          setStudioParam('video_length', uploaded.frame_count)
        }
      } else if (sidebarMode === 'director') {
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        if (asset.asset_type === 'character') addCharacterRef(file)
        else addLocationRef(file)
      } else {
        // Project asset cards are semantic identity/setting/item references in
        // Studio video mode. Route them to the separate non-distilled Ref2VA
        // checkpoint automatically; FL2VA treats images as timeline anchors.
        if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
        if (generationMode === 'video') {
          selectModel('minimax_h3_ref2va')
        }
        addImageRef(file)
      }
      setOpen(false)
    } catch (reason) {
      if (!isProjectAssetOperationCurrent(submittedProject, epoch, currentProject.current, projectEpoch.current)) return
      setError(reason instanceof Error ? reason.message : 'Could not use reference')
    }
  }

  return (
    <>
      <button
        ref={openButtonRef}
        type="button"
        onClick={() => setOpen(true)}
        disabled={browsingUploads || !project}
        className="mx-4 my-2 flex items-center justify-center gap-1.5 rounded-lg border border-border bg-bg-tertiary px-3 py-1.5 text-[11px] text-text-secondary hover:border-accent-blue/50 hover:text-accent-blue disabled:opacity-40"
      >
        <Library size={13} /> Project references & creation tool
      </button>

      {open && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-3">
          <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="project-reference-title" tabIndex={-1} onKeyDown={handleDialogKeyDown} className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h2 id="project-reference-title" className="text-sm font-semibold text-text-primary">Project references</h2>
                <p className="text-[10px] text-text-muted">{project} · characters, settings, items, styles, and generated candidates</p>
              </div>
              <button ref={closeButtonRef} type="button" aria-label="Close project references" onClick={() => setOpen(false)} className="text-text-muted hover:text-text-primary"><X size={17} /></button>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto md:grid-cols-[320px_1fr] md:overflow-hidden">
              <div className="overflow-visible border-b border-border p-4 md:overflow-y-auto md:border-b-0 md:border-r">
                <h3 className="mb-3 text-xs font-medium text-text-primary">Create reference candidates</h3>
                <div className="grid grid-cols-2 gap-1.5">
                  {ASSET_TYPES.map(option => {
                    const Icon = option.icon
                    return (
                      <button type="button" key={option.value} aria-pressed={assetType === option.value} onClick={() => setAssetType(option.value)} className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[10px] ${assetType === option.value ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary'}`}>
                        <Icon size={11} /> {option.label}
                      </button>
                    )
                  })}
                </div>
                <fieldset className="mt-3">
                  <legend className="mb-1.5 text-[10px] font-medium text-text-secondary">Sheet construction mode</legend>
                  <div className="space-y-1.5">
                    {SHEET_MODES.map(option => (
                      <label key={option.value} className={`block cursor-pointer rounded-md border p-2 ${sheetMode === option.value ? 'border-accent-blue bg-accent-blue/10' : 'border-border bg-bg-tertiary/40'}`}>
                        <span className="flex items-center gap-1.5 text-[10px] font-medium text-text-primary">
                          <input type="radio" name="reference-sheet-mode" value={option.value} checked={sheetMode === option.value} onChange={() => setSheetMode(option.value)} />
                          {option.label}
                        </span>
                        <span className="mt-0.5 block pl-5 text-[9px] leading-relaxed text-text-muted">{option.description}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <input aria-label="Reference name" value={name} onChange={event => setName(event.target.value)} placeholder="Name" className="mt-3 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                <textarea aria-label="Reference description" value={description} onChange={event => setDescription(event.target.value)} placeholder="Detailed description / card (optional)" rows={5} className="mt-2 w-full resize-y rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                <input aria-label="Reference visual style" value={style} onChange={event => setStyle(event.target.value)} placeholder="Genre / visual style" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                {assetType === 'character' && (
                  <>
                    <input aria-label="Reference character poses" value={poses} onChange={event => setPoses(event.target.value)} placeholder="Poses" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                    <input aria-label="Reference character outfits" value={outfits} onChange={event => setOutfits(event.target.value)} placeholder="Outfits / variants" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                  </>
                )}
                <label className="mt-3 flex items-center justify-between text-[10px] text-text-secondary">
                  Complete sheet candidates
                  <input type="number" min={1} max={8} value={candidateCount} onChange={event => setCandidateCount(Math.max(1, Math.min(8, Number(event.target.value) || 1)))} className="w-16 rounded border border-border bg-bg-tertiary px-2 py-1 text-right" />
                </label>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label className="text-[9px] text-text-muted">Collage columns
                    <input aria-label="Reference sheet collage columns" type="number" min={1} max={4} value={columns} onChange={event => setColumns(Math.max(1, Math.min(4, Number(event.target.value) || 1)))} className="mt-1 w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-text-secondary" />
                  </label>
                  <label className="text-[9px] text-text-muted">Palette swatches
                    <input aria-label="Reference sheet palette swatches" type="number" min={3} max={12} value={paletteSwatches} onChange={event => setPaletteSwatches(Math.max(3, Math.min(12, Number(event.target.value) || 3)))} className="mt-1 w-full rounded border border-border bg-bg-tertiary px-2 py-1 text-text-secondary" />
                  </label>
                </div>
                <label className="mt-2 flex items-center gap-2 text-[10px] text-text-secondary">
                  <input type="checkbox" checked={review} onChange={event => setReview(event.target.checked)} />
                  Local VLM review with at most one bounded panel repair
                </label>
                <button onClick={() => void generate()} disabled={submitting || !name.trim()} className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 py-2 text-xs font-medium text-white disabled:opacity-40">
                  {submitting ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Queue reference sheets
                </button>
                <p className="mt-2 text-[9px] leading-relaxed text-text-muted">Each candidate is one complete sheet. Its palette is an embedded region of that sheet, never a separate reference to apply. Keep one or more; originals and rejected candidates remain recorded until you delete them.</p>
                <details className="mt-3">
                  <summary className="cursor-pointer text-[10px] text-accent-blue">Build / sample a Blender scene guide</summary>
                  <BlenderSceneTool compact />
                </details>
                {queuedMessage && <p role="status" className="mt-2 text-[10px] text-accent-blue">{queuedMessage}</p>}
                {error && <p className="mt-2 text-[10px] text-red-400">{error}</p>}
              </div>

              <div className="overflow-visible p-4 md:overflow-y-auto">
                {loading && !assets.length ? (
                  <div className="flex h-48 items-center justify-center"><Loader2 size={20} className="animate-spin text-accent-blue" /></div>
                ) : assets.length === 0 ? (
                  <div className="flex h-48 items-center justify-center text-xs text-text-muted">No reference cards in this project yet.</div>
                ) : (
                  <div className="space-y-4">
                    {assets.map(asset => (
                      <section key={asset.id} className="rounded-lg border border-border bg-bg-tertiary/60 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div><h3 className="text-xs font-medium text-text-primary">{asset.name}</h3><p className="text-[9px] uppercase tracking-wide text-text-muted">{asset.asset_type}</p></div>
                          <div className="flex items-center gap-2">
                            <span className="text-[9px] text-text-muted">{asset.variants.length} variants</span>
                            <label className={`flex cursor-pointer items-center gap-1 rounded border border-accent-blue/40 px-2 py-1 text-[9px] text-accent-blue hover:bg-accent-blue/10 ${importing ? 'pointer-events-none opacity-50' : ''}`}>
                              {importing?.assetId === asset.id ? <Loader2 size={10} className="animate-spin" /> : <FileUp size={10} />}
                              Import media
                              <input
                                type="file"
                                accept="image/*,video/*"
                                aria-label={`Import media for ${asset.name}`}
                                className="sr-only"
                                disabled={Boolean(importing)}
                                onChange={event => {
                                  const file = event.currentTarget.files?.[0]
                                  event.currentTarget.value = ''
                                  if (file) void importVariant(asset.id, file)
                                }}
                              />
                            </label>
                          </div>
                        </div>
                        <p className="mt-1 text-[10px] text-text-secondary">{asset.description}</p>
                        {importing?.assetId === asset.id && <p className="mt-2 flex items-center gap-1 text-[9px] text-accent-blue"><Loader2 size={9} className="animate-spin" /> {importing.message}</p>}
                        {importErrors[asset.id] && <p className="mt-2 text-[9px] text-red-400">{importErrors[asset.id]}</p>}
                        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {asset.variants.map(variant => {
                            const applyOutput = selectProjectAssetApplyOutput(variant)
                            const componentOutputs = getProjectAssetComponentOutputs(variant)
                            const sheetStatus = referenceSheetStatus(variant)
                            const pendingKey = projectAssetVariantOperationKey(project, asset.id, variant.id)
                            const pendingAction = pendingSheetActions[pendingKey]
                            const editing = editVariantId === variant.id
                            const applyLabel = applyOutput?.media_type?.startsWith('video/')
                              ? 'Apply to Studio: LTX-2.3 control + semantic prompt'
                              : variant.variant_type === 'reference_sheet'
                                ? `Use complete sheet as ${sidebarMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 semantic ref (auto-select)' : 'Studio reference'}`
                                : `Use as ${sidebarMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 semantic ref (auto-select)' : 'Studio reference'}`
                            return (
                              <div key={variant.id} className={`overflow-hidden rounded-md border ${variant.status === 'kept' ? 'border-accent-green/60' : variant.status === 'rejected' ? 'border-border opacity-60' : 'border-border'}`}>
                                {applyOutput && (
                                  <ProjectAssetPreview
                                    project={project}
                                    assetId={asset.id}
                                    output={applyOutput}
                                    label={variant.variant_type === 'reference_sheet' ? `${variant.label} complete reference sheet` : variant.label}
                                  />
                                )}
                                <div className="p-2">
                                  <div className="flex items-center justify-between gap-1 text-[9px]"><span className="truncate text-text-secondary">{variant.label}</span><span className="shrink-0 text-text-muted">{variant.status}</span></div>
                                  {sheetStatus && (
                                    <div className="mt-1 text-[9px] leading-relaxed">
                                      <p className={sheetStatus.className}>{sheetStatus.label}</p>
                                      {sheetStatus.repair && <p className="text-text-muted">{sheetStatus.repair}</p>}
                                    </div>
                                  )}
                                  {componentOutputs.length > 0 && (
                                    <details className="mt-1.5 rounded border border-border/70 bg-bg-secondary/50">
                                      <summary className="flex cursor-pointer list-none items-center justify-between px-2 py-1 text-[9px] text-text-secondary">
                                        {componentOutputs.length} component panels <ChevronDown size={10} aria-hidden="true" />
                                      </summary>
                                      <div className="grid grid-cols-2 gap-1 border-t border-border p-1.5">
                                        {componentOutputs.map(output => {
                                          const role = output.metadata.reference_sheet?.role || output.label || 'component'
                                          return (
                                            <figure key={output.id} className="overflow-hidden rounded border border-border bg-bg-tertiary">
                                              <ProjectAssetPreview project={project} assetId={asset.id} output={output} label={`${variant.label}: ${friendlyRole(role)}`} />
                                              <figcaption className="truncate px-1 py-0.5 text-[8px] text-text-muted">{friendlyRole(role)}</figcaption>
                                            </figure>
                                          )
                                        })}
                                      </div>
                                    </details>
                                  )}
                                  <div className="mt-1.5 flex gap-1">
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void updateStatus(asset.id, variant.id, 'kept')} className="flex flex-1 items-center justify-center gap-1 rounded bg-accent-green/15 px-1 py-1 text-[9px] text-accent-green disabled:opacity-40"><Check size={9} /> Keep</button>
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void updateStatus(asset.id, variant.id, 'rejected')} className="rounded border border-border px-2 py-1 text-[9px] text-text-muted disabled:opacity-40">Reject</button>
                                    <button type="button" disabled={Boolean(pendingAction)} onClick={() => void deleteVariant(asset.id, variant.id, variant.label)} className="rounded border border-red-500/30 px-2 py-1 text-red-400 disabled:opacity-40" title="Delete candidate and copied media" aria-label={`Delete ${variant.label}`}><Trash2 size={9} /></button>
                                  </div>
                                  {variant.variant_type === 'reference_sheet' && (
                                    <div className="mt-1.5 grid grid-cols-2 gap-1">
                                      <button type="button" disabled={Boolean(pendingAction)} onClick={() => void generateFromVariant(asset, variant)} className="flex items-center justify-center gap-1 rounded border border-border px-1 py-1 text-[9px] text-text-secondary disabled:opacity-40">
                                        {pendingAction ? <Loader2 size={9} className="animate-spin" /> : <RotateCcw size={9} />} Retry
                                      </button>
                                      <button
                                        type="button"
                                        disabled={Boolean(pendingAction)}
                                        aria-expanded={editing}
                                        aria-controls={`reference-sheet-edit-${variant.id}`}
                                        onClick={() => {
                                          setEditVariantId(current => current === variant.id ? null : variant.id)
                                          setEditInstruction('')
                                        }}
                                        className="flex items-center justify-center gap-1 rounded border border-border px-1 py-1 text-[9px] text-text-secondary disabled:opacity-40"
                                      >
                                        <Pencil size={9} /> Edit
                                      </button>
                                    </div>
                                  )}
                                  {variant.variant_type === 'reference_sheet' && <p className="mt-1 text-[8px] leading-relaxed text-text-muted">Retry/Edit preserves recorded source mode, model, and privacy policy; current collage, palette, and review controls shape the new candidate.</p>}
                                  {editing && variant.variant_type === 'reference_sheet' && (
                                    <div id={`reference-sheet-edit-${variant.id}`} className="mt-1.5 rounded border border-border p-1.5">
                                      <label htmlFor={`reference-sheet-edit-instruction-${variant.id}`} className="text-[9px] text-text-muted">What should change in the next candidate?</label>
                                      <textarea
                                        id={`reference-sheet-edit-instruction-${variant.id}`}
                                        value={editInstruction}
                                        onChange={event => setEditInstruction(event.target.value)}
                                        rows={3}
                                        className="mt-1 w-full resize-y rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] text-text-primary"
                                      />
                                      <div className="mt-1 flex gap-1">
                                        <button type="button" disabled={!editInstruction.trim() || Boolean(pendingAction)} onClick={() => void generateFromVariant(asset, variant, editInstruction)} className="flex-1 rounded bg-accent-blue px-1 py-1 text-[9px] text-white disabled:opacity-40">Queue edited candidate</button>
                                        <button type="button" onClick={() => { setEditVariantId(null); setEditInstruction('') }} className="rounded border border-border px-2 py-1 text-[9px] text-text-muted">Cancel</button>
                                      </div>
                                    </div>
                                  )}
                                  {pendingAction && <p role="status" className="mt-1.5 flex items-center gap-1 text-[9px] text-accent-blue"><Loader2 size={9} className="animate-spin" /> {pendingAction.jobId ? 'Queued; waiting for the new candidate…' : 'Submitting…'}</p>}
                                  {variant.status === 'kept' && applyOutput && <button type="button" onClick={() => void applyReference(asset, variant)} className="mt-1.5 w-full rounded border border-accent-blue/40 px-1 py-1 text-[9px] text-accent-blue">{applyLabel}</button>}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
