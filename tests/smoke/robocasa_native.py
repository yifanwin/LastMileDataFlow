"""Explicit real check of frozen RoboCasa365 instances through the runtime interface (RoboCasa environment).

Per instance: create, render, close/reload, frozen-state reset, native action execution, repeated
set_station, single unscaled robot, and step-by-step agreement with the native Environment driven
directly through robocasa's own playback path. The pick judge runs on the same rollout; a random
rollout is not grasp evidence, so this script never reports pick acceptance.
"""

import argparse
import json
import time
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def max_diff(left, right):
    import numpy as np

    return float(np.max(np.abs(np.asarray(left, float) - np.asarray(right, float))))


def native_direct(scene_dir):
    """The native Environment restored the way robocasa's playback restores a demonstration."""
    from lastmile_dataflow.sim.robocasa_runtime import ENV_FILE, META_FILE, STATE_FILE, XML_FILE, make_env
    import numpy as np

    env = make_env(json.loads((scene_dir / ENV_FILE).read_text())["kwargs"])
    env.set_ep_meta(json.loads((scene_dir / META_FILE).read_text()))
    env.reset()
    env.reset_from_xml_string(env.edit_model_xml((scene_dir / XML_FILE).read_text()))
    env.sim.reset()
    with np.load(scene_dir / STATE_FILE) as frozen:
        env.sim.set_state_from_flattened(frozen["states"])
    env.sim.forward()
    env.update_state()
    return env


def flat(runtime):
    """Current flattened native state; env.sim is replaced on every reset, so never cache it."""
    return runtime.env.sim.get_state().flatten()


def robot_env_contacts(runtime):
    model, data = runtime.env.sim.model._model, runtime.env.sim.data._data
    count = 0
    for contact in data.contact[: data.ncon]:
        bodies = [int(model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2)]
        count += sum(b in runtime.robot_bodies for b in bodies) == 1
    return count


def hold(runtime, steps=10):
    """Zero arm/torso/base commands in arm mode for ``steps`` control periods; drift from the start."""
    import numpy as np

    start = runtime.get_state()
    zero = np.zeros(runtime.action_spec.dim)
    zero[-1] = -1.0
    for _ in range(steps):
        runtime.step(zero)
    end = runtime.get_state()
    return {
        "arm_drift_rad": max_diff(end.robot["joints"]["right"], start.robot["joints"]["right"]),
        "torso_drift_m": max_diff(end.robot["joints"]["torso"], start.robot["joints"]["torso"]),
        "base_drift": max_diff(end.robot["base_xyyaw"], start.robot["base_xyyaw"]),
    }


def check_robot(runtime, robot_config):
    model = runtime.env.sim.model._model
    names = [model.body(b).name for b in range(model.nbody)]
    link1 = model.body("robot0_link1").id
    finger = model.joint("gripper0_right_finger_joint1").id
    return {
        "native_robots": len(runtime.env.robots),
        "robot_roots": [n for n in names if n.endswith("_base") and n.startswith("robot")],
        "lmdf_namespace_bodies": sum(n.startswith("robot_0/") for n in names),
        "config_scale": robot_config.scale,
        # Factory Panda values: link1 at z 0.333 m, finger travel 0.04 m (0.9 * those under LMDF scaling).
        "link1_z_m": float(model.body_pos[link1][2]),
        "finger_upper_m": float(model.jnt_range[finger][1]),
    }


def check_instance(record, root, output, steps, seed):
    import numpy as np
    from PIL import Image

    from lastmile_dataflow.operation.outcomes.robocasa_pick import PickJudge
    from lastmile_dataflow.robots.registry import load_robot_config
    from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
    from lastmile_dataflow.utils.io import jsonable, write_json

    scene_dir = root / record["scene"]
    out = output / record["scene"].split("/")[-1]
    out.mkdir(parents=True)
    with np.load(scene_dir / "initial_state.npz") as frozen:
        initial = frozen["states"]
    result = {"scene_id": record["scene_id"], "target": record["target"]["name"]}

    start = time.monotonic()
    runtime = RoboCasaRuntime(scene_dir, target=record["target"]["name"])
    result["create_s"] = time.monotonic() - start
    result["created_state_max_diff"] = max_diff(flat(runtime), initial)
    frame = runtime.render("robot0_agentview_left")
    Image.fromarray(frame).save(out / "reload_robot0_agentview_left.png")
    preview = np.asarray(Image.open(scene_dir / "preview_robot0_agentview_left.png"))
    result["render"] = {"shape": list(frame.shape), "max_pixel_diff_vs_import_preview": int(np.abs(frame.astype(int) - preview).max())}
    result["robot"] = check_robot(runtime, load_robot_config(record["robot_config"]))
    result["action_spec"] = jsonable(runtime.action_spec)

    rng = np.random.default_rng(seed)
    actions = rng.uniform(-1, 1, size=(steps, runtime.action_spec.dim))
    actions[:, -1] = -1.0  # arm mode
    actions[:, 7:10] *= 0.3  # moderate base velocity commands
    first = runtime.reset()
    judge = PickJudge(runtime.target, first.target_pose[2])
    wrapped, statuses, native = [], [], []
    for action in actions:
        step = runtime.step(action)
        wrapped.append(flat(runtime))
        native.append(step.native["task_success"])
        statuses.append(judge.update(runtime.get_state()))
    moved = max_diff(wrapped[-1], initial)
    result["native_actions"] = {
        "steps": steps,
        "time_s": wrapped[-1][0],
        "state_change_max": moved,
        "native_task_success_any": bool(any(native)),
        "pick_success_any": bool(any(s.success for s in statuses)),
        "max_lift_m": max(s.lift_m for s in statuses),
        "grasped_steps": sum(s.grasped for s in statuses),
    }
    write_json(out / "rollout_pick_status.json", [jsonable(s) for s in statuses])

    after = runtime.reset()
    result["reset_after_rollout_max_diff"] = max_diff(flat(runtime), initial)
    result["reset_steps_counter"] = runtime.steps
    result["reset_target_pose_diff"] = max_diff(after.target_pose, first.target_pose)

    direct = native_direct(scene_dir)
    try:
        result["direct_initial_max_diff"] = max_diff(direct.sim.get_state().flatten(), initial)
        diffs = []
        for action, state in zip(actions, wrapped):
            direct.step(action)
            diffs.append(max_diff(direct.sim.get_state().flatten(), state))
        result["wrapper_vs_direct"] = {"steps": steps, "max_state_diff": max(diffs), "final_state_diff": diffs[-1]}
    finally:
        direct.close()

    runtime.reset()
    contacts = robot_env_contacts(runtime)
    result["hold_after_reset"] = {**hold(runtime), "robot_env_contacts": contacts}

    base = np.asarray(first.robot["base_xyyaw"])
    joints = first.robot["joints"]
    non_base = [k for k in joints if k != "base" and joints[k]]
    for name, station in (("initial_pose", base), ("offset_pose", base + np.array([0.10, -0.05, 0.20]))):
        states, flats = [], []
        for _ in range(2):
            states.append(runtime.set_station(*station))
            flats.append(flat(runtime))
        result[f"set_station_{name}"] = {
            "requested": station.tolist(),
            "pose_error_max": max(max_diff(s.robot["base_xyyaw"], station) for s in states),
            "repeat_state_diff": max_diff(*flats),
            "other_joints_diff_vs_initial": max(max_diff(states[-1].robot["joints"][k], joints[k]) for k in non_base),
            "target_pose_diff_vs_initial": max_diff(states[-1].target_pose, first.target_pose),
            "robot_env_contacts": robot_env_contacts(runtime),
            "hold_10_steps": hold(runtime),
        }
        np.savez_compressed(out / f"station_{name}_state.npz", states=flats[-1])
    runtime.close()

    reloaded = RoboCasaRuntime(scene_dir, target=record["target"]["name"])
    result["reload_state_max_diff"] = max_diff(reloaded.env.sim.get_state().flatten(), initial)
    reloaded.close()
    write_json(out / "validation.json", result)
    return result


def passed(result):
    robot = result["robot"]
    stations = [result["set_station_initial_pose"], result["set_station_offset_pose"]]
    checks = {
        "created_at_frozen_state": result["created_state_max_diff"] == 0,
        "render_matches_import_preview": result["render"]["max_pixel_diff_vs_import_preview"] == 0,
        "native_actions_move_state": result["native_actions"]["state_change_max"] > 1e-3,
        "reset_restores_frozen_state": result["reset_after_rollout_max_diff"] == 0 and result["reset_steps_counter"] == 0,
        "reload_restores_frozen_state": result["reload_state_max_diff"] == 0,
        "direct_native_same_initial": result["direct_initial_max_diff"] == 0,
        "wrapper_matches_direct_native": result["wrapper_vs_direct"]["max_state_diff"] == 0,
        "station_reached": all(s["pose_error_max"] < 1e-6 for s in stations),
        "station_repeatable": all(s["repeat_state_diff"] == 0 for s in stations),
        "station_keeps_arm_lift_gripper": all(s["other_joints_diff_vs_initial"] < 1e-9 for s in stations),
        "station_keeps_objects": all(s["target_pose_diff_vs_initial"] < 1e-9 for s in stations),
        "single_unscaled_robot": robot["native_robots"] == 1
        and robot["robot_roots"] == ["robot0_base"]
        and robot["lmdf_namespace_bodies"] == 0
        and robot["config_scale"] == 1.0
        and abs(robot["link1_z_m"] - 0.333) < 1e-9
        and abs(robot["finger_upper_m"] - 0.04) < 1e-9,
    }
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=Path, required=True, help="RoboCasa scene run from scripts/scenes/build.py")
    parser.add_argument("--output", type=Path, required=True, help="new validation run directory")
    parser.add_argument("--gpu", type=int, required=True, help="physical GPU used for EGL rendering")
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    select_devices([args.gpu])
    setup_logging()
    from lastmile_dataflow.utils.io import create_run, rows, write_json

    output = create_run(args.output, {"scenes": str(args.scenes.resolve()), "steps": args.steps, "seed": args.seed, "gpu": args.gpu})
    results = {}
    for record in rows(args.scenes / "scenes.jsonl"):
        if record["status"] == "accepted":
            checks = passed(check_instance(record, args.scenes.resolve(), output, args.steps, args.seed))
            results[record["scene_id"]] = {"checks": checks, "all_passed": all(checks.values())}
    summary = {"instances": results, "all_passed": all(r["all_passed"] for r in results.values()), "pick_acceptance": "not_evaluated_random_rollout"}
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
