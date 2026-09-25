import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64')
const PUBLIC_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#172d49"/><stop offset="1" stop-color="#db9570"/></linearGradient></defs><rect width="1280" height="720" fill="url(#sky)"/><circle cx="940" cy="245" r="92" fill="#ffcf9a"/><path d="M0 570 330 320 540 500 790 260 1280 590V720H0" fill="#172b36"/><path d="M0 650 400 510 820 610 1280 440V720H0" fill="#253d42"/></svg>'
const output = (name: string, isPrivate: boolean, createdAt: number) => ({
  name,
  url: `/api/v1/file/${name}?workspace=Synthetic%20project`,
  type: 'image', mode: 'image', edit_sub_mode: null,
  artifact_class: 'final', linked_component_count: 0,
  favorite: false, size: PNG.length, created_at: createdAt,
  revision: `${name}-revision`, workspace: 'Synthetic project',
  private: isPrivate, explicit: false,
})

const PRIVATE = output('private-scene.png', true, 1_725_000_500)
const PUBLIC = output('public-scene.png', false, 1_725_000_400)
const BROKEN = output('broken-scene.png', false, 1_725_000_300)
const ZERO_SIZE = output('zero-size-scene.png', false, 1_725_000_200)

let api: SyntheticApiController | undefined
test.beforeEach(async ({ page }) => { api = await installSyntheticApi(page) })
test.afterEach(async () => { await api?.assertClean(); api = undefined })

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`${viewport.name} viewer keeps private previews gated and supports navigation`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
    api!.setAccountScenario('remote-user')
    let privateMediaRequests = 0
    let availableOutputs = [PRIVATE, PUBLIC]
    await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ outputs: availableOutputs, total: availableOutputs.length }),
    }))
    await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ params: null, source: 'none' }),
    }))
    await page.route('**/api/v1/file/*.png*', route => {
      if (route.request().url().includes(PRIVATE.name)) {
        privateMediaRequests += 1
        return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
      }
      return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: PUBLIC_SVG })
    })

    await page.goto('/')
    await page.getByRole('tab', { name: 'Gallery' }).click()
    const opener = page.getByRole('button', { name: `Open ${PRIVATE.name} in Gallery viewer` })
    await expect(opener).toBeVisible()
    expect(privateMediaRequests).toBe(0)
    await opener.click()
    const viewer = page.getByRole('dialog', { name: 'Gallery viewer' })
    await expect(viewer).toBeVisible()
    await expect(viewer.getByRole('button', { name: 'Show preview' })).toBeVisible()
    expect(privateMediaRequests).toBe(0)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-viewer-gated.png`) })

    await viewer.getByRole('button', { name: 'Show preview' }).click()
    await expect(viewer.locator('[data-media-status="ready"]')).toBeVisible()
    await expect(viewer.getByRole('img', { name: PRIVATE.name })).toBeVisible()
    await expect.poll(() => privateMediaRequests).toBeGreaterThan(0)
    await viewer.getByRole('button', { name: 'Blur preview' }).click()
    await expect(viewer.getByRole('button', { name: 'Show preview' })).toBeVisible()
    await expect(viewer.locator(`img[src*="${PRIVATE.name}"]`)).toHaveCount(0)

    await viewer.getByRole('button', { name: 'Next media' }).click()
    await expect(viewer.locator('[data-media-status="ready"]')).toBeVisible()
    await expect(viewer.getByRole('img', { name: PUBLIC.name })).toBeVisible()
    await expect(viewer.getByRole('button', { name: 'Next media' })).toBeDisabled()
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-viewer-public.png`) })
    await viewer.getByRole('button', { name: 'Compare' }).click()
    await expect(viewer.getByRole('group', { name: 'Before and after image comparison' })).toBeVisible()
    await expect(viewer.getByRole('button', { name: 'Show Before preview' })).toBeVisible()
    await expect(viewer.locator(`img[src*="${PRIVATE.name}"]`)).toHaveCount(0)
    await viewer.getByRole('button', { name: 'Show Before preview' }).click()
    await expect(viewer.getByRole('group', { name: 'Before and after image comparison' }).locator(`img[src*="${PRIVATE.name}"]`)).toHaveCount(1)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-viewer-comparison.png`) })
    const comparisonAccessibility = await new AxeBuilder({ page }).include('[role="dialog"][aria-label="Gallery viewer"]').analyze()
    expect(comparisonAccessibility.violations.filter(violation =>
      violation.impact === 'critical' || violation.impact === 'serious')).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)
    const split = viewer.getByRole('slider', { name: 'Comparison split' })
    await split.press('End')
    expect(await split.evaluate(element => (element as HTMLInputElement).value)).toBe('100')
    await split.press('ArrowLeft')
    expect(await split.evaluate(element => (element as HTMLInputElement).value)).toBe('99')
    await split.evaluate(element => {
      for (const [type, x] of [['touchstart', 20], ['touchend', 250]] as const) {
        const event = new Event(type, { bubbles: true, cancelable: true })
        Object.defineProperty(event, 'changedTouches', { value: [{ clientX: x, clientY: 200 }] })
        element.dispatchEvent(event)
      }
    })
    await expect(viewer.getByRole('heading', { name: PUBLIC.name })).toBeVisible()
    await expect(viewer.getByRole('button', { name: 'Close comparison' })).toBeVisible()
    await viewer.getByRole('button', { name: 'Blur Before preview' }).click()
    await expect(viewer.locator(`img[src*="${PRIVATE.name}"]`)).toHaveCount(0)
    await viewer.getByRole('button', { name: 'Swap' }).click()
    await expect(viewer.getByRole('button', { name: 'Show After preview' })).toBeVisible()
    await expect(viewer.locator(`img[src*="${PRIVATE.name}"]`)).toHaveCount(0)
    await viewer.getByRole('button', { name: 'Close comparison' }).click()
    await expect(viewer.getByRole('img', { name: PUBLIC.name })).toBeVisible()
    const accessibility = await new AxeBuilder({ page }).include('[role="dialog"][aria-label="Gallery viewer"]').analyze()
    expect(accessibility.violations.filter(violation =>
      violation.impact === 'critical' || violation.impact === 'serious')).toEqual([])
    if (viewport.name === 'mobile') {
      for (const name of ['Close Gallery viewer', 'Previous media', 'Next media']) {
        const bounds = await viewer.getByRole('button', { name }).boundingBox()
        expect(bounds?.width).toBeGreaterThanOrEqual(44)
        expect(bounds?.height).toBeGreaterThanOrEqual(44)
      }
      await page.evaluate(() => { document.documentElement.style.zoom = '200%' })
      await expect(viewer.getByRole('button', { name: 'Close Gallery viewer' })).toBeInViewport()
      await page.evaluate(() => { document.documentElement.style.zoom = '' })
    }
    await viewer.getByRole('button', { name: 'Previous media' }).focus()
    await page.keyboard.press('ArrowLeft')
    await expect(viewer.getByRole('button', { name: 'Show preview' })).toBeVisible()
    await viewer.getByRole('button', { name: 'Next media' }).click()
    await expect(viewer.getByRole('img', { name: PUBLIC.name })).toBeVisible()
    availableOutputs = [PRIVATE]
    await page.evaluate(async () => {
      const storeModule = '/src/stores/useStore.ts'
      const { useStore } = await import(/* @vite-ignore */ storeModule)
      await useStore.getState().loadOutputs()
    })
    await expect(viewer.getByRole('button', { name: 'Show preview' })).toBeVisible()
    await expect(viewer.getByRole('heading', { name: PRIVATE.name })).toBeVisible()
    await expect(viewer.getByRole('button', { name: 'Next media' })).toBeDisabled()
    await page.keyboard.press('Escape')
    await expect(viewer).toHaveCount(0)
    await expect(opener).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)).toBe(false)

    availableOutputs = [PRIVATE, BROKEN]
    await page.route('**/api/v1/file/broken-scene.png*', route => route.fulfill({
      status: 503, contentType: 'text/plain', body: 'Synthetic media unavailable',
    }))
    await page.reload()
    await page.getByRole('tab', { name: 'Gallery' }).click()
    await opener.click()
    await page.getByRole('dialog', { name: 'Gallery viewer' }).getByRole('button', { name: 'Next media' }).click()
    await expect(page.getByRole('dialog', { name: 'Gallery viewer' }).getByRole('alert')).toContainText('could not be loaded')

    availableOutputs = [PRIVATE, ZERO_SIZE]
    let zeroSizeRequests = 0
    await page.route('**/api/v1/file/zero-size-scene.png*', route => {
      zeroSizeRequests += 1
      return route.fulfill({
        status: 200, contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"/>',
      })
    })
    await page.reload()
    await page.getByRole('tab', { name: 'Gallery' }).click()
    await page.getByRole('button', { name: `Open ${ZERO_SIZE.name} in Gallery viewer` }).click()
    await expect(page.getByRole('dialog', { name: 'Gallery viewer' }).getByRole('alert')).toContainText('could not be loaded')
    expect(zeroSizeRequests).toBeGreaterThan(1)
  })
}

test('comparison stays closable when its other image disappears', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  api!.setAccountScenario('remote-user')
  let availableOutputs = [PRIVATE, PUBLIC]
  await page.route(/\/api\/v1\/outputs(?:\?.*)?$/, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ outputs: availableOutputs, total: availableOutputs.length }),
  }))
  await page.route('**/api/v1/outputs/*/metadata*', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ params: null, source: 'none' }),
  }))
  await page.route('**/api/v1/file/*.png*', route => route.fulfill({
    status: 200, contentType: 'image/svg+xml', body: PUBLIC_SVG,
  }))
  await page.goto('/')
  await page.getByRole('tab', { name: 'Gallery' }).click()
  await page.getByRole('button', { name: `Open ${PRIVATE.name} in Gallery viewer` }).click()
  const viewer = page.getByRole('dialog', { name: 'Gallery viewer' })
  await viewer.getByRole('button', { name: 'Show preview' }).click()
  await viewer.getByRole('button', { name: 'Compare' }).click()
  await expect(viewer.getByRole('group', { name: 'Before and after image comparison' })).toBeVisible()

  availableOutputs = [PRIVATE]
  await page.evaluate(async () => {
    const storeModule = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ storeModule)
    await useStore.getState().loadOutputs()
  })
  await expect(viewer).toBeVisible()
  await expect(viewer.getByText('Choose two different images from this project to compare.')).toBeVisible()
  await expect(viewer.getByRole('button', { name: 'Close comparison' })).toBeEnabled()
  await viewer.getByRole('button', { name: 'Close comparison' }).click()
  await expect(viewer.getByRole('button', { name: 'Compare' })).toBeDisabled()
  await viewer.getByRole('button', { name: 'Close Gallery viewer' }).click()
  await expect(page.getByRole('button', { name: `Open ${PRIVATE.name} in Gallery viewer` })).toBeFocused()
})
