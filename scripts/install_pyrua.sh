#!/usr/bin/env bash
# PyRUA-Lean agent + RPent RoboCasa primitives + RLDX-1 for the pyrua backend.
# Everything lands under data/ (git-ignored). PYTHON is the project interpreter
# whose torch 2.7.x serves RLDX-1 too; only pure-Python overlays are added.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
mkdir -p data/third_party data/checkpoints data/hf_cache

clone() {  # url dir commit
  [[ -d "data/third_party/$2/.git" ]] || git clone "$1" "data/third_party/$2"
  git -C "data/third_party/$2" checkout -q "$3"
}
# The commits PyRUA-Lean's RoboCasa docs pin (docs/robots/robocasa.md).
clone https://github.com/RLinf/RPent.git RPent 886b3b2
clone https://github.com/RLinf/RLDX-1.git RLDX-1 ebcfd13df5177e4b3e574bdf5a3b427c7c4a1e8a

# RPent toolkit + PyRUA MCP server dependencies, appended after the project site-packages.
uv pip install --python "$PYTHON" --target data/pyrua_site \
  "pydantic-ai-slim==2.53.0" "mcp>=1.23.0,<2.0.0" uvicorn "httpx>=0.27"
# RLDX-1 inference-only packages, ahead of the project site-packages in the RLDX process only.
uv pip install --python "$PYTHON" --target data/rldx_site --no-deps \
  "transformers==4.57.1" "huggingface-hub==0.36.0" "gymnasium==1.2.2" "diffusers==0.35.1" "tyro==0.9.17" \
  termcolor "pandas==2.2.3" "albumentations==1.4.18" "albucore==0.0.17" "peft==0.17.1" "dm-tree==0.1.8" \
  "omegaconf==2.3.0" "antlr4-python3-runtime==4.9.3" docstring-parser shtab typeguard farama-notifications \
  cloudpickle tzdata pytz "msgpack==1.1.0" "msgpack-numpy==0.4.8" eval-type-backport accelerate \
  "opencv-python-headless==4.11.0.86" pyyaml regex "av==15.0.0" "scikit-image==0.25.2" lazy-loader tifffile

# Weights (13.8 GB) and the RLDX-1-VLM support files (15 files, ~16 MB), revisions as RPent pins them.
export HF_HOME="$PWD/data/hf_cache" HF_HUB_CACHE="$PWD/data/hf_cache/hub"
hf download RLWRLD/RLDX-1-FT-RC365 --revision 587e9ecdcc5e7184fcc17f58713908edff5af041 \
  --local-dir data/checkpoints/rldx-1-ft-rc365
hf download RLWRLD/RLDX-1-VLM --revision 4b9f870d1287e0d38d7eb1445e6d8c60afe66dd7 \
  .gitattributes LICENSE.md README.md added_tokens.json architecture.png chat_template.jinja config.json \
  generation_config.json merges.txt preprocessor_config.json special_tokens_map.json teaser.png \
  tokenizer_config.json video_preprocessor_config.json vocab.json

# Clean Codex home: no memories and no user-level AGENTS.md reach the agent.
# auth.json is a link, so token refreshes update the user's own login file.
mkdir -p data/codex_home
[[ -e data/codex_home/auth.json ]] || ln -s "${CODEX_HOME:-$HOME/.codex}/auth.json" data/codex_home/auth.json
cat > data/codex_home/config.toml <<'EOF'
approval_policy = "never"
model_auto_compact_token_limit = 945000

[features]
memories = false
EOF
