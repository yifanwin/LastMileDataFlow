"""Per-case constructors. `case2`/`case3` keep their historical behaviour via `legacy.py`."""
from .base import (CaseConstructor, Frame, INTENT, LAYERS, MEASUREMENT, PHYSICAL, PROXY, SCENE,
                   SEMANTIC, STRENGTHS, body_rotation, local_to_world, make_requirement,
                   requirement_summary, world_to_local)
from . import case1, case1_5, legacy

CLASSES = {'case1': case1.Case1, 'case1.5': case1_5.Case1_5,
           'case2': legacy.LegacyCase2, 'case3': legacy.LegacyCase3}


def constructor(session):
    """Bind the case constructor to a live build session.

    Everything the constructor measures comes from the session's current (restore) state, including
    the frozen initial base, so preflight and local_check are reproducible after any rollback.
    """
    config = session.config
    try: case_class = CLASSES[config.case_type]
    except KeyError: raise ValueError(f'unknown case type: {config.case_type}') from None
    case = case_class(session)
    frames = case.build_frames(session.sim)
    return case, frames


def requirements(session):
    case, frames = constructor(session)
    return case.preflight(session.sim) + case.local_check(session.sim, frames, [])


def preflight(session):
    case, _ = constructor(session)
    return case.preflight(session.sim)


def generate(session):
    """Enumerate candidates, unless a preflight check rejects the layout outright.

    Only *preflight* rejections block generation. A failing `case_intent` requirement is the reason
    candidates exist at all, so it must never suppress them.
    """
    case, frames = constructor(session)
    declared = case.build_frames(session.sim)
    rejected = [r for r in case.preflight(session.sim) if r['required'] and r['status'] != 'pass']
    if rejected: return [], declared, rejected
    return case.generate(session.sim, frames), declared, []


def pending_hypotheses(case):
    """What phase three must still settle; construction never asserts these."""
    return {'case1': ['current_station_failure', 'alternative_station_success'],
            'case2': ['real_handle_grasp_success'],
            'case3': ['real_planning_path_obstructed', 'possible_bypass'],
            'case1.5': ['narrow_side_passage_unsupported_by_construction',
                        'far_side_manipulation_difficulty',
                        'solvable_side_success']}[case]


def candidate_rank(candidate):
    """Same-dimension tuple ordering, minimal edit first; no cross-dimension weighting.

    Elements: (out-of-range violation count, translation|dyaw|, dyaw, instance names).
    """
    evidence = candidate.get('rank_evidence') or {}
    return (int(evidence.get('violations', 0)), float(evidence.get('edit_magnitude_m', 0.)),
            float(evidence.get('dyaw_rad', 0.)), str(candidate.get('candidate_id', '')))
