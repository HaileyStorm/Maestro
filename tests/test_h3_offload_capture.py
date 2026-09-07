"""CPU-only producer/consumer checks for ephemeral loaded-profile evidence."""
from __future__ import annotations

import ast
import copy
import functools
import gc
import inspect
from pathlib import Path
import sys
import types
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from services.h3_benchmark import H3OffloadObservation
from services.h3_oom_relief import H3OomReliefRetry


def function(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def calls(node, name):
    return any(isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
               and item.func.id == name for item in ast.walk(node))


class WgpCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((APP / 'wgp.py').read_text())

    def run_generation(self, profiles, *, repeats=None, fail_load=False, retry=False, live=True, reused=False, mismatch=False, reprofile=False, plain_repeats=1, extra_repeat=False, fail_before_geometry=False):
        wrapper = copy.deepcopy(function(self.tree, 'generate_video'))
        impl = copy.deepcopy(function(self.tree, '_generate_video_impl'))
        helper = copy.deepcopy(function(self.tree, '_notify_h3_profile_observer'))
        split_assignment, split_if = impl.body[:2]
        notifications = {node.value.args[1].value: node for node in impl.body
                         if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                         and isinstance(node.value.func, ast.Name)
                         and node.value.func.id == '_notify_h3_profile_observer'}
        state_init = next(node for node in impl.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == '_h3_profile_load_state'
                                  for target in node.targets))
        load_decision = copy.deepcopy(next(node for node in impl.body if isinstance(node, ast.If)
                                         and 'profile != loaded_profile' in ast.unparse(node.test)))
        load_decision.body = ast.parse('_simulate_load()').body
        request_snapshot = next(node for node in impl.body if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id == '_h3_observation_request'
                                        for target in node.targets))
        load_snapshots = [node for node in impl.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id in {
                              '_h3_observed_loaded_model', '_h3_observed_loaded_profile'} for target in node.targets)]
        geometry_guard = next(node for node in impl.body if isinstance(node, ast.If)
                              and 'initial_total_windows != 1' in ast.unparse(node.test))
        impl.body = [split_assignment, split_if, notifications['execution'], request_snapshot,
                     *ast.parse('profile = override_profile').body, state_init,
                     load_decision, *load_snapshots, *ast.parse('_before_geometry()').body,
                     geometry_guard, *ast.parse('return _leaf(locals())').body]
        wraps = next(node for node in self.tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'generate_video' for t in node.targets)
                     and isinstance(node.value, ast.Call))
        collector = H3OffloadObservation('minimax_h3')
        observed = []
        executions = []
        gen = {}
        namespace = {
            'inspect': inspect, 'gc': gc, 'functools': functools,
            'torch': types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
            'get_default_profile': lambda output: 4,
            'get_output_type_for_model': lambda model: 'video',
            'get_gen_info': lambda state: gen,
            'transformer_type': 'minimax_h3' if reused else None,
            'loaded_profile': (4 if mismatch else 4.5) if reused else None,
            'wan_model': object() if reused else None, 'offloadobj': object() if reused else None,
            'reload_needed': False, 'configuration_reprofiled': reprofile,
            'initial_total_windows': 1, 'first_window_video_length': 128, 'width': 960, 'height': 544,
            'generation_residency_must_yield_for_postprocess': lambda value: False,
        }

        def load():
            index = len(executions)
            executions.append(index)
            if fail_load:
                raise RuntimeError('synthetic load failure')
            namespace.update(transformer_type='minimax_h3', loaded_profile=profiles[index],
                             wan_model=object() if live else None,
                             offloadobj=object() if live else None, reload_needed=False)

        repeat_guard = next(node for node in ast.walk(function(self.tree, '_generate_video_impl'))
                            if isinstance(node, ast.If) and ast.unparse(node.test) == 'repeat_no > 1'
                            and calls(node, '_notify_h3_profile_observer'))

        def leaf(arguments):
            for repeat_no in range(1, int(arguments.get('repeat_generation') or 1) + int(extra_repeat) + 1):
                scope = {**namespace, 'repeat_no': repeat_no, 'model_type': 'minimax_h3',
                         '_h3_profile_observer': collector}
                exec(compile(ast.Module(body=[repeat_guard], type_ignores=[]), 'native-repeat-capture', 'exec'), scope)
            observed.append(collector.profile)
            self.assertIs(arguments['_h3_profile_observer'], collector)
            if retry and len(observed) == 1:
                raise H3OomReliefRetry({'override_profile': 5.0})
            # Teardown may leave the numeric global stale; the collector must
            # retain its task-local capture without consulting the global later.
            namespace.update(wan_model=None, offloadobj=None, loaded_profile=1)
            return True

        def before_geometry():
            if fail_before_geometry:
                raise RuntimeError('synthetic preprocessing failure')

        namespace.update(_simulate_load=load, _leaf=leaf, _before_geometry=before_geometry)
        code = ast.Module(body=[helper, wrapper, impl, wraps], type_ignores=[])
        exec(compile(ast.fix_missing_locations(code), 'wgp-capture-boundary', 'exec'), namespace)
        public = namespace['generate_video']
        self.assertEqual(inspect.signature(public), inspect.signature(namespace['_generate_video_impl']))
        kwargs = {name: None for name, param in inspect.signature(public).parameters.items()
                  if param.default is inspect.Parameter.empty}
        kwargs.update(task={'params': {}}, send_cmd=lambda *args: None, model_type='minimax_h3',
                      resolution='960x544', override_profile=4, num_inference_steps=20, video_length=128, batch_size=1,
                      state={}, seed=7, repeat_generation=plain_repeats, _h3_profile_observer=collector)
        if repeats is not None:
            kwargs.update(repeat_generation=repeats, after_repeat_output=lambda: True)
        limits = types.ModuleType('services.h3_host_limits')
        limits.record_denoise_success = lambda **kwargs: None
        with patch.dict(sys.modules, {'services.h3_host_limits': limits}):
            if fail_load or fail_before_geometry:
                with self.assertRaisesRegex(RuntimeError, 'synthetic .* failure'):
                    public(**kwargs)
            else:
                self.assertTrue(public(**kwargs))
        self.assertNotIn('_h3_profile_observer', kwargs['task']['params'])
        return collector.profile, observed, collector.load_state

    def test_single_load_and_nested_single_repeat_preserve_exact_profile_after_teardown(self):
        for repeats in (None, 1):
            with self.subTest(repeats=repeats):
                self.assertEqual(self.run_generation([4.5], repeats=repeats), (4.5, [4.5], 'cold'))

    def test_resident_snapshot_does_not_label_reload_or_reprofile_as_warm(self):
        self.assertEqual(self.run_generation([4.5], reused=True)[2], 'resident')
        self.assertEqual(self.run_generation([4.5], reused=True, mismatch=True)[2], 'cold')
        self.assertEqual(self.run_generation([4.5], reused=True, reprofile=True)[2], 'cold')

    def test_missing_residency_and_load_failure_cannot_create_observations(self):
        self.assertEqual(self.run_generation([4.5], live=False)[0], None)
        self.assertEqual(self.run_generation([], fail_load=True)[0], None)
        self.assertIsNone(self.run_generation([4.5], fail_before_geometry=True)[0])

    def test_retry_and_multiple_repeats_do_not_relabel_aggregate_work(self):
        self.assertIsNone(self.run_generation([4.5, 5], retry=True)[0])
        self.assertIsNone(self.run_generation([4.5, 5], repeats=2)[0])
        self.assertIsNone(self.run_generation([4.5, 4.5], repeats=2)[0])
        self.assertIsNone(self.run_generation([4.5], plain_repeats=2)[0])
        self.assertIsNone(self.run_generation([4.5], extra_repeat=True)[0])

    def test_final_geometry_and_aggregate_guards_discard_inexact_benchmark_work(self):
        impl = function(self.tree, '_generate_video_impl')
        guard = next(node for node in impl.body if isinstance(node, ast.If)
                     and 'initial_total_windows != 1' in ast.unparse(node.test))
        window_guard = next(node for node in ast.walk(impl) if isinstance(node, ast.If)
                            and ast.unparse(node.test) == 'window_no > 1'
                            and calls(node, '_notify_h3_profile_observer'))
        helper = function(self.tree, '_notify_h3_profile_observer')
        baseline = dict(initial_total_windows=1, batch_size=1, width=960, height=544,
                        first_window_video_length=125, num_inference_steps=20,
                        _h3_observation_request=('960x544', 125, 20), model_type='minimax_h3',
                        _h3_observed_loaded_model='minimax_h3', _h3_observed_loaded_profile=4.5,
                        _h3_profile_load_state='resident', transformer_type='minimax_h3', loaded_profile=4.5,
                        wan_model=object(), offloadobj=object(), reload_needed=False)
        for changed in ({}, {'first_window_video_length': 141}, {'width': 1024},
                        {'num_inference_steps': 4}, {'initial_total_windows': 2}, {'batch_size': 2},
                        {'loaded_profile': 5}, {'transformer_type': 'minimax_h3_ref2va'}):
            with self.subTest(changed=changed):
                observation = H3OffloadObservation('minimax_h3')
                observation('execution', 'minimax_h3')
                namespace = {**baseline, **changed, '_h3_profile_observer': observation}
                exec(compile(ast.Module(body=[helper, guard], type_ignores=[]), 'geometry-capture', 'exec'), namespace)
                self.assertEqual(observation.profile, None if changed else 4.5)
                if not changed:
                    namespace['window_no'] = 2
                    exec(compile(ast.Module(body=[window_guard], type_ignores=[]), 'extra-window-capture', 'exec'), namespace)
                    self.assertIsNone(observation.profile)

    def test_callback_is_removed_after_settings_overrides_before_output_formatting(self):
        impl = function(self.tree, '_generate_video_impl')
        blocks = [node for node in ast.walk(impl) if isinstance(node, ast.Expr)
                  and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                  and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'inputs'
                  and node.value.func.attr == 'pop' and node.value.args
                  and isinstance(node.value.args[0], ast.Constant)
                  and node.value.args[0].value == '_h3_profile_observer']
        self.assertEqual(len(blocks), 1)
        inputs = {'prompt': 'synthetic', '_h3_profile_observer': object()}
        exec(compile(ast.Module(body=blocks, type_ignores=[]), 'private-observer-removal', 'exec'), {'inputs': inputs})
        self.assertEqual(inputs, {'prompt': 'synthetic'})
        source = (APP / 'wgp.py').read_text()
        self.assertLess(source.index('inputs.update(overridden_inputs)'), blocks[0].col_offset + source.index('inputs.pop("_h3_profile_observer"'))


class LaunchCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((APP / 'launch.py').read_text())

    def helper_namespace(self):
        helpers = [function(self.tree, name) for name in (
            '_h3_observed_offload_profile', '_h3_task_offload_profile', '_h3_allocation_observation_scenario')]
        namespace = {'QueueRecoveryRuntimeError': ValueError,
                     '_h3_allocation_scenario': lambda params, **kwargs: {
                         'model_type': params['model_type'], 'frame_count': kwargs['frame_count'],
                         'offload_profile': 4}}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'allocation-observation', 'exec'), namespace)
        return namespace

    def test_launch_injects_its_collector_without_changing_task_settings(self):
        run = function(self.tree, '_run_generation')
        factory = next(node for node in ast.walk(run) if isinstance(node, ast.FunctionDef)
                       and node.name == 'make_error_handler')
        timing = {}
        received = []
        messages = []

        def generate(task, send_cmd, model_type, resolution, video_length, num_inference_steps, repeat_generation, batch_size, _h3_profile_observer=None, plugin_data=None):
            self.assertIsInstance(_h3_profile_observer, H3OffloadObservation)
            received.append(_h3_profile_observer)
            for event in ('reset', 'execution'):
                _h3_profile_observer(event, model_type)
            _h3_profile_observer('loaded', model_type, 4.5, 'cold')
            return True

        namespace = {
            'inspect': inspect, 'time': time, '_h3_call_timing': timing,
            'worker_start_lock': threading.Lock(), 'worker_started': threading.Event(),
            'worker_start_state': {'cancelled': False},
            'task_h3_turbo_validation_authorized': False,
            '_H3_LONG_STUDIO_MODELS': {'minimax_h3'},
            '_h3_model_is_resident': lambda model: True,
            'wgp': types.SimpleNamespace(generate_video=generate),
            '_run_generation_task_with_llm_exclusion': lambda model, send, call: call(),
        }
        exec(compile(ast.Module(body=[factory], type_ignores=[]), 'launch-observer-injection', 'exec'), namespace)
        params = {'model_type': 'minimax_h3', 'resolution': '960x544', 'video_length': 128, 'num_inference_steps': 20, 'repeat_generation': 1, 'batch_size': 1, '_h3_profile_observer': 'untrusted-placeholder'}
        task = {'params': copy.deepcopy(params)}
        handler = namespace['make_error_handler'](task, params, lambda *message: messages.append(message), timing)
        later_task_timing = {}
        namespace['_h3_call_timing'] = later_task_timing
        handler()
        self.assertEqual(later_task_timing, {})
        self.assertEqual(len(received), 1)
        self.assertEqual(timing['offload_profile'], 4.5)
        self.assertEqual(timing['model_load_state'], 'cold')
        self.assertEqual(task['params'], params)
        self.assertEqual(params['_h3_profile_observer'], 'untrusted-placeholder')
        self.assertEqual(messages[-1], ('exit', None))

    def test_observed_profile_is_bound_to_invocation_model_and_geometry(self):
        resolve = self.helper_namespace()['_h3_task_offload_profile']
        params = {'model_type': 'minimax_h3', 'resolution': '960x544', 'video_length': 128, 'num_inference_steps': 20, 'repeat_generation': 1, 'batch_size': 1}
        timing = {'offload_profile': 4.5, 'offload_context': dict(params)}
        self.assertEqual(resolve(params, timing), 4.5)
        for field, value in [('model_type', 'minimax_h3_ref2va'), ('resolution', '1344x768'),
                             ('video_length', 256), ('num_inference_steps', 50), ('repeat_generation', 2), ('batch_size', 2)]:
            with self.subTest(field=field):
                self.assertIsNone(resolve({**params, field: value}, timing))
        for resolution in ('auto', '', None, '0x544'):
            changed = {**params, 'resolution': resolution}
            self.assertIsNone(resolve(changed, {'offload_profile': 4.5, 'offload_context': changed}))

    def test_allocation_capture_uses_observed_fraction_not_requested_integer(self):
        namespace = self.helper_namespace()
        make = namespace['_h3_allocation_observation_scenario']
        params = {'model_type': 'minimax_h3', 'override_profile': 4}
        self.assertEqual(make(params, frame_count=128, observed_profile=4.5)['offload_profile'], 4.5)
        for profile in (None, True, '4.5', float('nan')):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                make(params, frame_count=128, observed_profile=profile)

    def test_first_failure_cannot_borrow_later_task_params_or_profile(self):
        run = function(self.tree, '_run_generation')
        capture = next(node for node in ast.walk(run) if isinstance(node, ast.If)
                       and isinstance(node.test, ast.Name) and node.test.id == 'is_first_failure_task')
        record_try = min((node for node in ast.walk(run) if isinstance(node, ast.Try)
                          and any(isinstance(item, ast.Name) and item.id == 'failed_profile'
                                  for item in ast.walk(node))
                          and calls(node, '_h3_allocation_observation_scenario')),
                         key=lambda node: node.end_lineno - node.lineno)
        namespace = self.helper_namespace()
        records = []
        namespace.update(copy=copy, job={}, first_failure_context=None,
                         _h3_allocation_outcome_is_clean=lambda job: (True, ''),
                         _get_h3_allocation_ledger=lambda: types.SimpleNamespace(
                             record=lambda scenario, *args, **kwargs: records.append(scenario)))
        for first, model, frames, profile in ((True, 'minimax_h3_ref2va', 128, 4.5),
                                              (False, 'minimax_h3', 256, 5)):
            params = {'model_type': model, 'video_length': frames, 'resolution': '960x544', 'num_inference_steps': 20, 'repeat_generation': 1, 'batch_size': 1}
            namespace.update(is_first_failure_task=first, task={'params': params},
                             _h3_call_timing={'offload_profile': profile, 'offload_context': dict(params)})
            exec(compile(ast.Module(body=[capture], type_ignores=[]), 'first-failure-capture', 'exec'), namespace)
        exec(compile(ast.Module(body=[record_try], type_ignores=[]), 'first-failure-record', 'exec'), namespace)
        self.assertEqual(records, [{'model_type': 'minimax_h3_ref2va', 'frame_count': 128, 'offload_profile': 4.5}])
        records.clear()
        namespace['first_failure_context'] = ({'model_type': 'minimax_h3', 'video_length': 128}, None)
        exec(compile(ast.Module(body=[record_try], type_ignores=[]), 'unknown-failure-record', 'exec'), namespace)
        self.assertEqual(records, [])

    def test_benchmark_capture_requires_observation_and_uses_it_in_spec(self):
        import services.h3_benchmark as benchmark
        namespace = self.helper_namespace()
        namespace.update(sys=sys, _H3_LONG_STUDIO_MODELS={'minimax_h3'},
                         _H3_REF2VA_MODEL='minimax_h3_ref2va', _H3_W4A8_FL2VA_MODEL='minimax_h3_w4a8_fl2va',
                         _H3_PEAK_RECOVERY_POLICY_VERSION=2,
                         _h3_benchmark_input_signature=lambda params, case_id: {},
                         wgp=types.SimpleNamespace(get_model_def=lambda model: {}),
                         torch=types.SimpleNamespace(__version__='synthetic', version=types.SimpleNamespace(cuda=None),
                             cuda=types.SimpleNamespace(is_available=lambda: False)))
        node = function(self.tree, '_record_h3_benchmark_observation')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'benchmark-observation', 'exec'), namespace)
        record = namespace[node.name]
        captured = Mock(side_effect=RuntimeError('captured synthetic spec'))
        params = {'model_type': 'minimax_h3', 'resolution': '960x544', 'video_length': 128,
                  'num_inference_steps': 20, 'repeat_generation': 1, 'batch_size': 1, 'override_profile': 4, 'custom_settings': {'h3_attention_engine': 'sdpa'}}
        with patch.object(benchmark, 'build_benchmark_spec', captured):
            record(params, wall_time_seconds=1, output_files=[], out_dir='synthetic')
            captured.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'captured synthetic spec'):
                record(params, wall_time_seconds=1, output_files=[], out_dir='synthetic', observed_profile=4.5)
        self.assertEqual(captured.call_args.kwargs['task']['offload_profile'], 4.5)
        self.assertEqual(captured.call_args.kwargs['task']['recovery_policy_version'], 2)


if __name__ == '__main__':
    unittest.main()
