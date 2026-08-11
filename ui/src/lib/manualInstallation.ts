export interface ManualInstallationManifest {
  filename: string
  size_bytes: number
  destination_hint: string
}

export function formatManualInstallationBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return 'Unknown size'
  const units = ['bytes', 'KiB', 'MiB', 'GiB']
  let value = size
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const friendly = unit === 0 ? `${Math.round(value)}` : value.toFixed(2)
  return `${size.toLocaleString()} bytes (${friendly} ${units[unit]})`
}

export function manualInstallationDestination(manifest: ManualInstallationManifest): string {
  return `${manifest.destination_hint.replace(/\/+$/, '')}/${manifest.filename}`
}
