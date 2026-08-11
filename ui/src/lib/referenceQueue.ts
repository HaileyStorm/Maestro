export async function confirmReconnectedJob(
  jobId: string,
  reconnect: () => Promise<void>,
  getJobs: () => readonly { id: string }[],
): Promise<void> {
  await reconnect()
  if (!jobId || !getJobs().some(job => job.id === jobId)) {
    throw new Error('Queued Reference Studio job could not be confirmed after reconnect.')
  }
}
