"""Static wiring checks that avoid importing Maestro's heavyweight server.

Locks leftover 1.9.0 `generate_video` AST probes to Continuum's
`_generate_video_impl` (the public wrapper is H3 OOM relief only) and the
Continuum `format_generation_time` raw-seconds form. Do not invent the
dropped compact `2m 8s` formatter or treat the wrapper as the impl.
"""
from __future__ import annotations

import ast
import math
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _parse(relative_path: str) -> ast.Module:
    with open(os.path.join(_ROOT, relative_path), "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=relative_path)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found")


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def _load_isolated_function(relative_path: str, name: str, namespace: dict):
    function = _function(_parse(relative_path), name)
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, relative_path, "exec"), namespace)
    return namespace[name]


def _generic_job_visible(job: dict) -> bool:
    """Mirror the generic endpoint's dedicated sample-campaign exclusion."""
    return job.get("kind") != "sample_campaign_generation"


class _PostDecodeStageError(RuntimeError):
    def __init__(self, message: str, *, stage: str, code: str):
        super().__init__(message)
        self.stage = stage
        self.code = code


class TestJobLifecycleWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = _parse("app/launch.py")

    def test_wgp_mmgp_target_matches_requirement_without_version_drift(self):
        wgp = _parse("app/wgp.py")
        assignments = {
            target.id: node.value.value
            for node in wgp.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance((target := node.targets[0]), ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        with open(
            os.path.join(_ROOT, "app", "requirements.txt"),
            "r",
            encoding="utf-8",
        ) as handle:
            requirement = next(
                line.strip()
                for line in handle
                if line.strip().startswith("mmgp @ ")
            )
        expected_hash = (
            "2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf"
        )
        self.assertIn(
            f"mmgp-{assignments['target_mmgp_version']}-py3-none-any.whl",
            requirement,
        )
        self.assertTrue(requirement.startswith("mmgp @ https://files.pythonhosted.org/"))
        self.assertTrue(requirement.endswith(f"#sha256={expected_hash}"))
        self.assertEqual(assignments["WanGP_version"], "10.9875")
        self.assertEqual(assignments["settings_version"], 2.57)

    def test_each_worker_uses_lifecycle_transitions(self):
        expected = {
            "_run_generation": {
                "try_start", "register_abort_state", "finish_job",
                "record_job_outputs",
            },
            "_run_recast": {"try_start", "register_abort_state", "try_requeue"},
            "_run_tool_upscale": {
                "try_start", "register_abort_state", "finish_job",
                "record_job_outputs",
            },
            "_run_tool_revoice": {"try_start", "register_abort_state", "finish_job"},
            "_run_blend_generation": {
                "register_abort_state", "finish_job", "record_job_outputs",
            },
            "_run_sfx_generation": {
                "finish_job", "record_job_outputs",
            },
        }
        for function_name, required in expected.items():
            with self.subTest(function=function_name):
                calls = _called_names(_function(self.launch, function_name))
                self.assertTrue(required <= calls, required - calls)

    def test_cancel_endpoint_routes_through_shared_helper(self):
        cancel = _function(self.launch, "cancel_job")
        self.assertIn("request_cancel", _called_names(cancel))
        self.assertFalse(any(
            isinstance(node, ast.Attribute) and node.attr == "_interrupt"
            for node in ast.walk(cancel)
        ))

    def test_studio_queue_uses_continuum_queue_held(self):
        generate = _function(self.launch, "generate")
        generate_constants = {
            node.value
            for node in ast.walk(generate)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("_queue_mode", generate_constants)
        self.assertIn("held", generate_constants)
        self.assertIn("queue_held", generate_constants)
        self.assertIn("Ready - waiting for Start Queue", generate_constants)
        mint = _function(self.launch, "_new_generation_job_id")
        self.assertTrue(any(
            isinstance(node, ast.Attribute) and node.attr == "hex"
            for node in ast.walk(mint)
        ))
        self.assertTrue(any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id == "hold_for_queue"
            and any(
                isinstance(child, ast.Name)
                and child.id == "_run_generation"
                for child in ast.walk(node)
            )
            for node in ast.walk(generate)
        ))

        launch_names = {
            node.name
            for node in ast.walk(self.launch)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("_start_held_studio_queue", launch_names)
        self.assertNotIn("_run_held_studio_jobs", launch_names)
        self.assertNotIn("release_held", launch_names)

        endpoint = _function(self.launch, "start_studio_queue")
        self.assertIn("set_job_hold", _called_names(endpoint))
        self.assertNotIn("release_held", _called_names(endpoint))
        self.assertNotIn("_start_held_studio_queue", _called_names(endpoint))

        get_queue = _function(self.launch, "get_queue_state")
        queue_constants = {
            node.value
            for node in ast.walk(get_queue)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("held", queue_constants)
        self.assertIn("queue_held", queue_constants)

    def test_studio_queue_release_clears_queue_held_via_set_job_hold(self):
        jobs = {
            "later": {"id": "later", "status": "queued", "queue_held": True, "created_at": 20},
            "active": {"id": "active", "status": "running", "queue_held": False, "created_at": 5},
            "earlier": {"id": "earlier", "status": "queued", "queue_held": True, "created_at": 10},
            "blank": {"id": "", "status": "queued", "queue_held": True},
        }
        released_ids = []

        def set_hold(job, held):
            if job.get("queue_held") is not True or held is not False:
                return None
            job["queue_held"] = False
            released_ids.append(job["id"])
            return "resumed"

        start_queue = _load_isolated_function(
            "app/launch.py",
            "start_studio_queue",
            {
                "api": SimpleNamespace(
                    post=lambda *_args, **_kwargs: (lambda function: function),
                ),
                "Request": object,
                "Response": object,
                "_jobs": jobs,
                "_set_recovery_no_store": lambda _response: None,
                "_require_remote_queue_project": lambda _request: None,
                "_require_generic_queue_control_job": lambda job_id, _request: jobs[job_id],
                "_queue_recovery_delivery_pending": lambda _job: None,
                "_require_job_runtime_model_admission": lambda _job: None,
                "set_job_hold": set_hold,
                "HTTPException": RuntimeError,
            },
        )

        result = start_queue(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(set(result["released"]), {"earlier", "later"})
        self.assertEqual(set(result["job_ids"]), {"earlier", "later"})
        self.assertFalse(jobs["earlier"]["queue_held"])
        self.assertFalse(jobs["later"]["queue_held"])
        self.assertTrue(jobs["active"]["queue_held"] is False)
        self.assertEqual(jobs["active"]["status"], "running")
        self.assertEqual(set(released_ids), {"earlier", "later"})

    def test_director_music_progress_validation_imports_regex_module(self):
        imported_modules = {
            alias.asname or alias.name.split(".", 1)[0]
            for node in self.launch.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        generate_music = _function(self.launch, "director_generate_music")
        uses_re_fullmatch = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fullmatch"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            for node in ast.walk(generate_music)
        )

        self.assertTrue(uses_re_fullmatch)
        self.assertIn("re", imported_modules)

    def test_director_dashboard_mutations_run_off_the_event_loop(self):
        expected = {
            "rerun_pipeline_clip_image": "rerun_clip_image",
            "rerun_pipeline_clip_video": "rerun_clip_video",
            "rejoin_pipeline_clips": "rejoin_clips",
        }
        for endpoint_name, worker_name in expected.items():
            with self.subTest(endpoint=endpoint_name):
                endpoint = _function(self.launch, endpoint_name)
                awaited_thread_targets = {
                    call.args[0].id
                    for node in ast.walk(endpoint)
                    if isinstance(node, ast.Await)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "to_thread"
                    for call in [node.value]
                    if call.args and isinstance(call.args[0], ast.Name)
                }
                self.assertIn(worker_name, awaited_thread_targets)

    def test_director_bulk_repair_routes_to_server_owned_worker(self):
        repair = _function(self.launch, "repair_saved_pipeline")
        cancel = _function(self.launch, "cancel_saved_pipeline_repair")
        self.assertIn("start_pipeline_repair", _called_names(repair))
        self.assertIn("cancel_pipeline_repair", _called_names(cancel))

    def test_blend_defers_generation_completion(self):
        blend = _function(self.launch, "_run_blend_generation")
        matching_calls = [
            node for node in ast.walk(blend)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_generation"
        ]
        self.assertEqual(len(matching_calls), 1)
        finalize = next(
            (kw.value for kw in matching_calls[0].keywords if kw.arg == "finalize"),
            None,
        )
        self.assertIsInstance(finalize, ast.Constant)
        self.assertIs(finalize.value, False)

    def test_wan_checks_abort_before_resetting_interrupt(self):
        wgp = _parse("app/wgp.py")
        generate = _function(wgp, "_generate_video_impl")
        self.assertIn(
            "_cleanup_generation_resources",
            {
                node.name for node in generate.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            },
        )
        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            source_lines = handle.read().splitlines()
        body = "\n".join(source_lines[generate.lineno - 1:generate.end_lineno])
        reset = body.index("wan_model._interrupt = False")
        before = body.rfind('if gen.get("abort", False):', 0, reset)
        after = body.find('if gen.get("abort", False):', reset)
        self.assertGreaterEqual(before, 0)
        self.assertGreater(after, reset)
        for check in (before, after):
            cleanup = body.find("_cleanup_generation_resources()", check)
            abort_return = body.find("return False", check)
            self.assertLess(check, cleanup)
            self.assertLess(cleanup, abort_return)

    def test_flashvsr_checks_cancel_before_replacing_source_video(self):
        upscale = _function(
            self.launch, "_apply_spatial_upsampling_to_file",
        )
        with open(
            os.path.join(_ROOT, "app", "launch.py"),
            "r", encoding="utf-8",
        ) as handle:
            launch_source = handle.read()
        source = ast.get_source_segment(launch_source, upscale)
        self.assertIsNotNone(source)
        for replacement in (
            "os.replace(tmp_muxed, video_path)",
            "os.replace(tmp_video, video_path)",
        ):
            replace_at = source.index(replacement)
            check_at = source.rfind("abort_check()", 0, replace_at)
            self.assertGreaterEqual(check_at, 0)

    def test_generation_stamps_partial_outputs_before_cancel_return(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            launch_source = handle.read()
        source = ast.get_source_segment(launch_source, generation)
        self.assertIsNotNone(source)
        publish_at = source.index("published_outputs = record_job_outputs(")
        stamp_at = source.index("_write_output_sidecars(new_files)", publish_at)
        cancel_at = source.index("if cancelled or is_cancel_requested(job):", stamp_at)
        cancel_return = source.index("return False", cancel_at)
        self.assertLess(publish_at, stamp_at)
        self.assertLess(stamp_at, cancel_at)
        self.assertLess(cancel_at, cancel_return)

    def test_ordinary_repeat_boundary_publishes_before_scheduler_yield(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            source = ast.get_source_segment(handle.read(), generation)
        self.assertIsNotNone(source)
        callback = source.split(
            "def _publish_and_yield_after_repeat_output():", 1,
        )[1].split("if not is_multiclip and not defer_output_publication:", 1)[0]
        collect_at = callback.index("collect_job_outputs(")
        sidecar_at = callback.index("_write_output_sidecars(", collect_at)
        record_at = callback.index("record_job_outputs(", sidecar_at)
        yield_at = callback.index(
            "yield_generation_slot_after_output(_gen_lock, job)", record_at,
        )
        no_next_at = callback.index(
            "if completed_repeats >= current_total:", record_at,
        )
        restore_path_at = callback.index("wgp.save_path = out_dir", yield_at)
        restore_coefficient_at = callback.index(
            "_apply_per_job_coefficient(job)", restore_path_at,
        )
        restore_abort_state_at = callback.index(
            "if not register_abort_state(", restore_coefficient_at,
        )
        unregister_at = callback.index("unregister_abort_state(", record_at)
        self.assertLess(collect_at, sidecar_at)
        self.assertLess(sidecar_at, record_at)
        self.assertLess(record_at, unregister_at)
        self.assertLess(unregister_at, no_next_at)
        self.assertLess(no_next_at, yield_at)
        self.assertLess(record_at, yield_at)
        self.assertLess(yield_at, restore_path_at)
        self.assertLess(restore_path_at, restore_coefficient_at)
        self.assertLess(restore_coefficient_at, restore_abort_state_at)
        self.assertIn(
            'params["after_repeat_output"] =',
            source,
        )
        self.assertIn(
            "if not is_multiclip and not defer_output_publication:",
            source,
        )

    def test_repeat_boundary_two_outputs_resume_cancel_and_extra_orders(self):
        import inspect

        namespace = {
            "inspect": inspect,
            "get_gen_info": lambda state: state["gen"],
        }
        dispatch = _load_isolated_function(
            "app/wgp.py", "generate_video", namespace,
        )
        signature = inspect.signature(dispatch)
        state = {"gen": {"abort": False, "extra_orders": 0}}
        calls = []
        yielded = []

        def one_output(**kwargs):
            calls.append(kwargs["seed"])
            return True

        one_output.__signature__ = signature
        namespace["generate_video"] = one_output

        def publish_and_maybe_yield():
            gen = state["gen"]
            if int(gen["repeat_no"]) < int(gen["total_generation"]):
                yielded.append(gen["repeat_no"])
            return True

        arguments = {
            name: None
            for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty
        }
        arguments.update({
            "task": {},
            "send_cmd": lambda *_args: None,
            "state": state,
            "seed": 41,
            "repeat_generation": 2,
            "after_repeat_output": publish_and_maybe_yield,
        })
        self.assertTrue(dispatch(**arguments))
        self.assertEqual(calls, [41, -1])
        self.assertEqual(yielded, [1])

        # A live delta retained by the one-output model task is consumed by the
        # outer dispatcher before it decides whether the request is complete.
        calls.clear()
        yielded.clear()
        state["gen"].update(abort=False, extra_orders=0)

        def add_one_output(**kwargs):
            calls.append(kwargs["seed"])
            if len(calls) == 1:
                state["gen"]["extra_orders"] = 1
            return True

        add_one_output.__signature__ = signature
        namespace["generate_video"] = add_one_output
        self.assertTrue(dispatch(**arguments))
        self.assertEqual(len(calls), 3)
        self.assertEqual(yielded, [1, 2])

        def cancel_at_boundary():
            return False

        namespace["generate_video"] = one_output
        state["gen"].update(abort=False, extra_orders=0)
        arguments["after_repeat_output"] = cancel_at_boundary
        self.assertFalse(dispatch(**arguments))
        self.assertTrue(state["gen"]["abort"])

        # Restart from two durably completed outputs: only repeat three is
        # dispatched, with the randomized-followup seed contract preserved.
        calls.clear()
        yielded.clear()
        state["gen"].update(abort=False, extra_orders=0)
        arguments.update({
            "repeat_generation": 3,
            "repeat_start_offset": 2,
            "after_repeat_output": publish_and_maybe_yield,
        })
        self.assertTrue(dispatch(**arguments))
        self.assertEqual(calls, [-1])
        self.assertEqual(yielded, [])
        self.assertEqual(state["gen"]["repeat_no"], 3)

    def test_repeat_boundary_one_output_dispatches_once(self):
        import inspect

        namespace = {
            "inspect": inspect,
            "get_gen_info": lambda state: state["gen"],
        }
        dispatch = _load_isolated_function(
            "app/wgp.py", "generate_video", namespace,
        )
        signature = inspect.signature(dispatch)
        state = {"gen": {"abort": False, "extra_orders": 0}}
        calls = []
        boundaries = []

        def one_output(**kwargs):
            calls.append(kwargs["seed"])
            return True

        one_output.__signature__ = signature
        namespace["generate_video"] = one_output

        def after_output():
            boundaries.append((
                state["gen"]["repeat_no"],
                state["gen"]["total_generation"],
            ))
            return True

        arguments = {
            name: None
            for name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty
        }
        arguments.update({
            "task": {},
            "send_cmd": lambda *_args: None,
            "state": state,
            "seed": 41,
            "repeat_generation": 1,
            "after_repeat_output": after_output,
        })
        self.assertTrue(dispatch(**arguments))
        self.assertEqual(calls, [41])
        self.assertEqual(boundaries, [(1, 1)])

    def test_inner_single_repeat_uses_a_stable_loop_target(self):
        wgp = _parse("app/wgp.py")
        generate = _function(wgp, "_generate_video_impl")
        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            source = ast.get_source_segment(handle.read(), generate)
        self.assertIsNotNone(source)
        offset_at = source.index("single_repeat_offset = (")
        local_index_at = source.index("repeat_no = 0", offset_at)
        target_at = source.index("single_repeat_target = 1", local_index_at)
        loop_at = source.index("while not abort:", target_at)
        use_at = source.index(
            "total_generation = single_repeat_target if single_repeat_dispatch",
            loop_at,
        )
        self.assertLess(offset_at, local_index_at)
        self.assertLess(local_index_at, target_at)
        self.assertLess(target_at, loop_at)
        self.assertLess(loop_at, use_at)
        self.assertNotIn(
            "total_generation = repeat_no + 1 if single_repeat_dispatch",
            source,
        )
        self.assertIn(
            "single_repeat_offset + repeat_no",
            source,
        )
        self.assertIn("if repeat_no == 1", source)

    def test_published_repeat_stays_visible_through_real_hold_resume_and_cancel(self):
        import sys
        import threading
        import time

        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        lifecycle._reset_queue_state_for_tests()
        generation_lock = threading.Lock()
        generation_lock.acquire()
        job = {"id": "repeat-resume", "status": "queued", "message": "Queued"}
        self.assertTrue(lifecycle.try_start(job))
        lifecycle.record_job_outputs(
            job,
            ["repeat-1.mp4"],
            final_output_files=["repeat-1.mp4"],
        )
        self.assertEqual(lifecycle.set_job_hold(job, True), "after_output")
        resume_result = []
        resume_thread = threading.Thread(target=lambda: resume_result.append(
            lifecycle.yield_generation_slot_after_output(
                generation_lock, job, poll_interval=0.005,
            )
        ))
        resume_thread.start()
        deadline = time.time() + 1
        while job.get("status") != "queued" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(
            lifecycle.snapshot_job(job).get("output_files"),
            ["repeat-1.mp4"],
        )
        self.assertEqual(lifecycle.set_job_hold(job, False), "resumed")
        resume_thread.join(timeout=1)
        self.assertEqual(resume_result, [True])
        generation_lock.release()

        lifecycle._reset_queue_state_for_tests()
        cancel_lock = threading.Lock()
        cancel_lock.acquire()
        cancelled = {
            "id": "repeat-cancel", "status": "queued", "message": "Queued",
        }
        self.assertTrue(lifecycle.try_start(cancelled))
        lifecycle.record_job_outputs(
            cancelled,
            ["repeat-before-cancel.mp4"],
            final_output_files=["repeat-before-cancel.mp4"],
        )
        self.assertEqual(
            lifecycle.set_job_hold(cancelled, True), "after_output",
        )
        cancel_result = []
        cancel_thread = threading.Thread(target=lambda: cancel_result.append(
            lifecycle.yield_generation_slot_after_output(
                cancel_lock, cancelled, poll_interval=0.005,
            )
        ))
        cancel_thread.start()
        deadline = time.time() + 1
        while cancelled.get("status") != "queued" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(
            lifecycle.snapshot_job(cancelled).get("output_files"),
            ["repeat-before-cancel.mp4"],
        )
        lifecycle.request_cancel(cancelled)
        cancel_thread.join(timeout=1)
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(cancel_result, [False])
        lifecycle._reset_queue_state_for_tests()

    def test_output_callbacks_are_optional_tail_keywords(self):
        wgp = _parse("app/wgp.py")
        generate = _function(wgp, "_generate_video_impl")
        public_tail_arguments = [
            argument
            for argument in generate.args.args
            if not argument.arg.startswith("_")
        ][-2:]
        self.assertEqual(
            [argument.arg for argument in public_tail_arguments],
            ["after_repeat_output", "after_segment_output"],
        )
        defaults_by_name = dict(zip(
            [argument.arg for argument in generate.args.args[-len(generate.args.defaults):]],
            generate.args.defaults,
        ))
        for name in ("after_repeat_output", "after_segment_output"):
            default = defaults_by_name[name]
            self.assertIsInstance(default, ast.Constant)
            self.assertIsNone(default.value)
        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            source = ast.get_source_segment(handle.read(), generate)
        self.assertIsNotNone(source)
        recursive_return = source.index(
            "if not generate_video(**recursive_arguments):",
        )
        boundary_at = source.index(
            "if after_repeat_output() is False:", recursive_return,
        )
        self.assertLess(recursive_return, boundary_at)

    def test_wgp_load_configuration_reuses_exact_key_and_reloads_on_budget_change(self):
        namespace = {"math": math}
        normalize = _load_isolated_function(
            "app/wgp.py", "_normalize_output_type", namespace,
        )
        namespace["_normalize_output_type"] = normalize
        configuration = _load_isolated_function(
            "app/wgp.py", "_model_load_configuration", namespace,
        )
        matches = _load_isolated_function(
            "app/wgp.py", "_model_load_configuration_matches", namespace,
        )
        namespace["_model_load_configuration_matches"] = matches
        reprofile = _load_isolated_function(
            "app/wgp.py", "_release_for_model_reprofile", namespace,
        )
        loaded = configuration(0.85, 4, "video", None, 0)
        self.assertTrue(matches(
            loaded, configuration(0.85, 4, "video", None, 0),
        ))
        self.assertTrue(matches(
            loaded, configuration(0.8500001, 4, "video", None, 0),
        ))
        self.assertFalse(matches(
            loaded, configuration(0.90, 4, "video", None, 0),
        ))
        self.assertTrue(matches(
            loaded, configuration(0.85, 4, "image", None, 0),
        ))
        self.assertFalse(matches(
            loaded, configuration(0.85, 3, "image", None, 0),
        ))
        self.assertFalse(matches(
            loaded, configuration(0.85, 4, "video", "vae2", 0),
        ))
        release = Mock()
        self.assertFalse(reprofile(
            object(), loaded, configuration(0.85, 4, "video", None, 0), release,
        ))
        release.assert_not_called()
        self.assertTrue(reprofile(
            object(), loaded, configuration(0.90, 4, "video", None, 0), release,
        ))
        release.assert_called_once_with()

    def test_submission_coefficient_override_matches_post_apply_residency_key(self):
        namespace = {
            "args": SimpleNamespace(vram_safety_coefficient=0.80, gpu=""),
            "compute_profile": lambda override, _output: override,
            "_model_load_configuration": lambda coefficient, profile, _output, vae, config, environment=None: (
                float(coefficient), profile, vae, config, environment,
            ),
            "_model_load_environment_signature": lambda model, profile: {
                "model": model, "profile": profile,
            },
            "make_residency_key": lambda *parts: repr(parts),
            "get_base_model_type": lambda model: f"base:{model}",
            "transformer_quantization": "fp8",
            "transformer_dtype_policy": "default",
            "text_encoder_quantization": "int8",
            "attention_mode": "sdpa",
            "compile": "",
            "vae_config": 0,
            "server_config": {
                "vae_precision": "16",
                "mixed_precision": "0",
                "enhancer_mode": 1,
            },
        }
        identity = _load_isolated_function(
            "app/wgp.py", "get_requested_residency_identity", namespace,
        )
        for label, effective_coefficient in (
            ("base", 0.80),
            ("heavy", 0.67),
        ):
            with self.subTest(job=label):
                namespace["args"].vram_safety_coefficient = 0.80
                pre_admission = identity(
                    "minimax_h3",
                    override_profile=4,
                    output_type="video",
                    vram_safety_coefficient=effective_coefficient,
                )
                namespace["args"].vram_safety_coefficient = effective_coefficient
                post_apply = identity(
                    "minimax_h3",
                    override_profile=4,
                    output_type="video",
                )
                self.assertEqual(pre_admission, post_apply)

    def test_wgp_load_environment_tracks_compile_decoder_and_attached_enhancer(self):
        state = {
            "compile": "",
            "server_config": {
                "enhancer_mode": 0,
                "enhancer_enabled": 1,
                "prompt_enhancer_quantization": "quanto_int8",
                "lm_decoder_engine": "legacy",
            },
        }
        namespace = {
            "get_model_def": lambda _model: {"lm_engines": ["legacy", "vllm"]},
            "resolve_lm_decoder_engine": lambda requested, _allowed: requested,
            "lm_decoder_engine": "legacy",
            **state,
        }
        signature = _load_isolated_function(
            "app/wgp.py", "_model_load_environment_signature", namespace,
        )
        baseline = signature("minimax_h3", 4)
        namespace["compile"] = "transformer"
        compiled = signature("minimax_h3", 4)
        self.assertNotEqual(baseline, compiled)
        namespace["server_config"]["prompt_enhancer_quantization"] = "gguf"
        requantized = signature("minimax_h3", 4)
        self.assertNotEqual(compiled, requantized)
        namespace["lm_decoder_engine"] = "vllm"
        decoder_changed = signature("minimax_h3", 3)
        self.assertEqual(decoder_changed["lm_decoder_engine"], "vllm")
        self.assertNotEqual(requantized, decoder_changed)

    def test_generation_submission_stamps_base_only_residency_before_worker(self):
        registration = _function(
            self.launch, "_queue_recovery_register_and_publish",
        )
        worker = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            source = handle.read()
        generate_source = ast.get_source_segment(source, registration)
        worker_source = ast.get_source_segment(source, worker)
        self.assertIsNotNone(generate_source)
        self.assertIsNotNone(worker_source)
        stamp_at = generate_source.index("_stamp_requested_generation_residency(prepared)")
        thread_at = generate_source.index("thread = threading.Thread", stamp_at)
        self.assertLess(stamp_at, thread_at)
        worker_stamp_at = worker_source.index(
            "_stamp_requested_generation_residency(job, replace=True)",
        )
        self.assertLess(worker_stamp_at, worker_source.index("with generation_slot("))

        stamp_function = _function(
            self.launch, "_stamp_requested_generation_residency_locked",
        )
        stamp_calls = [
            node for node in ast.walk(stamp_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "stamp_job_residency"
        ]
        self.assertEqual(len(stamp_calls), 1)
        self.assertEqual(len(stamp_calls[0].args), 2)
        identity_calls = [
            node for node in ast.walk(stamp_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_requested_residency_identity"
        ]
        self.assertEqual(len(identity_calls), 1)
        affinity = next(
            keyword.value for keyword in identity_calls[0].keywords
            if keyword.arg == "affinity_components"
        )
        self.assertIsInstance(affinity, ast.Constant)
        self.assertIsNone(affinity.value)

    def test_internal_h3_residency_uses_non_mutating_adaptive_first_model(self):
        params = {
            "model_type": "minimax_h3",
            "image_refs": ["synthetic.png"],
        }

        def route(candidate):
            candidate["model_type"] = "minimax_h3_ref2va"

        def plan(candidate):
            candidate["_h3_longform"] = {
                "segment_models": [{"model_type": candidate["model_type"]}],
            }

        helper = _load_isolated_function(
            "app/launch.py",
            "_residency_request_params",
            {
                "_H3_LONG_STUDIO_MODELS": {
                    "minimax_h3", "minimax_h3_ref2va",
                },
                "_apply_h3_adaptive_checkpoint": route,
                "_prepare_h3_long_studio_request": plan,
            },
        )
        effective = helper(params)
        self.assertEqual(effective["model_type"], "minimax_h3_ref2va")
        self.assertNotIn("_h3_longform", params)
        self.assertEqual(params["model_type"], "minimax_h3")

    def test_load_configuration_mutations_restamp_queued_jobs(self):
        for function_name in ("update_system_config", "apply_system_detect"):
            with self.subTest(function=function_name):
                function = _function(self.launch, function_name)
                self.assertIn(
                    "_restamp_queued_generation_residency",
                    _called_names(function),
                )
                self.assertIn(
                    "residency_configuration_update",
                    _called_names(function),
                )

    def test_failed_replacement_clears_stale_residency_key(self):
        cleared = Mock(return_value=True)
        stamp = _load_isolated_function(
            "app/launch.py",
            "_stamp_requested_generation_residency_locked",
            {
                "clear_job_residency": cleared,
                "_residency_request_params": lambda _params: None,
            },
        )
        job = {
            "status": "queued",
            "residency_base_key": "r1:stale",
            "params": {"model_type": "minimax_h3"},
        }
        self.assertFalse(stamp(job, replace=True))
        cleared.assert_called_once_with(job)

    def test_public_queue_residency_metadata_never_exposes_opaque_keys(self):
        helper = _load_isolated_function(
            "app/launch.py", "_public_queue_residency_metadata", {},
        )
        public = helper({
            "queue_reorder_reason": "resident_base",
            "queue_residency_bypass_count": 999,
            "queue_residency_bypassed_waiters": 99_999,
            "residency_base_key": "r1:secret",
            "residency_affinity_key": "r1:also-secret",
        })
        self.assertEqual(public["queue_reorder_reason"], "resident_base")
        self.assertEqual(public["queue_residency_bypass_count"], 2)
        self.assertEqual(public["queue_residency_bypassed_waiters"], 10_000)
        self.assertFalse(any("key" in name for name in public))
        remote = helper({
            "queue_reorder_reason": "resident_base",
            "queue_residency_bypass_count": 2,
            "queue_residency_bypassed_waiters": 123,
        }, remote=True)
        self.assertEqual(remote["queue_residency_bypass_count"], 1)
        self.assertEqual(remote["queue_residency_bypassed_waiters"], 1)

    def test_queue_endpoint_counts_only_authorized_logical_rows(self):
        import sys
        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        owner_job = {
            "id": "owner-job", "status": "queued", "session_id": "owner",
            "queue_priority": 0, "output_files": [],
            "resource_intent": "text",
            "logical_job_kind": "reference_pack_parent",
            "params": {"reference_pack": {"schema_version": 2}},
        }
        child_job = {
            "id": "owner-child", "status": "queued", "session_id": "owner",
            "queue_priority": 0, "output_files": [],
            "resource_intent": "generation",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": "owner-job",
        }
        other_job = {
            "id": "other-job", "status": "running", "session_id": "other",
            "queue_priority": 0, "output_files": [],
        }
        states = {
            id(owner_job): dict(owner_job),
            id(child_job): dict(child_job),
            id(other_job): dict(other_job),
        }
        summary = {
            "running": 1, "waiting": 1, "held": 0, "registering": 0,
            "active_total": 2,
        }
        scheduler = {
            "paused": False,
            "pause_after_current": False,
            "summary": summary,
            # Global ordinal gaps must be renumbered after owner fencing.
            "positions": {id(other_job): 1, id(child_job): 8},
            "states": states,
            "wait_reasons": {
                id(owner_job): "waiting_for_other_user",
                id(child_job): "waiting_for_other_user",
                id(other_job): "running",
            },
        }
        call_order = []
        require_remote_project = Mock(
            side_effect=lambda _request: call_order.append("gate"),
        )
        scheduler_snapshot = Mock(side_effect=lambda _jobs: (
            call_order.append("snapshot") or scheduler
        ))
        fake_api = SimpleNamespace(get=lambda *_args, **_kwargs: lambda function: function)
        endpoint = _load_isolated_function(
            "app/launch.py", "get_queue_state",
            {
                "api": fake_api,
                "Request": object,
                "Response": object,
                "_jobs": {
                    "owner-job": owner_job,
                    "owner-child": child_job,
                    "other-job": other_job,
                },
                "_generic_job_visible": _generic_job_visible,
                "_require_remote_queue_project": require_remote_project,
                "queue_scheduler_snapshot": scheduler_snapshot,
                "_job_owned_by_request": lambda job, request: (
                    job.get("session_id") == request.state.maestro_session_id
                ),
                "_job_eta_values": lambda _job: (None, None),
                "_queue_wait_reason_for_job": lambda _job: (
                    "waiting_for_other_user"
                ),
                "_queue_recovery_is_blocked": lambda _job: False,
                "_public_queue_residency_metadata": lambda *_args, **_kwargs: {},
                "_public_queue_recovery_metadata": lambda *_args, **_kwargs: {},
                "_public_resource_metadata": lambda *_args, **_kwargs: {},
                "_public_parent_job_id": lambda _job: None,
                "_public_logical_job_kind": lambda job: job.get(
                    "logical_job_kind"
                ),
                "authorized_logical_queue_projection": (
                    lifecycle.authorized_logical_queue_projection
                ),
                "_set_recovery_no_store": lambda response: (
                    response.headers.update({
                        "Cache-Control": "private, no-store",
                    })
                ),
            },
        )
        response_headers = SimpleNamespace(headers={})
        response = endpoint(
            SimpleNamespace(state=SimpleNamespace(
                maestro_session_id="owner", maestro_remote=True,
            )),
            response_headers,
        )

        self.assertEqual(call_order, ["gate", "snapshot"])
        require_remote_project.assert_called_once()
        self.assertEqual(response["summary"], {
            "running": 0,
            "waiting": 1,
            "held": 0,
            "registering": 0,
            "preparing": 0,
            "approval_waiting": 0,
            "active_total": 1,
            "cpu_text_running": 0,
            "cpu_text_waiting": 1,
        })
        self.assertEqual(response["summary"]["active_total"], sum(
            response["summary"][name]
            for name in ("running", "waiting", "held", "registering")
        ))
        self.assertEqual(
            {job["job_id"] for job in response["jobs"]},
            {"owner-job", "owner-child"},
        )
        self.assertEqual(response["total"], 1)
        self.assertEqual(
            response_headers.headers["Cache-Control"], "private, no-store",
        )
        child_row = next(
            item for item in response["jobs"]
            if item["job_id"] == "owner-child"
        )
        self.assertEqual(child_row["position"], 1)
        self.assertGreaterEqual(
            response["summary"]["waiting"],
            max(job["position"] for job in response["jobs"] if job["position"]),
        )

    def test_queue_endpoint_denial_happens_before_global_snapshot(self):
        fake_api = SimpleNamespace(
            get=lambda *_args, **_kwargs: lambda function: function,
        )
        snapshot = Mock()
        endpoint = _load_isolated_function(
            "app/launch.py", "get_queue_state",
            {
                "api": fake_api,
                "Request": object,
                "Response": object,
                "_set_recovery_no_store": lambda _response: None,
                "_require_remote_queue_project": Mock(
                    side_effect=PermissionError("locked"),
                ),
                "_jobs": {},
                "queue_scheduler_snapshot": snapshot,
            },
        )
        with self.assertRaises(PermissionError):
            endpoint(
                SimpleNamespace(state=SimpleNamespace(
                    maestro_session_id="owner", maestro_remote=True,
                )),
                SimpleNamespace(headers={}),
            )
        snapshot.assert_not_called()

    def test_jobs_endpoint_folds_children_before_count_and_pagination(self):
        import sys
        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        def make_job(job_id, status, created_at, **updates):
            value = {
                "id": job_id, "status": status, "created_at": created_at,
                "session_id": "owner", "progress": 0,
                "message": status, "output_files": [], "error": None,
                "params": {},
            }
            value.update(updates)
            return value

        parent = make_job(
            "reference-parent", "running", 1,
            logical_job_kind="reference_pack_parent",
            resource_intent="text",
            params={"reference_pack": {"schema_version": 2}},
        )
        live_child = make_job(
            "reference-live-child", "queued", 2,
            logical_job_kind="reference_pack_child",
            resource_intent="generation", parent_job_id=parent["id"],
        )
        cancelled_child = make_job(
            "reference-cancelled-child", "cancelled", 3,
            logical_job_kind="reference_pack_child",
            resource_intent="generation", parent_job_id=parent["id"],
        )
        blocked_child = make_job(
            "reference-blocked-child", "queued", 4,
            logical_job_kind="reference_pack_child",
            resource_intent="generation", resource_state="blocked",
            parent_job_id=parent["id"],
        )
        orphan = make_job(
            "reference-orphan", "cancelled", 5,
            logical_job_kind="reference_pack_child",
            resource_intent="generation", parent_job_id="missing-parent",
        )
        unauthorized = make_job(
            "other-session-job", "running", 6, session_id="other",
        )
        physical = [
            parent, live_child, cancelled_child, blocked_child, orphan,
            unauthorized,
        ]
        scheduler = {
            "states": {id(item): dict(item) for item in physical},
            "positions": {id(unauthorized): 1, id(live_child): 8},
        }
        fake_api = SimpleNamespace(
            get=lambda *_args, **_kwargs: lambda function: function,
        )
        endpoint = _load_isolated_function(
            "app/launch.py", "list_jobs",
            {
                "api": fake_api, "Request": object, "Response": object,
                "HTTPException": RuntimeError, "math": math,
                "_jobs": {item["id"]: item for item in physical},
                "_generic_job_visible": _generic_job_visible,
                "_set_recovery_no_store": lambda _response: None,
                "queue_scheduler_snapshot": lambda _jobs: scheduler,
                "_job_owned_by_request": lambda item, request: (
                    item.get("session_id") == request.state.maestro_session_id
                ),
                "authorized_logical_queue_projection": (
                    lifecycle.authorized_logical_queue_projection
                ),
                "snapshot_job": lambda item: dict(item),
                "_queue_recovery_is_blocked": lambda _item: False,
                "_job_eta_values": lambda _item: (None, None),
                "_public_job_prompt_fields": lambda _item: {
                    "prompt_preview": "", "active_window_prompt": "",
                },
                "_public_job_created_at": lambda item: float(item["created_at"]),
                "public_h3_offload_plan": lambda _value: None,
                "_public_h3_boundary": lambda _value: None,
                "queue_position": lambda _item: None,
                "_queue_wait_reason_for_job": lambda item: (
                    f"wait:{item['id']}"
                ),
                "_public_parent_job_id": lambda item: item.get("parent_job_id"),
                "_public_logical_job_kind": lambda item: item.get(
                    "logical_job_kind"
                ),
                "_public_failed_child_metadata": lambda *_args: {
                    "failed_child_job_id": None,
                    "failed_child_status": None,
                    "failed_child_reason": None,
                },
                "_public_resource_metadata": lambda _item: {},
                "_public_queue_residency_metadata": lambda *_args, **_kwargs: {},
                "_public_progress_telemetry": lambda _item: {},
                "_public_queue_recovery_metadata": lambda _item: {},
                "job_events": lambda *_args: [],
                "queue_control_state": lambda: {},
            },
        )
        result = endpoint(
            SimpleNamespace(state=SimpleNamespace(
                maestro_session_id="owner", maestro_remote=False,
            )),
            SimpleNamespace(headers={}),
            limit=2,
            offset=1,
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(
            [item["job_id"] for item in result["jobs"]],
            ["reference-blocked-child", "reference-orphan"],
        )
        self.assertEqual(result["summary"]["active_total"], 2)
        self.assertNotIn(
            "other-session-job",
            {item["job_id"] for item in result["jobs"]},
        )
        full = endpoint(
            SimpleNamespace(state=SimpleNamespace(
                maestro_session_id="owner", maestro_remote=False,
            )),
            SimpleNamespace(headers={}),
        )
        parent_row = next(
            item for item in full["jobs"]
            if item["job_id"] == parent["id"]
        )
        self.assertEqual(parent_row["queue_position"], 1)
        self.assertEqual(
            parent_row["queue_wait_reason"], "wait:reference-live-child",
        )
        self.assertNotIn(
            "reference-live-child",
            {item["job_id"] for item in full["jobs"]},
        )

    def test_authorized_reference_child_folds_into_one_logical_queue_root(self):
        import sys
        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        parent = {
            "id": "reference-parent", "status": "running",
            "session_id": "owner", "resource_intent": "text",
            "logical_job_kind": "reference_pack_parent",
            "params": {"reference_pack": {"schema_version": 2}},
        }
        child = {
            "id": "reference-child", "status": "queued",
            "session_id": "owner", "resource_intent": "generation",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": parent["id"], "queue_held": False,
        }
        scheduler = {
            "states": {id(parent): dict(parent), id(child): dict(child)},
            "positions": {id(child): 4},
            # This must never influence the owner-visible summary.
            "summary": {"running": 99, "waiting": 99, "active_total": 198},
        }
        projected = lifecycle.authorized_logical_queue_projection(
            [parent, child], scheduler,
        )
        self.assertEqual(
            [job["id"] for job in projected["logical_jobs"]],
            [parent["id"]],
        )
        self.assertEqual(
            projected["representative_job_ids"],
            {parent["id"]: child["id"]},
        )
        self.assertEqual(projected["summary"], {
            "running": 0, "waiting": 1, "held": 0, "registering": 0,
            "preparing": 0, "approval_waiting": 0, "active_total": 1,
        })

        child.update({"status": "cancelled", "cancel_requested": True})
        scheduler["states"][id(child)] = dict(child)
        cancelled = lifecycle.authorized_logical_queue_projection(
            [parent, child], scheduler,
        )
        self.assertEqual(cancelled["summary"]["active_total"], 1)
        self.assertEqual(cancelled["summary"]["running"], 1)
        self.assertEqual(cancelled["folded_child_ids"], {child["id"]})

    def test_authorized_queue_projection_retains_actionable_and_orphan_children(self):
        import sys
        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        parent = {
            "id": "reference-parent", "status": "running",
            "session_id": "owner", "resource_intent": "text",
            "logical_job_kind": "reference_pack_parent",
            "params": {"reference_pack": {"schema_version": 2}},
        }
        actionable = {
            "id": "reference-child", "status": "queued",
            "session_id": "owner", "resource_intent": "generation",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": parent["id"], "resource_state": "blocked",
            "recovery_state": "blocked_preparation", "queue_held": True,
        }
        orphan = {
            "id": "orphan-child", "status": "queued",
            "session_id": "owner", "resource_intent": "generation",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": "missing-parent", "queue_held": False,
        }
        other_session = {
            "id": "other-session-job", "status": "running",
            "session_id": "other", "resource_intent": "generation",
        }
        authorized = [parent, actionable, orphan]
        scheduler = {
            "states": {
                id(job): dict(job)
                for job in (*authorized, other_session)
            },
            "positions": {id(orphan): 8},
            "summary": {"running": 2, "waiting": 8, "active_total": 10},
        }
        projected = lifecycle.authorized_logical_queue_projection(
            authorized, scheduler,
        )
        self.assertEqual(
            [job["id"] for job in projected["logical_jobs"]],
            [parent["id"], actionable["id"], orphan["id"]],
        )
        self.assertEqual(projected["folded_child_ids"], set())
        self.assertEqual(projected["summary"], {
            "running": 1, "waiting": 1, "held": 1, "registering": 0,
            "preparing": 0, "approval_waiting": 0, "active_total": 3,
        })

    def test_logical_projection_never_infers_reference_from_text_or_names(self):
        import sys
        app_dir = os.path.join(_ROOT, "app")
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from services import job_lifecycle as lifecycle

        parent = {
            "id": "reference-looking-parent", "status": "running",
            "session_id": "owner", "resource_intent": "text",
            "message": "Reference pack planning",
            "params": {"reference_pack": {"schema_version": 2}},
        }
        child = {
            "id": "reference-looking-child", "status": "queued",
            "session_id": "owner", "resource_intent": "generation",
            "parent_job_id": parent["id"], "message": "Reference child",
        }
        scheduler = {
            "states": {id(parent): dict(parent), id(child): dict(child)},
            "positions": {id(child): 1},
        }
        projection = lifecycle.authorized_logical_queue_projection(
            [parent, child], scheduler,
        )
        self.assertEqual(
            [job["id"] for job in projection["logical_jobs"]],
            [parent["id"], child["id"]],
        )
        self.assertEqual(projection["summary"]["active_total"], 2)

    def test_queue_ui_explains_residency_reordering_without_keys(self):
        paths = {
            "client": "ui/src/api/client.ts",
            "types": "ui/src/types/index.ts",
            "main": "ui/src/components/MainContent/MainContent.tsx",
        }
        source = {}
        for name, path in paths.items():
            with open(os.path.join(_ROOT, path), "r", encoding="utf-8") as handle:
                source[name] = handle.read()
        for field in (
            "queue_reorder_reason",
            "queue_residency_bypass_count",
            "queue_residency_bypassed_waiters",
        ):
            self.assertIn(field, source["client"])
        self.assertIn("QueueReorderReason", source["client"])
        self.assertIn("Started sooner by reusing the loaded model", source["main"])
        self.assertIn("Waiting for another generation on this host", source["main"])
        for field in ("running", "waiting", "held", "registering", "active_total"):
            self.assertIn(f"{field}: number", source["client"])
        self.assertIn("queueSummaryLabel(projection.summary)", source["main"])
        self.assertIn("Next in line", source["main"])
        self.assertIn("ahead · ${position} of ${waiting}", source["main"])
        self.assertIn("logicalQueue.activeCount", source["main"])
        self.assertIn("queuePollSequence", source["main"])
        self.assertIn("queuePollAbort.current?.abort()", source["main"])
        self.assertNotIn("setQueueTabState(null)", source["main"])
        self.assertIn("reduceQueueTabSnapshot(current, {", source["main"])
        self.assertIn("kind: 'failure'", source["main"])
        self.assertIn("return { ...current, error: outcome.error }", source["main"])
        self.assertIn("jobs: useStore.getState().jobs", source["main"])
        self.assertIn("jobs={queueDisplayJobs}", source["main"])
        self.assertEqual(source["main"].count("api.fetchQueueState("), 1)
        self.assertIn("fetchQueueState(signal?: AbortSignal)", source["client"])
        self.assertIn("Your job: overall ETA", source["main"])
        self.assertNotIn("queueReorderReason", source["types"])
        self.assertNotIn("residency_base_key", source["client"])
        self.assertNotIn("residency_affinity_key", source["client"])

    def test_wgp_failure_invalidation_clears_loaded_identity_before_cleanup(self):
        invalidate_scheduler = Mock()
        namespace = {
            "reload_needed": False,
            "_loaded_model_configuration": (0.85, 4, None, 0),
            "_loaded_residency_base_key": "base",
            "_loaded_residency_affinity_key": "affinity",
            "invalidate_residency_state": invalidate_scheduler,
        }
        invalidate = _load_isolated_function(
            "app/wgp.py", "_invalidate_loaded_model_state", namespace,
        )
        invalidate()
        self.assertTrue(namespace["reload_needed"])
        self.assertIsNone(namespace["_loaded_model_configuration"])
        self.assertIsNone(namespace["_loaded_residency_base_key"])
        self.assertIsNone(namespace["_loaded_residency_affinity_key"])
        invalidate_scheduler.assert_called_once_with()

    def test_wgp_reuse_gate_tracks_and_invalidates_effective_offload_configuration(self):
        wgp = _parse("app/wgp.py")
        generate = _function(wgp, "_generate_video_impl")
        load_models = _function(wgp, "load_models")
        release_model = _function(wgp, "release_model")
        invalidate_loaded = _function(wgp, "_invalidate_loaded_model_state")
        current_identity = _function(wgp, "get_current_residency_identity")
        self.assertIn("_release_for_model_reprofile", _called_names(generate))
        self.assertIn("release_model", _called_names(generate))
        self.assertIn("note_residency_state", _called_names(load_models))
        self.assertIn("_invalidate_loaded_model_state", _called_names(load_models))
        self.assertIn("_invalidate_loaded_model_state", _called_names(release_model))
        self.assertIn(
            "invalidate_residency_state", _called_names(invalidate_loaded),
        )

        assigned_in_load = {
            node.id for node in ast.walk(load_models)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        self.assertIn("_loaded_model_configuration", assigned_in_load)
        self.assertIn("_loaded_residency_base_key", assigned_in_load)

        assigned_in_invalidation = {
            node.id for node in ast.walk(invalidate_loaded)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        self.assertTrue({
            "_loaded_model_configuration",
            "_loaded_residency_base_key",
            "_loaded_residency_affinity_key",
        } <= assigned_in_invalidation)
        self.assertIn("wan_model", {
            node.id for node in ast.walk(current_identity)
            if isinstance(node, ast.Name)
        })
        self.assertIn("reload_needed", {
            node.id for node in ast.walk(current_identity)
            if isinstance(node, ast.Name)
        })

    def test_director_sidecars_cover_every_supported_media_extension(self):
        generation = _function(self.launch, "_run_generation")
        sidecar_writer = next(
            node for node in ast.walk(generation)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_write_output_sidecars"
        )
        referenced_names = {
            node.id for node in ast.walk(sidecar_writer)
            if isinstance(node, ast.Name)
        }
        self.assertIn("GENERATED_MEDIA_EXTENSIONS", referenced_names)
        self.assertIn("output_filename", {
            node.value for node in ast.walk(sidecar_writer)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        })

    def test_gallery_sidecars_use_active_generation_time(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            launch_source = handle.read()
        source = ast.get_source_segment(launch_source, generation)
        self.assertIsNotNone(source)
        # Leftover 1.9.0 gallery cmd / active-seconds helpers were never restored.
        self.assertNotIn('cmd == "generation_time"', source)
        self.assertNotIn("active_generation_seconds_by_output", source)
        self.assertNotIn('"job_elapsed_time":', source)
        self.assertIn(
            '"generation_time": round(time.time() - start_time)',
            source,
        )

        generate_video = _function(_parse("app/wgp.py"), "_generate_video_impl")
        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            wgp_source = handle.read()
        wgp_body = ast.get_source_segment(wgp_source, generate_video)
        self.assertIsNotNone(wgp_body)
        self.assertIn('configs["generation_time"] = round(end_time-start_time)', wgp_body)
        self.assertNotIn('configs["generation_time_basis"] = "active"', wgp_body)

    def test_generation_duration_is_minutes_and_seconds(self):
        formatter = _load_isolated_function(
            "app/wgp.py",
            "format_generation_time",
            {},
        )
        self.assertEqual(formatter(128), "128s (2m 8s)")
        self.assertEqual(formatter(8), "8s")
        self.assertEqual(formatter(3601), "3601s (1h 0m 1s)")

    def test_continuation_accepts_all_generated_video_containers(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            launch_source = handle.read()
        source = ast.get_source_segment(launch_source, generation)
        continuation = source.split(
            "# Find the latest video explicitly registered by", 1,
        )[1].split("if latest_video:", 1)[0]
        for extension in (".mp4", ".webm", ".mkv", ".mov"):
            self.assertIn(extension, continuation)

    def test_wgp_structures_vae_failure_with_exact_safe_progress(self):
        structured = _load_isolated_function(
            "app/wgp.py",
            "structured_generation_failure",
            {"get_gen_info": lambda state: state},
        )

        def build(exception, **values):
            return {"exception": type(exception).__name__, **values}

        fake_oom = SimpleNamespace(
            build_failure_details=build,
            safe_allocator_facts=lambda: {"cuda_allocated_bytes": 64},
        )
        with patch.dict("sys.modules", {"services.oom_detect": fake_oom}):
            details = structured(
                RuntimeError("private device detail"),
                {"params": {"multi_clip_info": {
                    "index": 13,
                    "total": 14,
                    "output_index": 0,
                }}},
                {
                    "progress_phase": ("VAE Decoding", 19),
                    "window_no": 4,
                    "total_windows": 4,
                    "num_inference_steps": 19,
                },
            )
        self.assertEqual(details["stage"], "vae_decode")
        self.assertEqual(details["segment"], {
            "current": 14, "total": 14, "variant": 1,
        })

        reference_failure = structured(
            RuntimeError("private reference detail"),
            {"params": {}},
            {"progress_phase": ("Encoding H3 references", 0)},
        )
        self.assertEqual(reference_failure["stage"], "denoise")
        self.assertEqual(
            reference_failure["code"], "h3_reference_encode_failed",
        )

        audio_failure = structured(
            RuntimeError("private audio detail"),
            {"params": {}},
            {"progress_phase": ("Decoding H3 audio", 0)},
        )
        self.assertEqual(audio_failure["stage"], "vae_decode")
        self.assertEqual(audio_failure["code"], "h3_audio_decode_failed")
        self.assertEqual(details["window"], {"current": 4, "total": 4})
        self.assertEqual(details["step"], {"current": 19, "total": 19})
        self.assertEqual(details["exception"], "RuntimeError")

        staged_error = _PostDecodeStageError(
            "private ffmpeg detail", stage="audio_mux", code="audio_mux_timeout",
        )
        with patch.dict("sys.modules", {"services.oom_detect": fake_oom}):
            staged = structured(
                staged_error,
                {"params": {}},
                {"progress_phase": ("VAE Decoding", 19)},
            )
        self.assertEqual(staged["stage"], "audio_mux")
        self.assertEqual(staged["code"], "audio_mux_timeout")

    def test_wgp_marks_segment_encode_and_audio_mux_boundaries(self):
        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            source = handle.read()
        generation = ast.get_source_segment(
            source, _function(ast.parse(source), "_generate_video_impl"),
        )
        self.assertGreaterEqual(
            generation.count('stage="segment_checkpoint"'), 2,
        )
        self.assertGreaterEqual(
            generation.count('code="segment_encode_failed"'), 2,
        )
        self.assertIn('stage="audio_mux"', generation)
        self.assertIn('code="audio_mux_failed"', generation)

    def test_launch_prefers_explicit_postdecode_stage_over_stale_vae_phase(self):
        tree = _parse("app/launch.py")
        names = {
            "_job_failure_positions",
            "_failure_stage_from_job",
            "_safe_failure_updates",
        }
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        namespace = {"wgp": SimpleNamespace(server_config={})}
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, "app/launch.py", "exec"), namespace)
        error = _PostDecodeStageError(
            "/private/path ffmpeg stderr",
            stage="concat",
            code="concat_process_failed",
        )
        updates = namespace["_safe_failure_updates"](
            error,
            {
                "phase": "VAE Decoding 19/19",
                "clip_current": 14,
                "clip_total": 14,
            },
        )
        details = updates["failure_details"]
        self.assertEqual(details["stage"], "concat")
        self.assertEqual(details["code"], "concat_process_failed")
        self.assertFalse(details["is_oom"])
        self.assertNotIn("/private/path", str(details))
        self.assertNotIn("ffmpeg stderr", str(details))
        self.assertNotIn("VRAM", details["detail"])

    def test_failed_multiclip_concat_removes_partial_output(self):
        concatenate = _load_isolated_function(
            "app/wgp.py",
            "concatenate_multi_clip_videos",
            {"os": os, "PostDecodeStageError": _PostDecodeStageError},
        )
        with tempfile.TemporaryDirectory() as directory:
            clip = os.path.join(directory, "clip.mp4")
            output = os.path.join(directory, "joined.mp4")
            with open(clip, "wb") as handle:
                handle.write(b"clip")

            def fake_run(command, **kwargs):
                if "-filter_complex" in command:
                    with open(output, "wb") as handle:
                        handle.write(b"partial")
                    return SimpleNamespace(
                        returncode=1, stdout="", stderr="ffmpeg error",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                with self.assertRaises(_PostDecodeStageError) as caught:
                    concatenate([clip], output)
            self.assertEqual(caught.exception.stage, "concat")
            self.assertEqual(caught.exception.code, "concat_process_failed")
            self.assertNotIn(directory, str(caught.exception))
            self.assertFalse(os.path.exists(output))

    def test_multiclip_concat_rejects_a_partially_missing_component_set(self):
        concatenate = _load_isolated_function(
            "app/wgp.py",
            "concatenate_multi_clip_videos",
            {"os": os, "PostDecodeStageError": _PostDecodeStageError},
        )
        with tempfile.TemporaryDirectory() as directory:
            present = os.path.join(directory, "present.mp4")
            missing = os.path.join(directory, "missing.mp4")
            output = os.path.join(directory, "joined.mp4")
            with open(present, "wb") as handle:
                handle.write(b"component")
            with patch("subprocess.run") as run:
                with self.assertRaises(_PostDecodeStageError) as caught:
                    concatenate([present, missing], output)
            self.assertEqual(caught.exception.stage, "concat")
            self.assertEqual(caught.exception.code, "concat_input_incomplete")
            run.assert_not_called()
            self.assertFalse(os.path.exists(output))

    def test_final_segment_callback_runs_before_concat_and_is_stage_safe(self):
        seal = _load_isolated_function(
            "app/wgp.py",
            "seal_multi_clip_segment_before_concat",
            {"PostDecodeStageError": _PostDecodeStageError},
        )
        calls = []
        callback = lambda path, info: (
            calls.append((path, info["index"])) or "sealed.mp4"
        )
        self.assertEqual(seal(
            "segment-1.mp4", {"index": 0, "total": 2}, callback,
        ), "segment-1.mp4")
        self.assertEqual(calls, [])
        self.assertEqual(seal(
            "segment-2.mp4", {"index": 1, "total": 2}, callback,
        ), "sealed.mp4")
        self.assertEqual(calls, [("segment-2.mp4", 1)])

        def fail(_path, _info):
            raise OSError("private checkpoint path")

        with self.assertRaises(_PostDecodeStageError) as caught:
            seal("segment-2.mp4", {"index": 1, "total": 2}, fail)
        self.assertEqual(caught.exception.stage, "segment_checkpoint")
        self.assertEqual(caught.exception.code, "segment_checkpoint_failed")
        self.assertNotIn("private checkpoint path", str(caught.exception))

        with open(
            os.path.join(_ROOT, "app", "wgp.py"), "r", encoding="utf-8",
        ) as handle:
            source = handle.read()
        generation = ast.get_source_segment(
            source, _function(ast.parse(source), "_generate_video_impl"),
        )
        self.assertLess(
            generation.index("seal_multi_clip_segment_before_concat("),
            generation.index("concatenate_multi_clip_videos("),
        )

    def test_multiclip_external_audio_can_start_after_source_time_zero(self):
        concatenate = _load_isolated_function(
            "app/wgp.py",
            "concatenate_multi_clip_videos",
            {"os": os, "PostDecodeStageError": _PostDecodeStageError},
        )
        with tempfile.TemporaryDirectory() as directory:
            clip = os.path.join(directory, "clip.mp4")
            audio = os.path.join(directory, "song.wav")
            output = os.path.join(directory, "joined.mp4")
            for path in (clip, audio):
                with open(path, "wb") as handle:
                    handle.write(b"media")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if "-filter_complex" in command:
                    with open(output, "wb") as handle:
                        handle.write(b"joined")
                stdout = "25/1\n" if "stream=r_frame_rate" in command else ""
                return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                self.assertTrue(concatenate(
                    [clip], output, audio, audio_start_sec=2.0,
                ))

            command = next(c for c in commands if "-filter_complex" in c)
            filter_value = command[command.index("-filter_complex") + 1]
            self.assertIn(
                "[1:a]atrim=start=2.000000,asetpts=PTS-STARTPTS[outa]",
                filter_value,
            )
            self.assertIn("[outa]", command)

    def test_multiclip_concat_can_be_cancelled_during_ffmpeg(self):
        concatenate = _load_isolated_function(
            "app/wgp.py",
            "concatenate_multi_clip_videos",
            {"os": os, "PostDecodeStageError": _PostDecodeStageError},
        )
        with tempfile.TemporaryDirectory() as directory:
            clip = os.path.join(directory, "clip.mp4")
            output = os.path.join(directory, "joined.mp4")
            with open(clip, "wb") as handle:
                handle.write(b"clip")

            class FakeProcess:
                def __init__(self, *_args, **_kwargs):
                    self.returncode = None
                    self.finished = False
                    with open(output, "wb") as handle:
                        handle.write(b"partial")

                def communicate(self, timeout=None):
                    if not self.finished:
                        raise subprocess.TimeoutExpired("ffmpeg", timeout)
                    return "", ""

                def terminate(self):
                    self.finished = True
                    self.returncode = -15

                def kill(self):
                    self.finished = True
                    self.returncode = -9

                def poll(self):
                    return self.returncode

            probe = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("subprocess.run", return_value=probe):
                with patch("subprocess.Popen", FakeProcess):
                    self.assertFalse(concatenate(
                        [clip],
                        output,
                        abort_callback=lambda: True,
                    ))
            self.assertFalse(os.path.exists(output))

    def test_multiclip_can_pad_short_audio_to_exact_video_timeline(self):
        concatenate = _load_isolated_function(
            "app/wgp.py",
            "concatenate_multi_clip_videos",
            {"os": os, "PostDecodeStageError": _PostDecodeStageError},
        )
        with tempfile.TemporaryDirectory() as directory:
            clip = os.path.join(directory, "clip.mp4")
            audio = os.path.join(directory, "source.mp4")
            output = os.path.join(directory, "joined.mp4")
            for path in (clip, audio):
                with open(path, "wb") as handle:
                    handle.write(b"media")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if "-filter_complex" in command:
                    with open(output, "wb") as handle:
                        handle.write(b"joined")
                stdout = "30/1\n" if "stream=r_frame_rate" in command else ""
                return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

            with patch("subprocess.run", side_effect=fake_run):
                self.assertTrue(concatenate(
                    [clip],
                    output,
                    audio,
                    pad_audio=True,
                    audio_duration_sec=1.25,
                ))

            command = next(c for c in commands if "-filter_complex" in c)
            filter_value = command[command.index("-filter_complex") + 1]
            self.assertIn(
                "[1:a]asetpts=PTS-STARTPTS,apad,"
                "atrim=duration=1.250000[outa]",
                filter_value,
            )
            self.assertIn("[outa]", command)
            self.assertIn("-shortest", command)

    def test_multiclip_dispatch_preserves_audio_origin(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            source = ast.get_source_segment(handle.read(), generation)
        self.assertIn('raw_params.get("audio_frame_offset", 0)', source)
        self.assertGreaterEqual(source.count(
            '"audio_start_sec": multi_clip_audio_start_sec'
        ), 2)

    def test_director_multiclip_dispatch_uses_explicit_prompt_modes(self):
        generation = _function(self.launch, "_run_generation")
        with open(
            os.path.join(_ROOT, "app", "launch.py"), "r", encoding="utf-8",
        ) as handle:
            source = ast.get_source_segment(handle.read(), generation)
        self.assertIn('raw_params.pop(\n                    "per_clip_prompt_modes"', source)
        self.assertIn("explicit_prompt_mode", source)
        self.assertIn(
            'else (1 if "\\n" in clip_prompt else 0)',
            source,
        )

    def test_failed_audio_mux_removes_partial_output(self):
        combine = _load_isolated_function(
            "app/shared/utils/audio_video.py",
            "combine_and_concatenate_video_with_audio_tracks",
            {
                "os": os,
                "subprocess": subprocess,
                "get_mp4_audio_codec_settings": lambda _key: {
                    "codec": "aac", "bitrate": None,
                },
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "muxed.mp4")

            def fail_after_partial(command, **kwargs):
                with open(output, "wb") as handle:
                    handle.write(b"partial")
                raise subprocess.CalledProcessError(
                    1, command, stderr="mux failed",
                )

            with patch("subprocess.run", side_effect=fail_after_partial):
                with self.assertRaisesRegex(Exception, "FFmpeg error"):
                    combine(
                        output, "input.mp4", [], [], 0, 44100,
                    )
            self.assertFalse(os.path.exists(output))

    def test_wgp_audio_mux_always_cleans_raw_render_temp(self):
        generate = _function(_parse("app/wgp.py"), "_generate_video_impl")

        def calls_named(node, name):
            return any(
                isinstance(child, ast.Call)
                and (
                    isinstance(child.func, ast.Name)
                    and child.func.id == name
                    or isinstance(child.func, ast.Attribute)
                    and child.func.attr == name
                )
                for child in ast.walk(node)
            )

        cleanup_try = next(
            (
                node for node in ast.walk(generate)
                if isinstance(node, ast.Try)
                and calls_named(ast.Module(body=node.body, type_ignores=[]),
                                "combine_and_concatenate_video_with_audio_tracks")
                and calls_named(ast.Module(body=node.finalbody, type_ignores=[]),
                                "remove")
            ),
            None,
        )
        self.assertIsNotNone(cleanup_try)
        self.assertTrue(any(
            isinstance(child, ast.Name) and child.id == "save_path_tmp"
            for statement in cleanup_try.finalbody
            for child in ast.walk(statement)
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
