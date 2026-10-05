// Complete successful admission fixture. Tests exercise the real client validator.
export function acceptedStudioSubmission(params, jobId, overrides = {}) {
  const held = params._queue_mode === 'held'
  const workspace = params.workspace || 'default'
  return {
    generation_request_id: params.generation_request_id,
    workspace,
    admission_state: 'accepted',
    retained_job: true,
    reused: false,
    job: {
      job_id: jobId, created_at: 1, status: 'queued',
      progress: 0, step: 0, total_steps: 1, phase: 'queued',
      message: held ? 'Ready - waiting for Start Queue' : 'Queued...',
      output_files: [], error: null, prompt_preview: '', active_window_prompt: '',
      model_type: params.model_type, generation_mode: 'video', workspace,
      window_current: 0, window_total: 0, window_step: 0, window_total_steps: 0,
      window_progress: 0, overall_progress: 0, queue_priority: 0, queue_held: held,
      hold_after_output: false, queue_position: 1,
      queue_wait_reason: held ? 'held' : 'waiting_for_turn',
      queue_reorder_reason: 'queue_order', queue_residency_bypass_count: 0,
      queue_residency_bypassed_waiters: 0, requested_outputs: 1, produced_outputs: 0,
      queue: { paused: false, pause_after_current: false },
      ...overrides,
    },
  }
}
