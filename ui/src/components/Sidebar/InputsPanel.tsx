import { useState, useMemo, useEffect } from 'react'
import { X, Upload, Plus, Music, Film, Mic } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import * as api from '../../api/client'
import { controlFpsTotalFrames, effectiveSlidingWindowGeometry } from '../../lib/timelinePrompt'
import { HOST_TERM_NOTICES } from '../../lib/hostTerms'
import { DOWNLOAD_REFRESH_EVENT } from '../../lib/useVisibilityPolling'

// Unified, media-driven "Inputs" panel for Studio Frames mode (image_mode 0).
//
// Goal: a single image-forward tile surface where the media you add auto-selects
// the pipeline (image -> frame, audio -> soundtrack/voice, video -> control),
// replacing the old scattered sections + the audio mode dropdown.
//
// Shipped so far:
//   slice 1  — start / end frame tiles (replacing ImageUpload in Frames mode)
//   slice 1b — inject reference frames as tiles, out of the Advanced menu.
//   slice 2  — audio (soundtrack) + control-video tiles. Dropping audio sets the
//              model's soundtrack audio_prompt_type + audio_guide; adding a
//              control video sets "K" + video_guide. AudioModeSection's dropdown
//              is hidden in Frames mode (Sidebar) — the tiles route it instead.
// Next: voice-ref, references, then one unified drop zone + role auto-detect.
//
// Existing params stay the source of truth, so Load Settings restore keeps
// working (tiles are derived from params).

interface InjectedFrame {
  path: string
  filename: string
  position: string
  previewUrl: string
  window: number
  offset: string
  // The original File, kept for freshly-added frames so re-roling an inject
  // back to a native start/end frame can reuse it (restored frames have null
  // and fall back to their uploaded path).
  file: File | null
}

const OFFSET_PRESETS = [
  { value: 'start', label: 'Start', pct: 0 },
  { value: '25%', label: '25%', pct: 0.25 },
  { value: 'middle', label: 'Mid', pct: 0.5 },
  { value: '75%', label: '75%', pct: 0.75 },
  { value: 'end', label: 'End', pct: 1.0 },
] as const

const WINDOW_TOKEN_RE = /^[Ww](\d+):(\d{1,3})$/
const H3_REF2VA_LIMITS = { images: 9, videos: 3, audio: 3, mixed: 12 }
const H3_STUDIO_MODELS = new Set([
  'minimax_h3',
  'minimax_h3_pinkcherry_fl2va',
  'minimax_h3_w4a8_fl2va',
  'minimax_h3_ref2va',
])

const calcPositionToken = (windowIdx: number, offset: string): string => {
  const preset = OFFSET_PRESETS.find(p => p.value === offset)
  const pct = Math.round((preset?.pct ?? 1.0) * 100)
  return `W${windowIdx + 1}:${pct}`
}

const snapToOffsetPreset = (pct: number): string => {
  let closest: (typeof OFFSET_PRESETS)[number] = OFFSET_PRESETS[0]
  let minDist = Math.abs(pct - closest.pct)
  for (const p of OFFSET_PRESETS) {
    const dist = Math.abs(pct - p.pct)
    if (dist < minDist) { closest = p; minDist = dist }
  }
  return closest.value
}

const basename = (p: string) => p.replace(/\\/g, '/').split('/').pop() || p

const getMediaDuration = (file: File): Promise<number | null> => {
  const isVid = file.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(file.name)
  return new Promise(resolve => {
    const url = URL.createObjectURL(file)
    const el: HTMLMediaElement = isVid ? document.createElement('video') : new Audio()
    el.addEventListener('loadedmetadata', () => {
      const d = el.duration; URL.revokeObjectURL(url); resolve(Number.isFinite(d) ? d : null)
    })
    el.addEventListener('error', () => { URL.revokeObjectURL(url); resolve(null) })
    el.src = url
  })
}

const getUploadedMediaDuration = (path: string, video: boolean): Promise<number | null> => (
  new Promise(resolve => {
    const el: HTMLMediaElement = video ? document.createElement('video') : new Audio()
    el.preload = 'metadata'
    el.addEventListener('loadedmetadata', () => {
      resolve(Number.isFinite(el.duration) ? el.duration : null)
    }, { once: true })
    el.addEventListener('error', () => resolve(null), { once: true })
    el.src = api.getUploadUrl(basename(path))
  })
)

export function InputsPanel() {
  const modelOptions = useStore(s => s.modelOptions)
  const startImage = useStore(s => s.startImage)
  const endImage = useStore(s => s.endImage)
  const setStartImage = useStore(s => s.setStartImage)
  const setEndImage = useStore(s => s.setEndImage)
  const supportsEndFrame = useStore(s => s.modelOptions?.supports_end_frame ?? false)
  const strengthLabel = useStore(s => s.modelOptions?.input_video_strength_label ?? '')
  const inputVideoStrength = useStore(s => s.params.input_video_strength ?? 1.0)
  const params = useStore(s => s.params)
  const setParam = useStore(s => s.setParam)
  const activeModel = useStore(s => s.models.find(model => model.model_type === s.params.model_type))
  const ref2vaModel = useStore(s => s.models.find(model => model.model_type === 'minimax_h3_ref2va'))
  const loadModels = useStore(s => s.loadModels)
  const durationSeconds = useStore(s => s.durationSeconds)
  const slidingWindowSeconds = useStore(s => s.slidingWindowSeconds)
  const slidingWindowOverlap = useStore(s => s.slidingWindowOverlap)
  const audioGuideFilename = useStore(s => s.audioGuideFilename)
  const setAudioGuideFilename = useStore(s => s.setAudioGuideFilename)
  const setDurationSeconds = useStore(s => s.setDurationSeconds)
  const setGuideVideoFps = useStore(s => s.setGuideVideoFps)
  const guideVideoFps = useStore(s => s.guideVideoFps)
  const setGuideVideoFrameCount = useStore(s => s.setGuideVideoFrameCount)
  const guideVideoFrameCount = useStore(s => s.guideVideoFrameCount)
  const voiceRefEnabled = useStore(s => !!s.servicesConfig?.voice_reference_enabled)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const directorVoiceRef = useStore(s => s.directorVoiceRef)
  const setDirectorVoiceRef = useStore(s => s.setDirectorVoiceRef)
  const identityScale = useStore(s => s.directorIdentityGuidanceScale)
  const setIdentityScale = useStore(s => s.setDirectorIdentityGuidanceScale)
  const imageRefs = useStore(s => s.imageRefs)
  const addImageRef = useStore(s => s.addImageRef)
  const removeImageRef = useStore(s => s.removeImageRef)
  const reorderImageRefs = useStore(s => s.reorderImageRefs)
  const imageRefType = useStore(s => s.imageRefType)
  const setImageRefType = useStore(s => s.setImageRefType)
  const removeBackgroundRefs = useStore(s => s.removeBackgroundRefs)
  const setRemoveBackgroundRefs = useStore(s => s.setRemoveBackgroundRefs)
  const continueVideo = useStore(s => s.continueVideo)
  const continueVideoUrl = useStore(s => s.continueVideoUrl)
  const continueVideoDuration = useStore(s => s.continueVideoDuration)
  const setContinueVideo = useStore(s => s.setContinueVideo)
  const clearContinueVideo = useStore(s => s.clearContinueVideo)
  const isExtend = (params.image_mode as number) === 3
  const h3StudioWorkflow = (
    H3_STUDIO_MODELS.has(params.model_type)
    || modelOptions?.architecture === 'minimax_h3'
    || modelOptions?.architecture === 'minimax_h3_ref2va'
  )
  const h3AdaptiveConditioning = params.h3_adaptive_conditioning !== false
  const dedicatedRef2VAMode = (
    params.model_type === 'minimax_h3_ref2va'
    || modelOptions?.architecture === 'minimax_h3_ref2va'
    || modelOptions?.minimax_h3_conditioning_mode === 'semantic_references'
  )
  const h3HasSemanticInputs = (
    imageRefs.length > 0
    || (params.image_refs?.length ?? 0) > 0
    || [
      params.video_guide, params.video_guide2, params.video_guide3,
      params.audio_guide, params.audio_guide2, params.audio_guide3,
    ].some(Boolean)
  )
  // Adaptive Studio is a hybrid input surface: FL2VA consumes the edge
  // anchors while Ref2VA consumes semantic references on the segments chosen
  // by the reviewed plan. Keep existing semantic inputs visible after Auto is
  // turned off so the user can remove the now-incompatible inputs.
  const semanticReferenceMode = dedicatedRef2VAMode || (
    h3StudioWorkflow && (h3AdaptiveConditioning || h3HasSemanticInputs)
  )
  const canAttachSemanticReferences = dedicatedRef2VAMode || (
    h3StudioWorkflow && h3AdaptiveConditioning
  )
  const h3HasFrameInputs = !!(
    startImage || endImage || params.image_start || params.image_end
  )
  const canAttachFrameAnchors = !dedicatedRef2VAMode || h3AdaptiveConditioning
  const showFrameAnchorControls = canAttachFrameAnchors || h3HasFrameInputs

  const [selected, setSelected] = useState<string | null>(null)
  const [injectedFrames, setInjectedFrames] = useState<InjectedFrame[]>([])
  const [frameUploading, setFrameUploading] = useState(false)
  const [videoGuideFilename, setVideoGuideFilename] = useState<string | null>(null)
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)
  const [frameDragKey, setFrameDragKey] = useState<string | null>(null)
  const [frameDragOverKey, setFrameDragOverKey] = useState<string | null>(null)
  const [semanticRefDurations, setSemanticRefDurations] = useState<Record<string, number>>({})
  const [h3DownloadStatus, setH3DownloadStatus] = useState<'idle' | 'downloading' | 'failed'>('idle')
  const h3TermsAccepted = hostTerms?.minimax_h3_ref2va.accepted === true

  useEffect(() => {
    if (activeWorkspace && !hostTerms && !hostTermsLoading) void loadHostTerms()
  }, [activeWorkspace, hostTerms, hostTermsLoading, loadHostTerms])

  useEffect(() => {
    setSelected(null)
  }, [semanticReferenceMode])

  useEffect(() => {
    if (h3DownloadStatus !== 'downloading') return
    const timer = window.setInterval(async () => {
      try {
        const result = await api.fetchModelDownloads()
        const status = result.downloads.minimax_h3_ref2va?.status
        if (status === 'completed') {
          setH3DownloadStatus('idle')
          await loadModels()
        } else if (status === 'failed') {
          setH3DownloadStatus('failed')
        }
      } catch { /* keep polling while the backend is reachable intermittently */ }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [h3DownloadStatus, loadModels])

  const installH3Ref2VA = async () => {
    if (!h3TermsAccepted || !h3StudioWorkflow) return
    setH3DownloadStatus('downloading')
    try {
      await api.downloadModel('minimax_h3_ref2va', activeWorkspace || '')
      window.dispatchEvent(new CustomEvent(DOWNLOAD_REFRESH_EVENT))
    } catch {
      setH3DownloadStatus('failed')
    }
  }

  // ── Inject capability + window layout ──────────────────────────────
  const supportsInject = useMemo(() => {
    const cfg = (modelOptions?.guide_preprocessing || modelOptions?.guide_custom_choices) as
      { choices?: [string, string][]; selection?: string[] } | undefined
    if (!cfg) return false
    const values = cfg.choices ? cfg.choices.map(([, v]) => v) : (cfg.selection || [])
    return values.some(v => typeof v === 'string' && v.includes('KFI'))
  }, [modelOptions])

  const windowInfo = useMemo(() => {
    const fps = modelOptions?.fps ?? 25
    const hasEndAnchor = !!endImage || (
      Array.isArray(params.image_end) ? params.image_end.some(Boolean) : !!params.image_end
    )
    const requestedFrames = controlFpsTotalFrames(
      durationSeconds, params.force_fps, params.video_guide, guideVideoFps, guideVideoFrameCount,
    ) ?? Math.round(durationSeconds * fps)
    // H3 generates one native tail step for a supplied final-frame contract,
    // then trims the joined output back to the requested duration. Include
    // that tail in the at-a-glance segment count so the final tile and plan
    // agree at exact segment boundaries.
    const plannedFrames = requestedFrames + (
      h3StudioWorkflow && hasEndAnchor ? (modelOptions?.frames_steps || 0) : 0
    )
    const windowCount = modelOptions
      ? effectiveSlidingWindowGeometry(
          durationSeconds, slidingWindowSeconds, slidingWindowOverlap, modelOptions,
          { totalFrames: plannedFrames },
        ).windowCount
      : 1
    return { fps, windowCount }
  }, [modelOptions, durationSeconds, slidingWindowSeconds, slidingWindowOverlap, params.force_fps, params.video_guide, params.image_end, guideVideoFps, guideVideoFrameCount, h3StudioWorkflow, endImage])

  // ── Audio / control-video capability + current state ───────────────
  const audioCfg = modelOptions?.audio_prompt_type_sources as
    { choices?: [string, string][]; selection?: string[]; default?: string } | undefined
  const audioVals = audioCfg ? (audioCfg.choices ? audioCfg.choices.map(([, v]) => v) : (audioCfg.selection || [])) : []
  const audioOnly = !!modelOptions?.audio_only
  const soundtrackVal = audioVals.find(v => typeof v === 'string' && v.includes('A'))
  const supportsSoundtrack = !!soundtrackVal && !audioOnly
  const supportsControlVid = audioVals.includes('K')
  const audioPT = (params.audio_prompt_type as string) || ''
  const audioBase = audioPT.replace(/[NV]/g, '')
  const audioFlags = audioPT.replace(/[^NV]/g, '')
  // Media presence and audio behavior are independent. A control video can
  // keep driving motion while LTX-2 generates its soundtrack from text,
  // derives fresh audio from the video, or uses an uploaded soundtrack.
  const hasSoundtrack = supportsSoundtrack && !!params.audio_guide
  const hasControlVid = supportsControlVid && !!params.video_guide
  const soundtrackName = audioGuideFilename || (params.audio_guide ? basename(params.audio_guide as string) : null)
  const controlVidName = videoGuideFilename || (params.video_guide ? basename(params.video_guide as string) : null)

  // ── Guide video (motion source) for guide_custom_choices models ────
  // Models like SCAIL-2 take a Control Video as the motion/scene guide
  // (video_prompt_type contains 'V') with no audio coupling. Models with
  // guide_preprocessing keep their upload in Advanced Settings, and
  // K-audio models keep the soundtrack-coupled tile above — this tile
  // only fills the gap between them.
  const guideCfg = modelOptions?.guide_custom_choices as { choices?: [string, string][]; default?: string } | undefined
  const guideDefault = guideCfg?.default || ''
  const guideValues = guideCfg?.choices?.map(([, value]) => value) || []
  const rawControlProcess = guideValues.find(value => value === 'VG' || value === 'V') || ''
  const guideProcess = ((params.video_prompt_type as string) || guideDefault).replace(/T$/, '')
  const supportsGuideVid = !!guideCfg && !modelOptions?.guide_preprocessing && !supportsControlVid && guideProcess.includes('V')
  const hasGuideVid = supportsGuideVid && !!params.video_guide

  // ── Reference images (image_ref_choices) ───────────────────────────
  const refCfg = modelOptions?.image_ref_choices as { choices?: [string, string][] } | undefined
  const supportsRefs = semanticReferenceMode
    ? h3TermsAccepted || h3HasSemanticInputs
    : !!refCfg
  const hasLandscapeMode = refCfg?.choices?.some(([, v]) => v.includes('K')) ?? false
  const hasPeopleMode = refCfg?.choices?.some(([, v]) => v === 'I') ?? false
  const refBgLabel = modelOptions?.background_removal_label
  // max_image_refs includes the Edit source image, when present.
  const semanticVideoPaths = [params.video_guide, params.video_guide2, params.video_guide3]
    .filter((path): path is string => typeof path === 'string' && path.length > 0)
  const semanticAudioPaths = [params.audio_guide, params.audio_guide2, params.audio_guide3]
    .filter((path): path is string => typeof path === 'string' && path.length > 0)
  const semanticImageCount = semanticReferenceMode
    ? imageRefs.length + (params.image_refs?.length || 0)
    : imageRefs.length
  const restoredSemanticImagePaths = semanticReferenceMode
    ? (params.image_refs || [])
    : []
  const semanticMixedCount = semanticImageCount + semanticVideoPaths.length + semanticAudioPaths.length
  const semanticPathsKey = `${semanticVideoPaths.join('|')}::${semanticAudioPaths.join('|')}`
  const semanticVideoDurationTotal = semanticVideoPaths.reduce(
    (sum, path) => sum + (semanticRefDurations[path] || 0), 0,
  )
  const semanticAudioDurationTotal = semanticAudioPaths.reduce(
    (sum, path) => sum + (semanticRefDurations[path] || 0), 0,
  )
  const configuredMaxRefs = semanticReferenceMode
    ? Math.min(H3_REF2VA_LIMITS.images, H3_REF2VA_LIMITS.mixed - semanticVideoPaths.length - semanticAudioPaths.length)
    : modelOptions?.max_image_refs ?? null
  const maxRefs = configuredMaxRefs == null
    ? null
    : Math.max(0, configuredMaxRefs - ((params.image_mode as number) === 2 ? 1 : 0))
  const canAddRef = maxRefs == null || semanticImageCount < maxRefs
  const defaultRefType = hasLandscapeMode ? 'KI' : hasPeopleMode ? 'I' : ''

  // Auto-set the ref type when references are added/removed (mirrors ImageRefSection).
  useEffect(() => {
    if (imageRefs.length > 0 && imageRefType === '') setImageRefType(defaultRefType)
    else if (imageRefs.length === 0 && imageRefType !== '') setImageRefType('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageRefs.length])

  // Settings restore retains server upload paths but not browser File
  // objects. Re-probe those authorized uploads so the 15-second aggregate
  // limits remain enforced after refresh/load instead of treating old refs as
  // zero-duration.
  useEffect(() => {
    if (!semanticReferenceMode) return
    let cancelled = false
    const missing = [
      ...semanticVideoPaths.filter(path => semanticRefDurations[path] == null).map(path => ({ path, video: true })),
      ...semanticAudioPaths.filter(path => semanticRefDurations[path] == null).map(path => ({ path, video: false })),
    ]
    if (!missing.length) return
    void Promise.all(missing.map(async item => ({
      path: item.path,
      duration: await getUploadedMediaDuration(item.path, item.video),
    }))).then(results => {
      if (cancelled) return
      setSemanticRefDurations(current => {
        const next = { ...current }
        results.forEach(result => {
          if (result.duration != null && result.duration > 0) next[result.path] = result.duration
        })
        return next
      })
    })
    return () => { cancelled = true }
    // semanticPathsKey deliberately captures restored path identity; the
    // duration map itself must not retrigger media probes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [semanticReferenceMode, semanticPathsKey])

  const pickReferences = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.png,.jpg,.jpeg,.webp,.bmp'
    input.multiple = true
    input.onchange = () => {
      const files = Array.from(input.files || [])
      const room = maxRefs == null ? files.length : Math.max(0, maxRefs - semanticImageCount)
      files.slice(0, room).forEach(addImageRef)
    }
    input.click()
  }

  // Extend mode: the source video to continue from.
  const handleAddExtendSource = async (file: File) => {
    if (!file.type.startsWith('video/')) return
    try {
      const result = await api.uploadImage(file)
      const url = URL.createObjectURL(file)
      const video = document.createElement('video')
      video.src = url
      video.onloadedmetadata = () => {
        setContinueVideo(file, result.path, url, (video.duration && isFinite(video.duration)) ? video.duration : 0)
      }
    } catch (e) {
      console.error('Extend source upload failed:', e)
    }
  }

  // Restore inject tiles from params (Load Settings / KFI toggled in Advanced).
  useEffect(() => {
    const vpt = params.video_prompt_type || ''
    const refs = params.image_refs as string[] | undefined
    const positions = ((params.frames_positions as string) || '').split(' ').filter(Boolean)
    if (vpt.includes('KFI') && refs && refs.length > 0) {
      const restored: InjectedFrame[] = refs.map((refPath, i) => {
        const filename = basename(refPath)
        const pos = positions[i] || 'L'
        let win = 0, offset = 'end'
        const m = WINDOW_TOKEN_RE.exec(pos)
        if (m) {
          win = Math.max(0, parseInt(m[1], 10) - 1)
          offset = snapToOffsetPreset(Math.min(100, parseInt(m[2], 10)) / 100)
        }
        return { path: refPath, filename, position: pos, previewUrl: `/api/v1/uploads/${filename}`, window: win, offset, file: null }
      })
      const same = restored.length === injectedFrames.length &&
        restored.every((r, i) => r.path === injectedFrames[i]?.path && r.position === injectedFrames[i]?.position)
      if (!same) setInjectedFrames(restored)
    } else if (!vpt.includes('KFI') && injectedFrames.length > 0) {
      setInjectedFrames([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.video_prompt_type, params.image_refs, params.frames_positions])

  const pickFile = (accept: string, onFile: (f: File) => void) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.onchange = e => {
      const f = (e.target as HTMLInputElement).files?.[0]
      if (f) onFile(f)
    }
    input.click()
  }
  const pickImage = (onFile: (f: File) => void) => pickFile('image/*', onFile)

  // ── Inject handlers ────────────────────────────────────────────────
  const syncFrameParams = (frames: InjectedFrame[]) => {
    if (frames.length === 0) {
      setParam('image_refs', undefined)
      setParam('frames_positions', undefined)
      const vpt = (params.video_prompt_type as string) || ''
      if (vpt.includes('KFI')) setParam('video_prompt_type', vpt.replace('KFI', ''))
      return
    }
    setParam('image_refs', frames.map(f => f.path) as unknown as never)
    setParam('frames_positions', frames.map(f => f.position).join(' ') as unknown as never)
    const vpt = (params.video_prompt_type as string) || ''
    if (!vpt.includes('KFI')) setParam('video_prompt_type', 'KFI')
  }

  const addInjectFrame = async (file: File | null, path: string | null, previewUrl: string | null, offset: string, windowIdx = 0) => {
    setFrameUploading(true)
    try {
      let p = path
      if (!p && file) p = (await api.uploadImage(file)).path
      if (!p) return
      const newFrame: InjectedFrame = {
        path: p, filename: file?.name || basename(p), file: file ?? null,
        position: calcPositionToken(windowIdx, offset),
        previewUrl: previewUrl || `/api/v1/uploads/${basename(p)}`,
        window: windowIdx, offset,
      }
      const updated = [...injectedFrames, newFrame]
      setInjectedFrames(updated)
      syncFrameParams(updated)
    } catch (e) {
      console.error('Frame upload failed:', e)
    } finally {
      setFrameUploading(false)
    }
  }
  const handleRemoveFrame = (index: number) => {
    const updated = injectedFrames.filter((_, i) => i !== index)
    setInjectedFrames(updated)
    syncFrameParams(updated)
  }

  // ── Unified, window-aware "Frame" model (one concept, pipeline invisible) ──
  // Every frame is just a (window, offset). Its designation picks the pipe:
  //   W1 Start  -> native i2v start frame (image_start)
  //   Final End -> native end frame (image_end; last H3 segment when planned)
  //   everything else (W1 25/Mid/75, and ALL of W2+) -> injected keyframe
  // All tiles share the same controls (window + offset) — start/end aren't
  // special-cased. Extend mode: the source video is the anchor, so all inject.
  const offsetLabel = (offset: string) => OFFSET_PRESETS.find(p => p.value === offset)?.label ?? offset
  const offsetPct = (offset: string) => OFFSET_PRESETS.find(p => p.value === offset)?.pct ?? 1
  const lastWindow = Math.max(0, windowInfo.windowCount - 1)
  const hasStart = !!startImage || (!isExtend && !!params.image_start)
  const hasEnd = !!endImage || (!isExtend && !!params.image_end)
  const effectiveSupportsEndFrame = supportsEndFrame || (
    h3StudioWorkflow && (h3AdaptiveConditioning || hasEnd)
  )
  const nativeEndWindow = h3StudioWorkflow ? lastWindow : 0

  const frameRoleFor = (window: number, offset: string): 'start' | 'end' | 'inject' => {
    if (isExtend) return 'inject'
    if (window <= 0 && offset === 'start') return 'start'
    if (effectiveSupportsEndFrame && window === nativeEndWindow && offset === 'end') return 'end'
    return 'inject'
  }
  // Position along the whole timeline (window + within-window fraction) — used
  // for sorting the row and for drag-to-reposition interpolation.
  const frameKey = (window: number, offset: string) => window + offsetPct(offset)

  type FrameTile = { key: string; kind: 'start' | 'end' | 'inject'; injectIndex?: number; preview: string; offset: string; window: number; sortKey: number }

  // Render-only list, SORTED by timeline position so the row always reads left
  // (start) to right (end) and a frame repositions itself when you change it.
  const frameTiles = useMemo<FrameTile[]>(() => {
    const out: FrameTile[] = []
    if (!isExtend) {
      const startPreview = startImage ? URL.createObjectURL(startImage)
        : (params.image_start ? `/api/v1/uploads/${basename(params.image_start as string)}` : null)
      if (startPreview) out.push({ key: 'frame-start', kind: 'start', preview: startPreview, offset: 'start', window: 0, sortKey: 0 })
    }
    injectedFrames.forEach((f, i) => out.push({ key: `frame-inj-${i}`, kind: 'inject', injectIndex: i, preview: f.previewUrl, offset: f.offset, window: f.window, sortKey: frameKey(f.window, f.offset) }))
    if (!isExtend) {
      const endPreview = endImage ? URL.createObjectURL(endImage)
        : (params.image_end ? `/api/v1/uploads/${basename(params.image_end as string)}` : null)
      if (endPreview) out.push({ key: 'frame-end', kind: 'end', preview: endPreview, offset: 'end', window: nativeEndWindow, sortKey: frameKey(nativeEndWindow, 'end') })
    }
    out.sort((a, b) => a.sortKey - b.sortKey)
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startImage, endImage, injectedFrames, params.image_start, params.image_end, isExtend, nativeEndWindow])

  const canAddFrame = isExtend ? supportsInject : (!hasStart || (effectiveSupportsEndFrame && !hasEnd) || supportsInject)

  // "+ Frame": smart default — 1st image = start, 2nd = end (where supported),
  // the rest injected keyframes that walk forward through the windows: in a
  // multi-window clip the next inject lands at the END of the next window
  // (3rd frame -> window 2 End), never on the native last-window end. Single
  // window -> Mid.
  const handleAddFrameSmart = async (file: File) => {
    if (!isExtend && !hasStart) { setStartImage(file); return }
    if (!isExtend && effectiveSupportsEndFrame && !hasEnd) { setEndImage(file); return }
    let w = 0, off = 'middle'
    if (lastWindow >= 1) {
      w = Math.min(injectedFrames.length + 1, lastWindow)
      off = 'end'
    }
    await addInjectFrame(file, null, URL.createObjectURL(file), off, w)
  }

  // Set a frame's (window, offset), re-routing it across pipelines as needed.
  // A move onto an already-taken native start/end slot falls back to an inject
  // just inside that boundary instead of stealing it.
  const setFramePosition = async (tile: FrameTile, newWindow: number, newOffset: string) => {
    // H3's scalar end image is a published-output contract, not a movable KFI
    // tile. The planner reserves it for the final FL2VA segment.
    if (h3StudioWorkflow && tile.kind === 'end' && (
      newWindow !== nativeEndWindow || newOffset !== 'end'
    )) return
    let role = frameRoleFor(newWindow, newOffset)
    if (role === 'start' && hasStart && tile.kind !== 'start') { newOffset = '25%'; role = 'inject' }
    if (role === 'end' && hasEnd && tile.kind !== 'end') { newOffset = '75%'; role = 'inject' }

    if (tile.kind === role) {
      if (role === 'inject' && tile.injectIndex !== undefined) {
        const updated = injectedFrames.map((f, i) =>
          i === tile.injectIndex ? { ...f, window: newWindow, offset: newOffset, position: calcPositionToken(newWindow, newOffset) } : f)
        setInjectedFrames(updated); syncFrameParams(updated)
      }
      return
    }
    // Capture the image, then move it from its old home to its new one.
    let file: File | null = null, path: string | null = null, previewUrl: string | null = tile.preview
    if (tile.kind === 'start') { file = startImage; path = (params.image_start as string) || null }
    else if (tile.kind === 'end') { file = endImage; path = (params.image_end as string) || null }
    else if (tile.injectIndex !== undefined) {
      const f = injectedFrames[tile.injectIndex]; file = f?.file ?? null; path = f?.path ?? null; previewUrl = f?.previewUrl ?? previewUrl
    }
    if (tile.kind === 'start') setStartImage(null)
    else if (tile.kind === 'end') setEndImage(null)
    else if (tile.injectIndex !== undefined) {
      const updated = injectedFrames.filter((_, i) => i !== tile.injectIndex)
      setInjectedFrames(updated); syncFrameParams(updated)
    }
    if (role === 'start') {
      if (file) setStartImage(file); else if (path) { setStartImage(null); setParam('image_start', path) }
    } else if (role === 'end') {
      if (file) setEndImage(file); else if (path) { setEndImage(null); setParam('image_end', path) }
    } else {
      await addInjectFrame(file, path, previewUrl, newOffset, newWindow)
    }
    setSelected(null)
  }

  // Drag-to-reposition: dropping frame A onto tile B gives A a timeline slot
  // just before B (midway between B and its left neighbour), then snaps to the
  // nearest window/offset. Native start/end are guarded by setFramePosition.
  const repositionFrameBefore = async (draggedKey: string, target: FrameTile) => {
    if (draggedKey === target.key) return
    const dragged = frameTiles.find(t => t.key === draggedKey)
    if (!dragged) return
    const rest = frameTiles.filter(t => t.key !== draggedKey)
    const ti = rest.findIndex(t => t.key === target.key)
    const leftKey = ti > 0 ? rest[ti - 1].sortKey : target.sortKey - 1
    const targetKey = (leftKey + target.sortKey) / 2
    const w = Math.min(lastWindow, Math.max(0, Math.floor(targetKey)))
    const pct = Math.min(1, Math.max(0, targetKey - w))
    await setFramePosition(dragged, w, snapToOffsetPreset(pct))
  }

  const removeFrameTile = (tile: FrameTile) => {
    if (tile.kind === 'start') setStartImage(null)
    else if (tile.kind === 'end') setEndImage(null)
    else if (tile.injectIndex !== undefined) handleRemoveFrame(tile.injectIndex)
    if (selected === tile.key) setSelected(null)
  }

  // Strip offset buttons: which to show, and which are a TAKEN native slot.
  const framePresetVisible = (offset: string): boolean => {
    if (isExtend) return supportsInject
    if (offset === 'start') return true
    if (offset === 'end') return effectiveSupportsEndFrame || supportsInject
    return supportsInject
  }
  const framePresetDisabled = (tile: FrameTile, offset: string): boolean => {
    if (isExtend) return false
    if (h3StudioWorkflow && tile.kind === 'end') return offset !== 'end'
    const role = frameRoleFor(tile.window, offset)
    if (role === 'start') return hasStart && tile.kind !== 'start'
    if (role === 'end') return hasEnd && tile.kind !== 'end'
    return false
  }
  const frameRoutingHint = (tile: FrameTile): string => {
    if (isExtend) return 'Injected as a keyframe into the new content.'
    const role = frameRoleFor(tile.window, tile.offset)
    if (role === 'start') return 'Used as the first frame (image-to-video start).'
    if (role === 'end') return h3StudioWorkflow
      ? 'Reserved as the final frame of the final FL2VA segment.'
      : 'Used as the final frame.'
    return 'Injected as a keyframe at this point in the timeline.'
  }

  // ── Audio / control-video handlers ─────────────────────────────────
  const handleAddSoundtrack = async (file: File) => {
    try {
      const result = await api.uploadAudio(file)
      setParam('audio_guide', result.path)
      setAudioGuideFilename(file.name)
      setParam('audio_prompt_type', (soundtrackVal || 'A') + audioFlags)
      const dur = await getMediaDuration(file)
      if (dur && dur > 0) setDurationSeconds(Math.round(dur * 10) / 10)
    } catch (e) {
      console.error('Soundtrack upload failed:', e)
    }
  }
  const removeSoundtrack = () => {
    setParam('audio_guide', undefined)
    setAudioGuideFilename(null)
    if (audioBase.includes('A')) {
      setParam('audio_prompt_type', audioFlags)
    }
    if (selected === 'audio') setSelected(null)
  }
  const handleAddControlVid = async (file: File) => {
    try {
      const result = await api.uploadImage(file)  // full video kept (generic upload)
      setParam('video_guide', result.path)
      setVideoGuideFilename(file.name)
      // Preserve an explicit Pose/Depth/etc. process; otherwise make a
      // dropped LTX control video immediately usable as raw control.
      if (!((params.video_prompt_type as string) || '').includes('V') && rawControlProcess) {
        setParam('video_prompt_type', rawControlProcess)
      }
      // Source audio remains the default, with alternatives exposed in the
      // selected control tile instead of replacing the motion input.
      setParam('audio_prompt_type', `K${audioFlags}`)
    } catch (e) {
      console.error('Control video upload failed:', e)
    }
  }
  const removeControlVid = () => {
    setParam('video_guide', undefined)
    setVideoGuideFilename(null)
    if (audioBase === 'K' || audioBase === '2') {
      setParam('audio_prompt_type', audioFlags)
    }
    if (selected === 'ctrlvid') setSelected(null)
  }
  const handleAddGuideVid = async (file: File) => {
    try {
      const result = await api.uploadImage(file)
      setParam('video_guide', result.path)
      setVideoGuideFilename(file.name)
      // Lock in the guide process letters: defaults are not hydrated into
      // params client-side, so without this a user who never opens
      // Advanced Settings would submit video_prompt_type '' and the model
      // would not receive the control video at all.
      if (!params.video_prompt_type && guideDefault) setParam('video_prompt_type', guideDefault)
      // Real fps of the guide, probed server-side — startGeneration uses
      // it for the seconds→frames conversion on force_fps="control" models.
      setGuideVideoFps(result.fps && result.fps > 0 ? result.fps : null)
      setGuideVideoFrameCount(result.frame_count && result.frame_count > 0 ? result.frame_count : null)
      const dur = await getMediaDuration(file)
      if (dur && dur > 0) setDurationSeconds(Math.round(dur * 10) / 10)
    } catch (e) {
      console.error('Guide video upload failed:', e)
    }
  }
  const removeGuideVid = () => {
    setParam('video_guide', undefined)
    setVideoGuideFilename(null)
    setGuideVideoFps(null)
    setGuideVideoFrameCount(null)
    if (selected === 'guidevid') setSelected(null)
  }
  const syncSemanticVideoRefs = (paths: string[]) => {
    const keys = ['video_guide', 'video_guide2', 'video_guide3'] as const
    keys.forEach((key, index) => setParam(key, paths[index]))
    setParam('video_prompt_type', paths.length > 0 ? `V${'+'.repeat(paths.length - 1)}-` : '')
  }
  const syncSemanticAudioRefs = (paths: string[]) => {
    const keys = ['audio_guide', 'audio_guide2', 'audio_guide3'] as const
    keys.forEach((key, index) => setParam(key, paths[index]))
    setParam('audio_prompt_type', 'ABC'.slice(0, paths.length))
  }
  const handleAddSemanticVideo = async (file: File) => {
    if (!h3TermsAccepted || semanticVideoPaths.length >= H3_REF2VA_LIMITS.videos || semanticMixedCount >= H3_REF2VA_LIMITS.mixed) return
    const duration = await getMediaDuration(file)
    if (duration == null || duration < 2 || duration > 15) {
      window.alert('Each MiniMax H3 reference video must be between 2 and 15 seconds.')
      return
    }
    const knownTotal = semanticVideoPaths.reduce((sum, path) => sum + (semanticRefDurations[path] || 0), 0)
    if (knownTotal + duration > 15.01) {
      window.alert('MiniMax H3 reference videos may total at most 15 seconds.')
      return
    }
    try {
      const result = await api.uploadImage(file)
      setSemanticRefDurations(current => ({ ...current, [result.path]: duration }))
      syncSemanticVideoRefs([...semanticVideoPaths, result.path])
    } catch (e) {
      console.error('Semantic reference video upload failed:', e)
    }
  }
  const removeSemanticVideo = (index: number) => {
    const removed = semanticVideoPaths[index]
    syncSemanticVideoRefs(semanticVideoPaths.filter((_, itemIndex) => itemIndex !== index))
    setSemanticRefDurations(current => {
      const next = { ...current }; delete next[removed]; return next
    })
    if (selected === `semantic-video-${index}`) setSelected(null)
  }
  const handleAddSemanticAudio = async (file: File) => {
    if (!h3TermsAccepted || semanticAudioPaths.length >= H3_REF2VA_LIMITS.audio || semanticMixedCount >= H3_REF2VA_LIMITS.mixed) return
    const duration = await getMediaDuration(file)
    if (duration == null || duration < 2 || duration > 15) {
      window.alert('Each MiniMax H3 reference audio clip must be between 2 and 15 seconds.')
      return
    }
    const knownTotal = semanticAudioPaths.reduce((sum, path) => sum + (semanticRefDurations[path] || 0), 0)
    if (knownTotal + duration > 15.01) {
      window.alert('MiniMax H3 reference audio may total at most 15 seconds.')
      return
    }
    try {
      const result = await api.uploadAudio(file)
      setSemanticRefDurations(current => ({ ...current, [result.path]: duration }))
      syncSemanticAudioRefs([...semanticAudioPaths, result.path])
    } catch (e) {
      console.error('Semantic reference audio upload failed:', e)
    }
  }
  const removeSemanticAudio = (index: number) => {
    const removed = semanticAudioPaths[index]
    syncSemanticAudioRefs(semanticAudioPaths.filter((_, itemIndex) => itemIndex !== index))
    setSemanticRefDurations(current => {
      const next = { ...current }; delete next[removed]; return next
    })
    if (selected === `semantic-audio-${index}`) setSelected(null)
  }
  const toggleAudioFlag = (flag: 'N' | 'V') => {
    const cur = (params.audio_prompt_type as string) || ''
    setParam('audio_prompt_type', cur.includes(flag) ? cur.replace(flag, '') : cur + flag)
  }

  const selectedFrameTile = frameTiles.find(t => t.key === selected) || null

  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Inputs</label>
      {h3StudioWorkflow && (
        <div className="mb-2 rounded-lg border border-amber-500/35 bg-amber-500/5 p-2 space-y-1.5">
          <div className="text-[11px] font-medium text-text-primary">
            MiniMax H3 conditioning · {h3AdaptiveConditioning ? 'Adaptive FL2VA / Ref2VA' : dedicatedRef2VAMode ? 'Pinned Ref2VA' : 'Pinned FL2VA'}
          </div>
          <div className="rounded border border-border bg-bg-secondary px-2 py-1 text-[9px] text-text-secondary">
            Selected checkpoint: <span className="font-medium text-text-primary">{activeModel?.name || params.model_type}</span><br />
            {h3AdaptiveConditioning
              ? 'Frame anchors stay on FL2VA edge segments; semantic references use non-distilled Ref2VA segments. The editable plan shows every model and transition before queueing.'
              : dedicatedRef2VAMode
                ? 'Ref2VA is fixed: semantic references are accepted and frame anchors are incompatible.'
                : 'FL2VA is fixed: frame anchors are accepted and semantic references are incompatible.'}
          </div>
          <p className="text-[9px] leading-relaxed text-text-muted">
            References are character, object, setting, style, video, or audio context — never first/last-frame positions.
            {h3AdaptiveConditioning && ' A supplied end frame is reserved for the final FL2VA segment.'}
            {' '}Ref2VA allows up to 9 images, 3 videos, 3 audio clips, and 12 mixed files.
          </p>
          {!h3AdaptiveConditioning && !dedicatedRef2VAMode && h3HasSemanticInputs && (
            <p className="rounded border border-red-500/35 bg-red-500/10 px-2 py-1 text-[9px] text-red-200">
              Incompatible fixed plan: pinned FL2VA cannot use the attached semantic references. Re-enable automatic model choice or remove them before generating.
            </p>
          )}
          {!h3AdaptiveConditioning && dedicatedRef2VAMode && (hasStart || hasEnd) && (
            <p className="rounded border border-red-500/35 bg-red-500/10 px-2 py-1 text-[9px] text-red-200">
              Incompatible fixed plan: pinned Ref2VA cannot use the attached frame anchors. Re-enable automatic model choice or remove them before generating.
            </p>
          )}
          <div className="flex flex-col items-stretch gap-2 text-[9px] leading-relaxed text-text-secondary sm:flex-row sm:items-start">
            <span className="flex-1">
              {h3TermsAccepted ? 'MiniMax H3 Ref2VA model terms are accepted for this host. ' : `${HOST_TERM_NOTICES.minimax_h3_ref2va.text} Notice v${HOST_TERM_NOTICES.minimax_h3_ref2va.version}. `}
              <a href={HOST_TERM_NOTICES.minimax_h3_ref2va.href} target="_blank" rel="noreferrer" className="text-accent-blue hover:underline">{HOST_TERM_NOTICES.minimax_h3_ref2va.linkLabel}</a>.
            </span>
            {!h3TermsAccepted && hostTerms && (
              <button
                type="button"
                disabled={hostTermsLoading}
                onClick={() => { void acceptHostTerm('minimax_h3_ref2va') }}
                className="w-full shrink-0 rounded border border-accent-blue/50 px-2 py-1 text-accent-blue hover:bg-accent-blue/10 disabled:opacity-50 sm:w-auto sm:px-1.5 sm:py-0.5"
              >
                Accept for this host
              </button>
            )}
          </div>
          {!h3TermsAccepted && hostTermsError && (
            <p className="text-[9px] text-red-300">{hostTermsError}</p>
          )}
          {ref2vaModel?.is_downloaded !== true && (h3AdaptiveConditioning || dedicatedRef2VAMode || h3HasSemanticInputs) && (
            <button type="button" disabled={!h3TermsAccepted || h3DownloadStatus === 'downloading'} onClick={installH3Ref2VA}
              className="w-full rounded-md border border-accent-blue/50 bg-accent-blue/10 px-2 py-1 text-[10px] text-accent-blue hover:bg-accent-blue/20 disabled:opacity-45 disabled:cursor-not-allowed">
              {h3DownloadStatus === 'downloading' ? 'Installing MiniMax H3 Ref2VA…'
                : h3DownloadStatus === 'failed' ? 'Retry correct Ref2VA checkpoint install'
                  : 'Install correct Ref2VA checkpoint'}
            </button>
          )}
        </div>
      )}
      <div className="flex gap-2 overflow-x-auto pb-1">
        {/* Extend-from source video (Extend mode only) — the timeline anchor. */}
        {isExtend && (continueVideo ? (
          <div onClick={() => setSelected(selected === 'extend' ? null : 'extend')}
            className={`relative w-[90px] h-[90px] shrink-0 rounded-xl overflow-hidden border cursor-pointer transition-colors ${selected === 'extend' ? 'border-accent-blue' : 'border-border hover:border-border-light'}`}>
            {continueVideoUrl && <video src={continueVideoUrl} muted className="absolute inset-0 w-full h-full object-cover" />}
            <button onClick={e => { e.stopPropagation(); clearContinueVideo(); if (selected === 'extend') setSelected(null) }}
              className="absolute top-1 right-1 z-10 rounded-full bg-black/45 text-white p-0.5 hover:bg-black/70" aria-label="Remove"><X size={12} /></button>
            <div className="absolute inset-x-0 bottom-0 bg-black/55 px-1.5 py-1">
              <span className="text-[10px] text-white/95">Extend from{continueVideoDuration > 0 ? ` · ${continueVideoDuration.toFixed(1)}s` : ''}</span>
            </div>
          </div>
        ) : (
          <AddTile label="Extend from" icon={<Film size={18} />} onClick={() => pickFile('video/*', handleAddExtendSource)} onDropFile={handleAddExtendSource} dropAccept="video" />
        ))}

        {/* Unified "Frame" tiles — start / end / injected keyframes, one concept,
            sorted by timeline position and draggable to reposition. The per-tile
            position strip below routes each to its pipeline. */}
        {showFrameAnchorControls && frameTiles.map(tile => (
          <div key={tile.key} draggable={!(h3StudioWorkflow && tile.kind === 'end')}
            onDragStart={e => { setFrameDragKey(tile.key); e.dataTransfer.setData('frame-key', tile.key); e.dataTransfer.effectAllowed = 'move' }}
            onDragEnd={() => { setFrameDragKey(null); setFrameDragOverKey(null) }}
            onDragOver={e => { if (frameDragKey && frameDragKey !== tile.key) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setFrameDragOverKey(tile.key) } }}
            onDragLeave={() => setFrameDragOverKey(prev => (prev === tile.key ? null : prev))}
            onDrop={e => {
              const dk = e.dataTransfer.getData('frame-key')
              if (!dk) return
              e.preventDefault(); e.stopPropagation(); setFrameDragOverKey(null); setFrameDragKey(null)
              if (dk !== tile.key) void repositionFrameBefore(dk, tile)
            }}
            onClick={() => setSelected(selected === tile.key ? null : tile.key)}
            className={`relative w-[90px] h-[90px] shrink-0 rounded-xl overflow-hidden border cursor-grab active:cursor-grabbing transition-colors ${
              frameDragOverKey === tile.key ? 'border-accent-blue border-2'
                : selected === tile.key ? 'border-accent-blue' : 'border-border hover:border-border-light'
            }`}>
            <img src={tile.preview} alt={`Frame ${offsetLabel(tile.offset)}`} className="absolute inset-0 w-full h-full object-cover pointer-events-none" />
            <button onClick={e => { e.stopPropagation(); removeFrameTile(tile) }}
              className="absolute top-1 right-1 z-10 rounded-full bg-black/45 text-white p-0.5 hover:bg-black/70 transition-colors" aria-label="Remove"><X size={12} /></button>
            <div className="absolute inset-x-0 bottom-0 bg-black/55 px-1.5 py-1">
              <span className="text-[10px] text-white/95">{windowInfo.windowCount > 1 ? `W${tile.window + 1} · ` : ''}{offsetLabel(tile.offset)}</span>
            </div>
          </div>
        ))}
        {canAttachFrameAnchors && canAddFrame && (
          <AddTile label={frameUploading ? 'Uploading…' : 'Frame'} icon={<Plus size={18} />}
            onClick={() => pickImage(handleAddFrameSmart)} onDropFile={handleAddFrameSmart} dropAccept="image" />
        )}

        {/* H3 Ref2VA media are semantic context, never timeline controls. */}
        {semanticReferenceMode && (h3TermsAccepted || h3HasSemanticInputs) && semanticVideoPaths.map((path, index) => (
          <Tile key={`semantic-video-${path}`} role={`Video ref ${index + 1}`} filledIcon={<Film size={20} />} filledLabel={basename(path)}
            imgSrc={null} selected={selected === `semantic-video-${index}`} onClear={() => removeSemanticVideo(index)}
            onSelect={() => setSelected(selected === `semantic-video-${index}` ? null : `semantic-video-${index}`)} />
        ))}
        {semanticReferenceMode && canAttachSemanticReferences && h3TermsAccepted && semanticVideoPaths.length < H3_REF2VA_LIMITS.videos && semanticMixedCount < H3_REF2VA_LIMITS.mixed && (
          <AddTile label="Video ref" icon={<Film size={18} />} onClick={() => pickFile('.mp4,.webm,.mkv,.mov', handleAddSemanticVideo)} onDropFile={handleAddSemanticVideo} dropAccept="video" />
        )}
        {semanticReferenceMode && (h3TermsAccepted || h3HasSemanticInputs) && semanticAudioPaths.map((path, index) => (
          <Tile key={`semantic-audio-${path}`} role={`Audio ref ${index + 1}`} filledIcon={<Music size={20} />} filledLabel={basename(path)}
            imgSrc={null} selected={selected === `semantic-audio-${index}`} onClear={() => removeSemanticAudio(index)}
            onSelect={() => setSelected(selected === `semantic-audio-${index}` ? null : `semantic-audio-${index}`)} />
        ))}
        {semanticReferenceMode && canAttachSemanticReferences && h3TermsAccepted && semanticAudioPaths.length < H3_REF2VA_LIMITS.audio && semanticMixedCount < H3_REF2VA_LIMITS.mixed && (
          <AddTile label="Audio ref" icon={<Music size={18} />} onClick={() => pickFile('.wav,.mp3,.flac,.ogg,.m4a', handleAddSemanticAudio)} onDropFile={handleAddSemanticAudio} dropAccept="audio" />
        )}

        {/* Soundtrack (audio) */}
        {!semanticReferenceMode && (hasSoundtrack ? (
          <Tile role="Soundtrack" filledIcon={<Music size={20} />} filledLabel={soundtrackName ?? undefined}
            imgSrc={null} selected={selected === 'audio'} onClear={removeSoundtrack}
            onSelect={() => setSelected(selected === 'audio' ? null : 'audio')} />
        ) : supportsSoundtrack && (
          <AddTile label="Soundtrack" icon={<Music size={18} />} onClick={() => pickFile('.wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.mkv,.webm', handleAddSoundtrack)} onDropFile={handleAddSoundtrack} />
        ))}

        {/* Control video */}
        {!semanticReferenceMode && (hasControlVid ? (
          <Tile role="Control video" filledIcon={<Film size={20} />} filledLabel={controlVidName ?? undefined}
            imgSrc={null} selected={selected === 'ctrlvid'} onClear={removeControlVid}
            onSelect={() => setSelected(selected === 'ctrlvid' ? null : 'ctrlvid')} />
        ) : supportsControlVid && (
          <AddTile label="Control video" icon={<Film size={18} />} onClick={() => pickFile('.mp4,.webm,.mkv,.mov', handleAddControlVid)} onDropFile={handleAddControlVid} dropAccept="video" />
        ))}

        {/* Guide video (motion source) — guide_custom_choices models (SCAIL-2 etc.) */}
        {!semanticReferenceMode && (hasGuideVid ? (
          <Tile role="Control video" filledIcon={<Film size={20} />} filledLabel={controlVidName ?? undefined}
            imgSrc={null} selected={selected === 'guidevid'} onClear={removeGuideVid}
            onSelect={() => setSelected(selected === 'guidevid' ? null : 'guidevid')} />
        ) : supportsGuideVid && (
          <AddTile label="Control video" icon={<Film size={18} />} onClick={() => pickFile('.mp4,.webm,.mkv,.mov', handleAddGuideVid)} onDropFile={handleAddGuideVid} dropAccept="video" />
        ))}

        {/* Voice reference (ID-LoRA) — keeps the speaker's voice consistent. */}
        {!semanticReferenceMode && voiceRefEnabled && (directorVoiceRef ? (
          <Tile role="Voice ref" filledIcon={<Mic size={20} />} filledLabel={directorVoiceRef.name}
            imgSrc={null} selected={selected === 'voiceref'}
            onClear={() => { setDirectorVoiceRef(null); if (selected === 'voiceref') setSelected(null) }}
            onSelect={() => setSelected(selected === 'voiceref' ? null : 'voiceref')} />
        ) : (
          <AddTile label="Voice ref" icon={<Mic size={18} />} onClick={() => pickFile('.wav,.mp3,.flac,.ogg,.m4a', setDirectorVoiceRef)} onDropFile={setDirectorVoiceRef} dropAccept="audio" />
        ))}

        {/* Reference images (ordered; first = main subject/landscape). Drag to reorder. */}
        {supportsRefs && restoredSemanticImagePaths.map((path, i) => (
          <Tile key={`restored-ref-${path}`} role={`Reference ${i + 1}`} filledLabel={basename(path)}
            imgSrc={api.getUploadUrl(basename(path))} selected={selected === `restored-ref-${i}`}
            onClear={() => {
              const remaining = restoredSemanticImagePaths.filter((_, index) => index !== i)
              setParam('image_refs', remaining.length > 0 ? remaining : undefined)
              if (remaining.length === 0) setImageRefType('')
              if (selected === `restored-ref-${i}`) setSelected(null)
            }}
            onSelect={() => setSelected(selected === `restored-ref-${i}` ? null : `restored-ref-${i}`)} />
        ))}
        {supportsRefs && imageRefs.map((file, i) => (
          <div key={`ref-${i}-${file.name}`} draggable
            onDragStart={e => { e.dataTransfer.setData('ref-index', String(i)); e.dataTransfer.effectAllowed = 'move' }}
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverIndex(i) }}
            onDragLeave={() => setDragOverIndex(null)}
            onDrop={e => {
              e.preventDefault(); e.stopPropagation(); setDragOverIndex(null)
              const from = parseInt(e.dataTransfer.getData('ref-index'), 10)
              if (!isNaN(from) && from !== i) reorderImageRefs(from, i)
            }}
            onClick={() => setSelected(selected === `ref-${i}` ? null : `ref-${i}`)}
            className={`relative w-[90px] h-[90px] shrink-0 rounded-xl overflow-hidden border cursor-grab active:cursor-grabbing transition-colors ${
              dragOverIndex === i ? 'border-accent-blue border-2' : selected === `ref-${i}` ? 'border-accent-blue' : 'border-border hover:border-border-light'
            }`}>
            <img src={URL.createObjectURL(file)} alt={`Ref ${i + 1}`} className="absolute inset-0 w-full h-full object-cover pointer-events-none" />
            <span className="absolute top-1 left-1 z-10 rounded bg-black/55 text-white text-[9px] px-1">{i + 1}</span>
            <button onClick={e => { e.stopPropagation(); removeImageRef(i); if (selected === `ref-${i}`) setSelected(null) }}
              className="absolute top-1 right-1 z-10 rounded-full bg-black/45 text-white p-0.5 hover:bg-black/70" aria-label="Remove"><X size={12} /></button>
            <div className="absolute inset-x-0 bottom-0 bg-black/55 px-1.5 py-1">
              <span className="text-[10px] text-white/95">{i === 0 && hasLandscapeMode && imageRefType === 'KI' ? 'Main ref' : 'Reference'}</span>
            </div>
          </div>
        ))}
        {supportsRefs && canAddRef && (!semanticReferenceMode || (canAttachSemanticReferences && h3TermsAccepted)) && <AddTile label="Reference" icon={<Plus size={18} />} onClick={pickReferences} onDropFile={file => {
          if (canAddRef && (!semanticReferenceMode || semanticMixedCount < H3_REF2VA_LIMITS.mixed)) addImageRef(file)
        }} dropAccept="image" />}
      </div>
      {semanticReferenceMode && h3TermsAccepted && (
        <p className={`mt-1 text-[9px] ${semanticAudioPaths.length > semanticImageCount + semanticVideoPaths.length ? 'text-amber-400' : 'text-text-muted'}`}>
          Semantic context: {semanticImageCount}/9 images · {semanticVideoPaths.length}/3 videos · {semanticAudioPaths.length}/3 audio · {semanticMixedCount}/12 mixed.
          {' '}Video {semanticVideoDurationTotal.toFixed(1)}/15s · audio {semanticAudioDurationTotal.toFixed(1)}/15s.
          {' '}Audio references require at least the same number of visual references. Each video/audio clip must be 2–15s.
        </p>
      )}

      {/* Option strip — Frame: position picker (routes start / end / inject
          invisibly) + role-specific strength. */}
      {selectedFrameTile && (
        <Strip>
          <div className="flex items-center gap-1.5">
            {windowInfo.windowCount > 1 && (
              <>
                <span className="text-[10px] text-text-muted shrink-0">Window</span>
                <select value={selectedFrameTile.window}
                  disabled={h3StudioWorkflow && selectedFrameTile.kind === 'end'}
                  onChange={e => setFramePosition(selectedFrameTile, parseInt(e.target.value), selectedFrameTile.offset)}
                  className="shrink-0 bg-bg-secondary border border-border rounded px-1 py-0.5 text-[11px] text-text-primary focus:outline-none focus:border-accent-blue disabled:opacity-60">
                  {Array.from({ length: windowInfo.windowCount }, (_, wi) => <option key={wi} value={wi}>{wi + 1}</option>)}
                </select>
                <span className="text-[10px] text-text-muted shrink-0">at</span>
              </>
            )}
            <div className="flex gap-0.5 flex-1">
              {OFFSET_PRESETS.filter(p => framePresetVisible(p.value)).map(preset => {
                const disabled = framePresetDisabled(selectedFrameTile, preset.value)
                const active = selectedFrameTile.offset === preset.value
                return (
                  <button key={preset.value} disabled={disabled}
                    onClick={() => setFramePosition(selectedFrameTile, selectedFrameTile.window, preset.value)}
                    className={`flex-1 text-[10px] py-0.5 rounded transition-colors ${
                      active ? 'bg-accent-blue text-white'
                        : disabled ? 'bg-bg-secondary text-text-muted cursor-not-allowed'
                        : 'bg-bg-secondary text-text-muted hover:text-text-primary hover:bg-bg-hover'
                    }`}>{preset.label}</button>
                )
              })}
            </div>
          </div>
          {selectedFrameTile.kind === 'inject' ? (
            <>
              <Row label="Injection strength" value={(params.injection_strength ?? 1.0).toFixed(2)} />
              <input type="range" min={0} max={1} step={0.05} value={params.injection_strength ?? 1.0}
                onChange={e => setParam('injection_strength', parseFloat(e.target.value))} className="w-full h-1 accent-accent-blue" />
            </>
          ) : strengthLabel ? (
            <>
              <Row label={strengthLabel} value={inputVideoStrength.toFixed(2)} />
              <input type="range" min={0} max={1} step={0.01} value={inputVideoStrength}
                onChange={e => setParam('input_video_strength', parseFloat(e.target.value))} className="w-full h-1 accent-accent-blue" />
            </>
          ) : null}
          <p className="text-[9px] text-text-muted">{frameRoutingHint(selectedFrameTile)}</p>
        </Strip>
      )}

      {/* Option strip — extend source: source video strength */}
      {selected === 'extend' && continueVideo && (
        <Strip>
          <Row label="Source video strength" value={inputVideoStrength.toFixed(2)} />
          <input type="range" min={0} max={1} step={0.05} value={inputVideoStrength}
            onChange={e => setParam('input_video_strength', parseFloat(e.target.value))} className="w-full h-1 accent-accent-blue" />
          <p className="text-[9px] text-text-muted">1.0 = seamless continuation; lower gives more creative freedom. New content is appended after the source.</p>
        </Strip>
      )}

      {/* Option strip — soundtrack: audio strength + processing flags */}
      {selected === 'audio' && hasSoundtrack && (
        <Strip>
          <Row label={modelOptions?.audio_scale_name || 'Audio strength'} value={(((params as unknown as Record<string, unknown>).modality_scale as number) ?? 1.0).toFixed(1)} />
          <input type="range" min={0.1} max={3.0} step={0.1} value={((params as unknown as Record<string, unknown>).modality_scale as number) ?? 1.0}
            onChange={e => setParam('modality_scale' as keyof typeof params, parseFloat(e.target.value) as never)} className="w-full h-1 accent-accent-blue" />
          <label className="flex items-center gap-2 cursor-pointer pt-1">
            <input type="checkbox" checked={audioPT.includes('N')} onChange={() => toggleAudioFlag('N')} className="accent-accent-blue" />
            <span className="text-[10px] text-text-secondary">Normalize audio volume</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={audioPT.includes('V')} onChange={() => toggleAudioFlag('V')} className="accent-accent-blue" />
            <span className="text-[10px] text-text-secondary">Remove background music</span>
          </label>
        </Strip>
      )}

      {/* Option strip — control-video audio stays independent from motion. */}
      {selected === 'ctrlvid' && hasControlVid && (
        <Strip>
          <label className="text-[10px] text-text-muted uppercase tracking-wider">
            Audio behavior
          </label>
          <select
            value={
              audioBase === 'K' || audioBase === '2'
                ? audioBase
                : audioBase.includes('A') && hasSoundtrack
                  ? 'A'
                  : ''
            }
            onChange={event => {
              setParam('audio_prompt_type', `${event.target.value}${audioFlags}`)
            }}
            className="w-full bg-bg-secondary border border-border rounded-lg px-2 py-1.5 text-[11px] text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="K">Use control video's audio</option>
            <option value="">Generate soundtrack from text prompt</option>
            {audioVals.includes('2') && (
              <option value="2">Generate new audio from control video</option>
            )}
            {hasSoundtrack && soundtrackVal && (
              <option value="A">Use uploaded soundtrack</option>
            )}
          </select>
          <p className="text-[9px] text-text-muted">
            The control video remains attached as the motion guide in every mode.
          </p>
        </Strip>
      )}

      {/* Option strip — voice reference: identity guidance scale */}
      {selected === 'voiceref' && directorVoiceRef && (
        <Strip>
          <Row label="Identity scale" value={String(identityScale)} />
          <input type="range" min={0} max={10} step={0.5} value={identityScale}
            onChange={e => setIdentityScale(parseFloat(e.target.value))} className="w-full h-1 accent-accent-blue" />
          <p className="text-[9px] text-text-muted">~5s voice sample. With an active ID-LoRA, keeps the speaker's voice consistent across clips.</p>
        </Strip>
      )}

      {/* Option strip — references: focus mode + background removal */}
      {selected?.startsWith('ref-') && imageRefs.length > 0 && (
        <Strip>
          {hasLandscapeMode && hasPeopleMode && (
            <div className="flex bg-bg-tertiary rounded-lg p-0.5 border border-border">
              <button onClick={() => setImageRefType('KI')}
                className={`flex-1 text-[10px] py-1 rounded-md transition-all ${imageRefType === 'KI' ? 'bg-bg-active text-text-primary' : 'text-text-secondary hover:text-text-primary'}`}>Subject / Landscape</button>
              <button onClick={() => setImageRefType('I')}
                className={`flex-1 text-[10px] py-1 rounded-md transition-all ${imageRefType === 'I' ? 'bg-bg-active text-text-primary' : 'text-text-secondary hover:text-text-primary'}`}>People / Objects</button>
            </div>
          )}
          {hasLandscapeMode && imageRefType === 'KI' && (
            <p className="text-[9px] text-text-muted">First image is the main subject/landscape; the rest are people/objects. Drag tiles to reorder.</p>
          )}
          {refBgLabel && (
            <label className="flex items-start gap-2 cursor-pointer">
              <input type="checkbox" checked={removeBackgroundRefs} onChange={e => setRemoveBackgroundRefs(e.target.checked)} className="mt-0.5 accent-accent-blue shrink-0" />
              <span className="text-[10px] text-text-secondary leading-tight">{refBgLabel}</span>
            </label>
          )}
        </Strip>
      )}
    </div>
  )
}

function Strip({ children }: { children: React.ReactNode }) {
  return <div className="mt-2 px-1 space-y-1.5">{children}</div>
}
function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <label className="text-[10px] text-text-muted">{label}</label>
      <span className="text-[10px] text-text-muted tabular-nums">{value}</span>
    </div>
  )
}

function AddTile({ label, icon, onClick, onDropFile, dropAccept }: {
  label: string; icon?: React.ReactNode; onClick: () => void
  onDropFile?: (f: File) => void; dropAccept?: 'image' | 'audio' | 'video'
}) {
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (!f || !onDropFile) return
    if (dropAccept && !f.type.startsWith(`${dropAccept}/`)) return
    onDropFile(f)
  }
  return (
    <button onClick={onClick}
      onDrop={onDropFile ? handleDrop : undefined}
      onDragOver={onDropFile ? (e => e.preventDefault()) : undefined}
      className="w-[90px] h-[90px] shrink-0 rounded-xl border border-dashed border-border hover:border-accent-blue flex flex-col items-center justify-center gap-1 text-text-muted hover:text-text-primary transition-colors">
      {icon ?? <Plus size={18} />}
      <span className="text-[10px] text-center px-1">{label}</span>
    </button>
  )
}

function Tile({ role, imgSrc, icon, badge, selected, filledIcon, filledLabel, onPick, onClear, onSelect, onDropFile }: {
  role: string
  imgSrc: string | null
  icon?: React.ReactNode
  badge?: number
  selected: boolean
  filledIcon?: React.ReactNode   // non-image filled tiles (audio/video)
  filledLabel?: string
  onPick?: () => void
  onClear: () => void
  onSelect: () => void
  onDropFile?: (f: File) => void
}) {
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f && f.type.startsWith('image/') && onDropFile) onDropFile(f)
  }
  const filled = !!imgSrc || !!filledIcon
  return (
    <div onDrop={handleDrop} onDragOver={e => e.preventDefault()}
      onClick={() => (filled ? onSelect() : onPick?.())}
      className={`relative w-[90px] h-[90px] shrink-0 rounded-xl overflow-hidden cursor-pointer border transition-colors ${
        selected ? 'border-accent-blue' : filled ? 'border-border hover:border-border-light' : 'border-dashed border-border hover:border-border-light'
      }`}>
      {filled ? (
        <>
          {imgSrc ? (
            <img src={imgSrc} alt={role} className="absolute inset-0 w-full h-full object-cover" />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-bg-tertiary/50 text-text-secondary">
              {filledIcon}
              {filledLabel && <span className="text-[8px] text-text-muted px-1 truncate max-w-full">{filledLabel}</span>}
            </div>
          )}
          {badge !== undefined && <span className="absolute top-1 left-1 z-10 rounded bg-black/55 text-white text-[9px] px-1">{badge}</span>}
          <button onClick={e => { e.stopPropagation(); onClear() }}
            className="absolute top-1 right-1 z-10 rounded-full bg-black/45 text-white p-0.5 hover:bg-black/70 transition-colors" aria-label="Remove">
            <X size={12} />
          </button>
          <div className="absolute inset-x-0 bottom-0 bg-black/55 px-1.5 py-1">
            <span className="text-[10px] text-white/95">{role}</span>
          </div>
        </>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-bg-tertiary/40 text-text-muted">
          {icon ?? <Upload size={15} />}
          <span className="text-[10px] text-center px-1">{role}</span>
        </div>
      )}
    </div>
  )
}
