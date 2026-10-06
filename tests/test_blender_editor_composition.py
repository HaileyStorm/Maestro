"""Composition queue publication and recovery with actual CPU FFmpeg.

Native Blender/GPU, project authentication and unrelated model/credit guards
are fixtures. These tests prove queue/manifest/finality behavior, not native
rendering or live access acceptance.
"""
from __future__ import annotations
import sys
import unittest
import subprocess
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import ast, asyncio, copy, contextvars, hashlib, hmac, json, math, os, threading, time, types, uuid
from contextlib import nullcontext, contextmanager
from pathlib import Path
import tempfile
from unittest import mock
from fastapi import HTTPException
from services import blender_mcp_service, blender_mcp_transport, composition_worker, job_lifecycle as lifecycle
from services.composition_package import digest, CompositionError
from services.composition_worker import prepare_delivery, publish_delivery, delivery_paths
from services.editor_projects import probe_media
from services.output_access import output_policy_from_request
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import QueueRecoveryCoordinator, owner_principal_digest, project_instance_digest
from services.queue_recovery_runtime import atomic_write_request_manifest, load_request_manifest, remove_request_manifest, QueueRecoveryRuntimeError
from services.blender_mcp_service import BlenderMCPService
TREE = ast.parse((ROOT / "app/launch.py").read_text())
def raw_package():
    segments = []
    for sid in ["A", "B"]:
        segments.append(
            {
                "id": sid,
                "scene": {
                    "clear_scene": True,
                    "objects": [{"name": sid, "primitive": "cube"}],
                },
                "animation": {
                    "frame_start": 0,
                    "frame_end": 23,
                    "objects": [
                        {
                            "name": sid,
                            "keyframes": [
                                {"frame": 0, "location": [0, 0, 0]},
                                {"frame": 23, "location": [1, 0, 0]},
                            ],
                        }
                    ],
                },
                "fps": 24,
                "width": 128,
                "height": 72,
            }
        )
    return {
        "schema": "maestro/composition/v1",
        "id": "composition-queue-fixture",
        "canvas": {"width": 128, "height": 72, "fps": 24},
        "audio": {"mode": "silence", "sample_rate": 48000},
        "segments": segments,
        "clips": [
            {"id": "use-A-1", "segment_id": "A", "source_frame": 6, "frame_count": 12},
            {"id": "use-B", "segment_id": "B", "source_frame": 0, "frame_count": 24},
            {"id": "use-A-2", "segment_id": "A", "source_frame": 0, "frame_count": 18},
        ],
    }
SECRET = b'private-cpu-queue-fixture-signing-key-64bytes-not-production-key'

class Boundary(BlenderMCPService):
    invocations = []

    def __init__(self, client, root):
        super().__init__(object(), root)

    def composition_idle_probe(self, expected=None):
        from services.composition_recovery import process_identity
        identity = process_identity(os.getpid())
        if expected is not None and expected != identity:
            raise CompositionError("fixture incarnation changed")
        return identity

    def prepare_composition_segment(self, persist, *, cancelled):
        identity = self.composition_idle_probe()
        layout = {"frame_directory": str(self.project_root / ("maestro_frames_" + uuid.uuid4().hex)),
                  "encoder_destination": str(self.project_root / ("maestro_" + uuid.uuid4().hex + ".mp4"))}
        persist(identity, layout, None)

    def invoke(self, tool, args, *, cancelled):
        Boundary.invocations.append((tool, copy.deepcopy(args)))
        if cancelled():
            raise InterruptedError('cancelled')
        if tool == 'render_animation':
            color = 'red' if args['output_path'] == 'A.mp4' else 'blue'
            path = self.project_root / args['output_path']
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', f"color={color}:s={args['width']}x{args['height']}:r={args['fps']}", '-frames:v', str(args['frame_end'] - args['frame_start'] + 1), '-an', '-c:v', 'libx264', '-threads', '1', str(path)], check=True)
        return {'status': 'ok'}

class Request:

    def __init__(self, body):
        self.body = body
        self.state = types.SimpleNamespace(maestro_session_id='fixture-owner')

    async def stream(self):
        yield json.dumps(self.body).encode()

class NativeClient:
    closes = 0

    def __init__(self, **kwargs):
        pass

    def close(self):
        NativeClient.closes += 1

class NativeSlot:
    admissions = 0

    def __init__(self, **kwargs):
        self.check = kwargs.get('cancel_checkpoint')

    def __enter__(self):
        if self.check:
            self.check()
        NativeSlot.admissions += 1
        return True

    def __exit__(self, *args):
        pass

def functions(namespace, names):
    selected = []
    for node in TREE.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            node = copy.deepcopy(node)
            if hasattr(node, "decorator_list"):
                node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == set(names)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
            "private-launch-overlay",
            "exec",
        ),
        namespace,
    )

def environment(root):
    project = root / "scene"
    project.mkdir(exist_ok=True)
    project_digest = project_instance_digest(SECRET, "a" * 32)
    coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(root / "queue.jsonl"))
    namespace = {
        "os": os,
        "json": json,
        "hmac": hmac,
        "hashlib": hashlib,
        "math": math,
        "copy": copy,
        "time": time,
        "uuid": uuid,
        "threading": threading,
        "Request": Request,
        "HTTPException": HTTPException,
        "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
        "_request_remote": contextvars.ContextVar(
            "private-fixture-remote", default=True
        ),
        "_request_session_id": contextvars.ContextVar(
            "private-fixture-session", default="fixture-owner"
        ),
        "_workspace_lifecycle_lock": threading.RLock(),
        "_gen_lock": threading.Lock(),
        "_blender_scene_lock": threading.RLock(),
        "_queue_recovery_checkpoint_lock": threading.RLock(),
        "_active_gen_states": {},
        "_session_secret": lambda: SECRET,
        "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_fixture_unused",
        "output_policy_from_request": output_policy_from_request,
        "owner_principal_digest": owner_principal_digest,
        "atomic_write_request_manifest": atomic_write_request_manifest,
        "load_request_manifest": load_request_manifest,
        "remove_request_manifest": remove_request_manifest,
        "_queue_recovery_coordinator": coordinator,
        "_queue_recovery_with_bounded_compaction": lambda callback: callback(),
        "_queue_recovery_project_identity": lambda *args: project_digest,
        "_queue_recovery_existing_project_identity": lambda *args: project_digest,
        "_existing_workspace_dir": lambda workspace: (
            str(project)
            if workspace == "scene"
            else (_ for _ in []).throw(ValueError("Wrong project"))
        ),
        "_reserve_workspace_operations": lambda *args: nullcontext(),
        "_output_lineage_mutation_guard": lambda *args: nullcontext(),
        "_require_project_access": lambda request, workspace, **kwargs: (
            str(project)
            if workspace == "scene"
            else (_ for _ in []).throw(HTTPException(403))
        ),
        "_require_job_workspace_available": lambda job: None,
        "_require_job_model_recipe_terms": lambda job: None,
        "_seal_h3_offload_plan_for_job": lambda *args, **kwargs: None,
        "_stamp_h3_lightx2v_recovery_identity": lambda *args: None,
        "_credit_prepare_submission": lambda job: None,
        "_stamp_requested_generation_residency": lambda job: None,
        "_stamp_job_origin": lambda job: job,
        "_queue_recovery_input_descriptors": lambda *args: [],
        "_blender_runtime_info": lambda: {"version": "5.1.2"},
        "_blender_checkout_root": lambda: "fixture-unused",
        "_require_blender_ready": lambda: None,
        "_new_generation_job_id": lambda: uuid.uuid4().hex,
        "_WgpNativeGpuExecutionSlot": NativeSlot,
        "_WgpNativeGpuWaitCancelled": InterruptedError,
        "generation_slot": lifecycle.generation_slot,
        "try_start": lifecycle.try_start,
        "finish_job": lifecycle.finish_job,
        "is_cancel_requested": lifecycle.is_cancel_requested,
        "register_abort_state": lifecycle.register_abort_state,
        "unregister_abort_state": lifecycle.unregister_abort_state,
        "checkpoint_recovery_job": lifecycle.checkpoint_recovery_job,
        "durable_queue_state": lifecycle.durable_queue_state,
        "retry_failed_recovery_job": lifecycle.retry_failed_recovery_job,
        "update_queue_job": lifecycle.update_queue_job,
    }
    names = [
        "_JobRegistry",
        "_editor_request_body",
        "_queue_recovery_register_and_publish",
        "_queue_recovery_worker",
        "_queue_recovery_checkpoint",
        "_queue_recovery_materialize_job",
        "_queue_recovery_reconcile_cursor",
        "_run_tool_editor_export",
        "submit_blender_editor_composition",
        "recover_blender_editor_composition",
        "get_blender_editor_composition_recovery",
    ]
    names.extend(
        node.name
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_composition_")
    )
    names.append("_run_tool_composition_export")
    functions(namespace, names)
    namespace["_jobs"] = namespace["_JobRegistry"]()
    namespace["_require_owned_job"] = lambda job_id, request: namespace["_jobs"][job_id]
    namespace["_require_owned_job_project"] = lambda job_id, request, workspace: namespace["_jobs"][job_id]
    lifecycle.configure_durability_hook(None)
    lifecycle.configure_durability_hook(coordinator.prospective_transition)
    return namespace, coordinator, project, project_digest

def new_job(ns):
    # Exercise the actual HTTP route, registry, manifest and coordinator, while
    # retaining worker start for deterministic observation in this fixture.
    actual = ns["_queue_recovery_register_and_publish"]
    ns["_queue_recovery_register_and_publish"] = lambda job, **kwargs: actual(
        job, **kwargs, defer_worker=True
    )
    try:
        result = asyncio.run(
            ns["submit_blender_editor_composition"](
                "scene",
                Request({"package": raw_package(), "private_output": True}),
            )
        )
    finally:
        ns["_queue_recovery_register_and_publish"] = actual
    job = ns["_jobs"][result["job_id"]]
    assert (
        job["private"] and job["source_remote"] and job["kind"] == "tool_editor_export"
    )
    return job

def restore(ns, root, project, digest):
    lifecycle.configure_durability_hook(None)
    coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(root / "queue.jsonl"))
    lifecycle.configure_durability_hook(coordinator.prospective_transition)
    snapshot = next(iter(coordinator.restore().jobs.values()))
    ns["_queue_recovery_coordinator"] = coordinator
    job, resume = ns["_queue_recovery_materialize_job"](
        snapshot, {"scene": (str(project), digest)}
    )
    ns["_jobs"][job["id"]] = job
    return job, resume

def interrupted_job(ns):
    """Actual worker completes A and arms B, then the owned thread returns."""
    job = new_job(ns)
    original = Boundary.invoke
    def stop_b(service, tool, args, *, cancelled):
        if tool == "render_animation" and args["output_path"] == "B.mp4":
            raise RuntimeError("CPU fixture lost native response")
        return original(service, tool, args, cancelled=cancelled)
    with mock.patch.object(blender_mcp_service, "BlenderMCPService", Boundary), mock.patch.object(blender_mcp_transport, "StdioBlenderMCPClient", NativeClient), mock.patch.object(Boundary, "invoke", stop_b):
        worker = threading.Thread(target=ns["_run_tool_composition_export"], args=(job["id"],))
        worker.start(); worker.join(10)
        assert not worker.is_alive()
    assert job["status"] == "queued" and job["queue_held"]
    return job


def admit_recovery(ns, job, request_id=None):
    from services import composition_recovery
    request_id = request_id or uuid.uuid4().hex
    body = {"recovery_request_id": request_id, "expected_created_at": job["created_at"],
            "expected_execution_attempt": job["execution_attempt"], "confirmed": True}
    with mock.patch.object(composition_recovery, "reconcile_once", return_value=False):
        response = asyncio.run(ns["recover_blender_editor_composition"](job["id"], Request(body)))
    assert response["status"] == "pending"
    return body


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg is required")
class ExplicitCompositionRecoveryTests(unittest.TestCase):
    def tearDown(self):
        lifecycle.configure_durability_hook(None)

    def test_restart_recovery_reuses_a_renders_b_once_and_get_repeated_post_never_dispatch(self):
        from services.composition_package import read_receipt
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, coordinator, project, pdigest = environment(root)
            current_renderer = ns["_composition_renderer_identity"]
            historical_renderer = {"blender_version":"5.1.2",
                "blender_mcp_revision":"03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4",
                "blender_service_sha256":"0ad0d6607b1933dd2eec2a288b63ce63d5d20ff9bb642586dc72ee045a45f9bb",
                "editor_service_sha256":"0986b526eb7889e527d8ec80144f1f61b62f984de4e8ddd7f10ed24efd2b7aab"}
            ns["_composition_renderer_identity"] = lambda: copy.deepcopy(historical_renderer)
            job = interrupted_job(ns)
            ns["_composition_renderer_identity"] = current_renderer
            package, directory, _, original_binding = ns["_composition_job_context"](job, require_current_renderer=False)
            originals = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
            job, resume = restore(ns, root, project, pdigest)
            self.assertFalse(resume)
            body = admit_recovery(ns, job)
            # Pending logical request survives a genuinely fresh coordinator.
            job, resume = restore(ns, root, project, pdigest)
            self.assertFalse(resume)
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"], body["recovery_request_id"], Request({})))["status"], "pending")
            done = threading.Event()
            actual = ns["_run_tool_editor_export"]
            def run(job_id):
                try: actual(job_id)
                finally: done.set()
            ns["_run_tool_editor_export"] = run
            Boundary.invocations = []
            with mock.patch.object(blender_mcp_service, "BlenderMCPService", Boundary), mock.patch.object(blender_mcp_transport, "StdioBlenderMCPClient", NativeClient):
                ns["_composition_recovery_reconcile"](job["id"], body["recovery_request_id"])
                self.assertTrue(done.wait(15))
            self.assertEqual(job["status"], "completed")
            renders = [args["output_path"] for tool,args in Boundary.invocations if tool == "render_animation"]
            self.assertEqual(renders, ["B.mp4"])
            self.assertEqual({name:(directory/name).read_bytes() for name in originals}, originals)
            fresh = directory / ("attempt-" + body["recovery_request_id"])
            a = read_receipt(fresh / "A.receipt.json", SECRET)
            self.assertEqual(a["reused_from"]["binding"], original_binding)
            metadata=json.loads((project/("composition_"+job["id"]+".meta.json")).read_bytes())
            self.assertEqual(metadata["transform"]["segments"][0]["renderer_identity"],historical_renderer)
            self.assertEqual(metadata["transform"]["segments"][1]["renderer_identity"],current_renderer())
            self.assertEqual(a["binding"]["renderer_identity"],current_renderer())
            after = len(Boundary.invocations)
            response = asyncio.run(ns["recover_blender_editor_composition"](job["id"], Request(body)))
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"], body["recovery_request_id"], Request({}))), response)
            self.assertEqual(len(Boundary.invocations), after)
            reopened, resume = restore(ns, root, project, pdigest)
            self.assertEqual(reopened["status"], "completed")
            self.assertFalse(resume)
            changed = {**body, "expected_execution_attempt": body["expected_execution_attempt"]+1}
            with self.assertRaises(HTTPException) as error:
                asyncio.run(ns["recover_blender_editor_composition"](job["id"], Request(changed)))
            self.assertEqual(error.exception.status_code, 409)

    def test_actual_startup_resumes_persisted_pending_proof_without_another_post(self):
        from services.queue_recovery_adapter import AUTOMATIC_RETIREMENT_STATUSES
        from services.queue_recovery_runtime import cleanup_orphan_request_manifests, cleanup_orphan_staged_outputs
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, _, project, pdigest = environment(root)
            job = interrupted_job(ns)
            body = admit_recovery(ns, job)
            lifecycle.configure_durability_hook(None)
            fresh = QueueRecoveryCoordinator(QueueRecoveryJournal(root/"queue.jsonl"))
            lifecycle.configure_durability_hook(fresh.prospective_transition)
            ns.update(_queue_recovery_workers_started=False, _queue_recovery_coordinator=fresh,
                      _queue_recovery_restored=fresh.restore(),
                      _queue_recovery_existing_projects=lambda:{"scene":(str(project),pdigest)},
                      AUTOMATIC_RETIREMENT_STATUSES=AUTOMATIC_RETIREMENT_STATUSES,
                      _CREDIT_CLEANUP_PARAM="unused-credit-cleanup", _restore_h3_prompt_rewriter_cleanup=lambda jobs:None,
                      restore_scheduler_state=lifecycle.restore_scheduler_state,
                      _stamp_requested_generation_residency=lambda *args,**kwargs:None,
                      cleanup_orphan_request_manifests=cleanup_orphan_request_manifests,
                      cleanup_orphan_staged_outputs=cleanup_orphan_staged_outputs)
            functions(ns,["_restore_queue_recovery_on_startup"])
            done = threading.Event()
            actual = ns["_run_tool_editor_export"]
            def run(job_id):
                try: actual(job_id)
                finally: done.set()
            ns["_run_tool_editor_export"] = run
            Boundary.invocations = []
            with mock.patch.object(blender_mcp_service,"BlenderMCPService",Boundary),mock.patch.object(blender_mcp_transport,"StdioBlenderMCPClient",NativeClient):
                self.assertTrue(ns["_restore_queue_recovery_on_startup"]())
                self.assertTrue(done.wait(15))
            restored = ns["_jobs"][job["id"]]
            self.assertEqual(restored["status"],"completed")
            self.assertEqual([args["output_path"] for tool,args in Boundary.invocations if tool=="render_animation"],["B.mp4"])
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))["status"],"accepted")
            self.assertTrue(ns["_restore_queue_recovery_on_startup"]())
            self.assertEqual(len([tool for tool,_ in Boundary.invocations if tool=="render_animation"]),1)
            # Automatic terminal retirement must preserve the accepted request,
            # its manifest and the original attempt evidence for GET-only reload.
            cursor = copy.deepcopy(restored["recovery_cursor"])
            pointer = copy.deepcopy(restored["_recovery_manifest_pointer"])
            package, attempt, _, _ = ns["_composition_job_context"](restored, require_current_renderer=False)
            evidence = {str(p.relative_to(project)):p.read_bytes() for p in attempt.parent.rglob("*") if p.is_file()}
            fresh.compact()
            after_compaction = QueueRecoveryCoordinator(QueueRecoveryJournal(root/"queue.jsonl"))
            lifecycle.configure_durability_hook(None)
            lifecycle.configure_durability_hook(after_compaction.prospective_transition)
            ns.update(_queue_recovery_workers_started=False, _queue_recovery_coordinator=after_compaction,
                      _queue_recovery_restored=after_compaction.restore(), _jobs=ns["_JobRegistry"]())
            with mock.patch.object(blender_mcp_service,"BlenderMCPService",Boundary),mock.patch.object(blender_mcp_transport,"StdioBlenderMCPClient",side_effect=AssertionError("completed recovery must not probe or render")):
                self.assertTrue(ns["_restore_queue_recovery_on_startup"]())
            completed=ns["_jobs"][job["id"]]
            self.assertEqual(completed["status"],"completed")
            self.assertEqual(completed["recovery_cursor"],cursor)
            self.assertTrue((project/pointer["path"]).is_file())
            self.assertEqual({str(p.relative_to(project)):p.read_bytes() for p in attempt.parent.rglob("*") if p.is_file()},evidence)
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))["status"],"accepted")
            self.assertIn(job["id"],after_compaction.restore().jobs)

    def test_stale_valid_request_has_durable_rejected_receipt_then_fresh_request_can_proceed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, _, project, pdigest = environment(root)
            job = interrupted_job(ns)
            request_id = str(uuid.uuid4())
            stale = {"recovery_request_id":request_id,"expected_created_at":job["created_at"],"expected_execution_attempt":job["execution_attempt"]-1,"confirmed":True}
            response = asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(stale)))
            self.assertEqual(response["status"],"rejected")
            self.assertEqual(response["recovery_request_id"],request_id)
            job,_ = restore(ns,root,project,pdigest)
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],request_id,Request({}))),response)
            self.assertEqual(admit_recovery(ns,job)["expected_execution_attempt"],job["execution_attempt"])

    def test_worker_start_failure_keeps_accepted_intent_and_pristine_restart_can_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, _, project, pdigest = environment(root)
            job = interrupted_job(ns)
            body = admit_recovery(ns,job)
            with mock.patch.object(blender_mcp_service,"BlenderMCPService",Boundary),mock.patch.object(blender_mcp_transport,"StdioBlenderMCPClient",NativeClient),mock.patch.object(threading.Thread,"start",side_effect=OSError("CPU fixture thread start failure")):
                ns["_composition_recovery_reconcile"](job["id"],body["recovery_request_id"])
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))["status"],"accepted")
            self.assertTrue(job["queue_held"])
            self.assertEqual(job["_recovery_reason_code"],"worker_start_failed")
            restored,resume = restore(ns,root,project,pdigest)
            self.assertTrue(resume)
            self.assertFalse(restored["queue_held"])
            _, directory, _, _ = ns["_composition_job_context"](restored)
            self.assertFalse((directory/"B.receipt.json").exists())

    def test_finite_history_hides_new_action_but_existing_uuid_still_reconciles(self):
        with tempfile.TemporaryDirectory() as temporary:
            ns, _, _, _ = environment(Path(temporary))
            job = interrupted_job(ns)
            functions(ns,["_public_queue_recovery_metadata"])
            ns.update(_queue_recovery_reason_code=lambda job:job.get("_recovery_reason_code"),
                      _queue_recovery_attempt=lambda job:job.get("recovery_attempt",0),
                      MAX_RECOVERY_ATTEMPTS=3,_QUEUE_RECOVERY_REASON_TEXT={})
            failed=dict(job,status="failed",recovery_state="terminal")
            public=ns["_public_queue_recovery_metadata"](failed)
            self.assertTrue(public["recovery_blocked"])
            self.assertEqual(public["recovery_actions"],["recover_composition"])
            bodies = []
            for _ in range(8):
                body = {"recovery_request_id":str(uuid.uuid4()),"expected_created_at":job["created_at"],"expected_execution_attempt":job["execution_attempt"]-1,"confirmed":True}
                bodies.append(body)
                self.assertEqual(asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(body)))["status"],"rejected")
            with self.assertRaises(HTTPException) as error:
                admit_recovery(ns,job)
            self.assertEqual(error.exception.status_code,409)
            functions(ns,["_public_queue_recovery_metadata"])
            ns.update(_queue_recovery_reason_code=lambda job:job.get("_recovery_reason_code"),
                      _queue_recovery_attempt=lambda job:job.get("recovery_attempt",0),
                      MAX_RECOVERY_ATTEMPTS=3,_QUEUE_RECOVERY_REASON_TEXT={})
            public = ns["_public_queue_recovery_metadata"](job)
            self.assertEqual(public["recovery_actions"],[])
            self.assertFalse(public["recovery_actionable"])
            self.assertTrue(public["recovery_blocked"])
            self.assertIn("limit",public["recovery_reason_text"])
            self.assertEqual(asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(bodies[0])))["status"],"rejected")
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],bodies[0]["recovery_request_id"],Request({})))["status"],"rejected")

    def test_sigkill_owned_cpu_app_then_real_journal_recovery_preserves_partial_b(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = """import sys,time,threading
from pathlib import Path
from unittest import mock
import test_blender_editor_composition as f
ns,_,_,_=f.environment(Path(sys.argv[1]))
job=f.new_job(ns)
original=f.Boundary.invoke
def pause_b(service,tool,args,*,cancelled):
    if tool=='render_animation' and args['output_path']=='B.mp4':
        (service.project_root/'B.mp4').write_bytes(b'owned incomplete native B')
        print(job['id'],flush=True)
        time.sleep(60)
    return original(service,tool,args,cancelled=cancelled)
with mock.patch.object(f.blender_mcp_service,'BlenderMCPService',f.Boundary),mock.patch.object(f.blender_mcp_transport,'StdioBlenderMCPClient',f.NativeClient),mock.patch.object(f.Boundary,'invoke',pause_b):
    ns['_run_tool_composition_export'](job['id'])
"""
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT/"app"),str(ROOT/"tests")]),PYTHONDONTWRITEBYTECODE="1",CUDA_VISIBLE_DEVICES="",HIP_VISIBLE_DEVICES="")
            child = subprocess.Popen([sys.executable,"-B","-c",code,str(root)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            try:
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout,selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=10),"CPU child did not reach armed B")
                    job_id = child.stdout.readline().strip()
                self.assertRegex(job_id,r"^[0-9a-f]{32}$")
                child.kill(); child.wait(timeout=5)
                self.assertLess(child.returncode,0)
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                child.communicate(timeout=5)
            ns,fresh,project,pdigest = environment(root)
            snapshot = fresh.restore().jobs[job_id]
            job,resume = ns["_queue_recovery_materialize_job"](snapshot,{"scene":(str(project),pdigest)})
            ns["_jobs"][job_id] = job
            self.assertFalse(resume)
            _,directory,_,_ = ns["_composition_job_context"](job)
            original = {p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
            body = admit_recovery(ns,job)
            done=threading.Event()
            actual=ns["_run_tool_editor_export"]
            def run(job_id):
                try: actual(job_id)
                finally: done.set()
            ns["_run_tool_editor_export"]=run
            Boundary.invocations=[]
            with mock.patch.object(blender_mcp_service,"BlenderMCPService",Boundary),mock.patch.object(blender_mcp_transport,"StdioBlenderMCPClient",NativeClient):
                ns["_composition_recovery_reconcile"](job_id,body["recovery_request_id"])
                self.assertTrue(done.wait(15))
            self.assertEqual(job["status"],"completed")
            self.assertEqual([args["output_path"] for tool,args in Boundary.invocations if tool=="render_animation"],["B.mp4"])
            self.assertEqual({name:(directory/name).read_bytes() for name in original},original)
            self.assertEqual((directory/"B.mp4").read_bytes(),b"owned incomplete native B")

    def test_source_closure_changed_after_proof_is_rejected_before_queued_admission(self):
        from services import composition_recovery
        with tempfile.TemporaryDirectory() as temporary:
            ns,_,project,_=environment(Path(temporary))
            job=interrupted_job(ns)
            _,directory,_,_=ns["_composition_job_context"](job)
            body=admit_recovery(ns,job)
            actual=composition_recovery.prepare_attempt
            def alter(*args,**kwargs):
                result=actual(*args,**kwargs)
                (directory/"A.mp4").write_bytes(b"changed after stopped proof and copy")
                return result
            with mock.patch.object(composition_recovery,"prepare_attempt",alter),mock.patch.object(blender_mcp_service,"BlenderMCPService",Boundary),mock.patch.object(blender_mcp_transport,"StdioBlenderMCPClient",NativeClient):
                ns["_composition_recovery_reconcile"](job["id"],body["recovery_request_id"])
            self.assertTrue(job["queue_held"])
            self.assertNotIn("active_attempt",job["recovery_cursor"]["composition"])
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))["status"],"rejected")
            self.assertFalse(list(project.glob("composition_*.mp4")))

    def test_lost_durable_ack_does_not_start_proof_and_fresh_journal_retains_confirmed_request(self):
        from services import composition_recovery
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            ns,coordinator,project,pdigest=environment(root)
            job=interrupted_job(ns)
            body={"recovery_request_id":str(uuid.uuid4()),"expected_created_at":job["created_at"],"expected_execution_attempt":job["execution_attempt"],"confirmed":True}
            def lost_ack(transition):
                coordinator.prospective_transition(transition)
                raise OSError("CPU fixture lost persistence acknowledgement")
            lifecycle.configure_durability_hook(None)
            lifecycle.configure_durability_hook(lost_ack)
            with mock.patch.object(composition_recovery,"reconcile_once",side_effect=AssertionError("proof must not start after unknown commit")),self.assertRaises(OSError):
                asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(body)))
            self.assertNotIn("requests",job["recovery_cursor"]["composition"])
            restored,_=restore(ns,root,project,pdigest)
            response=asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))
            self.assertEqual(response["status"],"pending")
            self.assertEqual(restored["recovery_cursor"]["composition"]["requests"][uuid.UUID(body["recovery_request_id"]).hex]["expected_execution_attempt"],body["expected_execution_attempt"])
            self.assertFalse(list(project.glob("composition_*.mp4")))

    def test_changed_a_legacy_attempt_and_reused_pid_reject_before_native_barrier(self):
        from services.composition_package import read_receipt, atomic_json, sign_receipt
        for fault in ("corrupt-a", "legacy", "reused-pid"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                ns, _, project, _ = environment(root)
                job = interrupted_job(ns)
                _, directory, _, _ = ns["_composition_job_context"](job)
                b_path = directory / "B.receipt.json"
                if fault == "corrupt-a":
                    (directory / "A.mp4").write_bytes(b"changed")
                else:
                    b = read_receipt(b_path, SECRET)
                    if fault == "legacy": b.pop("execution")
                    else: b["execution"]["worker"]["app"]["start_ticks"] += 1
                    atomic_json(b_path, sign_receipt(b, SECRET))
                original_b = b_path.read_bytes()
                body = admit_recovery(ns, job)
                with mock.patch.object(blender_mcp_service.BlenderMCPService, "composition_idle_probe", side_effect=AssertionError("must inspect A/worker before barrier")):
                    ns["_composition_recovery_reconcile"](job["id"], body["recovery_request_id"])
                self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"], body["recovery_request_id"], Request({})))["status"], "rejected")
                self.assertEqual(b_path.read_bytes(), original_b)
                self.assertTrue(job["queue_held"])
                self.assertFalse(list(project.glob("composition_*.mp4")))

    def test_signed_pre_spawn_paths_block_live_encoder_with_no_pid_receipt(self):
        from services.composition_recovery import prove_stopped
        from services.composition_package import read_receipt, CompositionAttemptUnresolved
        with tempfile.TemporaryDirectory() as temporary:
            ns, _, _, _ = environment(Path(temporary))
            job = interrupted_job(ns)
            _, directory, _, _ = ns["_composition_job_context"](job)
            receipt = read_receipt(directory / "B.receipt.json", SECRET)
            layout = receipt["execution"]["layout"]
            self.assertIsNone(receipt["execution"]["encoder"])
            child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)", str(Path(layout["frame_directory"])/"frame_%04d.png"), layout["encoder_destination"]])
            try:
                with self.assertRaises(CompositionAttemptUnresolved):
                    prove_stopped(receipt, idle_barrier=lambda identity: identity)
                self.assertIsNone(child.poll())
            finally:
                child.terminate(); child.wait(timeout=5)
            self.assertTrue(prove_stopped(receipt, idle_barrier=lambda identity: identity))

    def test_unsupported_process_identity_keeps_normal_render_and_unbound_recovery_closed(self):
        from services import composition_recovery
        from services.composition_package import read_receipt
        with tempfile.TemporaryDirectory() as temporary:
            ns, _, _, _ = environment(Path(temporary))
            with mock.patch.object(composition_recovery.sys, "platform", "darwin"), mock.patch.object(composition_recovery, "process_identity", side_effect=AssertionError("unsupported host must not read proc")):
                job = interrupted_job(ns)
            _, directory, _, _ = ns["_composition_job_context"](job)
            self.assertTrue((directory / "A.mp4").is_file())
            self.assertNotIn("execution", read_receipt(directory / "B.receipt.json", SECRET))
            body = admit_recovery(ns, job)
            ns["_composition_recovery_reconcile"](job["id"], body["recovery_request_id"])
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"], body["recovery_request_id"], Request({})))["status"], "rejected")
            self.assertTrue(job["queue_held"])

    def test_admitted_pristine_attempt_survives_crash_but_new_armed_attempt_holds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, _, project, pdigest = environment(root)
            job = interrupted_job(ns)
            body = admit_recovery(ns, job)
            # Crash between durable acceptance and starting the worker.
            actual = ns["_run_tool_editor_export"]
            ns["_run_tool_editor_export"] = lambda job_id: None
            with mock.patch.object(blender_mcp_service, "BlenderMCPService", Boundary), mock.patch.object(blender_mcp_transport, "StdioBlenderMCPClient", NativeClient):
                ns["_composition_recovery_reconcile"](job["id"],body["recovery_request_id"])
            job, resume = restore(ns, root, project, pdigest)
            self.assertTrue(resume)
            ns["_run_tool_editor_export"] = actual
            original = Boundary.invoke
            def arm_b(service, tool, args, *, cancelled):
                if tool == "render_animation":
                    raise RuntimeError("new attempt lost its native response")
                return original(service, tool, args, cancelled=cancelled)
            with mock.patch.object(blender_mcp_service, "BlenderMCPService", Boundary), mock.patch.object(blender_mcp_transport, "StdioBlenderMCPClient", NativeClient), mock.patch.object(Boundary, "invoke", arm_b):
                worker = threading.Thread(target=ns["_run_tool_composition_export"],args=(job["id"],))
                worker.start(); worker.join(10)
                self.assertFalse(worker.is_alive())
            job, resume = restore(ns, root, project, pdigest)
            self.assertFalse(resume)
            self.assertTrue(job["queue_held"])
            self.assertFalse(list(project.glob("composition_*.mp4")))

    def test_gone_app_and_blender_need_no_rpc_but_unreadable_identity_holds(self):
        from services.composition_recovery import process_identity, prove_stopped
        from services.composition_package import CompositionAttemptUnresolved
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(30)"])
            try:
                identity = process_identity(child.pid)
            finally:
                child.terminate(); child.wait(timeout=5)
            receipt = {"execution": {"worker": {"app":identity,"app_incarnation":"a"*32,"worker_token":"b"*32},"blender":identity,"encoder":None,"layout":{"frame_directory":str(root/("maestro_frames_"+"c"*32)),"encoder_destination":str(root/("maestro_"+"d"*32+".mp4"))}}}
            self.assertTrue(prove_stopped(receipt, idle_barrier=lambda _: self.fail("gone Blender must not contact RPC")))
            with mock.patch("services.composition_recovery.process_identity",side_effect=CompositionAttemptUnresolved("unreadable")), self.assertRaises(CompositionAttemptUnresolved):
                prove_stopped(receipt,idle_barrier=lambda identity: identity)

    def test_closed_route_scope_and_payload_fail_without_request_or_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            ns, coordinator, _, _ = environment(Path(temporary))
            job = new_job(ns)
            body = {"recovery_request_id":str(uuid.uuid4()),"expected_created_at":job["created_at"],"expected_execution_attempt":job["execution_attempt"],"confirmed":True}
            for change in ({"confirmed":False},{"expected_execution_attempt":True},{"recovery_request_id":"bad"},{"native_path":"private-injection"}):
                with self.subTest(change=change), self.assertRaises(HTTPException) as error:
                    asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request({**body,**change})))
                self.assertEqual(error.exception.status_code,400)
            original_access = ns["_require_project_access"]
            permissions = []
            def deny_edit(request, workspace, **kwargs):
                permissions.append(kwargs["permission"])
                if kwargs["permission"] == "project.mutate":
                    raise HTTPException(403)
                return original_access(request, workspace, **kwargs)
            ns["_require_project_access"] = deny_edit
            with self.assertRaises(HTTPException) as error:
                asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(body)))
            self.assertEqual(error.exception.status_code,403)
            self.assertEqual(permissions,["project.mutate"])
            ns["_require_owned_job_project"] = lambda *args: (_ for _ in []).throw(HTTPException(404))
            with self.assertRaises(HTTPException) as error:
                asyncio.run(ns["recover_blender_editor_composition"](job["id"],Request(body)))
            self.assertEqual(error.exception.status_code,404)
            self.assertNotIn("requests",job["recovery_cursor"]["composition"])
            self.assertNotIn("requests",next(iter(coordinator.restore().jobs.values()))["recovery_cursor"]["composition"])

    def test_cancel_during_barrier_has_terminal_fresh_journal_no_b_or_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ns, _, project, pdigest = environment(root)
            job = interrupted_job(ns)
            body = admit_recovery(ns, job)
            entered, release = threading.Event(), threading.Event()
            def barrier(service, expected=None):
                entered.set()
                self.assertTrue(release.wait(5))
                return expected
            Boundary.invocations = []
            with mock.patch.object(blender_mcp_service.BlenderMCPService, "composition_idle_probe", barrier), mock.patch.object(blender_mcp_transport, "StdioBlenderMCPClient", NativeClient):
                thread = threading.Thread(target=ns["_composition_recovery_reconcile"], args=(job["id"],body["recovery_request_id"]))
                thread.start()
                self.assertTrue(entered.wait(5))
                lifecycle.request_cancel(job, job_id=job["id"], active_states=ns["_active_gen_states"])
                release.set(); thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(job["status"], "cancelled")
            self.assertEqual(Boundary.invocations, [])
            self.assertFalse(list(project.glob("composition_*.mp4")))
            restored, resume = restore(ns, root, project, pdigest)
            self.assertEqual(restored["status"], "cancelled")
            self.assertFalse(resume)
            self.assertEqual(asyncio.run(ns["get_blender_editor_composition_recovery"](job["id"],body["recovery_request_id"],Request({})))["status"], "rejected")


class BlenderEditorCompositionPackageTests(unittest.TestCase):
    def test_repeated_segment_instances_have_distinct_ids_and_final_frame_clock(self):
        from services.composition_package import normalize_package
        with tempfile.TemporaryDirectory() as temporary:
            service = BlenderMCPService(object(), temporary)
            package = normalize_package(raw_package(), service)
            self.assertEqual(package["frame_count"], 54)
            self.assertEqual([clip["segment_id"] for clip in package["clips"]], ["A", "B", "A"])
            self.assertEqual(len({clip["id"] for clip in package["clips"]}), 3)
            self.assertEqual(len(package["segments"]), 2)

    def test_closed_package_rejects_unsupported_shapes_and_frame_references_before_native_use(self):
        from services.composition_package import normalize_package
        with tempfile.TemporaryDirectory() as temporary:
            service = BlenderMCPService(object(), temporary)
            cases = []
            def changed(section, key, value):
                package = raw_package()
                target = package if section is None else package[section]
                if isinstance(target, list): target = target[0]
                target[key] = value
                cases.append(package)
            changed(None, "code", "arbitrary code")
            changed("canvas", "fps", True)
            changed("canvas", "fps", 121)
            changed("segments", "fps", 241)
            changed("segments", "width", 127)
            changed("segments", "scene", {"clear_scene": False, "objects": []})
            changed("clips", "segment_id", "missing")
            changed("clips", "source_frame", 23)
            changed("clips", "frame_count", True)
            changed("clips", "id", "use-B")
            for index, package in enumerate(cases):
                with self.subTest(case=index), self.assertRaises(ValueError):
                    normalize_package(package, service)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg is required")
class BlenderEditorCompositionQueueTests(unittest.TestCase):

    def test_prepared_delivery_rejects_changed_bytes_binding_and_signature_without_native_resend(self):
        from services.composition_worker import load_delivery
        with tempfile.TemporaryDirectory() as temporary:
            ns, coordinator, project, pdigest = environment(Path(temporary))
            job = new_job(ns)
            package, directory, output_root, binding = ns["_composition_job_context"](job)
            prepare_delivery(package, Boundary(object(), directory), directory, output_root,
                binding=binding, secret=SECRET, probe=probe_media, cancelled=lambda: False,
                policy=job["access_policy"], native_context=lambda: nullcontext())
            before = len(Boundary.invocations)
            media = delivery_paths(directory, output_root, binding)[0][0]
            original = media.read_bytes()
            media.write_bytes(b"replaced")
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=binding, secret=SECRET)
            media.write_bytes(original)
            wrong_binding = {**binding, "owner_digest": "different-owner"}
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=wrong_binding, secret=SECRET)
            receipt = directory / "delivery.receipt.json"
            unsigned = json.loads(receipt.read_text()); unsigned["mac"] = "0" * 64
            receipt.write_text(json.dumps(unsigned))
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=binding, secret=SECRET)
            self.assertEqual(len(Boundary.invocations), before)

    def setUp(self):
        Boundary.invocations = []
        NativeSlot.admissions = 0
        NativeClient.closes = 0
        lifecycle.configure_durability_hook(None)
        self.addCleanup(lifecycle.configure_durability_hook, None)
        service_patch = mock.patch.object(blender_mcp_service, 'BlenderMCPService', Boundary)
        client_patch = mock.patch.object(blender_mcp_transport, 'StdioBlenderMCPClient', NativeClient)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def test_registered_private_worker_completed_and_terminal_after_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            ns, coordinator, project, pdigest = environment(Path(temp))
            job = new_job(ns)
            assert ns['_queue_recovery_worker'](job) == ns['_run_tool_editor_export']
            before = len(Boundary.invocations)
            assert ns['_run_tool_editor_export'](job['id']) and job['status'] == 'completed'
            final = project / job['output_files'][0]
            media = probe_media(str(final))
            assert media['duration'] == 2.25 and media['width'] == 128 and (media['height'] == 72) and media['has_audio']
            meta = json.loads(final.with_suffix('.meta.json').read_text())
            assert meta['private'] and meta['params'] is None and (len(meta['transform']['clips']) == 3)
            assert len(Boundary.invocations) - before == 6 and NativeSlot.admissions == 1
            self.assertEqual(NativeClient.closes, 1)
            reopened, resume = restore(ns, Path(temp), project, pdigest)
            assert reopened['status'] == 'completed' and (not resume)

    def test_sealed_delivery_crash_gaps_adopt_without_blender_or_native_resend(self):
        for gap in ['sidecar_only', 'media_before_terminal']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)
                assert lifecycle.try_start(job, phase='Rendering')
                package, directory, output_root, binding = ns['_composition_job_context'](job)
                service = Boundary(object(), directory)
                prepare_delivery(package, service, directory, output_root, binding=binding, secret=SECRET, probe=probe_media, cancelled=lambda: False, policy=job['access_policy'], native_context=lambda: nullcontext())

                class Crash(BaseException):
                    pass

                def crash(stage):
                    if stage == ('metadata_published' if gap == 'sidecar_only' else 'media_published'):
                        raise Crash()
                try:
                    publish_delivery(package, directory, output_root, binding=binding, secret=SECRET, cancelled=lambda: False, finish=lambda name: (_ for _ in []).throw(AssertionError('terminal reached before injected crash')), event=crash)
                except Crash:
                    pass
                else:
                    raise AssertionError('crash gap not reached')
                if gap == 'media_before_terminal':

                    def unavailable():
                        raise RuntimeError('substituted unavailable Blender')
                    ns['_require_blender_ready'] = unavailable
                    ns['_blender_runtime_info'] = unavailable
                reopened, resume = restore(ns, root, project, pdigest)
                assert resume and reopened['status'] == 'queued'
                before = len(Boundary.invocations)
                admissions = NativeSlot.admissions
                assert ns['_run_tool_editor_export'](reopened['id']) and reopened['status'] == 'completed'
                assert len(Boundary.invocations) == before and NativeSlot.admissions == admissions

    def test_later_unknown_segment_holds_before_earlier_segment_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            package, directory, output_root, binding = ns['_composition_job_context'](job)
            from services.composition_package import atomic_json, sign_receipt
            second = package['segments'][1]
            atomic_json(directory / 'B.receipt.json', sign_receipt({'package_sha256': digest(package), 'binding': binding, 'blender_mcp_revision': blender_mcp_service.PINNED_INSTALL.revision, 'segment_id': 'B', 'segment_sha256': digest(second), 'state': 'attempting', 'attempt_id': uuid.uuid4().hex}, SECRET))
            before = len(Boundary.invocations)
            reopened, resume = restore(ns, root, project, pdigest)
            assert not resume and reopened['queue_held'] and (len(Boundary.invocations) == before)

    def test_cancel_all_execution_phases_survives_fresh_journal(self):
        for phase in ['native_wait', 'native_scene', 'cpu_assembly', 'publication']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)
                before = len(Boundary.invocations)

                def cancel():
                    lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                if phase == 'native_wait':

                    class CancelSlot(NativeSlot):

                        def __enter__(self):
                            cancel()
                            return super().__enter__()
                    ns['_WgpNativeGpuExecutionSlot'] = CancelSlot
                    patch = nullcontext()
                elif phase == 'native_scene':
                    original = Boundary.invoke

                    def invoke(self, tool, args, *, cancelled):
                        result = original(self, tool, args, cancelled=cancelled)
                        if tool == 'scene_create':
                            cancel()
                        return result
                    patch = mock.patch.object(Boundary, 'invoke', invoke)
                elif phase == 'cpu_assembly':
                    original = composition_worker.render_video_sequence

                    def render(*args, **kwargs):
                        result = original(*args, **kwargs)
                        cancel()
                        return result
                    patch = mock.patch.object(composition_worker, 'render_video_sequence', render)
                else:
                    checkpoint = ns['_queue_recovery_checkpoint']

                    def transition(job, **kwargs):
                        if ((kwargs.get('recovery_cursor') or {}).get('composition') or {}).get('stage') == 'media_published':
                            cancel()
                        return checkpoint(job, **kwargs)
                    ns['_queue_recovery_checkpoint'] = transition
                    patch = nullcontext()
                with patch:
                    assert not ns['_run_tool_editor_export'](job['id'])
                assert job['status'] == 'cancelled' and (not job['output_files']) and (not ns['_active_gen_states'])
                assert not list(project.glob('composition_*.mp4')) and (not list(project.glob('composition_*.meta.json')))
                if phase == 'native_wait':
                    assert len(Boundary.invocations) == before
                reopened, resume = restore(ns, root, project, pdigest)
                assert reopened['status'] == 'cancelled' and (not resume)

    def test_scene_owner_can_acquire_generation_lock_while_worker_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            failed_attempt = threading.Event()
            lock = threading.RLock()

            class SceneLock:

                def acquire(self, **kwargs):
                    result = lock.acquire(**kwargs)
                    if not result and kwargs.get('blocking') is False:
                        failed_attempt.set()
                    return result

                def release(self):
                    lock.release()
            ns['_blender_scene_lock'] = SceneLock()
            lock.acquire()
            outcome = []
            worker = threading.Thread(
                target=lambda: outcome.append(ns['_run_tool_editor_export'](job['id'])),
                daemon=True,
            )
            ownership_proved = False
            try:
                worker.start()
                assert failed_attempt.wait(2), 'worker did not attempt the owned scene lock'
                acquired = ns['_gen_lock'].acquire(timeout=2)
                try:
                    assert acquired, 'composition held Gen while waiting for Scene'
                finally:
                    if acquired:
                        ns['_gen_lock'].release()
                ownership_proved = True
            finally:
                lock.release()
                if not ownership_proved:
                    lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                if worker.ident is not None:
                    worker.join(5)
                    if worker.is_alive():
                        lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                        worker.join(2)
            assert not worker.is_alive() and outcome == [True] and (job['status'] == 'completed')

    def test_missing_manifest_cannot_borrow_generic_adoption_or_revive_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            snapshot = next(iter(coordinator.restore().jobs.values()))
            snapshot['request_manifest'] = {'invalid': 'missing'}
            ns['_queue_recovery_final_adoption_jobs'] = {('scene', job['id']): {'state': 'adopted', 'declared': 1, 'adopted': 1, 'missing': 0, 'quarantined': 0, 'output_files': ['forged.mp4']}}
            reopened, resume = ns['_queue_recovery_materialize_job'](snapshot, {'scene': (str(project), pdigest)})
            assert reopened['queue_held'] and (not resume) and (not reopened['output_files'])
            for terminal in ['completed', 'failed', 'cancelled']:
                snapshot['status'] = terminal
                reopened, resume = ns['_queue_recovery_materialize_job'](snapshot, {'scene': (str(project), pdigest)})
                assert reopened['status'] == terminal and (not resume)

    def test_cancel_rollback_occurs_before_generation_and_scene_ownership_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            original = composition_worker.rollback_delivery
            observed = []

            def rollback(*args, **kwargs):
                assert ns['_gen_lock'].locked() and ns['_blender_scene_lock']._is_owned()
                observed.append('rollback-under-generation-and-scene-ownership')
                return original(*args, **kwargs)

            def cancel_finish(*args, **kwargs):
                lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                return False
            ns['finish_job'] = cancel_finish
            with mock.patch.object(composition_worker, 'rollback_delivery', rollback):
                assert not ns['_run_tool_editor_export'](job['id'])
            assert observed and job['status'] == 'cancelled' and (not list(project.glob('composition_*')))

    def test_old_worker_preserves_newer_completed_registry_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)

            def newer_winner(*args, **kwargs):
                newer = copy.deepcopy(job)
                newer['execution_attempt'] += 1
                newer['status'] = 'completed'
                newer['output_files'] = ['composition_' + job['id'] + '.mp4']
                ns['_jobs'][job['id']] = newer
                return False
            ns['finish_job'] = newer_winner
            assert not ns['_run_tool_editor_export'](job['id'])
            assert ns['_jobs'][job['id']]['status'] == 'completed' and len(list(project.glob('composition_*'))) == 2

    def test_old_exception_cannot_hold_newer_same_object_attempt(self):
        for error_type in [CompositionError, RuntimeError]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)

                @contextmanager
                def changed_attempt(lock, job):
                    try:
                        with lifecycle.generation_slot(lock, job) as acquired:
                            yield acquired
                    finally:
                        assert lifecycle.checkpoint_recovery_job(job, expected_execution_attempt=job['execution_attempt'], execution_attempt=job['execution_attempt'] + 1, status='running', queue_held=False)
                ns['generation_slot'] = changed_attempt

                def unavailable(*args, **kwargs):
                    raise error_type('substituted pre-native evidence failure')
                ns['_composition_job_context'] = unavailable
                before = len(Boundary.invocations)
                assert not ns['_run_tool_editor_export'](job['id'])
                assert job['status'] == 'running' and job['execution_attempt'] == 3 and (not job['queue_held'])
                assert len(Boundary.invocations) == before
