"""RoboCasa365 native runtime: the official robosuite Environment behind the runtime interface.

Actions go straight to ``env.step``; this module never steps the physics itself. robosuite/robocasa
are imported lazily so the LMDF environment can import ``sim`` without them. A frozen scene directory
holds the environment arguments, episode metadata, the native XML and the flattened initial state;
reconstruction follows robocasa's own playback path (set_ep_meta → reset → reset_from_xml_string →
set_state_from_flattened). Assets stay external references to the robocasa checkout.
"""

import json
from pathlib import Path
import subprocess

import numpy as np

from lastmile_dataflow.utils.gpu import bind_cpu_renderer
from lastmile_dataflow.utils.io import read_json, write_json
from .runtime import ActionSpec, RuntimeState, StepResult

ENV_FILE, META_FILE, XML_FILE, STATE_FILE = "env.json", "ep_meta.json", "model.xml", "initial_state.npz"
BASE_JOINTS = ("mobilebase0_joint_mobile_forward", "mobilebase0_joint_mobile_side", "mobilebase0_joint_mobile_yaw")
TORSO_JOINT = "mobilebase0_joint_torso_height"


def _robosuite():
    """Import robosuite/robocasa for this process.

    RoboCasa physics uses no CUDA; keep the bound EGL render device and hide CUDA, since robosuite asserts at
    import that MUJOCO_EGL_DEVICE_ID appears in CUDA_VISIBLE_DEVICES while LMDF maps EGL indices separately.
    """
    bind_cpu_renderer()
    from .mujoco316_compat import import_robocasa

    import_robocasa()  # registers the kitchen environments; MuJoCo 3.16 for all simulators
    import robosuite

    return robosuite


def native_env_kwargs(robot, *, env_name, layout_id, style_id, seed, obj_groups):
    """robosuite.make arguments of one frozen kitchen instance; the default controller is resolved here once."""
    _robosuite()
    from robosuite.controllers import load_composite_controller_config

    return {
        "env_name": env_name,
        "robots": robot.embodiment,
        "controller_configs": load_composite_controller_config(controller=None, robot=robot.embodiment),
        "layout_ids": [layout_id],
        "style_ids": [style_id],
        "seed": seed,
        "obj_groups": obj_groups,
        "control_freq": robot.control_freq,
        "camera_names": list(robot.cameras),
        "camera_widths": robot.camera_width,
        "camera_heights": robot.camera_height,
        "has_renderer": False,
        "has_offscreen_renderer": True,
        "use_camera_obs": False,
        "ignore_done": True,
        "translucent_robot": False,
        "randomize_cameras": False,
        "render_gpu_device_id": -1,  # robosuite then renders on MUJOCO_EGL_DEVICE_ID
    }


def make_env(kwargs):
    return _robosuite().make(**kwargs)


def _commit(module):
    root = Path(module.__file__).resolve().parents[1]
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or None


def versions():
    robosuite = _robosuite()

    import mujoco
    import robocasa
    return {
        "robosuite": robosuite.__version__,
        "robosuite_commit": _commit(robosuite),
        "robocasa": robocasa.__version__,
        "robocasa_commit": _commit(robocasa),
        "robocasa_assets": str(Path(robocasa.__file__).parent / "models/assets"),
        "mujoco": mujoco.__version__,
    }


def object_record(env, name):
    """Target/object reference: native object name, root body, contact geoms and asset reference."""
    obj = env.objects[name]
    cfg = next(c for c in env.object_cfgs if c["name"] == name)
    info = cfg.get("info", {})
    return {
        "name": name,
        "root_body": obj.root_body,
        "contact_geoms": list(obj.contact_geoms),
        "category": info.get("cat"),
        "groups": info.get("groups"),
        "mjcf_path": info.get("mjcf_path"),
        "graspable": cfg.get("graspable"),
    }


def freeze_env(env, kwargs, path):
    """Save one imported instance: arguments, versions, ep_meta, native XML, flattened initial state."""
    return write_frozen_scene(
        path, {"kwargs": kwargs, "versions": versions()}, env.get_ep_meta(),
        env.sim.model.get_xml(), env.sim.get_state().flatten(),
    )


def write_frozen_scene(path, env_record, ep_meta, xml, state):
    """Write a model and its matching native playback state together."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    write_json(path / ENV_FILE, env_record)
    write_json(path / META_FILE, ep_meta)
    (path / XML_FILE).write_text(xml)
    with (path / STATE_FILE).open("xb") as stream:
        np.savez_compressed(stream, states=state)
    return path


SEMANTIC_KEYS = ("type", "input_type", "input_ref_frame", "input_max", "input_min", "output_max", "output_min")


def _part_semantics(configs, name):
    config = configs.get(name.removesuffix("_gripper"), {})
    if name.endswith("_gripper"):
        config = config.get("gripper", {})
    return {key: value for key, value in config.items() if key in SEMANTIC_KEYS}


def _body_subtree(model, root):
    result = {root}
    for body in range(root + 1, model.nbody):
        if int(model.body_parentid[body]) in result:
            result.add(body)
    return result


def _top_body(model, body):
    while int(model.body_parentid[body]) != 0:
        body = int(model.body_parentid[body])
    return body


def _yaw(quat_wxyz):
    w, x, y, z = quat_wxyz
    return float(np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


# Native robosuite joint-position controller with absolute goals, for tracking planned joint trajectories
# (the frozen default is OSC_POSE delta, which leaves the elbow free). Physics and actuators are unchanged.
JOINT_POSITION_ARM = {
    "type": "JOINT_POSITION", "input_type": "absolute", "input_max": 2 * np.pi, "input_min": -2 * np.pi,
    "kp": 200, "damping_ratio": 1, "impedance_mode": "fixed", "interpolation": None,
}


class RoboCasaRuntime:
    """One frozen RoboCasa instance; ``reset`` restores the frozen state and clears control history."""

    def __init__(self, scene_dir, *, target="obj", arm_controller=None):
        self.scene_dir = Path(scene_dir)
        saved = read_json(self.scene_dir / ENV_FILE)
        self.kwargs = saved["kwargs"]
        if arm_controller == "joint_position":
            right = self.kwargs["controller_configs"]["body_parts"]["right"]
            self.kwargs["controller_configs"]["body_parts"]["right"] = {**JOINT_POSITION_ARM, "gripper": right["gripper"]}
        elif arm_controller is not None:
            raise ValueError(f"unknown arm controller {arm_controller}")
        self.ep_meta = read_json(self.scene_dir / META_FILE)
        self.xml = (self.scene_dir / XML_FILE).read_text()
        with np.load(self.scene_dir / STATE_FILE, allow_pickle=False) as frozen:
            self.initial_state = frozen["states"]
        self.target = target
        self.env = make_env(self.kwargs)
        self.reset()
        self.action_spec = self._action_spec()

    @property
    def robot(self):
        return self.env.robots[0]

    def _restore(self):
        """robocasa's playback restore, unchanged: env.reset inside it rebuilds the controllers, so control
        history is cleared without touching their goals (extra goal resets would change native control)."""
        env = self.env
        env.set_ep_meta(json.loads(json.dumps(self.ep_meta)))
        env.reset()
        env.reset_from_xml_string(env.edit_model_xml(self.xml))
        env.sim.reset()
        env.sim.set_state_from_flattened(self.initial_state)
        env.sim.forward()
        env.update_state()
        model = env.sim.model._model
        self.target_geoms = {model.geom(name).id for name in env.objects[self.target].contact_geoms}
        self.target_body = model.body(env.objects[self.target].root_body).id
        arm_base = model.body(self.robot.robot_model.root_body).id
        self.robot_bodies = _body_subtree(model, _top_body(model, arm_base))

    def _sync_controllers(self):
        """After moving the base: controller goals follow the new measured state (no pending command)."""
        controller = self.robot.composite_controller
        controller.update_state()
        for part in controller.part_controllers.values():
            part.update(force=True)
            part.reset_goal()

    def reset(self):
        self._restore()
        self.steps = 0
        self.locked_base = None
        self.saved_ranges = None
        self.max_base_drift = np.zeros(2)
        self.max_torso_drift = 0.0
        return self.get_state()

    def step(self, action):
        action = np.asarray(action, dtype=float)
        if action.shape != (self.action_spec.dim,):
            raise ValueError(f"RoboCasa action must have shape ({self.action_spec.dim},)")
        if self.locked_base is not None:
            action = self.mask_base_action(action)
        observation, reward, done, info = self.env.step(action)
        self.steps += 1
        if self.locked_base is not None:
            self._track_drift()
        native = {
            "reward": float(reward),
            "done": bool(done),
            "task_success": bool(self.env._check_success()),
            "info": info,
        }
        return StepResult(float(self.env.sim.data.time), observation, native)

    def _action_spec(self):
        controller = self.robot.composite_controller
        order = sorted(controller._action_split_indexes.items(), key=lambda item: item[1][0])
        parts = [(name, end - start) for name, (start, end) in order]
        dim = self.env.action_dim
        if sum(size for _, size in parts) == dim - 1:
            parts.append(("base_mode", 1))
        configs = self.kwargs["controller_configs"]["body_parts"]
        semantics = {name: _part_semantics(configs, name) for name, _ in parts if name != "base_mode"}
        semantics["base_mode"] = {"type": "HYBRID_MOBILE_BASE", "meaning": "> 0 base mode, otherwise arm mode"}
        semantics["range"] = "each entry in [-1, 1], scaled by the native controller"
        return ActionSpec(runtime="robocasa", dim=dim, parts=tuple(parts), semantics=semantics)

    def joint_positions(self):
        """Measured joint positions per native part (arm, gripper, torso, base) from robosuite's index map."""
        qpos = self.env.sim.data.qpos
        model = self.env.sim.model._model
        return {
            part: [float(qpos[model.jnt_qposadr[j]]) for j in ids]
            for part, ids in self.robot._ref_joints_indexes_dict.items()
        }

    def grasped_objects(self):
        gripper = self.robot.gripper["right"]
        return tuple(name for name, obj in self.env.objects.items() if self.env._check_grasp(gripper, obj))

    def support_contacts(self):
        sim = self.env.sim
        model, data = sim.model._model, sim.data._data
        target = _body_subtree(model, self.target_body)
        found = set()
        for contact in data.contact[: data.ncon]:
            geoms = (contact.geom1, contact.geom2)
            if not any(g in self.target_geoms for g in geoms):
                continue
            other = geoms[1] if geoms[0] in self.target_geoms else geoms[0]
            body = int(model.geom_bodyid[other])
            if body not in target and body not in self.robot_bodies:
                found.add(model.geom(other).name or f"geom{other}")
        return tuple(sorted(found))

    def robot_env_contacts(self, *, include_target=True):
        """Active contacts between robot geoms and non-robot geoms (optionally excluding the target object)."""
        model, data = self.env.sim.model._model, self.env.sim.data._data
        target = set() if include_target else _body_subtree(model, self.target_body)
        count = 0
        for contact in data.contact[: data.ncon]:
            bodies = [int(model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2)]
            robot = [b in self.robot_bodies for b in bodies]
            if robot[0] != robot[1]:
                count += bodies[robot[0]] not in target  # the non-robot side
        return count

    def native_observation(self):
        """robosuite proprioceptive observation dict at the current state."""
        return self.env._get_observations(force_update=True)

    def base_pose(self):
        model, data = self.env.sim.model._model, self.env.sim.data._data
        body = model.joint(BASE_JOINTS[0]).bodyid[0]
        return np.r_[data.xpos[body][:2], _yaw(data.xquat[body])]

    def get_state(self):
        data = self.env.sim.data._data
        support = self.support_contacts()
        return RuntimeState(
            time_s=float(data.time),
            robot={"base_xyyaw": self.base_pose().tolist(), "joints": self.joint_positions()},
            target=self.target,
            target_pose=np.r_[data.xpos[self.target_body], data.xquat[self.target_body]],
            grasped_objects=self.grasped_objects(),
            target_supported=bool(support),
            support_contacts=support,
            target_bottom_z=self.target_bottom_z(),
        )

    def target_bottom_z(self):
        """Lowest collision vertex of the target in world z (station-quality lift reference)."""
        from .model.geometry import geom_points

        sim = self.env.sim
        return float(min(geom_points(sim, g)[:, 2].min() for g in self.target_geoms))

    def tabletop_z(self, support_contacts):
        """Height of the collision surfaces supporting the cup in its initial state."""
        from .model.geometry import geom_points

        sim = self.env.sim
        return float(max(geom_points(sim, sim.model.geom(name).id)[:, 2].max() for name in support_contacts))

    def set_station(self, x, y, yaw):
        """Reset, then move only the three base joints so the base body reaches the world pose (x, y, yaw).

        Arm, torso and gripper joints keep their frozen initial values and controller goals are resynced.
        This initializes a trial; it does not navigate or check that the station is feasible.
        """
        self.reset()
        self._place_base(x, y, yaw)
        self.env.update_state()
        self._sync_controllers()
        return self.get_state()

    def lock_base(self, torso_m=0.0):
        """Operation phase: clamp the planar base joints at their current values and the torso at ``torso_m``.

        Base and torso commands are masked and arm mode is forced at the env boundary; ``unlock_base`` restores
        the native ranges so a later navigation phase keeps the native base dynamics and controllers.
        """
        model, data = self.env.sim.model._model, self.env.sim.data._data
        torso = model.joint(TORSO_JOINT).id
        data.qpos[model.jnt_qposadr[torso]] = torso_m
        data.qvel[model.jnt_dofadr[torso]] = 0.0
        self.env.sim.forward()
        self.saved_ranges = {}
        for name in (*BASE_JOINTS, TORSO_JOINT):
            joint = model.joint(name).id
            self.saved_ranges[name] = (model.jnt_range[joint].copy(), int(model.jnt_limited[joint]))
            q = data.qpos[model.jnt_qposadr[joint]]
            model.jnt_range[joint] = (q, q)
            model.jnt_limited[joint] = 1
        self.env.update_state()
        self._sync_controllers()
        self.locked_base = self.base_pose()
        self.locked_torso = float(torso_m)

    def unlock_base(self):
        """Navigation phase: restore the native base/torso ranges, resync controller goals, pass base commands."""
        model = self.env.sim.model._model
        for name, (limits, limited) in self.saved_ranges.items():
            joint = model.joint(name).id
            model.jnt_range[joint] = limits
            model.jnt_limited[joint] = limited
        self.saved_ranges = None
        self.locked_base = None
        self._sync_controllers()

    def mask_base_action(self, action):
        """Zero planar base and torso commands and force arm mode; arm and gripper entries pass unchanged."""
        action = action.copy()
        start = 0
        for name, size in self.action_spec.parts:
            if name in ("base", "torso"):
                action[start : start + size] = 0.0
            elif name == "base_mode":
                action[start : start + size] = -1.0
            start += size
        return action

    def _track_drift(self):
        pose = self.base_pose()
        d = pose - self.locked_base
        drift = np.r_[np.hypot(d[0], d[1]), abs(np.arctan2(np.sin(d[2]), np.cos(d[2])))]
        self.max_base_drift = np.maximum(self.max_base_drift, drift)
        torso = self.joint_positions()["torso"][0]
        self.max_torso_drift = max(self.max_torso_drift, abs(torso - self.locked_torso))

    def probe_station(self, x, y, yaw):
        """Geometry-only check: frozen qpos with the base at (x, y, yaw); no reset, controllers untouched.

        Used to screen station candidates (contacts at a static pose); trials still call ``set_station``.
        """
        self.env.sim.set_state_from_flattened(self.initial_state)
        self._place_base(x, y, yaw)
        return self.robot_env_contacts()

    def _place_base(self, x, y, yaw):
        sim = self.env.sim
        model, data = sim.model._model, sim.data._data
        addresses = [model.joint(name).qposadr[0] for name in BASE_JOINTS]
        data.qpos[addresses] = 0.0
        data.qvel[[model.joint(name).dofadr[0] for name in BASE_JOINTS]] = 0.0
        sim.forward()
        _, _, yaw0 = self.base_pose()
        data.qpos[addresses[2]] = yaw - yaw0
        sim.forward()
        xr, yr, _ = self.base_pose()
        slide_ids = [model.joint(name).id for name in BASE_JOINTS[:2]]
        world_axes = data.xaxis[slide_ids, :2].T
        data.qpos[addresses[:2]] = np.linalg.solve(world_axes, [x - xr, y - yr])
        sim.forward()
        reached = self.base_pose()
        error = np.r_[reached[:2] - (x, y), np.arctan2(np.sin(reached[2] - yaw), np.cos(reached[2] - yaw))]
        if np.max(np.abs(error)) > 1e-6:
            raise RuntimeError(f"base joints did not reach the station: error={error.tolist()}")

    def render(self, camera):
        width, height = self.kwargs["camera_widths"], self.kwargs["camera_heights"]
        return self.env.sim.render(width=width, height=height, camera_name=camera)[::-1].copy()

    def close(self):
        self.env.close()
