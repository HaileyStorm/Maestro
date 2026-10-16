"""Opt-in, model-owned graphs of the original narrow native tower decode step."""
from contextlib import contextmanager
import threading
import time
import types
import torch
from fish_speech.models.text2semantic.fish_omni import tower as native
BUCKETS = (256, 384)
PRIVATE_LIMIT = 256 * 1024 ** 2

def signature(values):
    if values is None:
        return None
    values = tuple(values)
    if any((not isinstance(t, torch.Tensor) for t in values)):
        return None
    return tuple(((id(t), t.data_ptr(), tuple(t.shape), tuple(t.stride()), t.dtype, t.device) for t in values))

class TowerGraph:

    def __init__(self, model, *, cancel_check=None, prefix_provider=None):
        self.model, self.tower, self.cancel_check, self.prefix_provider = (model, model.audio_decoder, cancel_check, prefix_provider)
        self.original = self.tower.decode_step
        if self.original.__func__ is not native.FishOmniAudioDecoder.decode_step or native.RAS_WINDOW_FRAMES != 16:
            raise RuntimeError('Expected unmodified native decode function and RAS window')
        self.lock, self.graphs = (threading.RLock(), {})
        self.closed = self.failed = False
        self.active_thread = None
        self.request_reason = 'outside_generation'
        self.pending_reason = None
        self.fallback_reasons = {}
        self.cache_buffers = None
        self.stream = self.capture_stream = None
        self.buffers, self.cache_signature, self.weight_signature = (None, None, None)
        self.output, self.candidates = ({}, None)
        self.stats = dict(setups=0, setup_seconds=0.0, replays=0, finite_flags_checked=0, host_finite_checks=0, sampler_draws=0, private_peak_reserved_growth_bytes=0, cache_rows_restored=0, rng_restores=0, prefix_state_verified=0, private_allocated_growth_bytes=0, generations=0, fallback_calls=0, cache_rebinds=0, capture_failures=0, graph_failures=0, cancellations=0)
        self.last_reason = None
        original_fn = self.original.__func__
        globals_copy = dict(original_fn.__globals__)
        globals_copy['sample_top_k_top_p'] = self._sample
        clone = types.FunctionType(original_fn.__code__, globals_copy, original_fn.__name__, original_fn.__defaults__, original_fn.__closure__)
        clone.__kwdefaults__ = dict(original_fn.__kwdefaults__ or {})
        self.cloned = types.MethodType(clone, self.tower)
        self.hook = self.decode
        self.original_setup = self.tower.setup_caches
        self.setup_hook = self._setup_caches
        self.tower.decode_step = self.hook
        self.tower.setup_caches = self.setup_hook

    def _cancel(self):
        if self.cancel_check is not None and self.cancel_check() is True:
            raise InterruptedError('Drama 3 generation cancelled')

    def _attentions(self):
        return [l.transformer_layer.attention for l in (*self.tower.shared_layers, *self.tower.residual_layers)]

    def _caches(self):
        return [v for a in self._attentions() for v in (a.k_cache, a.v_cache)]

    def _sample(self, logits, temperature, top_k, top_p, *, check_finite=True):
        self.draw_cursor += 1
        if check_finite:
            if self.flag_cursor >= 10:
                raise RuntimeError('Native finite-check count grew')
            self.flags[self.flag_cursor].copy_(torch.isfinite(logits).all())
            self.flag_cursor += 1
        return native.sample_top_k_top_p(logits, temperature, top_k, top_p, check_finite=False)

    def _body(self):
        self.flag_cursor = self.draw_cursor = 0
        value = self.cloned(self.logits, self.hidden, self.position, self.frame, self.history, temperature=0.8, top_k=30, top_p=0.8, check_finite=True)
        if (self.flag_cursor, self.draw_cursor) != (10, 11):
            raise RuntimeError('Native sampler/finite schema changed')
        return value

    def _drop_graphs(self):
        if self.stream is not None:
            with torch.cuda.device(self.stream.device):
                self.stream.synchronize()
                if self.capture_stream is not None:
                    self.capture_stream.synchronize()
                for graph in self.graphs.values():
                    graph.reset()
        self.graphs.clear()
        self.output.clear()
        self.buffers = self.cache_buffers = self.candidates = None
        self.cache_signature = self.weight_signature = None
        self.stream = self.capture_stream = None
        for name in ('logits', 'hidden', 'position', 'frame', 'history', 'flags'):
            setattr(self, name, None)

    def _setup_caches(self, *args, **kwargs):
        with self.lock:
            if self.graphs:
                self._drop_graphs()
                self.stats['cache_rebinds'] += 1
                if self.active_thread is not None:
                    self.request_reason = 'cache_rebound'
                else:
                    self.pending_reason = 'cache_rebound'
            return self.original_setup(*args, **kwargs)

    @contextmanager
    def generation(self, *, prefix_enabled, temperature, top_k, top_p):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Concurrent tower graph generation')
        if self.closed or self.active_thread is not None:
            self.lock.release()
            raise RuntimeError('Tower graph generation closed or nested')
        try:
            self.active_thread = threading.get_ident()
            self.stats['generations'] += 1
            self.request_reason = self.pending_reason
            self.pending_reason = None
            if not prefix_enabled:
                self.request_reason = 'prefix_cache_disabled'
            elif (temperature, top_k, top_p) != (0.8, 30, 0.8):
                self.request_reason = 'sampler_settings'
            elif not self.tower.main_logits_are_candidates:
                self.request_reason = 'compact_head_required'
            if self.graphs and signature(self.tower.parameters()) != self.weight_signature:
                self._drop_graphs()
                self.request_reason = 'weights_rebound'
            yield
        except BaseException:
            if self.failed:
                try:
                    self.close()
                except BaseException:
                    pass
            raise
        finally:
            self.active_thread = None
            self.request_reason = 'outside_generation'
            self.lock.release()

    def _fallback(self, reason, *args, **kwargs):
        self.stats['fallback_calls'] += 1
        self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1
        self.last_reason = reason
        return self.original(*args, **kwargs)

    @torch.inference_mode()
    def _setup(self, logits, hidden, frames):
        self._cancel()
        device = self.tower.device
        attentions = self._attentions()
        caches = self._caches()
        if device.type != 'cuda' or any((p.dtype != torch.bfloat16 or p.device != device for p in self.tower.parameters())) or len(attentions) != 13 or (self.tower.config.num_codebooks != 10) or (not self.tower.main_logits_are_candidates) or (self.model.decode_graphs is not None) or any((a.static_kv_len is not None for a in attentions)) or any((not isinstance(c, torch.Tensor) or c.device != device or c.shape[2] < max(BUCKETS) for c in caches)):
            return 'unsupported_tower_layout'
        prefix = self.prefix_provider() if self.prefix_provider is not None else None
        if prefix is None or prefix.active is None:
            return 'prefix_not_active'
        saved_prefix = (prefix.entry, prefix.active, dict(prefix.stats), prefix.owner_thread)
        with torch.cuda.device(device):
            torch.cuda.synchronize(device)
            started = time.monotonic()
            base_reserved = torch.cuda.memory_reserved(device)
            base_allocated = torch.cuda.memory_allocated(device)
            if torch.cuda.mem_get_info(device)[0] < PRIVATE_LIMIT:
                return 'insufficient_vram'
            self.stream = torch.cuda.current_stream(device)
            self.capture_stream = torch.cuda.Stream(device=device)
            self.logits = torch.zeros_like(logits)
            self.hidden = torch.zeros_like(hidden)
            self.position = torch.zeros(1, dtype=torch.long, device=device)
            self.frame = torch.zeros_like(frames)
            self.history = torch.full((16,), -1, dtype=torch.long, device=device)
            self.flags = torch.ones(10, dtype=torch.bool, device=device)
            self.buffers = (self.logits, self.hidden, self.position, self.frame, self.history, self.flags)
            self.input_signature = signature(self.buffers)
            saved_rows = [c[:, :, 0:1, :].clone() for c in caches]
            rng = torch.cuda.get_rng_state(device).clone()
            try:
                self.capture_stream.wait_stream(self.stream)
                with torch.cuda.stream(self.capture_stream):
                    for bucket in BUCKETS:
                        self._cancel()
                        for a in attentions:
                            a.static_kv_len = bucket
                        for _ in range(3):
                            warm = self._body()
                        del warm
                        self.capture_stream.synchronize()
                        graph = torch.cuda.CUDAGraph()
                        self.graphs[bucket] = graph
                        with torch.cuda.graph(graph, stream=self.capture_stream):
                            self.output[bucket] = self._body()
                        self.capture_stream.synchronize()
                        growth = torch.cuda.memory_reserved(device) - base_reserved
                        self.stats['private_peak_reserved_growth_bytes'] = max(self.stats['private_peak_reserved_growth_bytes'], growth)
                        allocated_growth = torch.cuda.memory_allocated(device) - base_allocated
                        self.stats['private_allocated_growth_bytes'] = max(self.stats['private_allocated_growth_bytes'], allocated_growth)
                        if max(growth, allocated_growth) > PRIVATE_LIMIT:
                            raise RuntimeError('Tower private graph memory growth exceeds256MiB')
                self.stream.wait_stream(self.capture_stream)
            finally:
                try:
                    self.capture_stream.synchronize()
                finally:
                    for a in attentions:
                        a.static_kv_len = None
                for cache, saved in zip(caches, saved_rows, strict=True):
                    cache[:, :, 0:1, :].copy_(saved)
                self.stream.synchronize()
                torch.cuda.set_rng_state(rng, device)
                self.stats['cache_rows_restored'] += len(caches)
                self.stats['rng_restores'] += 1
            if prefix.entry is not saved_prefix[0] or prefix.active is not saved_prefix[1] or prefix.stats != saved_prefix[2] or (prefix.owner_thread != saved_prefix[3]):
                raise RuntimeError('Capture changed production prefix state')
            self.stats['prefix_state_verified'] += 1
            self.candidates = self.tower.main_candidates
            self.cache_buffers = tuple(caches)
            self.cache_signature = signature(caches)
            self.weight_signature = signature(self.tower.parameters())
            self.stats['setups'] += 1
            self.stats['setup_seconds'] += time.monotonic() - started
            self._cancel()
        return None

    @torch.inference_mode()
    def decode(self, logits, hidden, positions, frames, history, *, temperature, top_k, top_p, check_finite=True):
        with self.lock:
            if self.closed or self.failed:
                raise RuntimeError('Tower graph adapter closed or failed')
            self._cancel()
            args = (logits, hidden, positions, frames, history)
            kwargs = dict(temperature=temperature, top_k=top_k, top_p=top_p, check_finite=check_finite)
            reason = self.request_reason
            if self.active_thread != threading.get_ident():
                reason = 'outside_generation'
            if not check_finite or (temperature, top_k, top_p) != (0.8, 30, 0.8) or tuple(logits.shape) != (1, 4097) or (tuple(hidden.shape) != (1, 2048)) or (tuple(positions.shape) != (1,)) or (tuple(frames.shape) != (1, 11)) or (history.ndim != 1) or (logits.dtype != torch.bfloat16) or (hidden.dtype != torch.bfloat16) or any((t.dtype != torch.long for t in (positions, frames, history))) or any((t.device != self.tower.device for t in args)):
                reason = reason or 'unsupported_decode_inputs'
            # Native generation slices history through position+1.
            position = len(history) - 1
            bucket = next((b for b in BUCKETS if b > position), None)
            if bucket is None or position < 0:
                reason = reason or 'position_outside_buckets'
            if reason:
                return self._fallback(reason, *args, **kwargs)
            if self.graphs and (self.model.decode_graphs is not None or torch.cuda.current_stream(self.tower.device) != self.stream or signature(self.buffers) != self.input_signature or (signature(self._caches()) != self.cache_signature) or (self.tower.main_candidates is not self.candidates) or any((a.static_kv_len is not None for a in self._attentions()))):
                self._drop_graphs()
                self.request_reason = 'graph_binding_changed'
                return self._fallback(self.request_reason, *args, **kwargs)
            setting_up = not self.graphs
            if setting_up:
                try:
                    reason = self._setup(logits, hidden, frames)
                except BaseException as error:
                    self.failed = True
                    if isinstance(error, InterruptedError):
                        self.stats['cancellations'] += 1
                        self.last_reason = 'cancelled'
                    else:
                        self.stats['capture_failures'] += 1
                        self.stats['graph_failures'] += 1
                        self.last_reason = 'capture_failed'
                    try:
                        self._drop_graphs()
                    except BaseException:
                        pass
                    raise
                if reason:
                    self.request_reason = reason
                    return self._fallback(reason, *args, **kwargs)
            try:
                self.logits.copy_(logits)
                self.hidden.copy_(hidden)
                self.position.copy_(positions)
                self.frame.copy_(frames)
                self.history.fill_(-1)
                tail = history[-16:]
                self.history[-len(tail):].copy_(tail)
                self.graphs[bucket].replay()
                value = self.output[bucket].clone()
                finite = bool(self.flags.all().item())
                self.stats['replays'] += 1
                self.stats['finite_flags_checked'] += 10
                self.stats['host_finite_checks'] += 1
                self.stats['sampler_draws'] += 11
                if not finite:
                    raise FloatingPointError('non-finite logits; refusing to sample invalid audio')
                self.last_reason = None
                self._cancel()
                return value
            except BaseException as error:
                self.failed = True
                if isinstance(error, InterruptedError):
                    self.stats['cancellations'] += 1
                    self.last_reason = 'cancelled'
                else:
                    self.stats['graph_failures'] += 1
                    self.last_reason = 'graph_execution_failed'
                try:
                    self._drop_graphs()
                except BaseException:
                    pass
                raise

    def snapshot(self):
        return dict(**self.stats, enabled=not self.closed, prepared=bool(self.graphs), failed=self.failed, buckets=list(self.graphs), supported_buckets=list(BUCKETS), last_reason=self.last_reason, fallback_reasons=dict(self.fallback_reasons))

    def close(self):
        with self.lock:
            if self.closed:
                return
            if self.tower.decode_step is not self.hook or self.tower.setup_caches is not self.setup_hook:
                raise RuntimeError('Tower graph hook owner changed')
            self._drop_graphs()
            self.tower.decode_step = self.original
            self.tower.setup_caches = self.original_setup
            self.model = self.tower = self.original = self.original_setup = self.cloned = self.prefix_provider = None
            self.closed = True
