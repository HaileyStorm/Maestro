export interface RecoveryFailureCandidate {
  createdAt?: number
  manualRetryCount?: number
}

/** Select the newest original source failure, using backend creation time when
 *  both candidates have it and stable newest-first list order otherwise. */
export function selectRecoverySourceIndex(
  candidates: RecoveryFailureCandidate[],
): number {
  const sourceIndexes = candidates
    .map((candidate, index) => ({ candidate, index }))
    .filter(item => item.candidate.manualRetryCount == null)
  const eligible = sourceIndexes.length > 0
    ? sourceIndexes
    : candidates.map((candidate, index) => ({ candidate, index }))
  if (eligible.length === 0) return -1
  return eligible.reduce((newest, item) => {
    const itemTime = item.candidate.createdAt
    const newestTime = newest.candidate.createdAt
    if (
      Number.isFinite(itemTime)
      && Number.isFinite(newestTime)
      && itemTime !== newestTime
    ) {
      return Number(itemTime) > Number(newestTime) ? item : newest
    }
    return item.index < newest.index ? item : newest
  }).index
}

export function recoveryGalleryNavigationVerified({
  expectedWorkspace,
  switchSucceeded,
  outputsLoaded,
  activeWorkspace,
  browsingUploads,
}: {
  expectedWorkspace: string
  switchSucceeded: boolean
  outputsLoaded: boolean
  activeWorkspace: string
  browsingUploads: boolean
}): boolean {
  return switchSucceeded
    && outputsLoaded
    && activeWorkspace === expectedWorkspace
    && !browsingUploads
}
