import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi, syntheticStudioJob } from './syntheticApi'

const workspace = 'Synthetic project'
const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

async function openGenerate(page: Page) {
  const mobileMenu = page.getByRole('dialog', {
    name: 'Generate, Director, and References menu', exact: true, includeHidden: true,
  })
  if ((page.viewportSize()?.width ?? 0) < 768) {
    if (await mobileMenu.getAttribute('aria-hidden') === 'true') {
      await page.getByRole('button', { name: 'Open Generate, Director, and References menu' }).click()
    }
    await mobileMenu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    return mobileMenu
  }
  return page.locator('aside').filter({ has: page.locator('[data-generation-footer]') })
}

async function closeGenerate(page: Page) {
  const close = page.getByRole('button', { name: 'Close creative workspace menu', exact: true }).last()
  if (await close.isVisible()) await close.click()
}

async function setup(page: Page, mode: 'lost' | 'held') {
  const api = await installSyntheticApi(page)
  api.setAdaptiveScenario('ready')
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  const bodies: string[] = []
  const lookups: string[] = []
  let projectInstance = 'e'.repeat(64)
  let accepted = false
  let pending: Route | undefined
  const envelope = (body: Record<string, unknown>, reused: boolean) => {
    const job = syntheticStudioJob(String(body.generation_request_id).replaceAll('-', ''), workspace, body._queue_mode === 'held')
    return {
      generation_request_id: body.generation_request_id, workspace,
      admission_state: 'accepted', job, retained_job: true, reused,
      job_id: job.job_id, status: job.status, held: job.queue_held,
    }
  }
  await page.route('**/api/v1/generate', async route => {
    bodies.push(route.request().postData()!)
    const body = route.request().postDataJSON() as Record<string, unknown>
    if (bodies.length === 1) {
      if (mode === 'held') { pending = route; return }
      return json(route, { detail: 'Synthetic lost acknowledgement' }, 503)
    }
    return json(route, envelope(body, bodies.length === 2))
  })
  await page.route('**/api/v1/generate/submissions/**', async route => {
    expect(route.request().method()).toBe('GET')
    lookups.push(route.request().url())
    const url = new URL(route.request().url())
    const requestId = url.pathname.split('/').at(-1)
    expect(url.searchParams.get('workspace')).toBe(workspace)
    const body = JSON.parse(bodies[0]) as Record<string, unknown>
    expect(requestId).toBe(body.generation_request_id)
    return json(route, accepted ? envelope(body, true) : {
      generation_request_id: requestId, workspace, admission_state: 'unknown',
    })
  })
  await page.route('**/api/v1/llm/models?workspace=*', route => json(route, {
    models: [], guides: [], project_instance: projectInstance,
  }))
  await page.route('**/api/v1/status/*', async route => {
    const jobId = new URL(route.request().url()).pathname.split('/').at(-1)!
    if (!bodies.some(body => JSON.parse(body).generation_request_id.replaceAll('-', '') === jobId)) return route.fallback()
    return json(route, syntheticStudioJob(jobId, workspace, true))
  })
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  const menu = await openGenerate(page)
  await menu.locator('textarea').first().fill('Original Studio settings')
  return {
    api, bodies, lookups, menu, accept: () => { accepted = true },
    setProjectInstance: (value: string) => { projectInstance = value },
    rejectHeld: async () => {
      await expect.poll(() => Boolean(pending)).toBe(true)
      const body = JSON.parse(bodies[0]) as Record<string, unknown>
      const response = page.waitForResponse(r => r.url().endsWith('/api/v1/generate') && r.status() === 422)
      await json(pending!, { detail: {
        admission_state: 'rejected', generation_request_id: body.generation_request_id,
        workspace, message: 'Late obsolete rejection',
      } }, 422)
      pending = undefined
      await response
    },
  }
}

test('lost Studio acknowledgement remains visible after Generate closes the composer', async ({ page }, info) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.locator('[data-generation-footer]').getByRole('button', { name: /^(Generate|Go \(\d+\))$/ }).click()
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  await expect(page.getByText('Could not confirm whether this submission was queued.', { exact: true })).toBeVisible()
  expect(fixture.bodies).toHaveLength(1)
  await page.screenshot({ path: info.outputPath('studio-pending.png'), animations: 'disabled' })
  const menu = await openGenerate(page)
  await expect(menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true })).toBeDisabled()
  expect(fixture.bodies).toHaveLength(1)
  await fixture.api.assertClean()
})

test('Studio Retry sends the original held wire after the form changes', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await closeGenerate(page)
  const retry = page.getByRole('button', { name: 'Retry this submission', exact: true })
  await expect(retry).toBeEnabled()
  const menu = await openGenerate(page)
  await menu.locator('textarea').first().fill('Edited settings must not replace the pending request')
  await closeGenerate(page)
  await retry.click()
  await expect(retry).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(2)
  expect(fixture.bodies[1]).toBe(fixture.bodies[0])
  expect(JSON.parse(fixture.bodies[0])._queue_mode).toBe('held')
  expect(JSON.parse(fixture.bodies[0]).generation_request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  await fixture.api.assertClean()
})

test('canonical Studio acceptance releases an outstanding POST and fences its late rejection', async ({ page }) => {
  const fixture = await setup(page, 'held')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await expect.poll(() => fixture.bodies.length).toBe(1)
  await closeGenerate(page)
  fixture.accept()
  await page.getByRole('button', { name: 'Check submission', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Check submission', exact: true })).toHaveCount(0)
  const menu = await openGenerate(page)
  const hold = menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true })
  await expect(hold).toBeEnabled()
  await hold.click()
  await expect.poll(() => fixture.bodies.length).toBe(2)
  expect(JSON.parse(fixture.bodies[1]).generation_request_id).not.toBe(JSON.parse(fixture.bodies[0]).generation_request_id)
  await fixture.rejectHeld()
  await expect(page.getByText('Late obsolete rejection', { exact: true })).toHaveCount(0)
  await closeGenerate(page)
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(2)
  await fixture.api.assertClean()
})

test('changing account removes the uncertain Studio request without resubmission', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await closeGenerate(page)
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  fixture.api.setAccountScenario('owner')
  await page.evaluate(async () => {
    const path = '/src/stores/useStore.ts'
    const { useStore } = await import(path)
    await useStore.getState().loadAccountContext()
  })
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(1)
  await fixture.api.assertClean()
})

test('reload restores an uncertain Studio identity and recovers acceptance with GET only', async ({ page }, info) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await closeGenerate(page)
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  await expect.poll(() => fixture.lookups.length).toBe(1)
  const stored = await page.evaluate(() => sessionStorage.getItem('maestro:studio-submissions-v1'))
  expect(stored).not.toBeNull()
  expect(stored).not.toContain('Original Studio settings')
  expect(stored).not.toContain('prompt')
  expect(stored).not.toContain('body')
  expect(JSON.parse(stored!).submissions[0].requestId).toBe(JSON.parse(fixture.bodies[0]).generation_request_id)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Check submission', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeDisabled()
  await expect(page.getByText('This page reloaded. Check submission reads its existing status. Retry is unavailable because the original settings were not saved.', { exact: true })).toBeVisible()
  expect(fixture.lookups).toHaveLength(1)
  expect(fixture.bodies).toHaveLength(1)
  await page.screenshot({ path: info.outputPath('studio-reload-check-only.png'), animations: 'disabled' })
  fixture.accept()
  await page.getByRole('button', { name: 'Check submission', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Studio submission', exact: true })).toHaveCount(0)
  expect(fixture.lookups).toHaveLength(2)
  expect(fixture.lookups[1]).toBe(fixture.lookups[0])
  expect(fixture.bodies).toHaveLength(1)
  expect(await page.evaluate(() => sessionStorage.getItem('maestro:studio-submissions-v1'))).toBeNull()
  await fixture.api.assertClean()
})

test('unknown or unavailable acceptance after reload preserves the same request without retry', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await closeGenerate(page)
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  await page.reload()
  const check = page.getByRole('button', { name: 'Check submission', exact: true })
  await expect(check).toBeEnabled()
  await check.click()
  await expect(check).toBeEnabled()
  await page.route('**/api/v1/generate/submissions/**', route => json(route, { detail: 'Unavailable' }, 503))
  await check.click()
  await expect(check).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeDisabled()
  expect(fixture.bodies).toHaveLength(1)
  expect(await page.evaluate(() => sessionStorage.getItem('maestro:studio-submissions-v1'))).not.toBeNull()
  await fixture.api.assertClean()
})

test('reload cannot restore a foreign account or recreated project submission', async ({ page }) => {
  const fixture = await setup(page, 'lost')
  await fixture.menu.getByTitle('Hold current Studio settings in the queue without starting generation', { exact: true }).click()
  await closeGenerate(page)
  await expect(page.getByRole('button', { name: 'Retry this submission', exact: true })).toBeEnabled()
  const stored = await page.evaluate(() => sessionStorage.getItem('maestro:studio-submissions-v1'))
  await expect.poll(() => fixture.lookups.length).toBe(1)
  fixture.api.setAccountScenario('owner')
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Studio submission', exact: true })).toHaveCount(0)
  expect(fixture.lookups).toHaveLength(1)
  fixture.api.setAccountScenario('remote-user')
  fixture.setProjectInstance('f'.repeat(64))
  await page.evaluate(value => sessionStorage.setItem('maestro:studio-submissions-v1', value!), stored)
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('maestro:studio-submissions-v1'))).toBeNull()
  await expect(page.getByRole('region', { name: 'Studio submission', exact: true })).toHaveCount(0)
  expect(fixture.lookups).toHaveLength(1)
  expect(fixture.bodies).toHaveLength(1)
  await fixture.api.assertClean()
})
