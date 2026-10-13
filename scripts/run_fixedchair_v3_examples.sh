#!/usr/bin/env bash
# One current-version example: three scene samples, then three real LastMile units.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=${DATAFLOW_PYTHON:-$PWD/.worktrees/no-edit-lastmile-val/.venv-curobo-v080/bin/python}
EXAMPLE=outputs/case1-v3-fixed-chair-examples-20261013
export PYTHONPATH="$PWD/src" MUJOCO_GL=egl PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p outputs/logs
printf '%s\n' "$$" > outputs/logs/fixedchair-v3-examples-20261013.pid
"$PYTHON" -m lastmile_dataflow.cli build-scene-datasets \
  --config configs/scene_datasets/val103_cup30_fixedchair_v3_examples.json \
  --discovery outputs/diagnostics/fixedchair-v3-qualified-20261013.json \
  --output-dir outputs --dataset-id fixedchair-v3-examples-20261013 \
  --dataset-root "$EXAMPLE/scenes-datasets" --gpu-ids "${CONSTRUCTION_GPU:-6}" --workers 1
"$PYTHON" - "$EXAMPLE" <<'PY'
import sys
from pathlib import Path
from lastmile_dataflow.io import read_json
p=Path(sys.argv[1])/'scenes-datasets'
m=read_json(p/'manifest.json')
assert m['status']=='sealed' and m['sample_count']==3, 'three accepted samples required before collection'
PY
"$PYTHON" -m lastmile_dataflow.cli collect-lastmile-datasets \
  --dataset "$EXAMPLE/scenes-datasets" \
  --assets-dir "$PWD/../molmospaces_data/assets" \
  --config configs/lastmile_datasets/fixedchair_v3_examples.json \
  --output-dir outputs --publication-root "$EXAMPLE/lastmile-datasets" \
  --run-id fixedchair-v3-lastmile-20261013 --gpu-ids 0 1 2 3 4 5 6 7 --workers 1 \
  --no-molmobot-export
