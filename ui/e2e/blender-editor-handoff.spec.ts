import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

const video = {
  name: 'kept-motion.mp4', url: '/api/v1/file/kept-motion.mp4?workspace=Synthetic%20project',
  type: 'video', mode: 'video', edit_sub_mode: 'blender_director_reference',
  artifact_class: 'final', linked_component_count: 0, favorite: false,
  size: 1024, created_at: 1725000400, revision: 'current-gallery-revision',
  workspace: 'Synthetic project', private: true, explicit: true,
}

async function setup(page: Page, nonapproval = false, manualOnly = false, permissions?: string[], projects = [video.workspace]) {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  if (permissions || projects.length > 1) await page.route('**/api/v1/workspaces', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({
      workspaces: projects.map(name => ({ name, project_permissions: permissions ?? ['project.open', 'project.read', 'project.mutate', 'project.generate'] })), active: video.workspace,
    }),
  }))
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  await page.route('**/editor-fonts/DejaVuSans.ttf', route => route.fulfill({
    path: new URL('../public/editor-fonts/DejaVuSans.ttf', import.meta.url).pathname,
    contentType: 'font/ttf',
  }))
  const editorRequests: unknown[] = []
  let pending: Route | undefined
  const json = (route: Route, value: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  await page.route('**/api/v1/blender/status*', route => json(route, {
    installed: true, ready: true, mcp_attested: true, runtime_attested: true, mcp_sdk_ready: true,
    bridge_ready: true, workspace: video.workspace, max_total_frames: 7200,
  }))
  await page.route('**/api/v1/blender/director-finalize', route => {
    if (nonapproval) return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({
      detail: { code: 'blender_review_not_approved', review_count: 3,
        feedback: 'The cube is outside the camera view. Move it toward the center.' },
    }) })
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
  if (!manualOnly) await page.getByRole('button', { name: 'Review and render', exact: true }).click()
  if (!manualOnly && !nonapproval) {
    await expect(keep).toBeEnabled()
    await expect(edit).toHaveCount(0)
    await keep.click()
    await expect(edit).toBeEnabled()
  }
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

test('Director nonapproval shows actionable feedback without Keep or Editor controls', async ({ page }) => {
  const fixture = await setup(page, true)
  await expect(page.getByText('Director could not approve this animation. Adjust the scene or request, then review again. Director feedback: The cube is outside the camera view. Move it toward the center.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Review and render', exact: true })).toBeEnabled()
  await expect(fixture.keep).toHaveCount(0)
  await expect(fixture.edit).toHaveCount(0)
  expect(fixture.editorRequests).toEqual([])
  await fixture.api.assertClean()
})

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
  await page.route('**/api/v1/blender/status*', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({
      installed: true, ready: false, mcp_attested: true, runtime_attested: true,
      mcp_sdk_ready: false, bridge_ready: true, workspace: video.workspace, max_total_frames: 7200,
    }),
  }))
  await page.getByRole('button', { name: 'Blender', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Review and render', exact: true })).toBeDisabled()
  await expect(page.getByText('Blender support is missing from Maestro’s current environment. In Pinokio, open Maestro, run “Verify / Repair Blender MCP Support,” then reopen this panel. If Blender is still unavailable, restart Maestro.', { exact: true })).toBeVisible()
  await fixture.api.assertClean()
})


test('Blender repeats submit one native segment with separate clip instances and preserve privacy', async ({ page }) => {
  const fixture = await setup(page, false, true)
  const requests: Array<{ private_output: boolean; package: {
    id: string; canvas: unknown; audio: unknown; segments: Array<{ animation: { frame_end: number } }>; clips: unknown[]
  } }> = []
  await page.evaluate(() => Object.defineProperty(globalThis.crypto, 'randomUUID', { value: undefined, configurable: true }))
  await page.route('**/api/v1/projects/*/compositions', route => {
    requests.push(route.request().postDataJSON())
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: 'queued-repeat', status: 'queued' }) })
  })
  await page.getByRole('spinbutton', { name: 'Seconds', exact: true }).fill('2')
  await page.getByRole('spinbutton', { name: 'Repeats', exact: true }).fill('3')
  await page.getByRole('spinbutton', { name: 'FPS', exact: true }).fill('24.5')
  await expect(page.getByRole('button', { name: 'Render repeats', exact: true })).toBeDisabled()
  expect(requests).toHaveLength(0)
  await page.getByRole('spinbutton', { name: 'FPS', exact: true }).fill('24')
  await page.getByText('Private Off', { exact: true }).click()
  await page.getByRole('button', { name: 'Render repeats', exact: true }).click()
  await expect(page.getByText('Added to Queue. When the sequence finishes, open it from Gallery in Editor.', { exact: true })).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(requests[0].private_output).toBe(true)
  expect(requests[0].package.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  expect(requests[0].package.canvas).toEqual({ width: 1280, height: 720, fps: 24 })
  expect(requests[0].package.audio).toEqual({ mode: 'silence', sample_rate: 48000 })
  expect(requests[0].package.segments).toHaveLength(1)
  expect(requests[0].package.segments[0].animation.frame_end).toBe(47)
  expect(requests[0].package.clips).toEqual([1, 2, 3].map(index => ({ id: `clip-${index}`, segment_id: 'motion', source_frame: 0, frame_count: 48 })))
  await expect(fixture.keep).toHaveCount(0)
  await expect(fixture.edit).toHaveCount(0)
  await page.getByText('Private On', { exact: true }).click()
  await page.getByRole('button', { name: 'Render repeats', exact: true }).click()
  await expect.poll(() => requests.length).toBe(2)
  expect(requests[1].private_output).toBe(false)
  expect(requests[1].package.id).not.toBe(requests[0].package.id)
  await fixture.api.assertClean()
})

test('Blender repeats cannot submit without generation access or valid whole-number FPS', async ({ page }) => {
  const fixture = await setup(page, false, true, ['project.open', 'project.read', 'project.mutate'])
  const render = page.getByRole('button', { name: 'Render repeats', exact: true })
  await expect(render).toBeDisabled()
  await expect(page.getByText('This project needs editing and generation access to render repeats.', { exact: true }).filter({ visible: true })).toBeVisible()
  await page.getByRole('spinbutton', { name: 'FPS', exact: true }).fill('24.5')
  await expect(page.getByText('Use a whole-number FPS from 1–120, at least two motion frames and 2–8 repeats.', { exact: true }).filter({ visible: true })).toBeVisible()
  await expect(render).toBeDisabled()
  await fixture.api.assertClean()
})

test('Blender repeats guard rapid submissions and preserve ambiguous acknowledgment guidance', async ({ page }) => {
  const fixture = await setup(page, false, true)
  let pending: Route | undefined
  let count = 0
  await page.route('**/api/v1/projects/*/compositions', route => { count += 1; pending = route })
  const render = page.getByRole('button', { name: 'Render repeats', exact: true })
  await render.evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click() })
  await expect.poll(() => count).toBe(1)
  await expect(render).toBeDisabled()
  await pending!.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'queued' }) })
  await expect(page.getByText('Maestro could not confirm this submission. Check Queue before rendering repeats again; the sequence may already be queued.', { exact: true })).toBeVisible()
  await expect(render).toBeEnabled()
  expect(count).toBe(1)
  await expect(fixture.keep).toHaveCount(0)
  await fixture.api.assertClean()
})

test('Blender repeats never automatically resend lost or failed submission responses', async ({ page }) => {
  const fixture = await setup(page, false, true)
  let count = 0
  await page.route('**/api/v1/projects/*/compositions', route => {
    count += 1
    if (count === 1) return route.abort('failed')
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'private diagnostic' }) })
  })
  const render = page.getByRole('button', { name: 'Render repeats', exact: true })
  const guidance = page.getByText('Maestro could not confirm this submission. Check Queue before rendering repeats again; the sequence may already be queued.', { exact: true })
  await render.click()
  await expect(guidance).toBeVisible()
  expect(count).toBe(1)
  await render.click()
  await expect(guidance).toBeVisible()
  expect(count).toBe(2)
  await expect(page.getByText('private diagnostic', { exact: true })).toHaveCount(0)
  await fixture.api.assertClean()
})


const plannedScene = {
  workspace: video.workspace, director_prompt: 'Two shapes move together',
  scene: { clear_scene: true, objects: [
    { name: 'PlannedCube', primitive: 'cube', location: [0, 0, 0], rotation_degrees: [0, 0, 30], scale: [1, 2, 1], material: { name: 'Blue', color: [0, 0, 1, 1] } },
    { name: 'PlannedSphere', primitive: 'sphere', location: [1, 0, 0], material: { name: 'Red', color: [1, 0, 0, 1] } },
  ] },
  animation: { frame_start: 10, frame_end: 57, objects: [
    { name: 'PlannedCube', keyframes: [{ frame: 10, rotation_degrees: [0, 0, 30], interpolation: 'LINEAR' }, { frame: 57, rotation_degrees: [0, 0, 90], interpolation: 'LINEAR' }] },
    { name: 'PlannedSphere', keyframes: [{ frame: 10, scale: [1, 1, 1] }, { frame: 57, scale: [2, 2, 2] }] },
  ] },
  semantic_mapping: { legend: [
    { object_name: 'PlannedCube', primitive: 'cube', color: [0, 0, 1, 1], subject: 'Blue block', action: 'turns' },
    { object_name: 'PlannedSphere', primitive: 'sphere', color: [1, 0, 0, 1], subject: 'Red ball', action: 'grows' },
  ], conditioned_prompt: 'A blue block turns beside a growing red ball.' },
  review_frames: [10, 34, 57], notes: 'Two independent transforms', duration_seconds: 2, frame_count: 48,
  fps: 24, llm_model: 'Synthetic local planner', confirmation_required: false, review_strategy: 'Not visually reviewed',
}

async function planOnly(page: Page, value: unknown = plannedScene) {
  await page.route('**/api/v1/blender/director-plan', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) }))
  await page.getByPlaceholder('Describe the scene, subjects, props, movement, and camera layout…').filter({ visible: true }).fill('Two shapes move together')
  await page.getByRole('button', { name: 'Plan scene only', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Queue planned repeats', exact: true })).toBeVisible()
}

test('Director planned repeats preserve all typed actions and their own clock when manual controls change', async ({ page }) => {
  const fixture = await setup(page, false, true)
  let finalized = 0
  const requests: Array<{ package: { segments: Array<{ scene: unknown; animation: unknown; fps: number }>; clips: unknown[]; canvas: unknown; audio: unknown }; private_output: boolean }> = []
  await page.route('**/api/v1/blender/director-finalize', route => { finalized += 1; return route.abort('failed') })
  await page.route('**/api/v1/projects/*/compositions', route => {
    requests.push(route.request().postDataJSON())
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: 'planned-repeat', status: 'queued' }) })
  })
  await planOnly(page)
  await page.getByRole('textbox', { name: 'Object name', exact: true }).filter({ visible: true }).fill('ManualDifferentObject')
  await page.getByRole('spinbutton', { name: 'Seconds', exact: true }).fill('7')
  await page.getByRole('spinbutton', { name: 'FPS', exact: true }).fill('60')
  await page.getByRole('spinbutton', { name: 'Repeats', exact: true }).fill('3')
  await page.getByText('Private Off', { exact: true }).click()
  await page.getByRole('button', { name: 'Queue planned repeats', exact: true }).click()
  await expect(page.getByText('Added to Queue. When the sequence finishes, open it from Gallery in Editor.', { exact: true })).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(requests[0].private_output).toBe(true)
  expect(requests[0].package.segments).toEqual([{ id: 'planned-motion', scene: plannedScene.scene, animation: plannedScene.animation, fps: 24, width: 1280, height: 720 }])
  expect(requests[0].package.canvas).toEqual({ width: 1280, height: 720, fps: 24 })
  expect(requests[0].package.audio).toEqual({ mode: 'silence', sample_rate: 48000 })
  expect(requests[0].package.clips).toEqual([1, 2, 3].map(index => ({ id: `clip-${index}`, segment_id: 'planned-motion', source_frame: 0, frame_count: 48 })))
  expect(finalized).toBe(0)
  await expect(fixture.keep).toHaveCount(0)
  await expect(fixture.edit).toHaveCount(0)
  await expect(page.getByText('Object-to-scene guide', { exact: true })).toBeVisible()
  console.log('DIRECTOR_COMPOSITION_JSON=' + JSON.stringify(requests[0]))
  await fixture.api.assertClean()
})

test('Director planned repeats reject unsupported plan clocks and missing generation permission', async ({ page }) => {
  let fixture = await setup(page, false, true)
  await planOnly(page, { ...plannedScene, fps: 240 })
  await expect(page.getByRole('button', { name: 'Queue planned repeats', exact: true })).toBeDisabled()
  await expect(page.getByText('The planned sequence needs 2–7200 frames, a whole-number FPS from 1–120 and 2–8 repeats in the current project.', { exact: true })).toBeVisible()
  await fixture.api.assertClean()
  fixture = await setup(page, false, true, ['project.open', 'project.read', 'project.mutate'])
  await page.getByPlaceholder('Describe the scene, subjects, props, movement, and camera layout…').filter({ visible: true }).fill('Two shapes move together')
  await expect(page.getByRole('button', { name: 'Plan scene only', exact: true })).toBeDisabled()
  await fixture.api.assertClean()
})

test('Director planning cannot restore a plan after its description changes', async ({ page }) => {
  const fixture = await setup(page, false, true)
  let pending: Route | undefined
  await page.route('**/api/v1/blender/director-plan', route => { pending = route })
  const prompt = page.getByPlaceholder('Describe the scene, subjects, props, movement, and camera layout…').filter({ visible: true })
  await prompt.fill('Original description')
  await page.getByRole('button', { name: 'Plan scene only', exact: true }).click()
  await expect.poll(() => Boolean(pending)).toBe(true)
  await prompt.fill('Changed description')
  await pending!.fulfill({ contentType: 'application/json', body: JSON.stringify(plannedScene) })
  await expect(page.getByText('The description changed while planning. Plan the current description again.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Queue planned repeats', exact: true })).toHaveCount(0)
  await fixture.api.assertClean()
})

test('Planned and manual repeats share one synchronous submission latch', async ({ page }) => {
  const fixture = await setup(page, false, true)
  await planOnly(page)
  let count = 0
  let pending: Route | undefined
  await page.route('**/api/v1/projects/*/compositions', route => { count += 1; pending = route })
  await page.getByRole('button', { name: 'Queue planned repeats', exact: true }).evaluate(button => {
    (button as HTMLButtonElement).click()
    const manual = Array.from(document.querySelectorAll('button')).find(item => item.textContent === 'Render repeats')
    manual?.click()
  })
  await expect.poll(() => count).toBe(1)
  await expect(page.getByRole('button', { name: 'Render repeats', exact: true })).toBeDisabled()
  await pending!.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'private backend detail' }) })
  await expect(page.getByText('Maestro could not confirm this submission. Check Queue before rendering repeats again; the sequence may already be queued.', { exact: true })).toBeVisible()
  expect(count).toBe(1)
  await expect(fixture.keep).toHaveCount(0)
  await fixture.api.assertClean()
})

test('An accepted planned sequence cannot show stale success after changing project', async ({ page }) => {
  const fixture = await setup(page, false, true, undefined, [video.workspace, 'Other project'])
  await page.route('**/api/v1/workspaces/active', route => route.fulfill({ contentType: 'application/json', body: '{}' }))
  await planOnly(page)
  let pending: Route | undefined
  await page.route('**/api/v1/projects/*/compositions', route => { pending = route })
  await page.getByRole('button', { name: 'Queue planned repeats', exact: true }).click()
  await expect.poll(() => Boolean(pending)).toBe(true)
  const closeMenu = page.getByRole('dialog', { name: 'Generate, Director, and References menu', exact: true }).getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await page.getByRole('button', { name: /Current project: .*Open project selector/ }).click()
  await page.getByRole('dialog', { name: 'Projects', exact: true }).getByRole('button', { name: 'Other project', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Current project: Other project. Open project selector', exact: true })).toBeVisible()
  await pending!.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: 'prior-project-planned-repeat', status: 'queued' }) })
  await expect(page.getByText('Added to Queue. When the sequence finishes, open it from Gallery in Editor.', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Queue planned repeats', exact: true })).toHaveCount(0)
  await fixture.api.assertClean()
})

test('A delayed visual finalization keeps its video without restoring a plan for an edited description', async ({ page }) => {
  const fixture = await setup(page, false, true)
  await page.route('**/api/v1/blender/director-plan', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(plannedScene) }))
  let pending: Route | undefined
  await page.route('**/api/v1/blender/director-finalize', route => { pending = route })
  const prompt = page.getByPlaceholder('Describe the scene, subjects, props, movement, and camera layout…').filter({ visible: true })
  await prompt.fill('Original scene description')
  await page.getByRole('button', { name: 'Plan, review, and render', exact: true }).click()
  await expect.poll(() => Boolean(pending)).toBe(true)
  await prompt.fill('New scene description')
  await pending!.fulfill({ contentType: 'application/json', body: JSON.stringify({
    workspace: video.workspace, asset_id: 'asset', variant_id: 'variant',
    video: { filename: video.name, url: video.url }, director_reviews: [], final_plan: plannedScene,
  }) })
  await expect(fixture.keep).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Queue planned repeats', exact: true })).toHaveCount(0)
  await expect(prompt).toHaveValue('New scene description')
  await fixture.api.assertClean()
})
