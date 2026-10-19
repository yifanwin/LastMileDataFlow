#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv-pi05/bin/python ]]; then
  uv venv --python "${PYTHON_VERSION:-3.11}" .venv-pi05
fi
uv pip install --python .venv-pi05/bin/python --group pi05
uv pip install --python .venv-pi05/bin/python --no-deps -e .
