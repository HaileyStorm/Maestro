import { useCallback, useRef, useState } from 'react'
import { Upload, X } from 'lucide-react'

// Shared by InputsPanel's one mounted media picker. Keeping the controller in
// this upload primitive avoids reintroducing detached-input variants.
export interface PendingFileSelection {
  current: ((files: File[]) => void) | null
}

// eslint-disable-next-line react-refresh/only-export-components
export function cancelFileSelection(
  input: Pick<HTMLInputElement, 'value'>,
  pending: PendingFileSelection,
): void {
  input.value = ''
  pending.current = null
}

// eslint-disable-next-line react-refresh/only-export-components
export function consumeFileSelection(
  input: Pick<HTMLInputElement, 'files' | 'value'>,
  pending: PendingFileSelection,
): void {
  const files = Array.from(input.files || [])
  const onFiles = pending.current
  cancelFileSelection(input, pending)
  if (files.length > 0) onFiles?.(files)
}

function acceptsFile(file: File, accept: string): boolean {
  const name = file.name.toLowerCase()
  const mime = file.type.toLowerCase()
  return accept.split(',').some(rawToken => {
    const token = rawToken.trim().toLowerCase()
    if (!token) return false
    if (token.startsWith('.')) return name.endsWith(token)
    if (token.endsWith('/*')) {
      if (mime.startsWith(token.slice(0, -1))) return true
      if (token === 'image/*') return /\.(png|jpe?g|webp|bmp)$/i.test(name)
      if (token === 'audio/*') return /\.(wav|mp3|flac|ogg|m4a)$/i.test(name)
      if (token === 'video/*') return /\.(mp4|webm|mkv|mov|avi|m4v)$/i.test(name)
      return false
    }
    return mime === token
  })
}

export function FileUploadZone({ label, accept, filename, onFile, onClear, busy = false, disabled = false, error = null }: {
  label: string
  accept: string
  filename: string | null
  onFile: (file: File) => void
  onClear: () => void
  busy?: boolean
  disabled?: boolean
  error?: string | null
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [selectionError, setSelectionError] = useState<string | null>(null)

  const submitFile = useCallback((file: File) => {
    if (!acceptsFile(file, accept)) {
      setSelectionError('Choose a supported file and try again.')
      return
    }
    setSelectionError(null)
    onFile(file)
  }, [accept, onFile])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (busy || disabled) return
    const file = e.dataTransfer.files[0]
    if (file) submitFile(file)
  }, [busy, disabled, submitFile])

  const openPicker = () => {
    if (busy || disabled || !inputRef.current) return
    // Browsers suppress `change` when the same file remains selected. Reset
    // before every open so retrying or clearing and reselecting always works.
    inputRef.current.value = ''
    inputRef.current.click()
  }

  const visibleError = (error || selectionError)?.trim().slice(0, 220) || null

  return (
    <div className="space-y-1" aria-busy={busy}>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        disabled={busy || disabled}
        className="sr-only"
        aria-label={`Choose ${label}`}
        onChange={event => {
          const file = event.currentTarget.files?.[0]
          event.currentTarget.value = ''
          if (file) submitFile(file)
        }}
      />
      {filename ? (
        <div className="flex min-h-11 items-center gap-2 rounded-lg border border-border bg-bg-tertiary px-3 py-2">
          <span className="flex-1 truncate text-xs text-text-primary">{filename}</span>
          <button
            type="button"
            onClick={() => {
              setSelectionError(null)
              onClear()
            }}
            disabled={busy || disabled}
            aria-label={`Remove ${filename}`}
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary disabled:cursor-wait disabled:opacity-50"
          >
            <X size={12} />
          </button>
        </div>
      ) : (
        <button
          type="button"
          disabled={busy || disabled}
          className="flex min-h-11 w-full cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border p-3 text-text-muted transition-colors hover:border-border-light disabled:cursor-wait disabled:opacity-60"
          onDrop={handleDrop}
          onDragOver={e => e.preventDefault()}
          onClick={openPicker}
        >
          <Upload size={14} aria-hidden="true" />
          <span className="text-center text-[10px]">{label}</span>
        </button>
      )}
      {busy && <p role="status" className="text-[9px] text-text-muted">Upload in progress…</p>}
      {visibleError && <p role="alert" className="text-[9px] text-red-300">{visibleError}</p>}
    </div>
  )
}
