#!/usr/bin/env bash
# Install all dependencies into this repository's single environment.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python "${PYTHON_VERSION:-3.11}" .venv
  .venv/bin/python -m ensurepip
fi
.venv/bin/python -m pip install -e . --extra-index-url https://download.pytorch.org/whl/cu128
