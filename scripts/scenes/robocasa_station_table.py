"""Paired station table, facing the target by default (yaw offsets optional); a station is legal only if contact-free in every scene."""
import argparse, json
from pathlib import Path
import numpy as np
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.utils.io import rows, write_rows, write_json

p = argparse.ArgumentParser()
p.add_argument("--scenes-root", required=True); p.add_argument("--evidence", required=True)
p.add_argument("--scenes", nargs="+", required=True); p.add_argument("--offsets-deg", nargs="+", type=float, default=[0.0])
p.add_argument("--output", required=True)
a = p.parse_args()
tables = {s: {r["station_id"]: r for r in rows(Path(a.evidence) / s / "stations.jsonl")} for s in a.scenes}
base = [r for sid, r in tables[a.scenes[0]].items() if all(tables[s][sid]["valid"] for s in a.scenes)]
contacts = {}
for scene in a.scenes:
    rt = RoboCasaRuntime(Path(a.scenes_root) / scene)
    for r in base:
        for off in a.offsets_deg:
            contacts[(scene, r["xy_id"], off)] = int(rt.probe_station(r["x"], r["y"], r["yaw"] + np.radians(off)))
out = []
for r in base:
    for off in a.offsets_deg:
        c = {s: contacts[(s, r["xy_id"], off)] for s in a.scenes}
        tag = "y00" if off == 0 else f"y{'p' if off > 0 else 'm'}{abs(int(off)):02d}"
        out.append({"xy_id": r["xy_id"], "station_id": f"{r['xy_id']}-{tag}", "x": r["x"], "y": r["y"], "yaw": r["yaw"] + float(np.radians(off)),
                    "yaw_offset_deg": off, "valid": not any(c.values()), "contacts": c,
                    "pi05_visible": bool(off == 0 and all(tables[s][r["station_id"]]["pi05_visible"] for s in a.scenes))})
Path(a.output).mkdir(parents=True, exist_ok=True)
write_rows(Path(a.output) / "stations.jsonl", out)
summary = {"scenes": a.scenes, "offsets_deg": a.offsets_deg, "xy": len(base), "candidates": len(out), "legal": sum(r["valid"] for r in out),
           "legal_by_offset": {str(o): sum(r["valid"] for r in out if r["yaw_offset_deg"] == o) for o in a.offsets_deg},
           "pi05_subset": sum(r["pi05_visible"] for r in out)}
write_json(Path(a.output) / "summary.json", summary); print(json.dumps(summary))
