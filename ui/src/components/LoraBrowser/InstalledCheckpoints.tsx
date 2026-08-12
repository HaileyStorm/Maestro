import { useState, useEffect, useCallback } from 'react'
import { Loader2, Boxes, RefreshCw, ArrowUpCircle, Cpu } from 'lucide-react'
import { fetchInstalledCheckpoints, checkCheckpointUpdates } from '../../api/client'
import type { InstalledCheckpoint } from '../../api/client'

interface Props {
  // Open the CivitAI detail for a model so the user can re-import a new version.
  onSelectModel: (modelId: number) => void
}

type CheckpointError = 'load' | 'check'

export function InstalledCheckpoints({ onSelectModel }: Props) {
  const [items, setItems] = useState<InstalledCheckpoint[]>([])
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)
  const [lastCheck, setLastCheck] = useState<string | null>(null)
  const [error, setError] = useState<CheckpointError | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetchInstalledCheckpoints()
      setItems(r.checkpoints)
      setLastCheck(r.manifest_last_check_at)
    } catch {
      setError('load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleCheck = useCallback(async () => {
    if (checking) return
    setChecking(true)
    setError(null)
    try {
      await checkCheckpointUpdates(true) // force: bypass 24h staleness window
      await load()
    } catch {
      setError('check')
    } finally {
      setChecking(false)
    }
  }, [checking, load])

  const updatableCount = items.filter(c => c.update_status === 'available').length

  return (
    <div className="h-full overflow-y-auto p-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2 mb-4">
        <span className="text-xs text-text-muted">
          {items.length} imported checkpoint{items.length === 1 ? '' : 's'}
          {updatableCount > 0 && (
            <span className="ml-2 text-indicator-warning">{updatableCount} update{updatableCount === 1 ? '' : 's'} available</span>
          )}
        </span>
        {lastCheck && (
          <span className="text-[10px] text-text-muted">checked {new Date(lastCheck).toLocaleDateString()}</span>
        )}
        <button
          onClick={handleCheck}
          disabled={checking || items.length === 0}
          className="ml-auto flex min-h-11 items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-text-secondary transition-colors hover:border-border-light hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0"
          title="Check CivitAI for newer checkpoint versions"
        >
          {checking ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Check
        </button>
      </div>

      {!loading && error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
          <p>
            {error === 'load'
              ? 'Maestro could not load the imported checkpoints.'
              : 'Maestro could not check CivitAI for checkpoint updates.'}
          </p>
          <button
            type="button"
            onClick={() => { if (error === 'load') void load(); else void handleCheck() }}
            className="mt-2 min-h-11 rounded border border-red-400/40 px-3 text-xs font-medium text-red-100 transition-colors hover:bg-red-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0"
          >
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 size={24} className="animate-spin text-accent-blue" />
        </div>
      ) : error === 'load' && items.length === 0 ? null : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-text-muted">
          <Boxes size={32} className="mb-3 opacity-50" />
          <p className="text-sm">No checkpoints imported yet</p>
          <p className="text-xs mt-1">Switch to the search grid and import one from CivitAI</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-3">
          {items.map(c => {
            const clickable = !!c.civitai_model_id
            return (
              <button
                key={c.model_type}
                onClick={() => { if (c.civitai_model_id) onSelectModel(c.civitai_model_id) }}
                disabled={!clickable}
                title={c.update_status === 'available'
                  ? 'Update available — open to re-import the latest version'
                  : (clickable ? 'Open on CivitAI to re-import' : undefined)}
                className={`relative rounded-lg border overflow-hidden bg-bg-tertiary text-left transition-all ${
                  clickable ? 'border-border hover:border-accent-blue cursor-pointer group' : 'border-border/50 opacity-75'
                }`}
              >
                <div className="aspect-[3/4] bg-bg-active overflow-hidden">
                  {c.preview_url ? (
                    c.preview_url.endsWith('.mp4') || c.preview_url.endsWith('.webm') ? (
                      <video src={c.preview_url} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" muted loop autoPlay playsInline />
                    ) : (
                      <img src={c.preview_url} alt={c.name} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" loading="lazy" referrerPolicy="no-referrer" />
                    )
                  ) : (
                    <div className="w-full h-full flex flex-col items-center justify-center text-text-muted gap-1">
                      <Boxes size={20} className="opacity-30" />
                      <span className="text-[9px] opacity-40">No preview</span>
                    </div>
                  )}
                </div>
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 via-black/50 to-transparent p-2 pt-6">
                  <div className="text-xs font-medium text-white truncate">{c.name || c.model_type}</div>
                  <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-black/60 text-white/80">{c.architecture}</span>
                    {c.auto_quantize && (
                      <span className="flex items-center gap-0.5 text-[9px] text-white/60" title="Loads as int8 (optimized VRAM)">
                        <Cpu size={8} /> int8
                      </span>
                    )}
                    {c.base_model && <span className="text-[9px] text-white/50">{c.base_model}</span>}
                  </div>
                </div>
                {c.update_status === 'available' && (
                  <div className="absolute top-1.5 left-1.5">
                    <span
                      className="flex items-center gap-0.5 text-[8px] px-1 py-0.5 rounded bg-amber-500/90 text-white font-medium shadow-sm"
                      title={c.latest_published_at
                        ? `Update available — published ${new Date(c.latest_published_at).toLocaleDateString()}`
                        : 'Update available on CivitAI'}
                    >
                      <ArrowUpCircle size={9} />
                      UPDATE
                    </span>
                  </div>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
