"""Diagnostic diagrams for the case-specific observation packets.

These are *diagrams for humans and the Agent*, never robot/VLA input: every entry is written with
`vla_input: False` and `schematic: True`, and the workflow keeps them in the observation packet, not
in any task input. matplotlib is already a phase-three optional dependency; absence is reported
honestly instead of failing a build.
"""
from pathlib import Path

import numpy as np


def _axes(frames, key):
    frame = frames[key]
    return np.asarray(frame.origin, dtype=float), np.asarray(frame.rotation, dtype=float)


def _figure_path(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def candidate_distribution(path, session, candidates, frames):
    """Case 1: the layout that makes a distance meaningful — tabletop bounds, target, candidates."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        return {'available': False, 'reason': f'matplotlib_unavailable:{exc}'}
    region = session.placements[session.config.target]
    frame = frames[region.region_id]
    origin, rotation = _axes(frames, region.region_id)
    target = session.sim.data.xpos[session.sim.model.body(session.config.target).id]
    local_target = frame.local(target)
    figure, axis = plt.subplots(figsize=(4.2, 3.4), dpi=110)
    a, b, c, d = region.bounds
    axis.add_patch(plt.Rectangle((a, c), b - a, d - c, fill=False, edgecolor='#666', lw=1.0,
                                 label='support region bounds'))
    points, violations = [], []
    for candidate in candidates:
        pose = np.asarray(candidate['operations'][0]['pose'][:3], dtype=float)
        local = frame.local(pose)
        (points if not candidate['rank_evidence']['violations'] else violations).append(local[:2])
    if points:
        points = np.asarray(points)
        axis.scatter(points[:, 0], points[:, 1], s=14, c='#2b7bba', label='candidate (in range)')
    if violations:
        violations = np.asarray(violations)
        axis.scatter(violations[:, 0], violations[:, 1], s=14, c='#d05a3a', marker='x',
                     label='candidate (out of range)')
    axis.scatter([local_target[0]], [local_target[1]], s=42, c='#111', marker='*', label='target now')
    base_local = frame.local(np.r_[session.initial_robot_base[:2], 0.])
    axis.scatter([base_local[0]], [base_local[1]], s=42, c='#2f8f4e', marker='s',
                 label='frozen robot base')
    axis.set_xlim(a - .18, b + .18); axis.set_ylim(c - .18, d + .18)
    axis.set_aspect('equal'); axis.grid(alpha=.25, lw=.4)
    axis.set_xlabel('support-local x (m)'); axis.set_ylabel('support-local y (m)')
    axis.set_title(f'{session.config.case_type}: candidate distribution', fontsize=9)
    axis.legend(fontsize=6, loc='best')
    figure.tight_layout(); figure.savefig(_figure_path(path)); plt.close(figure)
    return {'available': True, 'method': 'support_local_plan_diagram'}


def furniture_sides(path, session, frames, sides, distances, clearances, candidates=()):
    """Case 1.5: side directions inside the furniture frame, with the two measured differences.

    `sides` is the constructor's ordered (label, furniture-local vector, world point) triples; only
    the vectors and points are plotted, the labels annotate them.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        return {'available': False, 'reason': f'matplotlib_unavailable:{exc}'}
    frame = frames['frame_body']
    target = session.sim.data.xpos[session.sim.model.body(session.config.target).id]
    local_target = frame.local(target)
    figure, axis = plt.subplots(figsize=(4.8, 4.0), dpi=110)
    for (label, vector, world_point), distance, clearance in zip(sides, distances, clearances):
        # The triple's second element is a furniture-local *vector*; only the world point can be
        # mapped into the frame, and that mapped point is the side's plotted position.
        local = frame.local(np.asarray(world_point, dtype=float))
        axis.plot([0., local[0]], [0., local[1]], color='#999', lw=.8, ls=':')
        axis.scatter([local[0]], [local[1]], s=52, c='#d05a3a', marker='v',
                     label='side point' if label == sides[0][0] else None)
        axis.annotate(f'{label}\nd={distance:.2f} m  clearance={clearance:.2f} m',
                      xy=(local[0], local[1]), xytext=(6, 8), textcoords='offset points', fontsize=6)
    plotted = [frame.local(np.asarray(c['operations'][0]['pose'][:3], dtype=float))[:2]
               for c in candidates]
    if plotted:
        plotted = np.asarray(plotted)
        axis.scatter(plotted[:, 0], plotted[:, 1], s=14, c='#2b7bba', label='candidate')
    axis.scatter([local_target[0]], [local_target[1]], s=60, c='#111', marker='*',
                 label='target now')
    axis.axhline(0, color='#bbb', lw=.6); axis.axvline(0, color='#bbb', lw=.6)
    axis.scatter([0], [0], s=20, c='#444')
    axis.annotate('furniture origin', (0, 0), fontsize=6, xytext=(4, 4), textcoords='offset points')
    axis.set_aspect('equal'); axis.grid(alpha=.25, lw=.4)
    axis.set_xlabel('furniture-local x (m)'); axis.set_ylabel('furniture-local y (m)')
    axis.set_title('case1.5: sides bound to the furniture frame', fontsize=9)
    axis.legend(fontsize=6, loc='best')
    figure.tight_layout(); figure.savefig(_figure_path(path)); plt.close(figure)
    return {'available': True, 'method': 'furniture_local_plan_diagram'}
