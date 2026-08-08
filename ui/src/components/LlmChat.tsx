import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, ImagePlus, Loader2, Send, Trash2, UserRound, X } from 'lucide-react'
import * as api from '../api/client'
import { useStore } from '../stores/useStore'
import type { LlmChatMessage, LlmModelOption, LlmPromptGuideOption } from '../types'

const STORAGE_PREFIX = 'maestro:llm-chat:'
const MAX_HISTORY_MESSAGES = 62
const MAX_IMAGE_ATTACHMENTS = 4
const MAX_IMAGE_BYTES = 32 * 1024 * 1024
const IMAGE_EXTENSION = /\.(avif|bmp|gif|jpe?g|png|webp)$/i

function storageKey(workspace: string, projectInstance: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(workspace)}:${projectInstance}`
}

function restoreMessages(workspace: string, projectInstance: string): LlmChatMessage[] {
  if (!workspace || !projectInstance) return []
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey(workspace, projectInstance)) || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((item): item is LlmChatMessage => (
        item && (item.role === 'user' || item.role === 'assistant') && typeof item.content === 'string'
      ))
      .map(item => ({
        role: item.role,
        content: item.content,
        attachments: Array.isArray(item.attachments)
          ? item.attachments
            .filter(attachment => (
              attachment?.kind === 'image'
              && typeof attachment.name === 'string'
              && attachment.name.length <= 255
            ))
            .map(attachment => ({ kind: 'image' as const, name: attachment.name }))
          : undefined,
      }))
  } catch {
    return []
  }
}

function persistMessages(
  workspace: string,
  projectInstance: string,
  messages: LlmChatMessage[],
): void {
  if (!workspace || !projectInstance) return
  try {
    localStorage.setItem(storageKey(workspace, projectInstance), JSON.stringify(messages))
  } catch { /* Conversation remains available for this browser session. */ }
}

function preferredModelId(
  models: LlmModelOption[],
  currentId: string,
  selectionTouched: boolean,
): string {
  const currentStillExists = models.some(model => model.id === currentId)
  if (selectionTouched && currentStillExists) return currentId
  return models.find(model => model.current)?.id
    || models.find(model => model.configured)?.id
    || models.find(model => model.label.toLowerCase().includes('recommended'))?.id
    || models[0]?.id
    || ''
}

function speedMeta(model: LlmModelOption): string {
  const speed = model.speed
  if (!speed) return 'speed estimate unavailable'
  const measured = speed.source === 'measured'
  const rate = (value: number | null, label: string) => {
    if (!value || value <= 0) return null
    const displayed = measured ? value.toFixed(1) : Math.round(value).toString()
    return `${measured ? '' : '≈'}${displayed} ${label} tok/s`
  }
  const rates = [
    rate(speed.prompt_tokens_per_second, 'prompt'),
    rate(speed.generation_tokens_per_second, 'generation'),
  ].filter(Boolean)
  if (!rates.length) return 'speed estimate unavailable'
  const basis = measured
    ? `measured${speed.sample_count > 1 ? ` · ${speed.sample_count} samples` : ''}`
    : `${speed.confidence} confidence ${speed.source}`
  return `${rates.join(' · ')} · ${basis}`
}

function modelPickerSpeedMeta(model: LlmModelOption): string {
  const speed = model.speed
  const format = (value: number | null | undefined) => {
    if (!value || value <= 0) return '—'
    return speed?.source === 'measured' ? value.toFixed(1) : `≈${Math.round(value)}`
  }
  return `P ${format(speed?.prompt_tokens_per_second)} · G ${format(speed?.generation_tokens_per_second)} TPS`
}

function modelPickerLabel(model: LlmModelOption): string {
  return `${modelPickerSpeedMeta(model)} · ${model.label}`
}

function providerDisplayName(provider?: string): string {
  switch (provider) {
    case 'openai': return 'OpenAI external provider'
    case 'anthropic': return 'Anthropic external provider'
    case 'remote': return 'configured external provider'
    case 'local': return 'local provider on this machine'
    default: return 'provider for the selected model'
  }
}

function matchingVideoGuideId(
  modelType: string,
  guides: LlmPromptGuideOption[],
): string {
  const normalized = modelType.trim().toLowerCase()
  if (!normalized) return ''
  let best: { id: string; prefixLength: number } | null = null
  for (const guide of guides) {
    if (guide.target_mode !== 'video') continue
    for (const rawPrefix of guide.target_model_prefixes || []) {
      const prefix = rawPrefix.toLowerCase()
      if (
        normalized.startsWith(prefix)
        && (!best || prefix.length > best.prefixLength)
      ) {
        best = { id: guide.id, prefixLength: prefix.length }
      }
    }
  }
  return best?.id || ''
}

function modelMeta(model: LlmModelOption): string {
  const runtime = model.current || model.loaded
    ? `${model.effective_device?.toUpperCase() || 'loaded'} · active`
    : model.loading
      ? `${model.loading_phase || 'loading'}…`
      : model.downloaded === false
        ? 'download needed in shared host cache'
        : 'installed'
  const vision = model.current && model.vision_capable && model.vision_available === false
    ? 'vision unavailable in active runtime'
    : model.native_vision
    ? 'native vision'
    : model.vision_capable === true
      ? model.projector_available === false ? 'vision · projector download needed in shared host cache' : 'vision ready'
    : model.vision_capable === false ? 'text only' : null
  const profile = model.runtime_profile
  const performance = profile
    ? [
      profile.gpu_layers === -1 ? 'full GPU offload' : null,
      profile.gpu_layers === 0 ? 'CPU layers' : null,
      profile.threads ? `${profile.threads} threads` : null,
      profile.flash_attention === true || profile.flash_attention === 'on'
        ? 'flash attention'
        : profile.flash_attention === 'auto' ? 'flash attention auto' : null,
    ].filter(Boolean).join(', ')
    : null
  return [
    model.size_hint,
    model.source,
    model.backend,
    runtime,
    vision,
    performance,
    speedMeta(model),
  ]
    .filter(Boolean)
    .join(' · ')
}

function downloadProgress(model?: LlmModelOption): string | null {
  const progress = model?.download
  if (!model?.loading || !progress) return null
  const total = progress.total_bytes || 0
  const downloaded = progress.downloaded_bytes || 0
  if (total > 0) return `${Math.min(100, Math.round((downloaded / total) * 100))}%`
  return downloaded > 0 ? `${(downloaded / 1_000_000).toFixed(0)} MB` : null
}

interface PendingChatRequest {
  workspace: string
  projectInstance: string
  controller: AbortController
  modelId: string
  customModel: string
  effectiveModelId: string
  useGuide: boolean
  guideId: string
  requestGuideId: string
  guideTargetOverridden: boolean
  draft: string
  images: File[]
  retainedHistory: LlmChatMessage[]
  uploadedRefs: string[]
  chatSubmitted: boolean
}

function cleanupUnsubmittedUploads(pending: PendingChatRequest): void {
  if (pending.chatSubmitted || pending.uploadedRefs.length === 0) return
  const filenames = pending.uploadedRefs.splice(0)
  for (const filename of filenames) {
    void api.deleteLlmChatImage(pending.workspace, filename).catch(() => {
      // The server's one-use marker and TTL pruning remain the fail-safe.
    })
  }
}

export function LlmChat() {
  const activeWorkspace = useStore(state => state.activeWorkspace)
  const accessContext = useStore(state => state.accessContext)
  const selectedVideoModel = useStore(state => state.selectedModelPerMode.video || '')
  const [messages, setMessages] = useState<LlmChatMessage[]>([])
  const [projectInstance, setProjectInstance] = useState('')
  const [models, setModels] = useState<LlmModelOption[]>([])
  const [guides, setGuides] = useState<LlmPromptGuideOption[]>([])
  const [modelId, setModelId] = useState('')
  const [customModel, setCustomModel] = useState('')
  const [useGuide, setUseGuide] = useState(false)
  const [guideId, setGuideId] = useState('')
  const [draft, setDraft] = useState('')
  const [selectedImages, setSelectedImages] = useState<File[]>([])
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [sending, setSending] = useState(false)
  const [uploadingImages, setUploadingImages] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const requestRef = useRef<PendingChatRequest | null>(null)
  const projectInstanceRef = useRef('')
  const guidesRef = useRef<LlmPromptGuideOption[]>([])
  const modelSelectionTouched = useRef(false)
  const guideTargetOverridden = useRef(false)
  const canUseCustomModel = accessContext?.custom_model_sources === true

  const adoptProjectInstance = useCallback((nextProjectInstance: string) => {
    if (
      !nextProjectInstance
      || nextProjectInstance === projectInstanceRef.current
    ) return false
    const pending = requestRef.current
    if (pending) {
      pending.controller.abort()
      persistMessages(
        pending.workspace,
        pending.projectInstance,
        pending.retainedHistory,
      )
      cleanupUnsubmittedUploads(pending)
      requestRef.current = null
    }
    projectInstanceRef.current = nextProjectInstance
    setProjectInstance(nextProjectInstance)
    setMessages(restoreMessages(activeWorkspace, nextProjectInstance))
    setDraft('')
    setUseGuide(false)
    setSelectedImages([])
    setSending(false)
    setUploadingImages(false)
    setError(null)
    guideTargetOverridden.current = false
    setGuideId(matchingVideoGuideId(
      useStore.getState().selectedModelPerMode.video || '',
      guidesRef.current,
    ))
    if (fileInputRef.current) fileInputRef.current.value = ''
    // The old name-only key could belong to an earlier project that was
    // deleted and recreated. Never import it into this project instance.
    try { localStorage.removeItem(`${STORAGE_PREFIX}${encodeURIComponent(activeWorkspace)}`) } catch { /* private mode */ }
    return true
  }, [activeWorkspace])

  useEffect(() => {
    setSending(false)
    setUploadingImages(false)
    setSelectedImages([])
    setDraft('')
    setUseGuide(false)
    guideTargetOverridden.current = false
    setGuideId('')
    if (fileInputRef.current) fileInputRef.current.value = ''
    projectInstanceRef.current = ''
    setProjectInstance('')
    setMessages([])
    setError(null)
    return () => {
      const pending = requestRef.current
      if (pending?.workspace !== activeWorkspace) return
      pending.controller.abort()
      persistMessages(
        pending.workspace,
        pending.projectInstance,
        pending.retainedHistory,
      )
      cleanupUnsubmittedUploads(pending)
      requestRef.current = null
    }
  }, [activeWorkspace])

  useEffect(() => {
    let cancelled = false
    setLoadingCatalog(true)
    const refresh = (initial = false) => {
      api.fetchLlmModels(activeWorkspace)
        .then(data => {
        if (cancelled) return
        setModels(data.models)
        guidesRef.current = data.guides
        setGuides(data.guides)
        setModelId(current => preferredModelId(
          data.models,
          current,
          modelSelectionTouched.current,
        ))
        if (data.project_instance) adoptProjectInstance(data.project_instance)
      })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (initial && !cancelled) setLoadingCatalog(false) })
    }
    refresh(true)
    // A model already loading can become the truthful current model shortly
    // after the first catalog response. Refresh twice without permanent idle
    // polling so an untouched selector follows that loaded model.
    const timers = [
      window.setTimeout(() => refresh(), 1000),
      window.setTimeout(() => refresh(), 3000),
    ]
    return () => {
      cancelled = true
      timers.forEach(timer => window.clearTimeout(timer))
    }
  }, [activeWorkspace, adoptProjectInstance])

  useEffect(() => {
    if (guideTargetOverridden.current) return
    setGuideId(matchingVideoGuideId(selectedVideoModel, guides))
  }, [guides, selectedVideoModel])

  useEffect(() => {
    if (!sending) return
    let cancelled = false
    const refresh = () => {
      api.fetchLlmModels(activeWorkspace)
        .then(data => {
          if (cancelled) return
          guidesRef.current = data.guides
          setGuides(data.guides)
          if (
            data.project_instance
            && adoptProjectInstance(data.project_instance)
          ) return
          setModels(data.models)
          setModelId(current => preferredModelId(
            data.models,
            current,
            modelSelectionTouched.current,
          ))
        })
        .catch(() => { /* The active request will surface actionable errors. */ })
    }
    refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeWorkspace, adoptProjectInstance, sending])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  const effectiveModelId = canUseCustomModel && customModel.trim() ? customModel.trim() : modelId
  const selectedModel = useMemo(
    () => models.find(model => model.id === effectiveModelId),
    [effectiveModelId, models],
  )
  const selectedProvider = (
    canUseCustomModel && customModel.trim()
      ? 'local'
      : selectedModel?.provider
  )
  const modelAcceptsImages = !selectedModel
    || (selectedModel.current
      ? selectedModel.vision_available !== false && selectedModel.vision_capable !== false
      : selectedModel.vision_capable !== false)
  const videoGuides = useMemo(
    () => guides.filter(guide => guide.target_mode === 'video'),
    [guides],
  )
  const selectedGuide = videoGuides.find(guide => guide.id === guideId)
  const canonicalGuideId = selectedGuide?.id || ''
  const unavailableReason = !activeWorkspace
    ? 'Select or create a project before chatting.'
    : !projectInstance
      ? 'Opening the project conversation…'
    : !effectiveModelId
      ? 'Choose an LLM first.'
      : useGuide && !canonicalGuideId
        ? 'Choose a video prompting target for this message.'
      : selectedImages.length > 0 && !modelAcceptsImages
        ? 'The selected LLM is text only. Remove the image attachment or choose a vision model.'
      : null

  const clearConversation = () => {
    const pending = requestRef.current
    pending?.controller.abort()
    if (pending) cleanupUnsubmittedUploads(pending)
    requestRef.current = null
    setSending(false)
    setUploadingImages(false)
    setMessages([])
    setSelectedImages([])
    guideTargetOverridden.current = false
    setGuideId(matchingVideoGuideId(selectedVideoModel, guides))
    if (fileInputRef.current) fileInputRef.current.value = ''
    setError(null)
    if (activeWorkspace && projectInstance) {
      localStorage.removeItem(storageKey(activeWorkspace, projectInstance))
    }
  }

  const selectImages = (files: FileList | null) => {
    if (!files?.length) return
    const candidates = Array.from(files)
    if (selectedImages.length + candidates.length > MAX_IMAGE_ATTACHMENTS) {
      setError(`Attach at most ${MAX_IMAGE_ATTACHMENTS} images per message.`)
      return
    }
    const invalid = candidates.find(file => (
      !(file.type.startsWith('image/') || IMAGE_EXTENSION.test(file.name))
      || file.size > MAX_IMAGE_BYTES
    ))
    if (invalid) {
      setError(`“${invalid.name}” must be a supported image no larger than 32 MB.`)
      return
    }
    setSelectedImages(current => [...current, ...candidates])
    setError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const send = async () => {
    const content = draft.trim()
    if (!content || sending || unavailableReason) return
    const requestWorkspace = activeWorkspace
    const requestProjectInstance = projectInstance
    const retainedHistory = messages.slice(-MAX_HISTORY_MESSAGES)
    const requestImages = [...selectedImages]
    const requestGuideId = useGuide ? canonicalGuideId : ''
    const userMessage: LlmChatMessage = {
      role: 'user',
      content,
      attachments: requestImages.length
        ? requestImages.map(file => ({ kind: 'image', name: file.name }))
        : undefined,
    }
    const nextMessages: LlmChatMessage[] = [...retainedHistory, userMessage]
    const controller = new AbortController()
    requestRef.current?.controller.abort()
    const pending: PendingChatRequest = {
      workspace: requestWorkspace,
      projectInstance: requestProjectInstance,
      controller,
      modelId,
      customModel,
      effectiveModelId,
      useGuide,
      guideId,
      requestGuideId,
      guideTargetOverridden: guideTargetOverridden.current,
      draft: content,
      images: requestImages,
      retainedHistory,
      uploadedRefs: [],
      chatSubmitted: false,
    }
    requestRef.current = pending
    const pendingStillOwnsProject = () => (
      requestRef.current === pending
      && projectInstanceRef.current === pending.projectInstance
    )
    setSending(true)
    setError(null)
    try {
      setUploadingImages(requestImages.length > 0)
      for (const file of requestImages) {
        const upload = await api.uploadLlmChatImage(
          requestWorkspace,
          file,
          controller.signal,
        )
        pending.uploadedRefs.push(upload.filename)
        if (
          controller.signal.aborted
          || !pendingStillOwnsProject()
        ) {
          cleanupUnsubmittedUploads(pending)
          return
        }
      }
      setUploadingImages(false)
      setMessages(nextMessages)
      persistMessages(requestWorkspace, requestProjectInstance, nextMessages)
      setDraft('')
      setSelectedImages([])
      if (fileInputRef.current) fileInputRef.current.value = ''
      pending.chatSubmitted = true
      const response = await api.llmChat({
        workspace: requestWorkspace,
        model_id: pending.effectiveModelId,
        messages: nextMessages,
        guide_ids: pending.useGuide && pending.requestGuideId ? [pending.requestGuideId] : [],
        image_paths: pending.uploadedRefs,
        max_new_tokens: 2048,
      }, controller.signal)
      if (!pendingStillOwnsProject()) return
      setMessages(current => {
        const completed = [...current, { role: 'assistant' as const, content: response.text }]
        persistMessages(requestWorkspace, requestProjectInstance, completed)
        return completed
      })
      guideTargetOverridden.current = false
      setUseGuide(false)
      setGuideId(matchingVideoGuideId(
        useStore.getState().selectedModelPerMode.video || '',
        guidesRef.current,
      ))
    } catch (err) {
      if (!pending.chatSubmitted) cleanupUnsubmittedUploads(pending)
      if (!pendingStillOwnsProject()) return
      setModelId(pending.modelId)
      setCustomModel(pending.customModel)
      setUseGuide(pending.useGuide)
      guideTargetOverridden.current = pending.guideTargetOverridden
      setGuideId(
        pending.guideTargetOverridden
          ? pending.guideId
          : matchingVideoGuideId(
            useStore.getState().selectedModelPerMode.video || '',
            guidesRef.current,
          ),
      )
      setMessages(pending.retainedHistory)
      persistMessages(
        requestWorkspace,
        requestProjectInstance,
        pending.retainedHistory,
      )
      setDraft(pending.draft)
      setSelectedImages(pending.images)
      if (controller.signal.aborted) {
        return
      }
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (!pending.chatSubmitted) cleanupUnsubmittedUploads(pending)
      if (pendingStillOwnsProject()) {
        requestRef.current = null
        setSending(false)
        setUploadingImages(false)
      }
    }
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-bg-primary">
      <div className="border-b border-border bg-bg-secondary px-3 py-3 md:px-6">
        <div className="mx-auto flex max-w-5xl flex-wrap items-end gap-3">
          <label className="min-w-0 basis-full flex-1 text-[10px] font-medium text-text-muted md:basis-[420px]">
            LLM
            <select
              aria-label="Language model for Chat"
              aria-describedby="llm-model-details chat-data-disclosure"
              value={modelId}
              onChange={event => {
                modelSelectionTouched.current = true
                const nextId = event.target.value
                const nextModel = models.find(model => model.id === nextId)
                setModelId(nextId)
                setCustomModel('')
                const nextAcceptsImages = !nextModel || (
                  nextModel.current
                    ? nextModel.vision_available !== false && nextModel.vision_capable !== false
                    : nextModel.vision_capable !== false
                )
                if (!nextAcceptsImages && selectedImages.length) {
                  setSelectedImages([])
                  if (fileInputRef.current) fileInputRef.current.value = ''
                  setError('Image attachments were removed because that LLM is text only.')
                }
              }}
              disabled={loadingCatalog || sending}
              className="mt-1 w-full rounded-md border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-primary"
            >
              {models.length === 0 && <option value="">{loadingCatalog ? 'Loading models…' : 'No models available'}</option>}
              {models.map(model => (
                <option key={model.id} value={model.id}>
                  {modelPickerLabel(model)}
                </option>
              ))}
            </select>
          </label>

          {canUseCustomModel && (
            <label className="min-w-0 basis-full flex-1 text-[10px] font-medium text-text-muted md:basis-[320px]">
              Hugging Face ID or URL (local only)
              <input
                value={customModel}
                onChange={event => {
                  modelSelectionTouched.current = true
                  setCustomModel(event.target.value)
                }}
                disabled={sending}
                placeholder="org/model or https://huggingface.co/org/model"
                className="mt-1 w-full rounded-md border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-primary placeholder:text-text-muted"
              />
            </label>
          )}

          <button type="button" onClick={clearConversation} disabled={!messages.length || sending} className="flex items-center gap-1 rounded-md border border-border px-3 py-2 text-xs text-text-secondary disabled:opacity-40">
            <Trash2 size={13} /> Clear
          </button>
        </div>
        <div id="llm-model-details" className="mx-auto mt-2 max-w-5xl text-[10px] text-text-muted">
          {selectedModel
            ? `${selectedModel.label} · ${modelMeta(selectedModel) || selectedModel.id}`
            : effectiveModelId
              ? `${effectiveModelId} · will be located or downloaded when you send`
              : 'Choose a model to begin.'}
          {downloadProgress(selectedModel) ? ` · ${downloadProgress(selectedModel)} downloaded` : ''}
        </div>
        {selectedModel?.speed?.reason && (
          <div className="mx-auto mt-1 max-w-5xl text-[10px] text-text-muted">
            Speed basis: {selectedModel.speed.reason}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5 md:px-6">
        <div className="mx-auto flex max-w-4xl flex-col gap-3">
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-border bg-bg-secondary/60 p-8 text-center text-sm text-text-muted">
              Start a project-scoped conversation with the selected LLM.
            </div>
          )}
          {messages.map((message, index) => (
            <article key={`${message.role}-${index}`} className={`flex gap-3 rounded-xl border p-3 ${message.role === 'user' ? 'border-accent-blue/30 bg-accent-blue/5' : 'border-border bg-bg-secondary'}`}>
              <div className="mt-0.5 shrink-0 text-text-muted">{message.role === 'user' ? <UserRound size={16} /> : <Bot size={16} />}</div>
              <div className="min-w-0 text-sm leading-6 text-text-primary">
                {!!message.attachments?.length && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {message.attachments.map((attachment, attachmentIndex) => (
                      <span key={`${attachment.name}-${attachmentIndex}`} className="rounded border border-border bg-bg-tertiary px-2 py-0.5 text-[10px] text-text-muted">
                        Image: {attachment.name}
                      </span>
                    ))}
                  </div>
                )}
                <div className="whitespace-pre-wrap break-words">{message.content}</div>
              </div>
            </article>
          ))}
          {sending && (
            <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-xl border border-border bg-bg-secondary p-3 text-xs text-text-muted">
              <Loader2 size={14} className="animate-spin" />
              {uploadingImages
                ? `Uploading ${selectedImages.length || 'attached'} image${selectedImages.length === 1 ? '' : 's'}…`
                : selectedModel?.loading
                  ? `Preparing ${selectedModel.label}${downloadProgress(selectedModel) ? ` (${downloadProgress(selectedModel)})` : ''}…`
                  : selectedModel?.downloaded === false || !selectedModel
                    ? 'Preparing model (download may be required), then generating…'
                    : `Generating with ${selectedModel.label}…`}
              <button
                type="button"
                onClick={() => requestRef.current?.controller.abort()}
                className="ml-auto rounded border border-border px-2 py-1 text-[10px] text-text-secondary"
              >
                Cancel wait
              </button>
            </div>
          )}
          {error && <div role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}
          <div ref={endRef} />
        </div>
      </div>

      <div className="border-t border-border bg-bg-secondary px-3 py-3 md:px-6">
        <div className="mx-auto max-w-4xl">
          {!!selectedImages.length && (
            <div className="mb-2 flex flex-wrap gap-2">
              {selectedImages.map((file, index) => (
                <span key={`${file.name}-${file.lastModified}-${index}`} className="flex max-w-full items-center gap-1.5 rounded-md border border-accent-blue/30 bg-accent-blue/5 px-2 py-1 text-[10px] text-text-secondary">
                  <ImagePlus size={12} className="shrink-0" />
                  <span className="truncate">{file.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => setSelectedImages(current => current.filter((_, itemIndex) => itemIndex !== index))}
                    disabled={sending}
                    className="shrink-0 text-text-muted hover:text-text-primary disabled:opacity-40"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            aria-label="Message the selected language model"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            disabled={sending || !!unavailableReason}
            rows={3}
            placeholder={unavailableReason || 'Message the model… (Shift+Enter for a new line)'}
            className="min-h-[74px] w-full resize-y rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,.avif,.bmp,.gif,.jpg,.jpeg,.png,.webp"
              multiple
              onChange={event => selectImages(event.target.files)}
              disabled={sending || !effectiveModelId || !modelAcceptsImages}
              className="sr-only"
            />
            <button
              type="button"
              aria-label="Attach images"
              title={!modelAcceptsImages ? 'Vision is unavailable for this LLM' : 'Attach up to four images'}
              onClick={() => fileInputRef.current?.click()}
              disabled={sending || !effectiveModelId || !modelAcceptsImages || selectedImages.length >= MAX_IMAGE_ATTACHMENTS}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border text-text-secondary disabled:opacity-40"
            >
              <ImagePlus size={16} />
            </button>
            <label className="flex min-h-10 items-center gap-2 rounded-lg border border-border px-3 text-[10px] font-medium text-text-secondary">
              <input
                type="checkbox"
                checked={useGuide}
                onChange={event => setUseGuide(event.target.checked)}
                disabled={!videoGuides.length || sending}
              />
              Add a prompting guide to this message
            </label>
            <label className="min-w-[220px] flex-1 text-[10px] font-medium text-text-muted sm:max-w-[320px]">
              Video prompting target
              <select
                aria-label="Video prompting target for this message"
                value={guideId}
                onChange={event => {
                  guideTargetOverridden.current = true
                  setGuideId(event.target.value)
                }}
                disabled={!useGuide || !videoGuides.length || sending}
                className="mt-0.5 w-full rounded-md border border-border bg-bg-tertiary px-2 py-1 text-xs text-text-primary disabled:opacity-50"
              >
                {!guideId && (
                  <option value="">
                    {selectedVideoModel ? 'Choose a target for this video model' : 'Choose a video prompting target'}
                  </option>
                )}
                {videoGuides.map(guide => (
                  <option key={guide.id} value={guide.id}>{guide.label}</option>
                ))}
              </select>
            </label>
            <span className="text-[10px] text-text-muted">
              {guideTargetOverridden.current
                ? 'Target overridden for this draft'
                : selectedVideoModel
                  ? 'Following Studio / Director video model'
                  : 'No Studio / Director video model selected'}
            </span>
            <button type="button" onClick={() => void send()} disabled={!draft.trim() || sending || !!unavailableReason} className="ml-auto flex h-10 items-center gap-2 rounded-lg bg-accent-blue px-4 text-xs font-medium text-white disabled:opacity-40">
              {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Send
            </button>
          </div>
          <p id="chat-data-disclosure" className="mt-2 text-[10px] leading-4 text-text-muted">
            Conversation history is stored in this browser, separately for this project. Sending a message transmits the retained conversation history and images attached to that message to the {providerDisplayName(selectedProvider)}.
          </p>
        </div>
      </div>
    </section>
  )
}
