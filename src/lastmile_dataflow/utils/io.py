"""小型 JSON/JSONL 读写；数组单独存 NPZ。"""

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path

import numpy as np
from dataclasses import dataclass
import os
import yaml


def jsonable(value):
    """Plain JSON value: dataclasses by field name, arrays as lists, non-finite floats as null."""
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [jsonable(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def canonical(value):
    return json.dumps(jsonable(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_row(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()


def write_rows(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(b"".join(canonical(v) + b"\n" for v in values))
    temporary.replace(path)


def create_run(path, config, *, prepared=False):
    path = Path(path)
    if prepared and (path / "config.json").exists():
        raise FileExistsError(path)
    path.mkdir(parents=True, exist_ok=prepared)
    write_json(path / "config.json", config)
    return path


REPO_ROOT = Path(__file__).absolute().parents[3]


def repo_path(value):
    # Do not dereference the virtual-environment Python symlink.
    return os.path.abspath(REPO_ROOT / Path(value).expanduser())


@dataclass(frozen=True)
class LocalConfig:
    assets_dir: Path | None = None


def load_local_config():
    """Optional machine defaults (configs/local.yaml); paths are relative to the repository."""
    path = REPO_ROOT / "configs/local.yaml"
    if not path.exists():
        return LocalConfig()
    return LocalConfig(**{key: Path(value) for key, value in read_config(path, repo_fields=("assets_dir",)).items()})


def read_config(path, *, data_fields=(), repo_fields=()):
    """Read one YAML input config; resolve only the fields explicitly named by the owning loader.

    Run outputs (config.json, summary.json …) are JSON and are read with ``utils.io.read_json``.
    """
    path = Path(repo_path(path))
    if path.suffix not in (".yaml", ".yml"):
        raise ValueError(f"input configs are YAML: {path}")
    value = yaml.safe_load(path.read_text()) or {}
    root = value.pop("data_root", None)
    if data_fields:
        if not root:
            raise ValueError("data_root must be configured explicitly")
        root = Path(repo_path(root))
        for key in data_fields:
            item = value.get(key)
            if isinstance(item, dict):
                value[key] = {name: os.path.abspath(root / Path(p).expanduser()) for name, p in item.items()}
            elif item:
                value[key] = os.path.abspath(root / Path(item).expanduser())
    for key in repo_fields:
        if value.get(key):
            value[key] = repo_path(value[key])
    return value
