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
    lines = ['# 无编辑全体 val 实验结果', '',
             '- 状态：done',
             f"- 日期：{time.strftime('%Y-%m-%d')}",
             '- 替代：无',
             f'- 关联：{analysis_dir}', '',
             f"标定包含 {len(result['runs'])} 个 run，"
             f"共记录 {result['counts']['trials']} 条 trial。", '',
             '统计表、失败明细和可视化保存在实验单元的 analysis，不在报告中重复保存。',
             f"- [失败统计表]({(analysis_dir / 'failure_table.md').resolve()})",
             f"- [汇总数据]({(analysis_dir / 'failure_summary.json').resolve()})", '',
             '规划无解、未执行和未知 timeout 不当作任务失败；基础设施错误单独统计。',
             '离线视频仅回放已记录状态，不新增物理步。']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='无编辑全量收尾')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--driver-pid', type=int)
    parser.add_argument('--analysis-out', type=Path, help='默认 <单元>/analysis')
    parser.add_argument('--report', type=Path,
                        default=Path('reports/no-edit-calibration/delivery/02-final.md'))
    parser.add_argument('--video-gpu', type=int, default=0)
    parser.add_argument('--skip-video', action='store_true')
    args = parser.parse_args(argv)
    args.analysis_out = args.analysis_out or args.output_dir / 'analysis'
    log_path = args.output_dir / 'analysis' / 'finalizer.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a') as log:
        log.write(f'finalizer start {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
        wait_pid(args.driver_pid, log)
        state = json.loads(args.state.read_text())
        runs = [next(iter(sorted((args.output_dir / 'runs').glob(f'*/{item}'))), args.output_dir / 'runs' / item) for item in state.get('completed', [])]
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
        status = [sys.executable, ROOT / 'scripts/report_no_edit_status.py',
                  '--state', args.state, '--output-dir', args.output_dir,
                  '--output', args.analysis_out / 'status.md',
                  '--driver-pid', str(args.driver_pid or 0)]
        subprocess.run([str(item) for item in status], check=False,
                       stdout=log, stderr=subprocess.STDOUT)
        log.write(f'finalizer done {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
