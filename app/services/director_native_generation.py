"""Native Director child entrypoint; never imports the Maestro server.

The owning server supplies admitted tasks, retained seeds and a pre-dispatch
consumer binding over a private channel. It keeps authentication, output
promotion and finality. Importing this module does not load WGP or use CUDA.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading


# Never transfer the general services configuration: it can contain provider
# credentials. These fields affect the native runtime or existing model terms.
_CONFIG_FIELDS = frozenset({
    "attention_mode", "transformer_types", "transformer_quantization", "transformer_dtype_policy",
    "text_encoder_quantization", "lm_decoder_engine", "save_path",
    "image_save_path", "audio_save_path", "compile", "metadata_type", "boost",
    "enable_int8_kernels", "enable_4k_resolutions", "max_reserved_loras",
    "vae_config", "profile", "video_profile", "image_profile", "audio_profile",
    "checkpoints_paths", "loras_root", "vram_safety_coefficient", "rife_version",
    "video_output_codec", "video_container", "embed_source_images",
    "image_output_codec", "audio_output_codec", "audio_stand_alone_output_codec",
    "mmaudio_mode", "mmaudio_persistence", "matanyone_version",
    "flashvsr_mode", "flashvsr_persistence", "flashvsr_backend", "flashvsr_topk_ratio",
    "vae_precision", "mixed_precision", "enhancer_enabled", "enhancer_mode",
    "prompt_enhancer_quantization", "prompt_enhancer_temperature", "prompt_enhancer_top_p",
    "prompt_enhancer_randomize_seed", "preload_in_VRAM", "preload_model_policy",
    "depth_anything_v2_variant", "fit_canvas",
})
_SERVICE_FIELDS = frozenset({"host_terms_acceptance", "h3_operating_location", "krea_owner_policy"})
_RUNTIME_FIELDS = (
    "attention_mode", "transformer_quantization", "transformer_dtype_policy",
    "text_encoder_quantization", "default_profile", "default_profile_video",
    "default_profile_image", "default_profile_audio", "vae_config", "compile",
    "boost", "enable_int8_kernels", "save_path", "image_save_path", "audio_save_path",
    "processing_device",
)
_ARG_FIELDS = ("gpu", "vram_safety_coefficient", "perc_reserved_mem_max", "preload",
               "check_loras", "save_masks", "save_quantized", "save_speakers", "loras")
_GEN_FIELDS = (
    "file_list", "artifact_list", "file_settings_list", "audio_file_list",
    "audio_file_settings_list", "artifact_roles", "selected", "audio_selected",
    "prompt_no", "prompts_max", "repeat_no", "total_generation", "window_no",
    "total_windows", "progress_status", "process_status", "sliding_window",
    "extra_orders", "abort", "in_progress",
    "current_window_prompt", "progress_phase", "num_inference_steps", "prompt",
    "header_text", "extra_windows", "last_was_audio", "h3_audio_true_peak",
)


def runtime_binding(wgp, model_type):
    """Freeze selected native settings without handling provider credentials."""
    config = {key: copy.deepcopy(wgp.server_config[key])
              for key in _CONFIG_FIELDS if key in wgp.server_config}
    services = wgp.server_config.get("services") or {}
    config["services"] = {key: copy.deepcopy(services[key])
                          for key in _SERVICE_FIELDS if key in services}
    base = wgp.get_base_model_type(model_type)
    return {"config": config,
            "globals": {key: copy.deepcopy(getattr(wgp, key)) for key in _RUNTIME_FIELDS},
            "args": {key: copy.deepcopy(getattr(wgp.args, key)) for key in _ARG_FIELDS},
            "catalog": {name: copy.deepcopy(wgp.get_model_def(name)) for name in {base, model_type}}}


def _generation_state(gen):
    return {key: gen[key] for key in _GEN_FIELDS if key in gen}


def run_native_generation(wgp, task, params, *, identity, consumer, send_cmd,
                          plugin_data, retain_repeat, after_repeat,
                          validate_authority, cancelled, state_lock,
                          requested_outputs, on_started=None, on_abort=None,
                          invocation_fence=None):
    """Parent adapter for one admitted invocation, using existing producers.

    The caller retains its generation slot and exact lease until this returns.
    It must retain custody on NativeProcessError.process, and check the same
    invocation_fence inside producer publication critical sections. Neither a
    callback ACK nor the worker's result grants authority or authenticates media.
    """
    from services.director_native_process import run_native_process, NativeProcessError
    from services.native_acceptance_reservation import prepare_stdio_ownership, finish_stdio_ownership, reservation

    fence = invocation_fence if invocation_fence is not None else threading.Event()
    gen = params["state"]["gen"]
    profile_observer = params.get("_h3_profile_observer")
    style_observer = gen.get("_h3_reference_observer")
    segment_output = params.get("after_segment_output")
    worker_params = dict(params)
    worker_params["state"] = dict(params["state"])
    worker_params["state"]["gen"] = _generation_state(gen)
    if style_observer is not None:
        worker_params["state"]["gen"]["_h3_reference_observer"] = True
    if profile_observer is not None:
        worker_params["_h3_profile_observer"] = True
    if segment_output is not None:
        worker_params["after_segment_output"] = True
    worker_params["after_repeat_output"] = True if after_repeat is not None else None
    worker_params.pop("_maestro_director_lora_guard", None)
    worker_params.pop("_maestro_director_repeat_output", None)
    # Only these explicitly bound callbacks have replacements in the child.
    if any(callable(value) for value in worker_params.values()):
        raise NativeProcessError("unsupported_director_callback")
    worker_task = dict(task)
    worker_task["params"] = dict(task["params"])
    worker_task["params"]["state"] = worker_params["state"]
    packet = {"identity": copy.deepcopy(identity), "task": worker_task, "params": worker_params,
              "plugin_data": plugin_data, "runtime": runtime_binding(wgp, params["model_type"]),
              "consumer": consumer.worker_binding()}

    def current():
        if fence.is_set() or cancelled():
            raise NativeProcessError("cancelled")
        return consumer.validate_worker_binding()

    def mirror(payload, fields):
        if (type(payload) is not dict or set(payload) != fields
                or payload["identity"] != identity or type(payload.get("gen")) is not dict
                or set(payload["gen"]) - set(_GEN_FIELDS)):
            raise NativeProcessError("director_callback_binding")
        with state_lock:
            current()
            aborted = bool(gen.get("abort"))
            gen.update({key: value for key, value in payload["gen"].items()
                        if key not in {"abort", "extra_orders"}})
            gen["abort"] = aborted or bool(payload["gen"].get("abort"))
            # Live controls use the durable requested count, never a stale
            # child copy of the delta WGP consumed at its repeat boundary.
            total = gen.get("total_generation", params.get("repeat_generation", 1))
            target = requested_outputs()
            if type(total) is not int or type(target) is not int:
                raise NativeProcessError("director_output_count")
            gen["extra_orders"] = target - total

    def callback_context(role, operation, callback_thread):
        if role != "on_request" or operation not in {"retain_preparation", "retain_repeat"}:
            return nullcontext()
        token = reservation.capture_director_callback(identity, fence, callback_thread,
            role=role, operation=operation)
        return reservation.director_callback(token)

    def on_request(name, payload):
        if name == "validate_current":
            if payload != identity:
                raise NativeProcessError("director_callback_binding")
            return current()
        if name == "retain_preparation":
            if (type(payload) is not dict or set(payload) != {"identity", "record"}
                    or payload["identity"] != identity):
                raise NativeProcessError("director_callback_binding")
            current()
            return consumer.retain_worker_preparation(payload["record"])
        mirror(payload, {"identity", "payload", "gen"})
        data = payload["payload"]
        if name == "retain_repeat":
            if type(data) is not dict or set(data) != {"receipt", "media_paths", "terminal_paths"}:
                raise NativeProcessError("director_repeat_binding")
            value = retain_repeat(data["receipt"], data["media_paths"], terminal_paths=data["terminal_paths"])
        elif name == "after_repeat" and data is None and after_repeat is not None:
            value = after_repeat()
        elif name == "after_segment" and segment_output is not None:
            if type(data) is not dict or set(data) != {"path", "clip_info"}:
                raise NativeProcessError("director_segment_binding")
            value = segment_output(data["path"], data["clip_info"])
        elif name == "profile_observation" and profile_observer is not None:
            if type(data) is not tuple:
                raise NativeProcessError("director_observation_binding")
            value = profile_observer(*data)
        elif name in {"style_begin", "style_fail", "style_encoded"} and style_observer is not None:
            method = {"style_begin": style_observer.begin, "style_fail": style_observer.fail,
                      "style_encoded": style_observer}[name]
            value = method(data)
        else:
            raise NativeProcessError("unsupported_director_request")
        with state_lock:
            current()
            return {"value": value, "gen": _generation_state(gen)}

    def on_event(name, payload):
        mirror(payload, {"identity", "values", "gen"})
        values = payload["values"]
        if type(values) is not tuple or len(values) > 1 or name == "exit":
            raise NativeProcessError("director_event_binding")
        # The parent supplies exit only after process drain, never on an
        # untrusted event that could release its generation slot early.
        current()
        send_cmd(name, *values)

    # Use the existing guardian's exclusive intent/birth/ACK protocol. An
    # uncertain spawn keeps its intent and cannot be retried as a fresh call.
    argv, entry = prepare_stdio_ownership(("-c",
        "import runpy;runpy.run_module('services.director_native_generation',run_name='__main__')"))
    environment = dict(os.environ)
    # The child has its private invocation authority. The server reservation
    # must not be imported as a second server or mint a duplicate startup ACK.
    environment.pop("MAESTRO_NATIVE_ACCEPTANCE_PLAN", None)
    environment.pop("MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256", None)
    custody_unresolved = False
    try:
        result = run_native_process(packet, command=[sys.executable, *argv],
            cwd=str(Path(wgp.__file__).resolve().parent), environment=environment,
            on_request=on_request, on_event=on_event, cancelled=cancelled,
            validate_authority=validate_authority, on_started=on_started,
            on_abort=on_abort, invocation_fence=fence, callback_context=callback_context)
        if (type(result) is not dict or set(result) != {"identity", "success", "gen"}
                or type(result["success"]) is not bool):
            raise NativeProcessError("director_result_binding")
        mirror(result, {"identity", "success", "gen"})
        return result["success"]
    except NativeProcessError as error:
        custody_unresolved = getattr(error, "process", None) is not None
        raise
    finally:
        if not custody_unresolved:
            finish_stdio_ownership(entry)


# This helper starts before torch import. It watches the exact server and child
# pidfds plus channel hangup independently of the native interpreter's GIL.
# All its descendants are members of the child-created session, not Maestro's.
_WATCHER = """
import os,select,signal,sys
server,worker,group,channel,stop,ready=map(int,sys.argv[1:])
pidfd_open=getattr(os,'pidfd_open',None)
if pidfd_open is None:
    import ctypes
    native_pidfd_open=ctypes.CDLL(None,use_errno=True).pidfd_open
    native_pidfd_open.argtypes=[ctypes.c_int,ctypes.c_uint]
    native_pidfd_open.restype=ctypes.c_int
    def pidfd_open(pid):
        fd=native_pidfd_open(pid,0)
        if fd<0: raise OSError(ctypes.get_errno(),'pidfd_open failed')
        return fd
poll=select.poll()
for pid in (server,worker):
    fd=pidfd_open(pid);poll.register(fd,select.POLLIN)
poll.register(channel,select.POLLHUP|select.POLLERR|select.POLLRDHUP)
poll.register(stop,select.POLLIN|select.POLLHUP)
os.write(ready,b'R');os.close(ready)
for fd,events in poll.poll():
    if fd==stop and events & select.POLLIN and os.read(stop,1)==b'X':
        sys.exit(0)
    os.killpg(group,signal.SIGKILL)
"""


def _start_parent_watch(channel):
    # The watcher handshake proves both pidfds opened, including on Python
    # builds without os.pidfd_open but with the same Linux libc interface.
    if (sys.platform != "linux"
            or os.getpgrp() != os.getpid() or os.getsid(0) != os.getpid()):
        raise RuntimeError("Director native child requires its own supported Linux session")
    reader, writer = os.pipe()
    ready_reader, ready_writer = os.pipe()
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _WATCHER,
             str(os.getppid()), str(os.getpid()), str(os.getpgrp()),
             str(channel.socket.fileno()), str(reader), str(ready_writer)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(reader, ready_writer, channel.socket.fileno()), close_fds=True,
        )
        os.close(ready_writer)
        ready_writer = None
        if (not select.select([ready_reader], [], [], 2)[0]
                or os.read(ready_reader, 1) != b"R"):
            raise RuntimeError("Director parent watcher did not start")
    except BaseException:
        os.close(writer)
        raise
    finally:
        os.close(reader)
        os.close(ready_reader)
        if ready_writer is not None:
            os.close(ready_writer)
    return process, writer


def _deny_download(*args, **kwargs):
    raise RuntimeError("Director native child requires installed assets")


def _installed_model_only(wgp, model_filename=None, model_type=None,
                          file_type=0, submodel_no=1, force_path=None):
    if model_filename in (None, ""):
        # WGP also calls this shared-assets preparation hook without a
        # primary filename. Installed ancillary loaders still require their
        # files; that hook must never acquire the broad download bundle.
        return
    selected = wgp.get_compatible_local_model_filename(
        model_filename, model_type, file_type=file_type, extra_paths=force_path)
    if not selected or not Path(selected).is_file():
        raise RuntimeError("Director native asset is not installed")


def run_worker(channel, packet):
    """Run once; report only bounded source metadata after owned cleanup."""
    stage, failure = ["validate_packet"], []
    try:
        return _run_worker(channel, packet, stage, failure)
    except BaseException as exc:
        failed_stage, failed_exc = failure[0] if failure else (stage[0], exc)
        try:
            channel.fail(failed_stage, failed_exc, trusted_source=__file__)
        except BaseException:
            pass  # Socket loss still requires the parent's independent custody drain.
        raise SystemExit(1) from None


def _run_worker(channel, packet, stage, failure):
    if (type(packet) is not dict or set(packet) != {"identity", "task", "params", "plugin_data", "runtime", "consumer"}
            or type(packet["identity"]) is not dict
            or not {"job_id", "execution_attempt", "task_index", "child_digest", "task_digest"} <= set(packet["identity"])):
        raise RuntimeError("Director native invocation binding is invalid")
    identity, consumer_binding, runtime = packet["identity"], packet["consumer"], packet["runtime"]
    plan = consumer_binding["plan"]
    if any(identity[key] != plan[key] for key in ("child_digest", "task_digest")):
        raise RuntimeError("Director native invocation changed its consumer")
    params = packet["params"]
    # Native object callbacks require their own explicit child-local bindings.
    # Unknown/private observers and media dispatchers are never silently dropped.
    if any(callable(value) for value in params.values()):
        raise RuntimeError("Director native callback has no child binding")
    if any(params.get(key) is not None for key in (
            "_h3_decode_observer",
            "_h3_cumulative_dispatch", "_h3_timeline_guides", "_h3_control_dispatch",
            "_h3_face_refine_dispatch", "_h3_face_refine_output")):
        raise RuntimeError("Director native observer or dispatcher has no child binding")
    callback_markers = {key: params.pop(key, None) for key in (
        "after_repeat_output", "after_segment_output", "_h3_profile_observer")}
    style_marker = params["state"]["gen"].pop("_h3_reference_observer", None)
    if any(value is not None and value is not True
           for value in (*callback_markers.values(), style_marker)):
        raise RuntimeError("Director native callback marker is invalid")
    if (set(runtime["config"]) - _CONFIG_FIELDS - {"services"}
            or set(runtime["config"].get("services", {})) - _SERVICE_FIELDS
            or set(runtime["globals"]) != set(_RUNTIME_FIELDS)
            or set(runtime["args"]) != set(_ARG_FIELDS)):
        raise RuntimeError("Director native configuration has unknown fields")
    stage[0] = "parent_watch"
    watcher, watcher_stop = _start_parent_watch(channel)
    wgp = None
    try:
        # Authenticate the exact projection and any completed prefix before
        # importing the GPU runtime. A generic RPC ACK cannot establish it.
        stage[0] = "consumer_binding"
        from services.director_video_lora_execution import DirectorLoraConsumer
        consumer = DirectorLoraConsumer(plan,
            transformer_count=consumer_binding["transformer_count"],
            validate_current=lambda: channel.request("validate_current", identity),
            retain_preparation=lambda record: channel.request("retain_preparation", {"identity": identity, "record": record}))
        consumer.restore_completed_prefix(validate_completed=lambda: consumer_binding["completed_repeats"])
        stage[0] = "private_runtime"
        with tempfile.TemporaryDirectory(prefix="maestro-director-native-") as private:
            os.chmod(private, 0o700)
            config_dir = Path(private) / "config"
            settings_dir = Path(private) / "settings"
            config_dir.mkdir(mode=0o700)
            settings_dir.mkdir(mode=0o700)
            target = config_dir / "wgp_config.json"
            with target.open("x", encoding="utf-8") as stream:
                os.chmod(target, 0o600)
                json.dump(runtime["config"], stream, allow_nan=False)
            os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
            sys.argv = ["maestro-director-native", "--config", str(config_dir), "--settings", str(settings_dir),
                        "--gpu", runtime["args"]["gpu"], "--vram-safety-coefficient",
                        str(runtime["args"]["vram_safety_coefficient"])]
            stage[0] = "wgp_import"
            import wgp as native_wgp
            wgp = native_wgp
            stage[0] = "runtime_binding"
            wgp.app = None
            if wgp.processing_device != runtime["globals"]["processing_device"]:
                raise RuntimeError("Director native startup selected a different device")
            # No model or ancillary acquisition is authorized by this child.
            wgp.hf_download_with_public_fallback = _deny_download
            wgp.snapshot_download = _deny_download
            wgp.download_file = _deny_download
            wgp.download_models = lambda *args, **kwargs: _installed_model_only(wgp, *args, **kwargs)
            for key, value in runtime["globals"].items():
                setattr(wgp, key, value)
            for key, value in runtime["args"].items():
                setattr(wgp.args, key, value)
            if any(wgp.get_model_def(name) != definition for name, definition in runtime["catalog"].items()):
                raise RuntimeError("Director native catalog changed during child startup")
            state = params["state"]
            gen = state["gen"]

            def state_rpc(name, payload):
                response = channel.request(name, {"identity": identity, "payload": payload, "gen": _generation_state(gen)})
                # The server returns its promotions and live repeat edits.
                aborted = bool(gen.get("abort"))
                gen.update(response["gen"])
                if aborted:
                    gen["abort"] = True
                return response["value"]

            def retain_repeat(index, media_paths, *, terminal_paths):
                windows = sum(key[0] == index for key in consumer._windows)
                receipt = consumer.completed_repeat(index, expected_windows=windows)
                return state_rpc("retain_repeat", {"receipt": receipt,
                    "media_paths": media_paths, "terminal_paths": terminal_paths})

            if callback_markers["after_repeat_output"] is True:
                def after_repeat():
                    # The scheduler may yield only after this child's residency
                    # is actually released; no idle hardware assumption.
                    wgp.release_model()
                    return state_rpc("after_repeat", None)
                params["after_repeat_output"] = after_repeat
            if callback_markers["after_segment_output"] is True:
                params["after_segment_output"] = lambda path, clip_info: state_rpc(
                    "after_segment", {"path": path, "clip_info": clip_info})
            if callback_markers["_h3_profile_observer"] is True:
                params["_h3_profile_observer"] = lambda *event: state_rpc("profile_observation", event)
            if style_marker is True:
                class StyleObservation:
                    def begin(self, context):
                        return state_rpc("style_begin", context)

                    def fail(self, code):
                        return state_rpc("style_fail", code)

                    def __call__(self, payload):
                        return state_rpc("style_encoded", payload)

                gen["_h3_reference_observer"] = StyleObservation()
            params["_maestro_director_lora_guard"] = consumer
            params["_maestro_director_repeat_output"] = retain_repeat
            def send_cmd(name, *values):
                channel.send_event(name, {"identity": identity, "values": values, "gen": _generation_state(gen)})
            stage[0] = "generation"
            success = wgp.generate_video(packet["task"], send_cmd,
                plugin_data=packet["plugin_data"], **params)
            final = {"identity": identity, "success": bool(success), "gen": _generation_state(gen)}
            stage[0] = "model_release"
            wgp.release_model()
            stage[0] = "finish"
            channel.finish(final)
    except BaseException as exc:
        failure.append((stage[0], exc))
        raise
    finally:
        stage[0] = "cleanup"
        # Keep the independent watcher until native cleanup has returned.
        if wgp is not None:
            wgp.release_model()
        os.write(watcher_stop, b"X")
        os.close(watcher_stop)
        watcher.wait(timeout=2)


if __name__ == "__main__":
    from services.director_native_process import worker_channel_from_fd
    native_channel = worker_channel_from_fd()
    try:
        native_packet = native_channel.receive()
    except BaseException as exc:
        try:
            native_channel.fail("bootstrap", exc, trusted_source=__file__)
        except BaseException:
            pass
        raise SystemExit(1) from None
    run_worker(native_channel, native_packet)
