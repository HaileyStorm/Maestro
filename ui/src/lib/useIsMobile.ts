import { useCallback, useSyncExternalStore } from 'react'

export function useIsMobile(breakpoint = 768): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const mql = window.matchMedia(`(max-width: ${breakpoint - 1}px)`)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [breakpoint])

  const getSnapshot = useCallback(
    () => window.matchMedia(`(max-width: ${breakpoint - 1}px)`).matches,
    [breakpoint],
  )

  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
