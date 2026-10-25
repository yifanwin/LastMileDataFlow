"""Measure PNG file writes through AttemptRecorder, without a task rollout."""

import argparse
import json
from pathlib import Path
import platform
import tempfile
import time
from unittest.mock import patch

import imageio
import imageio.v2 as imageio_v2
import numpy as np
import PIL

from lastmile_dataflow.data.recorder import AttemptRecorder


def scene_frames(scenes):
    import mujoco

    samples = []
    for scene in scenes:
        config = json.loads((scene.parents[1] / "config.json").read_text())
        robot, geometry = config["robot"], config["geometry"]
        model = mujoco.MjModel.from_binary_path(str(scene / "model.mjb"))
        data = mujoco.MjData(model)
        with np.load(scene / "initial.npz") as initial:
            mujoco.mj_setState(model, data, initial["state"], int(initial["state_spec"]))
        mujoco.mj_forward(model, data)
        with mujoco.Renderer(model, height=geometry["height"], width=geometry["width"]) as renderer:
            frames = {}
            for camera in robot["cameras"]:
                renderer.update_scene(data, camera=robot["namespace"] + camera)
                frames[camera] = renderer.render().copy()
        samples.append(frames)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenes", nargs="+", type=Path, help="Frozen scene directories")
    source.add_argument("--images", type=Path, help="Existing rollout images/ directory")
    parser.add_argument("--frames", nargs="+", type=int, default=[0, 50, 100, 150, 200])
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.scenes:
        samples = scene_frames(args.scenes)
        source_info = {"kind": "frozen_scene_initial_rgb", "scenes": [str(p.resolve()) for p in args.scenes]}
    else:
        cameras = sorted(p for p in args.images.iterdir() if p.is_dir())
        samples = [{p.name: imageio_v2.imread(p / f"{frame:06d}.png") for p in cameras}
                   for frame in args.frames]
        source_info = {"kind": "recorded_rgb", "images": str(args.images.resolve()), "frames": args.frames}

    # Reuse the actual recorder method, including directory creation and imageio's
    # file close. Only the encoder option differs; RGB is loaded before timing.
    original_write = imageio_v2.imwrite
    measurements = []
    for repeat in range(-1, args.repeats):
        for level in ((6, 1) if repeat % 2 == 0 else (1, 6)):
            def write_png(path, pixels, **kwargs):
                kwargs["compress_level"] = level
                return original_write(path, pixels, **kwargs)

            with tempfile.TemporaryDirectory(dir=args.output, prefix="png-") as directory:
                recorder = AttemptRecorder(directory, {"collection": {}}, attempt_id="probe")
                with patch("lastmile_dataflow.data.recorder.imageio.imwrite", side_effect=write_png):
                    started = time.perf_counter()
                    for step, frames in enumerate(samples):
                        recorder.observation(frames, step=step)
                    elapsed = time.perf_counter() - started
                total_bytes = 0
                for step, frames in enumerate(samples):
                    for camera, pixels in frames.items():
                        path = recorder.path / "images" / camera / f"{step:06d}.png"
                        np.testing.assert_array_equal(imageio_v2.imread(path), pixels)
                        total_bytes += path.stat().st_size
                if repeat >= 0:
                    measurements.append({"repeat": repeat, "compression_level": level,
                                         "write_s": elapsed, "bytes": total_bytes})

    summary = []
    for level in (6, 1):
        rows = [row for row in measurements if row["compression_level"] == level]
        durations = [row["write_s"] for row in rows]
        summary.append({"compression_level": level, "median_write_s": float(np.median(durations)),
                        "min_write_s": min(durations), "max_write_s": max(durations),
                        "bytes_per_pass": rows[0]["bytes"]})
    result = {
        "experiment": "E02", "source": source_info,
        "versions": {"python": platform.python_version(), "numpy": np.__version__,
                     "imageio": imageio.__version__, "pillow": PIL.__version__},
        "camera_frames": sum(len(frames) for frames in samples),
        "sample_shapes": [{camera: list(pixels.shape) for camera, pixels in frames.items()} for frames in samples],
        "warmup_passes_per_level": 1, "repeats": args.repeats,
        "timing_scope": "AttemptRecorder.observation: mkdir, PNG encoding, write and file close; "
                        "excludes RGB acquisition, decoding checks, cleanup and fsync; no physical stepping",
        "all_decoded_pixels_equal": True, "summary": summary, "measurements": measurements,
        "median_speedup": summary[0]["median_write_s"] / summary[1]["median_write_s"],
        "size_increase_fraction": summary[1]["bytes_per_pass"] / summary[0]["bytes_per_pass"] - 1,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("camera_frames", "summary", "median_speedup",
                                                 "size_increase_fraction", "all_decoded_pixels_equal")}, indent=2))


if __name__ == "__main__":
    main()
