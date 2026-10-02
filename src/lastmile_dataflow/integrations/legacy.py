"""一次性转换用户指定的旧 episode；运行期不读取旧仓库、协议或审批。"""
import copy
from pathlib import Path

import mujoco
import numpy as np

from ..io import file_digest, read_json, write_json
from ..robots.action import torso_joints, vector
from ..scenes.source import SceneSource


def convert_episode(episode_path, assets_dir, output_path):
    episode_path, assets_dir, output_path = map(Path, (episode_path, assets_dir, output_path))
    if output_path.exists():
        raise FileExistsError(output_path)
    e = read_json(episode_path)
    if e["robot"]["robot_name"] != "rby1m":
        raise ValueError("legacy robot unsupported")
    split, house = e["data_split"], e["house_index"]
    if split not in ("train", "val", "test") or not isinstance(house, int) or house < 0:
        raise ValueError("invalid legacy scene reference")
    source = SceneSource.procthor(assets_dir / "scenes" / f"procthor-10k-{split}", house)
    expected = e.get("provenance", {}).get("scene_sha256")
    if expected and file_digest(source.xml_path) != expected:
        raise ValueError("legacy source scene digest mismatch")
    mods = copy.deepcopy(e["scene_modifications"])
    if set(mods) - {"added_objects", "removed_objects", "object_poses"}:
        raise ValueError("unsupported legacy modifications")
    if mods.get("removed_objects"):
        raise ValueError("phase 1 legacy adapter does not implement deletion; never silently ignore")
    added = {}
    for name, relative in mods.get("added_objects", {}).items():
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("asset path escapes assets root")
        # 正式资产目录使用指向 NAS cache 的软链接；检查输入路径而不是禁止合法软链接。
        file = (assets_dir.absolute() / relative)
        added[name] = {"path": str(file), "sha256": file_digest(file)}
    init = copy.deepcopy(e["robot"]["init_qpos"])
    torso = vector(init["torso"], 6)
    if not np.allclose(torso, torso_joints(torso[1]), atol=1e-7):
        raise ValueError("legacy torso violates authorized coupling")
    init["torso"] = [float(torso[1])]
    for side in ("left", "right"):
        q = vector(init[side + "_gripper"], 2)
        if not np.allclose(q, [q[0], -q[0]], atol=1e-8):
            raise ValueError("legacy gripper violates coupling")
        # float32 -0.05000000074505806 是源序列化舍入，不允许扩大协议范围。
        if abs(q[0] + .05) < 1e-8:
            q[0] = -.05
        init[side + "_gripper"] = [float(q[0])]
    record = {"schema_version": "1.0", "source": source.__dict__,
              "target": e["task"]["pickup_obj_name"],
              "restoration": {"robot_initial": init, "added_objects": added,
                              "object_poses": mods.get("object_poses", {})},
              "provenance": {"legacy_episode_sha256": file_digest(episode_path),
                             "legacy_source_index": e.get("provenance", {}).get("source_index"),
                             "import_kind": "initial_state_only_no_success_or_approval"}}
    write_json(output_path, record)
    return record


def apply_restoration_spec(spec, restoration):
    for name, asset in restoration.get("added_objects", {}).items():
        if file_digest(asset["path"]) != asset["sha256"]:
            raise ValueError("imported asset modified")
        obj = mujoco.MjSpec.from_file(asset["path"])
        if len(obj.worldbody.bodies) != 1:
            raise ValueError("legacy added asset must have single root")
        root = obj.worldbody.bodies[0]
        parts = name.rsplit("/", 1)
        root.name = parts[-1]
        if root.first_joint() is None:
            root.add_freejoint(name="free")
        prefix = parts[0] + "/" if len(parts) == 2 else ""
        spec.worldbody.add_frame().attach_body(root, prefix, "")


def apply_restoration_state(sim, restoration):
    for name, pose in restoration.get("object_poses", {}).items():
        p = vector(pose, 7)
        if not np.isclose(np.linalg.norm(p[3:]), 1., atol=1e-5):
            raise ValueError("legacy object quaternion is not normalized")
        body = sim.model.body(name)
        j = int(sim.model.body_jntadr[body.id])
        if j < 0 or sim.model.jnt_type[j] != mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError(f"restored object has no free joint: {name}")
        adr = int(sim.model.jnt_qposadr[j])
        sim.data.qpos[adr:adr + 7] = p
    mujoco.mj_forward(sim.model, sim.data)
