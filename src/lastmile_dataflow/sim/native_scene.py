"""Derive native robot controls from an existing compiled scene, never rebuild it."""

import copy
from dataclasses import replace

import mujoco
import numpy as np

from .model.mjcf import load_spec
from lastmile_dataflow.robots.registry import prepare_robot_spec
from lastmile_dataflow.robots.rby1.adapter import NATIVE_CAMERA_FOVY
from .simulation import NATIVE_PROFILE, closed_on_error


def native_scene(parent):
    if parent.started or parent.closed:
        raise RuntimeError("native scene derivation requires preparation phase")
    config = replace(parent.robot.config, control_profile=NATIVE_PROFILE, control_hz=10.0, physics_dt=0.004)
    if parent.model.opt.timestep != 0.004:
        raise ValueError("source physics timestep is not MolmoBot compatible")
    model = copy.copy(parent.model)
    if parent.robot.config.control_profile != NATIVE_PROFILE:
        spec = load_spec(config.model_path)
        prepare_robot_spec(spec, config)
        # Only two robot actuators change. All non-robot physics stays frozen.
        for side in ("left", "right"):
            name = config.namespace + side + "_finger_act"
            actuator = spec.actuator(name)
            index = model.actuator(name).id
            for key in ("gaintype", "biastype", "gainprm", "biasprm", "ctrllimited", "ctrlrange"):
                getattr(model, "actuator_" + key)[index] = getattr(actuator, key)
    for alias, fovy in NATIVE_CAMERA_FOVY.items():
        model.cam_fovy[model.camera(config.namespace + alias).id] = fovy
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, 1024)
    model.vis.global_.offheight = max(model.vis.global_.offheight, 576)
    with closed_on_error(parent.derive(model=model, robot_config=config)) as sim:
        mujoco.mj_setState(model, sim.data, parent.state_vector(), parent.state_spec)
        sim.robot.fixed_head = parent.robot.fixed_head.copy()
        sim.robot.hold()
        mujoco.mj_forward(model, sim.data)
        if not np.array_equal(parent.data.qpos, sim.data.qpos) or not np.array_equal(parent.data.qvel, sim.data.qvel):
            raise RuntimeError("native conversion changed frozen physical state")
    return sim
