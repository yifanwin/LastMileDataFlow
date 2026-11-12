"""Stage b→c: sample candidate stations for every scene of a library.

Results go into the library itself (stations.jsonl, stations_summary.json), so later stages keep
reading one directory; scenes without a legal station drop out here.
"""

from collections import Counter
import logging
from pathlib import Path

from lastmile_dataflow.robots.registry import robot_config
from lastmile_dataflow.scenes.library import STATIONS, SceneRecipe
from lastmile_dataflow.scenes.preview import export_scene_previews
from lastmile_dataflow.utils.io import append_row, read_json, rows, write_json
from lastmile_dataflow.utils.logging_config import Progress
from .sample import sample_scene

logger = logging.getLogger(__name__)


def sample_stations(dataset):
    root = Path(dataset).resolve()
    output = root / STATIONS
    if output.exists():
        raise FileExistsError(output)
    recipe = SceneRecipe(**read_json(root / "config.json")["recipe"])
    scenes = [row for row in rows(root / "scenes.jsonl") if row["status"] == "accepted"]
    output.touch()
    progress = Progress(logger)
    counts = Counter()
    for index, scene in enumerate(scenes):
        location = root / scene["scene"]
        robot = robot_config(read_json(location / "config.json")["robot"])
        row = sample_scene(
            {**scene, "scene_path": str(location), "robot_config": robot}, recipe.geometry, recipe.max_final_stations
        )
        append_row(output, row)
        counts[row["status"]] += 1
        progress.update(
            "站位采样 场景=%s/%s 接受=%s 淘汰=%s", index + 1, len(scenes), counts["accepted"], counts["rejected"]
        )
    write_json(
        root / "stations_summary.json",
        {
            "scenes": len(scenes),
            "accepted": counts["accepted"],
            "rejected": counts["rejected"],
            "rejection_reasons": dict(Counter(r["reason"] for r in rows(output) if r["status"] == "rejected")),
            "max_final_stations": recipe.max_final_stations,
        },
    )
    export_scene_previews(root)
    return root
