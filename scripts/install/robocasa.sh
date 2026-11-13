#!/usr/bin/env bash
# RoboCasa365 native runtime: separate environment; robosuite/robocasa installed editable from local checkouts.
# Usage: scripts/install/robocasa.sh [robosuite_dir] [robocasa_dir] [asset types...]
# Asset types go to robocasa/scripts/download_kitchen_assets.py (e.g. tex fixtures_lw objs_objaverse objs_lw);
# none given skips the download. Assets land inside the robocasa checkout (models/assets).
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
refs=/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/reference_repos
robosuite_source=${1:-$refs/robosuite}
robocasa_source=${2:-$refs/robocasa}
shift $(( $# < 2 ? $# : 2 ))
python=$repo_root/.venv-robocasa/bin/python
export UV_CACHE_DIR=${UV_CACHE_DIR:-/tmp/lastmile-uv-cache}
uv --no-config venv --python 3.11 --allow-existing "$repo_root/.venv-robocasa"
uv --no-config pip install --python "$python" --group "$repo_root/pyproject.toml:robocasa"
# Upstream install_requires pull lerobot/tianshou/torch for dataset tooling the runtime does not use.
uv --no-config pip install --python "$python" --no-deps -e "$robosuite_source" -e "$robocasa_source"
site_dir=$("$python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$repo_root/src" > "$site_dir/lastmile-research.pth"
if (( $# )); then
    yes y | "$python" "$robocasa_source/robocasa/scripts/download_kitchen_assets.py" --type "$@"
fi
"$python" -c 'import importlib.metadata as m; print({p: m.version(p) for p in ("robosuite", "robocasa", "mujoco", "numpy")})'
"$repo_root/scripts/install/egl.sh" "$repo_root/.venv-robocasa"
