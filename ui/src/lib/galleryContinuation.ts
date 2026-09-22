export function retainContinuationPreview(
  url: string,
  references: () => readonly (string | undefined)[],
  subscribe: (listener: () => void) => () => void,
): void {
  let released = false
  let unsubscribe = () => {}
  const releaseUnused = () => {
    if (released || references().includes(url)) return
    released = true
    unsubscribe()
    URL.revokeObjectURL(url)
  }
  unsubscribe = subscribe(releaseUnused)
  releaseUnused()
}

export function readContinuationDuration(url: string, signal: AbortSignal): Promise<number> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video')
    const finish = (error?: Error) => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      video.onloadedmetadata = null
      video.onerror = null
      const duration = video.duration
      video.removeAttribute('src')
      video.load()
      if (error) reject(error)
      else if (!Number.isFinite(duration) || duration <= 0) reject(new Error('The source video has no readable duration.'))
      else resolve(duration)
    }
    const abort = () => finish(new Error('Video selection changed.'))
    const timer = setTimeout(() => finish(new Error('Reading the video took too long. Try again.')), 15000)
    if (signal.aborted) { abort(); return }
    signal.addEventListener('abort', abort, { once: true })
    video.onloadedmetadata = () => finish()
    video.onerror = () => finish(new Error('The source video could not be read.'))
    video.preload = 'metadata'
    video.src = url
    video.load()
  })
}

export async function prepareGalleryContinuation(options: {
  sourceUrl: string
  filename: string
  signal: AbortSignal
  isCurrent: () => boolean
  upload: (file: File) => Promise<{ path: string }>
  commit: (file: File, path: string, url: string, duration: number) => void
  readDuration?: typeof readContinuationDuration
}): Promise<boolean> {
  let preview = ''
  let committed = false
  const current = () => !options.signal.aborted && options.isCurrent()
  try {
    if (!current()) return false
    const response = await fetch(options.sourceUrl, { signal: options.signal })
    if (!response.ok) throw new Error('The source video could not be downloaded. Try again.')
    const blob = await response.blob()
    if (!current()) return false
    const file = new File([blob], options.filename, { type: blob.type || 'video/mp4' })
    preview = URL.createObjectURL(file)
    const duration = await (options.readDuration ?? readContinuationDuration)(preview, options.signal)
    if (!current()) return false
    const uploaded = await options.upload(file)
    if (!current()) return false
    options.commit(file, uploaded.path, preview, duration)
    committed = true
    return true
  } catch (error) {
    if (!current()) return false
    throw error
  } finally {
    if (preview && !committed) URL.revokeObjectURL(preview)
  }
}
