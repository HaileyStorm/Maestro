import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

const workspace = 'Synthetic project'
const modelId = 'synthetic-local-chat'
const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

async function setup(page: Page, immediate: boolean) {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  const bodies: Record<string, unknown>[] = []
  const lookups: string[] = []
  let response: 'foreign' | 'missing' | 'completed' = immediate ? 'completed' : 'foreign'
  let uploadReconciliations = 0
  await page.route('**/api/v1/llm/models*', route => json(route, {
    models: [{ id: modelId, label: 'Synthetic local model', size_hint: 'Test fixture',
      provider: 'local', installed: true, current: true, loaded: true, vision_capable: false }],
    guides: [], project_instance: 'synthetic-project-instance',
  }))
  await page.route('**/api/v1/llm/status', route => json(route, {
    loaded: true, model_id: modelId, device: 'cpu', provider: 'local',
  }))
  await page.route('**/api/v1/llm/prepare', route => json(route, {
    operation_id: 'synthetic-ready', status: 'ready', phase: 'ready', retryable: false,
    model_id: modelId, workspace, purpose: 'chat',
  }))
  await page.route('**/api/v1/llm/chat', async route => {
    expect(route.request().method()).toBe('POST')
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    return route.fulfill({ status: 202, contentType: 'application/json', body: '{"request_id":' })
  })
  await page.route('**/api/v1/llm/chat/*', async route => {
    const url = new URL(route.request().url())
    expect(route.request().method()).toBe('GET')
    expect(url.searchParams.get('workspace')).toBe(workspace)
    const id = decodeURIComponent(url.pathname.split('/').at(-1)!)
    lookups.push(id)
    expect(id).toBe(bodies[0].request_id)
    if (response === 'missing') return json(route, { detail: 'Chat request not found' }, 404)
    return json(route, {
      request_id: response === 'foreign' ? '00000000-0000-4000-8000-000000000001' : id.replaceAll('-', ''),
      status: 'completed', phase: 'completed', retryable: false,
      result: { text: response === 'foreign' ? 'FOREIGN RESULT MUST STAY HIDDEN' : 'Recovered original response',
        model_id: modelId, guide_ids: [] },
    })
  })
  await page.route('**/api/v1/llm/chat-upload-request/*', route => {
    uploadReconciliations += 1
    return json(route, { state: 'not_found', deleted: 0 })
  })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const shell = page.locator('[data-chat-shell]')
  await expect(shell).toBeVisible()
  await expect(shell.getByRole('combobox', { name: 'Language model for Chat', exact: true })).toHaveValue(modelId)
  const composer = shell.locator('[data-chat-composer]')
  const draft = composer.locator('textarea')
  await expect(draft).toBeEnabled()
  return { api, bodies, lookups, shell, composer, draft,
    setResponse: (value: typeof response) => { response = value },
    reconciliations: () => uploadReconciliations }
}

test('a truncated accepted Chat response retrieves one original turn', async ({ page }) => {
  const fixture = await setup(page, true)
  await fixture.draft.fill('Keep this accepted turn')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  await expect(fixture.shell.getByText('Keep this accepted turn', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.lookups).toHaveLength(1)
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toHaveCount(0)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})

test('foreign status and missing Resume keep Chat locked to its original request', async ({ page }, info) => {
  const fixture = await setup(page, false)
  await fixture.draft.fill('Keep this uncertain turn')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  const resume = fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })
  await expect(resume).toBeEnabled()
  await expect(fixture.shell.getByText('FOREIGN RESULT MUST STAY HIDDEN', { exact: true })).toHaveCount(0)
  fixture.setResponse('missing')
  await resume.click()
  await expect.poll(() => fixture.lookups.length).toBe(2)
  await expect(resume).toBeEnabled()
  await expect(fixture.composer.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await expect(fixture.draft).toBeDisabled()
  await expect(fixture.shell.getByText('Keep this uncertain turn', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.reconciliations()).toBe(0)
  await page.screenshot({ path: info.outputPath('chat-resume-pending.png'), animations: 'disabled' })
  fixture.setResponse('completed')
  await resume.click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(new Set(fixture.lookups).size).toBe(1)
  await fixture.api.assertClean()
})

test('browser reload checks a pending Chat turn without a second submission', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  await fixture.draft.fill('Keep this turn across reload')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toBeEnabled()
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toBeEnabled()
  await expect(fixture.composer.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  expect(fixture.reconciliations()).toBe(0)
  fixture.setResponse('completed')
  await fixture.shell.getByRole('button', { name: 'Resume wait', exact: true }).click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(new Set(fixture.lookups).size).toBe(1)
  await fixture.api.assertClean()
})
