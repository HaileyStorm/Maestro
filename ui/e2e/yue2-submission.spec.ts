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

async function setup(page: Page, mode: 'lost' | 'held') {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  const submissions: Submission[] = []
  let library: ReturnType<typeof take>[] = []
  let pending: Route | undefined
  await page.route('**/api/v1/yue2/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/status')) return json(route, { available: true, sampleRate: 44100, queue: 'local', loras: [] })
    if (path.endsWith('/library')) return json(route, { tracks: library })
    if (path.endsWith('/training')) return json(route, { jobs: [] })
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
  return { api, submissions, generate,
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
