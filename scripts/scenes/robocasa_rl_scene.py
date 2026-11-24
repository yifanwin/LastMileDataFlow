"""Export a frozen RoboCasa snapshot as an LMDF-format scene (robot removed) for the mjlab RL stack.

Run in the RoboCasa env. Writes scene.xml (free bodies baked at their frozen poses), metadata.json
(target -> support parent, as SceneSource expects) and robot.json (frozen arm/torso/gripper joints).
"""
import argparse
import json
from pathlib import Path

import mujoco
import numpy as np

from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime

ROBOT_TOPS = ("robot0_base", "left_eef_target", "right_eef_target")


def root(model, body):
    while model.body_parentid[body] != 0:
        body = int(model.body_parentid[body])
    return body


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scene_dir")
    p.add_argument("output")
    a = p.parse_args()
    rt = RoboCasaRuntime(a.scene_dir)
    m, d = rt.env.sim.model._model, rt.env.sim.data._data
    spec = mujoco.MjSpec.from_string(rt.env.edit_model_xml(rt.xml))
    for name in ROBOT_TOPS:
        spec.detach_body(spec.body(name))
    for element in list(spec.actuators) + list(spec.sensors):
        element.delete()
    free = [m.body(m.jnt_bodyid[j]).name for j in range(m.njnt) if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE]
    for name in free:
        body = spec.body(name)
        body.pos, body.quat = d.xpos[m.body(name).id].copy(), d.xquat[m.body(name).id].copy()
    spec.compile()
    target = m.body(rt.target_body).name
    support_geoms = rt.support_contacts()
    supports = {m.body(root(m, m.geom(g).bodyid[0])).name for g in support_geoms}
    if len(supports) != 1:
        raise ValueError(f"target support is not one body: {supports}")
    support = supports.pop()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.xml").write_text(spec.to_xml())
    metadata = {"objects": {target: {"category": "Cup", "parent": support}, support: {"category": "Counter"}},
                "source": {"robocasa_scene": str(Path(a.scene_dir).resolve()), "mujoco": mujoco.__version__}}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=1))
    joints = rt.joint_positions()
    robot = {"arm_q": joints["right"], "torso": joints["torso"], "gripper": joints["right_gripper"],
             "target_body": target, "support_body": support, "support_geoms": list(support_geoms),
             "target_pose_world": np.r_[d.xpos[rt.target_body], d.xquat[rt.target_body]].tolist(), "free_bodies": free}
    (out / "robot.json").write_text(json.dumps(robot, indent=1))
    print(json.dumps({"bodies_removed": ROBOT_TOPS, "target": target, "support": support, "free": free, "arm_q": joints["right"]}))


if __name__ == "__main__":
    main()
