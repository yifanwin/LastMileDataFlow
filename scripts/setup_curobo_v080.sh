#!/usr/bin/env bash
# Pinned planner package, separate site-packages; borrowing dependencies is explicit.
set -euo pipefail
cd "$(dirname "$0")/.."
SOURCE="${1:?Usage: setup_curobo_v080.sh /path/to/curobo-git /path/to/dependency-python}"
BASE_PYTHON="${2:?Existing Python with Torch/CUDA/MuJoCo is required}"
UV="${UV:-/home/wenyifan/.local/bin/uv}"
PIN=4ea77366ca48ee453e7df139e39fa6532af49f3b
[[ "$(git -C "$SOURCE" rev-parse 'v0.8.0^{commit}')" == "$PIN" ]] || { echo 'Unexpected v0.8.0 SHA' >&2; exit 1; }
mkdir -p outputs/dependencies
DEST="$PWD/outputs/dependencies/curobo-v0.8.0"
if [[ ! -d "$DEST" ]]; then
  mkdir "$DEST"
  git -C "$SOURCE" archive v0.8.0 | tar -xf - -C "$DEST"
fi
[[ -d .venv-curobo-v080 ]] || "$BASE_PYTHON" -m venv .venv-curobo-v080
SITE="$(.venv-curobo-v080/bin/python -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
BORROW="$("$BASE_PYTHON" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
printf '%s\n' "$BORROW" > "$SITE/borrowed_dependencies.pth"
# Full source installation: a non-editable tag wheel omits task YAMLs.
SETUPTOOLS_SCM_PRETEND_VERSION=0.8.0 "$UV" --cache-dir outputs/dependencies/uv-cache pip install \
  --python .venv-curobo-v080/bin/python --no-deps --no-build-isolation -e "$DEST"
"$UV" --cache-dir outputs/dependencies/uv-cache pip install --python .venv-curobo-v080/bin/python 'cuda-core[cu12]==1.2.1'
PYTHONPATH=src .venv-curobo-v080/bin/python - <<'PY'
import importlib.metadata as m
import sys
from lastmile_dataflow.planning.curobo_v2 import verify_version
from lastmile_dataflow.io import write_json
identity=verify_version()
identity.update(python=sys.executable, dependencies={name:m.version(name) for name in
    ('torch','numpy','scipy','warp-lang','cuda-core','cuda-bindings','cuda-pathfinder','mujoco','yourdfpy')},
    dependency_isolation='Separate cuRobo package/CUDA backend; other dependencies explicitly borrowed via .pth')
write_json('outputs/dependencies/environment-v080.json', identity)
print(identity)
PY
