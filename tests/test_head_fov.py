"""Geometry/program regressions, not a real-robot grasp-success claim."""
import math
import unittest
from types import SimpleNamespace
import mujoco
import numpy as np
import torch
from lastmile_dataflow.planning.head_fov import HeadFOVCost, SparseFOVValidator, knot_sample_indices, camera_robot_config, target_camera_torch
from lastmile_dataflow.planning.rby1m_mobile_torso import joint_names
from lastmile_dataflow.integrations.waypoints import PlanResult
from lastmile_dataflow.stations.no_edit_config import NoEditConfig


def fixture():
    body = '<body name="base"><joint name="base_x" type="slide" axis="1 0 0"/><joint name="base_y" type="slide" axis="0 1 0"/><joint name="base_theta" axis="0 1 0"/><geom size=".1" mass="1"/><camera name="head" fovy="45"/>'
    for n in [*[f'torso_{i}' for i in range(6)], *[f'left_arm_{i}' for i in range(7)]]:
        body += f'<body><joint name="{n}"/><geom size=".01" mass=".1"/>'
    body += '<site name="ee_site_l"/>' + '</body>'*14
    model = mujoco.MjModel.from_xml_string('<mujoco><worldbody>'+body+'<body name="target" pos="0 0 -2"/></worldbody></mujoco>')
    data = mujoco.MjData(model); mujoco.mj_forward(model, data)
    groups = {'base':['base_x','base_y','base_theta'], 'torso':[f'torso_{i}' for i in range(6)], 'left_arm':[f'left_arm_{i}' for i in range(7)]}
    addresses = {n:int(model.jnt_qposadr[model.joint(n).id]) for ns in groups.values() for n in ns}
    sim = SimpleNamespace(model=model, data=data, target_id=model.body('target').id,
                          robot=SimpleNamespace(groups=groups, addresses=addresses, config=SimpleNamespace(namespace='')))
    definition = {'camera_id':0,'tan_half_x':math.tan(math.pi/8)*4/3,'tan_half_y':math.tan(math.pi/8)}
    return sim, SparseFOVValidator(sim, definition, lambda p:p[:3], joint_names('left'))


class HeadFOVTests(unittest.TestCase):
    def test_center_edge_outside_behind_and_gradients(self):
        target = torch.tensor([[[0.,0.,-1.],[.5,0.,-1.],[1.,0.,-1.],[0.,0.,1.]]], requires_grad=True)
        value = HeadFOVCost(.55,.414)(target)
        self.assertEqual(value.shape,(1,4,1))
        self.assertEqual(value[0,0,0],0)
        self.assertGreater(value[0,1,0],0)
        self.assertGreater(value[0,2,0],value[0,1,0])
        self.assertGreater(value[0,3,0],value[0,2,0])
        value.sum().backward()
        self.assertTrue(torch.isfinite(target.grad).all())
        self.assertNotEqual(target.grad[0,2,0],0)
        self.assertGreater(target.grad[0,3,2],0)

    def test_geometry_rejects_outside_and_behind_without_live_mutation(self):
        sim, validator = fixture()
        before = sim.data.qpos.copy(); camera = sim.data.cam_xmat.copy()
        points = np.zeros((3,11)); points[1,0]=2; points[2,2]=math.pi
        report = validator.validate(points,[0,1,2])
        self.assertFalse(report['valid']);self.assertEqual(report['failed_indices'],[1,2])
        np.testing.assert_array_equal(sim.data.qpos,before)
        np.testing.assert_array_equal(sim.data.cam_xmat,camera)

    def test_middle_waypoint_escape_even_with_identical_endpoints(self):
        _, validator = fixture()
        points = np.zeros((21,11)); points[10,0]=2.
        report = validator.validate(points,[0,20])
        self.assertFalse(report['valid']);self.assertIn(10,report['failed_indices'])
        self.assertLess(report['geometry_checks'],len(points))

    def test_arm_only_cached_camera_no_midpoint_geometry(self):
        _, validator = fixture()
        points = np.zeros((101,11)); points[:,3] = np.linspace(0,1,101)
        report = validator.validate(points,[0,20,40,60,80,100])
        self.assertTrue(report['valid']);self.assertEqual(report['geometry_checks'],1)
        self.assertEqual(report['checked_indices'],[0,20,40,60,80,100])

    def test_carried_target_does_not_use_arm_only_cache(self):
        _, validator = fixture()
        validator.attached_offset=np.array([0.,0.,-2.])
        points=np.zeros((101,11));points[:,3]=np.linspace(0.,1.,101)
        report=validator.validate(points,[0,20,40,60,80,100])
        self.assertGreaterEqual(report['geometry_checks'],6)

    def test_edge_motion_refines_even_below_pose_threshold(self):
        _, validator = fixture()
        points = np.zeros((5,11));points[:,0]=np.linspace(1.04,1.06,5)
        report=validator.validate(points,[0,4])
        self.assertTrue(report['valid']);self.assertIn(2,report['checked_indices'])

    def test_knot_times_not_spline_control_positions(self):
        s=SimpleNamespace(knot=torch.zeros(1,5,11),knot_dt=torch.tensor(.1),dt=torch.tensor(.025))
        self.assertEqual(knot_sample_indices(s,.025,21),[0,4,8,12,16,20])

    def test_camera_fk_does_not_change_planner_goal_frames(self):
        cfg={'kinematics':{'extra_links':{},'tool_frames':['ee_left_tcp']}}
        derived=camera_robot_config(cfg,{'parent_link':'link_head_2','mount_pose':[0,0,0,1,0,0,0]})
        self.assertEqual(cfg['kinematics']['tool_frames'],['ee_left_tcp'])
        self.assertEqual(derived['kinematics']['tool_frames'][:2],['ee_left_tcp','head_fov_optical'])
        self.assertEqual(len(derived['kinematics']['tool_frames']),8)

    def test_fk_projection_preserves_batch_horizon_and_gradients(self):
        # Camera basis positions preserve both batch and horizon dimensions.
        x=torch.zeros(2,3,requires_grad=True)
        origin=torch.stack((x,torch.zeros_like(x),torch.zeros_like(x)),dim=-1)
        positions=torch.stack([origin, *[origin+.1*torch.eye(3)[i] for i in range(3)]],dim=-2)
        poses=SimpleNamespace(tool_frames=['head_fov_optical', *[f'head_fov_optical_fov_axis_{i}' for i in range(3)]],position=positions)
        fk=SimpleNamespace(compute_kinematics=lambda js:SimpleNamespace(tool_poses=poses))
        context={'fov_target':torch.tensor([1.,0.,-1.])}
        local=target_camera_torch(fk,None,context)
        self.assertEqual(local.shape,(2,3,3))
        HeadFOVCost(.55,.414)(local).sum().backward()
        self.assertTrue(torch.all(x.grad < 0))

    def test_v2_hard_rejection_returns_no_executable_plan(self):
        from lastmile_dataflow.planning.curobo_v2 import V2Planner
        planner=V2Planner.__new__(V2Planner)
        planner.motion=SimpleNamespace(plan_pose=lambda *a:SimpleNamespace(success=torch.tensor([False]),status='FOV_CONSTRAINT_FAILED'))
        planner.names=joint_names('left');planner.query_log=[];planner.fov_log=[{'valid':False}]
        planner.config=SimpleNamespace(head_fov_enabled=True)
        planner.goals=lambda g:g;planner.state=lambda:None
        result=planner.plan(np.eye(4))
        self.assertEqual(result.status,'fov_constraint_failed');self.assertEqual(result.positions,())
        self.assertTrue(result.diagnostics['finite_budget_not_impossibility'])

    def test_v2_success_result_does_not_require_nonexistent_native_status(self):
        from lastmile_dataflow.planning.curobo_v2 import V2Planner
        planner=V2Planner.__new__(V2Planner)
        native=SimpleNamespace(success=torch.tensor([True]),get_interpolated_plan=lambda:None)
        planner.motion=SimpleNamespace(plan_pose=lambda *a:native)
        planner.names=joint_names('left');planner.query_log=[];planner.fov_log=[]
        planner.config=SimpleNamespace(head_fov_enabled=True)
        planner.goals=lambda g:g;planner.state=lambda:None
        planner.points=lambda t:((0.,)*11,)
        self.assertEqual(planner.plan(np.eye(4)).status,'success')

    def test_failure_is_non_executable_and_config_validates(self):
        result=PlanResult('fov_constraint_failed',(),(),{})
        self.assertEqual(result.status,'fov_constraint_failed')
        with self.assertRaises(ValueError):PlanResult('fov_constraint_failed',(),((1.,),),{})
        for d in ({'head_fov_margin':1.},{'head_fov_weight':0.},{'head_fov_enabled':1},{'head_fov_min_depth_m':0.}):
            with self.assertRaises(ValueError):NoEditConfig(**d)

if __name__=='__main__':unittest.main()
