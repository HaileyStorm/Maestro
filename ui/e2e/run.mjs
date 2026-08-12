import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdirSync, realpathSync, statSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const PORT_PATTERN = /^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/
const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(uiRoot, '..')
const externalRoot = process.platform === 'linux' ? '/var/tmp' : tmpdir()
const action = process.argv[2]
const runId = randomUUID()

const browsersPath = resolve(process.env.PLAYWRIGHT_BROWSERS_PATH || join(externalRoot, 'maestro-playwright-browsers'))
const outputRoot = resolve(process.env.MAESTRO_PLAYWRIGHT_OUTPUT_DIR || join(externalRoot, 'maestro-playwright-results'))
const npmCache = resolve(process.env.npm_config_cache || join(externalRoot, 'maestro-npm-cache'))
const viteCacheRoot = resolve(process.env.MAESTRO_VITE_CACHE_DIR || join(externalRoot, 'maestro-vite-cache'))
const outputPath = resolve(outputRoot, `run-${runId}`)
const viteCache = resolve(viteCacheRoot, `run-${runId}`)

function createAndValidateExternalDirectory(label, candidate, exclusive = false) {
  mkdirSync(candidate, { recursive: !exclusive, mode: 0o700 })
  const canonicalRepository = realpathSync.native(repositoryRoot)
  const canonicalCandidate = realpathSync.native(candidate)
  const child = relative(canonicalRepository, canonicalCandidate)
  if (child === '' || (child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child))) {
    throw new Error(`Playwright ${label} path must be outside the repository: ${canonicalCandidate}`)
  }
  const sameFilesystem = process.platform === 'win32'
    ? parse(canonicalRepository).root.toLowerCase() === parse(canonicalCandidate).root.toLowerCase()
    : statSync(canonicalRepository).dev === statSync(canonicalCandidate).dev
  if (sameFilesystem) {
    throw new Error(`Playwright ${label} path must be on a different mounted filesystem or volume from the repository: ${canonicalCandidate}`)
  }
  return canonicalCandidate
}

function validateTestArguments(args) {
  const allowedValueOptions = new Set(['--grep', '--grep-invert', '--project'])
  const allowedBooleanOptions = new Set(['--list'])
  const validated = []
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index]
    if (argument === '--' || argument === '-' || argument.startsWith('@')) {
      throw new Error(`Unsafe Playwright argument rejected: ${argument}`)
    }
    if (!argument.startsWith('-')) {
      if (argument.includes('\0')) throw new Error('NUL is not allowed in Playwright test filters.')
      validated.push(argument)
      continue
    }
    const equals = argument.indexOf('=')
    const option = equals === -1 ? argument : argument.slice(0, equals)
    if (allowedBooleanOptions.has(option) && equals === -1) {
      validated.push(argument)
      continue
    }
    if (!allowedValueOptions.has(option)) {
      throw new Error(`Unsafe or unsupported Playwright option rejected: ${option}`)
    }
    const value = equals === -1 ? args[++index] : argument.slice(equals + 1)
    if (!value || value.startsWith('-')) throw new Error(`Playwright option ${option} requires a value.`)
    if (option === '--project' && !['desktop-firefox', 'android-like-chromium', 'ios-like-webkit'].includes(value)) {
      throw new Error(`Unknown Playwright project rejected: ${value}`)
    }
    validated.push(option, value)
  }
  return validated
}

async function allocateRunPort() {
  return await new Promise((resolvePort, reject) => {
    const server = createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string' || !PORT_PATTERN.test(String(address.port))) {
        server.close(() => reject(new Error('Could not allocate a canonical synthetic Playwright port')))
        return
      }
      server.close(error => error ? reject(error) : resolvePort(String(address.port)))
    })
  })
}

function buildChildEnvironment(runPort, runToken) {
  const allowedKeys = [
    'PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'HOME', 'USERPROFILE',
    'APPDATA', 'LOCALAPPDATA', 'TMPDIR', 'TMP', 'TEMP', 'XDG_RUNTIME_DIR',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'CI',
  ]
  const env = {}
  for (const key of allowedKeys) {
    if (process.env[key] !== undefined) env[key] = process.env[key]
  }
  return {
    ...env,
    PLAYWRIGHT_BROWSERS_PATH: canonicalBrowsersPath,
    MAESTRO_PLAYWRIGHT_OUTPUT_DIR: canonicalOutputPath,
    MAESTRO_VITE_CACHE_DIR: canonicalViteCache,
    ...(runPort === null ? {} : { MAESTRO_E2E_PORT: runPort }),
    ...(runToken === null ? {} : { MAESTRO_E2E_RUN_TOKEN: runToken }),
    npm_config_cache: canonicalNpmCache,
  }
}

if (action !== 'install' && action !== 'test') {
  throw new Error('Usage: node e2e/run.mjs <install|test> [safe Playwright selection arguments]')
}
const requestedArgs = action === 'test' ? validateTestArguments(process.argv.slice(3)) : []
const canonicalBrowsersPath = createAndValidateExternalDirectory('browser', browsersPath)
createAndValidateExternalDirectory('output root', outputRoot)
const canonicalNpmCache = createAndValidateExternalDirectory('npm cache', npmCache)
createAndValidateExternalDirectory('Vite cache root', viteCacheRoot)
const canonicalOutputPath = createAndValidateExternalDirectory('run output', outputPath, true)
const canonicalViteCache = createAndValidateExternalDirectory('run Vite cache', viteCache, true)

const runPort = action === 'test' ? await allocateRunPort() : null
const runToken = action === 'test' ? randomUUID() : null
if ((runPort !== null && !PORT_PATTERN.test(runPort)) || (runToken !== null && !UUID_PATTERN.test(runToken))) {
  throw new Error('Synthetic runner generated an invalid run identity.')
}

process.env.MAESTRO_E2E_PARENT_SENTINEL = runId
const args = action === 'install'
  ? ['install', 'chromium', 'firefox', 'webkit']
  : ['test', ...requestedArgs]
const cli = resolve(uiRoot, 'node_modules', '@playwright', 'test', 'cli.js')
const child = spawn(process.execPath, [cli, ...args], {
  cwd: uiRoot,
  env: buildChildEnvironment(runPort, runToken),
  stdio: 'inherit',
})

child.on('error', error => {
  console.error(error)
  process.exitCode = 1
})
child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`Playwright exited after signal ${signal}`)
    process.exitCode = 1
  } else {
    process.exitCode = code ?? 1
  }
})
