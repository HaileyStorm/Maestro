const DB_NAME = 'maestro-thumbnails'
const STORE_NAME = 'thumbnails'
const DB_VERSION = 1

let dbInstance: IDBDatabase | null = null

function openDB(): Promise<IDBDatabase> {
  if (dbInstance) return Promise.resolve(dbInstance)
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE_NAME)
    }
    req.onsuccess = () => {
      dbInstance = req.result
      resolve(req.result)
    }
    req.onerror = () => reject(req.error)
  })
}

export async function getCachedThumbnail(key: string): Promise<string | null> {
  try {
    const db = await openDB()
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const req = tx.objectStore(STORE_NAME).get(key)
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => resolve(null)
    })
  } catch {
    return null
  }
}

export async function setCachedThumbnail(key: string, dataUrl: string): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put(dataUrl, key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // silently fail
  }
}

/** Capture a video frame at the given time and return a data URL. */
function captureVideoFrame(videoUrl: string, signal: AbortSignal, timeSeconds = 0.1): Promise<string> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video')
    video.muted = true
    video.preload = 'auto'

    let settled = false
    const timer: { id?: ReturnType<typeof setTimeout> } = {}
    const cleanup = () => {
      if (timer.id !== undefined) clearTimeout(timer.id)
      signal.removeEventListener('abort', abort)
      video.onloadeddata = null
      video.onseeked = null
      video.onerror = null
      video.pause()
      video.removeAttribute('src')
      video.load()
    }
    const fail = (e: unknown) => {
      if (settled) return
      settled = true
      cleanup()
      reject(e)
    }
    const abort = () => fail(new DOMException('Thumbnail request aborted', 'AbortError'))

    if (signal.aborted) {
      abort()
      return
    }
    signal.addEventListener('abort', abort, { once: true })

    video.onloadeddata = () => {
      video.currentTime = timeSeconds
    }

    video.onseeked = () => {
      if (settled) return
      settled = true
      try {
        const canvas = document.createElement('canvas')
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('no canvas ctx')
        ctx.drawImage(video, 0, 0)
        const dataUrl = canvas.toDataURL('image/webp', 0.7)
        cleanup()
        resolve(dataUrl)
      } catch (e) {
        cleanup()
        reject(e)
      }
    }

    video.onerror = () => fail(new Error('video load failed'))

    timer.id = setTimeout(() => fail(new Error('timeout')), 10000)
    video.src = videoUrl
  })
}

// --- Priority queue: most recently requested items are processed first ---
// This ensures visible thumbnails get captured before off-screen ones.
type ThumbnailConsumer = {
  resolve: (dataUrl: string | null) => void
  signal?: AbortSignal
  abort?: () => void
}

type ThumbnailJob = {
  videoUrl: string
  key: string
  timestamp: number
  consumers: Set<ThumbnailConsumer>
  state: 'queued' | 'active'
  controller: AbortController
}

const queue: ThumbnailJob[] = []
const jobs = new Map<string, ThumbnailJob>()
let processing = false

function resolveConsumer(consumer: ThumbnailConsumer, dataUrl: string | null): void {
  if (consumer.abort && consumer.signal) consumer.signal.removeEventListener('abort', consumer.abort)
  consumer.resolve(dataUrl)
}

function resolveJob(job: ThumbnailJob, dataUrl: string | null): void {
  for (const consumer of job.consumers) resolveConsumer(consumer, dataUrl)
  job.consumers.clear()
}

function cancelConsumer(job: ThumbnailJob, consumer: ThumbnailConsumer): void {
  if (!job.consumers.delete(consumer)) return
  resolveConsumer(consumer, null)
  if (job.consumers.size > 0) return
  if (jobs.get(job.key) === job) jobs.delete(job.key)
  if (job.state === 'active') job.controller.abort()
}

async function processQueue() {
  if (processing) return
  processing = true

  while (queue.length > 0) {
    // Process newest request first (priority = most recently visible)
    queue.sort((a, b) => b.timestamp - a.timestamp)
    const job = queue.shift()!
    if (jobs.get(job.key) !== job || job.consumers.size === 0) continue
    job.state = 'active'

    try {
      // Check cache first
      const cached = await getCachedThumbnail(job.key)
      if (job.controller.signal.aborted || job.consumers.size === 0) continue
      if (cached) {
        resolveJob(job, cached)
        continue
      }
      // Capture and cache
      const dataUrl = await captureVideoFrame(job.videoUrl, job.controller.signal)
      if (job.controller.signal.aborted || job.consumers.size === 0) continue
      await setCachedThumbnail(job.key, dataUrl)
      resolveJob(job, dataUrl)
    } catch {
      resolveJob(job, null)
    } finally {
      if (jobs.get(job.key) === job) jobs.delete(job.key)
    }
  }

  processing = false
}

/**
 * Request a thumbnail for a video. Returns cached version instantly,
 * or queues a sequential capture with priority (newest requests first).
 * Deduplicates requests for the same file. Aborting one consumer leaves a
 * shared job running; the decode is released when its last consumer aborts.
 */
export function requestThumbnail(
  videoUrl: string,
  key: string,
  signal?: AbortSignal,
): Promise<string | null> {
  if (signal?.aborted) return Promise.resolve(null)
  return new Promise((resolve) => {
    let job = jobs.get(key)
    if (!job) {
      job = {
        videoUrl,
        key,
        timestamp: Date.now(),
        consumers: new Set(),
        state: 'queued',
        controller: new AbortController(),
      }
      jobs.set(key, job)
      queue.push(job)
    } else {
      job.timestamp = Date.now()
    }
    const consumer: ThumbnailConsumer = { resolve, signal }
    if (signal) {
      consumer.abort = () => cancelConsumer(job!, consumer)
      signal.addEventListener('abort', consumer.abort, { once: true })
    }
    job.consumers.add(consumer)
    processQueue()
  })
}
