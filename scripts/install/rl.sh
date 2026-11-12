#!/usr/bin/env bash
# Separate environment: never sync the root project's pinned robotics stack.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
mjlab_source=${1:-/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/reference_repos/mjlab}
cuda_route=${2:-cu130}
export UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/lastmile-uv-cache}
export UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT:-300}
rl_index_url=${RL_INDEX_URL:-https://pypi.org/simple}
case "$cuda_route" in cu130|cu126) ;; *) echo 'CUDA route must be cu130 or cu126' >&2; exit 2 ;; esac
uv --no-config venv --python 3.11 --allow-existing "$repo_root/.venv"
if [[ -n ${RL_WHEEL_DIR:-} ]]; then
    uv --no-config pip install --python "$repo_root/.venv/bin/python" --no-deps "$RL_WHEEL_DIR"/*.whl
fi
# Resolve NVIDIA runtime dependencies from PyPI: the cu130 index redirects
# their wheels to a regional NVIDIA mirror that can stall on this server.
uv --no-config pip install --python "$repo_root/.venv/bin/python" \
    --index-url "$rl_index_url" \
    "torch @ https://download-r2.pytorch.org/whl/$cuda_route/torch-2.14.0%2B$cuda_route-cp311-cp311-manylinux_2_28_x86_64.whl"
uv --no-config pip install --python "$repo_root/.venv/bin/python" \
    --index-url "$rl_index_url" 'warp-lang>=1.15.0'
uv --no-config pip install --python "$repo_root/.venv/bin/python" \
    --index-url "$rl_index_url" \
    --group "$repo_root/pyproject.toml:rl" --editable "$mjlab_source"
site_dir=$("$repo_root/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$repo_root/src" > "$site_dir/lastmile-research.pth"
"$repo_root/.venv/bin/python" -c \
    'import importlib.metadata as m, torch; print({p:m.version(p) for p in ("mjlab","mujoco","mujoco-warp","warp-lang","torch","torch-geometric")}); print("CUDA:", torch.cuda.is_available())'
if [[ -n ${3:-} ]]; then
    "$repo_root/.venv/bin/python" "$repo_root/tests/smoke/rl.py" --gpu "$3" --steps 2
fi
