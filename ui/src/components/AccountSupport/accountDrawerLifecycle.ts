export interface AccountDrawerLifecycle {
  opened: () => void
  closed: () => void
  operationLease: () => () => boolean
}

/** Prevent a response started in an old drawer lifetime from restoring secrets. */
export function createAccountDrawerLifecycle(): AccountDrawerLifecycle {
  let epoch = 0
  let open = false
  return {
    opened: () => {
      open = true
      epoch += 1
    },
    closed: () => {
      open = false
      epoch += 1
    },
    operationLease: () => {
      const operationEpoch = epoch
      return () => open && epoch === operationEpoch
    },
  }
}
