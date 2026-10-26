"""Numerical construction proxies; no claim of real planning or task success."""
from dataclasses import replace
from dataclasses import asdict
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.planning.astar import Grid
from lastmile_dataflow.scenes.build.proposal import BuildProposal
from lastmile_dataflow.scenes.mode_config import MODE_NAMES, ModeConfig
from lastmile_dataflow.scenes.modes import create_builders
from lastmile_dataflow.scenes.modes.geometry import segment_boxes, proxy_visibility
from lastmile_dataflow.scenes.modes.geometry import role_profiles
from lastmile_dataflow.scenes.loading.asset_library import AssetSelector
from lastmile_dataflow.scenes.loading.asset_profiles import AssetProfile
from lastmile_dataflow.utils.io import write_rows


def station(identity, xy):
    return {'station_id': identity, 'xy_id': identity, 'xy': xy,
            'initial': {'base_pose': [*xy, 0.]}, 'yaw': 0., 'geometry': 'valid'}


def context():
    free = np.ones((11, 11), bool)
    grid = Grid(np.array([-1., -1.]), .2, free)
    rows = [station('a', [-.8, 0.]), station('b', [.8, 0.])]
    task = {'operation': 'pick', 'anchor_world': [0., 0., 1.], 'category': 'Cup'}
    geometry = NS(task=task, lattice=rows, navigation=grid,
        occupancy=NS(occupied=~free), clearance_base=np.full(free.shape, .3))
    return NS(task=task, geometry=geometry, settings=ModeConfig(),
              baseline=NS(robot=NS(initial_base_field='base_pose', config=NS(arm_working_reach_m=.8))),
              station_config=NS(radius_m=1., spacing_m=.25), execution=NS(local_base_radius_m=.35))


class ModeProxyTests(unittest.TestCase):
    def test_all_eight_are_concrete_and_owned_by_each_task(self):
        first, second = create_builders(MODE_NAMES), create_builders(MODE_NAMES)
        self.assertEqual(set(first), set(MODE_NAMES))
        self.assertTrue(all(first[key] is not second[key] for key in first))

    def test_reachability_keeps_mobile_base_scope(self):
        ctx = context()
        builder = create_builders(['reachability'])['reachability']
        proposal = builder.propose(ctx, np.random.default_rng(42))
        check = builder.cheap_validate(ctx, proposal)
        self.assertEqual(check.status, 'pass')
        self.assertEqual(check.metrics['local_base_radius_m'], .35)
        self.assertIn('not_kinematic_unreachability', check.metrics['scope'])
        self.assertFalse(proposal.edits)

    def test_each_variant_restarts_the_proposal_search(self):
        ctx = context()
        rng = np.random.default_rng(42)
        builder = create_builders(['reachability'])['reachability']
        first = builder.propose(ctx, rng)
        second = builder.propose(ctx, rng)
        self.assertNotEqual(first.proposal_id, second.proposal_id)
        builder.begin_variant(ctx, rng)
        self.assertEqual(builder.propose(ctx, rng).proposal_id, first.proposal_id)

    def test_role_selection_uses_real_support_category_and_available_dimensions(self):
        ctx = context()
        ctx.geometry.supports = [NS(support='desk-body', bounds=[-.4, .4, -.3, .3])]
        ctx.baseline.catalog = [{'mjcf_body': 'desk-body', 'category': 'Desk'}]
        profile = AssetProfile('vase', '/source', 'Vase', width_m=.2, depth_m=.2,
            height_m=.3, footprint_m2=.04, xml_path='/adapter.xml', root_body='vase',
            movable=True, loadable=True, support_kind='surface',
            compatible_supports=('Desk',), roles=('visual_occluder',))
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / 'assets.jsonl'
            write_rows(index, [asdict(profile), asdict(replace(profile, asset_id='too-large', width_m=2.))])
            ctx.selector = AssetSelector(index)
            chosen = role_profiles(ctx, 'visual_occluder', np.random.default_rng(42))
        self.assertEqual([p.asset_id for p in chosen], ['vase'])

    def test_ground_requires_both_blocked_straight_line_and_detour(self):
        ctx = context()
        builder = create_builders(['ground_obstacle'])['ground_obstacle']
        proposal = builder._proposal(*ctx.geometry.lattice)
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'fail')
        grid = ctx.geometry.navigation
        grid.free[3:8, 5] = False
        check = builder.cheap_validate(ctx, proposal)
        self.assertEqual(check.status, 'pass')
        self.assertGreater(check.metrics['detour_ratio'], 1.2)
        grid.free[:, 5] = False
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'fail')

    def test_narrow_requires_positive_clearance_and_reachable_wider_pose(self):
        ctx = context()
        builder = create_builders(['narrow_space'])['narrow_space']
        proposal = BuildProposal('narrow', 'narrow_space')
        grid = ctx.geometry.navigation
        ctx.geometry.clearance_base[grid.cell([-.8, 0.])] = .04
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'pass')
        self.assertEqual(proposal.station_hints, ['a', 'b'])
        ctx.geometry.clearance_base[grid.cell([-.8, 0.])] = -.01
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'fail')

    def test_visibility_unknown_cannot_be_a_low_view(self):
        ctx = context()
        builder = create_builders(['visibility'])['visibility']
        a, b = ctx.geometry.lattice
        proposal = builder._proposal(a, b)
        a['visibility'] = {'complete': False, 'value': None, 'known_max': .1}
        b['visibility'] = {'complete': False, 'value': None, 'known_max': .9}
        self.assertEqual(builder.cheap_validate(ctx, proposal, stations=[a, b]).status, 'unknown')
        a['visibility'] = {'complete': True, 'value': .1, 'known_max': .1}
        self.assertEqual(builder.cheap_validate(ctx, proposal, stations=[a, b]).status, 'pass')
        b['visibility']['known_max'] = .4
        self.assertEqual(builder.cheap_validate(ctx, proposal, stations=[a, b]).status, 'fail')

    def test_orientation_requires_same_xy_and_visible_distinct_yaw(self):
        ctx = context()
        builder = create_builders(['orientation'])['orientation']
        a, b = ctx.geometry.lattice
        b.update(xy_id=a['xy_id'], yaw=np.pi / 4)
        for row in (a, b):
            row['visibility'] = {'known_max': .8}
        proposal = BuildProposal('yaw', 'orientation', metadata={'yaw_group_ids': ['a', 'b']})
        self.assertEqual(builder.cheap_validate(ctx, proposal, stations=[a, b]).status, 'pass')
        b['xy_id'] = 'different'
        self.assertEqual(builder.cheap_validate(ctx, proposal, stations=[a, b]).status, 'fail')

    def test_articulated_has_no_post_contact_gate(self):
        ctx = context()
        ctx.task.update(operation='open', joint_name='door', joint_initial=0., joint_goal=1.)
        builder = create_builders(['articulated_interaction'])['articulated_interaction']
        proposal = builder.propose(ctx, np.random.default_rng(42))
        self.assertFalse(proposal.edits)
        self.assertFalse(proposal.metadata['post_contact_gate'])
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'pass')
        ctx.task['joint_goal'] = 0.
        self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'fail')

    def test_arm_and_approach_require_blocked_channels_and_clear_alternative(self):
        ctx = context()
        channel = {'points': np.array([[-1., 0., 1.], [1., 0., 1.]]), 'radius': .05, 'goal_radius': .05}
        other = {**channel, 'points': channel['points'] + [0., 1., 0.]}
        boxes = np.array([[[-.1, -.1, .8], [.1, .1, 1.2]]])
        def channels(ctx, row, **kwargs):
            return [channel if row['station_id'] == 'a' else other]
        for mode in ('arm_path_collision', 'approach'):
            with self.subTest(mode=mode):
                builder = create_builders([mode])[mode]
                proposal = builder._proposal(*ctx.geometry.lattice)
                with (patch('lastmile_dataflow.scenes.modes.arm_path.arm_channels', side_effect=channels),
                      patch('lastmile_dataflow.scenes.modes.arm_path.proposal_boxes', return_value=boxes)):
                    check = builder.cheap_validate(ctx, proposal)
                    self.assertEqual(check.status, 'pass')
                    self.assertEqual(check.metrics['tested_channels'], 1)
                with (patch('lastmile_dataflow.scenes.modes.arm_path.arm_channels', return_value=[channel]),
                      patch('lastmile_dataflow.scenes.modes.arm_path.proposal_boxes', return_value=boxes)):
                    self.assertEqual(builder.cheap_validate(ctx, proposal).status, 'fail')

    def test_segment_box_parallel_and_capsule_radius(self):
        box = np.array([[[0., 0., 0.], [1., 1., 1.]]])
        self.assertFalse(segment_boxes([-1., 1.1, .5], [2., 1.1, .5], box).any())
        self.assertTrue(segment_boxes([-1., 1.1, .5], [2., 1.1, .5], box, .2).all())
        self.assertTrue(segment_boxes([.5, .5, .5], [.5, .5, .5], box).all())
        self.assertFalse(segment_boxes([2., .5, .5], [2., .5, .5], box).any())
