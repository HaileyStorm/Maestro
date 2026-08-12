import { mergeConfig, type Plugin } from 'vite'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import appConfig from '../vite.config'

const cacheDir = process.env.MAESTRO_VITE_CACHE_DIR
const runPort = process.env.MAESTRO_E2E_PORT
const runToken = process.env.MAESTRO_E2E_RUN_TOKEN

const portPattern = /^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
if (!cacheDir || !runPort || !portPattern.test(runPort) || !runToken || !uuidPattern.test(runToken)) {
  throw new Error('Synthetic browser tests require an external Vite cache, port, and run token')
}

const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const appOrigin = `http://127.0.0.1:${runPort}`
const healthPath = `/__maestro_e2e_health/${runToken}`
const viteCacheFsPrefix = `/@fs/${cacheDir.replaceAll('\\', '/').replace(/^\/+/, '')}/deps/`

function normalizedPathname(pathname: string): string {
  let normalized = pathname.replaceAll('\\', '/')
  for (let index = 0; index < 3; index += 1) {
    try {
      const decoded = decodeURIComponent(normalized).replaceAll('\\', '/')
      if (decoded === normalized) break
      normalized = decoded
    } catch {
      break
    }
  }
  return normalized
}

const syntheticNetworkBoundary: Plugin = {
  name: 'maestro-synthetic-network-boundary',
  configureServer(server) {
    server.httpServer?.prependListener('upgrade', (_request, socket) => {
      socket.end([
        'HTTP/1.1 403 Forbidden',
        'Connection: close',
        'Content-Length: 0',
        '',
        '',
      ].join('\r\n'))
    })
    server.middlewares.use((request, response, next) => {
      const target = new URL(request.url || '/', appOrigin)
      const pathname = normalizedPathname(target.pathname)
      const suppliedToken = request.headers['x-maestro-e2e-run-token']
      if (target.origin === appOrigin && target.pathname === healthPath) {
        response.statusCode = 200
        response.setHeader('Cache-Control', 'no-store')
        response.setHeader('Content-Type', 'application/json')
        response.setHeader('X-Maestro-E2E-Run-Token', runToken)
        response.end(JSON.stringify({ run_token: runToken }))
        return
      }
      if (
        target.origin !== appOrigin
        || suppliedToken !== runToken
        || pathname.startsWith('/__maestro_e2e_health/')
        || pathname === '/api'
        || pathname.startsWith('/api/')
        || pathname === '/classic'
        || pathname.startsWith('/classic/')
      ) {
        response.statusCode = 403
        response.setHeader('Content-Type', 'application/json')
        response.end(JSON.stringify({ detail: 'Synthetic browser tests do not proxy backend requests.' }))
        return
      }
      if (pathname.toLowerCase().startsWith('/@fs/') && (
        (request.method !== 'GET' && request.method !== 'HEAD')
        || !pathname.startsWith(viteCacheFsPrefix)
      )) {
        response.statusCode = 403
        response.setHeader('Content-Type', 'application/json')
        response.end(JSON.stringify({ detail: 'Synthetic browser tests restrict Vite filesystem requests to the isolated dependency cache.' }))
        return
      }
      next()
    })
  },
}

const config = mergeConfig(appConfig, {
  cacheDir,
  plugins: [syntheticNetworkBoundary],
  optimizeDeps: {
    noDiscovery: true,
    include: [
      'dompurify',
      'lucide-react',
      'react',
      'react-dom',
      'react-dom/client',
      'react/jsx-dev-runtime',
      'react/jsx-runtime',
      'zustand',
    ],
  },
})

export default {
  ...config,
  server: {
    ...config.server,
    hmr: false,
    proxy: {},
    watch: null,
    fs: {
      strict: true,
      allow: [uiRoot, cacheDir],
    },
  },
}
