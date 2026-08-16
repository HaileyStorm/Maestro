import { useState, useCallback, useEffect, useRef } from 'react'
import { ChevronDown, ChevronRight, RotateCcw, Check, Download, Trash2, Cpu, RefreshCw, Loader2, FolderOpen, Plus, HardDrive } from 'lucide-react'
import type { ModelFolderCandidate } from '../../types'
import { useStore, getFamiliesForMode, getModelsForFamily } from '../../stores/useStore'
import * as api from '../../api/client'
import type { GenerationMode } from '../../types'
import { FAMILIES, resolveVariant, onOsThemeChange, type FamilyId, type ThemeMode } from '../../lib/theme'
import { DOWNLOAD_REFRESH_EVENT, POLL_INTERVAL_MS, useVisibilityPolling } from '../../lib/useVisibilityPolling'

const profileLabels: Record<string, string> = {
  '1': 'Profile 1: High RAM and GPU memory',
  '2': 'Profile 2: High RAM, lower GPU memory',
  '3': 'Profile 3: Lower RAM, high GPU memory',
  '3.5': 'Profile 3.5: Minimal RAM, high GPU memory',
  '4': 'Profile 4: Lower RAM and GPU memory',
  '4.5': 'Profile 4.5: Lower memory (saves about 1 GB)',
  '5': 'Profile 5: Minimal RAM and lower GPU memory',
}

const quantizationOptions = [
  { value: 'int8', label: 'INT8' },
  { value: 'fp8', label: 'FP8' },
  { value: 'bf16', label: 'BF16' },
]

const vaeOptions = [
  { value: 0, label: 'Auto' },
  { value: 1, label: 'Full (Fast, High VRAM)' },
  { value: 2, label: 'Medium Tiling' },
  { value: 3, label: 'Aggressive Tiling (Low VRAM)' },
]

const compileOptions = [
  { value: '', label: 'None' },
  { value: 'transformer', label: 'Transformer' },
]

const videoCodecOptions = [
  { value: 'libx264_8', label: 'H.264 Quality 8' },
  { value: 'libx264_10', label: 'H.264 Quality 10' },
  { value: 'libx264_lossless', label: 'H.264 Lossless' },
  { value: 'libx265_8', label: 'H.265 CRF 8' },
  { value: 'libx265_28', label: 'H.265 CRF 28 (Fast)' },
]

const imageCodecOptions = [
  { value: 'jpeg_95', label: 'JPEG 95%' },
  { value: 'jpeg_85', label: 'JPEG 85%' },
  { value: 'jpeg_70', label: 'JPEG 70%' },
  { value: 'png', label: 'PNG (Lossless)' },
  { value: 'webp_95', label: 'WebP 95%' },
  { value: 'webp_85', label: 'WebP 85%' },
  { value: 'webp_lossless', label: 'WebP Lossless' },
]

const MODE_LABELS: { mode: GenerationMode; label: string }[] = [
  { mode: 'image', label: 'Image' },
  { mode: 'video', label: 'Video' },
  { mode: 'audio', label: 'Audio' },
  { mode: 'avatar', label: 'Edit' },
]

// Family collapse state persists so "collapse the families I never use"
// (issue #14) sticks across sessions — unlike the mode groups, which are
// navigational and reset each visit.
const COLLAPSED_FAMILIES_KEY = 'maestro-collapsed-model-families'

function ModelVisibilitySection() {
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const models = useStore(s => s.models)
  const families = useStore(s => s.families)
  const enabledModels = useStore(s => s.enabledModels)
  const toggleModelEnabled = useStore(s => s.toggleModelEnabled)
  const resetEnabledModels = useStore(s => s.resetEnabledModels)
  const setAllModelsEnabled = useStore(s => s.setAllModelsEnabled)
  const setModelsEnabled = useStore(s => s.setModelsEnabled)
  const loadModels = useStore(s => s.loadModels)
  const modelVisibilityFocus = useStore(s => s.modelVisibilityFocus)
  const clearModelVisibilityFocus = useStore(s => s.clearModelVisibilityFocus)
  // Root open by default so the section is discoverable; mode groups
  // (Image/Video/Audio/Edit) start collapsed to keep the list scannable.
  const [open, setOpen] = useState(true)
  const [expandedModes, setExpandedModes] = useState<Set<GenerationMode>>(new Set())
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  // Model pre-downloads in flight (click on the download icon). Byte-level
  // progress shows in the global DownloadStatusBanner; this set only drives
  // the per-row spinner and the completion refresh.
  const [downloading, setDownloading] = useState<Set<string>>(new Set())
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({})
  const sectionRef = useRef<HTMLDivElement>(null)

  // Poll download status while any model download is in flight. Also runs
  // once on mount so a download started before the drawer was closed and
  // reopened picks its spinner back up.
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const { downloads } = await api.fetchModelDownloads()
        if (cancelled) return
        const active = new Set<string>()
        const errors: Record<string, string> = {}
        let anyCompleted = false
        for (const [mt, d] of Object.entries(downloads)) {
          if (d.status === 'downloading') active.add(mt)
          else if (d.status === 'failed' && d.error) errors[mt] = d.error
          else if (d.status === 'completed') anyCompleted = true
        }
        setDownloading(prev => {
          if (prev.size === active.size && [...prev].every(mt => active.has(mt))) return prev
          return active
        })
        setDownloadErrors(errors)
        // A download finished since the last poll — refresh so the row
        // flips to the downloaded check mark.
        if (anyCompleted && downloading.size > 0 && active.size < downloading.size) loadModels()
      } catch { /* endpoint unavailable — ignore */ }
    }
    tick()
    if (downloading.size === 0) return
    const interval = setInterval(tick, 2000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [downloading.size, loadModels])

  const handleDownload = useCallback(async (modelType: string) => {
    setDownloadErrors(prev => { const next = { ...prev }; delete next[modelType]; return next })
    setDownloading(prev => new Set(prev).add(modelType))
    try {
      if (!activeWorkspace) throw new Error('Select and unlock a project before downloading models')
      await api.downloadModel(modelType, activeWorkspace)
      window.dispatchEvent(new CustomEvent(DOWNLOAD_REFRESH_EVENT))
    } catch (e) {
      console.error('Download start failed:', e)
      setDownloading(prev => { const next = new Set(prev); next.delete(modelType); return next })
      setDownloadErrors(prev => ({ ...prev, [modelType]: String(e) }))
    }
  }, [activeWorkspace])

  // When the ModelSelector "+N more" hint fires, open this section, expand
  // the requested mode, and scroll it into view — then clear the request.
  useEffect(() => {
    if (!modelVisibilityFocus) return
    setOpen(true)
    setExpandedModes(prev => new Set(prev).add(modelVisibilityFocus))
    requestAnimationFrame(() => sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    clearModelVisibilityFocus()
  }, [modelVisibilityFocus, clearModelVisibilityFocus])

  const toggleMode = (mode: GenerationMode) => {
    setExpandedModes(prev => {
      const next = new Set(prev)
      if (next.has(mode)) next.delete(mode)
      else next.add(mode)
      return next
    })
  }

  // Collapsed family groups, keyed "mode:familyId", persisted (issue #14).
  const [collapsedFamilies, setCollapsedFamilies] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(COLLAPSED_FAMILIES_KEY)
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set()
    } catch { return new Set() }
  })
  const toggleFamily = (key: string) => {
    setCollapsedFamilies(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      try { localStorage.setItem(COLLAPSED_FAMILIES_KEY, JSON.stringify([...next])) } catch { /* ignore */ }
      return next
    })
  }

  const handleDelete = useCallback(async (modelType: string) => {
    if (confirmDelete !== modelType) {
      setConfirmDelete(modelType)
      setTimeout(() => setConfirmDelete(null), 3000)
      return
    }
    setConfirmDelete(null)
    setDeleting(modelType)
    try {
      await api.deleteModel(modelType)
      // Refresh models to update download status
      await loadModels()
    } catch (e) {
      console.error('Delete failed:', e)
    } finally {
      setDeleting(null)
    }
  }, [confirmDelete, loadModels])

  // Group every installed/registered model by generation mode.
  const visibleModels = models
  const legalBlockedModelIds = visibleModels.filter(model => (
    model.availability_status === 'legal_blocked'
    || model.execution_allowed === false
  )).map(model => model.model_type)
  const modelsByMode = new Map<GenerationMode, { familyId: string; familyLabel: string; models: { model_type: string; name: string; is_downloaded?: boolean; architecture?: string; downloadable?: boolean; manual_checkpoint_verified?: boolean; availability_status?: string; execution_allowed?: boolean }[] }[]>()
  for (const { mode } of MODE_LABELS) {
    const modeFamilies = getFamiliesForMode(mode, families)
    const groups: { familyId: string; familyLabel: string; models: { model_type: string; name: string; is_downloaded?: boolean; architecture?: string; downloadable?: boolean; manual_checkpoint_verified?: boolean; availability_status?: string; execution_allowed?: boolean }[] }[] = []
    for (const fam of modeFamilies) {
      const familyModels = getModelsForFamily(fam.id, visibleModels, mode)
      if (familyModels.length > 0) {
        groups.push({
          familyId: fam.id,
          familyLabel: fam.label,
          models: familyModels.map(m => ({ model_type: m.model_type, name: m.name, is_downloaded: m.is_downloaded, architecture: m.architecture, downloadable: m.downloadable, manual_checkpoint_verified: m.manual_checkpoint_verified, availability_status: m.availability_status, execution_allowed: m.execution_allowed })),
        })
      }
    }
    modelsByMode.set(mode, groups)
  }

  const enabledCount = enabledModels.size
  const totalCount = visibleModels.length
  const downloadedCount = visibleModels.filter(m => m.is_downloaded).length

  return (
    <div ref={sectionRef} className="scroll-mt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[11px] text-text-secondary uppercase tracking-wider font-medium hover:text-text-primary transition-colors w-full"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="flex-1 text-left">Enabled Models</span>
        <span className="text-[10px] text-text-muted font-normal normal-case flex items-center gap-1.5">
          {enabledCount}/{totalCount}
          <span className="text-text-muted">|</span>
          <span className="flex items-center gap-0.5">
            <Download size={9} />
            {downloadedCount}
          </span>
        </span>
      </button>

      {open && (
      <div className="mt-3 space-y-3">
      <div className="flex gap-2">
        <button
          onClick={() => {
            resetEnabledModels()
            setModelsEnabled(legalBlockedModelIds, false)
          }}
          className="flex items-center gap-1 px-2 py-1 text-[10px] border border-border rounded text-text-secondary hover:text-text-primary hover:border-border-light transition-colors"
        >
          <RotateCcw size={10} />
          Reset
        </button>
        <button
          onClick={() => setModelsEnabled(
            visibleModels.filter(model => (
              model.availability_status !== 'legal_blocked'
              && model.execution_allowed !== false
            )).map(model => model.model_type),
            true,
          )}
          className="px-2 py-1 text-[10px] border border-border rounded text-text-secondary hover:text-text-primary hover:border-border-light transition-colors"
        >
          All
        </button>
        <button
          onClick={() => setAllModelsEnabled(false)}
          className="px-2 py-1 text-[10px] border border-border rounded text-text-secondary hover:text-text-primary hover:border-border-light transition-colors"
        >
          None
        </button>
      </div>

      {MODE_LABELS.map(({ mode, label }) => {
        const groups = modelsByMode.get(mode) ?? []
        const modeModels = groups.flatMap(g => g.models)
        const modeEnabled = modeModels.filter(m => enabledModels.has(m.model_type)).length
        const modeDownloaded = modeModels.filter(m => m.is_downloaded).length
        const isExpanded = expandedModes.has(mode)

        return (
          <div key={mode}>
            <button
              onClick={() => toggleMode(mode)}
              className="flex items-center gap-1.5 w-full text-left"
            >
              {isExpanded ? <ChevronDown size={11} className="text-text-muted shrink-0" /> : <ChevronRight size={11} className="text-text-muted shrink-0" />}
              <span className="text-xs text-text-primary font-medium">{label}</span>
              <span className="text-[10px] text-text-muted ml-auto">
                {modeEnabled}/{modeModels.length}
                {modeDownloaded > 0 && (
                  <span className="ml-1 text-indicator-success">
                    ({modeDownloaded} <Download size={8} className="inline -mt-0.5" />)
                  </span>
                )}
              </span>
            </button>

            {isExpanded && (
              <div className="mt-1.5 ml-4 space-y-0.5">
                {groups.map(group => {
                  const famKey = `${mode}:${group.familyId}`
                  const famCollapsed = collapsedFamilies.has(famKey)
                  const famEnabled = group.models.filter(m => enabledModels.has(m.model_type)).length
                  const executableModels = group.models.filter(m => (
                    m.availability_status !== 'legal_blocked'
                    && m.execution_allowed !== false
                  ))
                  const executableEnabled = executableModels.filter(m => enabledModels.has(m.model_type)).length
                  const famAllEnabled = executableModels.length > 0 && executableEnabled === executableModels.length
                  // Single-family modes render flat — a header would be noise.
                  const showFamilyHeader = groups.length > 1
                  return (
                  <div key={group.familyId}>
                    {showFamilyHeader && (
                      <div className="flex items-center gap-1.5 mt-2 mb-1">
                        {/* Tri-state family toggle: checked = all enabled,
                            indeterminate = some. Click enables the rest,
                            or disables the whole family when all are on. */}
                        <input
                          type="checkbox"
                          checked={famAllEnabled}
                          disabled={executableModels.length === 0}
                          ref={el => { if (el) el.indeterminate = executableEnabled > 0 && !famAllEnabled }}
                          onChange={() => setModelsEnabled(executableModels.map(m => m.model_type), !famAllEnabled)}
                          className="w-3 h-3 rounded border-border bg-bg-tertiary accent-accent-blue shrink-0"
                          title={executableModels.length === 0 ? `${group.familyLabel} models need separate legal authorization` : famAllEnabled ? `Disable all ${group.familyLabel} models` : `Enable all ${group.familyLabel} models`}
                        />
                        <button
                          onClick={() => toggleFamily(famKey)}
                          className="flex items-center gap-1 flex-1 min-w-0 text-left"
                        >
                          {famCollapsed
                            ? <ChevronRight size={10} className="text-text-muted shrink-0" />
                            : <ChevronDown size={10} className="text-text-muted shrink-0" />}
                          <span className="text-[10px] text-text-muted uppercase tracking-wider truncate">{group.familyLabel}</span>
                          <span className="text-[9px] text-text-muted ml-auto shrink-0 tabular-nums">{famEnabled}/{group.models.length}</span>
                        </button>
                      </div>
                    )}
                    {(!showFamilyHeader || !famCollapsed) && group.models.map(m => (
                      <div
                        key={m.model_type}
                        className="flex items-center gap-2 py-0.5 group"
                      >
                        <label className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={enabledModels.has(m.model_type)}
                            disabled={m.availability_status === 'legal_blocked' || m.execution_allowed === false}
                            onChange={() => toggleModelEnabled(m.model_type)}
                            className="w-3.5 h-3.5 rounded border-border bg-bg-tertiary accent-accent-blue shrink-0"
                          />
                          {/* Download status / click-to-download. preventDefault
                              keeps the label from forwarding the click to the
                              enable checkbox. MMAudio rows are virtual entries
                              with no backend model def, so no button there —
                              their files fetch on first SFX generation. */}
                          {m.availability_status === 'legal_blocked' || m.execution_allowed === false ? (
                            <span className="text-[9px] text-red-300" title="A separate written MiniMax H3 license is required">License required</span>
                          ) : m.is_downloaded ? (
                            <Check size={10} className="text-indicator-success shrink-0" />
                          ) : downloading.has(m.model_type) ? (
                            <Loader2 size={10} className="text-accent-blue shrink-0 animate-spin" />
                          ) : m.architecture === 'mmaudio' ? (
                            <Download size={10} className="text-text-muted shrink-0" />
                          ) : m.downloadable === false ? (
                            <span
                              aria-label={m.manual_checkpoint_verified ? 'Model verified; additional files still needed' : 'Manual model setup required'}
                              title={m.manual_checkpoint_verified ? 'This model is verified, but it still needs additional files before it can run' : 'Open the model selector to review its terms and finish the manual setup'}
                            >
                              <FolderOpen
                                size={10}
                                className="text-amber-300 shrink-0"
                              />
                            </span>
                          ) : (
                            <button
                              onClick={e => { e.preventDefault(); e.stopPropagation(); handleDownload(m.model_type) }}
                              className={`p-0.5 -m-0.5 rounded transition-colors shrink-0 ${
                                downloadErrors[m.model_type]
                                  ? 'text-red-400 hover:text-red-300'
                                  : 'text-text-muted hover:text-accent-blue'
                              }`}
                              title={downloadErrors[m.model_type]
                                ? `Download failed: ${downloadErrors[m.model_type]} — click to retry`
                                : 'Download model files now'}
                            >
                              <Download size={10} />
                            </button>
                          )}
                          <span className={`text-xs truncate ${
                            m.is_downloaded
                              ? 'text-text-primary'
                              : 'text-text-muted group-hover:text-text-secondary'
                          }`}>
                            {m.name}
                          </span>
                        </label>
                        {/* Delete button — only for downloaded models */}
                        {m.is_downloaded && (
                          <button
                            onClick={() => handleDelete(m.model_type)}
                            disabled={deleting === m.model_type}
                            className={`p-0.5 rounded transition-colors shrink-0 ${
                              confirmDelete === m.model_type
                                ? 'bg-red-500/20 text-red-400'
                                : deleting === m.model_type
                                  ? 'text-text-muted cursor-wait'
                                  : 'text-text-muted opacity-0 group-hover:opacity-100 hover:text-red-400'
                            }`}
                            title={confirmDelete === m.model_type ? 'Click again to confirm delete' : 'Delete model files'}
                          >
                            <Trash2 size={11} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
      )}
    </div>
  )
}

function LinkedModelFoldersSection() {
  const systemConfig = useStore(s => s.systemConfig)
  const loadSystemConfig = useStore(s => s.loadSystemConfig)
  const loadModels = useStore(s => s.loadModels)
  const [open, setOpen] = useState(false)
  const [candidates, setCandidates] = useState<ModelFolderCandidate[] | null>(null)
  const [scanning, setScanning] = useState(false)
  const [saving, setSaving] = useState(false)
  const [manualPath, setManualPath] = useState('')
  const [error, setError] = useState<string | null>(null)

  const folders = systemConfig?.model_folders ?? []

  // Direct API call (not the store action) so backend validation errors
  // ("Folder does not exist: ...") surface here instead of being swallowed.
  // Returns success so callers can decide what to reset.
  const save = async (next: string[]): Promise<boolean> => {
    if (saving) return false
    setSaving(true)
    setError(null)
    try {
      await api.updateSystemConfig({ model_folders: next })
      await loadSystemConfig()
      // Applied live server-side — refresh so downloaded badges light up.
      await loadModels()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save folders')
      return false
    } finally {
      setSaving(false)
    }
  }

  const scan = async () => {
    setScanning(true)
    setError(null)
    try {
      const res = await api.scanModelFolders()
      setCandidates(res.candidates)
      if (res.candidates.length === 0) setError('No reusable model folders were found in other Pinokio apps')
    } catch {
      setError('Could not search other Pinokio apps')
    } finally {
      setScanning(false)
    }
  }

  const addFolder = async (path: string) => {
    if (saving) return
    // Tolerate Windows Explorer "Copy as path" quoting.
    const p = path.trim().replace(/^["']+|["']+$/g, '').trim()
    if (!p || folders.includes(p)) return
    const ok = await save([...folders, p])
    // Keep the typed path visible on failure so the user can correct it.
    if (ok) setManualPath('')
  }

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[11px] text-text-secondary uppercase tracking-wider font-medium hover:text-text-primary transition-colors w-full"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="flex-1 text-left">Reuse Model Folders</span>
        <span className="text-[10px] text-text-muted font-normal normal-case">{folders.length} linked</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          <p className="text-[10px] text-text-muted leading-relaxed">
            Reuse model files from another app, such as Wan2GP, instead of downloading them again.
            Maestro only reads linked folders; new downloads still go to Maestro&apos;s own model folder.
          </p>

          {folders.length > 0 && (
            <div className="space-y-1">
              {folders.map(f => (
                <div key={f} className="flex items-center gap-2 group">
                  <FolderOpen size={11} className="text-text-secondary shrink-0" />
                  <span className="flex-1 text-[11px] text-text-primary truncate" title={f}>{f}</span>
                  <button
                    onClick={() => save(folders.filter(x => x !== f))}
                    disabled={saving}
                    className="p-1 rounded text-text-muted opacity-0 group-hover:opacity-100 hover:text-red-400 transition-colors shrink-0"
                    title="Unlink folder (files are not deleted)"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={scan}
              disabled={scanning || saving}
              className="flex items-center gap-1 px-2 py-1 text-[10px] border border-border rounded text-text-secondary hover:text-text-primary hover:border-border-light transition-colors disabled:opacity-50"
            >
              {scanning ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
              Find Pinokio model folders
            </button>
          </div>

          {candidates && candidates.filter(c => !c.linked && !folders.includes(c.path)).length > 0 && (
            <div className="space-y-1">
              {candidates.filter(c => !c.linked && !folders.includes(c.path)).map(c => (
                <div key={c.path} className="flex items-center gap-2">
                  <button
                    onClick={() => addFolder(c.path)}
                    disabled={saving}
                    className="p-1 rounded text-accent-blue hover:text-accent-blue-hover shrink-0 disabled:opacity-50"
                    title={`Link ${c.path}`}
                  >
                    <Plus size={12} />
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-text-primary truncate">{c.app}</div>
                    <div className="text-[10px] text-text-muted truncate" title={c.path}>
                      {c.files} files, {c.folders} folders{c.size_gb > 0 ? `, ~${c.size_gb} GB` : ''}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-1.5">
            <input
              type="text"
              value={manualPath}
              onChange={e => setManualPath(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addFolder(manualPath)}
              placeholder="Or paste a model folder path..."
              className="flex-1 bg-bg-tertiary border border-border rounded px-2 py-1 text-[11px] text-text-primary focus:outline-none focus:border-accent-blue"
            />
            <button
              onClick={() => addFolder(manualPath)}
              disabled={saving || !manualPath.trim()}
              className="px-2 py-1 text-[10px] border border-border rounded text-text-secondary hover:text-text-primary hover:border-border-light transition-colors disabled:opacity-50"
            >
              Add
            </button>
          </div>

          {error && <p className="text-[10px] text-red-400">{error}</p>}
        </div>
      )}
    </div>
  )
}

function SelectField({ label, value, options, onChange }: {
  label: string
  value: string | number
  options: { value: string | number; label: string }[]
  onChange: (val: string) => void
}) {
  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
        {label}
      </label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
      >
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </div>
  )
}

function ThemeSection() {
  const prefs = useStore(s => s.themePrefs)
  const setThemeMode = useStore(s => s.setThemeMode)
  const setThemeFamily = useStore(s => s.setThemeFamily)
  // Re-render when the OS flips its scheme while in auto mode so the
  // swatch and hint track the effective variant.
  const [, setOsTick] = useState(0)
  useEffect(() => onOsThemeChange(() => setOsTick(n => n + 1)), [])

  const family = FAMILIES.find(f => f.id === prefs.family) ?? FAMILIES[0]
  const variant = resolveVariant(prefs)
  // Swatch previews the variant the mode currently resolves to, so
  // toggling Dark/Light/Auto updates the preview immediately.
  const swatch = family[variant].swatch
  const modes: { value: ThemeMode; label: string }[] = [
    { value: 'dark', label: 'Dark' },
    { value: 'light', label: 'Light' },
    { value: 'auto', label: 'Auto' },
  ]

  return (
    <div className="space-y-3">
      <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Appearance</h3>
      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
          Mode
        </label>
        <div className="flex rounded-lg border border-border overflow-hidden">
          {modes.map(m => (
            <button
              key={m.value}
              onClick={() => setThemeMode(m.value)}
              className={`flex-1 px-3 py-1.5 text-xs transition-colors ${
                prefs.mode === m.value
                  ? 'bg-accent-blue text-white'
                  : 'bg-bg-tertiary text-text-secondary hover:bg-bg-hover'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        {prefs.mode === 'auto' && (
          <p className="text-[10px] text-text-muted mt-1.5">
            Follows your system's appearance — currently {variant}.
          </p>
        )}
      </div>
      <div>
        <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
          Theme
        </label>
        <div className="flex items-center gap-2">
          {/* Swatch — three colors stacked horizontally for a quick
              preview of the bg / surface / accent palette of the
              variant currently in effect. */}
          <div className="flex shrink-0 rounded-md overflow-hidden border border-border">
            <div className="w-3 h-7" style={{ background: swatch.bg }} />
            <div className="w-3 h-7" style={{ background: swatch.surface }} />
            <div className="w-3 h-7" style={{ background: swatch.accent }} />
          </div>
          <select
            value={family.id}
            onChange={e => setThemeFamily(e.target.value as FamilyId)}
            className="flex-1 bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            {FAMILIES.map(f => (
              <option key={f.id} value={f.id}>{f.label}</option>
            ))}
          </select>
        </div>
        <p className="text-[10px] text-text-muted mt-1.5">
          {family.description}
        </p>
      </div>
    </div>
  )
}

/**
 * AutoPerformanceCard — top of the Performance section.
 *
 * Shows the user's detected hardware + the recommended profile in
 * plain English. The toggle controls whether the rest of the
 * Performance + Profiles fields are hidden under "Show advanced
 * settings" (auto on) or shown directly (auto off).
 *
 * State sources:
 *   - servicesConfig.auto_performance — the toggle's value
 *     (loaded once at app boot; updated optimistically on toggle)
 *   - GET /api/v1/system-detect — fetched on mount and on Re-detect
 *     click. Returns hardware + recommendation. Always succeeds; on
 *     systems without CUDA it returns a "no GPU detected" payload.
 *
 * Side effects:
 *   - Toggle ON  → POST /api/v1/system-detect/apply (writes recommended
 *                  values to wgp_config.json + sets auto_performance=true)
 *   - Toggle OFF → PUT  /api/v1/services-config { auto_performance: false }
 *                  (preserves current settings; user is now in manual mode)
 *   - Re-detect  → POST /api/v1/system-detect/apply (re-runs detection,
 *                  applies fresh recommendation. Only enabled when auto is on)
 */
function AutoPerformanceCard() {
  const servicesConfig = useStore(s => s.servicesConfig)
  const updateServicesConfig = useStore(s => s.updateServicesConfig)
  const loadServicesConfig = useStore(s => s.loadServicesConfig)
  const loadSystemConfig = useStore(s => s.loadSystemConfig)
  // Detect data lives in the store so the rest of the System panel
  // can read it too (e.g. the VRAM coefficient subtext that shows
  // "Max VRAM target: ~19 GB of 24 GB" using the actual VRAM size).
  const detect = useStore(s => s.systemDetect)
  const loadSystemDetect = useStore(s => s.loadSystemDetect)
  const [loading, setLoading] = useState(!detect)
  const [applying, setApplying] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const autoOn = !!servicesConfig?.auto_performance

  // Initial fetch on mount. We don't refetch when the toggle changes
  // because the hardware detection itself doesn't change — only the
  // applied config does, and that's reflected in systemConfig. If
  // another mount of the panel already loaded detect into the store,
  // skip the fetch.
  useEffect(() => {
    if (detect) {
      setLoading(false)
      return
    }
    let alive = true
    loadSystemDetect().finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [detect, loadSystemDetect])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 4000)
  }

  // Toggle ON → call apply endpoint (writes recommendation + sets
  // services.auto_performance=true server-side). Refresh both configs
  // to pick up the new state.
  const handleToggleOn = useCallback(async () => {
    setApplying(true)
    try {
      const res = await api.applySystemDetect()
      await Promise.all([loadServicesConfig(), loadSystemConfig()])
      if (res.profile_changed) {
        showToast('Auto-tune applied — the new settings will be used the next time a model loads')
      } else {
        showToast('Auto-tune applied')
      }
    } catch (e) {
      console.error('apply failed:', e)
      showToast('Failed to apply auto-tune')
    } finally {
      setApplying(false)
    }
  }, [loadServicesConfig, loadSystemConfig])

  // Toggle OFF → just flip the flag. Preserves current settings so
  // the user has the same config they were just running, just no
  // longer being auto-managed.
  const handleToggleOff = useCallback(async () => {
    setApplying(true)
    try {
      await updateServicesConfig({ auto_performance: false })
      showToast('Auto-tune is off. Your current settings are unchanged and can now be edited.')
    } catch (e) {
      console.error('toggle off failed:', e)
    } finally {
      setApplying(false)
    }
  }, [updateServicesConfig])

  // Re-detect = same as toggle-on, just runs the apply again. Useful
  // after a hardware change (new GPU, more RAM) or driver update.
  const handleRedetect = useCallback(async () => {
    setApplying(true)
    try {
      const res = await api.applySystemDetect()
      // Refresh detect payload too via the store — hardware itself
      // may have changed (e.g. user upgraded GPU). Also refresh
      // services + system configs so the rest of the panel reflects
      // the newly-applied recommendation.
      await Promise.all([loadSystemDetect(), loadServicesConfig(), loadSystemConfig()])
      if (res.profile_changed) {
        showToast('Hardware checked — the new settings will be used the next time a model loads')
      } else {
        showToast('Hardware checked — your settings were already up to date')
      }
    } catch (e) {
      console.error('re-detect failed:', e)
      showToast('Failed to re-detect hardware')
    } finally {
      setApplying(false)
    }
  }, [loadServicesConfig, loadSystemConfig, loadSystemDetect])

  if (loading) {
    return (
      <div className="space-y-3">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Performance</h3>
        <div className="rounded-lg bg-bg-tertiary border border-border p-3 text-xs text-text-muted flex items-center gap-2">
          <Loader2 size={12} className="animate-spin" /> Detecting hardware...
        </div>
      </div>
    )
  }

  const hw = detect?.hardware
  const rec = detect?.recommended
  const cudaOK = !!hw?.cuda_available

  return (
    <div className="space-y-3">
      <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Performance</h3>

      <div className="rounded-lg bg-bg-tertiary border border-border p-3 space-y-2.5">
        {/* Hardware readout — GPU name, VRAM, RAM */}
        <div className="flex items-start gap-2">
          <Cpu size={16} className="text-text-secondary shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <div className="text-sm text-text-primary truncate" title={hw?.gpu_name || ''}>
              {cudaOK ? hw!.gpu_name : 'No CUDA GPU detected'}
            </div>
            <div className="text-[11px] text-text-muted">
              {cudaOK ? `${hw!.gpu_vram_gb} GB VRAM · ${hw!.ram_gb} GB RAM` : `${hw?.ram_gb ?? 0} GB RAM`}
            </div>
          </div>
        </div>

        {/* Profile readout — only meaningful when auto is on, but always
            visible so users can see what auto WOULD pick before flipping
            the toggle. */}
        {rec && (
          <div className="text-[11px] text-text-secondary leading-snug pl-6" title={rec._recommendation_reason}>
            {autoOn ? '✨ ' : ''}{rec._recommendation_label}
          </div>
        )}

        {/* Toggle + Re-detect button row */}
        <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-border/40">
          <div className="flex items-center gap-2">
            <button
              onClick={autoOn ? handleToggleOff : handleToggleOn}
              disabled={applying}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                autoOn ? 'bg-amber-500' : 'bg-bg-tertiary border border-border'
              } ${applying ? 'opacity-50 cursor-wait' : ''}`}
              title={autoOn ? 'Auto-tune is on — click to take manual control' : 'Auto-tune is off — click to enable'}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white border border-border transition-transform ${
                  autoOn ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span className="text-xs text-text-secondary">
              Auto-tune {autoOn ? 'on' : 'off'}
            </span>
          </div>
          {/* Re-detect only relevant in auto mode. In manual mode, a
              "Reset to auto-tune" affordance lives at the bottom of the
              advanced section instead, so it's not duplicated. */}
          {autoOn && cudaOK && (
            <button
              onClick={handleRedetect}
              disabled={applying}
              className="text-[11px] text-text-secondary hover:text-text-primary flex items-center gap-1 disabled:opacity-50"
              title="Check your hardware again after an upgrade or driver update"
            >
              <RefreshCw size={11} className={applying ? 'animate-spin' : ''} /> Check again
            </button>
          )}
        </div>

        {/* Toast — feedback after toggle / re-detect */}
        {toast && (
          <div className="text-[10px] text-indicator-warning bg-amber-500/10 border border-amber-500/30 rounded px-2 py-1.5">
            {toast}
          </div>
        )}
      </div>
    </div>
  )
}

function formatResearchTime(value: string | null | undefined): string {
  if (!value) return 'Not yet'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? 'Unavailable' : parsed.toLocaleString()
}

const SUGGESTION_COPY_REPLACEMENTS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bdurable plan\b/gi, 'saved plan'],
  [/\bdurable plans\b/gi, 'saved plans'],
  [/\bevidence ledger\b/gi, 'review history'],
  [/\bevidence ledgers\b/gi, 'review histories'],
  [/\bstructural proof\b/gi, 'consistency check'],
  [/\bimplementation[- ]eligible finding\b/gi, 'idea ready for a code update'],
  [/\bimplementation[- ]eligible findings\b/gi, 'ideas ready for a code update'],
  [/\bagent note\b/gi, 'working note'],
  [/\bagent notes\b/gi, 'working notes'],
  [/\bruntime error\b/gi, 'service error'],
  [/\bruntime errors\b/gi, 'service errors'],
]

function presentSuggestionText(value: string, fallback: string): { display: string; technical: string | null } {
  const original = value.trim()
  let presented = original
    .replace(/^\s*(?:agent|research)\s+(?:note|finding)\s*[:—-]\s*/i, '')
    .replace(/^\s{0,3}#{1,6}\s+/, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  for (const [pattern, replacement] of SUGGESTION_COPY_REPLACEMENTS) {
    presented = presented.replace(pattern, replacement)
  }
  return {
    display: presented || fallback,
    technical: presented && presented !== original ? original : null,
  }
}

function presentResearchError(value: string | null | undefined): { summary: string; technical: string } | null {
  const technical = value?.trim()
  if (!technical) return null
  const normalized = technical.toLowerCase()
  let summary = 'The improvement task stopped unexpectedly. Try again, or open the technical details below.'
  if (/\b(?:busy|already active|already running)\b/.test(normalized)) {
    summary = 'Another improvement task is already running. Wait for it to finish, then try again.'
  } else if (/\b(?:nonce|capability|expired)\b/.test(normalized)) {
    summary = 'This action expired before it could start. Try it again.'
  } else if (/\b(?:not ready|eligible|threshold)\b/.test(normalized)) {
    summary = 'No ideas are ready for a code update yet.'
  } else if (/\b(?:timeout|timed out)\b/.test(normalized)) {
    summary = 'The improvement task took too long and stopped. Try again.'
  } else if (/\b(?:disabled|unavailable|not configured|could not start)\b/.test(normalized)) {
    summary = 'Improvement checks are unavailable right now. Try again later.'
  }
  return { summary, technical }
}

function formatImprovementStatus(value: api.ResearchImplementationStatus['status']): string {
  const labels: Record<api.ResearchImplementationStatus['status'], string> = {
    never_run: 'Not yet',
    running: 'In progress',
    completed: 'Completed',
    failed: 'Needs attention',
    cancelled: 'Cancelled',
    interrupted_requires_review: 'Paused for review',
  }
  return labels[value]
}

function presentResearchDisclosure(value: string | null | undefined): string {
  if (!value) {
    return 'Privacy: this check uses only public model, tool, and LoRA catalog details. Project names, prompts, jobs, media, and logs are never sent.'
  }
  return `Privacy: ${value}`
    .replace('Only public model, tool, and LoRA catalog metadata is sent', 'only public model, tool, and LoRA catalog details are sent')
    .replace("If DeepSeek's mechanical gate fails or its circuit opens, isolated GPT-5.6 Luna is the only fallback.", 'If DeepSeek cannot be used, the only fallback is an isolated GPT-5.6 Luna session.')
    .replace('Maestro never sends project names, prompts, jobs, media, or logs.', 'Project names, prompts, jobs, media, and logs are never sent as part of this check.')
}

function ResearchCard() {
  const [status, setStatus] = useState<api.ResearchStatus | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [action, setAction] = useState<'research' | 'implementation' | null>(null)
  const [confirmImplementation, setConfirmImplementation] = useState(false)
  const requestSequence = useRef(0)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++requestSequence.current
    try {
      const next = await api.fetchResearchStatus(signal)
      if (sequence !== requestSequence.current) return
      setStatus(next)
      setPollError(null)
    } catch (error) {
      if (signal?.aborted || sequence !== requestSequence.current) return
      setPollError(error instanceof Error ? error.message : 'Improvement status is unavailable')
    }
  }, [])

  useVisibilityPolling(refresh, POLL_INTERVAL_MS.researchVisible)

  const runNow = useCallback(async () => {
    setConfirmImplementation(false)
    setAction('research')
    setPollError(null)
    try {
      await api.runResearchNow()
      await refresh()
    } catch (error) {
      setPollError(error instanceof Error ? error.message : 'The improvement check could not start')
    } finally {
      setAction(null)
    }
  }, [refresh])

  const startImplementation = useCallback(async () => {
    if (!status || status.implementation_chunk_count < 1) return
    setConfirmImplementation(false)
    setAction('implementation')
    setPollError(null)
    try {
      // One or two eligible findings may be implemented explicitly; force
      // bypasses only the normal count threshold on the server.
      await api.startResearchImplementation(!status.implementation_ready)
      await refresh()
    } catch (error) {
      setPollError(error instanceof Error ? error.message : 'Maestro could not start applying improvements')
    } finally {
      setAction(null)
    }
  }, [refresh, status])

  const busy = !!status?.research_active || !!status?.implementation_active
  const eligible = status?.implementation_chunk_count ?? 0
  const threshold = status?.readiness_threshold ?? 3
  const recent = (status?.recent_pending ?? []).slice(0, 3)
  const researchError = presentResearchError(pollError || status?.runtime_error)

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Improvement checks</h3>
        <span className={`text-[10px] ${status?.implementation_ready ? 'text-indicator-success' : 'text-text-muted'}`}>
          {eligible} {eligible === 1 ? 'idea' : 'ideas'} ready · {status?.implementation_ready ? 'group ready' : `${Math.max(0, threshold - eligible)} more for a group`}
        </span>
      </div>

      <div className="rounded-lg bg-bg-tertiary border border-border p-3 space-y-2.5">
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
          <span className="text-text-muted">Potential ideas left to check</span>
          <span className="text-text-secondary text-right">{status?.queued_candidate_count ?? 0}</span>
          <span className="text-text-muted">Next check</span>
          <span className="text-text-secondary text-right">{formatResearchTime(status?.next_due_at)}</span>
          <span className="text-text-muted">Last check</span>
          <span className="text-text-secondary text-right">{formatResearchTime(status?.last_cycle_at)}</span>
          <span className="text-text-muted">Last update</span>
          <span className="text-text-secondary text-right">
            {status?.last_implementation_run.status === 'never_run'
              ? 'Not yet'
              : status?.last_implementation_run
                ? `${formatImprovementStatus(status.last_implementation_run.status)} · ${formatResearchTime(
                    status.last_implementation_run.active
                      ? status.last_implementation_run.started_at
                      : status.last_implementation_run.completed_at,
                  )}`
                : 'Unavailable'}
          </span>
        </div>

        {busy && (
          <div className="text-[10px] text-indicator-warning">
            {status?.implementation_active ? 'Applying improvements…' : 'Checking sources for new ideas…'}
          </div>
        )}

        {recent.length > 0 && (
          <div className="space-y-1 border-t border-border/40 pt-2">
            <div className="text-[9px] uppercase tracking-wider text-text-muted">Recent ideas</div>
            {recent.map(suggestion => {
              const title = presentSuggestionText(suggestion.title, 'Improvement idea')
              const summary = presentSuggestionText(suggestion.summary, '')
              const hasTechnicalDetails = title.technical !== null || summary.technical !== null
              return (
                <div key={suggestion.finding_id} className="text-[10px] leading-snug">
                  <span className="text-text-secondary">{title.display}</span>
                  {summary.display && <span className="text-text-muted"> — {summary.display}</span>}
                  {hasTechnicalDetails && (
                    <details className="mt-0.5 text-[9px] text-text-muted">
                      <summary className="cursor-pointer">Original technical note</summary>
                      <div className="mt-0.5 whitespace-pre-wrap break-words">
                        {title.technical && <div>Title: {title.technical}</div>}
                        {summary.technical && <div>Summary: {summary.technical}</div>}
                      </div>
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        )}

        <p className="text-[9px] leading-snug text-text-muted border-t border-border/40 pt-2">
          {presentResearchDisclosure(status?.disclosure)}
        </p>

        {researchError && (
          <div className="text-[10px] text-indicator-warning">
            <div>{researchError.summary}</div>
            <details className="mt-1 text-[9px] text-text-muted">
              <summary className="cursor-pointer">Technical details</summary>
              <div className="mt-0.5 whitespace-pre-wrap break-words">{researchError.technical}</div>
            </details>
          </div>
        )}

        <p className="text-[9px] leading-snug text-text-muted">
          Starting a code update can change Maestro&apos;s code and settings on this computer.
        </p>

        <div className="flex gap-2">
          <button
            onClick={runNow}
            disabled={busy || action !== null}
            className="flex-1 px-2 py-1.5 text-[10px] rounded border border-border text-text-secondary hover:text-text-primary hover:border-border-light disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {action === 'research' ? 'Starting…' : 'Check for ideas now'}
          </button>
          <button
            onClick={() => confirmImplementation ? void startImplementation() : setConfirmImplementation(true)}
            disabled={busy || eligible < 1 || action !== null}
            className={`flex-1 px-2 py-1.5 text-[10px] rounded border disabled:opacity-50 disabled:cursor-not-allowed ${
              confirmImplementation
                ? 'border-amber-500/60 bg-amber-500/10 text-indicator-warning hover:bg-amber-500/15'
                : 'border-border text-text-secondary hover:text-text-primary hover:border-border-light'
            }`}
          >
            {action === 'implementation' ? 'Starting…' : confirmImplementation ? 'Confirm code update' : 'Start code update'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function SystemSettingsPanel() {
  const systemConfig = useStore(s => s.systemConfig)
  const systemConfigLoading = useStore(s => s.systemConfigLoading)
  const updateConfig = useStore(s => s.updateSystemConfig)
  const servicesConfig = useStore(s => s.servicesConfig)
  const updateServicesConfig = useStore(s => s.updateServicesConfig)
  // Detected VRAM is used in the VRAM coefficient subtext (see below)
  // so the "Max VRAM target: ~X GB of Y GB" line shows real numbers
  // instead of a hardcoded 24 GB. AutoPerformanceCard populates this
  // on mount; if it hasn't fired yet (e.g. user opened Settings →
  // System extremely fast), we fall back to 24 GB.
  const systemDetect = useStore(s => s.systemDetect)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  if (systemConfigLoading && !systemConfig) {
    return <div className="text-xs text-text-muted py-4 text-center">Loading system settings...</div>
  }

  if (!systemConfig) {
    return <div className="text-xs text-text-muted py-4 text-center">Failed to load system settings</div>
  }

  const attentionOptions = (systemConfig.attention_modes_available || ['auto', 'sdpa']).map(m => ({
    value: m,
    label: m === 'auto' ? 'Auto' : m === 'sdpa' ? 'SDPA' : m.charAt(0).toUpperCase() + m.slice(1),
  }))

  const profileOptions = Object.entries(profileLabels).map(([k, label]) => ({
    value: k,
    label,
  }))

  const autoOn = !!servicesConfig?.auto_performance

  // Wraps updateConfig so that any change to a Performance / Profile
  // field while auto is ON automatically flips auto OFF. Otherwise
  // the user would think they're editing manually but the auto card
  // would keep claiming "auto-tuned" — confusing and a lie.
  // Auto stays on if the user is just toggling fields *while already
  // in manual mode* (autoOn === false), which is the normal case.
  const updateConfigWithAutoFlip = (partial: Partial<typeof systemConfig>) => {
    updateConfig(partial)
    if (autoOn) {
      updateServicesConfig({ auto_performance: false })
    }
  }

  // Render the Performance + Profiles fields. Used both inside the
  // advanced expander (when auto is on) and inline (when auto is off).
  const renderAdvancedFields = () => (
    <>
      {/* Performance */}
      <div className="space-y-4">
        {!autoOn && (
          <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Performance</h3>
        )}

        <SelectField
          label="Attention Mode"
          value={systemConfig.attention_mode}
          options={attentionOptions}
          onChange={val => updateConfigWithAutoFlip({ attention_mode: val })}
        />

        <div>
          <SelectField
            label="Model Precision"
            value={systemConfig.transformer_quantization}
            options={quantizationOptions}
            onChange={val => updateConfigWithAutoFlip({ transformer_quantization: val })}
          />
          {/* FP8 footgun: many models ship only BF16 + INT8 files (no
              FP8 variant). Picking FP8 here silently falls back to
              INT8 for those models — UI says FP8 but you get INT8
              precision. The model name itself is the only place
              that tells you what's actually loaded — e.g. picking
              "LTX-2.3 Distilled FP8 22B" in the model selector loads
              FP8 regardless of this setting. Worth a hint so users
              don't think "I selected FP8 but performance/quality
              feels like INT8 — must be broken." */}
          {systemConfig.transformer_quantization === 'fp8' && (
            <p className="text-[10px] text-indicator-warning mt-1">
              Some models do not include FP8 files, so Maestro will use INT8 for them instead.
              To ensure FP8 is used, choose a model with “FP8” in its name, such as “LTX-2.3 Distilled FP8 22B.”
            </p>
          )}
        </div>

        <SelectField
          label="VAE Tiling"
          value={systemConfig.vae_config}
          options={vaeOptions}
          onChange={val => updateConfigWithAutoFlip({ vae_config: Number(val) })}
        />

        <SelectField
          label="Model Compilation"
          value={systemConfig.compile}
          options={compileOptions}
          onChange={val => updateConfigWithAutoFlip({ compile: val })}
        />
      </div>

      <hr className="border-border" />

      {/* Profiles */}
      <div className="space-y-4">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Memory profiles</h3>

        <SelectField
          label="Video Profile"
          value={String(systemConfig.video_profile)}
          options={profileOptions}
          onChange={val => updateConfigWithAutoFlip({ video_profile: parseFloat(val) })}
        />

        <SelectField
          label="Image Profile"
          value={String(systemConfig.image_profile)}
          options={profileOptions}
          onChange={val => updateConfigWithAutoFlip({ image_profile: parseFloat(val) })}
        />

        <SelectField
          label="Audio Profile"
          value={String(systemConfig.audio_profile)}
          options={profileOptions}
          onChange={val => updateConfigWithAutoFlip({ audio_profile: parseFloat(val) })}
        />

        <p className="text-[10px] text-text-muted">
          Changes are used the next time a model loads.
        </p>

        {/* VRAM Safety Coefficient */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[11px] text-text-muted uppercase tracking-wider">GPU memory limit</label>
            <span className="text-xs text-text-secondary">{(systemConfig.vram_safety_coefficient ?? 0.8).toFixed(2)}</span>
          </div>
          <input
            type="range" min={0.5} max={0.95} step={0.05}
            value={systemConfig.vram_safety_coefficient ?? 0.8}
            onChange={e => updateConfigWithAutoFlip({ vram_safety_coefficient: parseFloat(e.target.value) })}
            className="w-full"
          />
          <div className="flex justify-between text-[8px] text-text-muted mt-0.5 px-0.5">
            <span>0.50 (more free memory)</span>
            <span>0.80 (default)</span>
            <span>0.95 (use more memory)</span>
          </div>
          <p className="text-[10px] text-text-muted mt-1">
            {(() => {
              // Use detected VRAM when available so the math is honest.
              // Falls back to 24 GB if detection hasn't completed yet
              // — the auto card populates the store on mount.
              const totalVram = systemDetect?.hardware?.gpu_vram_gb ?? 24
              const coef = systemConfig.vram_safety_coefficient ?? 0.8
              return `Maestro may use about ${(totalVram * coef).toFixed(1)} GB of ${totalVram} GB of GPU memory.`
            })()} A lower limit leaves more room for demanding steps, including long videos. Changes are used the next time a model loads.
          </p>
        </div>
      </div>
    </>
  )

  return (
    <div className="space-y-5">
      <ThemeSection />

      <hr className="border-border" />

      {/* Storage Manager — usage analytics + duplicate reclaim */}
      <button
        type="button"
        onClick={() => useStore.getState().setStorageDashboardOpen(true)}
        className="flex min-h-11 w-full items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-text-secondary transition-colors hover:border-border-light hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
        aria-haspopup="dialog"
        aria-controls="storage-manager-dialog"
      >
        <HardDrive size={13} className="text-accent-blue" />
        <span className="flex-1 text-left">Manage storage</span>
        <span className="text-[10px] text-text-muted">view space and remove extra files</span>
      </button>

      <hr className="border-border" />

      <ResearchCard />

      <hr className="border-border" />

      {/* Model Visibility — moved to top */}
      <ModelVisibilitySection />

      <hr className="border-border" />

      {/* Linked model folders — reuse checkpoints from other installs */}
      <LinkedModelFoldersSection />

      <hr className="border-border" />

      {/* Auto-tune card always visible. The fields below are
          conditionally hidden based on autoOn. */}
      <AutoPerformanceCard />

      {/* Auto ON: collapse the advanced fields under an expander.
          The expander defaults closed — power users who want to peek
          at what auto picked can open it without leaving the page. */}
      {autoOn ? (
        <div>
          <button
            onClick={() => setAdvancedOpen(o => !o)}
            className="flex items-center gap-1 text-[11px] text-text-muted hover:text-text-secondary transition-colors"
          >
            {advancedOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            {advancedOpen ? 'Hide' : 'Show'} advanced settings
          </button>
          {advancedOpen && (
            <div className="mt-4 space-y-5 pl-2 border-l-2 border-border/30">
              {renderAdvancedFields()}
            </div>
          )}
        </div>
      ) : (
        // Auto OFF: show fields directly + a "Reset to auto-tune"
        // affordance below them. The Reset button just toggles auto
        // back ON, which triggers the apply endpoint via the card.
        <>
          {renderAdvancedFields()}
        </>
      )}

      <hr className="border-border" />

      {/* Output Codecs */}
      <div className="space-y-4">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Output Codecs</h3>

        <SelectField
          label="Video Codec"
          value={systemConfig.video_output_codec}
          options={videoCodecOptions}
          onChange={val => updateConfig({ video_output_codec: val })}
        />

        <SelectField
          label="Image Codec"
          value={systemConfig.image_output_codec}
          options={imageCodecOptions}
          onChange={val => updateConfig({ image_output_codec: val })}
        />
      </div>
    </div>
  )
}
