import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

async function openGenerate(page: Page) {
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  const menu = page.getByRole('dialog', {
    name: 'Generate, Director, and References menu', exact: true, includeHidden: true,
  })
  if ((page.viewportSize()?.width ?? 0) < 768) {
    await expect(menu).toHaveCount(1)
    if (await menu.getAttribute('aria-hidden') === 'true') {
      const mobileMenuButton = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
      await expect(mobileMenuButton).toBeVisible()
      await mobileMenuButton.click()
    }
    await expect(menu).toHaveAttribute('aria-hidden', 'false')
    await menu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    await expect(menu.getByRole('button', { name: /^Text & frames model:/ })).toBeVisible()
    await expect(menu.getByRole('button', { name: /^References model:/ })).toBeVisible()
    return menu
  }
  const desktopMenu = page.locator('aside').filter({ has: page.locator('[data-generation-footer]') })
  await expect(desktopMenu).toBeVisible()
  await expect(desktopMenu.getByRole('button', { name: /^Text & frames model:/ })).toBeVisible()
  await expect(desktopMenu.getByRole('button', { name: /^References model:/ })).toBeVisible()
  return desktopMenu
}

async function adaptiveFooterLayout(menu: Locator) {
  return menu.locator('[data-generation-footer]').evaluate(footer => {
    const footerBox = footer.getBoundingClientRect()
    const sidebarBox = footer.closest('aside')?.getBoundingClientRect()
    return {
      footerWidth: footerBox.width,
      footerHeight: footerBox.height,
      footerRight: footerBox.right,
      sidebarWidth: sidebarBox?.width ?? 0,
      sidebarRight: sidebarBox?.right ?? 0,
      selectorWidths: Array.from(
        footer.querySelectorAll<HTMLButtonElement>('button[aria-controls^="h3-"]'),
      ).map(button => button.getBoundingClientRect().width),
    }
  })
}

async function expectPopupModelName(option: Locator, expectedName: string) {
  const label = await option.evaluate((button, expected) => {
    const node = Array.from(button.querySelectorAll<HTMLElement>('span'))
      .find(candidate => candidate.textContent?.trim() === expected)
    if (!node) return null
    return {
      text: node.textContent?.trim() || '',
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth,
    }
  }, expectedName)
  expect(label).not.toBeNull()
  if (!label) return
  expect(label.text).toBe(expectedName)
  expect(label.clientWidth).toBeGreaterThan(0)
  expect(label.scrollWidth).toBeLessThanOrEqual(label.clientWidth + 1)
}

async function boot(page: Page, scenario: Parameters<SyntheticApiController['setAdaptiveScenario']>[0]) {
  const api = await installSyntheticApi(page)
  api.setAdaptiveScenario(scenario)
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  await page.goto('/')
  const menu = await openGenerate(page)
  return { api, menu }
}

async function adaptiveState(page: Page) {
  return page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    const p = useStore.getState().params
    return {
      fl: p.h3_adaptive_fl2va_model,
      ref: p.h3_adaptive_ref2va_model,
      flLoras: p.h3_fl2va_loras,
      refLoras: p.h3_ref2va_loras,
      flWeights: p.h3_fl2va_loras_multipliers,
      refWeights: p.h3_ref2va_loras_multipliers,
    }
  })
}

async function keyboardWeight(slider: Locator, value: number) {
  await slider.scrollIntoViewIfNeeded()
  await slider.focus()
  await slider.press('Home')
  for (let step = 0; step < Math.round(value / 0.05); step += 1) await slider.press('ArrowRight')
  await expect(slider).toHaveValue(String(value))
}

test('adaptive model catalog has a visible loading state before the synthetic response', async ({ page }, info) => {
  const api = await installSyntheticApi(page)
  api.setAdaptiveScenario('ready')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  let releaseModels!: () => void
  const catalogGate = new Promise<void>(resolve => { releaseModels = resolve })
  await page.route('**/api/v1/models', async route => {
    await catalogGate
    await route.fallback()
  })
  try {
    await page.goto('/')
    const menu = await openGenerate(page)
    await menu.getByRole('button', { name: /^References model:/ }).click()
    const popup = menu.getByRole('dialog', { name: 'References models', exact: true })
    await expect(popup.getByRole('status')).toHaveText('Loading model catalog…')
    await page.screenshot({ path: info.outputPath('adaptive-loading.png'), animations: 'disabled' })
    releaseModels()
    await expect(popup.getByRole('button', { name: /^Synthetic Reference Model/ })).toBeVisible()
  } finally {
    releaseModels()
  }
  await api.assertClean()
})

test('adaptive controls keep both choices visible, keyboard operable and responsive', async ({ page }, info) => {
  const mobile = info.project.name === 'android-like-chromium'
  await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { api, menu } = await boot(page, 'ready')
  const layout = await adaptiveFooterLayout(menu)
  expect(layout.sidebarWidth).toBeGreaterThan(0)
  expect(layout.footerWidth).toBeGreaterThanOrEqual(layout.sidebarWidth - 36)
  expect(layout.footerRight).toBeLessThanOrEqual(layout.sidebarRight + 1)
  expect(layout.footerHeight).toBeLessThanOrEqual(320)
  expect(layout.selectorWidths).toHaveLength(2)
  for (const width of layout.selectorWidths) expect(width).toBeGreaterThanOrEqual(180)
  const frames = menu.getByRole('button', { name: /^Text & frames model:/ })
  const references = menu.getByRole('button', { name: /^References model:/ })
  await expect(frames).toHaveAccessibleName(/Synthetic H3/)
  await expect(references).toHaveAccessibleName(/Synthetic Reference Model/)
  for (const [trigger, name] of [[frames, 'Text & frames models'], [references, 'References models']] as const) {
    await trigger.scrollIntoViewIfNeeded()
    await trigger.hover()
    await trigger.focus()
    await trigger.press('Enter')
    const openPopup = menu.getByRole('dialog', { name, exact: true })
    await expect(openPopup).toBeVisible()
    await expect.poll(() => openPopup.evaluate(element => element.contains(document.activeElement))).toBe(true)
    const expectedName = name === 'Text & frames models' ? 'Synthetic H3' : 'Synthetic Reference Model'
    await expectPopupModelName(
      openPopup.getByRole('button', { name: new RegExp(`^${expectedName}`) }).first(),
      expectedName,
    )
    await page.keyboard.press('Escape')
    await expect(openPopup).toBeHidden()
    await expect(menu).toBeVisible()
    await expect(trigger).toBeFocused()
    expect(await trigger.evaluate(element => {
      const style = getComputedStyle(element)
      return !element.matches(':focus-visible')
        || style.outlineStyle !== 'none'
        || style.boxShadow !== 'none'
    })).toBe(true)
    expect(await trigger.evaluate(element => (
      Math.max(...getComputedStyle(element).transitionDuration.split(',').map(value => parseFloat(value)))
    ))).toBeLessThanOrEqual(0.001)
    await trigger.click()
    await expect(openPopup).toBeVisible()
    await menu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    await expect(openPopup).toBeHidden()
  }
  const popup = menu.getByRole('dialog', { name: 'Text & frames models', exact: true })
  await frames.click()
  await popup.getByRole('button', { name: /^Synthetic Frame Alternative/ }).click()
  await expect(frames).toHaveAccessibleName(/Synthetic Frame Alternative/)
  await expect(references).toHaveAccessibleName(/Synthetic Reference Model/)
  await page.screenshot({ path: info.outputPath('adaptive-models.png') })

  const axe = await new AxeBuilder({ page })
    .include('button[aria-controls="h3-fl2va-selector-menu"]')
    .include('button[aria-controls="h3-ref2va-selector-menu"]')
    .analyze()
  expect(axe.violations).toEqual([])
  await page.setViewportSize({ width: 320, height: 568 })
  const narrowMenu = await openGenerate(page)
  const narrowLayout = await adaptiveFooterLayout(narrowMenu)
  expect(narrowLayout.sidebarWidth).toBeGreaterThan(0)
  expect(narrowLayout.footerWidth).toBeGreaterThanOrEqual(narrowLayout.sidebarWidth - 36)
  expect(narrowLayout.footerRight).toBeLessThanOrEqual(narrowLayout.sidebarRight + 1)
  expect(narrowLayout.footerHeight).toBeLessThanOrEqual(320)
  expect(narrowLayout.selectorWidths).toHaveLength(2)
  for (const width of narrowLayout.selectorWidths) expect(width).toBeGreaterThanOrEqual(180)
  const narrowFrames = narrowMenu.getByRole('button', { name: /^Text & frames model:/ })
  const narrowReferences = narrowMenu.getByRole('button', { name: /^References model:/ })
  for (const trigger of [narrowFrames, narrowReferences]) {
    await trigger.scrollIntoViewIfNeeded()
    const box = await trigger.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.height).toBeGreaterThanOrEqual(44)
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(321)
  }
  for (const [trigger, name, expectedName] of [
    [narrowFrames, 'Text & frames models', 'Synthetic Frame Alternative'],
    [narrowReferences, 'References models', 'Synthetic Reference Model'],
  ] as const) {
    await trigger.click()
    const popup = narrowMenu.getByRole('dialog', { name, exact: true })
    await expect(popup).toBeVisible()
    await expectPopupModelName(
      popup.getByRole('button', { name: new RegExp(`^${expectedName}`) }).first(),
      expectedName,
    )
    await page.keyboard.press('Escape')
    await expect(popup).toBeHidden()
    await expect(trigger).toBeFocused()
  }
  for (const action of await narrowMenu.locator('[data-generation-footer]').getByRole('button').all()) {
    await action.scrollIntoViewIfNeeded()
    const box = await action.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(321)
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual(569)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: info.outputPath('adaptive-models-narrow.png') })
  await page.reload()
  const restored = await openGenerate(page)
  await expect(restored.getByRole('button', { name: /^Text & frames model:/ }))
    .toHaveAccessibleName(/Synthetic Frame Alternative/)
  await api.assertClean()
})

test('adaptive LoRA edits preserve independent weights and explicit clears in the held request', async ({ page }, info) => {
  await page.setViewportSize(info.project.name === 'android-like-chromium'
    ? { width: 390, height: 844 } : { width: 1440, height: 900 })
  const { api, menu } = await boot(page, 'ready')
  await menu.locator('textarea').first().fill('A blue cube rests on a white table.')
  await menu.getByRole('button', { name: 'Open Advanced Settings', exact: true }).click()
  const advanced = page.getByRole('dialog', { name: 'Advanced Settings', exact: true })
  const frames = advanced.getByRole('group', { name: 'Text & frames · FL2VA adapters', exact: true })
  const references = advanced.getByRole('group', { name: 'References · Ref2VA adapters', exact: true })
  await frames.getByRole('button', { name: 'Add synthetic-shared from Text & frames', exact: true }).click()
  await references.getByRole('button', { name: 'Add synthetic-shared from References', exact: true }).click()
  await keyboardWeight(frames.getByRole('slider', { name: 'synthetic-shared Text & frames weight', exact: true }), 0.25)
  await keyboardWeight(references.getByRole('slider', { name: 'synthetic-shared References weight', exact: true }), 0.8)
  if (info.project.name === 'android-like-chromium') {
    for (const group of [frames, references]) {
      const slider = group.getByRole('slider')
      await slider.scrollIntoViewIfNeeded()
      expect((await slider.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
  }
  const axe = await new AxeBuilder({ page })
    .include('[aria-label="Text & frames · FL2VA adapters"]')
    .include('[aria-label="References · Ref2VA adapters"]')
    .analyze()
  expect(axe.violations).toEqual([])
  const weighted = await adaptiveState(page)
  expect(weighted.flWeights).toBe('0.25')
  expect(weighted.refWeights).toBe('0.8')
  await page.screenshot({ path: info.outputPath('adaptive-loras.png') })
  await frames.getByRole('button', { name: 'Remove synthetic-shared from Text & frames', exact: true }).first().click()
  await expect.poll(async () => (await adaptiveState(page)).flLoras).toEqual([])
  expect((await adaptiveState(page)).refLoras).toEqual(['synthetic-shared.safetensors'])
  await advanced.getByRole('button', { name: 'Close Advanced Settings', exact: true }).click()
  const hold = menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true })
  await expect(hold).toBeEnabled()
  await hold.click()
  await expect.poll(() => api.generationRequests().length).toBe(1)
  const request = api.generationRequests()[0]
  expect(request.h3_fl2va_loras).toEqual([])
  expect(request.h3_ref2va_loras).toEqual(['synthetic-shared.safetensors'])
  expect(request.h3_ref2va_loras_multipliers).toBe('0.8')
  await api.assertClean()
})

for (const scenario of ['missing-reference', 'blocked-reference'] as const) {
  test(`adaptive ${scenario} stays visible and blocks submission`, async ({ page }, info) => {
    const { api, menu } = await boot(page, scenario)
    await expect(menu.getByRole('button', { name: /^References model:/ })).toBeVisible()
    await expect(menu.getByRole('button', {
      name: scenario === 'missing-reference' ? 'Model unavailable' : 'License required', exact: true,
    })).toBeDisabled()
    if (scenario === 'missing-reference') {
      await menu.getByRole('button', { name: /^References model:/ }).click()
      const popup = menu.getByRole('dialog', { name: 'References models', exact: true })
      await expect(popup.getByRole('status')).toHaveText('No compatible models are enabled for this group.')
      await page.screenshot({ path: info.outputPath('adaptive-empty-group.png') })
    }
    expect(api.generationRequests()).toEqual([])
    await api.assertClean()
  })
}

test('malformed reference LoRAs remain repairable while frame LoRAs can still be edited', async ({ page }, info) => {
  const { api, menu } = await boot(page, 'ready')
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    useStore.setState({ params: { ...useStore.getState().params,
      h3_adaptive_conditioning: false,
      h3_fl2va_loras: [], h3_fl2va_loras_multipliers: '',
      h3_ref2va_loras: 'synthetic-invalid-list', h3_ref2va_loras_multipliers: '',
    } })
    useStore.getState().setParam('h3_adaptive_conditioning', true)
  })
  await expect(menu.getByRole('button', { name: /^Text & frames model:/ })).toBeVisible()
  await expect(menu.getByRole('button', { name: /^References model:/ })).toBeVisible()
  await menu.getByRole('button', { name: /^Text & frames model:/ }).click()
  await menu.getByRole('dialog', { name: 'Text & frames models', exact: true })
    .getByRole('button', { name: /^Synthetic Frame Alternative/ }).click()
  await expect(menu.getByRole('button', { name: /^Text & frames model:/ }))
    .toHaveAccessibleName(/Synthetic Frame Alternative/)
  expect((await adaptiveState(page)).refLoras).toBe('synthetic-invalid-list')
  await menu.getByRole('button', { name: 'Open Advanced Settings', exact: true }).click()
  const advanced = page.getByRole('dialog', { name: 'Advanced Settings', exact: true })
  const frames = advanced.getByRole('group', { name: 'Text & frames · FL2VA adapters', exact: true })
  const references = advanced.getByRole('group', { name: 'References · Ref2VA adapters', exact: true })
  await expect(references.getByRole('status')).toContainText('Saved LoRA settings need repair.')
  await frames.getByRole('button', { name: 'Add synthetic-shared from Text & frames', exact: true }).click()
  await expect.poll(async () => (await adaptiveState(page)).flLoras).toEqual(['synthetic-shared.safetensors'])
  expect((await adaptiveState(page)).refLoras).toBe('synthetic-invalid-list')
  await page.screenshot({ path: info.outputPath('adaptive-repair-state.png') })
  await references.getByRole('button', { name: 'Clear saved References LoRAs', exact: true }).click()
  await expect.poll(async () => (await adaptiveState(page)).refLoras).toEqual([])
  expect((await adaptiveState(page)).flLoras).toEqual(['synthetic-shared.safetensors'])
  await api.assertClean()
})

test('invalid paired models can be repaired independently without LoRA requests for invalid IDs', async ({ page }) => {
  const { api, menu } = await boot(page, 'ready')
  const loraRequests: string[] = []
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname
    if (pathname.includes('/api/v1/loras/')) loraRequests.push(pathname)
  })
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    useStore.setState({ params: { ...useStore.getState().params,
      h3_adaptive_fl2va_model: 'synthetic-invalid-frame',
      h3_adaptive_ref2va_model: 'synthetic-invalid-reference',
    } })
  })
  await expect(menu.getByRole('button', { name: 'Choose H3 models', exact: true })).toBeDisabled()
  await menu.getByRole('button', { name: 'Open Advanced Settings', exact: true }).click()
  const advanced = page.getByRole('dialog', { name: 'Advanced Settings', exact: true })
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  expect(loraRequests.some(path => path.includes('synthetic-invalid'))).toBe(false)
  await advanced.getByRole('button', { name: 'Close Advanced Settings', exact: true }).click()
  const frames = menu.getByRole('button', { name: /^Text & frames model:/ })
  await frames.click()
  await menu.getByRole('dialog', { name: 'Text & frames models', exact: true })
    .getByRole('button', { name: /^Synthetic H3(?: |$)/ }).click()
  await expect(frames).toHaveAccessibleName(/Synthetic H3/)
  expect((await adaptiveState(page)).ref).toBe('synthetic-invalid-reference')
  await menu.getByRole('button', { name: /^References model:/ }).click()
  await menu.getByRole('dialog', { name: 'References models', exact: true })
    .getByRole('button', { name: /^Synthetic Reference Model/ }).click()
  await expect.poll(async () => (await adaptiveState(page)).ref).toBe('minimax_h3_ref2va')
  expect(loraRequests.some(path => path.includes('synthetic-invalid'))).toBe(false)
  await api.assertClean()
})
