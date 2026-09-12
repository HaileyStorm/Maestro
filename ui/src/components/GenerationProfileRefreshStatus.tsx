export function GenerationProfileRefreshStatus({ error, busy, onRetry }: {
  error: string | null
  busy: boolean
  onRetry: () => void
}) {
  if (!error) return null
  return (
    <div role="status" className="mt-2 flex items-center justify-between gap-2 text-[10px] text-red-400">
      <span>{error}</span>
      <button
        type="button"
        onClick={onRetry}
        disabled={busy}
        className="mobile-control-target rounded border border-border px-2 py-1 text-text-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"
      >
        Retry
      </button>
    </div>
  )
}
