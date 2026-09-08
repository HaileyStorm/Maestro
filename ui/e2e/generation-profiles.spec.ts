import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

test('saved profiles lead Generate and restore through Advanced without replacing job text', async ({ page, baseURL }, info) => {
  const api = await installSyntheticApi(page)
  api.setAdaptiveScenario('ready')
  const records: Array<Record<string, unknown>> = []
  await page.route(`${baseURL}/api/v1/presets**`, async route => {
    if (route.request().method() === 'POST') {
      const record = { ...route.request().postDataJSON(), created_at: 1 }
      records.push(record)
      await route.fulfill({ json: record })
    } else if (route.request().method() === 'GET') {
      await route.fulfill({ json: { presets: records } })
    } else {
      await route.fallback()
    }
  })
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  const mobileMenu = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  if (await mobileMenu.isVisible()) await mobileMenu.click()
  const profiles = page.locator('[data-generation-profiles="sidebar"]')
  await expect(profiles).toBeVisible()
  await expect(profiles.getByRole('combobox')).toBeEnabled()
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    useStore.getState().setParam('num_inference_steps', 31)
    useStore.getState().setParam('seed', 42)
    useStore.getState().setFilmGrainIntensity(0.2)
  })
  await profiles.getByRole('button', { name: 'Save', exact: true }).click()
  await profiles.getByRole('textbox', { name: 'New profile name' }).fill('Synthetic full profile')
  await profiles.getByRole('button', { name: 'Save as new', exact: true }).click()
  await expect(profiles.getByRole('status')).toHaveText('Profile saved.')
  expect(records).toHaveLength(1)
  expect(records[0].profile_version).toBe(2)
  expect(records[0].params).toMatchObject({ num_inference_steps: 31, seed: 42 })
  expect(records[0].params).not.toHaveProperty('prompt')
  expect(records[0].ui_settings).toMatchObject({ filmGrainIntensity: 0.2 })
  await profiles.getByRole('combobox').selectOption(String(records[0].id))
  await profiles.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('profiles-generate.png'), animations: 'disabled' })
  const mainAudit = await new AxeBuilder({ page }).include('[data-generation-profiles="sidebar"]').analyze()
  expect(mainAudit.violations).toEqual([])
  await page.getByRole('button', { name: 'Open Advanced Settings', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: 'Advanced Settings', exact: true })
  const advancedProfiles = drawer.locator('[data-generation-profiles="advanced"]')
  await expect(advancedProfiles.getByRole('combobox')).toHaveValue(String(records[0].id))
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    useStore.getState().setParam('num_inference_steps', 4)
    useStore.getState().setParam('prompt', 'Current synthetic job text')
    useStore.getState().setFilmGrainIntensity(0)
  })
  await advancedProfiles.getByRole('button', { name: 'Load', exact: true }).click()
  await expect(advancedProfiles.getByRole('status')).toHaveText('Synthetic full profile loaded.')
  const restored = await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(modulePath)
    const state = useStore.getState()
    return { steps: state.params.num_inference_steps, seed: state.params.seed, prompt: state.params.prompt, grain: state.filmGrainIntensity }
  })
  expect(restored).toEqual({ steps: 31, seed: 42, prompt: 'Current synthetic job text', grain: 0.2 })
  await page.screenshot({ path: info.outputPath('profiles-advanced.png'), animations: 'disabled' })
  const advancedAudit = await new AxeBuilder({ page }).include('[data-generation-profiles="advanced"]').analyze()
  expect(advancedAudit.violations).toEqual([])
  if (info.project.name === 'android-like-chromium') {
    for (const control of await advancedProfiles.locator('button, select, input').all()) {
      if (await control.isVisible()) expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
  }
  await drawer.getByRole('button', { name: 'Close Advanced Settings', exact: true }).click()
  await expect(drawer).toBeHidden()
  await api.assertClean()
})
