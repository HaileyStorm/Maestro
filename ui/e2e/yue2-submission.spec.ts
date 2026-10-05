import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

const workspace = 'Synthetic project'
type Submission = { requestId: string; workspace: string; form: Record<string, unknown> }
const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
})
const take = (body: Submission) => ({
  id: `take-${body.requestId}`, requestId: body.requestId, project: workspace,
  title: 'Recovered song', status: 'succeeded', stage: 'done', duration: 20, elapsed: 30,
})

async function setup(page: Page, mode: 'lost' | 'held' | 'training') {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  const submissions: Submission[] = []
  const trainingSubmissions: Record<string, unknown>[] = []
  let library: ReturnType<typeof take>[] = mode === 'training'
    ? [take({ requestId: 'finished-source', workspace, form: {} })] : []
  let pending: Route | undefined
  await page.route('**/api/v1/yue2/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/status')) return json(route, { available: true, sampleRate: 44100, queue: 'local', loras: [], training: mode === 'training' })
    if (path.endsWith('/library')) return json(route, { tracks: library })
    if (path.endsWith('/training')) {
      if (route.request().method() === 'POST') {
        trainingSubmissions.push(route.request().postDataJSON())
        return json(route, { detail: 'Synthetic uncertain training acknowledgement' }, 503)
      }
      return json(route, { jobs: [] })
    }
    if (path.endsWith('/generations')) {
      const body = route.request().postDataJSON() as Submission
      submissions.push(body)
      if (submissions.length === 1) {
        if (mode === 'held') { pending = route; return }
        return json(route, { detail: 'Synthetic lost acknowledgement' }, 500)
      }
      library = [take(body)]
      return json(route, { requestId: body.requestId, reused: true, tracks: library })
    }
    throw new Error(`Unexpected YuE2 endpoint: ${path}`)
  })
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  const menu = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  if (await menu.isVisible()) {
    await menu.click()
    await page.getByRole('button', { name: 'Open Generate', exact: true }).click()
  }
  await page.getByRole('button', { name: 'Audio', exact: true }).click()
  await page.getByRole('button', { name: 'Music', exact: true }).click()
  await page.getByRole('button', { name: 'YuE2', exact: true }).click()
  await page.getByRole('textbox', { name: 'Style and music caption', exact: true }).fill('Warm piano, steady pulse')
  await page.getByRole('textbox', { name: 'Lyrics', exact: true }).fill('[Verse]\nThe coffee waits beside the door')
  const generate = page.getByRole('button', { name: 'Generate with YuE2', exact: true })
  await expect(generate).toBeEnabled()
  return { api, submissions, trainingSubmissions, generate,
    acceptFirst: () => { library = [take(submissions[0])] },
    rejectHeld: async () => {
      await expect.poll(() => Boolean(pending)).toBe(true)
      const response = page.waitForResponse(r => r.url().includes('/api/v1/yue2/generations') && r.status() === 422)
      await json(pending!, { detail: 'Late obsolete rejection' }, 422)
      pending = undefined
      await response
    },
  }
}

test('lost acknowledgement retains exact submission after composer reopening and edits', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.generate.click()
  const retry = page.getByRole('button', { name: 'Retry this submission', exact: true })
  await expect(retry).toBeEnabled()
  await expect(fixture.generate).toBeDisabled()
  expect(fixture.submissions).toHaveLength(1)
  await page.getByRole('button', { name: 'Speech', exact: true }).click()
  await page.getByRole('button', { name: 'Music', exact: true }).click()
  await expect(retry).toBeEnabled()
  await page.getByRole('textbox', { name: 'Style and music caption', exact: true }).fill('Changed instruments')
  await page.getByRole('textbox', { name: 'Lyrics', exact: true }).fill('Changed song')
  await retry.click()
  await expect(page.getByRole('article', { name: 'Recovered song · succeeded', exact: true })).toBeVisible()
  await expect(retry).toHaveCount(0)
  expect(fixture.submissions).toHaveLength(2)
  expect(fixture.submissions[1]).toEqual(fixture.submissions[0])
  await fixture.api.assertClean()
})

test('library confirmation adopts lost submission without another POST', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.generate.click()
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  fixture.acceptFirst()
  await page.getByRole('button', { name: 'Refresh YuE2 status', exact: true }).click()
  await expect(page.getByRole('article', { name: 'Recovered song · succeeded', exact: true })).toBeVisible()
  await expect(fixture.generate).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toHaveCount(0)
  expect(fixture.submissions).toHaveLength(1)
  await fixture.api.assertClean()
})

test('library acceptance releases held submit and late rejection cannot alter newer submission', async ({ page }) => {
  const fixture = await setup(page, 'held')
  await fixture.generate.click()
  await expect.poll(() => fixture.submissions.length).toBe(1)
  fixture.acceptFirst()
  await page.getByRole('button', { name: 'Refresh YuE2 status', exact: true }).click()
  await expect(fixture.generate).toBeEnabled()
  await fixture.generate.click()
  await expect.poll(() => fixture.submissions.length).toBe(2)
  expect(fixture.submissions[1].requestId).not.toBe(fixture.submissions[0].requestId)
  await expect(fixture.generate).toBeEnabled()
  await fixture.rejectHeld()
  await expect(page.getByText('Late obsolete rejection', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('article', { name: 'Recovered song · succeeded', exact: true })).toHaveCount(1)
  await expect(fixture.generate).toBeEnabled()
  expect(fixture.submissions).toHaveLength(2)
  await fixture.api.assertClean()
})

test('training retry inputs are cleared when another account opens the same project', async ({ page }) => {
  const fixture = await setup(page, 'training')
  await page.getByText('Train a YuE2 artist or style LoRA', { exact: true }).click()
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Account A private style')
  await page.getByRole('textbox', { name: 'Trigger word', exact: true }).fill('sv_account_a')
  await page.getByRole('checkbox', { name: 'Recovered song', exact: true }).check()
  await page.getByRole('textbox', { name: 'Style caption', exact: true }).fill('Account A private caption')
  await page.getByRole('textbox', { name: 'Lyrics, if present', exact: true }).fill('Account A private lyrics')
  await page.getByRole('button', { name: 'Queue training with 1 take', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Retry original training request', exact: true })).toBeEnabled()
  expect(fixture.trainingSubmissions).toHaveLength(1)

  const closeCreative = page.getByRole('button', { name: 'Close creative workspace menu', exact: true }).last()
  if (await closeCreative.isVisible()) await closeCreative.click()
  await page.getByRole('button', { name: 'Open account and support', exact: true }).click()
  const drawer = page.locator('#account-support-drawer[role="dialog"]')
  await drawer.getByRole('tab', { name: 'Account', exact: true }).click()
  await drawer.getByRole('button', { name: 'Sign out', exact: true }).last().click()
  const login = drawer.getByRole('heading', { name: 'Sign in', exact: true }).locator('xpath=../..')
  await login.getByLabel('Username', { exact: true }).fill('Synthetic Owner')
  await login.getByLabel('Password', { exact: true }).fill('synthetic-owner-password')
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(drawer.getByText('Signed in.', { exact: true })).toBeVisible()
  await drawer.getByRole('button', { name: 'Close Support panel', exact: true }).last().click()
  const menu = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  if (await menu.isVisible()) {
    await menu.click()
    await page.getByRole('button', { name: 'Open Generate', exact: true }).click()
  }
  await page.getByText('Train a YuE2 artist or style LoRA', { exact: true }).click()
  await expect(page.getByRole('button', { name: 'Retry original training request', exact: true })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue('')
  await expect(page.getByRole('checkbox', { name: 'Recovered song', exact: true })).not.toBeChecked()
  await expect(page.getByRole('button', { name: 'Queue training with 0 takes', exact: true })).toBeDisabled()
  expect(fixture.trainingSubmissions).toHaveLength(1)
  expect(fixture.submissions).toHaveLength(0)
  await fixture.api.assertClean()
})
