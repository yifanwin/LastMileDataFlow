"""Compare measured-state rendering with recorded RGB; measure PNG compression."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import time

import mujoco
import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--rollout", default="r000000")
    parser.add_argument("--frames", nargs="+", type=int, default=[0, 50, 100, 150, 200])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((args.run / "config.json").read_text())
    trials = [json.loads(line) for line in (args.run / "trials.jsonl").read_text().splitlines()]
    trial = next(row for row in trials if row["rollout_id"] == args.rollout)
    assert trial["status"] == "success"
    dataset = Path(config["dataset"])
    scenes = [json.loads(line) for line in (dataset / "scenes.jsonl").read_text().splitlines()]
    scene = next(row for row in scenes if row["scene_id"] == trial["scene_id"])
    scene_dir = dataset / scene["scene"]
    model = mujoco.MjModel.from_binary_path(str(scene_dir / "model.mjb"))
    data = mujoco.MjData(model)
    with np.load(scene_dir / "initial.npz") as snapshot:
        mujoco.mj_setState(model, data, snapshot["state"], int(snapshot["state_spec"]))
    profile = config["profiles"][trial["scene_id"]]["robot"]
    width, height = config["config"]["width"], config["config"]["height"]
    rollout = args.run / "rollouts" / args.rollout
    comparisons, timings = [], []
    frames = []
    with np.load(rollout / "trajectory.npz") as trajectory:
        with mujoco.Renderer(model, height=height, width=width) as renderer:
            for frame in args.frames:
                data.qpos[:] = trajectory["qpos"][frame]
                data.qvel[:] = trajectory["qvel"][frame]
                data.ctrl[:] = trajectory["ctrl"][frame]
                data.time = float(trajectory["time_s"][frame])
                started = time.perf_counter()
                mujoco.mj_forward(model, data)
                forward_s = time.perf_counter() - started
                for camera in profile["cameras"]:
                    started = time.perf_counter()
                    renderer.update_scene(data, camera=profile["namespace"] + camera)
                    pixels = renderer.render().copy()
                    render_s = time.perf_counter() - started
                    with Image.open(rollout / "images" / camera / f"{frame:06d}.png") as original:
                        original_pixels = np.asarray(original.convert("RGB"))
                    delta = np.abs(pixels.astype(np.int16) - original_pixels.astype(np.int16))
                    comparisons.append({
                        "frame": frame, "time_s": data.time, "camera": camera,
                        "exact": bool(np.array_equal(pixels, original_pixels)),
                        "mean_absolute_error": float(delta.mean()),
                        "max_absolute_error": int(delta.max()),
                        "different_pixel_fraction": float(np.any(delta != 0, axis=-1).mean()),
                    })
                    timings.append({"frame": frame, "camera": camera,
                                    "forward_s": forward_s, "render_s": render_s})
                    frames.append(pixels)
    encoding = []
    for level in (6, 1):
        elapsed, size = [], []
        for pixels in frames:
            stream = BytesIO()
            started = time.perf_counter()
            Image.fromarray(pixels).save(stream, format="PNG", compress_level=level)
            elapsed.append(time.perf_counter() - started)
            size.append(stream.tell())
            stream.seek(0)
            with Image.open(stream) as decoded:
                assert np.array_equal(pixels, np.asarray(decoded))
        encoding.append({"compression_level": level, "frames": len(frames),
                         "encode_total_s": sum(elapsed), "median_encode_s": float(np.median(elapsed)),
                         "total_bytes": sum(size), "lossless": True})
    report = {
        "experiment": "E01", "source_run": str(args.run.resolve()),
        "rollout": args.rollout, "model": str(scene_dir / "model.mjb"),
        "mujoco_version": mujoco.__version__,
        "fixed_state_source": str(scene_dir / "initial.npz"),
        "mocap_bodies": [model.body(i).name for i in np.flatnonzero(model.body_mocapid >= 0)],
        "model_counts": {"nq": model.nq, "nv": model.nv, "nbody": model.nbody,
                         "ngeom": model.ngeom, "nmocap": model.nmocap},
        "cameras": profile["cameras"], "resolution": [width, height],
        "comparisons": comparisons, "render_timings": timings, "encoding": encoding,
        "timing_scope": "PNG encoding to memory; excludes filesystem writes and startup; no physical stepping",
        "all_exact": all(row["exact"] for row in comparisons),
    }
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"all_exact": report["all_exact"], "images": len(comparisons),
                      "encoding": encoding, "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
