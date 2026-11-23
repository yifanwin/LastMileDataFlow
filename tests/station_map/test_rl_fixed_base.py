"""Physical base topology, station transforms and state transfer without assets."""

import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import mujoco
import numpy as np
from mjlab.entity import Entity, EntityCfg

from lastmile_dataflow.station_map.rl.env.scene import fix_robot_base
from lastmile_dataflow.station_map.rl.env.stations import map_joint_positions, station_mocap_poses


ROBOT_XML = """
<mujoco>
  <worldbody>
    <body name="robot_0/robot0_base" pos="0.21 0.02 0">
      <body name="robot_0/mobilebase0_base">
        <joint name="robot_0/mobilebase0_joint_mobile_forward" type="slide" axis="1 0 0"/>
        <joint name="robot_0/mobilebase0_joint_mobile_side" type="slide" axis="0 1 0"/>
        <joint name="robot_0/mobilebase0_joint_mobile_yaw" axis="0 0 1" pos="-0.21 0 0"/>
        <geom type="sphere" size="0.05" mass="1"/>
        <site name="robot_0/base_site" pos="-0.21 0 0"/>
        <body name="lift" pos="0 0 0.3">
          <joint name="robot_0/mobilebase0_joint_torso_height" type="slide" axis="0 0 1"/>
          <geom type="sphere" size="0.05" mass="1"/>
        </body>
      </body>
    </body>
    <body name="cube" pos="1 2 3">
      <freejoint name="cube_free"/>
      <geom type="box" size="0.02 0.02 0.02" mass="0.1"/>
    </body>
  </worldbody>
  <actuator>
    <motor name="robot_0/mobilebase0_actuator_mobile_forward" joint="robot_0/mobilebase0_joint_mobile_forward"/>
    <motor name="robot_0/mobilebase0_actuator_mobile_side" joint="robot_0/mobilebase0_joint_mobile_side"/>
    <motor name="robot_0/mobilebase0_actuator_mobile_yaw" joint="robot_0/mobilebase0_joint_mobile_yaw"/>
    <motor name="lift_motor" joint="robot_0/mobilebase0_joint_torso_height"/>
  </actuator>
</mujoco>
"""


class PhysicalBaseTests(unittest.TestCase):
    def fixed_model(self):
        spec = mujoco.MjSpec.from_string(ROBOT_XML)
        # The cube belongs to the scene, not the fixed robot entity.
        spec.delete(spec.body("cube"))
        fix_robot_base(spec)
        return Entity(EntityCfg(spec_fn=lambda: spec)).spec.compile()

    def test_only_base_dynamics_removed_and_force_cannot_move_base(self):
        model = self.fixed_model()
        self.assertEqual((model.nq, model.nv, model.nu, model.nmocap), (1, 1, 1, 1))
        self.assertEqual(model.joint(0).name, "robot_0/mobilebase0_joint_torso_height")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        base = model.site("robot_0/base_site").id
        before = data.site_xpos[base].copy()
        rotation = data.site_xmat[base].copy()
        data.xfrc_applied[model.body("robot_0/mobilebase0_base").id] = [500, -500, 500, 100, 100, 100]
        for _ in range(100):
            mujoco.mj_step(model, data)
        np.testing.assert_array_equal(data.site_xpos[base], before)
        np.testing.assert_array_equal(data.site_xmat[base], rotation)

    def test_station_transform_handles_root_offset_and_yaw(self):
        model = self.fixed_model()
        base = model.site("robot_0/base_site").id
        stations = [dict(x=6.6, y=2.6, yaw=angle) for angle in (0, np.pi / 2, -2.4)]
        poses = station_mocap_poses(model, base, stations)
        data = mujoco.MjData(model)
        for row, pose in zip(stations, poses):
            data.mocap_pos[0], data.mocap_quat[0] = pose[:3], pose[3:]
            mujoco.mj_forward(model, data)
            np.testing.assert_allclose(data.site_xpos[base], [row["x"], row["y"], 0], atol=1e-12)
            rotation = data.site_xmat[base].reshape(3, 3)
            self.assertAlmostEqual(np.arctan2(rotation[1, 0], rotation[0, 0]), row["yaw"])

    def test_named_state_mapping_preserves_free_object_and_lift_after_removal(self):
        spec = mujoco.MjSpec.from_string(ROBOT_XML)
        source = spec.compile()
        qpos = source.qpos0.copy()
        qpos[source.joint("cube_free").qposadr[0]:] = [4, 5, 6, 1, 0, 0, 0]
        qpos[source.joint("robot_0/mobilebase0_joint_torso_height").qposadr[0]] = 0.2
        fix_robot_base(spec)
        target = mujoco.MjSpec.from_string(spec.to_xml().replace('robot_0/', 'robot/robot_0/')).compile()
        mapped = map_joint_positions(source, qpos, target)
        self.assertEqual(len(mapped), len(qpos) - 3)
        self.assertAlmostEqual(mapped[target.joint("robot/robot_0/mobilebase0_joint_torso_height").qposadr[0]], 0.2)
        np.testing.assert_array_equal(mapped[target.joint("cube_free").qposadr[0]:], [4, 5, 6, 1, 0, 0, 0])

    def test_video_restores_mocap_pose_before_forward(self):
        from lastmile_dataflow.station_map.rl.video import render_rollout

        model = self.fixed_model()
        base = model.site("robot_0/base_site").id
        positions = []

        def render(sim, renderer, width, height):
            positions.append(sim.data.site_xpos[base].copy())
            return np.zeros((height, width, 3), dtype=np.uint8)

        camera = SimpleNamespace(render=render, metadata=lambda: {})
        settings = {"width": 32, "height": 32, "target_body": "lift"}
        with TemporaryDirectory() as directory, patch(
            "lastmile_dataflow.station_map.rl.video.ThirdPersonCamera", return_value=camera
        ), patch("lastmile_dataflow.station_map.rl.video.mujoco.Renderer", return_value=nullcontext(None)), patch(
            "lastmile_dataflow.station_map.rl.video.video_writer",
            return_value=nullcontext(SimpleNamespace(append_data=lambda frame: None)),
        ):
            render_rollout(model, np.array([[0.1], [0.2]]), ["first", "last"], Path(directory) / "video.mp4",
                           "fixture", "timeout", settings, 0.05,
                           mocap_pos=np.array([[3., 4., 0.]]), mocap_quat=np.array([[1., 0., 0., 0.]]))
        self.assertEqual(len(positions), 4)
        np.testing.assert_allclose(positions, np.tile([3., 4.02, 0.], (4, 1)), atol=1e-12)
