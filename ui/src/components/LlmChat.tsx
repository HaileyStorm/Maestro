import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Flag, ImagePlus, Loader2, Pencil, RotateCcw, Send, Trash2, UserRound, X } from 'lucide-react'
import * as api from '../api/client'
import { useStore } from '../stores/useStore'
import type { LlmChatMessage, LlmModelOption, LlmPromptGuideOption } from '../types'

const STORAGE_PREFIX = 'maestro:llm-chat:'
const OPERATION_STORAGE_PREFIX = 'maestro:llm-chat-operation:'
const MAX_HISTORY_MESSAGES = 62
const MAX_IMAGE_ATTACHMENTS = 4
const MAX_IMAGE_BYTES = 32 * 1024 * 1024
const IMAGE_EXTENSION = /\.(avif|bmp|gif|jpe?g|png|webp)$/i

interface RefusalCapture {
  messageIndex: number
  literal: string
}

interface RefusalCaptureError {
  messageIndex: number
  message: string
}

interface RefusalSelectionResult {
  literal?: string
  error?: string
}

interface RefusalSelectionSnapshot {
  messageIndex: number
  result: RefusalSelectionResult
}

interface RefusalLiteralSaveRequest {
  token: number
  workspace: string
  projectInstance: string
  controller: AbortController
}

function storageKey(workspace: string, projectInstance: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(workspace)}:${projectInstance}`
}

function pendingKey(workspace: string, projectInstance: string): string {
  return `${workspace}\0${projectInstance}`
}

function operationStorageKey(workspace: string, projectInstance: string): string {
  return `${OPERATION_STORAGE_PREFIX}${encodeURIComponent(workspace)}:${projectInstance}`
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
        performance: item.role === 'assistant'
          && item.performance
          && (item.performance.average_tps === null
            || (typeof item.performance.average_tps === 'number'
              && Number.isFinite(item.performance.average_tps)))
          ? {
              average_tps: item.performance.average_tps,
              generated_tokens_approx: typeof item.performance.generated_tokens_approx === 'number'
                ? item.performance.generated_tokens_approx
                : undefined,
              elapsed_seconds: typeof item.performance.elapsed_seconds === 'number'
                ? item.performance.elapsed_seconds
                : undefined,
            }
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
  requestId: string
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
  submittedMessages: LlmChatMessage[]
  uploadedRefs: string[]
  submissionAttempted: boolean
  requiresFreshImage: boolean
  editingTurn: EditingTurn | null
  latestStatus?: api.LlmChatOperationStatus
}

type ChatRequestPhase = 'idle' | 'uploading' | 'queued' | 'preparing' | 'generating'
interface EditingTurn {
  index: number
  requiresFreshImage: boolean
}

interface ScopedChatStatus {
  workspace: string
  projectInstance: string
  requestId: string
  status: api.LlmChatOperationStatus
}

function operationRequestPhase(phase: string): ChatRequestPhase {
  if (phase === 'queued') return 'queued'
  if (phase === 'loading') return 'preparing'
  return 'generating'
}

function boundedChatHistory(messages: LlmChatMessage[]): LlmChatMessage[] {
  const bounded = messages.slice(-MAX_HISTORY_MESSAGES)
  return bounded[0]?.role === 'assistant' ? bounded.slice(1) : bounded
}

function retryChatBranch(
  messages: LlmChatMessage[],
  assistantIndex: number,
): LlmChatMessage[] | null {
  if (messages[assistantIndex]?.role !== 'assistant') return null
  const branch = boundedChatHistory(messages.slice(0, assistantIndex))
  if (branch.some(message => message.attachments?.length)) return null
  return branch.length > 0 && branch.at(-1)?.role === 'user' ? branch : null
}

function editedChatBranch(
  messages: LlmChatMessage[],
  userIndex: number,
  editedUser: LlmChatMessage,
): LlmChatMessage[] | null {
  if (messages[userIndex]?.role !== 'user' || editedUser.role !== 'user') return null
  const prefix = boundedChatHistory(messages.slice(0, userIndex))
  if (prefix.some(message => message.attachments?.length)) return null
  return [...prefix, editedUser]
}

const suspendedChatRequests = new Map<string, PendingChatRequest>()

function persistPendingOperation(pending: PendingChatRequest): void {
  try {
    localStorage.setItem(operationStorageKey(pending.workspace, pending.projectInstance), JSON.stringify({
      requestId: pending.requestId,
      workspace: pending.workspace,
      projectInstance: pending.projectInstance,
      modelId: pending.modelId,
      customModel: pending.customModel,
      effectiveModelId: pending.effectiveModelId,
      useGuide: pending.useGuide,
      guideId: pending.guideId,
      requestGuideId: pending.requestGuideId,
      guideTargetOverridden: pending.guideTargetOverridden,
      draft: pending.draft,
      retainedHistory: pending.retainedHistory,
      submittedMessages: pending.submittedMessages,
      requires_fresh_image: pending.requiresFreshImage,
      editingTurn: pending.editingTurn,
    }))
  } catch { /* In-memory recovery remains available for this browser session. */ }
}

function removePendingOperation(workspace: string, projectInstance: string): void {
  try { localStorage.removeItem(operationStorageKey(workspace, projectInstance)) } catch { /* private mode */ }
}

function restorePendingOperation(
  workspace: string,
  projectInstance: string,
): PendingChatRequest | null {
  try {
    const raw = localStorage.getItem(operationStorageKey(workspace, projectInstance))
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<PendingChatRequest> & {
      requires_fresh_image?: unknown
    }
    if (
      typeof value.requestId !== 'string'
      || value.workspace !== workspace
      || value.projectInstance !== projectInstance
      || !Array.isArray(value.retainedHistory)
      || !Array.isArray(value.submittedMessages)
    ) return null
    return {
      workspace,
      projectInstance,
      controller: new AbortController(),
      requestId: value.requestId,
      modelId: typeof value.modelId === 'string' ? value.modelId : '',
      customModel: typeof value.customModel === 'string' ? value.customModel : '',
      effectiveModelId: typeof value.effectiveModelId === 'string' ? value.effectiveModelId : '',
      useGuide: value.useGuide === true,
      guideId: typeof value.guideId === 'string' ? value.guideId : '',
      requestGuideId: typeof value.requestGuideId === 'string' ? value.requestGuideId : '',
      guideTargetOverridden: value.guideTargetOverridden === true,
      draft: typeof value.draft === 'string' ? value.draft : '',
      images: [],
      retainedHistory: value.retainedHistory,
      submittedMessages: value.submittedMessages,
      uploadedRefs: [],
      submissionAttempted: true,
      requiresFreshImage: value.requires_fresh_image === true,
      editingTurn: value.editingTurn
        && typeof value.editingTurn.index === 'number'
        ? {
            index: value.editingTurn.index,
            requiresFreshImage: value.editingTurn.requiresFreshImage === true,
          }
        : null,
    }
  } catch {
    return null
  }
}

function hasPendingOperation(workspace: string, projectInstance: string): boolean {
  return suspendedChatRequests.has(pendingKey(workspace, projectInstance))
    || restorePendingOperation(workspace, projectInstance) !== null
}

function cleanupUnsubmittedUploads(pending: PendingChatRequest): void {
  if (pending.submissionAttempted || pending.uploadedRefs.length === 0) return
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
  const [requestPhase, setRequestPhase] = useState<ChatRequestPhase>('idle')
  const [resumeAvailable, setResumeAvailable] = useState(false)
  const [resumeNonce, setResumeNonce] = useState(0)
  const [editingTurn, setEditingTurn] = useState<EditingTurn | null>(null)
  const [freshImagesRequired, setFreshImagesRequired] = useState(false)
  const [liveChatStatus, setLiveChatStatus] = useState<ScopedChatStatus | null>(null)
  const [refusalCapture, setRefusalCapture] = useState<RefusalCapture | null>(null)
  const [refusalCaptureError, setRefusalCaptureError] = useState<RefusalCaptureError | null>(null)
  const [refusalCaptureNotice, setRefusalCaptureNotice] = useState<string | null>(null)
  const [savingRefusalLiteral, setSavingRefusalLiteral] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const refusalLiteralInputRef = useRef<HTMLTextAreaElement>(null)
  const assistantContentRefs = useRef(new Map<number, HTMLDivElement>())
  const refusalCaptureTriggerRefs = useRef(new Map<number, HTMLButtonElement>())
  const refusalCaptureErrorRefs = useRef(new Map<number, HTMLParagraphElement>())
  const refusalSelectionSnapshotRef = useRef<RefusalSelectionSnapshot | null>(null)
  const refusalLiteralSaveRef = useRef<RefusalLiteralSaveRequest | null>(null)
  const refusalLiteralSaveTokenRef = useRef(0)
  const requestRef = useRef<PendingChatRequest | null>(null)
  const projectInstanceRef = useRef('')
  const guidesRef = useRef<LlmPromptGuideOption[]>([])
  const modelSelectionTouched = useRef(false)
  const guideTargetOverridden = useRef(false)
  const canUseCustomModel = accessContext?.custom_model_sources === true
  const canManageRefusalLiterals = accessContext?.machine_controls === true

  const cancelActiveRefusalLiteralSave = useCallback(() => {
    refusalLiteralSaveTokenRef.current += 1
    refusalLiteralSaveRef.current?.controller.abort()
    refusalLiteralSaveRef.current = null
    setSavingRefusalLiteral(false)
  }, [])

  const adoptProjectInstance = useCallback((nextProjectInstance: string) => {
    if (
      !nextProjectInstance
      || nextProjectInstance === projectInstanceRef.current
    ) return false
    cancelActiveRefusalLiteralSave()
    refusalSelectionSnapshotRef.current = null
    const pending = requestRef.current
    if (pending) {
      pending.controller.abort()
      if (pending.submissionAttempted) {
        suspendedChatRequests.set(
          pendingKey(pending.workspace, pending.projectInstance),
          pending,
        )
        persistPendingOperation(pending)
        persistMessages(
          pending.workspace,
          pending.projectInstance,
          pending.submittedMessages,
        )
      } else {
        persistMessages(
          pending.workspace,
          pending.projectInstance,
          pending.retainedHistory,
        )
        cleanupUnsubmittedUploads(pending)
      }
      requestRef.current = null
    }
    projectInstanceRef.current = nextProjectInstance
    setProjectInstance(nextProjectInstance)
    setMessages(restoreMessages(activeWorkspace, nextProjectInstance))
    setDraft('')
    setEditingTurn(null)
    setFreshImagesRequired(false)
    setLiveChatStatus(null)
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    setRefusalCaptureNotice(null)
    setSavingRefusalLiteral(false)
    setUseGuide(false)
    setSelectedImages([])
    setSending(false)
    setUploadingImages(false)
    setRequestPhase('idle')
    setResumeAvailable(hasPendingOperation(activeWorkspace, nextProjectInstance))
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
  }, [activeWorkspace, cancelActiveRefusalLiteralSave])

  useEffect(() => {
    cancelActiveRefusalLiteralSave()
    refusalSelectionSnapshotRef.current = null
    setSending(false)
    setUploadingImages(false)
    setRequestPhase('idle')
    setResumeAvailable(false)
    setSelectedImages([])
    setDraft('')
    setEditingTurn(null)
    setFreshImagesRequired(false)
    setLiveChatStatus(null)
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    setRefusalCaptureNotice(null)
    setSavingRefusalLiteral(false)
    setUseGuide(false)
    guideTargetOverridden.current = false
    setGuideId('')
    if (fileInputRef.current) fileInputRef.current.value = ''
    projectInstanceRef.current = ''
    setProjectInstance('')
    setMessages([])
    setError(null)
    return () => {
      const refusalSave = refusalLiteralSaveRef.current
      if (refusalSave?.workspace === activeWorkspace) {
        refusalLiteralSaveTokenRef.current += 1
        refusalSave.controller.abort()
        refusalLiteralSaveRef.current = null
      }
      const pending = requestRef.current
      if (pending?.workspace !== activeWorkspace) return
      pending.controller.abort()
      if (pending.submissionAttempted) {
        suspendedChatRequests.set(
          pendingKey(pending.workspace, pending.projectInstance),
          pending,
        )
        persistPendingOperation(pending)
        persistMessages(
          pending.workspace,
          pending.projectInstance,
          pending.submittedMessages,
        )
      } else {
        persistMessages(
          pending.workspace,
          pending.projectInstance,
          pending.retainedHistory,
        )
        cleanupUnsubmittedUploads(pending)
      }
      requestRef.current = null
    }
  }, [activeWorkspace, cancelActiveRefusalLiteralSave])

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
    if (canManageRefusalLiterals) return
    cancelActiveRefusalLiteralSave()
    refusalSelectionSnapshotRef.current = null
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    setRefusalCaptureNotice(null)
  }, [canManageRefusalLiterals, cancelActiveRefusalLiteralSave])

  useEffect(() => {
    setRefusalCapture(current => (
      current && messages[current.messageIndex]?.role === 'assistant'
        ? current
        : null
    ))
  }, [messages])

  useEffect(() => {
    if (!sending) return
    let cancelled = false
    let refreshing = false
    let timer: number | null = null
    const schedule = () => {
      if (
        cancelled
        || (typeof document !== 'undefined' && document.visibilityState === 'hidden')
      ) return
      timer = window.setTimeout(run, 1000)
    }
    const run = () => {
      if (
        cancelled
        || refreshing
        || (typeof document !== 'undefined' && document.visibilityState === 'hidden')
      ) return
      refreshing = true
      void api.fetchLlmModels(activeWorkspace)
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
        .finally(() => {
          refreshing = false
          schedule()
        })
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (timer !== null) window.clearTimeout(timer)
        timer = null
      } else if (timer === null) {
        run()
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    run()
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [activeWorkspace, adoptProjectInstance, sending])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  useEffect(() => {
    if (!activeWorkspace || !projectInstance) return
    const key = pendingKey(activeWorkspace, projectInstance)
    const pending = suspendedChatRequests.get(key)
      ?? restorePendingOperation(activeWorkspace, projectInstance)
    if (!pending || requestRef.current) return
    suspendedChatRequests.set(key, pending)
    const controller = new AbortController()
    pending.controller = controller
    requestRef.current = pending
    const stillOwnsProject = () => (
      requestRef.current === pending
      && projectInstanceRef.current === pending.projectInstance
    )
    setResumeAvailable(false)
    setSending(true)
    setUploadingImages(false)
    setRequestPhase('queued')
    setLiveChatStatus(pending.latestStatus ? {
      workspace: pending.workspace,
      projectInstance: pending.projectInstance,
      requestId: pending.requestId,
      status: pending.latestStatus,
    } : null)
    setError(null)
    void api.waitForLlmChatOperation(
      pending.requestId,
      pending.workspace,
      controller.signal,
      undefined,
      status => {
        if (!stillOwnsProject()) return
        pending.latestStatus = status
        setLiveChatStatus({
          workspace: pending.workspace,
          projectInstance: pending.projectInstance,
          requestId: pending.requestId,
          status,
        })
        setRequestPhase(operationRequestPhase(status.phase))
      },
    ).then(response => {
      if (!stillOwnsProject()) return
      suspendedChatRequests.delete(key)
      removePendingOperation(pending.workspace, pending.projectInstance)
      const completed = [
        ...pending.submittedMessages,
        {
          role: 'assistant' as const,
          content: response.text,
          performance: response.average_tps != null
            || response.generated_tokens_approx != null
            || response.elapsed_seconds != null
            ? {
                average_tps: response.average_tps ?? null,
                generated_tokens_approx: response.generated_tokens_approx,
                elapsed_seconds: response.elapsed_seconds,
              }
            : undefined,
        },
      ]
      setMessages(completed)
      setLiveChatStatus(null)
      persistMessages(pending.workspace, pending.projectInstance, completed)
      guideTargetOverridden.current = false
      setUseGuide(false)
      setGuideId(matchingVideoGuideId(
        useStore.getState().selectedModelPerMode.video || '',
        guidesRef.current,
      ))
    }).catch(error => {
      if (!stillOwnsProject()) return
      if (controller.signal.aborted || error instanceof api.LlmChatWaitError) {
        persistPendingOperation(pending)
        setResumeAvailable(true)
        setError(
          error instanceof api.LlmChatWaitError
            ? error.message
            : 'Stopped waiting. The host may still be generating; resume to retrieve the result.',
        )
        return
      }
      suspendedChatRequests.delete(key)
      removePendingOperation(pending.workspace, pending.projectInstance)
      setLiveChatStatus(null)
      setMessages(pending.retainedHistory)
      persistMessages(pending.workspace, pending.projectInstance, pending.retainedHistory)
      setDraft(pending.draft)
      setSelectedImages(pending.images)
      setFreshImagesRequired(
        pending.requiresFreshImage && pending.images.length === 0,
      )
      setEditingTurn(pending.editingTurn)
      setError(error instanceof Error ? error.message : String(error))
    }).finally(() => {
      if (stillOwnsProject()) {
        requestRef.current = null
        setSending(false)
        setRequestPhase('idle')
      }
    })
    return () => controller.abort()
  }, [activeWorkspace, projectInstance, resumeNonce])

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
  const interactionLocked = sending || resumeAvailable || savingRefusalLiteral
  const branchControlsLocked = interactionLocked || editingTurn !== null
  const unavailableReason = !activeWorkspace
    ? 'Select or create a project before chatting.'
    : !projectInstance
      ? 'Opening the project conversation…'
    : !effectiveModelId
      ? 'Choose an LLM first.'
      : resumeAvailable
        ? 'Resume the accepted request before starting another message.'
      : useGuide && !canonicalGuideId
        ? 'Choose a video prompting target for this message.'
      : freshImagesRequired && selectedImages.length === 0
        ? 'Reattach at least one image before sending this image turn.'
      : selectedImages.length > 0 && !modelAcceptsImages
        ? 'The selected LLM is text only. Remove the image attachment or choose a vision model.'
      : null
  const activeLiveStatus = liveChatStatus
    && liveChatStatus.workspace === activeWorkspace
    && liveChatStatus.projectInstance === projectInstance
    ? liveChatStatus.status
    : null

  const clearConversation = () => {
    if (interactionLocked || refusalLiteralSaveRef.current) return
    const pending = requestRef.current
    pending?.controller.abort()
    if (pending) cleanupUnsubmittedUploads(pending)
    requestRef.current = null
    suspendedChatRequests.delete(pendingKey(activeWorkspace, projectInstance))
    removePendingOperation(activeWorkspace, projectInstance)
    setSending(false)
    setUploadingImages(false)
    setRequestPhase('idle')
    setResumeAvailable(false)
    setEditingTurn(null)
    setFreshImagesRequired(false)
    setLiveChatStatus(null)
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    setRefusalCaptureNotice(null)
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

  const submitBranch = async (
    nextMessages: LlmChatMessage[],
    retainedHistory: LlmChatMessage[],
    requestImages: File[],
    retainedDraft: string,
    clearComposer: boolean,
  ) => {
    if (interactionLocked || refusalLiteralSaveRef.current || !nextMessages.length) return
    refusalSelectionSnapshotRef.current = null
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    const requestWorkspace = activeWorkspace
    const requestProjectInstance = projectInstance
    const requestGuideId = useGuide ? canonicalGuideId : ''
    const requestExplicitOutput = useStore.getState().explicitOutput
    const controller = new AbortController()
    requestRef.current?.controller.abort()
    const pending: PendingChatRequest = {
      workspace: requestWorkspace,
      projectInstance: requestProjectInstance,
      controller,
      requestId: api.createLlmRequestId(),
      modelId,
      customModel,
      effectiveModelId,
      useGuide,
      guideId,
      requestGuideId,
      guideTargetOverridden: guideTargetOverridden.current,
      draft: retainedDraft,
      images: requestImages,
      retainedHistory,
      submittedMessages: nextMessages,
      uploadedRefs: [],
      submissionAttempted: false,
      requiresFreshImage: requestImages.length > 0,
      editingTurn,
    }
    requestRef.current = pending
    const pendingStillOwnsProject = () => (
      requestRef.current === pending
      && projectInstanceRef.current === pending.projectInstance
    )
    setSending(true)
    setResumeAvailable(false)
    setLiveChatStatus(null)
    setRequestPhase(requestImages.length > 0 ? 'uploading' : 'queued')
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
      setRequestPhase('queued')
      setMessages(nextMessages)
      if (clearComposer) {
        setDraft('')
        setSelectedImages([])
        setEditingTurn(null)
        setFreshImagesRequired(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
      const response = await api.llmChat({
        workspace: requestWorkspace,
        request_id: pending.requestId,
        model_id: pending.effectiveModelId,
        messages: nextMessages,
        guide_ids: pending.useGuide && pending.requestGuideId ? [pending.requestGuideId] : [],
        explicit_output: requestExplicitOutput,
        image_paths: pending.uploadedRefs,
        max_new_tokens: 2048,
      }, controller.signal, status => {
        if (!pendingStillOwnsProject()) return
        setRequestPhase(
          status.phase === 'queued'
            ? 'queued'
            : status.phase === 'loading'
              ? 'preparing'
              : 'queued',
        )
      }, status => {
        pending.latestStatus = status
        persistPendingOperation(pending)
        if (pendingStillOwnsProject()) {
          setLiveChatStatus({
            workspace: pending.workspace,
            projectInstance: pending.projectInstance,
            requestId: pending.requestId,
            status,
          })
          setRequestPhase(operationRequestPhase(status.phase))
        }
      }, () => {
        pending.submissionAttempted = true
        persistPendingOperation(pending)
        if (pendingStillOwnsProject()) {
          persistMessages(
            requestWorkspace,
            requestProjectInstance,
            pending.submittedMessages,
          )
        }
      })
      if (!pendingStillOwnsProject()) return
      setMessages(current => {
        const completed = [
          ...current,
          {
            role: 'assistant' as const,
            content: response.text,
            performance: response.average_tps != null
              || response.generated_tokens_approx != null
              || response.elapsed_seconds != null
              ? {
                  average_tps: response.average_tps ?? null,
                  generated_tokens_approx: response.generated_tokens_approx,
                  elapsed_seconds: response.elapsed_seconds,
                }
              : undefined,
          },
        ]
        persistMessages(requestWorkspace, requestProjectInstance, completed)
        return completed
      })
      setLiveChatStatus(null)
      guideTargetOverridden.current = false
      setUseGuide(false)
      setGuideId(matchingVideoGuideId(
        useStore.getState().selectedModelPerMode.video || '',
        guidesRef.current,
      ))
      suspendedChatRequests.delete(pendingKey(requestWorkspace, requestProjectInstance))
      removePendingOperation(requestWorkspace, requestProjectInstance)
      setLiveChatStatus(null)
    } catch (err) {
      if (!pending.submissionAttempted) cleanupUnsubmittedUploads(pending)
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
      if (controller.signal.aborted || err instanceof api.LlmChatWaitError) {
        if (pending.submissionAttempted) {
          suspendedChatRequests.set(
            pendingKey(requestWorkspace, requestProjectInstance),
            pending,
          )
          persistPendingOperation(pending)
          setMessages(pending.submittedMessages)
          persistMessages(
            requestWorkspace,
            requestProjectInstance,
            pending.submittedMessages,
          )
          setResumeAvailable(true)
          setError(
            err instanceof api.LlmChatWaitError
              ? err.message
              : 'Stopped waiting. The host may still be generating; resume to retrieve the result.',
          )
        } else {
          setMessages(pending.retainedHistory)
          persistMessages(
            requestWorkspace,
            requestProjectInstance,
            pending.retainedHistory,
          )
          setDraft(pending.draft)
          setSelectedImages(pending.images)
          setFreshImagesRequired(
            pending.requiresFreshImage && pending.images.length === 0,
          )
          setEditingTurn(pending.editingTurn)
          setLiveChatStatus(null)
        }
        return
      }
      suspendedChatRequests.delete(pendingKey(requestWorkspace, requestProjectInstance))
      removePendingOperation(requestWorkspace, requestProjectInstance)
      setMessages(pending.retainedHistory)
      persistMessages(
        requestWorkspace,
        requestProjectInstance,
        pending.retainedHistory,
      )
      setDraft(pending.draft)
      setSelectedImages(pending.images)
      setFreshImagesRequired(
        pending.requiresFreshImage && pending.images.length === 0,
      )
      setEditingTurn(pending.editingTurn)
      setLiveChatStatus(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (!pending.submissionAttempted) cleanupUnsubmittedUploads(pending)
      if (pendingStillOwnsProject()) {
        requestRef.current = null
        setSending(false)
        setUploadingImages(false)
        setRequestPhase('idle')
      }
    }
  }

  const send = async () => {
    const content = draft.trim()
    if (!content || interactionLocked || unavailableReason) return
    const requestImages = [...selectedImages]
    const userMessage: LlmChatMessage = {
      role: 'user',
      content,
      attachments: requestImages.length
        ? requestImages.map(file => ({ kind: 'image', name: file.name }))
        : undefined,
    }
    const nextMessages = editingTurn
      ? editedChatBranch(messages, editingTurn.index, userMessage)
      : [...boundedChatHistory(messages), userMessage]
    if (!nextMessages) {
      setError('This image-bearing branch cannot be edited without starting a new message and reattaching its images.')
      return
    }
    await submitBranch(nextMessages, messages, requestImages, content, true)
  }

  const retryAssistantTurn = async (assistantIndex: number) => {
    if (branchControlsLocked || refusalLiteralSaveRef.current) return
    const branch = retryChatBranch(messages, assistantIndex)
    if (!branch) {
      setError('Retry is unavailable because this branch contains a one-use image. Start a new message and attach the image again.')
      return
    }
    await submitBranch(branch, messages, [], draft, false)
  }

  const editUserTurn = (userIndex: number) => {
    if (branchControlsLocked || refusalLiteralSaveRef.current) return
    const message = messages[userIndex]
    if (message?.role !== 'user') return
    if (messages.slice(0, userIndex).some(item => item.attachments?.length)) {
      setError('Edit is unavailable because earlier context contains a one-use image. Start a new message and attach the image again.')
      return
    }
    setEditingTurn({
      index: userIndex,
      requiresFreshImage: !!message.attachments?.length,
    })
    setFreshImagesRequired(!!message.attachments?.length)
    setDraft(message.content)
    setSelectedImages([])
    if (fileInputRef.current) fileInputRef.current.value = ''
    setError(message.attachments?.length
      ? 'This turn used one-use images. Reattach fresh images before sending the edit.'
      : null)
    window.requestAnimationFrame(() => textareaRef.current?.focus())
  }

  const readRefusalSelection = (messageIndex: number): RefusalSelectionResult => {
    const content = assistantContentRefs.current.get(messageIndex)
    const selection = window.getSelection()
    if (!content || !selection || selection.rangeCount !== 1 || selection.isCollapsed) {
      return { error: 'Select refusal wording inside this assistant response first.' }
    }
    const range = selection.getRangeAt(0)
    if (
      !content.contains(range.startContainer)
      || !content.contains(range.endContainer)
      || !content.contains(range.commonAncestorContainer)
    ) {
      return { error: 'Keep the selection entirely inside this assistant response.' }
    }
    const literal = selection.toString()
    const validationError = api.validateLlmRefusalLiteral(literal)
    return validationError ? { error: validationError } : { literal }
  }

  const showRefusalCaptureError = (messageIndex: number, message: string) => {
    setRefusalCaptureError({ messageIndex, message })
    window.requestAnimationFrame(() => (
      refusalCaptureErrorRefs.current.get(messageIndex)?.focus()
    ))
  }

  const snapshotRefusalSelection = (messageIndex: number) => {
    refusalSelectionSnapshotRef.current = {
      messageIndex,
      result: readRefusalSelection(messageIndex),
    }
  }

  const beginRefusalCapture = (messageIndex: number, usePointerSnapshot: boolean) => {
    setRefusalCapture(null)
    setRefusalCaptureNotice(null)
    setRefusalCaptureError(null)
    if (!canManageRefusalLiterals || messages[messageIndex]?.role !== 'assistant') return
    const snapshot = refusalSelectionSnapshotRef.current
    refusalSelectionSnapshotRef.current = null
    const result = usePointerSnapshot && snapshot?.messageIndex === messageIndex
      ? snapshot.result
      : readRefusalSelection(messageIndex)
    if (result.error || result.literal == null) {
      showRefusalCaptureError(
        messageIndex,
        result.error || 'Select refusal wording inside this assistant response first.',
      )
      return
    }
    setRefusalCapture({ messageIndex, literal: result.literal })
    window.requestAnimationFrame(() => refusalLiteralInputRef.current?.focus())
  }

  const cancelRefusalCapture = () => {
    const messageIndex = refusalCapture?.messageIndex
    setRefusalCapture(null)
    setRefusalCaptureError(null)
    if (messageIndex != null) {
      window.requestAnimationFrame(() => (
        refusalCaptureTriggerRefs.current.get(messageIndex)?.focus()
      ))
    }
  }

  const submitRefusalCapture = async () => {
    if (!refusalCapture || !canManageRefusalLiterals || savingRefusalLiteral) return
    const capture = refusalCapture
    const validationError = api.validateLlmRefusalLiteral(capture.literal)
    if (validationError) {
      showRefusalCaptureError(capture.messageIndex, validationError)
      return
    }
    const saveRequest: RefusalLiteralSaveRequest = {
      token: refusalLiteralSaveTokenRef.current + 1,
      workspace: activeWorkspace,
      projectInstance,
      controller: new AbortController(),
    }
    refusalLiteralSaveTokenRef.current = saveRequest.token
    refusalLiteralSaveRef.current = saveRequest
    setSavingRefusalLiteral(true)
    setRefusalCaptureError(null)
    const isCurrentSave = () => (
      refusalLiteralSaveRef.current === saveRequest
      && refusalLiteralSaveTokenRef.current === saveRequest.token
      && useStore.getState().activeWorkspace === saveRequest.workspace
      && projectInstanceRef.current === saveRequest.projectInstance
      && useStore.getState().accessContext?.machine_controls === true
    )
    try {
      const result = await api.addLlmRefusalLiteral(
        capture.literal,
        saveRequest.controller.signal,
      )
      if (!isCurrentSave()) return
      setRefusalCapture(null)
      setRefusalCaptureNotice(
        `${result.added ? 'Added to' : 'Already in'} local refusal retries. `
        + `${result.count} phrase${result.count === 1 ? '' : 's'} · revision ${result.revision}.`,
      )
      window.getSelection()?.removeAllRanges()
      window.requestAnimationFrame(() => (
        refusalCaptureTriggerRefs.current.get(capture.messageIndex)?.focus()
      ))
    } catch {
      if (!isCurrentSave()) return
      showRefusalCaptureError(
        capture.messageIndex,
        'Could not add the selected refusal wording. Try again locally.',
      )
    } finally {
      if (refusalLiteralSaveRef.current === saveRequest) {
        refusalLiteralSaveRef.current = null
        setSavingRefusalLiteral(false)
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
              disabled={loadingCatalog || interactionLocked}
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
                disabled={interactionLocked}
                placeholder="org/model or https://huggingface.co/org/model"
                className="mt-1 w-full rounded-md border border-border bg-bg-tertiary px-3 py-2 text-xs text-text-primary placeholder:text-text-muted"
              />
            </label>
          )}

          <button type="button" onClick={clearConversation} disabled={!messages.length || interactionLocked} className="flex items-center gap-1 rounded-md border border-border px-3 py-2 text-xs text-text-secondary disabled:opacity-40">
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
          {messages.map((message, index) => {
            const precedingHasImages = messages
              .slice(0, index)
              .some(item => item.attachments?.length)
            const retryUnavailable = message.role === 'assistant' && precedingHasImages
            const editUnavailable = message.role === 'user' && precedingHasImages
            return (
              <article key={`${message.role}-${index}`} className={`flex gap-3 rounded-xl border p-3 ${message.role === 'user' ? 'border-accent-blue/30 bg-accent-blue/5' : 'border-border bg-bg-secondary'}`}>
                <div className="mt-0.5 shrink-0 text-text-muted">{message.role === 'user' ? <UserRound size={16} /> : <Bot size={16} />}</div>
                <div className="min-w-0 flex-1 text-sm leading-6 text-text-primary">
                  {!!message.attachments?.length && (
                    <div className="mb-2 flex flex-wrap gap-1.5">
                      {message.attachments.map((attachment, attachmentIndex) => (
                        <span key={`${attachment.name}-${attachmentIndex}`} className="rounded border border-border bg-bg-tertiary px-2 py-0.5 text-[10px] text-text-muted">
                          Image: {attachment.name}
                        </span>
                      ))}
                    </div>
                  )}
                  <div
                    ref={message.role === 'assistant'
                      ? node => {
                          if (node) assistantContentRefs.current.set(index, node)
                          else assistantContentRefs.current.delete(index)
                        }
                      : undefined}
                    data-assistant-response-content={message.role === 'assistant' ? index : undefined}
                    className="whitespace-pre-wrap break-words"
                  >
                    {message.content}
                  </div>
                  {message.role === 'assistant' && message.performance && (
                    <div className="mt-2 text-[10px] leading-4 text-text-muted" aria-label="Final response performance">
                      Final average: {message.performance.average_tps != null
                        ? `${message.performance.average_tps.toFixed(1)} TPS`
                        : 'unavailable'}
                      {message.performance.generated_tokens_approx != null
                        ? ` · approximately ${message.performance.generated_tokens_approx} generated tokens`
                        : ''}
                    </div>
                  )}
                  {message.role === 'assistant'
                    && refusalCapture?.messageIndex === index && (
                    <form
                      aria-label="Confirm selected refusal wording"
                      onSubmit={event => {
                        event.preventDefault()
                        void submitRefusalCapture()
                      }}
                      className="mt-3 rounded-lg border border-border bg-bg-tertiary p-3"
                    >
                      <label
                        htmlFor={`refusal-literal-${index}`}
                        className="block text-[10px] font-medium text-text-secondary"
                      >
                        Add this exact wording to local refusal retries
                      </label>
                      <textarea
                        ref={refusalLiteralInputRef}
                        id={`refusal-literal-${index}`}
                        aria-describedby={`refusal-literal-help-${index}`}
                        value={refusalCapture.literal}
                        rows={3}
                        onChange={event => {
                          const literal = event.target.value
                          setRefusalCapture({ messageIndex: index, literal })
                          const validationError = api.validateLlmRefusalLiteral(literal)
                          setRefusalCaptureError(validationError
                            ? { messageIndex: index, message: validationError }
                            : null)
                        }}
                        onKeyDown={event => {
                          if (event.key === 'Escape') {
                            event.preventDefault()
                            cancelRefusalCapture()
                          }
                        }}
                        disabled={savingRefusalLiteral}
                        className="mt-1 min-h-[72px] w-full resize-y rounded-md border border-border bg-bg-secondary px-2 py-1.5 text-xs leading-5 text-text-primary disabled:opacity-50"
                      />
                      <p id={`refusal-literal-help-${index}`} className="mt-1 text-[10px] leading-4 text-text-muted">
                        Literal match only, stored on this host. Maximum {api.LLM_REFUSAL_LITERAL_MAX_CODE_POINTS} characters.
                      </p>
                      {refusalCaptureError?.messageIndex === index && (
                        <p
                          ref={node => {
                            if (node) refusalCaptureErrorRefs.current.set(index, node)
                            else refusalCaptureErrorRefs.current.delete(index)
                          }}
                          role="alert"
                          tabIndex={-1}
                          className="mt-1 text-[10px] leading-4 text-red-300"
                        >
                          {refusalCaptureError.message}
                        </p>
                      )}
                      <div className="mt-2 flex flex-wrap justify-end gap-2">
                        <button
                          type="button"
                          onClick={cancelRefusalCapture}
                          disabled={savingRefusalLiteral}
                          className="min-h-9 rounded-md border border-border px-3 text-xs text-text-secondary disabled:opacity-40"
                        >
                          Cancel
                        </button>
                        <button
                          type="submit"
                          disabled={savingRefusalLiteral || !!api.validateLlmRefusalLiteral(refusalCapture.literal)}
                          className="flex min-h-9 items-center gap-1.5 rounded-md bg-accent-blue px-3 text-xs font-medium text-white disabled:opacity-40"
                        >
                          {savingRefusalLiteral && <Loader2 size={13} className="animate-spin" />}
                          Confirm exact wording
                        </button>
                      </div>
                    </form>
                  )}
                  {message.role === 'assistant'
                    && refusalCapture?.messageIndex !== index
                    && refusalCaptureError?.messageIndex === index && (
                    <p
                      ref={node => {
                        if (node) refusalCaptureErrorRefs.current.set(index, node)
                        else refusalCaptureErrorRefs.current.delete(index)
                      }}
                      role="alert"
                      tabIndex={-1}
                      className="mt-2 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-[10px] leading-4 text-red-300"
                    >
                      {refusalCaptureError.message}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-start gap-1">
                  {message.role === 'user' ? (
                    <button
                      type="button"
                      aria-label={`Edit user turn ${index + 1}`}
                      title={editUnavailable
                        ? 'Edit unavailable: earlier context contains one-use images. Start a new message and reattach them.'
                        : message.attachments?.length
                          ? 'Edit this turn; its one-use images must be attached again'
                          : 'Edit this turn and replace its descendants'}
                      onClick={() => editUserTurn(index)}
                      disabled={branchControlsLocked || editUnavailable}
                      className="flex h-9 w-9 items-center justify-center rounded border border-border text-text-muted hover:text-text-primary disabled:opacity-40"
                    >
                      <Pencil size={13} />
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        aria-label={`Retry assistant turn ${index + 1}`}
                        title={retryUnavailable
                          ? 'Retry unavailable: this branch contains one-use images. Start a new message and reattach them.'
                          : 'Retry this response and replace its descendants'}
                        onClick={() => void retryAssistantTurn(index)}
                        disabled={branchControlsLocked || retryUnavailable}
                        className="flex h-9 w-9 items-center justify-center rounded border border-border text-text-muted hover:text-text-primary disabled:opacity-40"
                      >
                        <RotateCcw size={13} />
                      </button>
                      {canManageRefusalLiterals && (
                        <button
                          ref={node => {
                            if (node) refusalCaptureTriggerRefs.current.set(index, node)
                            else refusalCaptureTriggerRefs.current.delete(index)
                          }}
                          type="button"
                          aria-label={`Add selected refusal wording from assistant turn ${index + 1}`}
                          title="Select refusal wording in this response, then add the exact selection to local retries"
                          onPointerDown={() => snapshotRefusalSelection(index)}
                          onPointerCancel={() => {
                            if (refusalSelectionSnapshotRef.current?.messageIndex === index) {
                              refusalSelectionSnapshotRef.current = null
                            }
                          }}
                          onClick={event => beginRefusalCapture(index, event.detail > 0)}
                          disabled={savingRefusalLiteral}
                          className="flex h-9 w-9 items-center justify-center rounded border border-border text-text-muted hover:text-text-primary disabled:opacity-40"
                        >
                          <Flag size={13} />
                        </button>
                      )}
                    </>
                  )}
                </div>
              </article>
            )
          })}
          {refusalCaptureNotice && (
            <div role="status" aria-live="polite" className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-xs text-text-secondary">
              {refusalCaptureNotice}
            </div>
          )}
          {activeLiveStatus?.partial_text && (sending || resumeAvailable) && (
            <article
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
              aria-atomic="false"
              aria-label="Streaming assistant response"
              className="flex gap-3 rounded-xl border border-border bg-bg-secondary p-3"
            >
              <div className="mt-0.5 shrink-0 text-text-muted"><Bot size={16} /></div>
              <div className="min-w-0 whitespace-pre-wrap break-words text-sm leading-6 text-text-primary">
                {activeLiveStatus.partial_text}
              </div>
            </article>
          )}
          {sending && (
            <div aria-label="LLM request status" className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-bg-secondary p-3 text-xs text-text-muted">
              <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                {requestPhase === 'uploading'
                  ? 'Uploading attached images.'
                  : requestPhase === 'queued'
                    ? 'LLM request queued.'
                    : requestPhase === 'preparing'
                      ? 'Preparing the selected LLM.'
                      : 'The selected LLM is generating.'}
              </span>
              <Loader2 size={14} className="animate-spin" />
              {uploadingImages
                ? `Uploading ${selectedImages.length || 'attached'} image${selectedImages.length === 1 ? '' : 's'}…`
                : requestPhase === 'queued'
                  ? 'Queued for the selected LLM…'
                  : requestPhase === 'preparing'
                    ? `Preparing ${selectedModel?.label || 'the selected LLM'}${downloadProgress(selectedModel) ? ` (${downloadProgress(selectedModel)})` : ''}…`
                    : `Generating with ${selectedModel?.label || 'the selected LLM'}…`}
              {activeLiveStatus && (
                <span>
                  Phase: {activeLiveStatus.phase || requestPhase}
                  {activeLiveStatus.attempt != null
                    ? ` · Attempt ${activeLiveStatus.attempt}${activeLiveStatus.attempt_limit != null ? `/${activeLiveStatus.attempt_limit}` : ''}`
                    : ''}
                  {activeLiveStatus.live_tps != null
                    ? ` · Live ${activeLiveStatus.live_tps.toFixed(1)} TPS`
                    : ''}
                  {activeLiveStatus.average_tps != null
                    ? ` · Average ${activeLiveStatus.average_tps.toFixed(1)} TPS`
                    : ''}
                </span>
              )}
              <button
                type="button"
                aria-label="Cancel waiting for this LLM request"
                onClick={() => requestRef.current?.controller.abort()}
                className="ml-auto rounded border border-border px-2 py-1 text-[10px] text-text-secondary"
              >
                Cancel wait
              </button>
            </div>
          )}
          {error && (
            <div role="alert" className="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              <span>{error}</span>
              {resumeAvailable && (
                <button
                  type="button"
                  onClick={() => setResumeNonce(value => value + 1)}
                  className="ml-auto rounded border border-red-400/40 px-2 py-1 text-[10px] text-red-200"
                >
                  Resume wait
                </button>
              )}
            </div>
          )}
          {resumeAvailable && activeLiveStatus && (
            <div role="status" aria-live="polite" aria-label="Paused LLM request status" className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-[10px] text-text-muted">
              Phase: {activeLiveStatus.phase}
              {activeLiveStatus.attempt != null
                ? ` · Attempt ${activeLiveStatus.attempt}${activeLiveStatus.attempt_limit != null ? `/${activeLiveStatus.attempt_limit}` : ''}`
                : ''}
              {activeLiveStatus.live_tps != null ? ` · Last live rate ${activeLiveStatus.live_tps.toFixed(1)} TPS` : ''}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="border-t border-border bg-bg-secondary px-3 py-3 md:px-6">
        <div className="mx-auto max-w-4xl">
          {editingTurn && (
            <div className="mb-2 flex items-center gap-2 rounded-md border border-accent-blue/30 bg-accent-blue/5 px-3 py-2 text-xs text-text-secondary">
              <span>
                Editing user turn {editingTurn.index + 1}. Sending replaces this turn and all descendants.
                {editingTurn.requiresFreshImage ? ' Fresh images are required.' : ''}
              </span>
              <button
                type="button"
                onClick={() => {
                  setEditingTurn(null)
                  setFreshImagesRequired(false)
                  setDraft('')
                  setSelectedImages([])
                  setError(null)
                  if (fileInputRef.current) fileInputRef.current.value = ''
                  window.requestAnimationFrame(() => textareaRef.current?.focus())
                }}
                disabled={interactionLocked}
                className="ml-auto rounded border border-border px-2 py-1 text-[10px] disabled:opacity-40"
              >
                Cancel edit
              </button>
            </div>
          )}
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
                    disabled={interactionLocked}
                    className="shrink-0 text-text-muted hover:text-text-primary disabled:opacity-40"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            aria-label="Message the selected language model"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (
                event.key === 'Enter'
                && !event.shiftKey
                && !event.nativeEvent.isComposing
              ) {
                event.preventDefault()
                void send()
              }
            }}
            disabled={interactionLocked || !activeWorkspace || !projectInstance || !effectiveModelId}
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
              disabled={interactionLocked || !effectiveModelId || !modelAcceptsImages}
              className="sr-only"
            />
            <button
              type="button"
              aria-label="Attach images"
              title={!modelAcceptsImages ? 'Vision is unavailable for this LLM' : 'Attach up to four images'}
              onClick={() => fileInputRef.current?.click()}
              disabled={interactionLocked || !effectiveModelId || !modelAcceptsImages || selectedImages.length >= MAX_IMAGE_ATTACHMENTS}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border text-text-secondary disabled:opacity-40"
            >
              <ImagePlus size={16} />
            </button>
            <label className="flex min-h-10 items-center gap-2 rounded-lg border border-border px-3 text-[10px] font-medium text-text-secondary">
              <input
                type="checkbox"
                checked={useGuide}
                onChange={event => setUseGuide(event.target.checked)}
                disabled={!videoGuides.length || interactionLocked}
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
                disabled={!useGuide || !videoGuides.length || interactionLocked}
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
            <button type="button" onClick={() => void send()} disabled={!draft.trim() || interactionLocked || !!unavailableReason} className="ml-auto flex h-10 items-center gap-2 rounded-lg bg-accent-blue px-4 text-xs font-medium text-white disabled:opacity-40">
              {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} {editingTurn ? 'Send edit' : 'Send'}
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
