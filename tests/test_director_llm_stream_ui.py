"""Source contracts for pipeline-scoped Director LLM telemetry in the UI."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
CHAT = (ROOT / "ui/src/components/Sidebar/DirectorChat.tsx").read_text(
    encoding="utf-8",
)
DASHBOARD = (
    ROOT / "ui/src/components/DirectorDashboard/DirectorDashboard.tsx"
).read_text(encoding="utf-8")
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")


def source_slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class TestDirectorLlmStreamUi(unittest.TestCase):
    def test_live_status_types_exact_pipeline_progress_envelope(self):
        progress = source_slice(
            CLIENT,
            "export interface PipelineLlmProgress",
            "export interface PipelineStatus",
        )
        for field in (
            "phase", "pass", "activity", "partial_text", "attempt",
            "attempt_limit", "generated_tokens_approx", "elapsed_seconds",
            "live_tps", "average_tps", "done",
        ):
            self.assertRegex(progress, rf"\b{field}\s*:")

        status = source_slice(
            CLIENT,
            "export interface PipelineStatus",
            "export async function startPipeline",
        )
        self.assertIn("workspace: string", status)
        self.assertIn("llm_progress: PipelineLlmProgress | null", status)
        self.assertIn("llm_planning_time_sec?: number | null", status)
        self.assertNotIn("llm_streaming", status)

    def test_director_chat_consumes_pipeline_snapshot_without_raw_history(self):
        stream = source_slice(CHAT, "function LlmThinkingStream", "export function DirectorChat")
        self.assertIn("s.pipelineStatus", stream)
        self.assertIn("pipelineStatus?.llm_progress", stream)
        self.assertIn("progress.partial_text", stream)
        self.assertIn("progress.live_tps", stream)
        self.assertIn("progress.average_tps", stream)
        self.assertIn("progress.attempt_limit", stream)
        self.assertIn('role="status"', stream)
        self.assertIn('aria-live="polite"', stream)
        self.assertIn('aria-atomic="true"', stream)
        self.assertIn('aria-live="off"', stream)
        self.assertNotIn("/api/v1/llm/stream-status", CHAT)
        self.assertNotIn("directorAppendLlmLog", CHAT)
        self.assertNotIn("LlmLogStage", CHAT)
        self.assertNotIn("setStreamText", stream)

        live_region = re.search(
            r'<span role="status" aria-live="polite"[\s\S]+?</span>',
            stream,
        )
        self.assertIsNotNone(live_region)
        self.assertNotIn("partialText", live_region.group(0))
        self.assertNotIn("metrics", live_region.group(0))

    def test_poller_guards_pipeline_and_project_before_and_after_await(self):
        poller = source_slice(STORE, "pollPipelineStatus: () => {", "\n  },\n}))")
        self.assertIn("const workspace = get().activeWorkspace", poller)
        fetch_at = poller.index("await api.fetchPipelineStatus(pid)")
        first_workspace_guard = poller.index(
            "get().activeWorkspace !== workspace",
        )
        second_workspace_guard = poller.index(
            "get().activeWorkspace !== workspace",
            first_workspace_guard + 1,
        )
        store_status_at = poller.index("pipelineStatus: status")
        self.assertLess(first_workspace_guard, fetch_at)
        self.assertLess(fetch_at, second_workspace_guard)
        self.assertLess(second_workspace_guard, store_status_at)
        self.assertIn("status.id !== pid || status.workspace !== workspace", poller)
        self.assertIn("? 400 : 2000", poller)
        self.assertNotIn("status.llm_streaming", poller)

    def test_lifecycle_clears_transient_status_and_old_stream_state_is_gone(self):
        for obsolete in (
            "directorLlmLog", "directorAppendLlmLog", "llmStreamText",
            "llmStreamDone",
        ):
            self.assertNotIn(obsolete, STORE)

        start = source_slice(STORE, "startDirectorPipeline: async", "continuePipeline: async")
        resume = source_slice(STORE, "resumePipeline: async", "// ── Recipes")
        reset = source_slice(STORE, "directorReset: () => {", "// --- Short Film Director actions ---")
        switch = source_slice(STORE, "switchWorkspace: async", "createWorkspace: async")
        for lifecycle in (start, resume, reset, switch):
            self.assertIn("pipelineStatus: null", lifecycle)

        poller = source_slice(STORE, "pollPipelineStatus: () => {", "\n  },\n}))")
        self.assertIn("pipelineStatus: status", poller)
        self.assertIn("pipelineTerminal || pipelineBlocked", poller)

    def test_start_and_resume_use_one_token_bound_project_lifecycle(self):
        helper = source_slice(
            STORE,
            "function _beginDirectorPipelineLifecycle",
            "function _isBrowserAbort",
        )
        self.assertIn("_directorPipelineLifecycleToken = token", helper)
        self.assertIn("lifecycle.ownsWorkspace()", helper)
        self.assertIn("_directorPipelineLifecycleToken === token", helper)

        start = source_slice(STORE, "startDirectorPipeline: async", "continuePipeline: async")
        begin_at = start.index("_beginDirectorPipelineLifecycle(requestWorkspace)")
        defaults_at = start.index("await Promise.all")
        post_at = start.index("await api.startPipeline(pipelineParams)")
        guards = [match.start() for match in re.finditer(
            r"if \(!lifecycle\.ownsWorkspace\(\)\) return",
            start,
        )]
        self.assertLess(begin_at, defaults_at)
        self.assertTrue(any(defaults_at < guard < post_at for guard in guards))
        self.assertTrue(any(guard > post_at for guard in guards))
        self.assertIn("workspace: requestWorkspace", start)
        self.assertRegex(
            start,
            r"catch \(e\) \{\s+if \(!lifecycle\.ownsWorkspace\(\)\) return",
        )
        self.assertIn("finally {\n      lifecycle.dispose()", start)

        resume = source_slice(STORE, "resumePipeline: async", "// ── Recipes")
        self.assertIn("workspace !== state.activeWorkspace", resume)
        self.assertIn("_beginDirectorPipelineLifecycle(workspace)", resume)
        self.assertGreaterEqual(
            resume.count("if (!lifecycle.ownsWorkspace()) return"),
            3,
        )
        self.assertIn("finally {\n      lifecycle.dispose()", resume)

        switch = source_slice(STORE, "switchWorkspace: async", "createWorkspace: async")
        self.assertLess(
            switch.index("_directorPipelineLifecycleToken = null"),
            switch.index("await api.setActiveWorkspace(name)"),
        )
        self.assertLess(
            switch.index("_dashboardPipelineLoadToken += 1"),
            switch.index("await api.setActiveWorkspace(name)"),
        )
        self.assertLess(
            switch.index("_dashboardPipelineListLoadToken += 1"),
            switch.index("await api.setActiveWorkspace(name)"),
        )
        self.assertIn("dashboardSelectedPipeline: null", switch)

    def test_token_ownership_model_rejects_project_switch_and_older_response(self):
        current_workspace = "project-a"
        current_token = object()
        first_token = current_token

        def owns(token: object, workspace: str) -> bool:
            return token is current_token and workspace == current_workspace

        self.assertTrue(owns(first_token, "project-a"))

        # A project switch invalidates the first operation before it can adopt
        # an upload, error, or POST response.
        current_workspace = "project-b"
        self.assertFalse(owns(first_token, "project-a"))

        # A competing operation in the same project invalidates the older PID.
        second_token = object()
        current_token = second_token
        self.assertFalse(owns(first_token, "project-b"))
        self.assertTrue(owns(second_token, "project-b"))

    def test_saved_dashboard_is_aggregate_only_and_ignores_legacy_raw_log(self):
        for raw_field in (
            "system_prompt", "user_prompt", "response_text", "thinking_text",
            "LlmPassView", "LlmLogPanel",
        ):
            self.assertNotIn(raw_field, DASHBOARD)
        self.assertIn("pipeline.llm_planning_time_sec", DASHBOARD)
        self.assertIn("PlanningTelemetryPanel", DASHBOARD)
        self.assertNotIn("pipeline.llm_log", DASHBOARD)

        self.assertNotIn("interface PipelineLlmPass", TYPES)
        self.assertNotIn("interface PipelineLlmLog", TYPES)
        saved = source_slice(
            TYPES,
            "export interface SavedPipelineState",
            "export interface PipelineListItem",
        )
        self.assertIn("llm_log: null", saved)
        self.assertIn("llm_planning_time_sec?: number | null", saved)
        self.assertNotIn("llm_progress", saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
