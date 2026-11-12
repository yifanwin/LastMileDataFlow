"""Prepare RBY-1 position demonstrations for LeRobot v2.1 and π0.5.

Command line: ``scripts/export_pi05.py``. No simulation, model download or training is performed here.
"""

from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from .lastmile_dataset import finalize_dataset, successful, training_record
from ...policies.pi05.config import PI05_CAMERAS
from ...policies.pi05.data_config import PADDED_SIZE
from ...policies.task import instruction
from ...utils.io import create_run, read_json, rows, write_json, write_rows

CAMERAS = PI05_CAMERAS["rby1m"]
STATE_SIZE, ACTION_SIZE, FPS = 22, 20, 20


def prepare_reference(evaluations, output):
    """Reuse complete successful calibration records as direct-operation demos."""
    training = []
    for source in evaluations:
        source = Path(source).resolve()
        training.extend(
            training_record(row, str(source), "s1_direct_operation")
            for row in rows(source / "trials.jsonl")
            if successful(row, observations=True)
        )
    if not training:
        raise ValueError("no complete successful demonstrations")
    root = create_run(output, {"kind": "lastmile", "evaluation_sources": list(map(str, evaluations))})
    write_rows(root / "train_index.jsonl", training)
    write_rows(root / "pair_index.jsonl", [])
    return finalize_dataset(root)


def _check_rollout(rollout):
    """Return (state, action, recorded task) of one RBY-1 position-v1 20Hz three-camera rollout."""
    metadata = read_json(rollout / "metadata.json")
    robot = metadata["profile"]["robot"]
    if (
        robot["name"] != "rby1m"
        or robot["control_profile"] != "position-v1"
        or robot["control_hz"] != FPS
        or not set(CAMERAS.values()) <= set(metadata["cameras"])
    ):
        raise ValueError(f"expected RBY-1 position-v1 20Hz three-camera recording: {rollout}")
    with np.load(rollout / "trajectory.npz", allow_pickle=False) as trace:
        action = trace["actions"].astype(np.float32)
        state = trace["state22"][:-1].astype(np.float32)
        times = trace["time_s"]
    if (
        state.shape != (len(action), STATE_SIZE)
        or action.shape[1:] != (ACTION_SIZE,)
        or not np.isfinite(state).all()
        or not np.isfinite(action).all()
    ):
        raise ValueError(f"invalid state/action layout: {rollout}")
    # Existing 4ms physics alternates 48/52ms ticks at a nominal 20Hz.
    if not np.allclose(np.diff(times), 1 / FPS, atol=0.00201):
        raise ValueError(f"not a 20Hz trajectory: {rollout}")
    return state, action, read_json(rollout / "recording.json")["task"]


def _write_episode(writer, rollout, state, action, task):
    for step in range(len(action)):
        frame = {"state": state[step], "actions": action[step]}
        frame.update(
            {key: imageio.imread(rollout / "images" / camera / f"{step:06d}.png") for key, camera in CAMERAS.items()}
        )
        writer.add_frame(frame, task=task)
    writer.save_episode()


def _norm_stats(states, actions):
    stats = {}
    for name, batches in [("state", states), ("actions", actions)]:
        vectors = np.concatenate(batches).astype(np.float64)
        stats[name] = {
            key: np.pad(value, (0, PADDED_SIZE - len(value))).tolist()
            for key, value in {
                "mean": vectors.mean(0),
                "std": vectors.std(0),
                "q01": np.quantile(vectors, 0.01, axis=0),
                "q99": np.quantile(vectors, 0.99, axis=0),
            }.items()
        }
    return stats


def _data_config(dataset, repo_id, prompts, episodes):
    return {
        "kind": "pi05_rby1_data",
        "repo_id": repo_id,
        "format": "lerobot-v2.1",
        "source": str(dataset),
        "embodiment": "rby1m",
        "controller_profile": "position-v1",
        "fps": FPS,
        "state_size": STATE_SIZE,
        "action_size": ACTION_SIZE,
        "padded_size": PADDED_SIZE,
        "state_layout": "state22",
        "action_layout": "rby1-20d",
        "images": CAMERAS,
        "prompt_from_task": True,
        "action_sequence_keys": ["actions"],
        "task_prompts": prompts,
        "action_horizon": 50,
        "replan_steps": 5,
        "use_quantile_norm": True,
        "delta_transform": False,
        "norm_stats": "assets/norm_stats.json",
        "normalization_population": "each pre-action state and executed action once; all episodes are training",
        "timestamp_policy": "nominal 20Hz; original 48/52ms timestamps retained in materialized source",
        "episodes": len(episodes),
        "frames": sum(item["frames"] for item in episodes),
    }


def _create_writer(dataset, records, output, repo_id):
    import datasets
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    datasets.config.HF_DATASETS_CACHE = output.parent / "dependencies" / "datasets-cache"
    first = dataset / records[0]["rollout_path"]
    features = {
        "state": {"dtype": "float32", "shape": (STATE_SIZE,), "names": None},
        "actions": {"dtype": "float32", "shape": (ACTION_SIZE,), "names": None},
    }
    for key, camera in CAMERAS.items():
        shape = imageio.imread(first / "images" / camera / "000000.png").shape
        features[key] = {"dtype": "image", "shape": shape, "names": ["height", "width", "channel"]}
    return LeRobotDataset.create(
        repo_id=repo_id,
        root=output,
        fps=FPS,
        robot_type="rby1m",
        features=features,
        use_videos=False,
        image_writer_threads=6,
    )


def convert(dataset, output, *, repo_id="lastmile/rby1_position"):
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    if read_json(dataset / "summary.json")["status"] != "completed":
        raise ValueError("materialization is incomplete")
    records = rows(dataset / "train_index.jsonl")
    if not records:
        raise ValueError("empty training dataset")
    writer = _create_writer(dataset, records, output, repo_id)
    states, actions, episodes, prompts = [], [], [], {}
    try:
        for record in records:
            rollout = dataset / record["rollout_path"]
            state, action, task = _check_rollout(rollout)
            prompts[task["instruction"]] = instruction(task)
            _write_episode(writer, rollout, state, action, task["instruction"])
            states.append(state)
            actions.append(action)
            episodes.append(
                {
                    "episode_index": len(episodes),
                    "rollout_path": record["rollout_path"],
                    "frames": len(action),
                    "task": task["instruction"],
                    "source": record.get("source"),
                }
            )
            print(f"converted {len(episodes)}/{len(records)}: {len(action)} frames", flush=True)
    finally:
        writer.stop_image_writer()
    write_json(output / "assets" / "norm_stats.json", {"norm_stats": _norm_stats(states, actions)})
    write_rows(output / "meta" / "lastmile_sources.jsonl", episodes)
    write_json(output / "data_config.json", _data_config(dataset, repo_id, prompts, episodes))
    return output
