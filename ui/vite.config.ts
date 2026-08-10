import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const continuumVersion = readVersion('../CONTINUUM_VERSION')
const maestroBaseVersion = readVersion('../VERSION')

function readVersion(path: string): string {
  const version = readFileSync(new URL(path, import.meta.url), 'utf8').trim()
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    throw new Error(`Invalid version in ${path}: ${version}`)
  }
  return version
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __CONTINUUM_VERSION__: JSON.stringify(continuumVersion),
    __MAESTRO_BASE_VERSION__: JSON.stringify(maestroBaseVersion),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://127.0.0.1:7860',
      '/classic': 'http://127.0.0.1:7860',
    },
  },
  // Strip console.* and debugger statements from the production bundle.
  // Dev mode (npm run dev) is unaffected — esbuild `drop` only runs at
  // build time.
  esbuild: {
    drop: ['console', 'debugger'],
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
