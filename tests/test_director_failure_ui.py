"""Source contracts for Director terminal-state rendering."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
CHAT = (ROOT / "ui/src/components/Sidebar/DirectorChat.tsx").read_text(
    encoding="utf-8",
)
DASHBOARD = (
    ROOT / "ui/src/components/DirectorDashboard/DirectorDashboard.tsx"
).read_text(encoding="utf-8")


class TestDirectorFailureUi(unittest.TestCase):
    def test_status_type_and_submission_use_current_director_transients(self):
        status = re.search(
            r"export interface PipelineStatus[\s\S]+?\n}\n",
            CLIENT,
        )
        self.assertIsNotNone(status)
        self.assertIn("'queued'", status.group(0))
        self.assertIn("'registered'", status.group(0))

        submission_start = STORE.index(
            "const pipelineParams: Record<string, unknown> = {",
        )
        submission_end = STORE.index(
            "api.startPipeline(pipelineParams)", submission_start,
        )
        submission = STORE[submission_start:submission_end]
        self.assertIn(
            "director_max_shot_frames: directorMaxShotFrames",
            submission,
        )

    def test_active_pipeline_accepts_registered_queue_then_running_phases(self):
        predicate = re.search(
            r"export function isDirectorPipelineActive\([\s\S]+?\n}\n",
            STORE,
        )
        self.assertIsNotNone(predicate)
        self.assertIn("status?.status === 'running'", predicate.group(0))
        self.assertIn("status?.status === 'queued'", predicate.group(0))
        self.assertIn("status.phase === 'registered'", predicate.group(0))
        self.assertIn("DIRECTOR_PIPELINE_ACTIVE_PHASES.has(status.phase)", predicate.group(0))

        phases = re.search(
            r"DIRECTOR_PIPELINE_ACTIVE_PHASES[^=]*= new Set[^\[]*\[([\s\S]+?)\]\)",
            STORE,
        )
        self.assertIsNotNone(phases)
        for phase in (
            "registered",
            "planning",
            "polishing_prompts",
            "generating_images",
            "generating_video",
            "post_processing",
        ):
            self.assertIn(f"'{phase}'", phases.group(1))
        self.assertNotIn("'completed'", phases.group(1))

        # A queued registration and its following running phase both keep the
        # same Director activity indicator alive; terminal state ends it.
        states = [
            ("queued", "registered"),
            ("running", "planning"),
            ("completed", "completed"),
        ]
        active = [
            (status == "running" or (status == "queued" and phase == "registered"))
            and phase in phases.group(1)
            for status, phase in states
        ]
        self.assertEqual(active, [True, True, False])

    def test_chat_generation_state_uses_active_predicate_not_phase_presence(self):
        self.assertIn(
            "const pipelineActive = isDirectorPipelineActive(pipelineStatus)",
            CHAT,
        )
        self.assertIn("isGenerating={isGenerating || pipelineActive}", CHAT)
        self.assertIn("isAutoGenerating={autoMode && pipelineActive}", CHAT)
        self.assertNotIn("pipelinePhase !== undefined", CHAT)

    def test_terminal_poll_stops_activity_and_preserves_visible_error(self):
        terminal_update = re.search(
            r"const pipelineActive = isDirectorPipelineActive\(status\)([\s\S]+?)"
            r"// Sync pipeline state",
            STORE,
        )
        self.assertIsNotNone(terminal_update)
        update = terminal_update.group(1)
        self.assertIn("directorLoading: false", update)
        self.assertIn("directorLoading: true", update)
        self.assertIn("pipelinePolling: false", update)
        self.assertNotIn("llmStreamDone", update)
        self.assertIn("directorError: status.error || 'Pipeline stopped'", update)
        self.assertIn("status: 'error'", update)
        self.assertIn("const pipelineBlocked = status.status === 'blocked'", update)
        self.assertIn("pipelineTerminal || pipelineBlocked", update)

        transitions = re.search(
            r"// Handle phase transitions([\s\S]+?)// The exact pipeline status",
            STORE,
        )
        self.assertIsNotNone(transitions)
        self.assertEqual(transitions.group(1).count("pipelineActive && status.phase"), 3)

        autoscroll = re.search(
            r"messagesEndRef\.current\?\.scrollIntoView[\s\S]+?\n\s*}, \[([^]]+)\]\)",
            CHAT,
        )
        self.assertIsNotNone(autoscroll)
        self.assertIn("error", autoscroll.group(1))

    def test_dashboard_empty_state_waits_for_authoritative_list_read(self):
        self.assertNotIn("fetchPipelineList", DASHBOARD)
        self.assertIn(
            "const pipelineListRead = useStore(s => s.dashboardPipelineListRead)",
            DASHBOARD,
        )
        self.assertIn("dashboardPipelineListRead: DashboardPipelineListRead", STORE)
        self.assertIn(
            "dashboardPipelineListRead: { workspace, generation, status: 'loading' }",
            STORE,
        )
        self.assertIn(
            "dashboardPipelineListRead: { workspace, generation, status: 'ready' }",
            STORE,
        )
        self.assertIn(
            "dashboardPipelineListRead: { workspace, generation, status: 'failed' }",
            STORE,
        )
        self.assertEqual(STORE.count("await api.fetchPipelineList(workspace)"), 1)
        self.assertIn("generation !== _dashboardPipelineListLoadToken", STORE)
        self.assertIn("get().activeWorkspace !== workspace", STORE)
        self.assertIn("const pipelineListHydrating =", DASHBOARD)
        self.assertIn("const pipelineListFailed =", DASHBOARD)
        self.assertIn("const pipelineListEmpty =", DASHBOARD)
        self.assertIn("pipelineListRead.status === 'idle'", DASHBOARD)
        self.assertIn("pipelineListRead.status === 'ready'", DASHBOARD)
        self.assertNotIn("pipelineListRead.count", DASHBOARD)
        self.assertIn("{(loading || pipelineListHydrating) && (", DASHBOARD)
        self.assertIn("{!loading && pipelineListFailed && (", DASHBOARD)
        self.assertIn("Saved pipelines unavailable", DASHBOARD)

        start = DASHBOARD.index("{!loading && pipelineListEmpty && (")
        end = DASHBOARD.index("{selectedPipeline && (", start)
        empty_state = DASHBOARD[start:end]

        self.assertIn("<Film size={24} />", empty_state)
        self.assertIn("<h2", empty_state)
        self.assertIn("No saved pipelines yet", empty_state)
        self.assertIn("Run a Director pipeline and it will appear here", empty_state)
        self.assertIn("text-text-primary", empty_state)
        self.assertIn("text-text-muted", empty_state)
        self.assertNotIn("<button", empty_state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
