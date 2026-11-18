"""Plot a full-corpus FPS pool against the target's actual visual mesh."""

from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
from matplotlib.colors import LightSource
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from mpl_toolkits.mplot3d import proj3d

from lastmile_dataflow.robots.registry import load_robot_config, load_robot_spec
from lastmile_dataflow.sim.model.geometry import descendants, geom_points
from lastmile_dataflow.sim.model.mjcf import load_spec
from lastmile_dataflow.sim.source import SceneSource, instance_catalog, resolve_target
from lastmile_dataflow.utils.figures import plt, save_figure
from lastmile_dataflow.utils.io import write_json
from .grasps import load_grasps, select_grasps


def _visual_triangles(model, data, target):
    bodies = descendants(model, target)
    rotation = data.xmat[target].reshape(3, 3)
    sim = SimpleNamespace(model=model, data=data)
    triangles = []
    for geom in range(model.ngeom):
        if (int(model.geom_bodyid[geom]) not in bodies
                or model.geom_type[geom] != mujoco.mjtGeom.mjGEOM_MESH
                or model.geom_contype[geom] or model.geom_conaffinity[geom]):
            continue
        mesh = int(model.geom_dataid[geom])
        start, count = int(model.mesh_faceadr[mesh]), int(model.mesh_facenum[mesh])
        vertices = (geom_points(sim, geom) - data.xpos[target]) @ rotation
        triangles.append(vertices[model.mesh_face[start:start + count]])
    if not triangles:
        raise ValueError("Target has no visual mesh triangles")
    return np.concatenate(triangles)


def _panda_gripper_triangles(config, scale):
    """Native open Panda Hand mesh in its measured grip-site coordinate frame."""
    model = load_robot_spec(load_robot_config(config), scale=scale).compile()
    data = mujoco.MjData(model)
    for suffix, endpoint in ((1, 1), (2, 0)):
        joint = model.joint(f"robot_0/gripper0_right_finger_joint{suffix}").id
        data.qpos[model.jnt_qposadr[joint]] = model.jnt_range[joint, endpoint]
    mujoco.mj_forward(model, data)
    root = model.body("robot_0/gripper0_right_right_gripper").id
    site = model.site("robot_0/gripper0_right_grip_site").id
    root_world, tcp_world = np.eye(4), np.eye(4)
    root_world[:3, :3], root_world[:3, 3] = data.xmat[root].reshape(3, 3), data.xpos[root]
    tcp_world[:3, :3], tcp_world[:3, 3] = data.site_xmat[site].reshape(3, 3), data.site_xpos[site]
    root_to_tcp = np.linalg.inv(root_world) @ tcp_world
    triangles = _visual_triangles(model, data, root)
    return (triangles - root_to_tcp[:3, 3]) @ root_to_tcp[:3, :3], root_to_tcp


def _scene_mapping(triangles, poses, object_world):
    """Apply the same object-to-world transform to both mesh and TCP poses."""
    world_triangles = triangles @ object_world[:3, :3].T + object_world[:3, 3]
    return world_triangles, object_world @ poses


def _plot_pool(triangles, poses, gripper, rows, target, total, seed, output):
    figure = plt.figure(figsize=(14, 10), facecolor="white")
    axes = figure.add_axes([0.01, 0.12, 0.76, 0.75], projection="3d", computed_zorder=False)
    axes.add_collection3d(Poly3DCollection(
        triangles * 100, facecolor="#b6cbd3", edgecolor="none", alpha=0.4, zorder=1,
    ))
    colors = plt.get_cmap("tab20")(np.linspace(0, 0.95, len(poses)))
    hand_meshes = []
    for number, (pose, color) in enumerate(zip(poses, colors), 1):
        hand = gripper @ pose[:3, :3].T + pose[:3, 3]
        hand_meshes.append(hand)
        axes.add_collection3d(Poly3DCollection(
            hand * 100, facecolor=color, edgecolor="none", alpha=0.12, zorder=2,
        ))
        origin, rotation = pose[:3, 3] * 100, pose[:3, :3]
        axes.scatter(*origin, color=color, edgecolor="white", linewidth=0.6,
                     s=52, depthshade=False, zorder=3)
        for axis, axis_color in enumerate(("#d44444", "#269b55", "#356bd0")):
            axes.quiver(*origin, *(rotation[:, axis] * 1.25), color=axis_color,
                        arrow_length_ratio=0.25, linewidth=1.3, zorder=3)
    points = np.concatenate([triangles.reshape(-1, 3)] + [h.reshape(-1, 3) for h in hand_meshes]) * 100
    low, high = points.min(0), points.max(0)
    center, half = (low + high) / 2, max(high - low) * 0.54
    axes.set(xlim=(center[0] - half, center[0] + half),
             ylim=(center[1] - half, center[1] + half),
             zlim=(center[2] - half, center[2] + half),
             xlabel="World X relative to cup (cm)", ylabel="World Y relative to cup (cm)",
             zlabel="World Z relative to cup (cm)")
    axes.set_box_aspect((1, 1, 1))
    axes.view_init(elev=28, azim=-55)
    figure.canvas.draw()
    labels = []
    for number, (pose, color) in enumerate(zip(poses, colors), 1):
        x, y, _ = proj3d.proj_transform(*(pose[:3, 3] * 100), axes.get_proj())
        anchor = axes.transData.transform((x, y))
        # Keep the short labels near their TCP origins without hiding another label.
        for radius in (14, 25, 36, 47):
            for angle in np.arange(8) * np.pi / 4:
                offset = radius * np.array([np.cos(angle), np.sin(angle)])
                pixel = anchor + offset * figure.dpi / 72
                if all(abs(pixel[0] - p[0]) > 30 or abs(pixel[1] - p[1]) > 26 for p in labels):
                    break
            else:
                continue
            break
        labels.append(pixel)
        axes.annotate(str(number), xy=(x, y), xytext=offset, textcoords="offset points",
                      ha="center", va="center", fontsize=10, weight="bold", color="#243346",
                      bbox=dict(facecolor="white", edgecolor=color, alpha=0.95, boxstyle="round,pad=0.15"),
                      arrowprops=dict(arrowstyle="-", color=color, linewidth=0.8), zorder=5)
    figure.suptitle(f"val103 / {target}: {len(poses)} native Panda grippers on the cup",
                   fontsize=19, weight="bold", y=0.96)
    figure.text(0.5, 0.9, f"{total} valid source poses  |  FPS seed: {seed}  |  actual scene orientation",
                ha="center", fontsize=12, color="#455364")
    legend = figure.add_axes([0.78, 0.18, 0.21, 0.65])
    legend.axis("off")
    legend.text(0, 1.02, "Pose / NPZ source row", fontsize=12, weight="bold", transform=legend.transAxes)
    for i, (row, color) in enumerate(zip(rows, colors)):
        y = 0.97 - i * 0.057
        legend.plot([0, 0.1], [y, y], color=color, linewidth=3, transform=legend.transAxes)
        legend.text(0.15, y, f"{i + 1:02d}      row {row}", va="center", fontsize=11, transform=legend.transAxes)
    figure.text(0.5, 0.055, "Colored surfaces = native open Panda Hand meshes, placed by their measured TCP frames\n"
                "Same physical scale as the cup; TCP roll applied. Overlaid hands are alternative grasps, not simultaneous execution.",
                ha="center", fontsize=11, color="#455364")
    return save_figure(figure, output, dpi=180)


def _plot_individual(triangles, poses, gripper, rows, target, output):
    figure = plt.figure(figsize=(16, 16), facecolor="white")
    colors = plt.get_cmap("tab20")(np.linspace(0, 0.95, len(poses)))
    columns = 4
    nrows = int(np.ceil(len(poses) / columns))
    for index, (pose, row, color) in enumerate(zip(poses, rows, colors)):
        axes = figure.add_subplot(nrows, columns, index + 1, projection="3d")
        hand = gripper @ pose[:3, :3].T + pose[:3, 3]
        axes.add_collection3d(Poly3DCollection(triangles * 100, facecolors="#bccad1", edgecolor="none", alpha=1,
                                                     shade=True, lightsource=LightSource(315, 45)))
        axes.add_collection3d(Poly3DCollection(hand * 100, facecolors=color, edgecolor="none", alpha=1,
                                                     shade=True, lightsource=LightSource(315, 45)))
        points = np.concatenate((triangles.reshape(-1, 3), hand.reshape(-1, 3))) * 100
        low, high = points.min(0), points.max(0)
        center, half = (low + high) / 2, max(high - low) * 0.53
        axes.set(xlim=(center[0] - half, center[0] + half), ylim=(center[1] - half, center[1] + half),
                 zlim=(center[2] - half, center[2] + half))
        axes.set_box_aspect((1, 1, 1))
        axes.view_init(elev=25, azim=-55)
        axes.set_axis_off()
        axes.set_title(f"{index + 1:02d} / row {row}", fontsize=13, pad=0)
    figure.suptitle(f"{target}: each selected grasp with the native open Panda Hand", fontsize=20, y=0.98)
    figure.text(0.5, 0.025, "Cup and gripper share one world frame and physical scale; TCP roll and native root-to-TCP offset applied.",
                ha="center", fontsize=12)
    figure.subplots_adjust(left=0, right=1, bottom=0.05, top=0.94, wspace=0, hspace=0.04)
    return save_figure(figure, output, dpi=150)


def visualize_grasps(scene, metadata, target, grasps, output, *, count, seed,
                     position_scale_m, rotation_scale_rad, roll_rad, robot_config, robot_scale):
    """Save the figure and exact selected TCP poses; no simulation stepping or planning."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    scene, metadata, grasps = (Path(p).absolute() for p in (scene, metadata, grasps))
    model = load_spec(scene).compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    source = SceneSource("grasp-visualization", str(scene), str(metadata))
    target_id = resolve_target(model, instance_catalog(model, source), target)
    candidates = load_grasps(grasps, model.body(target_id).name)
    pool = select_grasps(candidates, [c["pose_local"] for c in candidates], count,
                         position_scale_m, rotation_scale_rad, seed=seed)
    if not pool:
        raise ValueError("No valid corpus grasps")
    poses = np.array([c["pose_local"] for c in pool])
    roll = np.array([[np.cos(roll_rad), -np.sin(roll_rad), 0],
                     [np.sin(roll_rad), np.cos(roll_rad), 0], [0, 0, 1]])
    poses[:, :3, :3] = poses[:, :3, :3] @ roll
    rows = [c["row"] for c in pool]
    triangles = _visual_triangles(model, data, target_id)
    object_world = np.eye(4)
    object_world[:3, :3] = data.xmat[target_id].reshape(3, 3)
    object_world[:3, 3] = data.xpos[target_id]
    world_triangles, world_poses = _scene_mapping(triangles, poses, object_world)
    # Center the view at the cup without changing world-axis orientation or scale.
    plot_poses = world_poses.copy()
    plot_poses[:, :3, 3] -= object_world[:3, 3]
    gripper, root_to_tcp = _panda_gripper_triangles(robot_config, robot_scale)
    plot_triangles = world_triangles - object_world[:3, 3]
    figure = _plot_pool(plot_triangles, plot_poses, gripper, rows,
                        target, len(candidates), seed, output / "grasps.png")
    _plot_individual(plot_triangles, plot_poses, gripper, rows, target, output / "individual-grasps.png")
    np.savez(output / "selected-grasps.npz", transforms=poses, world_transforms=world_poses,
             object_world=object_world, source_rows=rows, root_to_tcp=root_to_tcp,
             gripper_root_world=world_poses @ np.linalg.inv(root_to_tcp))
    write_json(output / "selection.json", dict(
        scene=str(scene), metadata=str(metadata), target=target, target_body=model.body(target_id).name,
        grasps=str(grasps), valid_count=len(candidates), count=count, seed=seed,
        position_scale_m=position_scale_m, rotation_scale_rad=rotation_scale_rad, roll_rad=roll_rad,
        source_rows=rows, frame="target_body_local", transforms_include_roll=True,
        plot_frame="world_axes_relative_to_target_origin", object_world=object_world.tolist(),
        robot_config=str(Path(robot_config).absolute()), robot_scale=robot_scale, gripper_state="fully_open",
        gripper_geometry="native_panda_visual_mesh", gripper_triangle_count=len(gripper),
    ))
    return figure
