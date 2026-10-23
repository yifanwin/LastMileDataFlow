"""Independent benchmark maps and evaluation contact sheets, exported as PNG."""
from pathlib import Path
import textwrap

import imageio.v2 as imageio
import numpy as np

from ..evaluation.recovery import RecoveryRegion
from ...utils.io import read_json, rows


def _plotting():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return plt


def _save(fig, target):
    target.parent.mkdir(exist_ok=True)
    fig.savefig(target, dpi=170, facecolor='white', bbox_inches='tight')
    _plotting().close(fig)
    return target


def _number(value, unit=''):
    return 'unknown' if value is None else f'{value:.3f}{unit}'


def _map(ax, episode, evidence, region, trace=None):
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch, Rectangle

    grid = region.grid
    h, w = grid.free.shape
    r = grid.resolution
    extent = [grid.origin[0] - r / 2, grid.origin[0] + (w - .5) * r,
              grid.origin[1] - r / 2, grid.origin[1] + (h - .5) * r]
    ax.imshow(grid.free, origin='lower', extent=extent,
              cmap=ListedColormap(['#d9dfe3', '#ffffff']), vmin=0, vmax=1)
    # XY projection of valid goal samples; heading tolerance still applies.
    for xy in {tuple(g['xy']) for g in region.goals()}:
        ax.add_patch(Rectangle(np.asarray(xy) - r / 2, r, r,
                               facecolor='#46b58b', edgecolor='#16835f', alpha=.7, lw=.4))
    for obstacle in episode['task'].get('support_obstacles', []):
        lo, hi = np.asarray(obstacle['min']), np.asarray(obstacle['max'])
        ax.add_patch(Rectangle(lo[:2], *(hi - lo)[:2], fill=False,
                               edgecolor='#a08052', lw=1.2))
    path = np.asarray(evidence['difficulty']['oracle_path']['xy'])
    ax.plot(path[:, 0], path[:, 1], '--', color='#8056ae', lw=2,
            label='Shortest recovery path', zorder=4)
    for station in region.measured:
        if not station['high']:
            continue
        xy = station['xy']
        ax.scatter(*xy, s=55, marker='D', c='#16835f', zorder=5)
        ax.annotate(f"{station['station_id']}  {station['successes']}/{station['trials']}\n"
                    f"yaw {np.degrees(station['yaw']):.1f} deg", xy,
                    xytext=(8, -27), textcoords='offset points', fontsize=8, color='#116148')
        ax.quiver(*xy, np.cos(station['yaw']), np.sin(station['yaw']),
                  angles='xy', scale_units='xy', scale=12, color='#16835f', width=.004, zorder=6)
    start = episode['station']['base']
    ax.scatter(*start[:2], marker='o', s=70, c='#df851b', edgecolors='white', zorder=8, label='S0')
    ax.annotate('S0', start[:2], xytext=(8, 10), textcoords='offset points', weight='bold', color='#a96009')
    ax.quiver(*start[:2], np.cos(start[2]), np.sin(start[2]), angles='xy',
              scale_units='xy', scale=12, color='#df851b', width=.004, zorder=7)
    target = episode['task']['anchor_world']
    ax.scatter(*target[:2], marker='*', s=130, c='#bb4b8b', label='Target', zorder=5)
    if trace is not None and len(trace):
        ax.plot(trace[:, 0], trace[:, 1], c='#137bb0', lw=2, label='Actual base trajectory', zorder=6)
        for i, marker, label in [(len(trace) // 2, 's', 'mid'), (len(trace) - 1, 'X', 'end')]:
            ax.scatter(*trace[i, :2], c='#137bb0', marker=marker, s=55, edgecolors='white', zorder=9)
            ax.annotate(label, trace[i, :2], xytext=(8, -12 if label == 'mid' else 8),
                        textcoords='offset points', fontsize=8, color='#075582')
        end = trace[-1]
        ax.quiver(*end[:2], np.cos(end[2]), np.sin(end[2]), angles='xy',
                  scale_units='xy', scale=12, color='#137bb0', width=.004, zorder=7)
    handles, labels = ax.get_legend_handles_labels()
    handles += [Patch(facecolor='#46b58b', label='Recovery region (XY approximation)'),
                Patch(facecolor='#16835f', label='High-success station'),
                Patch(facecolor='#d9dfe3', label='Navigation exclusion (inflated)')]
    ax.legend(handles=handles, loc='lower left', fontsize=8, framealpha=.95)
    ax.set(xlabel='World X (m)', ylabel='World Y (m)', aspect='equal', title='Navigation map')
    # Furniture bounds can extend outside this local navigation map.
    # Keep them clipped rather than expanding the view into unmeasured space.
    limits = np.asarray([[extent[0], extent[2]], [extent[1], extent[3]]])
    if trace is not None and len(trace):
        limits[0] = np.minimum(limits[0], trace[:, :2].min(axis=0) - .03)
        limits[1] = np.maximum(limits[1], trace[:, :2].max(axis=0) + .03)
    ax.set(xlim=limits[:, 0], ylim=limits[:, 1], facecolor='#d9dfe3')


def _facts(episode, evidence):
    d = evidence['difficulty']
    return [f"Episode: {episode['episode_id']} / {episode['station']['station_id']}",
            f"Task: {episode['task']['operation']} / {episode['task'].get('category', '')}",
            f"Case: {evidence['case']} / Family: {evidence['failure_family']}",
            f"S0 reference success: {evidence['start_successes']}/{evidence['start_trials']}",
            f"Shortest recovery path: {_number(d['path_length_m'], ' m')}",
            f"Recovery ratio: {_number(d['recovery_ratio'])}",
            f"Detour factor: {_number(d['detour_factor'])}"]


def _text(ax, lines, size=11):
    ax.axis('off')
    ax.text(0, 1, '\n'.join(lines), va='top', ha='left', fontsize=size,
            linespacing=1.65, transform=ax.transAxes)


def _summary(root, episodes, summary, results=None):
    plt = _plotting()
    fig, ax = plt.subplots(figsize=(12, max(4, 2 + len(episodes) * .4)), layout='constrained')
    fig.suptitle(('Benchmark' if results is None else 'Evaluation') + ' summary: ' + root.name, fontsize=16)
    if results is None:
        lines = [f"Episodes: {len(episodes)}", f"Build status: {summary['status']}",
                 f"New calibration rollouts: {summary.get('new_calibration_rollouts', 0)}", '']
        for scene in summary.get('scenes', []):
            lines += [scene['scene_id'], f"  low / high stations: {scene['low_stations']} / {scene['high_stations']}",
                      f"  exclusions: {scene['exclusions']}"]
    else:
        lines = [f"Task success rate: {_number(summary.get('task_success_rate'))}",
                 f"Coverage: {_number(summary.get('coverage'))}", '']
        lookup = {r['episode_id']: r for r in results}
        for e in episodes:
            result = lookup.get(e['episode_id'], {})
            lines += [f"{e['episode_id']}: {result.get('status', 'not_executed')}",
                      '  ' + result.get('reason', 'no result')]
    _text(ax, lines)
    return _save(fig, root / 'visualization' / 'summary.png')


def benchmark_view(root):
    root = Path(root)
    oracle = read_json(root / 'oracle.json')
    episodes = rows(root / 'episodes.jsonl')
    plt = _plotting()
    for episode in episodes:
        evidence = oracle['episodes'][episode['episode_id']]
        region = RecoveryRegion.from_dict(oracle['regions'][evidence['region_id']])
        fig, axes = plt.subplots(1, 2, figsize=(13, 8), gridspec_kw={'width_ratios': [1.6, 1]}, layout='constrained')
        fig.suptitle('Benchmark: ' + root.name, fontsize=17)
        _map(axes[0], episode, evidence, region)
        _text(axes[1], _facts(episode, evidence) + ['',
            'Private oracle evidence; not policy input.',
            'No policy trajectory, video or score.', '',
            f'Region expansion: {region.radius_m:.3f} m',
            f'Heading tolerance: {np.degrees(region.yaw_tolerance_rad):.1f} deg',
            'Green: XY projection across calibrated headings.',
            'Recovery requires both XY and valid heading.'], size=10)
        _save(fig, root / 'visualization' / f"{episode['episode_id']}.png")
    return _summary(root, episodes, read_json(root / 'summary.json'))


def _video_still(ax, rollout, video, index, time_s, name):
    ax.axis('off')
    ax.set_title(f'{name} | final recorded state t={time_s:.3f} s' if video else name, fontsize=11)
    if video is None:
        ax.text(.5, .5, 'No video (not recorded / not executed)', ha='center', va='center', transform=ax.transAxes)
        return
    with imageio.get_reader(rollout / video['path']) as reader:
        ax.imshow(reader.get_data(min(index, video['frames'] - 1)))
    ax.text(0, -.04, video['source'], fontsize=8, transform=ax.transAxes, va='top', color='#526575')


def evaluation_view(root):
    root = Path(root).resolve()
    benchmark = Path(read_json(root / 'config.json')['benchmark'])
    oracle = read_json(benchmark / 'oracle.json')
    episodes = rows(benchmark / 'episodes.jsonl')
    results = rows(root / 'results.jsonl')
    lookup = {r['episode_id']: r for r in results}
    plt = _plotting()
    for episode in episodes:
        eid = episode['episode_id']
        evidence = oracle['episodes'][eid]
        region = RecoveryRegion.from_dict(oracle['regions'][evidence['region_id']])
        result = lookup.get(eid, {})
        rollout = root / 'rollouts' / (result.get('rollout_id') or eid)
        trace = None
        times = np.array([0.])
        videos = {}
        if (rollout / 'trajectory.npz').exists():
            with np.load(rollout / 'trajectory.npz', allow_pickle=False) as recording:
                trace = recording['base_xyyaw']
                times = recording['time_s'] - recording['time_s'][0]
            videos = read_json(rollout / 'recording.json').get('videos', {})
        fig = plt.figure(figsize=(15, 11), layout='constrained')
        grid = fig.add_gridspec(3, 2, width_ratios=[1.2, 1], height_ratios=[.8, 1, 1])
        fig.suptitle('Evaluation: ' + root.name + ' / ' + eid, fontsize=17)
        _map(fig.add_subplot(grid[:, 0]), episode, evidence, region, trace)
        lines = _facts(episode, evidence)[:3] + [
            'Task success: ' + {'success': 'True', 'failure': 'False'}.get(
                (result.get('task_outcome') or {}).get('status', result.get('status')), 'unknown'),
            f"Status: {result.get('status', 'not_executed')}",
            'Reason: ' + '\n'.join(textwrap.wrap(result.get('reason', 'no result'), 65)),
            f"Recovery success: {result['recovery_success'] if result.get('recovery_success') is not None else 'unknown'}",
            f"Actual base path: {_number(result.get('base_path_length_m'), ' m')}",
            f"Final displacement: {_number(result.get('final_displacement_m'), ' m')}",
            f"Actual duration: {_number(times[-1], ' s') if trace is not None else 'unknown'} | {len(trace) if trace is not None else 0} states"]
        _text(fig.add_subplot(grid[0, 1]), lines, size=10)
        observation_key = 'head' if 'head' in videos else 'observation'
        for row, (key, name) in enumerate([(observation_key, 'Robot observation camera'), ('third_person', 'Third-person camera')], 1):
            _video_still(fig.add_subplot(grid[row, 1]), rollout, videos.get(key), len(times) - 1, times[-1], name)
        fig.supxlabel('Static contact sheet. Full observation / third-person MP4 videos remain in rollouts/<episode>/videos/.\n'
                      'Benchmark oracle layers are read only. Recovery region is an XY approximation with heading constraints.', fontsize=9)
        _save(fig, root / 'visualization' / f'{eid}.png')
    return _summary(root, episodes, read_json(root / 'summary.json'), results)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--benchmark', type=Path)
    source.add_argument('--evaluation', type=Path)
    args = parser.parse_args()
    print(benchmark_view(args.benchmark) if args.benchmark else evaluation_view(args.evaluation))
