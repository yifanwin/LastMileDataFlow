#!/usr/bin/env bash
# Sequential queue for the S0009->S0023 navigation stop-point ablation.
set -u
cd "$(dirname "$0")/.."
PY=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
SRC=outputs/no_edit/molmobot-val103-cup30-20261011-194653
STAMP=$(date +%Y%m%d-%H%M%S)
export MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=6 CUDA_VISIBLE_DEVICES=6
export PYTHONPATH=src
for pair in "50:0.5" "67:0.6666666666666666" "75:0.75"; do
  tag=${pair%%:*}; frac=${pair##*:}
  out="outputs/diagnostics/path-molmobot-S0009-S0023-f${tag}-${STAMP}"
  echo "[$(date +%H:%M:%S)] === fraction from start f${tag} (${frac}) -> $out ==="
  "$PY" scripts/run_molmobot_station_path_transfer.py \
    --source "$SRC" --output "$out" --start S0009 --goal S0023 --fraction "$frac" \
    >> "outputs/logs/path-transfer-f${tag}-${STAMP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] === f${tag} exit=$? ==="
done
echo "[$(date +%H:%M:%S)] QUEUE DONE"
