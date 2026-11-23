"""Analytic grasp pool for RoboCasa cup_3 (tapered cup, object frame, z up, centre at origin).

TCP = gripper0_right_grip_site: +z approach, x closing axis, max opening 0.08 m.
Side grasps: horizontal approach toward the cup axis, closing axis horizontal.
Rim pinches: vertical approach downward, closing radial across the wall at the rim.
"""
import argparse, json
from pathlib import Path
import numpy as np


def radius_at(z):
    # measured profile: r = 0.0258 at z=-0.0525 -> 0.0439 at z=+0.0525
    return 0.0258 + (z + 0.0525) * (0.0439 - 0.0258) / 0.105


def frame(approach, closing):
    z = approach / np.linalg.norm(approach); x = closing - z * (closing @ z); x /= np.linalg.norm(x)
    return np.stack([x, np.cross(z, x), z], axis=1)


def build(azimuths_side, heights, azimuths_rim, rim_depth, pitches_deg=(0.0,)):
    poses, widths, kinds = [], [], []
    for pitch in np.radians(pitches_deg):
        for z in heights:
            for a in np.linspace(0, 2 * np.pi, azimuths_side, endpoint=False):
                radial = np.array([np.cos(a), np.sin(a), 0.0])
                approach = -radial * np.cos(pitch) + np.array([0, 0, -1.0]) * np.sin(pitch)
                T = np.eye(4); T[:3, :3] = frame(approach, np.array([-np.sin(a), np.cos(a), 0.0])); T[:3, 3] = (0, 0, z)
                poses.append(T); widths.append(2 * radius_at(z)); kinds.append(f"side{int(round(np.degrees(pitch)))}")
    z = 0.0525 - rim_depth
    for a in np.linspace(0, 2 * np.pi, azimuths_rim, endpoint=False):
        radial = np.array([np.cos(a), np.sin(a), 0.0])
        T = np.eye(4); T[:3, :3] = frame(np.array([0, 0, -1.0]), radial); T[:3, 3] = radius_at(z) * radial + (0, 0, z)
        poses.append(T); widths.append(0.02); kinds.append("rim")
    return np.array(poses), np.array(widths), np.array(kinds)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--side-azimuths", type=int, default=16)
    p.add_argument("--heights", type=float, nargs="+", default=[-0.03, -0.01, 0.01])
    p.add_argument("--rim-azimuths", type=int, default=8)
    p.add_argument("--rim-depth", type=float, default=0.015)
    p.add_argument("--pitch-deg", type=float, nargs="+", default=[0.0, 30.0, 45.0], help="side approach tilted downward")
    a = p.parse_args()
    poses, widths, kinds = build(a.side_azimuths, a.heights, a.rim_azimuths, a.rim_depth, a.pitch_deg)
    a.output.mkdir(parents=True, exist_ok=False)
    np.savez(a.output / "grasps.npz", object_to_tcp=poses, width=widths, kind=kinds, grasp_id=np.arange(len(poses)))
    (a.output / "config.json").write_text(json.dumps({k: str(v) for k, v in vars(a).items()} | {"count": len(poses), "tcp": "gripper0_right_grip_site", "frame": "cup_3 body"}, indent=1))
    print(len(poses), dict(zip(*np.unique(kinds, return_counts=True))), "width range", widths[kinds != "rim"].min().round(4), widths[kinds != "rim"].max().round(4))


if __name__ == "__main__":
    main()
