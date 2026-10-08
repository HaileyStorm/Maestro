export async function confirmReconnectedJob(
  jobId: string,
  reconnect: () => Promise<void>,
  getJobs: () => readonly { id: string }[],
): Promise<void> {
  await reconnect()
  if (!jobId || !getJobs().some(job => job.id === jobId)) {
    throw new Error('Queued Reference job could not be confirmed after reconnect.')
  }
}

export async function confirmReconnectedJobWithin(
  jobId: string,
  reconnect: () => Promise<void>,
  getJobs: () => readonly { id: string }[],
  timeoutMs = 2_500,
): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      confirmReconnectedJob(jobId, reconnect, getJobs).then(() => true, () => false),
      new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), timeoutMs) }),
    ])
  } finally {
    clearTimeout(timer)
  }
}
