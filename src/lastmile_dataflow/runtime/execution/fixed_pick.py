"""Fixed-scene pick composition of the shared planner, stepping and monitor."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import imageio.v2 as imageio
import numpy as np

from ...config import read_config
from ...planning.curobo_v2 import V2Planner
from ...scenes.geometry import body_pose, tcp_pose
from ...scenes.loading.source import SceneSource
from ...scenes.tasks.discovery import grasp_candidates, local_bounds
from ...utils.io import write_json
from ..control.interface import RobotCommand
from ..outcomes.pick import PickMonitor, evaluate_pick
from ..simulation import Simulation
from .context import follow_arm_points, semantic_tick, OperationFailure


def run_fixed_pick(robot_config, case_path, output, seed):
    case = read_config(case_path, data_fields=("scene_xml", "metadata", "assets_dir"))
    metadata = json.loads(Path(case["metadata"]).read_text())["objects"]
    target = next(name for name, obj in metadata.items()
                  if obj["asset_id"] == case["target_asset"] and
                  obj["object_id"] == case["source_object_id"])
    initial = deepcopy(robot_config.initial)
    initial.update(base_xyyaw=case["base_xyyaw"], lift=case["lift"],
                   gripper_width={"right": case["gripper_width"]})
    robot_config = replace(robot_config, initial=initial)
    source = SceneSource("val_103_fixed_pick", case["scene_xml"], case["metadata"])
    print("loading fixed scene", flush=True)
    sim = Simulation.from_source(source, robot_config, target=target)
    planner = None
    writers = {}
    facts = []
    phase = "settle"
    monitor = None
    ticks = 0
    def frame():
        nonlocal ticks
        ticks += 1
        if ticks % 2:
            return
        for camera, pixels in sim.render(640, 480).items():
            if camera not in writers:
                writers[camera] = imageio.get_writer(output/f'{camera}.mp4', fps=robot_config.control_hz/2)
            writers[camera].append_data(pixels)
    def sample():
        if monitor is not None:
            monitor.phase = phase
            facts.append(monitor.sample())
    try:
        sim.enable_third_person()
        for _ in range(20):
            semantic_tick(sim, RobotCommand())
        lo, hi = local_bounds(sim, sim.target_id)
        task = {"operation": "pick", "asset_id": case["target_asset"],
                "target_body": target, "local_bounds": [lo.tolist(), hi.tolist()]}
        pool = grasp_candidates(task, case["assets_dir"])
        goals = [body_pose(sim, sim.target_id) @ c["pose_local"] for c in pool]
        # A fixed candidate subset, ordered by travel from the actual TCP.
        current = tcp_pose(sim, "right")
        order = sorted(range(len(goals)), key=lambda i: np.linalg.norm(goals[i][:3,3]-current[:3,3]))
        order = order[:case["max_candidates"]]
        goals = [goals[i] for i in order]
        angle = case["grasp_roll_rad"]
        roll = np.array([[np.cos(angle), -np.sin(angle), 0.],
                         [np.sin(angle), np.cos(angle), 0.], [0., 0., 1.]])
        for goal in goals:
            goal[:3, 3] += goal[:3, 2] * case["approach_offset_m"]
            goal[:3, :3] = goal[:3, :3] @ roll
        write_json(output/"case.json", {**case, "seed": seed,
                   "grasp_rows": [pool[i]["row"] for i in order],
                   "grasp_sources": [pool[i]["source"] for i in order]})
        options = SimpleNamespace(seed=seed, head_fov_enabled=False, width=640, height=480,
            head_fov_margin=.9, head_fov_weight=10000., head_fov_min_depth_m=.01,
            num_ik_seeds=case["num_ik_seeds"], num_trajopt_seeds=case["num_trajopt_seeds"],
            max_candidates=len(goals), time_dilation=case["time_dilation"])
        center = sim.data.xpos[sim.target_id].copy()
        directory = output/"planning-approach"
        directory.mkdir()
        print("planner setup", directory.name, flush=True)
        planner = V2Planner(sim, options, "right", directory, workspace=(center, 2.))
        success, index, stages, diagnostic = planner.grasp(goals, lambda p: print('planning',p,flush=True), lift=False)
        if not success:
            return {"valid": False, "failure_phase": "planning", "diagnostic": diagnostic}
        monitor = PickMonitor(sim, "right")
        write_json(output/"pick_initial.json", monitor.initial)
        for phase, key in (("pregrasp", "pregrasp"), ("approach", "approach")):
            follow_arm_points(sim, stages[key], "right", case["gripper_width"], sample=sample, on_tick=frame)
        phase = "close"
        for _ in range(20):
            semantic_tick(sim, RobotCommand(gripper_width={"right": 0.}), sample=sample, fixed_base=True)
            frame()
        if len(facts[-1]["finger_forces_n"]) != 2:
            return {"valid": False, "failure_phase": phase, "reason": "no_bilateral_finger_force", "final_state": sim.robot.state()}
        planner.close()
        planner = None
        directory = output/"planning-lift"
        directory.mkdir()
        print("planner setup", directory.name, flush=True)
        planner = V2Planner(sim, options, "right", directory, workspace=(center, 2.))
        planner.attach_target_geometry()
        phase = "lift"
        goal = tcp_pose(sim, "right").copy()
        goal[2, 3] += .10
        plan = planner.plan(goal)
        if plan.status != "success":
            return {"valid": False, "failure_phase": phase, "reason": plan.status}
        follow_arm_points(sim, plan.positions, "right", 0., sample=sample, on_tick=frame)
        phase = "hold"
        for _ in range(50):
            semantic_tick(sim, RobotCommand(), sample=sample, fixed_base=True)
            frame()
        outcome = evaluate_pick(facts, monitor.initial)
        return {"valid": outcome["status"] == "success", "task_outcome": outcome,
                "final_state": sim.robot.state(), "continuous_physics": True}
    except OperationFailure as error:
        return {"valid": False, "failure_phase": phase, "reason": error.reason}
    finally:
        if facts:
            with (output/"physics.jsonl").open("w") as stream:
                for row in facts:
                    stream.write(json.dumps(row)+"\n")
        if planner is not None:
            planner.close()
        for writer in writers.values():
            writer.close()
        sim.close()
