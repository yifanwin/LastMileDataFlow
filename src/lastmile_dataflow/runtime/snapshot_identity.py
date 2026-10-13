"""Fixed-size model/initial-state identity, not a recursive output seal."""
from pathlib import Path

from ..io import file_digest


def snapshot_identity(path):
    root = Path(path)
    names = ('model.mjb', 'initial.npz', 'version.json', 'instances.json')
    return {name: file_digest(root / name) for name in names}


def check_snapshot_identity(path, expected):
    if snapshot_identity(path) != expected:
        raise ValueError('snapshot model/initial identity mismatch')

