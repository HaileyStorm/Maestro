import { expect, test } from '@playwright/test'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const IMAGE = {
  name: 'gallery-input.png',
  url: '/api/v1/file/gallery-input.png?workspace=Synthetic%20project',
  type: 'image', mode: 'image', edit_sub_mode: null,
  artifact_class: 'final', linked_component_count: 0,
  favorite: false, size: PNG.length, created_at: 1_725_000_400,
  revision: 'gallery-input-v1', workspace: 'Synthetic project',
  private: false, explicit: false,
}

let api: SyntheticApiController | undefined
test.beforeEach(async ({ page }) => {
  api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ outputs: [IMAGE], total: 1 }),
  }))
  await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ params: null, source: 'none' }),
  }))
})
test.afterEach(async () => { await api?.assertClean(); api = undefined })

async function inputState(page: import('@playwright/test').Page) {
  return page.evaluate(async () => {
    const storeModule = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ storeModule)
    const state = useStore.getState()
    return {
      mode: state.generationMode, sidebar: state.sidebarMode,
      imageRefs: state.imageRefs.map((file: File) => file.name),
      startImage: state.startImage?.name ?? null,
      imageMode: state.params.image_mode,
    }
  })
}

test('Gallery image destination reports failed fetches and retries into Image mode', async ({ page }) => {
  let failNextFetch = true
  await page.route('**/api/v1/file/gallery-input.png*', route => {
    if (route.request().resourceType() === 'fetch' && failNextFetch) {
      failNextFetch = false
      return route.fulfill({ status: 503, contentType: 'text/plain', body: 'Unavailable' })
    }
    return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Gallery' }).click()
  await page.getByRole('button', { name: `Choose a Studio input for ${IMAGE.name}` }).click()
  const destinations = page.getByRole('group', { name: `Studio destinations for ${IMAGE.name}` })
  await expect(destinations.getByRole('button', { name: 'Use in Image mode' })).toBeVisible()
  await expect(destinations.getByRole('button', { name: 'Use as Video start frame' })).toBeVisible()
  await destinations.getByRole('button', { name: 'Use in Image mode' }).click()
  await expect(page.getByRole('alert')).toContainText('Could not read this Gallery image (503). Try again.')
  expect((await inputState(page)).imageRefs).toEqual([])

  await destinations.getByRole('button', { name: 'Use in Image mode' }).click()
  await expect(page.getByRole('button', { name: 'Image', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect(await inputState(page)).toMatchObject({ mode: 'image', sidebar: 'studio', imageRefs: [IMAGE.name] })
})

test('Gallery keeps its one-click Video start-frame handoff', async ({ page }) => {
  await page.route('**/api/v1/file/gallery-input.png*', route => route.fulfill({
    status: 200, contentType: 'image/png', body: PNG,
  }))
  await page.goto('/')
  await page.getByRole('tab', { name: 'Gallery' }).click()
  await page.getByRole('button', { name: `Use ${IMAGE.name} as a start frame` }).click()
  await expect.poll(() => inputState(page)).toMatchObject({ mode: 'video', sidebar: 'studio', startImage: IMAGE.name, imageMode: 0 })
})

test('Gallery input fetch cannot commit after the selected mode changes', async ({ page }) => {
  let requestStarted!: () => void
  let releaseRequest!: () => void
  const started = new Promise<void>(resolve => { requestStarted = resolve })
  const release = new Promise<void>(resolve => { releaseRequest = resolve })
  await page.route('**/api/v1/file/gallery-input.png*', async route => {
    if (route.request().resourceType() === 'fetch') {
      requestStarted()
      await release
    }
    await route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Gallery' }).click()
  await page.getByRole('button', { name: `Choose a Studio input for ${IMAGE.name}` }).click()
  await page.getByRole('group', { name: `Studio destinations for ${IMAGE.name}` }).getByRole('button', { name: 'Use in Image mode' }).click()
  await started
  await page.evaluate(async () => {
    const storeModule = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ storeModule)
    useStore.getState().setGenerationMode('audio')
  })
  releaseRequest()
  await expect(page.getByRole('alert')).toContainText('The project or input mode changed while the image was loading.')
  expect(await inputState(page)).toMatchObject({ mode: 'audio', imageRefs: [], startImage: null })
})
