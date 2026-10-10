#!/usr/bin/env bash
# 归集 outputs/ 根目录下的散落日志到唯一目录 outputs/logs/。
#
# 约定（见 README.md「输出目录约定」）：
#   outputs/logs/          所有 *.log / *.pid / *.txt 运行日志（唯一入口）
#   outputs/no_edit/<run>/ 采集产物（数据集 run 目录）
#   outputs/diagnostics/   诊断/验收 run 目录（每个 run 一个子目录）
#   outputs/dependencies/  环境与缓存（warp-cache / mpl / uv-cache / cuRobo 版本证据）
#
# 幂等：可安全重复执行；同名文件保留 logs/ 中已存在的那份，不覆盖。
# 只移动 outputs/ 根目录下的散落文件，不递归、不删除、不触碰 run 目录。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="outputs/logs"
[ -d outputs ] || exit 0
mkdir -p "$LOG_DIR"

moved=0
for pattern in 'outputs/*.log' 'outputs/*.pid' 'outputs/*.txt'; do
  for f in $pattern; do
    [ -e "$f" ] || continue
    b="$(basename "$f")"
    if [ -e "$LOG_DIR/$b" ]; then
      printf 'skip (already in logs): %s\n' "$b"
      continue
    fi
    mv -n "$f" "$LOG_DIR/$b"
    printf 'moved: %s -> %s/%s\n' "$f" "$LOG_DIR" "$b"
    moved=$((moved + 1))
  done
done

printf 'tidy_outputs: moved %d file(s) into %s\n' "$moved" "$LOG_DIR"
