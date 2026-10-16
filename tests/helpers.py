"""Shared synthetic fixtures, not real RBY-1 acceptance evidence."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from lastmile_dataflow.config import CollectionConfig, RobotConfig
from lastmile_dataflow.utils.io import append_row, create_run
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.loading.source import SceneSource

ROOT = Path(__file__).resolve().parents[1]


def fixtures(root):
    root = Path(root)
    robot_path = root / "robot.xml"
    xml = ET.Element("mujoco", model="synthetic_protocol_fixture")
    ET.SubElement(xml, "compiler", angle="radian", autolimits="true")
    default = ET.SubElement(xml, "default")
    ET.SubElement(default, "joint", damping="10", armature="1")
    ET.SubElement(default, "position", kp="100", kv="20", ctrllimited="true", ctrlrange="-25 25")
    world = ET.SubElement(xml, "worldbody")
    body = ET.SubElement(world, "body", name="robot_0/base", pos="0 0 .5")
    ET.SubElement(body, "geom", type="sphere", size=".02", mass="1", contype="0", conaffinity="0")
    actuators = ET.SubElement(xml, "actuator")
    for name, kind, axis, ran in [
        ("base_x", "slide", "1 0 0", "-25 25"),
        ("base_y", "slide", "0 1 0", "-25 25"),
        ("base_theta", "hinge", "0 0 1", "-3.14 3.14"),
    ]:
        ET.SubElement(body, "joint", name="robot_0/" + name, type=kind, axis=axis, range=ran)
        ET.SubElement(
            actuators, "position", name="robot_0/" + name + "_act", joint="robot_0/" + name, ctrlrange=ran
        )
    for group, count in [("torso", 6), ("head", 2), ("left_arm", 7), ("right_arm", 7)]:
        child = body
        for i in range(count):
            name = f"{group}_{i}"
            child = ET.SubElement(child, "body", name="robot_0/" + name + "_link", pos="0 0 .02")
            ET.SubElement(child, "geom", type="sphere", size=".02", mass="1", contype="0", conaffinity="0")
            ET.SubElement(
                child,
                "joint",
                name="robot_0/" + name,
                axis=("1 0 0", "0 1 0", "0 0 1")[i % 3],
                range="-3.14 3.14",
            )
            actname = (
                f"link{i + 1}_act"
                if group == "torso"
                else f"head_{i}_act"
                if group == "head"
                else f"{group}_{i + 1}_act"
            )
            ET.SubElement(
                actuators,
                "position",
                name="robot_0/" + actname,
                joint="robot_0/" + name,
                ctrlrange="-3.14 3.14",
            )
    for side in ["left", "right"]:
        for i in (1, 2):
            name = f"gripper_finger_{side[0]}{i}"
            child = ET.SubElement(body, "body", name="robot_0/" + name, pos="0 0 .1")
            ET.SubElement(child, "geom", type="sphere", size=".02", mass="1", contype="0", conaffinity="0")
            ET.SubElement(
                child,
                "joint",
                name="robot_0/" + name,
                type="slide",
                axis="1 0 0",
                range="-.05 0" if i == 1 else "0 .05",
            )
            if i == 1:
                ET.SubElement(
                    actuators, "motor", name="robot_0/" + side + "_finger_act", joint="robot_0/" + name
                )
    for name in ("head_camera", "wrist_camera_l", "wrist_camera_r"):
        ET.SubElement(body, "camera", name="robot_0/" + name, pos="1 0 1", xyaxes="0 1 0 -.7 0 .7")
    ET.ElementTree(xml).write(robot_path)
    scene = root / "scene.xml"
    scene.write_text("""<mujoco model="synthetic_scene"><compiler angle="radian"/>
    <worldbody><geom name="floor" type="plane" size="0 0 .01"/>
    <body name="target" pos="2 0 .15"><freejoint name="target_free"/><geom name="target_geom" type="sphere" size=".1" mass=".1"/></body>
    </worldbody></mujoco>""")
    config = RobotConfig(str(robot_path))
    source = SceneSource("unit-scene", str(scene))
    return source, config


def make_sim(root):
    source, robot = fixtures(root)
    sim = Simulation.from_source(source, robot, target="target")
    return sim, robot


def table_scene(root):
    source, robot = fixtures(root)
    Path(source.xml_path).write_text("""<mujoco><compiler angle="radian"/>
    <worldbody><light pos="2 0 3" dir="0 0 -1" diffuse=".8 .8 .8"/><geom name="floor" type="plane" size="0 0 .01"/>
    <body name="table" pos="2 0 .4"><geom name="table_top" type="box" size=".6 .5 .04" rgba=".7 .6 .4 1"/></body>
    <body name="target" pos="2 0 .50"><freejoint name="target_free"/><geom name="target_geom" type="box" size=".04 .04 .05" rgba=".9 .1 .1 1" mass=".1"/></body>
    <body name="neighbor" pos="2.3 .2 .50"><freejoint name="neighbor_free"/><geom name="neighbor_geom" type="box" size=".04 .04 .05" rgba=".1 .2 .9 1" mass=".1"/></body>
    </worldbody></mujoco>""")
    return source, robot


def evidence(duration=2.2, *, support=False, gap=False, slip=False):
    """A complete synthetic hold trace; variants change one physical condition."""
    initial = {
        "base": [0.0, 0.0, 0.0],
        "head": [0.0, 0.0],
        "idle_arm": [0.0] * 7,
        "target": "fixture",
        "height_m": 0.8,
        "fingers": [1, 2],
        "workspace_center_xy": [0.0, 0.0],
        "workspace_radius_m": 2.0,
    }
    samples = []
    for i in range(round(duration / 0.004) + 1):
        pose = np.eye(4)
        if slip:
            pose[0, 3] = i * 0.004
        samples.append(
            {
                "time_s": i * 0.004,
                "phase": "hold",
                "command_h": 0.0,
                "base": [0.0, 0.0, 0.0],
                "head": [0.0, 0.0],
                "idle_arm": [0.0] * 7,
                "torso": [0.0] * 6,
                "target": "fixture",
                "height_m": 0.9,
                "support_contact": support,
                "finger_forces_n": {"1": 1.0, "2": 1.0},
                "relative_pose": pose.tolist(),
                "illegal": [],
                "sampler": "force-aware-v3",
            }
        )
    if gap:
        samples.pop(1)
    return initial, samples


class DatasetTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.robot = make_sim(self.root)
        self.collection = CollectionConfig(record_video=False, record_images=False)
        self.task = {
            "task_id": "pick-target",
            "target_body": "target",
            "instance_id": "target",
            "operation": "pick",
            "asset_id": "fixture",
            "category": "Cup",
            "anchor_world": [2.0, 0.0, 0.15],
            "instruction": "pick the target",
        }
        initial = self.robot.initial
        self.points = [
            {
                "station_id": "s0",
                "geometry": "valid",
                "xy": [0.0, 0.0],
                "base": [0.0, 0.0, 0.0],
                "initial": initial,
            },
            {
                "station_id": "s1",
                "geometry": "valid",
                "xy": [0.4, 0.0],
                "base": [0.4, 0.0, 0.0],
                "initial": {**initial, "base": [0.4, 0.0, 0.0]},
            },
        ]
        self.scene = {
            "scene_id": "one",
            "task": self.task,
            "stations": self.points,
            "scene": "scene-files/one",
            "status": "accepted",
        }

    def tearDown(self):
        self.sim.close()
        self.tmp.cleanup()

    def dataset(self):
        dataset = create_run(self.root / "dataset", {"geometry": {}, "seed": 3})
        self.sim.freeze(dataset / self.scene["scene"])
        append_row(dataset / "scenes.jsonl", self.scene)
        return dataset
