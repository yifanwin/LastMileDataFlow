"""原始 MJCF 来源与稳定实例映射；不需要 benchmark episode。"""
from dataclasses import asdict, dataclass
from pathlib import Path

from ..io import digest, file_digest, read_json


@dataclass(frozen=True)
class SceneSource:
    scene_id: str
    xml_path: str
    metadata_path: str | None = None
    dataset: str = "custom"
    split: str = "unknown"

    @classmethod
    def procthor(cls, dataset_dir, house):
        root = Path(dataset_dir).resolve()
        split = root.name.rsplit("-", 1)[-1]
        if split not in ("train", "val", "test"):
            raise ValueError("dataset directory must end in -train/-val/-test")
        if not isinstance(house, int) or house < 0:
            raise ValueError("house must be nonnegative integer")
        name = f"{split}_{house}"
        return cls(f"{root.name}/{name}", str(root / f"{name}.xml"),
                   str(root / f"{name}_metadata.json"), root.name, split)

    def provenance(self):
        value = asdict(self)
        value["xml_sha256"] = file_digest(self.xml_path)
        value["metadata_sha256"] = file_digest(self.metadata_path) if self.metadata_path else None
        return value


def instance_catalog(model, source):
    meta = read_json(source.metadata_path).get("objects", {}) if source.metadata_path else {}
    result, used = [], set()
    body_names = {model.body(i).name: i for i in range(1, model.nbody)}
    for key, obj in sorted(meta.items()):
        mapping = obj.get("name_map", {}).get("bodies", {})
        present = {n: mapped for n, mapped in mapping.items() if n in body_names}
        if key not in body_names:
            continue  # 显式导入删除后，源 metadata 的旧实例不再有效
        used.update(present)
        used.add(key)
        result.append({"instance_id": key, "mjcf_body": key, "body_id": body_names[key],
                       "asset_id": obj.get("asset_id"), "source_object_id": obj.get("object_id"),
                       "category": obj.get("category"), "parent_instance_id": obj.get("parent"),
                       "name_map": present, "pose_frame": "world", "pose_order": "xyz_wxyz"})
    for i in range(1, model.nbody):
        name = model.body(i).name
        if not name or name in used or name.startswith("robot_0/"):
            continue
        parent = int(model.body_parentid[i])
        if parent != 0 and not name.startswith("place_receptacle/"):
            continue
        result.append({"instance_id": name, "mjcf_body": name, "body_id": i,
                       "asset_id": None, "category": None, "name_map": {},
                       "pose_frame": "world", "pose_order": "xyz_wxyz"})
    return result


def resolve_target(model, catalog, target):
    if target is None:
        return None
    names = {c["mjcf_body"] for c in catalog if target in (c["instance_id"], c.get("asset_id"))}
    # 允许明确 MJCF 部件名；资产类别不被当成唯一目标。
    if not names:
        try:
            model.body(target)
            names = {target}
        except KeyError:
            pass
    if len(names) != 1:
        raise ValueError(f"target must resolve uniquely: {target}, matches={sorted(names)}")
    return model.body(next(iter(names))).id


def scene_version(source, robot_config, model_hash, restoration=None):
    descriptor = {"source": source.provenance(), "robot": robot_config,
                  "compiled_model_sha256": model_hash, "restoration": restoration}
    return {"version_id": digest(descriptor), **descriptor}
