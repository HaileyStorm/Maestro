"""Deterministic source and request-budget contracts for UI polling."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLLING = (ROOT / "ui/src/lib/useVisibilityPolling.ts").read_text(encoding="utf-8")
APP = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")
MAIN = (
    ROOT / "ui/src/components/MainContent/MainContent.tsx"
).read_text(encoding="utf-8")
HARDWARE = (
    ROOT / "ui/src/components/Sidebar/HardwareStatusBar.tsx"
).read_text(encoding="utf-8")
DOWNLOADS = (
    ROOT / "ui/src/components/DownloadStatusBanner.tsx"
).read_text(encoding="utf-8")
REFERENCES = (
    ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx"
).read_text(encoding="utf-8")
SETTINGS = (
    ROOT / "ui/src/components/SettingsDrawer/SystemSettingsPanel.tsx"
).read_text(encoding="utf-8")
INPUTS = (
    ROOT / "ui/src/components/Sidebar/InputsPanel.tsx"
).read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
UI_SOURCES = {
    path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
    for path in (ROOT / "ui/src").rglob("*")
    if path.suffix in {".ts", ".tsx"}
}

MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000


def _interval(name: str) -> int:
    match = re.search(rf"\b{name}: ([\d_]+)", POLLING)
    if match is None:
        raise AssertionError(f"missing polling interval {name}")
    return int(match.group(1).replace("_", ""))


def _requests_per_day(interval_ms: int) -> int:
    quotient, remainder = divmod(MILLISECONDS_PER_DAY, interval_ms)
    if remainder:
        raise AssertionError(f"{interval_ms}ms does not divide one day exactly")
    return quotient


def _store_interval(name: str) -> int:
    match = re.search(rf"\b{name} = ([\d_]+)", STORE)
    if match is None:
        raise AssertionError(f"missing store interval {name}")
    return int(match.group(1).replace("_", ""))


class UiPollingBudgetTests(unittest.TestCase):
    def test_proven_remote_idle_mounts_are_below_ten_thousand_per_day(self):
        # Remote sessions have machine_controls=false, so hardware/download/
        # settings pollers do not mount. The stable baseline is queue + LLM.
        self.assertIn("{machineControls && <DownloadStatusBanner />}", APP)
        remote_idle = sum(
            _requests_per_day(_interval(name))
            for name in ("queueIdleVisible", "llmIdleVisible")
        )
        self.assertEqual(remote_idle, 2_880)
        self.assertLess(remote_idle, 10_000)
        self.assertLess(remote_idle, 25_000)

    def test_local_idle_baseline_remains_below_thirty_thousand_per_day(self):
        local_idle = sum(
            _requests_per_day(_interval(name))
            for name in (
                "hardwareVisible",
                "queueIdleVisible",
                "downloadsIdleVisible",
                "llmIdleVisible",
            )
        )
        self.assertEqual(local_idle, 23_040)
        self.assertLess(local_idle, 30_000)

    def test_continuously_active_remote_safety_budget_stays_below_sixty_thousand(self):
        status_poll = _store_interval("ACTIVE_JOB_STATUS_POLL_MS")
        output_cap = _store_interval("ACTIVE_OUTPUT_REFRESH_MIN_MS")
        remote_active = sum(
            _requests_per_day(interval)
            for interval in (
                status_poll,
                _interval("queueActiveVisible"),
                _interval("llmIdleVisible"),
                output_cap,
            )
        )
        self.assertEqual(remote_active, 56_160)
        self.assertLess(remote_active, 60_000)
        self.assertIn("ACTIVE_OUTPUT_REFRESH_MIN_MS = 15_000", STORE)
        self.assertIn("ACTIVE_OUTPUT_REFRESH_SAFETY_MS = 30_000", STORE)

    def test_one_running_with_ten_queued_cards_stays_below_sixty_thousand(self):
        running_and_shared = sum(
            _requests_per_day(interval)
            for interval in (
                _store_interval("ACTIVE_JOB_STATUS_POLL_MS"),
                _store_interval("ACTIVE_OUTPUT_REFRESH_MIN_MS"),
                _interval("queueActiveVisible"),
                _interval("llmIdleVisible"),
            )
        )
        queued_safety = 10 * _requests_per_day(
            _store_interval("QUEUED_JOB_STATUS_SAFETY_MS")
        )
        self.assertEqual(running_and_shared + queued_safety, 59_040)
        self.assertLess(running_and_shared + queued_safety, 60_000)


class UiPollingStateTests(unittest.TestCase):
    def test_shared_scheduler_pauses_hidden_and_refreshes_on_visibility(self):
        self.assertIn("if (cancelled || document.hidden) return", POLLING)
        self.assertIn("document.addEventListener('visibilitychange'", POLLING)
        self.assertIn("controller?.abort()", POLLING)
        self.assertIn("void run()", POLLING)
        self.assertIn("running = true", POLLING)
        self.assertIn("running = false", POLLING)
        self.assertIn("pendingImmediate = true", POLLING)
        self.assertIn("if (pendingImmediate && !cancelled && !document.hidden)", POLLING)
        self.assertIn("schedule()", POLLING)

        for source in (APP, MAIN, HARDWARE, DOWNLOADS, REFERENCES, SETTINGS):
            self.assertIn("useVisibilityPolling", source)

    def test_idle_active_transitions_select_the_intended_cadence(self):
        self.assertIn("queueActivity", MAIN)
        self.assertIn("POLL_INTERVAL_MS.queueActiveVisible", MAIN)
        self.assertIn("POLL_INTERVAL_MS.queueIdleVisible", MAIN)
        self.assertIn("if (queuePollingReady && !document.hidden) void refreshQueue()", MAIN)
        self.assertIn("QUEUE_REFRESH_EVENT", MAIN)
        self.assertIn("queueRefreshIsStale(sequence, queuePollSequence.current", MAIN)
        self.assertIn("role=\"status\"", MAIN)
        self.assertIn("aria-live=\"polite\"", MAIN)
        self.assertIn("Showing the last successful update", MAIN)
        self.assertIn("retrying automatically", MAIN)
        self.assertIn("machineControls && queue &&", MAIN)
        self.assertIn("const queueDisplayJobs = queueTabDisplayJobs(queueTabSnapshot, jobs)", MAIN)
        self.assertIn("The queue is unavailable. Maestro is retrying automatically.", MAIN)
        self.assertLess(
            MAIN.index("reconcileQueueState(next)"),
            MAIN.index("jobs: useStore.getState().jobs"),
        )
        self.assertNotIn("setQueueTabState(null)", MAIN)
        self.assertIn("downloads.length > 0", DOWNLOADS)
        self.assertIn("POLL_INTERVAL_MS.downloadsActiveVisible", DOWNLOADS)
        self.assertIn("POLL_INTERVAL_MS.downloadsIdleVisible", DOWNLOADS)
        self.assertIn("boundedBackoffDelay(", DOWNLOADS)
        self.assertIn("downloadsRef.current", DOWNLOADS)
        self.assertNotIn("setDownloads([])", DOWNLOADS)
        self.assertIn("DOWNLOAD_REFRESH_EVENT", SETTINGS)
        self.assertIn("DOWNLOAD_REFRESH_EVENT", INPUTS)
        self.assertIn("llmTransitionActive", APP)
        self.assertIn("POLL_INTERVAL_MS.llmActiveVisible", APP)
        self.assertIn("POLL_INTERVAL_MS.llmIdleVisible", APP)

    def test_access_context_backoff_is_visibility_aware_and_capped(self):
        delays = [min(30_000, 2_500 * (2**attempt)) for attempt in range(8)]
        self.assertEqual(delays, [2_500, 5_000, 10_000, 20_000, 30_000, 30_000, 30_000, 30_000])
        self.assertIn("boundedBackoffDelay(accessPollAttempt)", MAIN)
        self.assertIn("{ enabled: accessContextPending, immediate: false }", MAIN)
        self.assertIn("Math.min(maximumMs, initialMs * (2 ** exponent))", POLLING)
        self.assertIn("accessContextMaximum: 30_000", POLLING)

    def test_gallery_refresh_is_state_driven_with_cleanup_and_safety(self):
        tracker = STORE[
            STORE.index("function _coalescedGalleryRefreshDue("):
            STORE.index("function _waitForDownloadPoll(")
        ]
        for source_state in (
            "status.produced_outputs",
            "status.output_files",
            "outputChanged",
            "phasePublished",
            "pendingDelta",
            "coalescedDeltaDue",
            "ACTIVE_OUTPUT_REFRESH_MIN_MS",
            "ACTIVE_OUTPUT_REFRESH_SAFETY_MS",
            "visible",
        ):
            self.assertIn(source_state, tracker)

        active_poll = STORE[
            STORE.index("_pollRecoveredJob: (jobId)"):
            STORE.index("reconnectJobs: async", STORE.index("_pollRecoveredJob: (jobId)"))
        ]
        self.assertIn("_activeOutputRefreshDue", active_poll)
        self.assertIn("document.removeEventListener('visibilitychange'", active_poll)
        self.assertIn("status.status === 'completed'", active_poll)
        self.assertIn("status.status === 'failed' || status.status === 'cancelled'", active_poll)
        self.assertGreaterEqual(active_poll.count("get().loadOutputs()"), 2)

    def test_phase_and_output_churn_is_coalesced_to_the_budget_cap(self):
        minimum = _store_interval("ACTIVE_OUTPUT_REFRESH_MIN_MS")
        poll = _store_interval("ACTIVE_JOB_STATUS_POLL_MS")
        last_refresh = -minimum
        refreshes = 0
        for now in range(0, MILLISECONDS_PER_DAY, poll):
            # Every tick represents a legal changed phase/output identity.
            if now - last_refresh >= minimum:
                refreshes += 1
                last_refresh = now
        # A 15s cap observed by a 2s status clock fires every 16s. It may be
        # slower than the theoretical cap, but must never exceed it.
        self.assertEqual(refreshes, 5_400)
        self.assertLessEqual(refreshes, _requests_per_day(minimum))

    def test_queue_snapshot_drives_start_transition_without_queued_fast_fanout(self):
        reconcile = STORE[
            STORE.index("reconcileQueueState: (queue)"):
            STORE.index("resumeJobRecovery: async", STORE.index("reconcileQueueState: (queue)"))
        ]
        standard = STORE[
            STORE.index("// Queued cards use the shared queue snapshot"):
            STORE.index("} catch (e) {", STORE.index("// Queued cards use the shared queue snapshot"))
        ]
        reconnect = STORE[
            STORE.index("reconnectJobs: async"):
            STORE.index("// LoRA state", STORE.index("reconnectJobs: async"))
        ]
        self.assertIn("_queueJobDetails(queueJob, job)", reconcile)
        self.assertIn("becameFast", reconcile)
        self.assertIn("_jobNeedsFastStatusPoll", reconcile)
        self.assertIn("get()._pollRecoveredJob(job_id)", standard)
        self.assertNotIn("setInterval", standard)
        self.assertIn("get()._pollRecoveredJob(status.job_id)", reconnect)
        self.assertNotIn("setInterval", reconnect)
        self.assertIn("QUEUED_JOB_STATUS_SAFETY_MS", STORE)
        fast_gate = STORE[
            STORE.index("function _jobNeedsFastStatusPoll"):
            STORE.index("function _queueJobDetails")
        ]
        self.assertIn("job.status === 'running'", fast_gate)
        self.assertIn("job.recoveryState === 'retrying'", fast_gate)

    def test_all_job_submit_paths_use_the_one_queue_aware_poller(self):
        # Plan-review hydration plus two recovery refreshes are bounded
        # one-shots; _pollRecoveredJob remains the sole recurring fetch.
        self.assertEqual(STORE.count("api.fetchJobStatus("), 4)
        review_start = STORE.index("openH3PlanReview: async")
        review = STORE[
            review_start:STORE.index("closeH3PlanReview: () =>", review_start)
        ]
        recurring_start = STORE.index("_pollRecoveredJob: (jobId) => {")
        recurring = STORE[
            recurring_start:STORE.index("reconnectJobs: async", recurring_start)
        ]
        self.assertEqual(review.count("api.fetchJobStatus(jobId)"), 1)
        self.assertNotIn("setInterval", review)
        self.assertNotIn("setTimeout", review)
        self.assertEqual(recurring.count("api.fetchJobStatus(jobId)"), 1)
        self.assertIn("scheduleNext()", recurring)
        self.assertIn("_recoveryJobPolls.set(jobId, poll)", recurring)
        self.assertEqual(
            recurring.count("_recoveryJobPolls.get(jobId) !== poll"),
            2,
        )
        self.assertIn("|| !_accountIdentityIsCurrent(accountIdentityEpoch)", recurring)
        self.assertNotIn("const pollInterval = setInterval(async", STORE)
        self.assertNotIn("status.status === 'running') get().refreshOutputs()", STORE)
        self.assertEqual(
            STORE.count("get()._pollRecoveredJob(result.job_id)"),
            6,
        )
        director_start = STORE.index("directorGenerateStartImages: async")
        director_images = STORE[
            director_start:STORE.index("directorReset: ()", director_start)
        ]
        self.assertIn("_waitForTerminalJobStatus(job_id, 600_000)", director_images)
        self.assertIn("get()._pollRecoveredJob(job_id)", director_images)
        self.assertNotIn("while (attempts <", director_images)
        self.assertIn("_rejectTerminalJobWaiter(jobId, 'Generation cancelled')", STORE)
        self.assertIn("[..._recoveryJobPolls.values()]", STORE)
        self.assertIn("[..._terminalJobWaiters.keys()]", STORE)

    def test_tree_wide_job_status_calls_remain_centralized(self):
        callsites = {
            path: source.count("fetchJobStatus(")
            for path, source in UI_SOURCES.items()
            if "fetchJobStatus(" in source
        }
        self.assertEqual(
            callsites,
            {
                "ui/src/api/client.ts": 1,
                "ui/src/stores/useStore.ts": 4,
            },
        )
        references = UI_SOURCES[
            "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx"
        ]
        self.assertIn("await generateProjectAssetReferences(project", references)
        self.assertEqual(
            references.count("await confirmAcceptedProjectReferenceJob("),
            2,
        )
        self.assertEqual(references.count("jobId => confirmReconnectedJob("), 2)
        self.assertIn(
            "response.job_id,\n        jobId => confirmReconnectedJob(",
            references,
        )
        self.assertIn(
            "jobId,\n          reconnectJobs,\n          () => useStore.getState().jobs,",
            references,
        )
        retake = UI_SOURCES["ui/src/components/RetakeDialog.tsx"]
        self.assertIn("await api.submitRetake({", retake)
        self.assertNotIn("fetchJobStatus(", retake)
        self.assertNotIn("setInterval", retake)

    def test_pipeline_gallery_publication_uses_the_shared_rate_cap(self):
        pipeline_start = STORE.index("pollPipelineStatus: () => {")
        pipeline = STORE[
            pipeline_start:STORE.index("\n  },\n}))", pipeline_start)
        ]
        self.assertIn("_coalescedGalleryRefreshDue(", pipeline)
        self.assertIn("ACTIVE_OUTPUT_REFRESH_MIN_MS", STORE)
        self.assertEqual(pipeline.count("refreshOutputs()"), 1)
        self.assertNotIn("status.phase === 'generating_images') {\n          get().refreshOutputs()", pipeline)

    def test_reference_refreshes_share_one_sequence_and_visibility_scheduler(self):
        self.assertIn("const requestSequence = useRef(0)", REFERENCES)
        self.assertIn("async (signal: AbortSignal)", REFERENCES)
        self.assertIn("signal.aborted || sequence !== requestSequence.current", REFERENCES)
        self.assertIn("const refreshNow = useVisibilityPolling(", REFERENCES)
        self.assertIn("requestSequence.current += 1", REFERENCES)
        self.assertNotIn("await refresh()", REFERENCES)

    def test_hardware_cadence_comments_match_runtime(self):
        self.assertNotIn("HardwareStatusBar). Polled ~2s", STORE)
        self.assertIn("HardwareStatusBar). Polled ~5s", STORE)
        self.assertNotIn("console at 2s cadence", STORE)


if __name__ == "__main__":
    unittest.main()
