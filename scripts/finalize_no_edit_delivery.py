"""Wait for the full pipeline, then build the aggregate analysis, report and replay manifest."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from lastmile_dataflow.data.evaluation.failure_analysis import analyze_runs


ROOT = Path(__file__).resolve().parents[1]


def wait_pid(pid, log):
    while pid and os.path.exists(f'/proc/{pid}'):
        log.write(f'waiting for pipeline pid={pid} {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
        log.flush()
        time.sleep(60)


def report(path, result, analysis_dir):
    lines = ['# 无编辑全体 val 最终交付', '',
             f"- 日期：{time.strftime('%Y-%m-%d')}",
             f"- 标定 run：{len(result['runs'])} 个",
             f"- house：{result['counts']['houses']}；task：{result['counts']['tasks']}；"
             f"station：{result['counts']['stations']}；有效 trial：{result['counts']['trials']}", '',
             '## 任务分类', '', '| 类别 | 数量 |', '|---|---:|']
    lines += [f'| {key} | {value} |' for key, value in result['task_classes'].items()]
    lines += ['', '## trial 级失败主表', '',
              '| 阶段/原因 | pick | open | 合计 | 占全部 |', '|---|---:|---:|---:|---:|']
    for row in result['main_table']:
        share = f"{100 * row['share']:.2f}%" if row.get('share') is not None else '—'
        lines.append(f"| {row['row']} | {row['pick']} | {row['open']} | {row['合计']} | {share} |")
    lines += ['', '## 目标类别成功率', '',
              '| 类别 | 操作 | tasks | houses | trials | 成功 | 成功率 |', '|---|---|---:|---:|---:|---:|---:|']
    for row in result['target_categories']:
        rate = f"{100 * row['success_rate']:.2f}%" if row.get('success_rate') is not None else '—'
        lines.append(f"| {row['category']} | {row['operation']} | {row['tasks']} | {row['houses']} | "
                     f"{row['trials']} | {row['successes']} | {rate} |")
    lines += ['', '## 后续批次排除类别', '',
              ', '.join(result['exclude_categories']) if result['exclude_categories'] else '无', '',
              '## 明细文件', '',
              f"- `{analysis_dir / 'planning_failures.jsonl'}`",
              f"- `{analysis_dir / 'execution_failures.jsonl'}`",
              f"- `{analysis_dir / 'failure_summary.json'}`",
              f"- `{analysis_dir / 'target_success_rates.json'}`",
              f"- `{analysis_dir / 'failure_table.md'}`",
              f"- `{analysis_dir / 'replay_manifest.jsonl'}`", '',
              '## 边界', '',
              '- 规划无解、未执行、未知 timeout 不写成任务失败。',
              '- 基础设施错误与任务失败分开；不补造 trial。',
              '- 视频由已有 rollout 离线回放，不新增物理步。']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='无编辑全量收尾')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--driver-pid', type=int)
    parser.add_argument('--analysis-out', type=Path, default=Path('outputs/_analysis/final'))
    parser.add_argument('--report', type=Path,
                        default=Path('reports/no-edit-calibration/delivery/02-final.md'))
    parser.add_argument('--video-gpu', type=int, default=0)
    parser.add_argument('--skip-video', action='store_true')
    args = parser.parse_args(argv)
    log_path = args.output_dir / 'logs' / 'no-edit-finalizer.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a') as log:
        log.write(f'finalizer start {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
        wait_pid(args.driver_pid, log)
        state = json.loads(args.state.read_text())
        runs = [args.output_dir / item for item in state.get('completed', [])]
        log.write(f'analyze {len(runs)} runs\n'); log.flush()
        result = analyze_runs(runs, args.analysis_out, min_trials=30, min_houses=2)
        report(args.report, result, args.analysis_out)
        log.write(f'report {args.report}\n'); log.flush()
        if not args.skip_video:
            manifest = args.analysis_out / 'replay_manifest.jsonl'
            if manifest.is_file() and manifest.stat().st_size:
                command = [sys.executable, ROOT / 'scripts/render_no_edit_replays.py',
                           '--manifests', manifest, '--output-dir', args.output_dir,
                           '--run-id-prefix', 'no-edit-val-final', '--gpu', args.video_gpu]
                log.write(f"replay $ {' '.join(map(str, command))}\n"); log.flush()
                subprocess.run([str(item) for item in command], check=False,
                               stdout=log, stderr=subprocess.STDOUT)
        organize = [sys.executable, ROOT / 'scripts/organize_outputs_by_task.py',
                    '--output-dir', args.output_dir]
        log.write(f"organize $ {' '.join(map(str, organize))}\n"); log.flush()
        subprocess.run([str(item) for item in organize], check=False,
                       stdout=log, stderr=subprocess.STDOUT)
        status = [sys.executable, ROOT / 'scripts/report_no_edit_status.py',
                  '--state', args.state, '--output-dir', args.output_dir,
                  '--output', ROOT / 'reports/no-edit-calibration/delivery/status.md',
                  '--driver-pid', str(args.driver_pid or 0)]
        subprocess.run([str(item) for item in status], check=False,
                       stdout=log, stderr=subprocess.STDOUT)
        log.write(f'finalizer done {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
