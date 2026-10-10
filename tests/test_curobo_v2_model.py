import tempfile
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import numpy as np
from lastmile_dataflow.planning.rby1m_mobile_torso import derive_urdf, joint_names, ordered_points, robot_config, retime_points, static_ik_state, chain_state, minimum_mesh_query_dimension
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext


class V2ModelTests(unittest.TestCase):
    def test_coupled_urdf_limits_and_read_only_source(self):
        root = ET.Element('robot')
        for name, lo, hi, vel in [('torso_1',0,1,2),('torso_2',-1.4,0,1),('torso_3',0,.8,3),
                                  ('base_x',-1,1,1),('base_y',-1,1,1),('base_theta',-3.14,3.14,1)]:
            j = ET.SubElement(root, 'joint', name=name, type='revolute')
            ET.SubElement(j, 'limit', lower=str(lo), upper=str(hi), velocity=str(vel))
        with tempfile.TemporaryDirectory() as directory:
            src, dst = [Path(directory)/n for n in ('source.urdf', 'derived.urdf')]
            ET.ElementTree(root).write(src); original = src.read_bytes()
            result = derive_urdf(src, dst, {n:(-2,2) for n in ('base_x','base_y','base_theta')})
            self.assertEqual(src.read_bytes(), original)
            self.assertEqual(result['h_limits'], [0,.7]); self.assertEqual(result['h_velocity_limit'], .5)
            self.assertEqual(ET.parse(dst).find("joint[@name='torso_2']/mimic").get('multiplier'), '-2')

    def test_named_joint_order_rejects_extra_dof(self):
        canonical = joint_names('left'); reversed_names = list(reversed(canonical))
        self.assertEqual(len(canonical), 11)
        np.testing.assert_equal(ordered_points(np.arange(11), reversed_names, list(canonical)), np.arange(10,-1,-1))
        with self.assertRaises(ValueError):
            ordered_points(np.arange(12), list(canonical)+['head_0'], list(canonical))

    def test_native_trajectory_timing_preserves_endpoints(self):
        p = retime_points(np.array([[0.]*11,[1.]*11,[2.]*11]), .025, 20, .5)
        np.testing.assert_allclose(p[:,0], [0,1,2])
        with self.assertRaises(ValueError): retime_points(p, 0., 20, .5)

    def test_chaining_adapter_preserves_real_state_and_dynamics(self):
        current=SimpleNamespace(position=np.arange(31), velocity=np.ones(31), dt=.025,
                                knot=np.zeros(11), knot_dt=.1)
        current.clone=lambda:copy.copy(current)
        static=static_ik_state(current)
        self.assertIsNone(static.dt);self.assertEqual(current.dt,.025)
        np.testing.assert_equal(static.velocity,current.velocity)
        chain_state(current)
        self.assertIsNone(current.knot);self.assertIsNone(current.knot_dt)
        self.assertEqual(current.dt,.025)
        np.testing.assert_equal(current.position,np.arange(31))

    def test_small_mesh_no_hit_bound_exceeds_largest_sphere(self):
        r=.105
        cup_half_diagonal=np.linalg.norm([.124,.124,.098])/2
        self.assertLess(cup_half_diagonal,r)  # regression's constant false collision
        padded=minimum_mesh_query_dimension(r)
        self.assertGreater(np.linalg.norm([padded]*3)/2,r+.01)

    def test_target_contact_mask_does_not_disable_environment_geometry(self):
        import torch
        from lastmile_dataflow.planning.curobo_v2_costs import contact_spheres
        original=torch.ones((2,1,5,4),requires_grad=True)
        selected=contact_spheres(original,[1,3],True)
        self.assertTrue(torch.equal(original[...,3],torch.ones((2,1,5))))
        self.assertTrue(torch.equal(selected[..., [0,2,4],3],torch.ones((2,1,3))))
        self.assertTrue(torch.all(selected[..., [1,3],3] == -100))
        selected[...,:3].sum().backward()
        self.assertTrue(torch.all(original.grad[...,:3] == 1))
        self.assertTrue(torch.equal(contact_spheres(original,[1,3],False),original))

    def test_version_and_retry_layers(self):
        cfg = NoEditConfig(planner_backend='curobo_v2_v080', max_attempts=5)
        self.assertEqual(cfg.operation_retries+1, 2)
        self.assertEqual(cfg.trials_per_station, 5)
        self.assertEqual(cfg.max_operation_plans, 12)
        with self.assertRaises(ValueError):
            NoEditConfig(planner_backend='curobo_v2_v080', max_attempts=3)
        with self.assertRaises(ValueError):
            NoEditConfig(planner_backend='curobo_v2_v080', max_attempts=5, operation_retries=2)

    def test_11d_waypoint_maps_h_absolute_not_delta(self):
        groups = {'base':np.zeros(3), 'left_arm':np.zeros(7), 'torso':np.array([0,.3,-.6,.3,0,0])}
        robot = SimpleNamespace(group=lambda name:groups[name].copy(), neutral_action=lambda:np.zeros(20),
                                config=SimpleNamespace(max_base_delta=.1,max_yaw_delta=.2,max_arm_delta=.2,
                                                       control_hz=20,torso_limits=(0,.738)))
        ctx = ContinuousContext.__new__(ContinuousContext); ctx.sim=SimpleNamespace(robot=robot)
        ctx.config=NoEditConfig(); ctx.monitor=None; actions=[]; ctx.tick=lambda a,**kw:actions.append(a)
        planner=SimpleNamespace(names=joint_names('left'),torso_active=True,base_target_world=lambda p:p[:3])
        point=np.r_[.1,0,0,np.full(7,.1),.4]
        scale=ctx.mobile_tick(planner,point,'left',-.05,0.)
        self.assertAlmostEqual(scale,80)
        self.assertAlmostEqual(actions[-1][19],.30125)
        self.assertAlmostEqual(actions[-1][0],.00125)
        self.assertEqual(actions[-1][10],-.05)
        np.testing.assert_equal(actions[-1][11:18],0)

if __name__ == '__main__': unittest.main()
