import { useCallback, useEffect, useState } from 'react'
import { Check, EyeOff, FileUp, ImagePlus, Library, Loader2, MapPin, Package, Trash2, UserRound, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import {
  fetchProjectAssets,
  addProjectAssetVariant,
  deleteProjectAssetVariant,
  generateProjectAssetReferences,
  getProjectAssetMediaUrl,
  setProjectAssetVariantStatus,
  uploadImage,
  type ProjectAsset,
  type ProjectAssetOutput,
} from '../../api/client'
import { BlenderSceneTool } from './BlenderSceneTool'
import { hidePrivatePreview, privatePreviewIdentity, privatePreviewWasRevealed, revealPrivatePreview } from '../../lib/privatePreview'

const ASSET_TYPES = [
  { value: 'character', label: 'Character', icon: UserRound },
  { value: 'setting', label: 'Setting', icon: MapPin },
  { value: 'item', label: 'Item', icon: Package },
  { value: 'style', label: 'Style', icon: ImagePlus },
]

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
  const [assetType, setAssetType] = useState('character')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [style, setStyle] = useState('cinematic production reference sheet')
  const [poses, setPoses] = useState('front, profile, three-quarter')
  const [outfits, setOutfits] = useState('primary outfit')
  const [candidateCount, setCandidateCount] = useState(2)
  const [importing, setImporting] = useState<{ assetId: string; message: string } | null>(null)
  const [importErrors, setImportErrors] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    if (!open || browsingUploads) return
    try {
      setAssets(await fetchProjectAssets(project))
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to load references')
    } finally {
      setLoading(false)
    }
  }, [open, browsingUploads, project])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [open, refresh])

  const generate = async () => {
    if (!name.trim() || !description.trim()) return
    setSubmitting(true)
    setError('')
    try {
      await generateProjectAssetReferences(project, {
        name: name.trim(),
        asset_type: assetType,
        description: description.trim(),
        style,
        poses: assetType === 'character' ? poses : undefined,
        outfits: assetType === 'character' ? outfits : undefined,
        candidate_count: candidateCount,
        model_type: selectedModelPerMode.image || 'flux2_klein_9b',
        private_output: privateOutput,
        explicit_output: explicitOutput,
      })
      setName('')
      setDescription('')
      await reconnectJobs()
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Reference generation failed')
    } finally {
      setSubmitting(false)
    }
  }

  const updateStatus = async (assetId: string, variantId: string, status: 'kept' | 'rejected') => {
    try {
      await setProjectAssetVariantStatus(project, assetId, variantId, status)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update candidate')
    }
  }

  const deleteVariant = async (assetId: string, variantId: string, label: string) => {
    if (!window.confirm(`Permanently delete reference candidate “${label}” and its copied media?`)) return
    try {
      await deleteProjectAssetVariant(project, assetId, variantId)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete candidate')
    }
  }

  const importVariant = async (assetId: string, file: File) => {
    setImportErrors(current => ({ ...current, [assetId]: '' }))
    setImporting({ assetId, message: `Uploading ${file.name}…` })
    try {
      const uploaded = await uploadImage(file)
      setImporting({ assetId, message: 'Adding to project references…' })
      await addProjectAssetVariant(project, assetId, {
        source_workspace: project,
        variant_type: 'reference',
        label: file.name,
        status: 'kept',
        provenance: 'local_import',
        outputs: [{ path: uploaded.path, label: file.name }],
        metadata: {
          original_filename: file.name,
          media_type: file.type,
          size_bytes: file.size,
        },
      })
      await refresh()
    } catch (reason) {
      setImportErrors(current => ({
        ...current,
        [assetId]: reason instanceof Error ? reason.message : 'Could not import reference media',
      }))
    } finally {
      setImporting(null)
    }
  }

  const applyReference = async (asset: ProjectAsset, variant: ProjectAsset['variants'][number], output: ProjectAssetOutput) => {
    try {
      const url = getProjectAssetMediaUrl(project, output.relative_path)
      const response = await fetch(url)
      if (!response.ok) throw new Error('Could not load reference media')
      const blob = await response.blob()
      const file = new File([blob], output.filename, { type: blob.type || output.media_type })
      if (output.media_type?.startsWith('video/')) {
        // A Director-approved Blender animation is a full-rate Studio control
        // reference with a paired semantic prompt, even when the library was
        // opened from Director mode.
        setSidebarMode('studio')
        setGenerationMode('video')
        const setStudioParam = setParam as (key: string, value: unknown) => void
        const metadata = variant.metadata || {}
        const semanticMapping = metadata.semantic_mapping
        const semanticPrompt = typeof metadata.conditioned_prompt === 'string'
          ? metadata.conditioned_prompt
          : semanticMapping && typeof semanticMapping === 'object' && 'conditioned_prompt' in semanticMapping
            ? String((semanticMapping as { conditioned_prompt?: unknown }).conditioned_prompt || '')
            : ''
        const uploaded = await uploadImage(file)
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
        if (asset.asset_type === 'character') addCharacterRef(file)
        else addLocationRef(file)
      } else {
        // Project asset cards are semantic identity/setting/item references in
        // Studio video mode. Route them to the separate non-distilled Ref2VA
        // checkpoint automatically; FL2VA treats images as timeline anchors.
        if (generationMode === 'video') {
          selectModel('minimax_h3_ref2va')
        }
        addImageRef(file)
      }
      setOpen(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not use reference')
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={browsingUploads || !project}
        className="mx-4 my-2 flex items-center justify-center gap-1.5 rounded-lg border border-border bg-bg-tertiary px-3 py-1.5 text-[11px] text-text-secondary hover:border-accent-blue/50 hover:text-accent-blue disabled:opacity-40"
      >
        <Library size={13} /> Project references & creation tool
      </button>

      {open && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-3">
          <div className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-text-primary">Project references</h2>
                <p className="text-[10px] text-text-muted">{project} · characters, settings, items, styles, and generated candidates</p>
              </div>
              <button onClick={() => setOpen(false)} className="text-text-muted hover:text-text-primary"><X size={17} /></button>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[320px_1fr]">
              <div className="overflow-y-auto border-b border-border p-4 md:border-b-0 md:border-r">
                <h3 className="mb-3 text-xs font-medium text-text-primary">Create reference candidates</h3>
                <div className="grid grid-cols-2 gap-1.5">
                  {ASSET_TYPES.map(option => {
                    const Icon = option.icon
                    return (
                      <button key={option.value} onClick={() => setAssetType(option.value)} className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[10px] ${assetType === option.value ? 'border-accent-blue bg-accent-blue/15 text-accent-blue' : 'border-border text-text-secondary'}`}>
                        <Icon size={11} /> {option.label}
                      </button>
                    )
                  })}
                </div>
                <input value={name} onChange={event => setName(event.target.value)} placeholder="Name" className="mt-3 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="Detailed description / card" rows={5} className="mt-2 w-full resize-y rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                <input value={style} onChange={event => setStyle(event.target.value)} placeholder="Genre / visual style" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                {assetType === 'character' && (
                  <>
                    <input value={poses} onChange={event => setPoses(event.target.value)} placeholder="Poses" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                    <input value={outfits} onChange={event => setOutfits(event.target.value)} placeholder="Outfits / variants" className="mt-2 w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-2 text-xs text-text-primary" />
                  </>
                )}
                <label className="mt-3 flex items-center justify-between text-[10px] text-text-secondary">
                  Candidate outputs
                  <input type="number" min={1} max={8} value={candidateCount} onChange={event => setCandidateCount(Math.max(1, Math.min(8, Number(event.target.value) || 1)))} className="w-16 rounded border border-border bg-bg-tertiary px-2 py-1 text-right" />
                </label>
                <button onClick={() => void generate()} disabled={submitting || !name.trim() || !description.trim()} className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent-blue px-3 py-2 text-xs font-medium text-white disabled:opacity-40">
                  {submitting ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Generate candidates
                </button>
                <p className="mt-2 text-[9px] leading-relaxed text-text-muted">Each output becomes a separate candidate. Keep one or more; rejected candidates remain recorded until deleted.</p>
                <details className="mt-3">
                  <summary className="cursor-pointer text-[10px] text-accent-blue">Build / sample a Blender scene guide</summary>
                  <BlenderSceneTool compact />
                </details>
                {error && <p className="mt-2 text-[10px] text-red-400">{error}</p>}
              </div>

              <div className="overflow-y-auto p-4">
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
                                className="hidden"
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
                        <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-3">
                          {asset.variants.map(variant => (
                            <div key={variant.id} className={`overflow-hidden rounded-md border ${variant.status === 'kept' ? 'border-accent-green/60' : variant.status === 'rejected' ? 'border-border opacity-45' : 'border-border'}`}>
                              {variant.outputs[0] && (
                                <ProjectAssetPreview
                                  project={project}
                                  assetId={asset.id}
                                  output={variant.outputs[0]}
                                  label={variant.label}
                                />
                              )}
                              <div className="p-2">
                                <div className="flex items-center justify-between text-[9px]"><span className="truncate text-text-secondary">{variant.label}</span><span className="text-text-muted">{variant.status}</span></div>
                                <div className="mt-1.5 flex gap-1">
                                  <button onClick={() => void updateStatus(asset.id, variant.id, 'kept')} className="flex flex-1 items-center justify-center gap-1 rounded bg-accent-green/15 px-1 py-1 text-[9px] text-accent-green"><Check size={9} /> Keep</button>
                                  <button onClick={() => void updateStatus(asset.id, variant.id, 'rejected')} className="rounded border border-border px-2 py-1 text-[9px] text-text-muted">Reject</button>
                                  <button onClick={() => void deleteVariant(asset.id, variant.id, variant.label)} className="rounded border border-red-500/30 px-2 py-1 text-red-400" title="Delete candidate and copied media"><Trash2 size={9} /></button>
                                </div>
                                {variant.status === 'kept' && variant.outputs[0] && <button onClick={() => void applyReference(asset, variant, variant.outputs[0])} className="mt-1.5 w-full rounded border border-accent-blue/40 px-1 py-1 text-[9px] text-accent-blue">{variant.outputs[0].media_type?.startsWith('video/') ? 'Apply to Studio: LTX-2.3 control + semantic prompt' : `Use as ${sidebarMode === 'director' ? asset.asset_type : generationMode === 'video' ? 'H3 semantic ref (auto-select)' : 'Studio reference'}`}</button>}
                              </div>
                            </div>
                          ))}
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
