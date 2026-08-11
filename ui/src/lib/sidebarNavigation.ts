export type SidebarMode = 'studio' | 'director' | 'reference'
export type ReferenceReturnMode = Exclude<SidebarMode, 'reference'>

export interface SidebarNavigationState {
  sidebarMode: SidebarMode
  referenceReturnMode: ReferenceReturnMode
}

export interface SidebarNavigationTransition extends SidebarNavigationState {
  /** Returning through Reference must not reinitialize an existing Director draft. */
  preserveDirectorState: boolean
}

export function resolveSidebarNavigation(
  current: SidebarNavigationState,
  target: SidebarMode,
): SidebarNavigationTransition {
  if (target === 'reference') {
    if (current.sidebarMode === 'reference') {
      return { ...current, preserveDirectorState: false }
    }
    return {
      sidebarMode: 'reference',
      referenceReturnMode: current.sidebarMode === 'director' ? 'director' : 'studio',
      preserveDirectorState: false,
    }
  }
  return {
    sidebarMode: target,
    referenceReturnMode: current.referenceReturnMode,
    preserveDirectorState: target === 'director'
      && current.sidebarMode === 'reference'
      && current.referenceReturnMode === 'director',
  }
}
