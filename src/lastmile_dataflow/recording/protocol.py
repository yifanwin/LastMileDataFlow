"""Small, simulator-independent primitives for immutable raw-first packages."""
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from ..io import canonical, digest, file_digest

SCHEMA = 'research-canonical/2.0.0'


def diagnostic(value):
    """Finite JSON projection only. Original arrays are separately retained."""
    if isinstance(value, dict):
        return {str(k): diagnostic(v) for k, v in value.items()}
    if isinstance(value, np.ndarray):
        return diagnostic(value.tolist())
    if isinstance(value, (list, tuple)):
        return [diagnostic(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def write_npz(path, **arrays):
    """Atomically publish an exclusive, no-pickle, lossless array chunk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.chunk-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            np.savez_compressed(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)  # Atomic and refuses an existing destination.
    finally:
        os.unlink(name)


def append(stream, value):
    stream.write(canonical(diagnostic(value)) + b'\n')
    stream.flush()


def rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def safe_path(root, relative):
    """Refuse traversal, absolute paths and symlinks escaping the package."""
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe package reference: ' + str(relative))
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('reference escapes package: ' + str(relative))
    return path


@lru_cache(maxsize=1)
def code_identity():
    package = Path(__file__).resolve().parents[1]
    root = package.parents[1]
    files = {str(p.relative_to(root)): file_digest(p) for p in sorted(package.rglob('*.py'))}
    files['pyproject.toml'] = file_digest(root / 'pyproject.toml')
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return {'code_commit': commit, 'code_dirty': dirty, 'code_sha256': digest(files)}
