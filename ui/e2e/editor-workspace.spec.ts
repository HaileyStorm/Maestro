import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

const VIDEO = {
  name: 'sidecarless-clip.mp4',
  url: '/api/v1/file/sidecarless-clip.mp4?workspace=Synthetic%20project',
  type: 'video', mode: 'video', edit_sub_mode: null,
  artifact_class: 'final', linked_component_count: 0,
  favorite: false, size: 1024, created_at: 1_725_000_400,
  revision: 'gallery-stat-v1', workspace: 'Synthetic project',
  private: true, explicit: false,
}

const CONTENT_REVISION = `sha256:${'a'.repeat(64)}`
const EXPORT_JOB_ID = 'a1b2c3d4e5f6471889abcdef01234567'

function editorProject(revision: number, sourceIn = 0) {
  return {
    id: 'synthetic-cut', name: 'Cut of sidecarless-clip.mp4',
    workspace: VIDEO.workspace, revision,
    canvas: { width: 1280, height: 720, fps: 30, background: '#000000' },
    assets: {
      'source-video': {
        id: 'source-video', name: VIDEO.name, type: 'video', origin: 'output',
        workspace: VIDEO.workspace, output_id: VIDEO.name,
        output_revision: CONTENT_REVISION, private: true,
        duration: 10, width: 1280, height: 720, fps: 30, has_audio: true,
      },
    },
    tracks: [{
      id: 'video-main', name: 'Main video', type: 'video',
      items: [{ id: 'source-clip', asset_id: 'source-video', start: 0,
        source_in: sourceIn, duration: 10 - sourceIn, speed: 1 }],
    }],
  }
}

async function skipWelcome(page: Page) {
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
}

let api: SyntheticApiController | undefined
test.beforeEach(async ({ page }) => {
  api = await installSyntheticApi(page)
})
test.afterEach(async () => {
  await api?.assertClean()
  api = undefined
})

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`${viewport.name} sidecarless video opens in Editor and Back preserves an edit during save`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await skipWelcome(page)
    api!.setAccountScenario('remote-user')
    await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({
      path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname,
      contentType: 'font/ttf',
    }))
    const saves: Array<{ expected_revision: number; source_in: number }> = []
    const exports: Array<{ expected_revision: number; url: string }> = []
    const previewRevisions: string[] = []
    let releaseFirstSave!: () => void
    let savedProject = editorProject(1)
    const firstSaveGate = new Promise<void>(resolve => { releaseFirstSave = resolve })

    await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ outputs: [VIDEO], total: 1 }),
    }))
    await page.route('**/api/v1/outputs/sidecarless-clip.mp4/metadata*', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ params: null, source: 'none' }),
    }))
    await page.route('**/api/v1/file/sidecarless-clip.mp4*', route => {
      const revision = new URL(route.request().url()).searchParams.get('content_revision')
      if (revision) previewRevisions.push(revision)
      return route.fulfill({
        status: revision ? 409 : 404, contentType: 'text/plain',
        body: 'Synthetic media unavailable',
      })
    })
    await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects(?:\/[^/?]+)?(?:\?.*)?$/, async route => {
      const request = route.request()
      if (request.method() === 'POST') {
        expect(request.postDataJSON()).toEqual({
          output_name: VIDEO.name, output_revision: VIDEO.revision,
        })
        await route.fulfill({ status: 200, contentType: 'application/json',
          body: JSON.stringify({ project: savedProject }) })
        return
      }
      expect(request.method()).toBe('PUT')
      const body = request.postDataJSON()
      const sourceIn = body.project.tracks[0].items[0].source_in as number
      saves.push({ expected_revision: body.expected_revision, source_in: sourceIn })
      if (saves.length === 1) await firstSaveGate
      savedProject = { ...body.project, revision: saves.length + 1 }
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ project: savedProject }) })
    })
    await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects\/[^/]+\/exports$/, route => {
      exports.push({ expected_revision: route.request().postDataJSON().expected_revision,
        url: route.request().url() })
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ job_id: EXPORT_JOB_ID, status: 'queued' }) })
    })
    const exportJob = {
      job_id: EXPORT_JOB_ID, status: 'queued', workspace: VIDEO.workspace,
      created_at: 1_725_000_500, progress: 0, step: 0, total_steps: 1,
      phase: 'queued', message: 'Queued (Editor export)', output_files: [],
      error: null, prompt_preview: '', active_window_prompt: '',
      model_type: 'editor_export', generation_mode: 'edit',
      requested_outputs: 1, produced_outputs: 0,
      queue_wait_reason: 'queue_paused',
    }

    await page.goto('/')
    await page.getByRole('tab', { name: 'Gallery' }).click()
    const open = page.getByRole('button', { name: `Open ${VIDEO.name} in Editor` })
    await expect(open).toBeVisible()
    await open.click()
    await expect(page.getByRole('main', { name: 'Video Editor' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Reveal private preview' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    const accessibility = await new AxeBuilder({ page }).include('main[aria-label="Video Editor"]').analyze()
    expect(accessibility.violations.filter(violation =>
      violation.impact === 'critical' || violation.impact === 'serious')).toEqual([])
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-editor.png`), fullPage: true })
    await page.getByRole('button', { name: 'Reveal private preview' }).click()
    await expect.poll(() => previewRevisions.length).toBeGreaterThan(0)
    expect(previewRevisions.every(revision => revision === CONTENT_REVISION)).toBe(true)

    const start = page.getByRole('slider', { name: /Start/ })
    await start.focus()
    await start.press('ArrowRight')
    await expect(page.getByRole('status').filter({ hasText: 'Unsaved changes' })).toBeVisible()
    await page.getByRole('button', { name: 'Gallery' }).click()
    await expect.poll(() => saves.length).toBe(1)
    await expect(page.getByRole('button', { name: 'Export MP4' })).toBeDisabled()
    await start.press('ArrowRight')
    releaseFirstSave()
    await expect(page.getByRole('main', { name: 'Video Editor' })).toBeVisible()
    await expect.poll(() => saves.length).toBe(2)
    expect(saves[0].expected_revision).toBe(1)
    expect(saves[1].expected_revision).toBe(2)
    expect(saves[1].source_in).toBeGreaterThan(saves[0].source_in)
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    await page.getByRole('button', { name: 'Add text', exact: true }).click()
    const title = page.getByLabel('Text', { exact: true })
    await expect(title).toBeFocused()
    await title.fill('Field Notes\nLiteral [v]; fiction')
    await page.getByLabel('Starts at (seconds)', { exact: true }).fill('0.5')
    await page.getByLabel('Ends at (seconds)', { exact: true }).fill('1.5')
    await page.getByRole('combobox', { name: 'Position', exact: true }).selectOption('top')
    await page.getByRole('button', { name: 'Add text', exact: true }).click()
    await page.getByRole('button', { name: 'Remove text', exact: true }).click()
    await expect(title).toBeFocused()
    await expect(title).toHaveValue('Field Notes\nLiteral [v]; fiction')
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    await page.getByRole('button', { name: 'Gallery', exact: true }).click()
    await open.click()
    await expect(title).toHaveValue('Field Notes\nLiteral [v]; fiction')
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    const titledAccessibility = await new AxeBuilder({ page }).include('main[aria-label="Video Editor"]').analyze()
    expect(titledAccessibility.violations.filter(violation => violation.impact === 'critical' || violation.impact === 'serious')).toEqual([])
    await title.focus()
    await title.press('Tab')
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-text-layers.png`), fullPage: true })
    await page.route('**/api/v1/jobs', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ jobs: [exportJob] }),
    }))
    await page.route(`**/api/v1/status/${EXPORT_JOB_ID}`, route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(exportJob),
    }))
    await page.getByRole('button', { name: 'Export MP4' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Track the export in Queue' })).toBeVisible()
    await page.getByRole('button', { name: 'Gallery' }).click()
    await page.getByRole('tab', { name: /Queue/ }).click()
    await expect(page.getByRole('button', { name: `Copy job id ${EXPORT_JOB_ID}` })).toBeVisible()
    expect(exports).toHaveLength(1)
    expect(exports[0].expected_revision).toBe(savedProject.revision)
    expect(exports[0].url).toContain('/projects/Synthetic%20project/editor/projects/synthetic-cut/exports')
  })
}

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  test(`${viewport.name} timed Gallery audio import saves, reopens and guards export`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await skipWelcome(page)
    api!.setAccountScenario('remote-user')
    await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({
      path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname, contentType: 'font/ttf',
    }))
    const sound = { ...VIDEO, name: 'evening.wav', type: 'audio', mode: 'audio', revision: 'audio-gallery-v1', url: '/api/v1/file/evening.wav' }
    const base = editorProject(1)
    let saved = { ...base, assets: { ...base.assets } as Record<string, typeof base.assets['source-video']>, tracks: [
      ...base.tracks, { id: 'audio-main', name: 'Audio', type: 'audio', items: [] as Array<typeof base.tracks[0]['items'][0] & { volume?: number; muted?: boolean }> },
    ] }
    const imports: unknown[] = []
    const exports: unknown[] = []
    const previewPins: string[] = []
    await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ outputs: [VIDEO, sound], total: 2 }) }))
    await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ params: null, source: 'none' }) }))
    await page.route('**/api/v1/file/sidecarless-clip.mp4*', route => route.fulfill({ status: 404, body: 'Synthetic video unavailable' }))
    await page.route('**/api/v1/file/evening.wav*', route => {
      const pin = new URL(route.request().url()).searchParams.get('content_revision')
      if (pin) previewPins.push(pin)
      const wave = Buffer.alloc(44 + 48000 * 2)
      wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16)
      wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22); wave.writeUInt32LE(48000, 24); wave.writeUInt32LE(96000, 28)
      wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
      return route.fulfill({ contentType: 'audio/wav', body: wave })
    })
    await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects(?:\/[^/?]+)?(?:\?.*)?$/, route => {
      if (route.request().method() === 'PUT') saved = { ...route.request().postDataJSON().project, revision: saved.revision + 1 }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project: saved }) })
    })
    await page.route('**/editor/projects/*/audio', route => {
      imports.push(route.request().postDataJSON())
      saved = { ...saved, revision: saved.revision + 1, assets: { ...saved.assets, 'source-audio': {
        ...base.assets['source-video'], id: 'source-audio', name: sound.name, type: 'audio', output_id: sound.name,
        output_revision: `sha256:${'b'.repeat(64)}`, duration: 8, width: 0, height: 0, fps: 0,
      } }, tracks: saved.tracks.map(track => track.id === 'audio-main' ? { ...track, items: [{ id: 'audio-layer', asset_id: 'source-audio', source_in: 0, start: 0, duration: 8, speed: 1, volume: 1, muted: false }] } : track) }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project: saved }) })
    })
    await page.route('**/editor/projects/*/exports', route => { exports.push(route.request().postDataJSON()); return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: EXPORT_JOB_ID, status: 'queued' }) }) })
    await page.goto('/')
    await page.getByRole('tab', { name: 'Gallery' }).click()
    await page.getByRole('button', { name: `Open ${VIDEO.name} in Editor` }).click()
    const panel = page.getByRole('region', { name: 'Audio layer' })
    await expect(panel).toBeVisible()
    await panel.getByRole('combobox').selectOption(sound.name)
    await panel.getByRole('button', { name: 'Add audio', exact: true }).click()
    await expect(panel.getByRole('button', { name: 'Reveal private audio' })).toBeVisible()
    expect(imports).toEqual([{ expected_revision: 1, output_name: sound.name, output_revision: sound.revision }])
    expect(previewPins).toEqual([])
    await panel.getByLabel('Audio source start (seconds)', { exact: true }).fill('2')
    await panel.getByLabel('Audio source end (seconds)', { exact: true }).fill('5')
    await panel.getByLabel('Audio timeline start (seconds)', { exact: true }).fill('3')
    await panel.getByRole('slider', { name: /Audio volume/ }).focus()
    await panel.getByRole('slider', { name: /Audio volume/ }).press('Home')
    for (let index = 0; index < 25; index++) await panel.getByRole('slider', { name: /Audio volume/ }).press('ArrowRight')
    await panel.getByRole('checkbox', { name: 'Mute audio layer' }).check()
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    await page.getByRole('button', { name: 'Gallery', exact: true }).click()
    await page.getByRole('button', { name: `Open ${VIDEO.name} in Editor` }).click()
    await expect(panel.getByLabel('Audio source start (seconds)', { exact: true })).toHaveValue('2')
    await expect(panel.getByRole('checkbox', { name: 'Mute audio layer' })).toBeChecked()
    await panel.getByRole('button', { name: 'Reveal private audio' }).click()
    await expect.poll(() => previewPins.length).toBeGreaterThan(0)
    expect(previewPins.every(pin => pin === `sha256:${'b'.repeat(64)}`)).toBe(true)
    await expect(panel.getByRole('button', { name: 'Audition audio' })).toBeDisabled()
    await panel.getByLabel('Audio timeline start (seconds)', { exact: true }).fill('9')
    await expect(page.getByText('Audio ends after this cut.', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Export MP4' })).toBeDisabled()
    await panel.getByLabel('Audio timeline start (seconds)', { exact: true }).fill('3')
    await panel.getByRole('checkbox', { name: 'Mute audio layer' }).uncheck()
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    const accessibility = await new AxeBuilder({ page }).include('main[aria-label="Video Editor"]').analyze()
    expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([])
    await panel.screenshot({ path: testInfo.outputPath(`${viewport.name}-audio-layer.png`) })
    await page.getByRole('button', { name: 'Export MP4' }).click()
    await expect.poll(() => exports.length).toBe(1)
    await panel.getByRole('button', { name: 'Remove audio layer' }).click()
    await expect(panel.getByRole('combobox')).toBeFocused()
  })
}

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  test(`${viewport.name} timed Gallery image import saves, reopens and guards export`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await skipWelcome(page)
    api!.setAccountScenario('remote-user')
    await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({
      path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname, contentType: 'font/ttf',
    }))
    const sound = { ...VIDEO, name: 'logo.png', type: 'image', mode: 'image', revision: 'image-gallery-v1', url: '/api/v1/file/logo.png' }
    const base = editorProject(1)
    let saved = { ...base, assets: { ...base.assets } as Record<string, typeof base.assets['source-video']>, tracks: [
      ...base.tracks, { id: 'images-main', name: 'Image layer', type: 'video', items: [] as Array<typeof base.tracks[0]['items'][0] & { size?: number; opacity?: number; position?: string }> },
    ] }
    const imports: unknown[] = []
    const exports: unknown[] = []
    const previewPins: string[] = []
    await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ outputs: [VIDEO, sound], total: 2 }) }))
    await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ params: null, source: 'none' }) }))
    await page.route('**/api/v1/file/sidecarless-clip.mp4*', route => route.fulfill({ status: 404, body: 'Synthetic video unavailable' }))
    await page.route('**/api/v1/file/logo.png*', route => {
      const pin = new URL(route.request().url()).searchParams.get('content_revision')
      if (pin) previewPins.push(pin)
      return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64') })
    })
    await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects(?:\/[^/?]+)?(?:\?.*)?$/, route => {
      if (route.request().method() === 'PUT') saved = { ...route.request().postDataJSON().project, revision: saved.revision + 1 }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project: saved }) })
    })
    await page.route('**/editor/projects/*/image', route => {
      imports.push(route.request().postDataJSON())
      saved = { ...saved, revision: saved.revision + 1, assets: { ...saved.assets, 'source-image': {
        ...base.assets['source-video'], id: 'source-image', name: sound.name, type: 'image', output_id: sound.name,
        output_revision: `sha256:${'b'.repeat(64)}`, duration: 0, width: 32, height: 16, fps: 0, has_audio: false,
      } }, tracks: saved.tracks.map(track => track.id === 'images-main' ? { ...track, items: [{ id: 'image-layer', asset_id: 'source-image', source_in: 0, start: 0, duration: 10, speed: 1, size: 0.25, opacity: 1, position: 'center' }] } : track) }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ project: saved }) })
    })
    await page.route('**/editor/projects/*/exports', route => { exports.push(route.request().postDataJSON()); return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: EXPORT_JOB_ID, status: 'queued' }) }) })
    await page.goto('/')
    await page.getByRole('tab', { name: 'Gallery' }).click()
    await page.getByRole('button', { name: `Open ${VIDEO.name} in Editor` }).click()
    const panel = page.getByRole('region', { name: 'Image layer' })
    await expect(panel).toBeVisible()
    await panel.getByRole('combobox').selectOption(sound.name)
    await panel.getByRole('button', { name: 'Add image', exact: true }).click()
    await expect(panel.getByRole('button', { name: 'Show private image' })).toBeVisible()
    expect(imports).toEqual([{ expected_revision: 1, output_name: sound.name, output_revision: sound.revision }])
    expect(previewPins).toEqual([])
    const revealVideo = page.getByRole('button', { name: 'Reveal private preview', exact: true })
    if (await revealVideo.count()) await revealVideo.click()
    expect(previewPins).toEqual([])
    await panel.getByRole('button', { name: 'Show private image' }).click()
    await expect.poll(() => previewPins.length).toBeGreaterThan(0)
    expect(previewPins.every(pin => pin === `sha256:${'b'.repeat(64)}`)).toBe(true)
    await panel.getByLabel('Image end (seconds)', { exact: true }).fill('3')
    await panel.getByLabel('Image start (seconds)', { exact: true }).fill('1')
    await panel.getByLabel('Image end (seconds)', { exact: true }).fill('3')
    await panel.getByRole('slider', { name: /Image opacity/ }).focus()
    await panel.getByRole('slider', { name: /Image opacity/ }).press('Home')
    for (let index = 0; index < 50; index++) await panel.getByRole('slider', { name: /Image opacity/ }).press('ArrowRight')
    await panel.getByRole('combobox', { name: 'Image placement' }).selectOption('bottom')
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    await page.getByRole('button', { name: 'Gallery', exact: true }).click()
    await page.getByRole('button', { name: `Open ${VIDEO.name} in Editor` }).click()
    await expect(panel.getByLabel('Image start (seconds)', { exact: true })).toHaveValue('1')
    await expect(panel.getByLabel('Image end (seconds)', { exact: true })).toHaveValue('3')
    await expect(panel.getByRole('combobox', { name: 'Image placement' })).toHaveValue('bottom')
    await panel.getByLabel('Image start (seconds)', { exact: true }).fill('9')
    await expect(page.getByText('The image must last at least one video frame', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Export MP4' })).toBeDisabled()
    await panel.getByLabel('Image start (seconds)', { exact: true }).fill('1')
    await expect(page.getByRole('status').filter({ hasText: 'Draft saved' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    const accessibility = await new AxeBuilder({ page }).include('main[aria-label="Video Editor"]').analyze()
    expect(accessibility.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([])
    await panel.screenshot({ path: testInfo.outputPath(`${viewport.name}-image-layer.png`) })
    await page.getByRole('button', { name: 'Export MP4' }).click()
    await expect.poll(() => exports.length).toBe(1)
    await panel.getByRole('button', { name: 'Remove image' }).click()
    await expect(panel.getByRole('combobox')).toBeFocused()
  })
}
