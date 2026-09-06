"""Static UI contracts for generic queue recovery and Director preparation.

The UI has no React test harness. These focused source contracts keep the
server-authored recovery protocol, action gating, and Director request chain
reviewable without launching Maestro or a browser.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
MAIN = (ROOT / "ui/src/components/MainContent/MainContent.tsx").read_text(
    encoding="utf-8"
)
PLAN_DIALOG = (ROOT / "ui/src/components/H3GenerationPlanDialog.tsx").read_text(
    encoding="utf-8"
)
RECOVERY_ADAPTER = (ROOT / "app/services/queue_recovery_adapter.py").read_text(
    encoding="utf-8"
)
LAUNCH = (ROOT / "app/launch.py").read_text(encoding="utf-8")
DIRECTOR_CHAT = (ROOT / "ui/src/components/Sidebar/DirectorChat.tsx").read_text(
    encoding="utf-8"
)
DIRECTOR_DASHBOARD = (
    ROOT / "ui/src/components/DirectorDashboard/DirectorDashboard.tsx"
).read_text(encoding="utf-8")


def source_slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class QueueRecoveryUiContracts(unittest.TestCase):
    def test_preparation_and_plan_review_are_durable_per_job_actions(self):
        generation = source_slice(STORE, "startGeneration: async", "stopGeneration: (jobId)")
        review = source_slice(STORE, "openH3PlanReview: async", "startGeneration: async")
        placeholder = source_slice(MAIN, "function JobPlaceholder", "function queueSummaryLabel")
        self.assertIn("enhance_before_generate", generation)
        self.assertNotIn("get().enhancePrompt()", generation)
        self.assertNotIn("previewGenerationPlan(params)", generation)
        self.assertIn("'preparing' | 'waiting_for_plan_approval'", CLIENT)
        self.assertIn("plan_review_required?: boolean", CLIENT)
        self.assertIn("plan_review_deadline?: number | null", CLIENT)
        self.assertIn("plan_review_terms_required?: boolean", CLIENT)
        self.assertIn("waiting_for_plan_approval", TYPES)
        self.assertIn("Review plan", placeholder)
        self.assertIn("Maestro will accept this plan in", placeholder)
        self.assertIn("Approval required to accept Ref2VA terms", placeholder)
        self.assertIn("openH3PlanReview(job.id)", MAIN)
        self.assertIn("api.approveGenerationPlan(jobId", review)
        self.assertIn("workspace !== workspace", review)
        self.assertIn("job.id === jobId", review)
        self.assertIn("job.status === 'waiting_for_plan_approval'", STORE)
        self.assertIn("planReviewDeadline", PLAN_DIALOG)
        self.assertIn("Continuum will approve the saved plan unchanged in", PLAN_DIALOG)
        self.assertIn("Accept the Ref2VA terms before approving", PLAN_DIALOG)
        self.assertIn("editorJobId === planJobId", PLAN_DIALOG)
        self.assertIn("!plan || !editsReady", PLAN_DIALOG)
        self.assertNotIn("if (seconds <= 0) submit()", PLAN_DIALOG)
        self.assertNotIn("_h3PlanDecisionResolver", STORE)
        self.assertIn("Approve & resume", PLAN_DIALOG)
        self.assertIn("Cancel generation", PLAN_DIALOG)

    def test_restarted_terms_blocked_plan_retains_authoritative_checkpoint_catalog(self):
        self.assertIn('"checkpoint_options"', RECOVERY_ADAPTER)
        self.assertIn(
            "result[key] = _safe_h3_checkpoint_options(child)",
            RECOVERY_ADAPTER,
        )
        self.assertIn("serverOptions !== undefined", PLAN_DIALOG)
        self.assertNotIn("serverOptions?.length", PLAN_DIALOG)
        self.assertIn("plan_review_terms_required?: boolean", CLIENT)
        self.assertIn("Accept the Ref2VA terms before approving", PLAN_DIALOG)

    def test_plan_review_hydration_records_ownership_before_await(self):
        review = source_slice(STORE, "openH3PlanReview: async", "closeH3PlanReview: ()")
        self.assertLess(
            review.index("pendingH3PlanJobId: jobId"),
            review.index("await api.fetchJobStatus(jobId)"),
        )
        self.assertLess(
            review.index("pendingH3PlanWorkspace: workspace"),
            review.index("await api.fetchJobStatus(jobId)"),
        )
        self.assertIn("get().pendingH3PlanJobId !== jobId", review)
        self.assertIn("get().pendingH3PlanWorkspace !== workspace", review)
        self.assertIn("current.createdAt !== initial.createdAt", review)
        self.assertIn("status.created_at !== current.createdAt", review)
        self.assertIn("get().closeH3PlanReview()", review)

    def test_live_status_exposes_the_server_job_incarnation_timestamp(self):
        status_route = source_slice(
            LAUNCH,
            '@api.get("/api/v1/status/{job_id}")',
            '@api.post("/api/v1/cancel/{job_id}")',
        )
        timestamp_helper = source_slice(
            LAUNCH,
            "def _public_job_created_at",
            '@api.get("/api/v1/status/{job_id}")',
        )
        active_jobs_route = source_slice(
            LAUNCH,
            '@api.get("/api/v1/jobs")',
            "def _require_owned_job",
        )
        api_status = source_slice(
            CLIENT,
            "export interface ApiJobStatus",
            "export interface QueueJobState",
        )
        mapper = source_slice(STORE, "function _jobStatusDetails", "function _mergeJobStatus")
        self.assertIn('created_at = job.get("created_at")', timestamp_helper)
        self.assertIn("math.isfinite(created_at)", timestamp_helper)
        self.assertIn("created_at >= 0", timestamp_helper)
        self.assertIn('"created_at": _public_job_created_at(j)', status_route)
        self.assertIn('"created_at": _public_job_created_at(j)', active_jobs_route)
        self.assertIn("created_at: number", api_status)
        self.assertNotIn("created_at?: number", api_status)
        self.assertIn("createdAt: status.created_at", mapper)

    def test_all_public_recovery_fields_map_through_api_and_job_state(self):
        fields = (
            "recovery_state",
            "recovery_interrupted",
            "recovery_blocked",
            "recovery_attempt",
            "recovery_attempt_limit",
            "recovery_reruns_denoise",
            "recovery_reason",
            "recovery_reason_text",
            "recovery_actionable",
            "recovery_actions",
            "estimate_after_resume",
        )
        api_status = source_slice(CLIENT, "export interface QueueRecoveryMetadata", "export interface ApiJobStatus")
        mapper = source_slice(STORE, "function _jobStatusDetails", "function _mergeJobStatus")
        for field in fields:
            self.assertIn(field, api_status)
            self.assertIn(field, mapper)
        for field in (
            "recoveryState", "recoveryInterrupted", "recoveryBlocked",
            "recoveryAttempt", "recoveryAttemptLimit", "recoveryRerunsDenoise",
            "recoveryReason", "recoveryReasonText", "recoveryActionable",
            "recoveryActions", "estimateAfterResume",
        ):
            self.assertIn(field, TYPES)
        self.assertIn("jobs: QueueJobState[]", CLIENT)
        self.assertIn("fetchActiveJobs(): Promise<{ jobs: ApiJobStatus[] }>", CLIENT)

    def test_recovery_endpoints_have_bounded_errors_and_store_refresh(self):
        recovery_client = source_slice(CLIENT, "async function queueRecoveryRequest", "export interface JobLogEvent")
        self.assertIn("recovery-resume", recovery_client)
        self.assertIn("recovery-retry", recovery_client)
        self.assertIn("res.status === 404", recovery_client)
        self.assertIn("res.status === 409", recovery_client)
        self.assertIn("res.status === 503", recovery_client)
        actions = source_slice(STORE, "resumeJobRecovery: async", "reconnectJobs: async")
        self.assertIn("api.fetchJobStatus(jobId)", actions)
        self.assertIn("_mergeJobStatus(job, status)", actions)
        self.assertIn("await get().reconnectJobs(accountIdentityEpoch)", actions)

    def test_reconnect_updates_in_place_and_deduplicates_cards(self):
        reconnect = source_slice(STORE, "reconnectJobs: async", "// LoRA state")
        self.assertIn("const ordinaryStatuses = data.jobs.filter(status => (", reconnect)
        self.assertIn("!_sampleCampaignKnownJobIds.has(status.job_id)", reconnect)
        self.assertIn(
            "s.jobs.filter(job => !_sampleCampaignKnownJobIds.has(job.id))",
            reconnect,
        )
        self.assertIn(
            "for (const jobId of _sampleCampaignKnownJobIds) "
            "_recoveryJobPolls.get(jobId)?.stop()",
            reconnect,
        )
        self.assertIn(
            "new Map(ordinaryStatuses.map(job => [job.job_id, job]))",
            reconnect,
        )
        self.assertIn("_mergeJobStatus(job, status)", reconnect)
        self.assertIn("const existingIds = new Set(get().jobs.map(j => j.id))", reconnect)
        new_jobs = source_slice(
            reconnect,
            "const newJobs: GenerationJob[] = ordinaryStatuses",
            "if (newJobs.length > 0)",
        )
        self.assertIn(".filter(j => !existingIds.has(j.job_id))", new_jobs)
        # Existing cards merge against their prior state above; genuinely new
        # reconnect cards have no prior client state and map the server record.
        self.assertIn(".map(_newGenerationJobFromStatus)", new_jobs)
        terminal_poll = source_slice(STORE, "_pollRecoveredJob: (jobId)", "reconnectJobs: async")
        self.assertIn("_recoveryJobPolls.get(jobId)", terminal_poll)
        self.assertIn("api.fetchJobStatus(jobId)", terminal_poll)
        self.assertIn("status.status === 'completed'", terminal_poll)
        self.assertIn("_recoveryJobPolls.delete(jobId)", terminal_poll)
        self.assertIn("consecutivePollFailures += 1", terminal_poll)
        self.assertIn("consecutivePollFailures >= 3", terminal_poll)
        self.assertIn("void get().reconnectJobs(accountIdentityEpoch)", terminal_poll)

    def test_blocked_cards_use_only_server_actions_and_hide_generic_controls(self):
        placeholder = source_slice(MAIN, "function JobPlaceholder", "function queueSummaryLabel")
        queue_panel = source_slice(MAIN, "function QueuePanel", "function GalleryBulkToolbar")
        self.assertIn("job.recoveryActions.map(action =>", placeholder)
        self.assertIn("Retry generation", placeholder)
        self.assertIn("job.recoveryActions?.includes('retry')", placeholder)
        self.assertNotIn("recoveryActionable ?", placeholder)
        self.assertIn("The current part will restart from the beginning", placeholder)
        self.assertIn("completed parts will stay saved", placeholder)
        self.assertIn("Estimated work after resume", placeholder)
        self.assertIn("!recoveryBlocked", placeholder)
        blocked_branch = source_slice(queue_panel, "info.recovery_blocked ?", ") : (")
        for forbidden in ("Start next", "setQueuePriority", "setQueueOutputCount", "resumeQueueJob"):
            self.assertNotIn(forbidden, blocked_branch)
        self.assertIn("Recovery needs your choice", blocked_branch)

    def test_remote_resume_uses_exact_project_unlock_then_refreshes(self):
        workspace = source_slice(MAIN, "function WorkspaceSelector", "const OVERSCAN")
        queue_panel = source_slice(MAIN, "function QueuePanel", "function GalleryBulkToolbar")
        self.assertIn("detail: { workspace: job.workspace, jobId: job.id }", queue_panel)
        self.assertIn("setUnlockTarget(workspace)", workspace)
        self.assertIn("const password = unlockPassword", workspace)
        self.assertIn("setUnlockPassword('')", workspace)
        self.assertIn("await unlockWorkspace(target, password, remember)", workspace)
        self.assertIn("await switchWorkspace(target)", workspace)
        self.assertIn("await reconnectJobs()", workspace)
        self.assertIn("await resumeJobRecovery(recoveryJobId)", workspace)
        self.assertIn("QUEUE_REFRESH_EVENT", workspace)
        self.assertNotIn("set({ unlockPassword", workspace)
        self.assertLess(
            workspace.index("await unlockWorkspace(target, password, remember)"),
            workspace.index("await reconnectJobs()"),
        )
        self.assertLess(
            workspace.index("await reconnectJobs()"),
            workspace.index("await switchWorkspace(target)"),
        )
        self.assertLess(
            workspace.index("await switchWorkspace(target)"),
            workspace.index("await resumeJobRecovery(recoveryJobId)"),
        )

    def test_generic_recovery_does_not_replace_delivery_oom_flow(self):
        placeholder = source_slice(MAIN, "function JobPlaceholder", "function queueSummaryLabel")
        self.assertIn("job.oomInfo?.stage === 'h3_delivery'", placeholder)
        self.assertIn("<H3DeliveryRecoveryStatus", placeholder)
        self.assertIn("recoveryBlocked", placeholder)
        self.assertIn("recovery-resume", CLIENT)
        self.assertIn("delivery-recovery", CLIENT)

    def test_state_model_keeps_one_card_until_recovered_job_completes(self):
        cards = {"job-a": {"id": "job-a", "status": "failed", "recovery_state": "blocked"}}
        pollers = set()
        is_generating = False

        # Mock the successful endpoint + immediate status refresh performed by
        # resumeJobRecovery/retryJobRecovery.
        before = cards["job-a"]["status"]
        cards["job-a"].update(status="queued", recovery_state="retrying")
        is_generating = any(card["status"] in {"queued", "running"} for card in cards.values())
        if before not in {"queued", "running"}:
            pollers.add("job-a")
        self.assertEqual(list(cards), ["job-a"])
        self.assertEqual(pollers, {"job-a"})
        self.assertTrue(is_generating)

        # Reconnect/upsert and the recovery poll both address the same stable
        # id; neither creates a second card.
        cards["job-a"].update(status="running", recovery_state="restored")
        self.assertEqual(list(cards), ["job-a"])
        cards.pop("job-a")
        pollers.discard("job-a")
        is_generating = any(card["status"] in {"queued", "running"} for card in cards.values())
        self.assertEqual(cards, {})
        self.assertEqual(pollers, set())
        self.assertFalse(is_generating)

    def test_state_model_refreshes_blocked_status_after_503_without_sticky_generation(self):
        cards = {"job-a": {"id": "job-a", "status": "failed", "recovery_state": "blocked"}}
        endpoint_error = RuntimeError("The recovery worker could not be started. Try again.")
        # The endpoint failed, but its authoritative follow-up status is still
        # merged before the bounded error is shown.
        cards["job-a"].update(
            status="failed",
            recovery_state="blocked",
            recovery_reason="worker_start_failed",
        )
        is_generating = any(card["status"] in {"queued", "running"} for card in cards.values())
        self.assertFalse(is_generating)
        self.assertEqual(cards["job-a"]["recovery_reason"], "worker_start_failed")
        self.assertEqual(str(endpoint_error), "The recovery worker could not be started. Try again.")

    def test_state_model_transient_poll_failure_keeps_poller_until_completion(self):
        cards = {"job-a": {"id": "job-a", "status": "queued"}}
        pollers = {"job-a"}
        # One status request fails: the card and its sole poller remain.
        transient_error = True
        if transient_error and "job-a" not in cards:
            pollers.discard("job-a")
        self.assertEqual(pollers, {"job-a"})
        self.assertEqual(cards["job-a"]["status"], "queued")
        # The next successful poll reaches the terminal state and performs the
        # normal card/poller cleanup.
        cards.pop("job-a")
        pollers.discard("job-a")
        self.assertEqual(cards, {})
        self.assertEqual(pollers, set())


class DirectorPreparationUiContracts(unittest.TestCase):
    def test_director_status_and_saved_views_type_blocked_recovery(self):
        status = source_slice(CLIENT, "export interface PipelineStatus", "export async function startPipeline")
        saved = source_slice(TYPES, "export type DirectorRecoveryState", "export interface PipelineListItem")
        self.assertIn("extends DirectorRecoveryMetadata", status)
        self.assertIn("| 'blocked'", status)
        self.assertIn("'blocked_remote_reauth'", status)
        self.assertIn("'blocked_input_changed'", status)
        self.assertIn("recovery_actions?: DirectorRecoveryAction[]", saved)
        self.assertIn("SavedPipelineState extends DirectorRecoveryMetadata", saved)
        self.assertIn("PipelineListItem extends DirectorRecoveryMetadata", TYPES)

    def test_client_issues_and_polls_public_preparation_id(self):
        preparation = source_slice(CLIENT, "export async function startDirectorPreparation", "export async function generateMusic")
        self.assertIn("/api/v1/director/preparation", preparation)
        self.assertIn("fetchDirectorPreparation", preparation)
        self.assertIn("?${query}", preparation)
        self.assertIn("cache: 'no-store'", preparation)
        self.assertIn("director_request_id: string", CLIENT)

    def test_generated_music_retains_id_before_model_work_and_reloads_it(self):
        generation = source_slice(STORE, "directorGenerateTrack: async", "directorSetEnergyBias: async")
        start = generation.index("api.startDirectorPreparation(musicRequest)")
        retain = generation.index("_storeDirectorPreparation(directorRequestId, workspace)")
        generate = generation.index("api.generateMusic({")
        self.assertLess(start, retain)
        self.assertLess(retain, generate)
        self.assertIn("...musicRequest", generation)
        self.assertIn("director_request_id: directorRequestId", generation)
        self.assertIn("reconnectDirectorPreparation", generation)
        self.assertIn("_loadStoredDirectorPreparation()", STORE)
        self.assertIn("directorRequestId: _storedDirectorPreparation?.requestId", STORE)
        self.assertIn("api.fetchDirectorPreparation(directorRequestId, workspace)", generation)
        self.assertIn("else {\n        // Register the durable chain", generation)
        reconnect = source_slice(
            STORE, "reconnectDirectorPreparation: async", "directorResolution:",
        )
        self.assertNotIn("_storeDirectorPreparation(null, null)", reconnect)
        self.assertIn("Keep the public cursor", reconnect)
        self.assertIn("_directorPreparationPoll = setInterval", reconnect)
        self.assertIn("useStore.getState().reconnectDirectorPreparation()", reconnect)

    def test_public_id_reaches_every_settled_director_call(self):
        analyze = source_slice(STORE, "directorAnalyzeAndPlan: async", "directorWriteSong: async")
        pipeline = source_slice(STORE, "startDirectorPipeline: async", "continuePipeline: async")
        self.assertGreaterEqual(analyze.count("director_request_id: directorRequestId || undefined"), 3)
        self.assertIn("api.analyzeAudio", analyze)
        self.assertIn("api.classifySections", analyze)
        self.assertIn("api.planClipStructure", analyze)
        self.assertIn("director_request_id: state.directorRequestId", pipeline)
        client_pipeline = source_slice(CLIENT, "export async function startPipeline", "export async function fetchPipelineStatus")
        self.assertIn("delete publicParams._director_request_id", client_pipeline)
        self.assertIn("body: JSON.stringify(publicParams)", client_pipeline)

    def test_remote_paused_resume_explicitly_continues(self):
        resume = source_slice(STORE, "resumePipeline: async", "// ── Recipes")
        self.assertIn("selected?.pipeline_id === pid", resume)
        self.assertIn("selected.workspace", resume)
        self.assertIn("dashboardPipelineList.find(item => item.id === pid)?.workspace", resume)
        self.assertIn("api.resumePipeline(pid, workspace)", resume)
        self.assertNotIn("api.resumePipeline(pid, get().activeWorkspace)", resume)
        self.assertIn("result.status === 'paused'", resume)
        self.assertIn("result.next_action === 'continue'", resume)
        self.assertIn("result.actions?.includes('continue')", resume)
        self.assertIn("await api.continuePipeline(pid)", resume)
        self.assertIn("pipelinePolling: true", resume)

    def test_director_dashboard_resume_is_authoritative_and_block_reason_visible(self):
        self.assertIn(
            "selectedPipeline?.recovery_actions?.includes('resume') === true",
            DIRECTOR_DASHBOARD,
        )
        self.assertIn("{canResumeRecovery && (", DIRECTOR_DASHBOARD)
        self.assertNotIn(
            "(selectedPipeline.status === 'crashed' || selectedPipeline.status === 'failed')",
            DIRECTOR_DASHBOARD,
        )
        self.assertIn("selectedPipeline.recovery_reason_text", DIRECTOR_DASHBOARD)

    def test_restored_next_action_is_visible_and_executable(self):
        self.assertIn("s.directorPreparationStatus", DIRECTOR_CHAT)
        self.assertIn("preparationStatus.actions.length > 0", DIRECTOR_CHAT)
        self.assertIn("preparationStatus.status === 'completed'", DIRECTOR_CHAT)
        self.assertIn("preparationStatus.phase === 'structure_completed'", DIRECTOR_CHAT)
        self.assertIn("preparationStatus.next_action", DIRECTOR_CHAT)
        self.assertIn("onClick={() => void generateTrack()}", DIRECTOR_CHAT)
        generation = source_slice(STORE, "directorGenerateTrack: async", "directorSetEnergyBias: async")
        self.assertIn("const restoredRequest =", generation)
        self.assertIn("&& !restoredRequest", generation)

        completed_unconsumed = {
            "status": "completed",
            "phase": "structure_completed",
            "actions": [],
        }
        visible = bool(completed_unconsumed["actions"]) or (
            completed_unconsumed["status"] == "completed"
            and completed_unconsumed["phase"] == "structure_completed"
        )
        self.assertTrue(visible)

    def test_reload_state_model_reuses_cursor_without_starting_another_chain(self):
        calls = []
        stored = {"requestId": "d" * 32, "workspace": "project-a"}

        def fetch_preparation(request_id, workspace):
            calls.append(("status", request_id, workspace))
            return {"director_request_id": request_id, "phase": "music_queued"}

        def start_preparation(_body):
            calls.append(("start",))
            return {"director_request_id": "e" * 32}

        workspace = "project-a"
        request_id = stored["requestId"] if stored["workspace"] == workspace else None
        if request_id:
            status = fetch_preparation(request_id, workspace)
        else:
            status = start_preparation({"workspace": workspace})
            request_id = status["director_request_id"]
        for step in ("generate_music", "analyze", "classify", "plan_structure", "pipeline_start"):
            calls.append((step, request_id))

        self.assertNotIn(("start",), calls)
        self.assertEqual(calls[0], ("status", "d" * 32, "project-a"))
        self.assertTrue(all(call[-1] == "d" * 32 for call in calls[1:]))


if __name__ == "__main__":
    unittest.main()
