import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page, type Route } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

const revision = `sha256:${'a'.repeat(64)}`
const audioRevision = `sha256:${'b'.repeat(64)}`
const output = { name: 'preview.mp4', url: '/api/v1/file/preview.mp4', type: 'video', mode: 'video',
  artifact_class: 'final', linked_component_count: 0, favorite: false, size: 1024, created_at: 1_725_000_400,
  revision: 'gallery-v1', workspace: 'Synthetic project', private: true, explicit: false }
let api: SyntheticApiController
let movie: Buffer
const wave = Buffer.alloc(44 + 48000 * 8 * 2)
wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16)
wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22); wave.writeUInt32LE(48000, 24); wave.writeUInt32LE(96000, 28)
wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
for (let i = 0; i < 48000 * 8; i++) wave.writeInt16LE(Math.round(4000 * Math.sin(2 * Math.PI * 440 * i / 48000)), 44 + i * 2)

test.beforeAll(() => {
  // Small real CPU-encoded media exercises browser clocks; no production model or GPU.
  const directory = mkdtempSync(join(process.env.MAESTRO_PLAYWRIGHT_OUTPUT_DIR!, 'media-'))
  try {
    const file = join(directory, 'preview.mp4')
    execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=30:d=8',
      '-f', 'lavfi', '-i', 'sine=frequency=220:duration=8', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p',
      '-g', '30', '-c:a', 'aac', '-movflags', '+faststart', file], { timeout: 15000, maxBuffer: 2_000_000 })
    movie = readFileSync(file)
  } finally { rmSync(directory, { recursive: true }) }
})
test.beforeEach(async ({ page }) => { api = await installSyntheticApi(page) })
test.afterEach(async () => { await api.assertClean() })

function serveMedia(route: Route, body: Buffer, contentType: string) {
  const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range ?? '')
  if (!range) return route.fulfill({ contentType, headers: { 'Accept-Ranges': 'bytes' }, body })
  const start = Number(range[1]), end = Math.min(body.length - 1, range[2] ? Number(range[2]) : body.length - 1)
  return route.fulfill({ status: 206, contentType, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${body.length}` }, body: body.subarray(start, end + 1) })
}

async function openEditor(page: Page, privateAudio = false, failAudio = false) {
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  api.setAccountScenario('remote-user')
  const videoAsset = { id: 'video', name: output.name, type: 'video', origin: 'output', workspace: output.workspace,
    output_id: output.name, output_revision: revision, private: true, duration: 8, width: 160, height: 90, fps: 30, has_audio: true }
  let saved = { id: 'synthetic-mix', workspace: output.workspace, name: 'Mixed preview', revision: 1,
    canvas: { width: 160, height: 90, fps: 30, background: '#000000' }, assets: {
      video: videoAsset, sound: { ...videoAsset, id: 'sound', name: 'preview.wav', output_id: 'preview.wav',
        output_revision: audioRevision, type: 'audio', private: privateAudio, width: 0, height: 0, fps: 0 },
    }, tracks: [
      { id: 'video-main', name: 'Main video', type: 'video', items: [
        { id: 'one', asset_id: 'video', start: 0, source_in: 0, duration: 2, speed: 1 },
        { id: 'two', asset_id: 'video', start: 2, source_in: 2, duration: 4, speed: 1 },
      ] },
      { id: 'audio-main', name: 'Audio', type: 'audio', items: [
        { id: 'bed', asset_id: 'sound', start: 3, source_in: 1, duration: 2, speed: 1, volume: 0.5, fade_in: 1, fade_out: 0.5 },
      ] },
    ] }
  const pins: string[] = []
  await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ outputs: [output], total: 1 }) }))
  await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ params: null, source: 'none' }) }))
  await page.route('**/api/v1/file/preview.mp4*', route => serveMedia(route, movie, 'video/mp4'))
  await page.route('**/api/v1/file/preview.wav*', route => {
    pins.push(new URL(route.request().url()).searchParams.get('content_revision') ?? '')
    if (failAudio) return route.fulfill({ status: 409, body: 'Synthetic source conflict' })
    return serveMedia(route, wave, 'audio/wav')
  })
  await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({ path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname, contentType: 'font/ttf' }))
  await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects(?:\/[^/?]+)?(?:\?.*)?$/, route => {
    if (route.request().method() === 'PUT') saved = { ...route.request().postDataJSON().project, revision: saved.revision + 1 }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project: saved }) })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Gallery' }).click()
  await page.getByRole('button', { name: 'Open preview.mp4 in Editor' }).click()
  const editor = page.getByRole('main', { name: 'Video Editor' })
  await editor.getByRole('button', { name: 'Select clip 2: preview.mp4' }).click()
  await editor.getByRole('button', { name: 'Reveal private preview', exact: true }).click()
  return { editor, pins, recoverAudio: () => { failAudio = false } }
}

async function seek(page: Page, position: number) {
  await expect.poll(() => page.locator('main[aria-label="Video Editor"] video').evaluate(video => ({ ready: (video as HTMLVideoElement).readyState, error: (video as HTMLVideoElement).error?.code ?? null }))).toEqual({ ready: 4, error: null })
  const slider = page.getByRole('slider', { name: 'Preview position', exact: true })
  await slider.fill(String(position))
  await expect.poll(() => page.locator('main[aria-label="Video Editor"] video').evaluate(video => (video as HTMLVideoElement).currentTime)).toBeCloseTo(2 + position, 1)
}

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  test(`${viewport.name} mix follows clip offset, fades, seeks, pause, disable, audition and leaving Editor`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const { editor, pins } = await openEditor(page)
    const audio = page.getByTestId('editor-mixed-audio')
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState)).toBeGreaterThan(0)
    await seek(page, 1.3)
    await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
    const observed = await editor.evaluate(root => {
      const v = root.querySelector('video')!, a = root.querySelector<HTMLAudioElement>('[data-testid="editor-mixed-audio"]')!
      return { drift: Math.abs(a.currentTime - (v.currentTime - 2)), gain: a.volume, expected: Math.min(0.5, (v.currentTime - 3) * 0.5) }
    })
    expect(observed.drift).toBeLessThan(0.15)
    expect(Math.abs(observed.gain - observed.expected)).toBeLessThan(0.05)
    // Deterministic underflow injection: the event and readyState mirror a browser stall.
    await editor.locator('video').evaluate(node => {
      Object.defineProperty(node, 'readyState', { configurable: true, get: () => 2 })
      node.dispatchEvent(new Event('waiting'))
    })
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await editor.locator('video').evaluate(node => {
      Reflect.deleteProperty(node, 'readyState')
      node.dispatchEvent(new Event('playing'))
    })
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
    await editor.getByRole('button', { name: 'Pause', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await seek(page, 0.3)
    await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await editor.getByRole('button', { name: 'Pause', exact: true }).click()
    await seek(page, 2)
    await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
    await editor.getByRole('checkbox', { name: 'Include audio layer in preview' }).uncheck()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await editor.getByRole('checkbox', { name: 'Include audio layer in preview' }).check()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
    await editor.getByRole('region', { name: 'Audio layer' }).getByRole('button', { name: /Audition/ }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await seek(page, 3.2)
    await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    await editor.getByRole('button', { name: 'Pause', exact: true }).click()
    await seek(page, 2)
    await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
    await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
    const detachedAudio = await audio.elementHandle()
    await editor.getByRole('button', { name: 'Gallery', exact: true }).click()
    expect(await detachedAudio!.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
    expect(pins.length).toBeGreaterThan(0)
    expect(pins.every(pin => pin === audioRevision)).toBe(true)
  })
}

test('private layer does not fetch until explicit reveal; muted layer stays silent', async ({ page }) => {
  const { editor, pins } = await openEditor(page, true)
  await expect(editor.getByText('Reveal private audio below to include it in preview.')).toBeVisible()
  await expect(page.getByTestId('editor-mixed-audio')).toHaveCount(0)
  expect(pins).toEqual([])
  await editor.getByRole('region', { name: 'Audio layer' }).getByRole('button', { name: 'Reveal private audio' }).click()
  const audio = page.getByTestId('editor-mixed-audio')
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState)).toBeGreaterThan(0)
  await editor.getByRole('checkbox', { name: /Mute/ }).check()
  await expect(editor.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
  await seek(page, 2)
  await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
  expect(pins.every(pin => pin === audioRevision)).toBe(true)
  const accessibility = await new AxeBuilder({ page }).include('main[aria-label="Video Editor"]').analyze()
  expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([])
})

test('failed mixed audio has explicit retry and resumes only after Play', async ({ page }) => {
  const { editor, recoverAudio } = await openEditor(page, false, true)
  const retry = editor.getByRole('button', { name: 'Retry audio preview', exact: true })
  await expect(retry).toBeVisible()
  recoverAudio()
  await retry.click()
  await expect(retry).toHaveCount(0)
  const audio = page.getByTestId('editor-mixed-audio')
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).readyState)).toBeGreaterThan(0)
  expect(await audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(true)
  await seek(page, 2)
  await editor.getByRole('button', { name: 'Play clip', exact: true }).click()
  await expect.poll(() => audio.evaluate(node => (node as HTMLAudioElement).paused)).toBe(false)
})
