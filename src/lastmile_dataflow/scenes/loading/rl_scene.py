"""Prepare one fixed task-local XML topology using the existing scene loader."""
from dataclasses import dataclass

import mujoco
import numpy as np

from ...config import PandaOmronConfig, REPO_ROOT
from ...runtime.simulation import Simulation
from .local import task_local_model
from .source import SceneSource


@dataclass(frozen=True)
class RLSceneConfig:
    data_root: str
    scene_xml: str
    scene_metadata: str
    robot_xml: str
    robot_scale: float
    target: str = 'Cup_30'
    local_radius_m: float = 2.
    physics_dt: float = .002
    multiccd: bool = False
    home: tuple = (0., -.00365045915, 0., -2.61799387799, 0., 2.74159265359, .78539816339)
    lift: float = .2

    def asset(self, name):
        return str(REPO_ROOT / self.data_root / name)


@dataclass
class PreparedRLScene:
    scene_spec: mujoco.MjSpec
    robot_spec: mujoco.MjSpec
    target_body: str
    target_position: np.ndarray
    initial_qpos: dict
    initial_ctrl: dict
    catalog: list
    local_model: dict
    multiccd: bool

    def copy_robot(self):
        return self.robot_spec.copy()

    def attach_scene(self, spec):
        spec.attach(self.scene_spec.copy(), prefix='', frame=spec.worldbody.add_frame())


def prepare_rl_scene(config):
    source = SceneSource('val103-cup30', config.asset(config.scene_xml),
                         config.asset(config.scene_metadata), 'procthor-10k-val', 'val')
    robot = PandaOmronConfig(model_path=config.asset(config.robot_xml), physics_dt=config.physics_dt,
                            cameras=('overview', 'robot0_eye_in_hand'),
                            initial={'base_xyyaw': [0., 0., 0.], 'lift': config.lift,
                                     'arm_q': {'right': list(config.home)},
                                     'gripper_width': {}})
    sim = Simulation.from_source(source, robot, target=config.target, robot_scale=config.robot_scale)
    target = sim.model.body(sim.target_id).name
    anchor = sim.data.xpos[sim.target_id].copy()
    local = task_local_model(sim, {'anchor_world': anchor.tolist(), 'target_body': target},
                             config.local_radius_m)
    scene_spec = local.spec.copy()
    robot_roots = [body for body in scene_spec.worldbody.bodies
                   if body.name.startswith(robot.namespace)]
    for body in robot_roots:
        scene_spec.delete(body)
    used_meshes = {geom.meshname for geom in scene_spec.geoms if geom.meshname}
    for mesh in list(scene_spec.meshes):
        if mesh.name not in used_meshes:
            scene_spec.delete(mesh)
    from ...runtime.control.robot import load_robot_spec
    robot_spec = load_robot_spec(robot, config.robot_scale)
    qpos = {local.model.joint(j).name: float(local.data.qpos[local.model.jnt_qposadr[j]])
            for j in range(local.model.njnt)
            if local.model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE}
    ctrl = {local.model.actuator(a).name: float(local.data.ctrl[a])
            for a in range(local.model.nu)}
    prepared = PreparedRLScene(scene_spec, robot_spec, target, anchor, qpos, ctrl,
                               local.catalog, local.local_model, config.multiccd)
    local.close()
    sim.close()
    return prepared
