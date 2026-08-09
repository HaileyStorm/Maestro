"""Truthful generation telemetry regression coverage."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _load_function(path: str, name: str, namespace: dict | None = None):
    tree = ast.parse(_source(path), filename=path)
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = dict(namespace or {})
    exec(compile(module, path, "exec"), scope)
    return scope[name]


class GenerationTelemetryTests(unittest.TestCase):
    def test_named_prep_is_indeterminate_then_exact_steps_are_determinate(self):
        normalize = _load_function("app/launch.py", "_phase_progress_values")

        loading = normalize("Loading H3 conditioner checkpoint")
        prep = normalize("Preparing H3 denoising schedule")
        pass_boundary = normalize("Denoising", -1, 0)
        first = normalize("Denoising | 0:12", 1, 20)
        middle = normalize("Denoising | 1:03", 10, 20)
        short_wgp = normalize("Denoising | 2.4s", 2, 20)
        minute_wgp = normalize("Denoising | 2m 04s", 3, 20)
        hour_wgp = normalize("Denoising | 1h 02m 03s", 4, 20)

        self.assertTrue(loading["progress_indeterminate"])
        self.assertTrue(prep["progress_indeterminate"])
        self.assertTrue(pass_boundary["progress_indeterminate"])
        self.assertFalse(first["progress_indeterminate"])
        self.assertFalse(middle["progress_indeterminate"])
        self.assertEqual(first["phase"], "Denoising")
        self.assertEqual(middle["phase"], "Denoising")
        self.assertEqual(short_wgp["phase"], "Denoising")
        self.assertEqual(minute_wgp["phase"], "Denoising")
        self.assertEqual(hour_wgp["phase"], "Denoising")

    def test_eta_excludes_queue_and_generation_hold_time(self):
        clock = SimpleNamespace(time=lambda: 160.0)
        eta_values = _load_function(
            "app/launch.py", "_job_eta_values", {"time": clock},
        )
        queued = {
            "status": "queued",
            "h3_estimate": {"seconds": 120, "model_load_seconds": 30},
        }
        running = {
            "status": "running",
            "started_at": 100.0,
            "phase_started_at": 150.0,
            "overall_progress": 50,
            "window_step": 10,
            "window_total_steps": 20,
            "_eta_inactive_seconds": 50.0,
        }

        self.assertEqual(eta_values(queued), (None, None))
        # Only ten active seconds elapsed: ten remain overall and in phase.
        self.assertEqual(eta_values(running), (10, 10))

    def test_cold_load_does_not_inflate_early_h3_eta(self):
        clock = SimpleNamespace(time=lambda: 610.0)
        eta_values = _load_function(
            "app/launch.py", "_job_eta_values", {"time": clock},
        )
        early = {
            "status": "running",
            "started_at": 10.0,
            "phase_started_at": 600.0,
            "overall_progress": 1,
            "window_step": 1,
            "window_total_steps": 20,
            "h3_estimate": {
                "seconds": 120,
                "model_load_seconds": 150,
            },
        }
        blended = {
            **early,
            "overall_progress": 20,
            "phase_started_at": 100.0,
            "_eta_progress_started_at": 100.0,
            "_eta_progress_baseline": 5,
        }
        early_eta, _ = eta_values(early)
        clock.time = lambda: 120.0
        blended_eta, _ = eta_values(blended)
        self.assertLessEqual(early_eta, 120)
        self.assertGreaterEqual(early_eta, 100)
        self.assertGreaterEqual(blended_eta, 75)
        self.assertLessEqual(blended_eta, 120)

    def test_h3_progress_stream_anchors_eta_before_first_step_warmup(self):
        clock = SimpleNamespace(time=lambda: 110.0)
        eta_values = _load_function(
            "app/launch.py", "_job_eta_values", {"time": clock},
        )
        track_progress = _load_function(
            "app/launch.py",
            "_subtask_eta_progress_updates",
            {"time": clock},
        )
        job = {
            "status": "running",
            "started_at": 50.0,
            "phase_started_at": 109.0,
            "overall_progress": 5,
            "window_current": 1,
            "_eta_inactive_seconds": 0,
        }

        # These are the actual payload shapes emitted by WGP's
        # set_progress_status and build_callback paths.
        clock.time = lambda: 100.0
        warmup = [
            0,
            "Clip 1/2 - Running first H3 denoising step (runtime warmup)",
        ]
        job.update(track_progress(job, {"window_current": 1}, warmup))

        clock.time = lambda: 110.0
        first_step = [(1, 20), "Clip 1/2 - Denoising | 10s"]
        job.update(track_progress(job, {"window_current": 1}, first_step))
        job.update(window_step=1, window_total_steps=20)

        _, after_first = eta_values(job)
        clock.time = lambda: 120.0
        second_step = [(2, 20), "Clip 1/2 - Denoising | 20s"]
        job.update(track_progress(job, {"window_current": 1}, second_step))
        job["window_step"] = 2
        _, after_second = eta_values(job)

        self.assertEqual(job["_eta_subtask_started_at"], 100.0)
        self.assertEqual(after_first, 190)
        self.assertEqual(after_second, 180)

    def test_denominatorless_decode_preserves_completed_progress(self):
        window_values = _load_function(
            "app/launch.py", "_window_progress_values",
        )
        preserve = _load_function(
            "app/launch.py", "_preserve_indeterminate_progress",
        )
        fraction, percent, exact = window_values(0, 0, 100)
        decode = {
            "progress": 0,
            "window_progress": percent,
            "overall_progress": 50,
            "clip_progress": percent,
        }
        prior_denoise = {
            "progress": 75,
            "window_progress": 100,
            "overall_progress": 75,
            "clip_progress": 100,
        }

        preserve(decode, prior_denoise, exact)

        self.assertEqual(fraction, 1.0)
        self.assertFalse(exact)
        self.assertEqual(decode, prior_denoise)

    def test_h3_load_and_generation_phases_follow_real_work_order(self):
        main = _source("app/models/minimax_h3/minimax_h3_main.py")
        load_labels = [
            "Loading H3 transformer checkpoint",
            "Loading H3 conditioner checkpoint",
            "Loading H3 video VAE checkpoint",
            "Loading H3 audio VAE checkpoint",
        ]
        self.assertEqual(
            [main.index(label) for label in load_labels],
            sorted(main.index(label) for label in load_labels),
        )
        self.assertLess(
            main.index('report_phase("Preparing H3 denoising schedule")'),
            main.index("callback(-1"),
        )
        self.assertLess(
            main.index("callback(-1"),
            main.index('report_phase("Running first H3 denoising step (runtime warmup)")'),
        )
        self.assertLess(
            main.index('report_phase("Decoding H3 video")'),
            main.index("self.vae.decode(video_latents"),
        )
        self.assertLess(
            main.index('report_phase("Decoding H3 audio")'),
            main.index("self.audio_vae.decode(audio_latents"),
        )

    def test_audio_progress_does_not_announce_work_before_model_load(self):
        source = _source("app/services/audio_analysis.py")
        analyze = source[source.index("def analyze("):source.index("def suggest_clip_boundaries(")]
        self.assertLess(
            analyze.index("_get_whisper_model()"),
            analyze.index('_set_progress("transcribing", "Transcribing audio")'),
        )
        self.assertLess(
            analyze.index('diarizer = get_diarizer_pipeline(profile="music")'),
            analyze.index('_set_progress("identifying_speakers", "Identifying speakers")'),
        )
        self.assertNotIn('_set_progress("extracting_vocals"', analyze)

    def test_status_jobs_director_and_ui_share_phase_semantics(self):
        launch = _source("app/launch.py")
        director = _source("app/services/director_pipeline.py")
        client = _source("ui/src/api/client.ts")
        store = _source("ui/src/stores/useStore.ts")
        main = _source("ui/src/components/MainContent/MainContent.tsx")

        self.assertGreaterEqual(launch.count("**_public_progress_telemetry(j)"), 2)
        self.assertIn("progress_indeterminate?: boolean", client)
        self.assertIn("progressIndeterminate: status.status === 'running'", store)
        self.assertIn("hasExactCurrentSteps\n        ? (currentStep / currentTotalSteps)", main)
        self.assertIn("Overall ETA ${compactEta(job.etaSeconds)}", main)
        self.assertIn("Current segment ETA ${compactEta(job.subtaskEtaSeconds)}", main)
        self.assertIn("Estimated time ${compactEta(queuedH3Runtime)} after start", main)
        self.assertIn("Planned time ${compactEta(queuedH3Runtime)} after start", main)
        self.assertIn("stripTimeSuffix", main)
        pipeline_start = main.index("function PipelinePlaceholder()")
        pipeline = main[pipeline_start:]
        self.assertIn("progress?.overall_progress", pipeline)
        self.assertIn("progress?.window_total_steps", pipeline)
        self.assertIn("progress?.indeterminate", pipeline)
        self.assertIn("Current segment", pipeline)
        self.assertIn('if _dir_pid and j.get("status") in {"queued", "running"}:', director)
        self.assertIn('p["progress"]["indeterminate"]', director)
        self.assertIn('p["progress"]["window_progress"]', director)

        director_panel = _source("ui/src/components/Sidebar/DirectorPanel.tsx")
        self.assertIn("Director video · overall", director_panel)
        self.assertIn("videoTelemetry.indeterminate", director_panel)
        self.assertIn("videoTelemetry.window_current", director_panel)

        reconnect_start = store.index("reconnectJobs: async")
        reconnect = store[
            reconnect_start:store.index("// LoRA state", reconnect_start)
        ]
        self.assertNotIn("isGenerating: remaining.length > 0", reconnect)
        self.assertIn("job.status === 'queued' || job.status === 'running'", reconnect)

    def test_requeued_h3_card_prefers_preserved_remaining_eta(self):
        main = _source("ui/src/components/MainContent/MainContent.tsx")
        store = _source("ui/src/stores/useStore.ts")
        estimate_helper = main[
            main.index("function h3EstimatedRuntime"):
            main.index("function h3QueuedRuntime")
        ]
        helper = main[
            main.index("function h3QueuedRuntime"):
            main.index("function estimateRuntime")
        ]
        placeholder = main[
            main.index("function JobPlaceholder"):
            main.index("function queueSummaryLabel")
        ]
        eta_render = placeholder[
            placeholder.index("{!isFailed && !recoveryBlocked && ("):
            placeholder.index("{recoveryBlocked && (")
        ]
        status_mapper = store[
            store.index("function _jobStatusDetails"):
            store.index("function _mergeJobStatus")
        ]

        self.assertIn("const estimate = job.h3Estimate", estimate_helper)
        self.assertIn("const remaining = job.etaSeconds", helper)
        self.assertIn(
            "remaining != null && Number.isFinite(remaining) && remaining >= 0",
            helper,
        )
        self.assertLess(
            helper.index("return remaining"),
            helper.index("return h3EstimatedRuntime(job)"),
        )
        self.assertIn("? h3QueuedRuntime(job)", placeholder)
        self.assertIn(
            "job.status === 'queued' && job.modelType?.startsWith('minimax_h3')",
            placeholder,
        )
        self.assertIn(
            "held: 'Held — use Start next or Resume when ready'",
            placeholder,
        )
        self.assertIn(
            "Estimated time ${compactEta(queuedH3Runtime)} after start",
            eta_render,
        )
        self.assertIn(
            "Planned time ${compactEta(queuedH3Runtime)} after start",
            eta_render,
        )
        self.assertIn("Overall ETA ${compactEta(job.etaSeconds)}", eta_render)
        self.assertIn("? (previous?.etaSeconds ?? estimatedTotal)", status_mapper)


if __name__ == "__main__":
    unittest.main()
