"""Explicit factory/control/alignment/fixed-pick validation; no station sampling."""
import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("factory", "control", "align", "pick"), required=True)
    p.add_argument("--robot", default="configs/robots/panda_omron.json")
    p.add_argument("--factory-root", type=Path)
    p.add_argument("--asset-output", type=Path)
    p.add_argument("--case", type=Path)
    p.add_argument("--gpu", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def floor_source(output):
    from lastmile_dataflow.scenes.loading.source import SceneSource
    path = output / "floor.xml"
    path.write_text('<mujoco><worldbody><light pos="0 0 3"/><geom name="floor" '
                    'type="plane" size="5 5 .01" rgba=".3 .35 .4 1"/></worldbody></mujoco>')
    return SceneSource("panda-floor", str(path.resolve()))


def pose_errors(a, b):
    import numpy as np
    from lastmile_dataflow.runtime.outcomes.pick import rotation_error
    return {"position_m": float(np.linalg.norm(a[:3, 3]-b[:3, 3])),
            "rotation_rad": rotation_error(a[:3, :3], b[:3, :3])}


def pivot_check(sim):
    import mujoco
    import numpy as np
    from lastmile_dataflow.runtime.control.panda_omron import PIVOT
    from lastmile_dataflow.scenes.geometry import body_pose, tcp_pose
    scratch = mujoco.MjData(sim.model)
    scratch.qpos[:] = sim.data.qpos
    robot = type(sim.robot)(sim.model, scratch, sim.robot.config)
    from types import SimpleNamespace
    check = SimpleNamespace(model=sim.model, data=scratch, robot=robot)
    addresses = [robot.addresses[n] for n in robot.groups["base"]]
    scratch.qpos[addresses] = 0.
    mujoco.mj_forward(sim.model, scratch)
    root_id = sim.model.body(robot.config.namespace+"robot0_base").id
    moving_id = sim.model.body(robot.config.namespace+"mobilebase0_base").id
    # The outer model root stays fixed; its mobile child rotates around PIVOT.
    local_tcp = tcp_pose(check, "right").copy()
    local_tcp[:3, 3] += PIVOT
    site = sim.model.site(robot.config.namespace+"base_site").id
    rows = []
    for xy in ([0., 0.], [.7, -.4]):
        for yaw in (0., np.pi/2, np.pi):
            c, s = np.cos(yaw), np.sin(yaw)
            rotation = np.array([[c,-s,0], [s,c,0], [0,0,1.]])
            b = np.r_[xy, 0.]
            scratch.qpos[addresses] = [*xy, yaw]
            mujoco.mj_forward(sim.model, scratch)
            expected_root = b - rotation @ PIVOT
            expected_tcp = np.eye(4)
            expected_tcp[:3, 3] = b + rotation @ (local_tcp[:3, 3]-PIVOT)
            expected_tcp[:3, :3] = rotation @ local_tcp[:3, :3]
            row = {"base_xyyaw": [*xy, yaw],
                   "pivot_error_m": float(np.linalg.norm(scratch.site_xpos[site]-b)),
                   "mobile_root_error_m": float(np.linalg.norm(scratch.xpos[moving_id]-expected_root)),
                   "outer_model_root_world": scratch.xpos[root_id].tolist(),
                   "tcp_error": pose_errors(tcp_pose(check, "right"), expected_tcp)}
            rows.append(row)
    valid = all(max(r["pivot_error_m"], r["mobile_root_error_m"], r["tcp_error"]["position_m"]) <= .001
                and r["tcp_error"]["rotation_rad"] <= .001 for r in rows)
    return {"valid": valid, "rows": rows}


def kinematics(robot, fixed=False):
    from curobo._src.robot.kinematics.kinematics import Kinematics
    from curobo._src.types.robot import RobotCfg
    from curobo.types import DeviceCfg
    from lastmile_dataflow.planning.panda_omron import configuration
    device = DeviceCfg()
    cfg = configuration(robot, fixed=fixed)
    cfg["kinematics"]["tool_frames"] += ["panda_finger1", "panda_finger2"]
    return Kinematics(RobotCfg.create(cfg, device_cfg=device).kinematics), device


def kinematic_state(kin, device, robot, fixed=False):
    from curobo.types import JointState
    from lastmile_dataflow.planning.panda_omron import ARM_NAMES, FULL_NAMES
    values = robot.arm_positions("right").tolist()
    if not fixed:
        values = [*robot.base_pose(), float(robot.group("lift")[0]), *values, *robot.group("gripper")]
    names = ARM_NAMES if fixed else FULL_NAMES
    js = JointState.from_position(device.to_device(values).view(1, -1), joint_names=list(names))
    return kin.compute_kinematics(js.reorder(kin.joint_names))


def sphere_image(sim, spheres, output):
    import imageio.v2 as imageio
    import mujoco
    import numpy as np
    sim.render(640, 480)
    renderer = sim.renderer
    renderer.update_scene(sim.data, camera=sim.robot.camera_names["overview"])
    for sphere in spheres:
        if sphere[3] <= 0:
            continue
        geom = renderer.scene.geoms[renderer.scene.ngeom]
        mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_SPHERE,
                           np.array([sphere[3], 0., 0.]), sphere[:3],
                           np.eye(3).ravel(), np.array([.1, .8, .2, .3], np.float32))
        renderer.scene.ngeom += 1
    imageio.imwrite(output, renderer.render())


def alignment(sim, output, seed):
    import mujoco
    import numpy as np
    from types import SimpleNamespace
    from lastmile_dataflow.planning.panda_omron import TCP
    from lastmile_dataflow.scenes.geometry import tcp_pose
    pivot = pivot_check(sim)
    kin, device = kinematics(sim.robot)
    data = mujoco.MjData(sim.model)
    data.qpos[:] = sim.data.qpos
    robot = type(sim.robot)(sim.model, data, sim.robot.config)
    robot.lift_target = sim.robot.lift_target
    scratch = SimpleNamespace(model=sim.model, data=data, robot=robot)
    rng = np.random.default_rng(seed)
    addresses = [robot.addresses[n] for n in robot.groups["arm"]]
    limits = np.array([sim.model.joint(robot.config.namespace+n).range for n in robot.groups["arm"]])
    base_addresses = [robot.addresses[n] for n in robot.groups["base"]]
    rows = []
    for _ in range(20):
        data.qpos[addresses] = rng.uniform(limits[:, 0]+.05, limits[:, 1]-.05)
        data.qpos[base_addresses] = [*rng.uniform(-1., 1., 2), rng.uniform(-np.pi, np.pi)]
        mujoco.mj_forward(sim.model, data)
        fk = kinematic_state(kin, device, robot).tool_poses.get_link_pose(TCP).get_numpy_matrix().reshape(4, 4)
        rows.append({"state": robot.state(), **pose_errors(tcp_pose(scratch, "right"), fk)})
    fk_target = all(r["position_m"] <= .002 and r["rotation_rad"] <= .005 for r in rows)
    fk_valid = all(r["position_m"] <= .005 and r["rotation_rad"] <= .01 for r in rows)
    fingers = []
    for width in (.08, 0.):
        for name, value in zip(sim.robot.groups["gripper"], [width/2, -width/2]):
            data.qpos[robot.addresses[name]] = value
        data.qpos[addresses] = sim.robot.arm_positions("right")
        data.qpos[base_addresses] = 0.
        mujoco.mj_forward(sim.model, data)
        state = kinematic_state(kin, device, robot)
        spheres = state.robot_spheres.detach().cpu().numpy().reshape(-1, 4)
        from lastmile_dataflow.scenes.geometry import body_pose
        finger_errors = []
        for i, side in ((1, "left"), (2, "right")):
            actual = body_pose(scratch, sim.model.body(robot.config.namespace+f"gripper0_right_{side}finger").id)
            predicted = state.tool_poses.get_link_pose(f"panda_finger{i}").get_numpy_matrix().reshape(4, 4)
            finger_errors.append(pose_errors(actual, predicted))
        fingers.append({"width_m": width, "spheres": spheres.tolist(), "frame_errors": finger_errors})
        # Render actual open/closed geometry on a separate preparation instance.
        saved = sim.data.qpos.copy()
        sim.data.qpos[:] = data.qpos
        mujoco.mj_forward(sim.model, sim.data)
        sphere_image(sim, spheres, output/f'collision-width-{width:.2f}.png')
        sim.data.qpos[:] = saved
        mujoco.mj_forward(sim.model, sim.data)
    fixed, device = kinematics(sim.robot, fixed=True)
    pose = kinematic_state(fixed, device, sim.robot, fixed=True).tool_poses.get_link_pose(TCP).get_numpy_matrix().reshape(4, 4)
    fixed_error = pose_errors(tcp_pose(sim, "right"), pose)
    probes = collision_probes(sim, kin, device, output)
    finger_valid = all(e["position_m"] <= .005 and e["rotation_rad"] <= .01 for r in fingers for e in r["frame_errors"])
    return {"valid": bool(finger_valid and all(r["valid"] for r in probes) and pivot["valid"] and fk_valid and fixed_error["position_m"] <= .005
            and fixed_error["rotation_rad"] <= .01), "fk_target_met": fk_target,
            "fk_target": {"position_m": .002, "rotation_rad": .005},
            "fk_max_allowed": {"position_m": .005, "rotation_rad": .01},
            "pivot": pivot, "random_fk": rows, "fixed_fk": fixed_error,
            "max_position_m": max(r["position_m"] for r in rows),
            "max_rotation_rad": max(r["rotation_rad"] for r in rows),
            "collision_geometry": fingers, "collision_probes": probes}


def collision_probes(sim, kin, device, output):
    import mujoco
    from curobo._src.geom.types import SceneCfg, Cuboid
    from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg
    from curobo._src.geom.collision.buffer_collision import CollisionBuffer
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.scenes.loading.source import SceneSource
    path = output / "collision-probe.xml"
    path.write_text('<mujoco><worldbody><geom name="floor" type="plane" size="5 5 .01"/>'
                    '<body name="probe" mocap="true" pos="3 3 3">'
                    '<geom name="probe_box" type="box" size=".025 .025 .025"/>'
                    '</body></worldbody></mujoco>')
    probe_sim = Simulation.from_source(SceneSource("collision-probe", str(path.resolve())), sim.robot.config)
    results = []
    try:
        robot_geoms = [g for g in range(probe_sim.model.ngeom)
                       if probe_sim.model.body(int(probe_sim.model.geom_bodyid[g])).name.startswith(sim.robot.config.namespace)
                       and (probe_sim.model.geom_contype[g] or probe_sim.model.geom_conaffinity[g])]
        probe_id = probe_sim.model.geom("probe_box").id
        state = kinematic_state(kin, device, probe_sim.robot)
        for name, position, expected in (("separated", [3., 3., 3.], False),
                                         ("base_overlap", [.01, -.18, .12], True)):
            probe_sim.data.mocap_pos[0] = position
            mujoco.mj_forward(probe_sim.model, probe_sim.data)
            actual = min(mujoco.mj_geomDistance(probe_sim.model, probe_sim.data, probe_id, g, 10., None)
                         for g in robot_geoms)
            scene = SceneCfg(cuboid=[Cuboid(name="probe", pose=[*position, 1., 0., 0., 0.], dims=[.05, .05, .05])])
            checker = SceneCollision.from_config(SceneCollisionCfg(device_cfg=device, scene_model=scene))
            buffer = CollisionBuffer.from_shape(state.robot_spheres.shape, device)
            costs = checker.get_sphere_distance(state, buffer, device.to_device([1.]), device.to_device([0.]))
            collision = float(costs.max().item()) > 0.
            results.append({"case": name, "position": position, "mujoco_min_distance_m": float(actual),
                            "v2_max_collision_cost": float(costs.max().item()),
                            "valid": bool((actual < 0.) == expected and collision == expected)})
    finally:
        probe_sim.close()
    return results


def control(sim, output):
    import imageio.v2 as imageio
    import numpy as np
    from lastmile_dataflow.runtime.control.interface import RobotCommand
    from lastmile_dataflow.runtime.execution.context import semantic_tick
    initial = sim.robot.arm_positions("right")
    moved = initial + np.array([.1, -.1, .1, .15, -.1, -.1, .1])
    commands = [("hold", RobotCommand()),
                ("base_translate", RobotCommand(base_xyyaw=[.25, .15, 0.])),
                ("base_yaw", RobotCommand(base_xyyaw=[.25, .15, np.pi/2])),
                ("arm", RobotCommand(arm_q={"right": moved})),
                ("gripper_close", RobotCommand(gripper_width={"right": 0.})),
                ("gripper_half", RobotCommand(gripper_width={"right": .04})),
                ("gripper_open", RobotCommand(gripper_width={"right": .08}))]
    writers = {name: imageio.get_writer(output/f'{name}.mp4', fps=4)
               for name in sim.robot.camera_names}
    rows, max_lift_error = [], 0.
    def sample():
        nonlocal max_lift_error
        max_lift_error = max(max_lift_error, abs(sim.robot.group("lift")[0]-sim.robot.lift_target))
    try:
        for phase, command in commands:
            for i in range(100):
                semantic_tick(sim, command, sample=sample)
                if i % 5 == 0:
                    for name, frame in sim.render(640, 480).items():
                        writers[name].append_data(frame)
            actual = sim.robot.state()
            target = sim.robot.state_targets()
            base_error = np.abs(np.asarray(actual["base_xyyaw"])-target["base_xyyaw"])
            arm_error = float(np.max(np.abs(np.asarray(actual["arm_q"]["right"])-target["arm_q"]["right"])))
            width_error = abs(actual["gripper_width"]["right"]-target["gripper_width"]["right"])
            valid = np.linalg.norm(base_error[:2]) <= .005 and base_error[2] <= .01 and arm_error <= .02 and width_error <= .002
            rows.append({"phase": phase, "actual": actual, "target": target,
                         "base_error": base_error.tolist(), "arm_error_rad": arm_error,
                         "width_error_m": width_error, "valid": bool(valid)})
        for name, pixels in sim.render(640, 480).items():
            imageio.imwrite(output/f'{name}-final.png', pixels)
    finally:
        for writer in writers.values():
            writer.close()
    return {"valid": bool(all(r["valid"] for r in rows) and max_lift_error <= .002),
            "max_lift_error_m": float(max_lift_error), "phases": rows}


def main():
    args = arguments()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu), MUJOCO_GL="egl")
    os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/panda-omron-numba")
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("WARP_CACHE_PATH", str(Path("outputs/dependencies/panda-omron-warp").resolve()))
    from lastmile_dataflow.config import load_robot_config
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.runtime.control.panda_omron import export_factory
    config = load_robot_config(args.robot)
    sim = None
    result = {"stage": args.stage, "gpu": args.gpu, "seed": args.seed, "status": "error"}
    try:
        if args.stage == "factory":
            if args.factory_root is None:
                raise ValueError("factory stage requires --factory-root")
            model = export_factory(args.factory_root, args.asset_output or args.output/"assets")
            config = replace(config, model_path=str(model.resolve()))
        if args.stage == "pick":
            from lastmile_dataflow.runtime.execution.fixed_pick import run_fixed_pick
            result.update(run_fixed_pick(config, args.case, args.output, args.seed))
        else:
            sim = Simulation.from_source(floor_source(args.output), config)
            if args.stage == "control":
                result.update(control(sim, args.output))
            elif args.stage == "align":
                result.update(alignment(sim, args.output, args.seed))
            else:
                from lastmile_dataflow.runtime.control.interface import RobotCommand
                from lastmile_dataflow.runtime.execution.context import semantic_tick
                for _ in range(100):
                    semantic_tick(sim, RobotCommand())
                result.update(valid=not any(w.number for w in sim.data.warning),
                              model_path=config.model_path, nq=sim.model.nq, nu=sim.model.nu,
                              physics_time_s=float(sim.data.time), state=sim.robot.state())
        result["status"] = "passed" if result["valid"] else "failed"
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        result["robot"] = asdict(config)
        if sim is not None:
            sim.close()
        (args.output/"result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in result.items() if k in ("stage", "status", "max_position_m", "max_rotation_rad", "max_lift_error_m")}), flush=True)
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
