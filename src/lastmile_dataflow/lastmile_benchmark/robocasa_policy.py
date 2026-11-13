"""π0.5 pick trials on frozen RoboCasa365 instances from RL-grid stations (native runtime).

The parent prepares stations per scene and is the only writer of the run summary; one worker per GPU owns
one π0.5 service and one RoboCasa environment, runs its episodes in order and appends one row per episode.
Success is ``robocasa-pick-1cm-1s-v1`` checked every control step; the native place task is recorded
separately, and robot–environment contacts (target excluded) are reported apart from success.
"""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
import logging
import multiprocessing
from pathlib import Path
import time

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL, PickJudge
from lastmile_dataflow.policies.pi05.actions import robocasa_env_action
from lastmile_dataflow.policies.pi05.client import Pi05Client
from lastmile_dataflow.policies.pi05.config import PI05_CAMERAS
from lastmile_dataflow.policies.pi05.observation import robocasa_observation
from lastmile_dataflow.policies.process import PolicyInfrastructureError
from lastmile_dataflow.robots.registry import load_robot_config
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.station_map.sampling.robocasa import robocasa_stations
from lastmile_dataflow.utils.gpu import bind_device
from lastmile_dataflow.utils.io import append_row, create_run, rows, write_json, write_rows
from lastmile_dataflow.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
CAMERAS = tuple(PI05_CAMERAS["panda_omron"].values())
VIDEO_CAMERAS = ("robot0_agentview_left", "robot0_eye_in_hand")


@dataclass(frozen=True)
class Episode:
    episode_id: str
    scene_id: str
    scene_dir: str
    station_id: str
    base_xyyaw: tuple
    seed: int


@dataclass(frozen=True)
class TrialSettings:
    policy_config: str
    prompt: str
    horizon: int
    output: str


def prepare_stations(records, root, output, max_stations_per_scene):
    """Candidate lattice per scene; optionally keep the valid stations nearest the native spawn."""
    selected = {}
    for record in records:
        robot = load_robot_config(record["robot_config"])
        runtime = RoboCasaRuntime(root / record["scene"], target=record["target"]["name"])
        try:
            spawn = np.asarray(runtime.get_state().robot["base_xyyaw"][:2])
            candidates, meta = robocasa_stations(runtime, robot)
        finally:
            runtime.close()
        name = record["scene"].split("/")[-1]
        write_rows(output / "stations" / f"{name}.jsonl", candidates)
        valid = [row for row in candidates if row["valid"]]
        if max_stations_per_scene:
            valid = sorted(valid, key=lambda r: np.hypot(r["x"] - spawn[0], r["y"] - spawn[1]))[:max_stations_per_scene]
        selected[record["scene_id"]] = valid
        meta.update(scene_id=record["scene_id"], native_spawn_xy=spawn.tolist(), selected=len(valid))
        write_json(output / "stations" / f"{name}.meta.json", meta)
        logger.info("站位 %s 候选=%s 合法=%s 选用=%s", record["scene_id"], meta["candidates"], meta["valid"], len(valid))
    return selected


def _video_writer(path):
    import imageio

    return imageio.get_writer(path, fps=20, macro_block_size=1)


def run_episode(runtime, client, episode, settings):
    """One trial from ``set_station``; stops at pick success or the step budget."""
    start = time.monotonic()
    initial = runtime.set_station(*episode.base_xyyaw)
    judge = PickJudge(runtime.target, initial.target_pose[2])
    video = Path(settings.output) / "videos" / f"{episode.episode_id}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    best_lift = best_hold = 0.0
    grasp_steps = contact_steps = 0
    native_success = False
    first_contact = success_step = None
    inference_s = 0.0
    with _video_writer(video) as writer:
        for step in range(settings.horizon):
            frames = {camera: runtime.render(camera) for camera in CAMERAS}
            writer.append_data(np.concatenate([frames[c] for c in VIDEO_CAMERAS], axis=1))
            observation = robocasa_observation(runtime.native_observation(), frames, settings.prompt)
            response = client.action(
                observation, task={"operation": "pick"}, attempt=episode.episode_id, seed=episode.seed, first=step == 0
            )
            inference_s += response.inference_s
            result = runtime.step(robocasa_env_action(response.action))
            status = judge.update(runtime.get_state())
            contacts = runtime.robot_env_contacts(include_target=False)
            best_lift, best_hold = max(best_lift, status.lift_m), max(best_hold, status.hold_s)
            grasp_steps += status.grasped
            contact_steps += contacts > 0
            first_contact = step if contacts and first_contact is None else first_contact
            native_success |= result.native["task_success"]
            if status.success:
                success_step = step
                break
    steps = step + 1
    return {
        **asdict(episode),
        "status": "success" if success_step is not None else "budget_exhausted",
        "success": success_step is not None,
        "success_time_s": None if success_step is None else judge.succeeded_at - initial.time_s,
        "collision_free_success": success_step is not None and contact_steps == 0,
        "steps": steps,
        "sim_time_s": result.time_s - initial.time_s,
        "max_lift_m": best_lift,
        "max_hold_s": best_hold,
        "grasp_steps": grasp_steps,
        "robot_env_contact_steps": contact_steps,
        "first_contact_step": first_contact,
        "native_task_success": native_success,
        "inference_s": inference_s,
        "wall_s": time.monotonic() - start,
        "video": str(video.relative_to(settings.output)),
    }


def worker(gpu, episodes, settings):
    """One GPU: π0.5 service first (it inherits CUDA), then the CPU-physics RoboCasa environment."""
    bind_device(gpu)
    setup_logging()
    output = Path(settings.output)
    (output / "logs").mkdir(parents=True, exist_ok=True)
    client = Pi05Client(settings.policy_config, output / "logs" / f"pi05-gpu{gpu}.log")
    runtime, scene = None, None
    try:
        for index, episode in enumerate(episodes):
            if episode.scene_dir != scene:
                if runtime is not None:
                    runtime.close()
                runtime, scene = RoboCasaRuntime(episode.scene_dir), episode.scene_dir
            try:
                row = run_episode(runtime, client, episode, settings)
            except PolicyInfrastructureError:
                raise
            except Exception as exc:  # one failed episode must not cost the rest of this GPU's queue
                logger.exception("回合失败 %s", episode.episode_id)
                row = {**asdict(episode), "status": "error", "success": False, "error": f"{type(exc).__name__}: {exc}"}
            row.update(gpu=gpu, index=index)
            append_row(output / f"episodes-gpu{gpu}.jsonl", row)
            logger.info("gpu%s %s/%s %s %s", gpu, index + 1, len(episodes), episode.episode_id, row["status"])
    finally:
        if runtime is not None:
            runtime.close()
        client.close()
    return gpu


def summarize(output, selected):
    episodes = [row for path in sorted(Path(output).glob("episodes-gpu*.jsonl")) for row in rows(path)]
    write_rows(Path(output) / "episodes.jsonl", episodes)

    def block(items):
        done = [r for r in items if r["status"] != "error"]
        return {
            "episodes": len(items),
            "errors": len(items) - len(done),
            "success": sum(r["success"] for r in done),
            "success_rate": sum(r["success"] for r in done) / len(done) if done else None,
            "collision_free_success": sum(r.get("collision_free_success", False) for r in done),
            "native_task_success": sum(r.get("native_task_success", False) for r in done),
            "stations_with_success": len({r["station_id"] for r in done if r["success"]}),
        }

    scenes = {scene: {"stations": len(stations), **block([r for r in episodes if r["scene_id"] == scene])} for scene, stations in selected.items()}
    summary = {"criterion": PICK_PROTOCOL["version"], "overall": block(episodes), "scenes": scenes}
    write_json(Path(output) / "summary.json", summary)
    return summary


def run_trials(scenes_root, *, policy_config, output, gpus, seeds, horizon, prompt, max_stations_per_scene=0):
    scenes_root = Path(scenes_root).resolve()
    records = [r for r in rows(scenes_root / "scenes.jsonl") if r["status"] == "accepted"]
    settings = TrialSettings(str(Path(policy_config).resolve()), prompt, horizon, str(Path(output).resolve()))
    root = create_run(
        Path(output),
        {
            "scenes": str(scenes_root),
            "settings": asdict(settings),
            "gpus": list(gpus),
            "seeds": list(seeds),
            "max_stations_per_scene": max_stations_per_scene,
            "criterion": PICK_PROTOCOL,
            "action_mapping": "model 12D unnormalized; gripper/mode >= 0.5 -> +1 else -1 (RoboCasa gym)",
        },
    )
    selected = prepare_stations(records, scenes_root, root, max_stations_per_scene)
    episodes = [
        Episode(
            f"{record['scene'].split('/')[-1]}__{station['station_id']}__s{seed}",
            record["scene_id"],
            str(scenes_root / record["scene"]),
            station["station_id"],
            (station["x"], station["y"], station["yaw"]),
            seed,
        )
        for record in records
        for station in selected[record["scene_id"]]
        for seed in seeds
    ]
    # Contiguous blocks keep each worker on as few scenes (environment rebuilds) as possible.
    blocks = np.array_split(np.arange(len(episodes)), len(gpus))
    plan = {gpu: [episodes[i] for i in block] for gpu, block in zip(gpus, blocks)}
    write_json(root / "plan.json", {str(gpu): [e.episode_id for e in items] for gpu, items in plan.items()})
    logger.info("回合总数 %s，GPU %s", len(episodes), list(gpus))
    with ProcessPoolExecutor(len(gpus), mp_context=multiprocessing.get_context("spawn")) as pool:
        for future in [pool.submit(worker, gpu, items, settings) for gpu, items in plan.items() if items]:
            future.result()
    return summarize(root, selected)
