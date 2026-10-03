"""Shared-layer contract tests: annotation, preflight rejection, frozen-base source, rank.

These are synthetic-protocol regressions. They prove program behaviour (annotation vocabulary,
preflight gating, measurement-source rules, decision grammar) and are not RBY-1 or grab evidence.
"""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest

import mujoco
import numpy as np
from helpers import fixtures
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.construction.config import BuildConfig, BuildProtocol
from lastmile_dataflow.construction import cases
from lastmile_dataflow.construction.candidates import candidates
from lastmile_dataflow.runtime.build_session import BuildSession
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.agents.protocol import observe, parse_decision, DecisionGateway
from lastmile_dataflow.workflows.build import run_build

SCENE = '''<mujoco><compiler angle="radian"/><option integrator="implicitfast"/>
<worldbody><geom name="floor" type="plane" size="0 0 .01"/>
<body name="table" pos="2 0 .4"><geom name="top" type="box" size=".5 .4 .04"/></body>
<body name="target" pos="2.03 0 .50"><freejoint name="target_free"/><geom name="target_geom" type="box" size=".04 .04 .05" mass=".1"/></body>
<body name="neighbor" pos="2.35 .20 .49"><freejoint name="neighbor_free"/><geom name="neighbor_geom" type="box" size=".02 .02 .05" mass=".1"/></body>
</worldbody></mujoco>'''

SIDES = {'frame_body': 'table', 'side_a': [0, -.7, -.4], 'side_b': [0, .7, -.4],
         'min_distance_difference_m': .25, 'clearance_radius_m': .8,
         'min_clearance_difference_m': .1}


def tabletop(root, case_type='case1', parameters=None, base=(2.03, 0), variant=0):
    source, robot = fixtures(root)
    text = SCENE.replace('pos="2.03 0 .50"', f'pos="{base[0] + variant * .03} {base[1]} .50"')
    Path(source.xml_path).write_text(text)
    config = BuildConfig('2.0', 'shared-layer', case_type, 'target', 'table', robot_base=[0, 0, 0],
                         editable=['target'],
                         parameters=parameters or {'distance_range_m': [2.15, 2.4]},
                         protocol=BuildProtocol(settle_s=.5, max_settle_s=1, window_s=.2))
    collection = CollectionConfig(output_dir=str(Path(root) / 'outputs'), max_steps=2, record_video=False)
    return source, robot, config, collection


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def test_every_requirement_is_annotated_with_strength_and_layer(self):
        for case_type, parameters in (('case1', {'distance_range_m': [2.15, 2.4]}),
                                      ('case1.5', dict(SIDES))):
            source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()), case_type, parameters)
            session = BuildSession(source, robot, config, collection)
            try:
                records = cases.requirements(session)
                self.assertTrue(records, case_type)
                for record in records:
                    self.assertIn(record['strength'], cases.STRENGTHS, record)
                    self.assertIn(record['layer'], cases.LAYERS, record)
                    self.assertIsInstance(record['evidence'], dict)
                summary = cases.requirement_summary(records)
                self.assertEqual(summary['status'], 'pass' if not any(
                    r['status'] != 'pass' and r['required'] for r in records) else 'fail')
            finally: session.close()

    def test_case1_distance_uses_frozen_base_and_does_not_change_scene_validity(self):
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()))
        session = BuildSession(source, robot, config, collection)
        try:
            session.settle(); check = session.validate()
            distance = next(r for r in check['requirements'] if r['requirement'] == 'target_robot_horizontal_distance')
            self.assertEqual(distance['layer'], 'case_intent')
            self.assertEqual(distance['strength'], 'geometric_measurement')
            self.assertEqual(distance['status'], 'fail')
            self.assertEqual(distance['failure_kind'], 'intent_failure')
            source_record = next(r for r in check['requirements']
                                 if r['requirement'] == 'distance_measured_from_frozen_initial_base')
            self.assertEqual(source_record['status'], 'pass')
            # A failing case_intent requirement must not be reported as an invalid scene.
            self.assertEqual(check['scene_valid'], True)
            self.assertFalse(check['valid'])
            self.assertEqual(check['requirement_summary']['case_intent_failures'], 1)
            self.assertEqual(check['requirement_summary']['engineering_failures'], 0)
        finally: session.close()

    def test_frozen_initial_base_bypass_is_an_engineering_failure(self):
        """Required-fault probe (design 5.B): stale/rough distance measured from the live pose."""
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()))
        session = BuildSession(source, robot, config, collection)
        try:
            session.settle()
            # Move the live robot without touching the frozen base: this is exactly the bypass.
            address = session.sim.robot.addresses['base_x']
            session.sim.data.qpos[address] += .5
            mujoco.mj_forward(session.sim.model, session.sim.data)
            check = session.validate()
            record = next(r for r in check['requirements']
                          if r['requirement'] == 'distance_measured_from_frozen_initial_base')
            self.assertEqual(record['status'], 'fail')
            self.assertEqual(record['reason'], 'measurement_source_inconsistent')
            self.assertEqual(record['failure_kind'], 'engineering_failure')
            self.assertEqual(record['layer'], 'scene_validity')
            self.assertEqual(check['scene_valid'], False)
            self.assertEqual(check['requirement_summary']['engineering_failures'], 1)
        finally: session.close()

    def test_case15_side_point_inside_furniture_rejects_before_any_candidate(self):
        """Required-fault probe: a side vector pointing into the furniture must not generate one."""
        parameters = dict(SIDES); parameters['side_a'] = [0., 0., -.4]
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()), 'case1.5', parameters)
        session = BuildSession(source, robot, config, collection)
        try:
            session.settle()
            records = cases.preflight(session)
            outside = next(r for r in records
                           if r['requirement'] == 'side_point_outside_furniture_footprint::side_a')
            self.assertEqual(outside['status'], 'fail')
            self.assertEqual(outside['reason'], 'side_vector_points_inside_furniture')
            self.assertEqual(outside['layer'], 'case_intent')
            self.assertEqual(candidates(session), [])
            session.config = config
            path = run_build(source, robot, config, collection, images=False, regression=False,
                             build_id='preflight-rejected')
            result = read_json(path / 'result.json')
            self.assertEqual(result['status'], 'failed')
            self.assertIn('case_preflight_rejected', result['error'])
            preflight = read_json(path / 'preflight.json')
            self.assertEqual(preflight['candidates_generated'], 0)
            self.assertTrue(preflight['rejected'])
        finally: session.close()

    def test_case15_three_sides_and_roles_are_accepted_and_labelled(self):
        parameters = dict(SIDES)
        parameters.update({'side_c': [.9, 0, -.4],
                           'side_roles': {'side_a': 'narrow', 'side_b': 'far', 'side_c': 'solvable'}})
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()), 'case1.5', parameters)
        session = BuildSession(source, robot, config, collection)
        try:
            session.settle()
            case, frames = cases.constructor(session)
            records = case.local_check(session.sim, frames, [])
            difference = next(r for r in records
                              if r['requirement'] == 'frame_bound_side_distance_difference')
            self.assertEqual(difference['evidence']['side_labels'], ['side_a', 'side_b', 'side_c'])
            self.assertEqual(difference['evidence']['side_roles']['side_c'], 'solvable')
            self.assertIn('side_vectors_local', difference['evidence'])
            clearance = next(r for r in records
                             if r['requirement'] == 'frame_bound_side_clearance_difference')
            self.assertEqual(clearance['strength'], 'geometric_proxy')
            self.assertIn('not_navigation', clearance['evidence']['method'])
            with self.assertRaises(ValueError):
                replace(config, parameters={**parameters, 'side_roles': {'side_d': 'narrow'}})
        finally: session.close()

    def test_zero_edit_branch_is_explicit_and_reaches_full_validation(self):
        source, robot, config, collection = tabletop(
            Path(tempfile.mkdtemp()), parameters={'distance_range_m': [1.4, 2.6]})
        path = run_build(source, robot, config, collection, images=False, regression=False,
                         build_id='zero-edit')
        result = read_json(path / 'result.json')
        self.assertEqual(result['status'], 'candidate_ready_regression_pending')
        self.assertIs(result['edited'], False)
        self.assertEqual(result['cost']['request_cumulative']['edits'], 0)
        candidate = read_json(path / 'task_candidate.json')
        self.assertIs(candidate['edited'], False)
        self.assertEqual(candidate['results']['case_condition']['status'], 'unknown')
        # The scene is still fully validated and frozen even with zero edits.
        self.assertTrue((path / 'handoff.json').is_file())
        self.assertEqual(read_json(path / 'handoff.json')['independent_restore_exact'], True)
        self.assertIn('scenario_identity', result)

    def test_edited_branch_is_marked_and_rank_is_same_dimension(self):
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()))
        path = run_build(source, robot, config, collection, images=False, regression=False,
                         build_id='edited')
        result = read_json(path / 'result.json')
        self.assertEqual(result['status'], 'candidate_ready_regression_pending')
        self.assertIs(result['edited'], True)
        self.assertGreater(result['cost']['request_cumulative']['edits'], 0)
        ranks = [read_json(p) for p in sorted((path / 'decisions').glob('*.json'))]
        self.assertTrue(ranks)
        self.assertEqual(ranks[0]['source'], 'rule')
        self.assertIs(ranks[0]['rule_fallback_selection'], True)

    def test_rule_fallback_and_budget_exhaustion_are_distinguishable(self):
        source, robot, config, collection = tabletop(Path(tempfile.mkdtemp()))
        path = run_build(source, robot, config, collection, images=False, regression=False,
                         build_id='degraded')
        degraded = read_json(path / 'result.json')
        self.assertEqual(degraded['decision_source'], 'rule')
        self.assertEqual(degraded['degradation']['path'], 'rule')
        self.assertEqual(degraded['degradation']['agent_calls_used'], 0)
        exhausted = run_build(source, robot, replace(config, budget=replace(config.budget, candidates=0)),
                             collection, images=False, regression=False, build_id='exhausted')
        result = read_json(exhausted / 'result.json')
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertNotIn('degradation', result)


class DecisionGrammarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        source, robot, config, collection = tabletop(self.root)
        self.session = BuildSession(source, robot, config, collection)
        self.session.settle()
        # No GL context in the offline regression: diagrams are matplotlib/Agg, camera renders are
        # covered by the real smoke runs.
        self.packet = observe(self.session, candidates(self.session), self.root / 'obs')

    def tearDown(self): self.session.close(); self.tmp.cleanup()

    def test_rank_must_cover_all_candidates_shortlist_may_subset(self):
        base = {'revision': self.packet['revision'], 'observation_id': self.packet['observation_id']}
        ids = [c['candidate_id'] for c in self.packet['candidates']]
        self.assertTrue(len(ids) >= 2)
        ranked = parse_decision({'action': 'rank', 'candidate_ids': ids[::-1], **base}, self.packet)
        self.assertEqual(ranked['candidate_ids'], ids[::-1])
        shortlist = parse_decision({'action': 'shortlist', 'candidate_ids': ids[:1], **base}, self.packet)
        self.assertEqual(shortlist['candidate_ids'], ids[:1])
        for bad in ({'action': 'rank', 'candidate_ids': ids[:-1], **base},
                    {'action': 'shortlist', 'candidate_ids': ['missing'], **base},
                    {'action': 'rank', 'candidate_ids': [ids[0], ids[0]], **base}):
            with self.assertRaises(ValueError): parse_decision(bad, self.packet)

    def test_case_specific_view_is_offered_and_rendered_schematic(self):
        """Case 1's candidate-distribution view is a schematic diagram, never a VLA input."""
        from lastmile_dataflow.agents.protocol import case_views, diagram
        self.assertEqual(self.packet['case_type'], 'case1')
        self.assertEqual(self.packet['frames']['available'], True)
        self.assertIn('diagnostic_distribution', case_views(self.session))
        entry = diagram(self.session, self.root / 'diagnostic', 'diagnostic_distribution',
                        self.packet['candidates'], 'settled')
        self.assertIs(entry['vla_input'], False)
        self.assertIs(entry['schematic'], True)
        self.assertIs(entry['diagnostic'], True)
        self.assertIs(entry['available'], True)
        self.assertTrue(Path(entry['path']).is_file())

    def test_gateway_counts_ranking_and_selection_separately(self):
        base = {'revision': self.packet['revision'], 'observation_id': self.packet['observation_id']}
        ids = [c['candidate_id'] for c in self.packet['candidates']]
        gateway = DecisionGateway(lambda o: {'action': 'rank', 'candidate_ids': ids, **base},
                                  budget=1, purposes={'selection': 1, 'ranking': 2})
        gateway.decide(self.packet); gateway.decide(self.packet)
        self.assertEqual(gateway.purposes, {'ranking': 2})
        with self.assertRaises(RuntimeError): gateway.decide(self.packet)

    def test_ranking_cannot_introduce_a_new_candidate(self):
        base = {'revision': self.packet['revision'], 'observation_id': self.packet['observation_id']}
        gateway = DecisionGateway(lambda o: {'action': 'shortlist', 'candidate_ids': ['invented'], **base},
                                  budget=1)
        with self.assertRaises(ValueError): gateway.decide(self.packet)


def write_json_target(gateway):
    return str(gateway.path) if gateway.path else '.'


if __name__ == '__main__': unittest.main()