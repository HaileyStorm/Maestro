"""Model-free transactional prompt-plan validation."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from services.h3_execution_contract import rewrite_h3_execution_prompts, validate_h3_execution_shots
from services.h3_shot_planner import plan_h3_native_shots, seal_h3_shot_plan, validate_h3_shot_plan_seal
from services.queue_recovery_runtime import QueueRecoveryRuntimeError


class H3ExecutionContractTests(unittest.TestCase):
    def plan(self):
        return plan_h3_native_shots(
            global_prompt='A person says <d>[English] Keep  these words.</d>',
            clip_frame_counts=[121], fps=24)

    def reject_unchanged(self, plan, prompts, *, validator=lambda *args: None):
        before = copy.deepcopy(plan)
        with self.assertRaises((QueueRecoveryRuntimeError, ValueError, TypeError)):
            rewrite_h3_execution_prompts(plan, prompts, validate_prompt=validator)
        self.assertEqual(plan, before)

    def test_canonicalized_raw_source_keeps_ownership_during_schema_rewrite(self):
        source = "  [0s-5s] The book opens. <d>[English]  Keep  this. </d>\n[5s-10s] The book closes.  "
        plan = plan_h3_native_shots(
            global_prompt=source, clip_frame_counts=[120, 120], fps=24,
            source_canonicalization="t2va",
            structured_shots=[{"spatial_setup": "The book starts closed.",
                               "closing_blocking": "The book stays closed."}],
        )
        before = copy.deepcopy(plan)
        result = rewrite_h3_execution_prompts(
            plan, plan["clip_prompts"], validate_prompt=lambda *args: None,
        )
        self.assertEqual(plan, before)
        self.assertEqual(result["source_contracts"][0]["authored_prompt"], source)
        self.assertEqual(result["event_ownership"], plan["event_ownership"])
        self.assertEqual(result["dialogue_manifest"], plan["dialogue_manifest"])
        self.assertEqual(sum(p.count("<d>[English]  Keep  this. </d>") for p in result["clip_prompts"]), 1)
        self.reject_unchanged(plan, [p.replace("The book opens.", "The book burns.") for p in plan["clip_prompts"]])

    def test_shared_dispatch_preserves_optional_legacy_paths_and_copy_isolation(self):
        self.assertIsNone(validate_h3_execution_shots(None, [], 0))
        self.assertIsNone(validate_h3_execution_shots({}, [], 0))
        self.assertIsNone(validate_h3_execution_shots({'shot_plan': {}}, [], 0))
        plan = self.plan()
        before = copy.deepcopy(plan)
        shots = validate_h3_execution_shots({'shot_plan': plan}, plan['clip_prompts'], 1)
        shots[0]['prompt'] = 'Changed returned copy'
        self.assertEqual(plan, before)

    def test_success_validates_copy_and_updates_all_executable_mirrors(self):
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                plan = self.plan()
                if serialized:
                    plan = json.loads(json.dumps(plan))
                before = copy.deepcopy(plan)
                prompts = [plan['clip_prompts'][0] + '\nA light changes.']
                calls = []
                result = rewrite_h3_execution_prompts(plan, prompts, validate_prompt=lambda *args: calls.append(args))
                self.assertEqual(plan, before)
                self.assertIsNot(result, plan)
                self.assertEqual(calls, [(0, prompts[0])])
                self.assertEqual(result['shots'][0]['prompt'], prompts[0])
                for name in ('source_contracts', 'semantic_shots'):
                    self.assertEqual(result[name][0]['executable_prompt_sha256'], [hashlib.sha256(prompts[0].encode()).hexdigest()])
                validate_h3_shot_plan_seal(result)
                self.assertEqual(len(validate_h3_execution_shots({'shot_plan': result}, prompts, 1)), 1)

    def test_validator_cannot_swap_the_caller_prompt_list_after_validation(self):
        plan = self.plan()
        mapped = plan['clip_prompts'][0] + '\nA light changes.'
        prompts = [mapped]
        def validate(index, prompt):
            self.assertEqual(prompt, mapped)
            prompts[index] = 'Unvalidated replacement'
        result = rewrite_h3_execution_prompts(plan, prompts, validate_prompt=validate)
        self.assertEqual(result['clip_prompts'], [mapped])

    def test_preexisting_drift_cannot_be_resealed(self):
        plan = self.plan()
        plan['global_prompt'] = 'Changed after review'
        self.reject_unchanged(plan, plan['clip_prompts'])

    def test_malformed_contract_is_rejected_without_partial_update(self):
        for field, value in [('segment_indices', ['invalid']), ('event_ownership', None), ('dialogue_manifest', None)]:
            with self.subTest(field=field):
                plan = self.plan()
                plan['source_contracts'][0][field] = value
                seal_h3_shot_plan(plan)
                self.reject_unchanged(plan, ['new prompt'])

    def test_target_validation_failure_or_unconfirmed_result_is_atomic(self):
        def fail(*args):
            raise ValueError('target invalid')
        for validator in (fail, lambda *args: ['target invalid'], lambda *args: False):
            plan = self.plan()
            self.reject_unchanged(plan, plan['clip_prompts'], validator=validator)

    def test_dialogue_bytes_order_and_multiplicity_cannot_change(self):
        plan = self.plan()
        prompt = plan['clip_prompts'][0]
        literal = '<d>[English] Keep  these words.</d>'
        for changed in (prompt.replace(literal, ''), prompt.replace('Keep  these', 'Keep these'), prompt + ' ' + literal):
            self.reject_unchanged(plan, [changed])

    def test_forged_dialogue_owner_is_not_accepted_even_with_matching_seal(self):
        for mutation in ('mirror', 'source', 'index', 'ordinal', 'words'):
            plan = self.plan()
            if mutation == 'mirror':
                plan['dialogue_manifest'] = []
            elif mutation == 'source':
                plan['source_contracts'][0]['dialogue_manifest'][0]['source_index'] = 99
            elif mutation == 'index':
                plan['shots'][0]['dialogue_manifest_indices'] = [False]
            elif mutation == 'ordinal':
                plan['source_contracts'][0]['dialogue_manifest'][0]['semantic_occurrence_index'] = 99
            else:
                plan['source_contracts'][0]['dialogue_manifest'][0]['spoken_text'] = 'Forged words'
            seal_h3_shot_plan(plan)
            self.reject_unchanged(plan, plan['clip_prompts'])

    def test_non_dialogue_owned_event_cannot_be_removed(self):
        plan = plan_h3_native_shots(global_prompt='A person walks.', clip_frame_counts=[121], fps=24)
        self.reject_unchanged(plan, ['A different action.'])

    def test_multisource_serialized_plan_retains_intentional_repeated_dialogue(self):
        literal = '<d>[English] Again.</d>'
        plan = plan_h3_native_shots(
            global_prompt='Two sources.', clip_frame_counts=[121,121,121], fps=24,
            source_indices=[0,0,1],
            source_prompts=[f'A person says {literal} then {literal}', f'Another person says {literal}'])
        plan = json.loads(json.dumps(plan))
        before = copy.deepcopy(plan)
        result = rewrite_h3_execution_prompts(plan, plan['clip_prompts'], validate_prompt=lambda *a: None)
        self.assertEqual(plan, before)
        self.assertEqual(result['dialogue_manifest'], plan['dialogue_manifest'])
        self.assertEqual(len(result['dialogue_manifest']), 3)
        self.assertEqual(len(validate_h3_execution_shots({'shot_plan':result},result['clip_prompts'],3)),3)

    def test_event_identity_and_continuation_corruption_is_rejected(self):
        for field, value in [('source_index', 99), ('semantic_shot_index', 99),
                             ('authored_shot_id', 'forged'), ('event_id', 'forged'),
                             ('published_start_frame', 99)]:
            plan = plan_h3_native_shots(global_prompt='[0s-10s] A person walks.', clip_frame_counts=[121,121], fps=24)
            plan['source_contracts'][0]['event_ownership'][0][field] = value
            seal_h3_shot_plan(plan)
            self.reject_unchanged(plan, plan['clip_prompts'])
        plan = plan_h3_native_shots(global_prompt='[0s-10s] A person walks.', clip_frame_counts=[121,121], fps=24)
        before = copy.deepcopy(plan)
        result = rewrite_h3_execution_prompts(plan, plan['clip_prompts'], validate_prompt=lambda *a: None)
        self.assertEqual(plan, before)
        self.assertEqual(result['event_ownership'], plan['event_ownership'])
        plan['event_ownership'][0]['continuation_slices'][0]['local_start_frame'] = 2
        seal_h3_shot_plan(plan)
        self.reject_unchanged(plan, plan['clip_prompts'])

    def test_coherent_event_content_forgery_is_rejected_against_semantic_source(self):
        plan = plan_h3_native_shots(global_prompt='A person walks.\nB person jumps.', clip_frame_counts=[121], fps=24)
        plan['source_contracts'][0]['event_ownership'][0]['executable_payload'] = 'B person jumps.'
        seal_h3_shot_plan(plan)
        self.reject_unchanged(plan, plan['clip_prompts'])

    def test_mapping_cannot_duplicate_or_move_an_owned_event(self):
        plan = plan_h3_native_shots(global_prompt='[0s-10s] A person walks.', clip_frame_counts=[121,121], fps=24)
        prompts = list(plan['clip_prompts'])
        prompts[0] += ' A person walks.'
        self.reject_unchanged(plan, prompts)
        prompts = list(plan['clip_prompts'])
        prompts[1] += ' A person walks.'
        self.reject_unchanged(plan, prompts)

    def test_count_mismatch_and_legacy_plan_are_explicitly_rejected(self):
        plan = self.plan()
        self.reject_unchanged(plan, [])
        plan['semantic_physical_contract_version'] = 1
        self.reject_unchanged(plan, plan['clip_prompts'])


if __name__ == '__main__':
    unittest.main()
