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
    const saves: Array<{ expected_revision: number; source_in: number }> = []
    const exports: Array<{ expected_revision: number; url: string }> = []
    const previewRevisions: string[] = []
    let releaseFirstSave!: () => void
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
          body: JSON.stringify({ project: editorProject(1) }) })
        return
      }
      expect(request.method()).toBe('PUT')
      const body = request.postDataJSON()
      const sourceIn = body.project.tracks[0].items[0].source_in as number
      saves.push({ expected_revision: body.expected_revision, source_in: sourceIn })
      if (saves.length === 1) await firstSaveGate
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ project: editorProject(saves.length + 1, sourceIn) }) })
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
    expect(exports[0].expected_revision).toBe(3)
    expect(exports[0].url).toContain('/projects/Synthetic%20project/editor/projects/synthetic-cut/exports')
  })
}
