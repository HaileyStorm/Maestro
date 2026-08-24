import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  ProtectedReadApiError,
  QueueApiError,
  accessRecoveryStatus,
  estimateH3Performance,
  fetchAccessContext,
  fetchActiveJobs,
  fetchQueueState,
  fetchSampleCampaignQueue,
  fetchWorkspaces,
  protectedProjectReadsReady,
  protectedReadFailureIsTransient,
  queueAccessRecoveryStatus,
} from '../src/api/client.ts'

const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')
const [main, boundary, app, mainContent, fixture, client] = await Promise.all([
  source('../src/main.tsx'),
  source('../src/RootRecoveryBoundary.tsx'),
  source('../src/App.tsx'),
  source('../src/components/MainContent/MainContent.tsx'),
  source('../e2e/syntheticApi.ts'),
  source('../src/api/client.ts'),
])

test('the React root has an accessible content-free recovery boundary', () => {
  assert.match(main, /<RootRecoveryBoundary>[\s\S]*<App \/>[\s\S]*<\/RootRecoveryBoundary>/)
  assert.match(boundary, /static getDerivedStateFromError\(\)/)
  assert.match(boundary, /role="alert"/)
  assert.match(boundary, /Maestro needs to recover this screen/)
  assert.match(boundary, />\s*Try again\s*</)
  assert.match(boundary, />\s*Reload Maestro\s*</)
  assert.match(boundary, /do not log exception text or component stacks/)
  assert.doesNotMatch(boundary, /console\.(?:error|warn|log)\s*\(/)
})

test('boot recovery uses status-only account and project paths and clears protected presentation', () => {
  assert.match(app, /api\.accessRecoveryKind\(error\) \?\? 'error'/)
  assert.match(app, /awaitingProjectSelection[\s\S]*setBootstrapState\('ready'\)/)
  assert.match(app, /workspaces: \[\],[\s\S]*activeWorkspace: '',[\s\S]*jobs: \[\],[\s\S]*sampleCampaignPairs: \[\]/)
  assert.match(app, /Your session is no longer valid\. Sign in again/)
  assert.match(app, /Sign in to open your projects and creative tools\./)
  assert.match(app, /accountGateKind === 'expired'/)
  assert.match(app, /Project access changed\. Try again/)
  assert.match(app, /<AccountSupportDrawer[\s\S]*required=\{accountRecovery\}[\s\S]*onAuthenticated=\{accountRecovery \? finishAccountRecovery : undefined\}/)
  assert.doesNotMatch(app, /setBootstrapError\(error instanceof Error/)
})

test('optional remote LoRA update denial never invalidates a valid account session', () => {
  const updateCheck = client.match(/export async function checkLoraUpdates[\s\S]*?\n}/)?.[0] || ''
  assert.match(updateCheck, /LoRA update check is unavailable/)
  assert.doesNotMatch(updateCheck, /protectedReadApiError|requestAccessRecovery/)
})

test('queue status recovery is exact while transient failures retain the last good snapshot', () => {
  assert.equal(queueAccessRecoveryStatus(new QueueApiError(401)), 401)
  assert.equal(queueAccessRecoveryStatus(new QueueApiError(403)), 403)
  assert.equal(queueAccessRecoveryStatus(new QueueApiError(423)), 423)
  assert.equal(queueAccessRecoveryStatus(new QueueApiError(503)), null)
  assert.equal(queueAccessRecoveryStatus(new TypeError('offline')), null)
  assert.equal(protectedReadFailureIsTransient(new QueueApiError(503)), true)
  assert.equal(protectedReadFailureIsTransient(new QueueApiError(423)), false)
  assert.equal(protectedReadFailureIsTransient(new TypeError('offline')), true)

  assert.match(mainContent, /const queuePollingReady = api\.protectedProjectReadsReady\(/)
  assert.match(mainContent, /activeProject \? \[activeProject\] : \[\]/)
  assert.match(mainContent, /\{ enabled: queuePollingReady \}/)
  assert.match(mainContent, /const recoveryStatus = api\.queueAccessRecoveryStatus\(reason\)/)
  assert.match(mainContent, /api\.requestAccessRecovery\(recoveryStatus\)/)
  assert.match(mainContent, /kind: 'failure',[\s\S]*error: reason instanceof Error \? reason\.message/)
})

test('remote protected reads require an authenticated authorized active project', () => {
  const account = {
    enabled: true,
    authenticated: true,
    account: null,
    capabilities: [],
    reauthenticated: false,
    passkey_authentication_available: false,
    activation_state: 'ready',
  }
  const context = {
    remote: true,
    account_project_access_active: true,
    accounts: account,
  }
  const project = {
    name: 'project-a',
    unlocked: false,
    project_permissions: ['project.open', 'project.read'],
  }

  assert.equal(protectedProjectReadsReady(context, account, [project], 'project-a'), true)
  assert.equal(protectedProjectReadsReady(context, { ...account, authenticated: false }, [project], 'project-a'), false)
  assert.equal(protectedProjectReadsReady(context, account, [{ ...project, project_permissions: ['project.open'] }], 'project-a'), false)
  assert.equal(protectedProjectReadsReady(context, account, [project], 'missing'), false)
  assert.equal(protectedProjectReadsReady({ ...context, remote: false }, null, [], ''), true)
})

test('protected API reads preserve status without reflecting backend details', async t => {
  const originalFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = originalFetch })
  const privateDetail = 'private backend detail must not render'

  for (const [surface, invoke, status] of [
    ['access', () => fetchAccessContext(), 401],
    ['workspaces', () => fetchWorkspaces(), 403],
    ['queue', () => fetchQueueState(), 423],
    ['jobs', () => fetchActiveJobs(), 423],
    ['sample_queue', () => fetchSampleCampaignQueue(), 503],
    ['h3_estimate', () => estimateH3Performance({}), 403],
  ]) {
    globalThis.fetch = async () => new Response(
      JSON.stringify({ detail: privateDetail }),
      { status, headers: { 'Content-Type': 'application/json' } },
    )
    await assert.rejects(invoke, error => {
      assert.ok(error instanceof ProtectedReadApiError)
      assert.equal(error.surface, surface)
      assert.equal(error.status, status)
      assert.doesNotMatch(error.message, /private backend detail/)
      return true
    })
  }

  assert.equal(accessRecoveryStatus(new ProtectedReadApiError('h3_estimate', 403)), 403)
  assert.equal(accessRecoveryStatus(new ProtectedReadApiError('workspaces', 423)), 423)
  assert.equal(accessRecoveryStatus(new ProtectedReadApiError('jobs', 503)), null)
})

test('synthetic fixture can independently reproduce stale account, project, and queue statuses', () => {
  assert.match(fixture, /setBootFailures\(failures:/)
  assert.match(fixture, /accountFailureStatus: 403 \| null/)
  assert.match(fixture, /projectFailureStatus: 403 \| null/)
  assert.match(fixture, /queueAccessFailureStatus: 423 \| null/)
  assert.match(fixture, /jobsFailureStatus: 403 \| null/)
  assert.match(fixture, /estimateFailureStatus: 403 \| null/)
  assert.match(fixture, /loraFailureStatus: 403 \| null/)
  assert.match(fixture, /requestCount\(pathname:/)
})
