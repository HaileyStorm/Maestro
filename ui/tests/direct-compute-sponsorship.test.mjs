import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  allowedManualSupportKinds,
  allowedManualSupportSources,
  visibleSupportProviders,
} from '../src/components/AccountSupport/supportPresentation.ts'

function projection(recoveryState, providerState = 'available') {
  return {
    development_cost_recovery: recoveryState === null
      ? null
      : { target_minor: 100_000, currency: 'USD', state: recoveryState },
    provider_catalog: {
      providers: [{
        provider_id: 'direct_compute_sponsorship',
        display_name: 'Vast.ai compute sponsorship',
        funding_modes: ['one_time'],
        description: 'Optional direct-compute sponsorship through Vast.ai.',
        enabled: true,
        configured: true,
        state: providerState,
        support_url: 'https://cloud.vast.ai/',
        destinations: [],
        membership_contract: false,
        support_evidence: null,
      }],
    },
  }
}

test('Vast.ai compute sponsorship stays public but non-actionable until exact recovery', () => {
  for (const recovery of ['locked', null]) {
    const providers = visibleSupportProviders(projection(recovery))
    assert.equal(providers.length, 1)
    assert.equal(providers[0].provider_id, 'direct_compute_sponsorship')
    assert.equal(providers[0].display_name, 'Vast.ai compute sponsorship')
    assert.equal(providers[0].support_url, null)
  }

  const recovered = visibleSupportProviders(projection('recovered'))
  assert.equal(recovered.length, 1)
  assert.equal(recovered[0].support_url, 'https://cloud.vast.ai/')
})

test('owner direct-compute records remain a one-time audit lifecycle before and after recovery', () => {
  assert.deepEqual(allowedManualSupportSources(false), [
    'buy_me_a_coffee',
    'patreon',
    'direct_compute_sponsorship',
  ])
  assert.deepEqual(allowedManualSupportSources(true), [
    'buy_me_a_coffee',
    'patreon',
    'direct_compute_sponsorship',
  ])
  assert.deepEqual(allowedManualSupportKinds('direct_compute_sponsorship', true), [
    'one_time_contribution',
    'refund',
    'chargeback',
  ])
  assert.deepEqual(allowedManualSupportKinds('direct_compute_sponsorship', false), [
    'one_time_contribution',
    'refund',
    'chargeback',
  ])
})

test('Support panel explains sponsorship without payment, credit, or scheduling promises', async () => {
  const source = await readFile(
    new URL('../src/components/AccountSupport/SupportPanel.tsx', import.meta.url),
    'utf8',
  )
  assert.match(source, /Vast\.ai compute sponsorship/)
  assert.match(source, /Locked until net other support reaches \$1,000/)
  assert.match(source, /processed no payment, granted no credits, and guarantees no compute or service/)
  assert.match(source, /Zero-credit work remains schedulable/)
  assert.match(source, /local or authenticated-LAN use stays available/)
  assert.match(source, /visibleSupportProviders\(effectiveSupportProjection\)/)
})
