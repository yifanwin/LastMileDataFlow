from dataclasses import dataclass, field, replace
import os
from pathlib import Path


@dataclass(frozen=True)
class StationRun:
    """Recording switches and process resources of one calibration or collection run."""

    execution: str
    assets_dir: str | None = None
    max_trials: int | None = None
    record_video: bool = False
    recording_mode: str | None = None
    planning_workers_per_gpu: list = field(default_factory=lambda: [1])
    cpu_workers: int = 60
    preparation_workers: int = 12

    def __post_init__(self):
        if self.recording_mode not in (None, "numerical", "visual"):
            raise ValueError("recording_mode must be numerical or visual")
        cpus = max(1, os.cpu_count() or 1)
        if not 1 <= self.cpu_workers <= cpus:
            raise ValueError(f"cpu_workers must be between 1 and {cpus}")
        if not 1 <= self.preparation_workers <= min(64, cpus):
            raise ValueError(f"preparation_workers must be between 1 and {min(64, cpus)}")

    @property
    def mode(self):
        """Calibration recording: numerical keeps no training RGB; visual records RGB (and video if asked)."""
        return self.recording_mode or ("visual" if self.record_video else "numerical")

    def calibration_recording(self, collection):
        visual = self.mode == "visual"
        return replace(collection, record_images=visual, record_video=visual and collection.record_video)


def add_station_run_arguments(cli):
    """Command-line options of the calibration and collection scripts (defaults are StationRun's)."""
    cli.add_argument("--config", type=Path, required=True, help="执行配置 YAML，见 configs/station_map")
    cli.add_argument("--assets-dir", type=Path, help="资产目录；省略时取 configs/local.yaml 的 assets_dir")
    cli.add_argument("--max-trials", type=int, help="每场景试验上限，只做少量检查；不完整标定不能用于正式配对")
    cli.add_argument("--record-video", action="store_true", default=None, help="visual 模式下额外录像")
    cli.add_argument(
        "--recording-mode",
        choices=("numerical", "visual"),
        help="标定记录：numerical 不存训练 RGB（默认，--record-video 时为 visual）",
    )
    cli.add_argument("--planning-workers-per-gpu", type=int, nargs="+", help="每卡规划进程数 1–4，默认 1")
    cli.add_argument("--cpu-workers", type=int, help="CPU 执行进程数，默认 60")
    cli.add_argument("--preparation-workers", type=int, help="站位准备进程数，默认 12")


def station_run(args):
    """StationRun from ``add_station_run_arguments`` options; CLI paths are relative to the working directory."""
    values = {
        "execution": str(args.config.resolve()),
        "assets_dir": str(args.assets_dir.resolve()) if args.assets_dir else None,
        "max_trials": args.max_trials,
        "record_video": args.record_video,
        "recording_mode": args.recording_mode,
        "planning_workers_per_gpu": args.planning_workers_per_gpu,
        "cpu_workers": args.cpu_workers,
        "preparation_workers": args.preparation_workers,
    }
    return StationRun(**{key: value for key, value in values.items() if value is not None})
