"""Derive native robot controls from an existing compiled scene, never rebuild it."""
import copy
from dataclasses import replace

import mujoco
import numpy as np

from ..scenes.mjcf import load_spec
from .robot import prepare_robot_spec
from .simulation import Simulation


def native_scene(parent):
    if parent.started or parent.closed:
        raise RuntimeError('native scene derivation requires preparation phase')
    config = replace(parent.robot.config, control_profile='molmobot-native-v1',
                     control_hz=10., physics_dt=.004)
    if parent.model.opt.timestep != .004:
        raise ValueError('source physics timestep is not MolmoBot compatible')
    model = copy.copy(parent.model)
    if parent.robot.config.control_profile != 'molmobot-native-v1':
        spec = load_spec(config.model_path)
        prepare_robot_spec(spec, config)
        # Only two robot actuators change. All non-robot physics stays frozen.
        for side in ('left', 'right'):
            name = config.namespace + side + '_finger_act'
            actuator = spec.actuator(name)
            index = model.actuator(name).id
            for key in ('gaintype', 'biastype', 'gainprm', 'biasprm', 'ctrllimited', 'ctrlrange'):
                getattr(model, 'actuator_' + key)[index] = getattr(actuator, key)
    for alias, fovy in (('head_camera',139.), ('wrist_camera_l',58.), ('wrist_camera_r',58.)):
        model.cam_fovy[model.camera(config.namespace + alias).id] = fovy
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, 1024)
    model.vis.global_.offheight = max(model.vis.global_.offheight, 576)
    sim = Simulation(model, config, copy.deepcopy(parent.catalog), parent.model.body(parent.target_id).name)
    mujoco.mj_setState(model, sim.data, parent.state_vector(), parent.state_spec)
    sim.robot.fixed_head = parent.robot.fixed_head.copy()
    sim.source = parent.source
    sim.restoration = parent.restoration
    sim.robot.hold()
    mujoco.mj_forward(model, sim.data)
    if not np.array_equal(parent.data.qpos, sim.data.qpos) or not np.array_equal(parent.data.qvel, sim.data.qvel):
        sim.close()
        raise RuntimeError('native conversion changed frozen physical state')
    return sim
