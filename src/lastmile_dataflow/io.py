"""小型 JSON/JSONL 读写；数组单独存 NPZ。"""
import json
from pathlib import Path
import numpy as np



def diagnostic(value):
    if isinstance(value, dict):
        return {k: diagnostic(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [diagnostic(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def canonical(value):
    return json.dumps(diagnostic(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def append_row(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('ab') as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()


def write_rows(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join(canonical(v) + b"\n" for v in values))


def create_run(path, config):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    write_json(path / 'config.json', config)
    return path
