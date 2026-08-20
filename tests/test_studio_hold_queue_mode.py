"""Continuum hold-queue contracts for Studio ListPlus and Director Add to Queue.

Locks the leftover 1.9.0 port: `_queue_mode: held` uses Continuum
`queue_held` + `set_job_hold`, not the upstream `status=="held"` /
`release_held` / `_start_held_studio_queue` lifecycle.
"""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = (ROOT / "app/launch.py").read_text(encoding="utf-8")
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
QUEUE_PROJECTION = (
    ROOT / "ui/src/lib/queueProjection.ts"
).read_text(encoding="utf-8")
GENERATE_BUTTON = (
    ROOT / "ui/src/components/Sidebar/GenerateButton.tsx"
).read_text(encoding="utf-8")
DIRECTOR_CHAT = (
    ROOT / "ui/src/components/Sidebar/DirectorChat.tsx"
).read_text(encoding="utf-8")


def _slice(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


class StudioHoldQueueModeTests(unittest.TestCase):
    def test_generate_accepts_held_queue_mode_and_sets_queue_held(self):
        generate = _slice(LAUNCH, "async def generate(request: Request):", "@api.post(\"/api/v1/retake\")")
        self.assertIn("_queue_mode", generate)
        self.assertIn("queue_mode not in {\"now\", \"held\"}", generate)
        self.assertIn("hold_for_queue = queue_mode == \"held\"", generate)
        self.assertIn("\"queue_held\": bool(hold_for_queue)", generate)
        self.assertIn("\"Ready - waiting for Start Queue\" if hold_for_queue", generate)
        self.assertIn("\"held\": bool(job.get(\"queue_held\"))", generate)
        self.assertIn("_queue_recovery_register_and_publish(job, worker=_run_generation)", generate)
        self.assertNotIn("release_held", generate)
        self.assertNotIn("_start_held_studio_queue", generate)
        self.assertNotIn("status\": \"held\"", generate)

    def test_start_studio_queue_releases_via_set_job_hold(self):
        start_queue = _slice(
            LAUNCH,
            "def start_studio_queue(request: Request, response: Response):",
            "def resume_held_job(",
        )
        self.assertIn("if not job.get(\"queue_held\")", start_queue)
        self.assertIn("mode = set_job_hold(owned, False)", start_queue)
        self.assertNotIn("release_held", start_queue)
        self.assertNotIn("_run_held_studio_jobs", start_queue)

    def test_client_posts_held_or_now_queue_mode(self):
        submit = _slice(CLIENT, "export async function submitGeneration(", "export interface GenerationPlanApprovalRequest")
        self.assertIn("holdForQueue = false", submit)
        self.assertIn("_queue_mode: holdForQueue ? 'held' : 'now'", submit)
        self.assertIn("held?: boolean", submit)

    def test_listplus_starts_generation_in_queue_mode(self):
        self.assertIn("startGeneration(mode)", GENERATE_BUTTON)
        self.assertIn("handleClick('queue')", GENERATE_BUTTON)
        self.assertIn("Hold current Studio settings in the queue without starting generation", GENERATE_BUTTON)
        self.assertIn("if (mode === 'now') setSidebarOpen(false)", GENERATE_BUTTON)
        generation = _slice(STORE, "startGeneration: async (mode = 'now') => {", "stopGeneration: (jobId)")
        self.assertIn("const holdForQueue = mode === 'queue'", generation)
        self.assertIn("held: holdForQueue", generation)
        self.assertIn("isGenerating: holdForQueue ? s.isGenerating : true", generation)
        self.assertIn("await api.submitGeneration(params, holdForQueue)", generation)
        self.assertIn("'Ready - waiting for Start Queue'", generation)

    def test_director_add_to_queue_enqueues_pipeline(self):
        self.assertIn("queueCurrentDirectorPipeline()", DIRECTOR_CHAT)
        self.assertIn("Hold this complete project in the persistent queue without starting it", DIRECTOR_CHAT)
        queue_entry = _slice(
            STORE,
            "queueCurrentDirectorPipeline: async () => {",
            "startDirectorPipeline: async",
        )
        self.assertIn("startDirectorPipeline('queue')", queue_entry)
        pipeline = _slice(
            STORE,
            "startDirectorPipeline: async (mode = 'now') => {",
            "const { pipeline_id } = await api.startPipeline(pipelineParams)",
        )
        self.assertIn("if (mode === 'queue')", pipeline)
        self.assertIn("await api.enqueueDirectorPipeline(pipelineParams)", pipeline)
        enqueue = _slice(
            CLIENT,
            "export async function enqueueDirectorPipeline(",
            "export async function startDirectorQueue()",
        )
        self.assertIn("${BASE}/api/v1/director/queue", enqueue)
        self.assertIn("method: 'POST'", enqueue)

    def test_queue_projection_counts_continuum_held_flag_not_status_held(self):
        statuses = _slice(
            QUEUE_PROJECTION,
            "const ACTIVE_STATUSES = new Set<GenerationJob['status']>([",
            "])",
        )
        self.assertIn("'queued'", statuses)
        self.assertIn("'running'", statuses)
        self.assertNotIn("'held'", statuses)
        self.assertIn("if (queueJob?.held || publicJob.held) summary.held += 1", QUEUE_PROJECTION)
        self.assertIn(
            "job.status === 'queued' || job.status === 'running' || job.held",
            GENERATE_BUTTON,
        )


if __name__ == "__main__":
    unittest.main()
