import unittest
from types import SimpleNamespace
import numpy as np
from lastmile_dataflow.robots.action import InvalidAction
from lastmile_dataflow.robots.action_limits import sanitize_action, local_yaw_bounds
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext
from lastmile_dataflow.stations.no_edit_config import NoEditConfig


def robot_fixture():
    groups={'base':['base_x','base_y','base_theta'], 'left_arm':[f'l{i}' for i in range(7)],
            'right_arm':[f'r{i}' for i in range(7)], 'torso':[f't{i}' for i in range(6)],
            'left_gripper':['lf','lfp'], 'right_gripper':['rf','rfp']}
    names=[n for g in groups.values() for n in g];joints={n:i for i,n in enumerate(names)}
    ranges=np.array([[-3.14,3.14]]*len(names));ranges[joints['base_x']]=[-10,10];ranges[joints['base_y']]=[-10,10]
    for n,r in [('t1',(0,.738)),('t2',(-1.476,0)),('t3',(0,.738)),('lf',(-.05,0)),('rf',(-.05,0)),('lfp',(0,.05)),('rfp',(0,.05))]:ranges[joints[n]]=r
    actuators={g:ns[:1] if 'gripper' in g else ns for g,ns in groups.items()}
    values={g:np.zeros(len(ns)) for g,ns in groups.items()};values['torso']=np.array([0,.3,-.6,.3,0,0]);values['left_gripper'][0]=-.05;values['right_gripper'][0]=-.05
    model=SimpleNamespace(jnt_limited=np.ones(len(names)),jnt_range=ranges,actuator_ctrllimited=np.ones(len(names)),actuator_ctrlrange=ranges.copy())
    config=SimpleNamespace(torso_limits=(0,.738),gripper_limits=(-.05,0),max_base_delta=.1,max_yaw_delta=.2,max_arm_delta=.2)
    return SimpleNamespace(groups=groups,joints=joints,actuators=actuators,act_ids=joints,model=model,config=config,group=lambda g:values[g].copy(),_check_joint=lambda n,v:None),values


class ActionLimitTests(unittest.TestCase):
    def test_recorded_yaw_incident(self):
        r,v=robot_fixture();v['base'][2]=-3.1252652876619265
        a=np.zeros(20);a[19]=.3;a[2]=-3.1404502842168096-v['base'][2]
        b,e=sanitize_action(r,a)
        self.assertAlmostEqual(v['base'][2]+b[2],-3.14)
        self.assertEqual(e[0]['joint'],'base_theta')
        self.assertNotEqual(a[2],b[2])
        lo,hi=local_yaw_bounds(r,2.8);self.assertAlmostEqual(lo,-5.94);self.assertAlmostEqual(hi,.34)

    def test_arm_gripper_translation_and_actuator_intersection(self):
        r,v=robot_fixture();v['left_arm'][0]=3.14;v['base'][0]=10
        a=np.zeros(20);a[19]=.3;a[3]=.001;a[0]=.0005;a[10]=.0005
        b,e=sanitize_action(r,a);self.assertEqual(b[0],0);self.assertEqual(b[3],0);self.assertEqual(b[10],0)
        self.assertEqual(len(e),3)
        r.model.actuator_ctrlrange[r.joints['r0']]=[-1,1];v['right_arm'][0]=1
        a=np.zeros(20);a[19]=.3;a[11]=.001
        self.assertEqual(sanitize_action(r,a)[0][11],0)
        a[11]=.1
        with self.assertRaises(InvalidAction):sanitize_action(r,a)

    def test_unsafe_yaw_wrap_not_allowed(self):
        ctx=ContinuousContext.__new__(ContinuousContext);ctx.config=NoEditConfig(planner_backend='curobo_v2_v080',max_attempts=5)
        self.assertAlmostEqual(ctx.yaw_gap(3.13,-3.13),6.26)
        ctx.config=NoEditConfig();self.assertLess(abs(ctx.yaw_gap(3.13,-3.13)),.03)
        r,v=robot_fixture();v['base'][2]=-3.13;a=np.zeros(20);a[19]=.3;a[2]=-.02
        with self.assertRaises(InvalidAction):sanitize_action(r,a)

if __name__=='__main__':unittest.main()
