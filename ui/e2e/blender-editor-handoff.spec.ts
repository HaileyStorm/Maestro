import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

const video = {
  name: 'kept-motion.mp4', url: '/api/v1/file/kept-motion.mp4?workspace=Synthetic%20project',
  type: 'video', mode: 'video', edit_sub_mode: 'blender_director_reference',
  artifact_class: 'final', linked_component_count: 0, favorite: false,
  size: 1024, created_at: 1725000400, revision: 'current-gallery-revision',
  workspace: 'Synthetic project', private: true, explicit: true,
}

async function setup(page: Page) {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({
    path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname,
    contentType: 'font/ttf',
  }))
  const editorRequests: unknown[] = []
  let pending: Route | undefined
  const json = (route: Route, value: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  await page.route('**/api/v1/blender/status*', route => json(route, {
    installed: true, ready: true, mcp_attested: true, runtime_attested: true,
    bridge_ready: true, workspace: video.workspace, max_total_frames: 7200,
  }))
  await page.route('**/api/v1/blender/director-finalize', route => {
    const body = route.request().postDataJSON()
    return json(route, { workspace: body.workspace, asset_id: 'asset', variant_id: 'variant',
      video: { filename: video.name, url: video.url }, director_reviews: [], final_plan: body.plan })
  })
  await page.route('**/api/v1/projects/*/assets/asset/variants/variant', route => json(route, { status: route.request().postDataJSON().status }))
  await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, async route => {
    if (new URL(route.request().url()).searchParams.get('search') === video.name) {
      pending = route
      return
    }
    await json(route, { outputs: [video], total: 1 })
  })
  await page.route('**/api/v1/outputs/kept-motion.mp4/metadata*', route => json(route, { params: null, source: 'none' }))
  await page.route('**/api/v1/file/kept-motion.mp4*', route => route.fulfill({ status: 404, body: 'Synthetic media unavailable' }))
  await page.route(/\/api\/v1\/projects\/[^/]+\/editor\/projects(?:\/[^/?]+)?(?:\?.*)?$/, route => {
    if (route.request().method() === 'POST') editorRequests.push(route.request().postDataJSON())
    return json(route, { project: {
      id: 'kept-cut', name: 'Kept motion cut', workspace: video.workspace, revision: 1,
      canvas: { width: 1280, height: 720, fps: 24, background: '#000000' },
      assets: { source: { id: 'source', name: video.name, type: 'video', origin: 'output',
        workspace: video.workspace, output_id: video.name, output_revision: `sha256:${'a'.repeat(64)}`,
        private: true, duration: 2, width: 1280, height: 720, fps: 24, has_audio: false } },
      tracks: [{ id: 'video-main', name: 'Main video', type: 'video', items: [
        { id: 'clip', asset_id: 'source', start: 0, duration: 2, source_in: 0, speed: 1 },
      ] }],
    } })
  })
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  const mobileMenu = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  if (await mobileMenu.isVisible()) {
    await mobileMenu.click()
    await page.getByRole('button', { name: 'Open Generate', exact: true }).click()
  }
  await page.getByRole('button', { name: 'Tools', exact: true }).click()
  await page.getByRole('button', { name: 'Blender', exact: true }).click()
  const edit = page.getByRole('button', { name: 'Edit this video', exact: true })
  const keep = page.getByRole('button', { name: 'Keep motion video', exact: true })
  await page.getByRole('button', { name: 'Review and render', exact: true }).click()
  await expect(keep).toBeEnabled()
  await expect(edit).toHaveCount(0)
  await keep.click()
  await expect(edit).toBeEnabled()
  return {
    api, edit, keep, editorRequests,
    resolve: async (outputs: unknown[]) => {
      await expect.poll(() => Boolean(pending)).toBe(true)
      const current = pending!
      pending = undefined
      await json(current, { outputs, total: outputs.length })
    },
  }
}

test('kept Blender video opens Editor with the exact current Gallery revision and privacy', async ({ page }) => {
  const fixture = await setup(page)
  await fixture.edit.click()
  await expect(fixture.edit).toBeDisabled()
  await fixture.resolve([video])
  const editor = page.getByRole('main', { name: 'Video Editor' })
  await expect(editor).toBeVisible()
  await expect(editor.getByRole('button', { name: 'Reveal private preview', exact: true })).toBeVisible()
  await expect.poll(() => fixture.editorRequests.length).toBeGreaterThan(0)
  for (const request of fixture.editorRequests) {
    expect(request).toEqual({ output_name: video.name, output_revision: video.revision })
  }
  await fixture.api.assertClean()
})

test('missing or ambiguous Gallery results and leaving Blender cannot initialize an Editor draft', async ({ page }) => {
  const fixture = await setup(page)
  for (const outputs of [[], [video, video], [{ ...video, workspace: 'Other project' }], [{ ...video, revision: '' }]]) {
    await fixture.edit.click()
    await fixture.resolve(outputs)
    await expect(fixture.edit).toBeEnabled()
    await expect(page.getByText('This video could not open in Editor. Refresh Gallery and open the current video from there.', { exact: true })).toBeVisible()
    expect(fixture.editorRequests).toEqual([])
  }
  await fixture.edit.click()
  await expect(fixture.edit).toBeDisabled()
  await page.getByRole('button', { name: 'Upscale', exact: true }).click()
  await fixture.resolve([video])
  await expect(page.getByRole('main', { name: 'Video Editor' })).toHaveCount(0)
  expect(fixture.editorRequests).toEqual([])
  await fixture.api.assertClean()
})
