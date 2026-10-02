"""纯算法/生命周期测试使用的简化模型，不作为真实 RBY-1 物理验收证据。"""
from pathlib import Path
import xml.etree.ElementTree as ET

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.scenes.source import SceneSource
from lastmile_dataflow.runtime.simulation import Simulation


def fixtures(root):
    root = Path(root)
    robot_path = root / 'robot.xml'
    xml = ET.Element('mujoco', model='synthetic_protocol_fixture')
    ET.SubElement(xml, 'compiler', angle='radian', autolimits='true')
    default = ET.SubElement(xml, 'default')
    ET.SubElement(default, 'joint', damping='10', armature='1')
    ET.SubElement(default, 'position', kp='100', kv='20', ctrllimited='true', ctrlrange='-25 25')
    world = ET.SubElement(xml, 'worldbody')
    body = ET.SubElement(world, 'body', name='robot_0/base', pos='0 0 .5')
    ET.SubElement(body, 'geom', type='sphere', size='.02', mass='1', contype='0', conaffinity='0')
    actuators = ET.SubElement(xml, 'actuator')
    for name, kind, axis, ran in [('base_x','slide','1 0 0','-25 25'),('base_y','slide','0 1 0','-25 25'),('base_theta','hinge','0 0 1','-3.14 3.14')]:
        ET.SubElement(body, 'joint', name='robot_0/'+name, type=kind, axis=axis, range=ran)
        ET.SubElement(actuators,'position',name='robot_0/'+name+'_act',joint='robot_0/'+name,ctrlrange=ran)
    for group,count in [('torso',6),('head',2),('left_arm',7),('right_arm',7)]:
        child = body
        for i in range(count):
            name = f'{group}_{i}'
            child = ET.SubElement(child,'body',name='robot_0/'+name+'_link',pos='0 0 .02')
            ET.SubElement(child,'geom',type='sphere',size='.02',mass='1',contype='0',conaffinity='0')
            ET.SubElement(child,'joint',name='robot_0/'+name,axis=('1 0 0','0 1 0','0 0 1')[i%3],range='-3.14 3.14')
            actname = f'link{i+1}_act' if group=='torso' else f'head_{i}_act' if group=='head' else f'{group}_{i+1}_act'
            ET.SubElement(actuators,'position',name='robot_0/'+actname,joint='robot_0/'+name,ctrlrange='-3.14 3.14')
    for side in ['left','right']:
        for i in (1,2):
            name = f'gripper_finger_{side[0]}{i}'
            child = ET.SubElement(body,'body',name='robot_0/'+name, pos='0 0 .1')
            ET.SubElement(child,'geom',type='sphere',size='.02',mass='1',contype='0',conaffinity='0')
            ET.SubElement(child,'joint',name='robot_0/'+name,type='slide',axis='1 0 0',range='-.05 0' if i==1 else '0 .05')
            if i==1:
                ET.SubElement(actuators,'motor',name='robot_0/'+side+'_finger_act',joint='robot_0/'+name)
    for name in ('head_camera','wrist_camera_l','wrist_camera_r'):
        ET.SubElement(body,'camera',name='robot_0/'+name,pos='1 0 1',xyaxes='0 1 0 -.7 0 .7')
    ET.ElementTree(xml).write(robot_path)
    scene = root/'scene.xml'
    scene.write_text('''<mujoco model="synthetic_scene"><compiler angle="radian"/>
    <worldbody><geom name="floor" type="plane" size="0 0 .01"/>
    <body name="target" pos="2 0 .15"><freejoint name="target_free"/><geom name="target_geom" type="sphere" size=".1" mass=".1"/></body>
    </worldbody></mujoco>''')
    config = RobotConfig(str(robot_path))
    source = SceneSource('unit-scene',str(scene))
    return source,config


def make_sim(root):
    source,robot = fixtures(root)
    sim = Simulation.from_source(source,robot,target='target')
    return sim,robot
