"""CPU task-boundary checks using real binders and extracted active wrappers."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.h3_mapping_dispatch import bind_h3_mapping_task, prepare_h3_single_mapping_source, resolve_h3_mapping_source_plan
from services.h3_shot_planner import plan_h3_native_shots
from services.queue_recovery_runtime import QueueRecoveryRuntimeError, recovery_unit_id

SOURCE = "The book opens. <d>[English] Keep <Audio 77> literally.</d>"


def function(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


def descriptor(field, path, *, digest="a" * 64, dependency=None):
    value = dict(field=field, path=path, sha256=digest, size=16)
    if dependency is not None:
        value["dependency"] = dependency
    return value


class H3MappingDispatchTests(unittest.TestCase):
    def plan(self):
        return plan_h3_native_shots(global_prompt=SOURCE, clip_frame_counts=[124, 124],
                                    fps=24, source_canonicalization="t2va")

    def task(self, plan, *, index=0, refs=None, **extra):
        params = dict(prompt=plan["clip_prompts"][index], model_type="minimax_h3_ref2va",
                      video_length=124, image_refs=refs or [], video_prompt_type="",
                      audio_prompt_type="", multi_clip_info=dict(index=index, output_index=0,
                      automatic_h3_longform=True, generated_frames=124, published_frames=124,
                      trim_tail_frames=0, conditioning_model="minimax_h3_ref2va"))
        params.update(extra)
        return dict(id=index + 1, prompt=params["prompt"], params=params)

    def test_native_source_marker_preserves_original_and_is_idempotent(self):
        body = dict(prompt=SOURCE, model_type="minimax_h3_ref2va", video_length=124)
        prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
        self.assertEqual(body["_h3_prompt_mapping_source_plan"]["source_contracts"][0]["authored_prompt"], SOURCE)
        before = copy.deepcopy(body)
        prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
        self.assertEqual(body, before)
        body["prompt"] = "changed"
        with self.assertRaises(QueueRecoveryRuntimeError):
            prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)

    def test_manual_and_same_base_freeform_stay_unchanged(self):
        text = 'Loose prose | separator\n<d>multiline\nwords</d>'
        for model, adaptive in (("minimax_h3", True), ("minimax_h3_ref2va", False)):
            body = dict(prompt=text, model_type=model, video_length=124, h3_adaptive_conditioning=adaptive)
            before = copy.deepcopy(body)
            prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
            self.assertEqual(body, before)

    def test_opaque_fields_do_not_trigger_same_checkpoint_mapping(self):
        for model in ("minimax_h3", "minimax_h3_ref2va"):
            for text in ("summary: first\nsummary: second",
                         "integrated_multimodal_description: literal\nsummary: also literal"):
                body = dict(prompt=text, model_type=model, video_length=124)
                before = copy.deepcopy(body)
                prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
                self.assertEqual(body, before)
        body = dict(prompt="", model_type="minimax_h3_ref2va", video_length=124,
                    _h3_requested_checkpoint="minimax_h3")
        before = copy.deepcopy(body)
        prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
        self.assertEqual(body, before)

    def test_authored_ref_to_base_rejects_at_admission(self):
        from services.h3_prompt_mapping import create_mapping_record
        ref = create_mapping_record(SOURCE, "ref2va", duration_seconds=124 / 24,
                                    reference_manifest=[])["mapped_prompt"]
        body = dict(prompt=ref, model_type="minimax_h3", video_length=124)
        before = copy.deepcopy(body)
        with self.assertRaisesRegex(QueueRecoveryRuntimeError, "original Base source"):
            prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
        self.assertEqual(body, before)

    def test_native_mapping_uses_actual_wgp_frame_alignment(self):
        align = function("app/wgp.py", "align_model_frame_count", {})
        model = dict(fps=24, frames_minimum=107, frames_maximum=345,
                     frame_alignment_modulus=17, frame_alignment_remainder=5,
                     frame_alignment_mode="ceil")
        body = dict(prompt=SOURCE, model_type="minimax_h3_ref2va", video_length=120)
        prepare_h3_single_mapping_source(body, model, align_frame_count=align)
        self.assertEqual(body["video_length"], 124)
        plan = body["_h3_prompt_mapping_source_plan"]
        self.assertEqual(plan["clip_frames"], [124])
        from services.h3_adaptive_execution import bind_h3_execution_segment
        mapped = bind_h3_execution_segment(plan, segment_index=0,
                                           model_type="minimax_h3_ref2va", reference_manifest=[])
        self.assertEqual(mapped["receipt"]["frames"], align(120, model))
        self.assertEqual(mapped["record"]["duration_seconds"], 124 / 24)

    def test_macro_generated_ref_fields_reject_base_at_admission(self):
        from services.h3_prompt_mapping import create_mapping_record, source_prompt_schema
        from services.h3_shot_planner import resolve_h3_source_template
        ref = create_mapping_record(SOURCE, "ref2va", duration_seconds=124 / 24,
                                    reference_manifest=[])["mapped_prompt"]
        definitions, lines = [], []
        for index, line in enumerate(ref.splitlines()):
            if ":" in line:
                name, value = line.split(":", 1)
                definitions.append('{f' + str(index) + '}="' + name + '"')
                lines.append('{f' + str(index) + '}:' + value)
            else:
                lines.append(line)
        source = "!" + " : ".join(definitions) + "\n" + "\n".join(lines)
        self.assertEqual(source_prompt_schema(resolve_h3_source_template(source)), "ref2va")
        body = dict(prompt=source, model_type="minimax_h3", video_length=124)
        with self.assertRaisesRegex(QueueRecoveryRuntimeError, "original Base source"):
            prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
        self.assertEqual(body["prompt"], source)

    def test_macro_source_replays_before_mapping_without_second_expansion(self):
        from services.h3_shot_planner import h3_effective_source, h3_source_compiler_inputs
        for value in ("cat", "{literal_value}"):
            source = '!{animal}="' + value + '"\nA {animal} walks. <d>[English] Keep {spoken}.</d>'
            body = dict(prompt=source, model_type="minimax_h3_ref2va", video_length=124)
            prepare_h3_single_mapping_source(body, {"fps": 24}, align_frame_count=lambda n, d: n)
            plan = body["_h3_prompt_mapping_source_plan"]
            contract = plan["source_contracts"][0]
            self.assertEqual(contract["authored_prompt"], source)
            recipe = h3_source_compiler_inputs(contract)["source_canonicalization"]
            self.assertEqual(recipe["recipe_version"], 2)
            self.assertIn("A " + value + " walks", h3_effective_source(source, recipe))
            task = dict(prompt=body["prompt"], params=copy.deepcopy(body))
            mapped, _ = bind_h3_mapping_task(task, source_plan=plan, segment_index=0,
                source_snapshot=copy.deepcopy(body), initial_images=(), descriptors=[],
                validate_descriptor=lambda d: False, has_audio=lambda p: False)
            self.assertIn("A " + value + " walks", mapped["prompt"])
            self.assertIn("<d>[English] Keep {spoken}.</d>", mapped["prompt"])
            self.assertNotIn("!{animal}", mapped["prompt"])
            tree = ast.parse((ROOT / "app/wgp.py").read_text())
            gate = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                        and ast.unparse(node.test) == "inputs.get('_h3_prompt_template_resolved') is True")
            namespace = dict(inputs={"_h3_prompt_template_resolved": True}, prompt=mapped["prompt"])
            exec(compile(ast.Module(body=[gate], type_ignores=[]), "active-template-gate", "exec"), namespace)
            self.assertEqual(namespace["errors"], "")
            self.assertEqual(namespace["prompt"], mapped["prompt"])

    def test_unknown_mapping_versions_fail_before_dispatch(self):
        for version in (True, 0, 2, "1"):
            with self.subTest(version=version), self.assertRaisesRegex(QueueRecoveryRuntimeError, "version"):
                resolve_h3_mapping_source_plan(dict(prompt_mapping_version=version, shot_plan=self.plan()))

    def test_real_studio_plan_binds_every_carried_child(self):
        from tests.test_studio_prompt_windows import H3LongStudioPlanningTests
        from services.h3_visual_continuity import SAME_SOURCE_VISUAL_CARRY_LINE
        helpers = H3LongStudioPlanningTests()._load_launch_helpers()
        path = "/synthetic/studio-reference.png"
        body = dict(model_type="minimax_h3_ref2va", video_length=700,
                    image_refs=[path], prompt="A person crosses the long room.")
        longform = helpers["_prepare_h3_long_studio_request"](body)
        plan = longform["shot_plan"]
        before = copy.deepcopy(plan)
        self.assertGreater(len(plan["clip_prompts"]), 1)
        self.assertIn(SAME_SOURCE_VISUAL_CARRY_LINE, plan["clip_prompts"][1])
        admitted = descriptor("image_refs:0", path)
        for index, prompt in enumerate(plan["clip_prompts"]):
            image = object()
            params = dict(prompt=prompt, model_type=longform["segment_models"][index]["model_type"],
                          video_length=plan["clip_frames"][index], image_refs=[image])
            task = dict(prompt=prompt, params=params)
            mapped, sidecar = bind_h3_mapping_task(task, source_plan=plan, segment_index=index,
                source_snapshot={**params, "image_refs": [path]}, initial_images=(image,),
                descriptors=[admitted], validate_descriptor=lambda d: d == admitted,
                has_audio=lambda _path: False, materialize_image=lambda _binding: image)
            self.assertIn("detailed_description:", mapped["prompt"])
            if SAME_SOURCE_VISUAL_CARRY_LINE in prompt:
                self.assertIn(SAME_SOURCE_VISUAL_CARRY_LINE, mapped["prompt"])
            self.assertEqual(sidecar["_h3_prompt_mapping_record"]["source_prompt"], prompt)
        self.assertEqual(plan, before)

    def test_unsupported_file_platform_is_reported_before_admission(self):
        from services.h3_mapping_dispatch import require_h3_mapping_audio_roles
        with patch("services.h3_mapping_dispatch.os.name", "nt"):
            for inputs in (dict(video_prompt_type="V", video_guide="reference.mp4"),
                           dict(audio_prompt_type="A", audio_guide="reference.wav")):
                with self.assertRaisesRegex(QueueRecoveryRuntimeError, "manual checkpoint"):
                    require_h3_mapping_audio_roles(inputs)
            require_h3_mapping_audio_roles(dict(image_refs=["reference.png"]))

    def test_active_wrapper_updates_both_prompts_before_wgp_validation(self):
        plan = self.plan()
        image = object()
        task = self.task(plan, refs=[image])
        task["start_image_data_base64"] = ["stale-preview"]
        task["start_image_labels"] = ["stale-label"]
        task["end_image_data_base64"] = ["stale-secondary-preview"]
        task["end_image_labels"] = ["stale-secondary-label"]
        snapshot = {**task["params"], "image_refs": ["/synthetic/image.png"]}
        admitted = [descriptor("image_refs:0", "/synthetic/image.png")]
        namespace = dict(QueueRecoveryRuntimeError=QueueRecoveryRuntimeError,
                         _queue_recovery_manifest_validator=lambda d, **kw: d in admitted)
        binder = function("app/launch.py", "_bind_h3_task_prompt_mapping", namespace)
        with patch("services.h3_reference_binding.materialize_h3_reference_image", return_value=image):
            updated, sidecar = binder(dict(workspace="test", _recovery_owner_digest="owner"),
                                      task, snapshot, (image,), plan, admitted, "/synthetic")
        self.assertEqual(task["prompt"], plan["clip_prompts"][0])
        self.assertEqual(updated["prompt"], updated["params"]["prompt"])
        self.assertIn("detailed_description:", updated["prompt"])
        self.assertIn("<Picture 1>", updated["prompt"])
        self.assertEqual(updated["prompt"].count("<d>"), 1)
        self.assertIs(updated["params"]["image_refs"][0], image)
        self.assertIsNone(updated["start_image_data_base64"])
        self.assertIsNone(updated["start_image_labels"])
        self.assertIsNone(updated["end_image_data_base64"])
        self.assertIsNone(updated["end_image_labels"])
        json.dumps(sidecar)
        observed = []
        def validate_settings(state, model, **kwargs):
            observed.append(kwargs["inputs"]["prompt"])
            self.assertTrue(kwargs["inputs"]["_h3_prompt_template_resolved"])
            return {}, None, None, None
        validator = function("app/wgp.py", "validate_task",
                             dict(primary_settings={}, validate_settings=validate_settings,
                                  get_base_model_type=lambda value: value))
        result = validator(updated, {}, _h3_prompt_template_resolved=True)
        self.assertEqual(observed, [updated["prompt"]])
        self.assertEqual(result["prompt"], updated["prompt"])
        self.assertNotIn("_h3_prompt_template_resolved", result)
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_generation")
        mapping_call = next(n for n in ast.walk(worker) if isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Name) and n.func.id == "_bind_h3_task_prompt_mapping")
        validation_call = next(n for n in ast.walk(worker) if isinstance(n, ast.Call)
                               and isinstance(n.func, ast.Attribute) and n.func.attr == "validate_task")
        self.assertLess(mapping_call.lineno, validation_call.lineno)
        spoofed = {**updated, "params": {**updated["params"], "_h3_prompt_template_resolved": True}}
        self.assertIsNone(validator(spoofed, {}, _h3_prompt_template_resolved=True))

    def test_late_recovered_still_needs_exact_predecessor_and_path(self):
        plan = self.plan()
        path = "/synthetic/late.png"
        task = self.task(plan, index=1, refs=[path])
        snapshot = {**task["params"], "image_refs": [], "_ref2va_continuation": "cut"}
        continuation = dict(mode="semantic_still", sha256="b" * 64, size=16,
                            dependency="unit:v1:" + "c" * 64)
        predecessor = dict(unit_id=continuation["dependency"], continuation=continuation)
        namespace = dict(QueueRecoveryRuntimeError=QueueRecoveryRuntimeError,
                         _queue_recovery_unit_matches=lambda *a, **kw: predecessor,
                         _queue_recovery_continuation_path=lambda *a: path,
                         _queue_recovery_manifest_validator=lambda *a, **kw: False)
        binder = function("app/launch.py", "_bind_h3_task_prompt_mapping", namespace)
        pinned = object()
        with patch("services.h3_reference_binding.materialize_h3_reference_image", return_value=pinned):
            updated, sidecar = binder({}, task, snapshot, (), plan, [], "/synthetic")
        self.assertIs(updated["params"]["image_refs"][0], pinned)
        refs = sidecar["_h3_prompt_mapping_record"]["reference_manifest"]
        self.assertEqual(refs[0]["path"], path)
        self.assertEqual(refs[0]["sha256"], "b" * 64)
        self.assertIn("<Picture 1>", updated["prompt"])
        task["params"]["image_refs"] = ["/synthetic/substitute.png"]
        with self.assertRaises(QueueRecoveryRuntimeError):
            binder({}, task, snapshot, (), plan, [], "/synthetic")
        task["params"]["image_refs"] = [path]
        predecessor["unit_id"] = "changed"
        with self.assertRaises(QueueRecoveryRuntimeError):
            binder({}, task, snapshot, (), plan, [], "/synthetic")

    def test_paired_audio_binds_selected_video_bytes_and_ignores_stale_audio(self):
        plan = self.plan()
        task = self.task(plan, video_prompt_type="V-", video_guide="/synthetic/one.mp4",
                         video_guide3="/synthetic/three.mp4", audio_prompt_type="KABC",
                         audio_guide="/synthetic/stale.wav")
        refs = [descriptor("video_guide:0", "/synthetic/one.mp4"),
                descriptor("video_guide3:0", "/synthetic/three.mp4", digest="b" * 64)]
        observed = []
        copies = {"video_guide:0": "/pinned/one.mp4", "video_guide3:0": "/pinned/three.mp4"}
        updated, sidecar = bind_h3_mapping_task(task, source_plan=plan, segment_index=0,
            source_snapshot=copy.deepcopy(task["params"]), initial_images=(), descriptors=refs,
            validate_descriptor=lambda d: d in refs, has_audio=lambda p: observed.append(p) or True,
            materialize_files=lambda bindings: {"paths": copies}, cleanup_files=lambda token: True)
        self.assertEqual(observed, ["/synthetic/one.mp4", "/synthetic/three.mp4", "/pinned/one.mp4", "/pinned/three.mp4"])
        self.assertEqual(updated["params"]["video_guide3"], copies["video_guide3:0"])
        self.assertIn("<Audio 2>", updated["prompt"])
        self.assertNotIn("<Audio 3>", updated["prompt"])
        manifest = sidecar["_h3_prompt_mapping_record"]["reference_manifest"]
        self.assertTrue(all("audio_path" not in item for item in manifest))

    def test_file_copy_consumption_and_failed_probe_cleanup(self):
        import hashlib
        import tempfile
        from services.h3_reference_binding import materialize_h3_reference_files, cleanup_h3_reference_files
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reference.mp4"
            source.write_bytes(b"admitted video bytes")
            staging = root / "staging"
            staging.mkdir(mode=0o700)
            task = self.task(plan, video_prompt_type="V", video_guide=str(source), audio_prompt_type="K")
            refs = [dict(field="video_guide:0", path=str(source), size=source.stat().st_size,
                         sha256=hashlib.sha256(source.read_bytes()).hexdigest())]
            for cancelled in (False, True):
                tokens = []
                def materialize(bindings):
                    token = materialize_h3_reference_files(bindings, str(staging), job_id="mapper-test")
                    tokens.append(token)
                    return token
                def probe(path):
                    if path != str(source) and cancelled:
                        raise InterruptedError("cancelled")
                    return True
                kwargs = dict(source_plan=plan, segment_index=0,
                    source_snapshot=copy.deepcopy(task["params"]), initial_images=(), descriptors=refs,
                    validate_descriptor=lambda d: d in refs, has_audio=probe,
                    materialize_files=materialize, cleanup_files=cleanup_h3_reference_files)
                if cancelled:
                    with self.assertRaises(InterruptedError):
                        bind_h3_mapping_task(task, **kwargs)
                    self.assertFalse(Path(tokens[0]["directory"]).exists())
                else:
                    updated, sidecar = bind_h3_mapping_task(task, **kwargs)
                    source.write_bytes(b"replaced source bytes")
                    self.assertEqual(Path(updated["params"]["video_guide"]).read_bytes(), b"admitted video bytes")
                    self.assertEqual(sidecar["video_guide"], str(source))
                    tree = ast.parse((ROOT / "app/launch.py").read_text())
                    runtime = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_run_generation")
                    loop = next(node for node in ast.walk(runtime) if isinstance(node, ast.For)
                                and ast.unparse(node.iter) == "enumerate(queue)")
                    cleanup_gate = loop.body[0]
                    self.assertIsInstance(cleanup_gate, ast.If)
                    namespace = dict(h3_reference_file_tokens=[updated["_h3_reference_files"]],
                                     QueueRecoveryRuntimeError=QueueRecoveryRuntimeError)
                    exec(compile(ast.Module(body=[cleanup_gate], type_ignores=[]), "next-child-cleanup", "exec"), namespace)
                    self.assertEqual(namespace["h3_reference_file_tokens"], [])
                    self.assertFalse(Path(tokens[0]["directory"]).exists())
                    source.write_bytes(b"admitted video bytes")

    def test_loaded_image_replacement_and_unadmitted_reference_fail(self):
        plan = self.plan()
        original = object()
        task = self.task(plan, refs=[object()])
        snapshot = {**task["params"], "image_refs": ["/synthetic/image.png"]}
        with self.assertRaises(QueueRecoveryRuntimeError):
            bind_h3_mapping_task(task, source_plan=plan, segment_index=0,
                source_snapshot=snapshot, initial_images=(original,), descriptors=[],
                validate_descriptor=lambda d: False, has_audio=lambda p: True)
        task["params"]["image_refs"] = [original]
        with self.assertRaises(ValueError):
            bind_h3_mapping_task(task, source_plan=plan, segment_index=0,
                source_snapshot=snapshot, initial_images=(original,), descriptors=[],
                validate_descriptor=lambda d: False, has_audio=lambda p: True)

    def test_mapping_receipt_enters_segment_id_and_checks_current_geometry(self):
        plan = self.plan()
        task = self.task(plan)
        updated, sidecar = bind_h3_mapping_task(task, source_plan=plan, segment_index=0,
            source_snapshot=copy.deepcopy(task["params"]), initial_images=(), descriptors=[],
            validate_descriptor=lambda d: False, has_audio=lambda p: True)
        settings = function("app/launch.py", "_h3_segment_recovery_settings",
                            dict(QueueRecoveryRuntimeError=QueueRecoveryRuntimeError, copy=copy))
        legacy = settings(task["params"]["multi_clip_info"])
        mapped = settings(updated["params"]["multi_clip_info"])
        self.assertNotIn("prompt_mapping", legacy)
        self.assertEqual(mapped["prompt_mapping"], sidecar["_h3_prompt_mapping_receipt"])
        self.assertNotEqual(recovery_unit_id("job", "h3_segment", settings=legacy),
                            recovery_unit_id("job", "h3_segment", settings=mapped))
        broken = copy.deepcopy(updated["params"]["multi_clip_info"])
        broken["generated_frames"] = broken["published_frames"] = 132
        with self.assertRaises(QueueRecoveryRuntimeError):
            settings(broken)


if __name__ == "__main__":
    unittest.main()
