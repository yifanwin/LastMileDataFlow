#!/usr/bin/env bash
# 归集 outputs/diagnostics/ 根目录下的大批散落 run 目录到分组子目录。
#
# 背景：outputs/diagnostics/ 曾把上百个 run 平铺在根目录（66GB / 97 项），
# 大量同类 scene-dataset run（r1/r2 的 u000-r00、unit-000 …）混在一起。
# 本脚本按「同一目标产物」分组，只做位置移动，不改内容。
#
# 分组（约定见 outputs/diagnostics/README.md）：
#   obstacle_augmentation/   程序化障碍物增强相关 run、几何证据与资产 preflight
#   scene_datasets/discovery/               场景发现（discovery/qualified）结果
#   scene_datasets/val103_val104/           val103+val104 双场景构造批次
#   scene_datasets/val103_cup30/            val103 cup30 最小交付批次（含审计/完整性）
#   scene_datasets/twohouse_v3/             twohouse-v3 配方构造与 orchestration
#   scene_datasets/tooling/                 批次启动脚本外的通用脚本与 GPU 选卡记录
#
# 幂等：源不存在（已移动）时跳过；目标已存在时跳过，不覆盖。
# 只移动本脚本列出的白名单项；不在白名单内的新产物保持原位，需自行补录。
#
# 用法：scripts/tidy_diagnostics.sh [--dry-run] [--force]
#
# ⚠️ 运行前必须确认没有活跃的 lastmile run：scene-dataset build 会重新创建
#    <dataset_id>-unit-NNN / <dataset_id>-uNNN-rNN，若此时目录已被移走，
#    运行中的 unit 会直接 FileNotFoundError 失败（实测确认）。
#    脚本默认会检测并拒绝执行；确实要在 run 中强行移动时加 --force。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
D="$ROOT/outputs/diagnostics"
DRY=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

[ -d "$D" ] || { echo "no such dir: $D" >&2; exit 1; }

if [ "$FORCE" = 0 ] && pgrep -f 'lastmile_dataflow' >/dev/null 2>&1; then
  echo "refused: 检测到运行中的 lastmile_dataflow 进程，移动会打断它们：" >&2
  pgrep -af 'lastmile_dataflow' | head -5 >&2
  echo "请等 run 结束，或加 --force 自行承担风险。" >&2
  exit 3
fi

moved=0; skipped=0
move() {  # move <group> <name>
  local group="$1" name="$2" src="$D/$2" dst="$D/$1/$2"
  if [ ! -e "$src" ]; then skipped=$((skipped + 1)); return 0; fi
  if [ -e "$dst" ]; then printf 'skip (target exists): %s\n' "$1/$2"; skipped=$((skipped + 1)); return 0; fi
  if [ "$DRY" = 1 ]; then printf 'would move: %s -> %s/%s\n' "$2" "$1" "$2"; moved=$((moved + 1)); return 0; fi
  mkdir -p "$D/$1"
  mv -n "$src" "$dst"
  moved=$((moved + 1))
}

# ---- obstacle_augmentation：程序化障碍物增强 ----
for n in dense-case1-five-20261011-r1 dense-case1-five-20261011-r2 dense-case1-five-20261011-r3 \
         dense-case1-five-20261011-r4-views dense-case1-five-20261011-r5-views \
         dense-case2-five-20261011-r1 dense-case2-five-20261011-r2 dense-case2-five-20261011-r3 \
         dense-case2-five-20261011-r4-views dense-case2-five-20261011-r5-views \
         procedural-chair-five-20261011-r1 procedural-chair-five-20261011-r2 procedural-chair-five-20261011-r3 \
         procedural-chair-smoke-20261011 dense-reach-geometry-20261011.json preflight \
         molmobot-final-stations-case1-v000-20261011-r1 molmobot-final-stations-case2-v001-20261011-r1; do
  move obstacle_augmentation "$n"
done

# ---- scene_datasets/discovery ----
for n in scene-discovery-val103-cup30-minimal-v3-20261012-r1.json \
         scene-discovery-val103-cup30-minimal-v3-20261012-r1-baselines \
         scene-discovery-val103-val104-20261012-qualified-r1.json \
         scene-discovery-val103-val104-20261012-qualified-r2.json \
         scene-discovery-val103-val104-20261012-r3-baselines \
         scene-discovery-val103-val104-20261012-r4-baselines \
         scene-discovery-val103-val104-20261012-r4.json; do
  move scene_datasets/discovery "$n"
done

# ---- scene_datasets/val103_cup30 ----
for n in scene-probe-cup30-one-thread-20261012.json \
         scene-qualified-val103-cup30-minimal-v3-20261012-r1.json \
         scene-val103-cup30-minimal-v3-20261012-r1-u000-r00 scene-val103-cup30-minimal-v3-20261012-r1-u001-r00 \
         scene-val103-cup30-minimal-v3-20261012-r1-unit-000 scene-val103-cup30-minimal-v3-20261012-r1-unit-001 \
         scene-val103-cup30-minimal-v3-20261012-r2-audit.json \
         scene-val103-cup30-minimal-v3-20261012-r2-release-integrity.json \
         scene-val103-cup30-minimal-v3-20261012-r2-u000-r00 scene-val103-cup30-minimal-v3-20261012-r2-u001-r00 \
         scene-val103-cup30-minimal-v3-20261012-r2-unit-000 scene-val103-cup30-minimal-v3-20261012-r2-unit-001 \
         scene-val103-cup30-minimal-v3-gpu-selection-20261012.csv; do
  move scene_datasets/val103_cup30 "$n"
done

# ---- scene_datasets/twohouse_v3 ----
for n in scene-twohouse-v3-20261012-192738-orchestration scene-twohouse-v3-20261012-192824-orchestration \
         scene-twohouse-v3-20261012-192824-qualified.json scene-twohouse-v3-20261012-192824-static-baselines \
         scene-twohouse-v3-20261012-192824-static.json scene-twohouse-v3-qualified42-20261012-215947-orchestration \
         scene-twohouse-v3-qualified42-20261012-215947-u000-r00 scene-twohouse-v3-qualified42-20261012-215947-u001-r00 \
         scene-twohouse-v3-qualified42-20261012-215947-u003-r00 scene-twohouse-v3-qualified42-20261012-215947-unit-000 \
         scene-twohouse-v3-qualified42-20261012-215947-unit-001 scene-twohouse-v3-qualified42-20261012-215947-unit-002 \
         scene-twohouse-v3-qualified42-20261012-215947-unit-003 scene-twohouse-v3-qualified42-unlimited-20261012.json \
         launch-qualified42.py launch-twohouse-v3.py; do
  move scene_datasets/twohouse_v3 "$n"
done

# ---- scene_datasets/tooling ----
for n in build-scene-gallery20-20261012.py scene-datasets-gpu-selection-20261012.csv \
         scene-datasets-gpu-selection-build-20261012.csv gpu-outage-20261012; do
  move scene_datasets/tooling "$n"
done

# ---- scene_datasets/val103_val104 ----
for n in scene-selected-baseline-probe-20261012.json scene-selected-baseline-probe-20261012.py \
         scene-val103-val104-20261012-r2-stop.json \
         scene-val103-val104-20261012-r1-u000-r00 scene-val103-val104-20261012-r1-u000-r01 scene-val103-val104-20261012-r1-u000-r02 \
         scene-val103-val104-20261012-r1-u001-r00 scene-val103-val104-20261012-r1-u002-r00 scene-val103-val104-20261012-r1-u003-r00 \
         scene-val103-val104-20261012-r1-u004-r00 scene-val103-val104-20261012-r1-unit-000 scene-val103-val104-20261012-r1-unit-001 \
         scene-val103-val104-20261012-r1-unit-002 scene-val103-val104-20261012-r1-unit-003 scene-val103-val104-20261012-r1-unit-004 \
         scene-val103-val104-20261012-r2-u000-r00 scene-val103-val104-20261012-r2-u001-r00 scene-val103-val104-20261012-r2-u001-r01 \
         scene-val103-val104-20261012-r2-u002-r00 scene-val103-val104-20261012-r2-u002-r01 scene-val103-val104-20261012-r2-u003-r00 \
         scene-val103-val104-20261012-r2-u003-r01 scene-val103-val104-20261012-r2-u003-r02 scene-val103-val104-20261012-r2-u005-r00 \
         scene-val103-val104-20261012-r2-u005-r01 scene-val103-val104-20261012-r2-u006-r00 scene-val103-val104-20261012-r2-u007-r00 \
         scene-val103-val104-20261012-r2-u008-r00 scene-val103-val104-20261012-r2-u008-r01 scene-val103-val104-20261012-r2-unit-000 \
         scene-val103-val104-20261012-r2-unit-001 scene-val103-val104-20261012-r2-unit-002 scene-val103-val104-20261012-r2-unit-003 \
         scene-val103-val104-20261012-r2-unit-004 scene-val103-val104-20261012-r2-unit-005 scene-val103-val104-20261012-r2-unit-006 \
         scene-val103-val104-20261012-r2-unit-007 scene-val103-val104-20261012-r2-unit-008; do
  move scene_datasets/val103_val104 "$n"
done

printf 'tidy_diagnostics: moved %d, skipped %d%s\n' "$moved" "$skipped" "$([ "$DRY" = 1 ] && echo ' (dry-run)')"
