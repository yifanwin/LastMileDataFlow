import copy
import json
import math
from pathlib import Path
import unittest

from lastmile_dataflow.construction.case_schema import (CaseEditRequest, CaseTemplate, CheckResult,
    Condition, ContractError, GenerationConfig, ParameterSpec, Review, RunResult, required_checks_pass)
from lastmile_dataflow.construction.dsl import ExecutableDSL, SymbolicDSL, relative_quaternion
from case_edit_helpers import template, proposal


class Contracts(unittest.TestCase):
    def test_roundtrip_fixtures(self):
        classes = {'request': CaseEditRequest, 'template': CaseTemplate, 'symbolic': SymbolicDSL,
                   'executable': ExecutableDSL, 'review': Review, 'result': RunResult}
        for name, cls in classes.items():
            value = json.loads((Path(__file__).parent / 'fixtures/case_edit' / (name + '.json')).read_text())
            parsed = cls.from_dict(value)
            self.assertEqual(parsed.to_dict(), cls.from_json(json.dumps(parsed.to_dict())).to_dict())

    def test_strict_errors(self):
        for invalid in (float('nan'), float('inf'), True, '1'):
            with self.assertRaises(ContractError):
                GenerationConfig(timeout_s=invalid)
        for invalid in ({'bogus': 1}, {1: 'x'}, {'target_scene_count': 0}):
            with self.assertRaises(ContractError):
                GenerationConfig.from_dict(invalid)
        value = template().to_dict()
        for ref in ('target', '$missing', '$target..x'):
            bad = copy.deepcopy(value)
            bad['requirements'][0]['args'][0] = ref
            with self.assertRaises(ContractError):
                CaseTemplate.from_dict(bad)
        for source in ('inferred', None):
            with self.assertRaises(ContractError):
                ParameterSpec(source=source, range=[1, 2])
        with self.assertRaises(ContractError):
            CaseTemplate.from_dict({**value, 'protected_roles': ['support']})

    def test_unknown_cannot_pass(self):
        self.assertFalse(required_checks_pass([CheckResult('support', 'unknown')]))
        with self.assertRaises(ContractError):
            Review('s1', 'pass', [CheckResult('visible', 'unknown')])
        with self.assertRaises(ContractError):
            Review('s1', 'pass', [CheckResult('visible', 'pass', required=False)])
        with self.assertRaises(ContractError):
            RunResult('r1', 'completed', 1)

    def test_additive_and_endpoint_conflicts(self):
        p = proposal()
        self.assertEqual(len(p.effective_requirements(template())), 1)
        p2 = SymbolicDSL('p2', p.bindings, [{'op': 'remove', 'subject': '$target'}],
                         goals=[Condition('supported', ['$target'], value=False)])
        self.assertEqual(len(p2.effective_requirements(template())), 2)
        self.assertTrue(p2.semantic_conflicts(template()))
        add = {'op': 'add', 'bind_as': '$new', 'asset_selector': {'category': ['box']},
               'search_space': {'support': '$support'}}
        self.assertFalse(SymbolicDSL('p3', p.bindings, [add],
                         goals=[Condition('supported', ['$new'], value=True)]).semantic_conflicts(template()))
        self.assertTrue(SymbolicDSL('p3', p.bindings, [add],
                         invariants=[Condition('supported', ['$new'], value=True)]).semantic_conflicts(template()))
        with self.assertRaises(ContractError):
            SymbolicDSL('p4', p.bindings, [{'op': 'remove', 'subject': '$new'}, add])

    def test_rotation_is_relative_and_frame_explicit(self):
        before = [math.sqrt(.5), 0, 0, math.sqrt(.5)]
        world = relative_quaternion(before, [1, 0, 0], math.pi/2, 'world')
        local = relative_quaternion(before, [1, 0, 0], math.pi/2, 'object')
        self.assertAlmostEqual(world[2], -.5)
        self.assertAlmostEqual(local[2], .5)
        self.assertEqual(relative_quaternion(before, [0, 0, 1], 0), before)
        with self.assertRaises(ContractError):
            relative_quaternion(before, [0, 0, 0], 1)
        exe = ExecutableDSL('p', 's', [{'op': 'remove', 'instance': 'target'}])
        self.assertEqual(exe.to_runtime_operations()[0]['op'], 'delete')

    def test_parameter_references(self):
        value = template().to_dict()
        value['parameters'] = {'distance': {'source': 'heuristic_default', 'range': [1., 2.]}}
        value['requirements'].append({'predicate': 'distance_xy', 'args': ['$target', '$support'],
                                       'range': {'parameter': 'distance'}})
        self.assertEqual(CaseTemplate.from_dict(value).parameters['distance'].source, 'heuristic_default')
        value['parameters'] = {}
        with self.assertRaises(ContractError):
            CaseTemplate.from_dict(value)


if __name__ == '__main__':
    unittest.main()
