"""Task-local intent/variant search; only validated candidates are frozen."""
from dataclasses import asdict
from pathlib import Path
import time

import numpy as np

from ..stations.sampling import select_task_stations
from .repair import repair_allowed
from .validation import proposal_scene
from ..modes.geometry import resolve_assets


def search_variant(ctx, builder, mode, root, scene_id, *, rng, task_prefix):
    settings = ctx.settings
    deadline = time.monotonic() + settings.variant_timeout_s
    metrics = {'proposals': 0, 'cheap_rejected': 0, 'physical_candidates': 0,
               'physical_trials': 0, 'repairs': 0, 'station_initializations': 0}
    result = {'scene_id': scene_id, 'proposal_intent': mode, 'status': 'rejected', 'build_metrics': metrics}
    compile_start = ctx.assets.compile_count
    metrics['asset_compiles'] = 0
    last_reason = 'proposal_budget_exhausted'
    last_checks = {}
    for _ in range(settings.max_proposals):
        if time.monotonic() >= deadline:
            last_reason = 'variant_timeout'
            break
        proposal = builder.propose(ctx, rng)
        if proposal is None:
            exhausted = getattr(builder, 'exhausted_reason', 'no_candidate_proposal')
            if metrics['proposals'] == 0 or exhausted != 'no_legal_candidate':
                last_reason = exhausted
            break
        metrics['proposals'] += 1
        cheap = builder.cheap_validate(ctx, proposal)
        if cheap.status != 'pass':
            metrics['cheap_rejected'] += 1
            last_reason = cheap.reason
            last_checks = {'cheap': asdict(cheap)}
            continue
        if proposal.edits:
            if metrics['physical_candidates'] >= settings.max_physical_candidates:
                last_reason = 'physical_candidate_budget_exhausted'
                break
            metrics['physical_candidates'] += 1
        original = proposal
        for repair_index in range(settings.max_repairs + 1):
            try:
                proposal = resolve_assets(ctx, proposal)
                metrics['asset_compiles'] = ctx.assets.compile_count - compile_start
                if repair_index == 0:
                    original = proposal
                if proposal.edits:
                    metrics['physical_trials'] += 1
                with proposal_scene(ctx, proposal, deadline=deadline) as (sim, geometry, physical):
                    checks = physical
                    if physical['valid']:
                        settled = builder.cheap_validate(ctx, proposal, sim=sim, geometry=geometry)
                        checks = {**physical, 'proxy': asdict(settled), 'valid': settled.status == 'pass'}
                    if checks['valid']:
                        stations, sampling = select_task_stations(ctx, sim, geometry, proposal)
                        metrics['station_initializations'] += sampling['new_initializations']
                        final = builder.cheap_validate(ctx, proposal, sim=sim, geometry=geometry, stations=stations)
                        checks = {**checks, 'final_proxy': asdict(final),
                                  'valid': bool(stations) and final.status == 'pass'}
                        if not stations:
                            checks['reason'] = 'no_valid_initial_stations'
                        elif final.status != 'pass':
                            checks['reason'] = final.reason
                        if not checks['valid']:
                            checks['station_evidence'] = stations + sampling['rejected_stations']
                    if checks['valid']:
                        if not proposal.edits:
                            if ctx.baseline_snapshot is None:
                                ctx.baseline_snapshot = Path('scene-files') / task_prefix / 'baseline'
                                ctx.baseline.freeze(Path(root) / ctx.baseline_snapshot)
                            location = ctx.baseline_snapshot
                        else:
                            location = Path('scene-files') / scene_id
                            sim.freeze(Path(root) / location)
                        for station in stations:
                            cell = geometry.navigation.cell(station['xy'])
                            station['clearance_base_m'] = float(geometry.clearance_base[cell]) if cell is not None else None
                        return {**result, 'status': 'accepted', 'scene': str(location), 'task': geometry.task,
                                'stations': stations, 'station_hints': proposal.station_hints,
                                'edits': proposal.runtime_changes(), 'proposal_metadata': proposal.metadata,
                                'construction_checks': checks, 'station_sampling': sampling,
                                'task_completion': 'unknown'}
                    last_reason = checks.get('reason') or checks.get('proxy', {}).get('reason', 'physical_validation_failed')
                    last_checks = checks
            except TimeoutError:
                return {**result, 'reason': 'variant_timeout', 'last_checks': last_checks}
            except ValueError as exc:
                metrics['asset_compiles'] = ctx.assets.compile_count - compile_start
                checks = {'valid': False, 'reason': str(exc)}
                last_reason = str(exc)
                last_checks = checks
            if repair_index == settings.max_repairs:
                break
            repaired = builder.repair(ctx, proposal, checks)
            if repaired is None:
                break
            if not repair_allowed(ctx, original, repaired):
                last_reason = 'repair_outside_allowed_bounds'
                break
            proposal = repaired
            metrics['repairs'] += 1
    return {**result, 'reason': last_reason, 'last_checks': last_checks}


def build_task(ctx, builders, root, *, house, seed):
    """One context for all applicable intents/variants; caller writes main JSONL."""
    rng = np.random.default_rng(seed)
    prefix = f'{ctx.baseline.source.split}{house}-{ctx.task["task_id"]}'
    for mode in ctx.recipe.modes:
        if mode == 'articulated_interaction' and ctx.task['operation'] != 'open':
            yield {'task_id': ctx.task['task_id'], 'house': house, 'proposal_intent': mode,
                   'status': 'skipped', 'reason': 'mode_not_applicable_to_operation'}
            continue
        for index in range(ctx.recipe.variants_per_intent):
            scene_id = f'{prefix}-{mode}-v{index:03d}'
            builders[mode].begin_variant(ctx, rng)
            proxy_start = ctx.proxy_initializations
            record = search_variant(ctx, builders[mode], mode, root, scene_id, rng=rng, task_prefix=prefix)
            metrics = record['build_metrics']
            metrics['proxy_initializations'] = ctx.proxy_initializations - proxy_start
            metrics['final_station_initializations'] = metrics['station_initializations']
            metrics['station_initializations'] += metrics['proxy_initializations']
            record.update(house=house, task_id=ctx.task['task_id'], seed=seed,
                          source_split=ctx.baseline.source.split,
                          dataset_split=ctx.recipe.house_splits[str(house)])
            yield record
