import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api/client'

const RECOVERY_REFRESH_EVENT = 'maestro:h3-delivery-recovery-changed'

export function announceH3DeliveryRecoveryChange(sourceJobId: string) {
  window.dispatchEvent(new CustomEvent(RECOVERY_REFRESH_EVENT, {
    detail: { sourceJobId },
  }))
}

export function useH3DeliveryRecovery(
  sourceJobId: string,
  workspace: string,
  enabled: boolean,
) {
  const [recovery, setRecovery] = useState<api.H3DeliveryRecoveryState | null>(null)
  const [recoveryIdentity, setRecoveryIdentity] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestVersion = useRef(0)
  const identity = enabled ? `${sourceJobId}\u0000${workspace}` : ''

  const refresh = useCallback(async () => {
    const version = ++requestVersion.current
    if (!enabled || !sourceJobId || !workspace) {
      setRecovery(null)
      setRecoveryIdentity('')
      setError(null)
      setLoading(false)
      return null
    }
    setLoading(true)
    try {
      const next = await api.fetchH3DeliveryRecovery(sourceJobId, workspace)
      if (requestVersion.current === version) {
        setRecovery(next)
        setRecoveryIdentity(identity)
        setError(null)
      }
      return next
    } catch (reason) {
      if (requestVersion.current === version) {
        setRecovery(null)
        setRecoveryIdentity(identity)
        setError(reason instanceof Error ? reason.message : 'Delivery recovery status is unavailable')
      }
      return null
    } finally {
      if (requestVersion.current === version) setLoading(false)
    }
  }, [enabled, identity, sourceJobId, workspace])

  useEffect(() => {
    void refresh()
    return () => { requestVersion.current += 1 }
  }, [refresh])

  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => { void refresh() }, 2500)
    return () => window.clearInterval(timer)
  }, [enabled, refresh])

  useEffect(() => {
    if (!enabled) return
    const handleChange = (event: Event) => {
      const detail = (event as CustomEvent<{ sourceJobId?: string }>).detail
      if (detail?.sourceJobId === sourceJobId) void refresh()
    }
    window.addEventListener(RECOVERY_REFRESH_EVENT, handleChange)
    return () => window.removeEventListener(RECOVERY_REFRESH_EVENT, handleChange)
  }, [enabled, refresh, sourceJobId])

  return {
    recovery: recoveryIdentity === identity ? recovery : null,
    loading,
    error,
    refresh,
  }
}
