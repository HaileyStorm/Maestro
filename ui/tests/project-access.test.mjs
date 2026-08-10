import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  lockAllWorkspaces,
  lockWorkspace,
  unlockWorkspace,
} from '../src/api/client.ts'

const mainContentUrl = new URL('../src/components/MainContent/MainContent.tsx', import.meta.url)
const storeUrl = new URL('../src/stores/useStore.ts', import.meta.url)

test('workspace access API sends explicit remember policy and server-side revocations', async t => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, init = {}) => {
    requests.push({
      url: String(url),
      method: init.method,
      body: init.body == null ? null : JSON.parse(String(init.body)),
    })
    if (String(url).endsWith('/unlock')) {
      return new Response(JSON.stringify({
        unlocked: true,
        remember_policy: 'device',
        unlock_expires_at: 2_000_000_000,
        unlock_idle_expires_at: 1_999_000_000,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({
      unlocked: false,
      locked_count: 1,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  t.after(() => { globalThis.fetch = originalFetch })

  const unlock = await unlockWorkspace('alpha/beta', 'transient password', 'device')
  const one = await lockWorkspace('alpha/beta')
  const all = await lockAllWorkspaces()

  assert.equal(unlock.remember_policy, 'device')
  assert.equal(unlock.unlock_idle_expires_at, 1_999_000_000)
  assert.deepEqual(one, { unlocked: false, locked_count: 1 })
  assert.deepEqual(all, { unlocked: false, locked_count: 1 })
  assert.deepEqual(requests, [
    {
      url: '/api/v1/workspaces/alpha%2Fbeta/unlock',
      method: 'POST',
      body: { password: 'transient password', remember: 'device' },
    },
    {
      url: '/api/v1/workspaces/alpha%2Fbeta/lock',
      method: 'POST',
      body: null,
    },
    {
      url: '/api/v1/workspaces/lock-all',
      method: 'POST',
      body: null,
    },
  ])
})

test('selector keeps row selection separate from independent project locks', async () => {
  const [main, store] = await Promise.all([
    readFile(mainContentUrl, 'utf8'),
    readFile(storeUrl, 'utf8'),
  ])
  const selector = main.slice(
    main.indexOf('function WorkspaceSelector()'),
    main.indexOf('function stripTimeSuffix'),
  )

  assert.match(selector, /workspaces\.map\(ws =>/)
  assert.match(selector, /ws\.password_protected && ws\.unlocked/)
  assert.match(selector, /<LockOpen size=\{12\}/)
  assert.match(selector, /aria-label=\{ws\.unlocked \? `Lock \$\{ws\.name\}` : `Unlock \$\{ws\.name\}`\}/)
  assert.match(selector, /event\.stopPropagation\(\)/)
  assert.match(selector, /await lockWorkspace\(name\)/)
  assert.match(selector, /await lockAllWorkspaces\(\)/)
  assert.match(selector, /if \(unlockingTarget \|\| lockingAll \|\| lockingTarget\) return/)
  assert.match(selector, /disabled=\{unlockingTarget !== null \|\| lockingAll \|\| lockingTarget !== null\}/)
  assert.match(selector, /Remember this device/)
  assert.match(selector, /setUnlockRemember\(event\.target\.checked \? 'device' : 'session'\)/)
  assert.match(selector, /beginUnlock\(ws\.name, null, true\)/)
  assert.match(selector, /if \(selectAfter\) \{[\s\S]*await switchWorkspace\(target\)/)
  assert.match(selector, /else \{[\s\S]*beginUnlock\(ws\.name\)/)
  assert.match(selector, /const nextExpiry = Math\.min\(\.\.\.expiries\)/)
  assert.match(selector, /const refreshed = await loadWorkspaces\(\)/)
  assert.match(selector, /window\.setTimeout\(\(\) => void refreshAtExpiry\(\), 5000\)/)
  assert.match(selector, /nextExpiry <= nowSeconds[\s\S]*\? 5000/)
  assert.match(selector, /await switchWorkspace\(ws\.name\)/)
  assert.doesNotMatch(selector, /localStorage|sessionStorage/)

  assert.match(store, /const result = await api\.lockWorkspace\(name\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /const result = await api\.lockAllWorkspaces\(\)[\s\S]*await get\(\)\.loadWorkspaces\(\)/)
  assert.match(store, /outputs: \[\],[\s\S]*selectedOutputKeys: \[\]/)
  assert.match(store, /pendingH3Plan: null,[\s\S]*pendingH3PlanWorkspace: null/)
  assert.match(store, /workspaces: state\.workspaces\.map\(workspace => workspace\.name === name[\s\S]*unlocked: false/)
  assert.match(store, /if \(!await get\(\)\.loadWorkspaces\(\)\) \{[\s\S]*current access state could not be refreshed/)
  assert.match(store, /const previousAccessRevoked = Boolean\(previousActive\)[\s\S]*workspace\.unlocked !== false/)
  assert.match(store, /const requestSequence = \+\+_workspaceLoadSequence[\s\S]*requestSequence !== _workspaceLoadSequence/)
  assert.match(store, /previousAccessRevoked[\s\S]*state\.jobs\.filter\(job => job\.workspace && job\.workspace !== previousActive\)/)
  assert.match(store, /job\.workspace \? job\.workspace !== name : !lockedActiveWorkspace/)
  assert.match(store, /job\.workspace[\s\S]*!lockedWorkspaces\.has\(job\.workspace\)[\s\S]*!lockedActiveWorkspace/)
})
