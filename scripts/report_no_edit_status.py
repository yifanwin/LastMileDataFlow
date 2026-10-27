"""Write a periodically refreshed time and progress report for the no-edit run."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def timestamp(path, pattern):
    if not path.is_file():
        return None
    match = re.search(pattern, path.read_text(errors='ignore'))
    return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S') if match else None


def driver_info(path):
    text = path.read_text(errors='ignore') if path.is_file() else ''
    chunks = re.search(r'chunks=(\d+)', text)
    houses = dict(re.findall(r'calibrate (\S+) houses=(\S+)', text))
    order = re.findall(r'calibrate (\S+) houses=', text)
    return (int(chunks.group(1)) if chunks else 0), houses, order


def scene_operations(path):
    operations = {}
    if not path.is_file():
        return operations
    with path.open(errors='ignore') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            task = row.get('task') or {}
            if row.get('status') == 'accepted' and row.get('scene_id') and task.get('operation'):
                operations[row['scene_id']] = task['operation']
    return operations


def trial_counts(path, cache, scene_ops):
    if not path.is_file():
        return Counter(), 0, set(), Counter(), {}, Counter()
    stat = path.stat()
    key = str(path)
    cached = cache.get(key)
    if (cached and cached['size'] == stat.st_size and cached['mtime_ns'] == stat.st_mtime_ns
            and 'trial_ops' in cached):
        return (Counter(cached['statuses']), cached['trials'], set(cached['success_scenes']),
                Counter(cached['success_ops']),
                {op: set(items) for op, items in cached['success_scene_ops'].items()},
                Counter(cached['trial_ops']))
    counts = Counter()
    success_scenes = set()
    success_ops = Counter()
    success_scene_ops = defaultdict(set)
    trial_ops = Counter()
    trials = 0
    with path.open(errors='ignore') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            status = row.get('status') or 'unknown'
            counts[status] += 1
            trials += 1
            scene_id = row.get('scene_id')
            operation = scene_ops.get(scene_id)
            if operation not in ('pick', 'open'):
                task = row.get('task_id') or ''
                operation = task.split('-', 1)[0] if task.startswith(('pick-', 'open-')) else 'unknown'
            trial_ops[operation] += 1
            if status != 'success':
                continue
            if scene_id:
                success_scenes.add(scene_id)
            success_ops[operation] += 1
            if scene_id:
                success_scene_ops[operation].add(scene_id)
    cache[key] = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                  'statuses': dict(counts), 'trials': trials,
                  'success_scenes': sorted(success_scenes),
                  'success_ops': dict(success_ops),
                  'success_scene_ops': {op: sorted(items) for op, items in success_scene_ops.items()},
                  'trial_ops': dict(trial_ops)}
    return counts, trials, success_scenes, success_ops, success_scene_ops, trial_ops


def scene_counts(path):
    tasks = stations = 0
    if path.is_file():
        with path.open(errors='ignore') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('status') != 'accepted':
                    continue
                tasks += 1
                stations += len(row.get('stations', []))
    return tasks, stations


def read_summary(path, field):
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return data.get(field)


def source_houses(path):
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return sorted({item['house'] for item in data.get('sources', []) if item.get('house') is not None})


def house_label(houses):
    if not houses:
        return '—'
    if len(houses) == houses[-1] - houses[0] + 1:
        return f'{houses[0]}-{houses[-1]}'
    return ','.join(map(str, houses))


def minutes(seconds):
    return seconds / 60 if seconds is not None else None


def fmt_min(value):
    return '—' if value is None else f'{value:.1f}'


def fmt_rate(value):
    return '—' if value is None else f'{value:.2f}'


def fmt_pct(value):
    return '—' if value is None else f'{100 * value:.2f}%'


def fmt_hours(seconds):
    return f'{seconds / 3600:.2f} h' if seconds is not None else '—'


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成无编辑标定实时状态文件')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--driver-pid', type=int)
    args = parser.parse_args(argv)

    state = json.loads(args.state.read_text())
    completed = state.get('completed', [])
    driver_log = args.output_dir / 'logs' / 'no-edit-val-all-driver.log'
    total_chunks, house_ranges, order = driver_info(driver_log)
    last_driver = order[-1] if order else None
    current = last_driver if last_driver and last_driver not in completed else None
    now = datetime.now()

    cache_path = args.output_dir / '_analysis' / 'status_cache.json'
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    prep = {}
    prep_houses = {}
    for path in (args.output_dir / '_calibration_inputs').glob('no-edit-*'):
        value = read_summary(path / 'summary.json', 'wall_elapsed_s')
        if value is not None:
            prep[path.name] = value
            prep_houses[path.name] = source_houses(path / 'summary.json')

    rows = []
    status_total = Counter()
    success_total = 0
    success_scene_ids = set()
    success_ops_total = Counter()
    success_scene_ops_total = defaultdict(set)
    trial_ops_total = Counter()
    task_ops_total = Counter()
    trial_total = tasks_total = stations_total = 0
    cal_completed = 0.0
    for run in completed:
        cal_s = read_summary(args.output_dir / run / 'summary.json', 'elapsed_s') or 0.0
        cal_completed += cal_s
        scene_ops = scene_operations(args.output_dir / '_calibration_inputs' / run / 'scenes.jsonl')
        counts, trials, success_scenes, success_ops, success_scene_ops, trial_ops = trial_counts(
            args.output_dir / run / 'trials.jsonl', cache, scene_ops)
        trial_ops_total.update(trial_ops)
        task_ops_total.update(Counter(scene_ops.values()))
        status_total.update(counts)
        trial_total += trials
        success_total += counts.get('success', 0)
        success_scene_ids.update(success_scenes)
        success_ops_total.update(success_ops)
        for operation, scene_ids in success_scene_ops.items():
            success_scene_ops_total[operation].update(scene_ids)
        tasks, stations = scene_counts(args.output_dir / '_calibration_inputs' / run / 'scenes.jsonl')
        tasks_total += tasks
        stations_total += stations
        rows.append({'run': run, 'houses': house_ranges.get(run) or house_label(prep_houses.get(run, [])),
                     'prep': minutes(prep.get(run)),
                     'cal': minutes(cal_s), 'trials': trials, 'tasks': tasks, 'stations': stations,
                     'success': counts.get('success', 0), 'success_scenes': len(success_scenes),
                     'success_pick': success_ops.get('pick', 0), 'success_open': success_ops.get('open', 0),
                     'rate': trials / cal_s * 60 if cal_s else None,
                     'success_rate': counts.get('success', 0) / cal_s * 60 if cal_s else None,
                     'scene_rate': len(success_scenes) / cal_s * 60 if cal_s else None,
                     'pick_rate': success_ops.get('pick', 0) / cal_s * 60 if cal_s else None,
                     'open_rate': success_ops.get('open', 0) / cal_s * 60 if cal_s else None,
                     'state': 'done'})

    current_elapsed = current_eta = None
    current_trials = current_planned = 0
    current_success = current_success_scenes = 0
    current_success_ops = Counter()
    current_success_scene_ops = {}
    current_success_rate = current_scene_rate = None
    current_pick_rate = current_open_rate = None
    current_pick_scene_rate = current_open_scene_rate = None
    if current:
        start = timestamp(args.output_dir / 'logs' / f'{current}.log', r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*开始命令')
        current_elapsed = (now - start).total_seconds() if start else None
        scene_ops = scene_operations(args.output_dir / '_calibration_inputs' / current / 'scenes.jsonl')
        counts, current_trials, success_scenes, success_ops, success_scene_ops, trial_ops = trial_counts(
            args.output_dir / current / 'trials.jsonl', cache, scene_ops)
        trial_ops_total.update(trial_ops)
        task_ops_total.update(Counter(scene_ops.values()))
        status_total.update(counts)
        trial_total += current_trials
        current_success = counts.get('success', 0)
        current_success_scenes = len(success_scenes)
        current_success_ops = success_ops
        current_success_scene_ops = success_scene_ops
        success_total += current_success
        success_scene_ids.update(success_scenes)
        success_ops_total.update(success_ops)
        for operation, scene_ids in success_scene_ops.items():
            success_scene_ops_total[operation].update(scene_ids)
        current_tasks, stations = scene_counts(args.output_dir / '_calibration_inputs' / current / 'scenes.jsonl')
        tasks_total += current_tasks
        stations_total += stations
        current_planned = stations * 3
        if current_elapsed and current_trials:
            rate = current_trials / current_elapsed
            current_eta = max(current_planned - current_trials, 0) / rate if rate else None
            current_success_rate = current_success / current_elapsed * 60
            current_scene_rate = current_success_scenes / current_elapsed * 60
            current_pick_rate = success_ops.get('pick', 0) / current_elapsed * 60
            current_open_rate = success_ops.get('open', 0) / current_elapsed * 60
            current_pick_scene_rate = len(success_scene_ops.get('pick', set())) / current_elapsed * 60
            current_open_scene_rate = len(success_scene_ops.get('open', set())) / current_elapsed * 60
        rows.append({'run': current, 'houses': house_ranges.get(current) or house_label(prep_houses.get(current, [])),
                     'prep': minutes(prep.get(current)), 'cal': minutes(current_elapsed),
                     'trials': current_trials, 'tasks': current_tasks, 'stations': stations,
                     'success': current_success, 'success_scenes': current_success_scenes,
                     'success_pick': success_ops.get('pick', 0), 'success_open': success_ops.get('open', 0),
                     'rate': current_trials / current_elapsed * 60 if current_elapsed else None,
                     'success_rate': current_success_rate, 'scene_rate': current_scene_rate,
                     'pick_rate': current_pick_rate, 'open_rate': current_open_rate,
                     'state': 'running'})

    for name in sorted(prep):
        if name in completed or name == current or (args.output_dir / name).exists():
            continue
        tasks, stations = scene_counts(args.output_dir / '_calibration_inputs' / name / 'scenes.jsonl')
        rows.append({'run': name, 'houses': house_ranges.get(name) or house_label(prep_houses.get(name, [])),
                     'prep': minutes(prep[name]),
                     'cal': None, 'trials': 0, 'tasks': tasks, 'stations': stations,
                     'success': 0, 'success_scenes': 0,
                     'success_pick': 0, 'success_open': 0, 'rate': None,
                     'success_rate': None, 'scene_rate': None,
                     'pick_rate': None, 'open_rate': None, 'state': 'prepared'})

    pilot = 'no-edit-pilot-c000'
    pilot_start = timestamp(args.output_dir / 'logs' / f'{pilot}.log',
                            r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*开始命令')
    experiment_start = (pilot_start - timedelta(seconds=prep.get(pilot, 0))) if pilot_start else None
    wall = (now - experiment_start).total_seconds() if experiment_start else None
    cal_total = cal_completed + (current_elapsed or 0)
    non_cal = max(wall - cal_total, 0) if wall is not None else None
    overall_success_rate = success_total / (cal_total / 60) if cal_total else None
    overall_scene_rate = len(success_scene_ids) / (cal_total / 60) if cal_total else None
    overall_pick_rate = success_ops_total.get('pick', 0) / (cal_total / 60) if cal_total else None
    overall_open_rate = success_ops_total.get('open', 0) / (cal_total / 60) if cal_total else None
    overall_pick_scene_rate = len(success_scene_ops_total.get('pick', set())) / (cal_total / 60) if cal_total else None
    overall_open_scene_rate = len(success_scene_ops_total.get('open', set())) / (cal_total / 60) if cal_total else None

    manifest = args.output_dir / 'replays' / 'incremental_valid_manifest.jsonl'
    videos = Counter()
    video_runs = {}
    if manifest.is_file():
        for line in manifest.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            videos[row.get('kind')] += 1
            name = Path(row.get('calibration', '')).name
            video_runs.setdefault(name, Counter())[row.get('kind')] += 1
    rendered = 0
    state_path = args.output_dir / 'replays' / 'incremental_valid_state.json'
    if state_path.is_file():
        rendered = len(json.loads(state_path.read_text()).get('rendered', []))

    heatmaps = {}
    for run in completed + ([current] if current else []):
        figures = args.output_dir / run / 'figures'
        heatmaps[run] = len(list(figures.glob('*/*.svg'))) if figures.is_dir() else 0

    driver_alive = os.path.exists(f'/proc/{args.driver_pid}') if args.driver_pid else None
    prepared = [row for row in rows if row['state'] == 'prepared']
    full_chunks = [row['cal'] for row in rows
                   if row['state'] == 'done' and row['run'].startswith('no-edit-val-all')
                   and row['houses'] != '—' and '-' in row['houses']
                   and int(row['houses'].split('-')[-1]) - int(row['houses'].split('-')[0]) >= 20]
    avg_chunk = sum(full_chunks) / len(full_chunks) if full_chunks else None
    driver_done = len([run for run in completed if run.startswith('no-edit-val-all')])
    remaining_chunks = max(total_chunks - driver_done - (1 if current else 0), 0)
    eta_chunks = remaining_chunks * avg_chunk if avg_chunk is not None else None
    eta_total = (eta_chunks or 0) + (current_eta or 0) / 60 if eta_chunks is not None else None

    pick_tasks = task_ops_total.get('pick', 0)
    open_tasks = task_ops_total.get('open', 0)
    pick_valid = len(success_scene_ops_total.get('pick', set()))
    open_valid = len(success_scene_ops_total.get('open', set()))
    total_tasks = pick_tasks + open_tasks
    total_valid = pick_valid + open_valid
    pick_task_pct = pick_valid / pick_tasks if pick_tasks else None
    open_task_pct = open_valid / open_tasks if open_tasks else None
    overall_task_pct = total_valid / total_tasks if total_tasks else None
    pick_success_pct = (success_ops_total.get('pick', 0) / trial_ops_total.get('pick', 0)
                        if trial_ops_total.get('pick', 0) else None)
    open_success_pct = (success_ops_total.get('open', 0) / trial_ops_total.get('open', 0)
                        if trial_ops_total.get('open', 0) else None)
    overall_success_pct = success_total / trial_total if trial_total else None
    overall_trial_rate = trial_total / (cal_total / 60) if cal_total else None
    heatmap_total = sum(heatmaps.values())
    ranges = [row['houses'] for row in rows
              if row['state'] in ('done', 'running') and row['houses'] != '—' and '-' in row['houses']]
    overall_houses = (f"{min(int(item.split('-')[0]) for item in ranges)}-"
                      f"{max(int(item.split('-')[-1]) for item in ranges)}") if ranges else '—'

    lines = ['# 无编辑标定实时状态', '',
             '- 状态：active（自动生成，覆盖更新）',
             f"- 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
             f'- 驱动：{"运行中" if driver_alive else "已退出"}' +
             (f'（PID {args.driver_pid}）' if args.driver_pid else ''), '',
             '## 1. 总体', '',
             '| 指标 | 数值 |', '|---|---:|',
             f'| 墙钟（试点准备起） | {fmt_hours(wall)} |',
             f'| 已完成标定 | {fmt_hours(cal_completed)} |',
             f'| 当前 chunk 已跑 | {fmt_hours(current_elapsed)} |',
             f'| 标定累计 | {fmt_hours(cal_total)} |',
             f'| 非标定墙钟 | {fmt_hours(non_cal)} |',
             f'| 累计准备（与标定重叠） | {fmt_hours(sum(prep.values()))} |',
             f'| 已完成 chunk（不含试点） | {driver_done}/{total_chunks} |',
             f'| 当前 chunk | {current or "—"} |',
             f'| 已渲染 S0/S1 视频（task-mode） | {rendered}（S0 {videos["S0"]} / S1 {videos["S1"]}） |',
             f'| 排除目标类别 | {len(state.get("exclude_categories", []))} |', '']

    lines += ['## 2. 各 chunk 时间与产出', '',
              '| chunk | houses | 准备 min | 标定 min | trial | trial/min | success | success/min | success rate | 任务数 | 有效任务 | 有效任务/min | 有效任务率 | 热图 | 状态 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for row in rows:
        evaluated = row['state'] != 'prepared'
        success_pct = row['success'] / row['trials'] if evaluated and row['trials'] else None
        task_pct = row['success_scenes'] / row['tasks'] if evaluated and row['tasks'] else None
        lines.append(f"| {row['run']} | {row['houses']} | {fmt_min(row['prep'])} | "
                     f"{fmt_min(row['cal'])} | {row['trials']:,} | {fmt_min(row['rate'])} | "
                     f"{row['success']:,} | {fmt_rate(row['success_rate'])} | {fmt_pct(success_pct)} | "
                     f"{row['tasks']:,} | {row['success_scenes']:,} | {fmt_rate(row['scene_rate'])} | "
                     f"{fmt_pct(task_pct)} | {heatmaps.get(row['run'], 0)} | {row['state']} |")
    lines.append(f"| **总体** | {overall_houses} | — | {fmt_min(minutes(cal_total))} | {trial_total:,} | "
                 f"{fmt_min(overall_trial_rate)} | {success_total:,} | {fmt_rate(overall_success_rate)} | "
                 f"{fmt_pct(overall_success_pct)} | {total_tasks:,} | {total_valid:,} | "
                 f"{fmt_rate(overall_scene_rate)} | {fmt_pct(overall_task_pct)} | {heatmap_total:,} | — |")
    lines += ['', '> `prepared` 表示输入已准备、尚未开始标定；准备自 c001 起与上一批标定重叠。',
              '> 任务 = 房间 + 目标物体；有效任务 = 至少 1 条 success trial；总体行的准备时间不求和不重复计。', '',
              '### 2.1 总体任务（pick / open）', '',
              '| 操作 | 总任务数 | 累计有效任务数 | 每分钟有效任务数 | 有效任务率 |',
              '|---|---:|---:|---:|---:|',
              f'| pick | {pick_tasks:,} | {pick_valid:,} | {fmt_rate(overall_pick_scene_rate)} | {fmt_pct(pick_task_pct)} |',
              f'| open | {open_tasks:,} | {open_valid:,} | {fmt_rate(overall_open_scene_rate)} | {fmt_pct(open_task_pct)} |',
              f'| **合计** | **{total_tasks:,}** | **{total_valid:,}** | '
              f'**{fmt_rate(overall_scene_rate)}** | **{fmt_pct(overall_task_pct)}** |', '',
              '### 2.2 总体 trial（pick / open）', '',
              '| 操作 | 总 trial | 累计成功 trial | 每分钟成功 trial 数 | success rate |',
              '|---|---:|---:|---:|---:|',
              f"| pick | {trial_ops_total.get('pick', 0):,} | {success_ops_total.get('pick', 0):,} | {fmt_rate(overall_pick_rate)} | {fmt_pct(pick_success_pct)} |",
              f"| open | {trial_ops_total.get('open', 0):,} | {success_ops_total.get('open', 0):,} | {fmt_rate(overall_open_rate)} | {fmt_pct(open_success_pct)} |",
              f'| **合计** | **{trial_ops_total.get("pick", 0) + trial_ops_total.get("open", 0):,}** | '
              f'**{success_ops_total.get("pick", 0) + success_ops_total.get("open", 0):,}** | '
              f'**{fmt_rate(overall_success_rate)}** | **{fmt_pct(overall_success_pct)}** |', '',
              '> 总任务数/总 trial 为已完成 + 当前 chunk；分母为标定累计分钟。', '']

    lines += ['## 3. Trial 状态分布（已完成 + 当前）', '',
              '| 状态 | 数量 | 占比 |', '|---|---:|---:|']
    for status, count in status_total.most_common():
        share = f'{100 * count / trial_total:.2f}%' if trial_total else '—'
        lines.append(f'| {status} | {count:,} | {share} |')
    lines.append(f'| **合计** | **{trial_total:,}** | **100%** |')
    lines += ['', '> 规划无解、未执行和 unknown 不是任务失败；`episode_error`/`timeout` 与任务失败分开。', '']

    lines += ['## 4. 视频与热图', '',
              f'- 视频状态文件：`{state_path}`；清单：`{manifest}`。',
              f'- 已渲染 {rendered} 条；S0 {videos["S0"]} 条、S1 {videos["S1"]} 条。',
              '- 单卡约 19.5 s/条；8 卡并行实测约 16.3 条/min（约 5.3×单卡）。',
              '- 热图由 16 workers 离线导出，单批约 1–2 min。', '',
              '| 标定 run | S0 | S1 | 热图 |', '|---|---:|---:|---:|']
    for run in completed + ([current] if current else []):
        counts = video_runs.get(run, Counter())
        lines.append(f"| {run} | {counts['S0']} | {counts['S1']} | {heatmaps.get(run, 0)} |")
    lines += ['', '> S0 全部为 0 步规划失败的场景没有轨迹，不生成 S0 视频，也不补造。', '']

    lines += ['## 5. 目录视图', '',
              '- `figures/`、`preparations/`、`planning/`、`rollouts/`：`<pick|open>/<类别>/<valid_task|no_valid_task>/<条目>`。',
              '- `replays/`：`<pick|open>/<类别>/<valid_task|no_valid_task>/<success|failure>/<scene_id>`，不再经过 replay-run 层。',
              '- 分类视图是符号链接，已有结果不移动、不复制；每 30 min 自动重建。',
              '- 目录中的 `valid_task` 与第 1/2 节一致：任务至少 1 条 success trial。', '',
              '## 6. ETA', '',
              f'- 当前 chunk 剩余：{fmt_min(minutes(current_eta) if current_eta else None)} min。',
              f'- 已完成 25 房 chunk 平均标定：{fmt_min(avg_chunk)} min。',
              f'- chunk 进度：已完成 {driver_done}，当前 {1 if current else 0}，总 {total_chunks}。',
              f'- 剩余 {remaining_chunks} 个 chunk，预计标定 {fmt_hours(eta_chunks * 60 if eta_chunks else None)}；'
              f'含当前 chunk 共 {fmt_hours(eta_total * 60 if eta_total is not None else None)}。',
              '- 不含最终全量失败分析和代表视频；排除类别继续生效后实际可能更短。', '',
              '## 7. 口径', '',
              '- 准备、标定、热图、视频均来自真实 `summary.json`、日志时间戳和产物计数。',
              '- 视频与热图常与标定并行，不能与标定时间直接相加。',
              '- 剔除规则：累计 ≥1000 trial 且 success rate < 0.1%；Cup、Mug、Bowl、SoapBottle、SprayBottle、Vase 永久保留。',
              '- 状态文件由 `scripts/report_no_edit_status.py` 覆盖生成，不写回任何标定记录。']

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    temp.write_text('\n'.join(lines) + '\n')
    temp.replace(args.output)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1) + '\n')
    print(json.dumps({'output': str(args.output), 'trials': trial_total,
                      'rendered': rendered, 'current': current}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
