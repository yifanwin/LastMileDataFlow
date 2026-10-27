"""Analyze one or more no-edit calibration runs and print the output directory."""
import argparse
import json
from pathlib import Path

from lastmile_dataflow.data.evaluation.failure_analysis import analyze_runs


def main(argv=None):
    parser = argparse.ArgumentParser(description='无编辑标定失败与目标成功率分析')
    parser.add_argument('--calibration', nargs='+', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--min-trials', type=int, default=30,
                        help='目标类别被排除前至少累计的有效 trial 数')
    parser.add_argument('--min-houses', type=int, default=2,
                        help='目标类别被排除前至少覆盖的 house 数')
    args = parser.parse_args(argv)
    result = analyze_runs(args.calibration, args.output,
                          min_trials=args.min_trials, min_houses=args.min_houses)
    print(json.dumps({'output': str(args.output), 'counts': result['counts'],
                      'exclude_categories': result['exclude_categories']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
