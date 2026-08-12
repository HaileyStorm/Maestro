import type { FullConfig } from '@playwright/test'

export default async function verifySyntheticServer(config: FullConfig) {
  const runToken = process.env.MAESTRO_E2E_RUN_TOKEN
  const runPort = process.env.MAESTRO_E2E_PORT
  const baseURL = config.projects[0]?.use.baseURL
  const portPattern = /^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  if (!runToken || !uuidPattern.test(runToken) || !runPort || !portPattern.test(runPort) || typeof baseURL !== 'string') {
    throw new Error('Synthetic server identity is not configured.')
  }
  const parsedBase = new URL(baseURL)
  if (
    parsedBase.protocol !== 'http:'
    || parsedBase.hostname !== '127.0.0.1'
    || parsedBase.port !== runPort
    || parsedBase.username !== ''
    || parsedBase.password !== ''
  ) {
    throw new Error('Synthetic server base URL is not the authenticated loopback listener.')
  }
  const leakedEnvironment = Object.keys(process.env).filter(name => (
    name !== 'MAESTRO_E2E_RUN_TOKEN'
    && /(?:TOKEN|SECRET|PASSWORD|COOKIE|API_KEY|AUTHORIZATION)/i.test(name)
  ))
  if (process.env.MAESTRO_E2E_PARENT_SENTINEL || process.env.NODE_OPTIONS || leakedEnvironment.length > 0) {
    throw new Error(`Synthetic child environment contains forbidden parent state: ${leakedEnvironment.join(', ') || 'sentinel/options'}`)
  }

  const response = await fetch(`${baseURL}/__maestro_e2e_health/${runToken}`, {
    headers: { 'X-Maestro-E2E-Run-Token': runToken },
    redirect: 'error',
    signal: AbortSignal.timeout(5_000),
  })
  const body = await response.text()
  const expectedBody = JSON.stringify({ run_token: runToken })
  if (
    response.status !== 200
    || response.headers.get('x-maestro-e2e-run-token') !== runToken
    || body !== expectedBody
  ) {
    throw new Error('Synthetic server identity check failed; refusing to run against an untrusted listener.')
  }
}
