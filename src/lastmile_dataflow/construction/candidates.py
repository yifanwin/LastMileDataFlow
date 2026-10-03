"""Case-dispatched candidate generation: revision-bound, minimal-edit, same-dimension ordering."""
from .generate import support_move_candidates
from .cases import candidate_rank, constructor, generate


def _legacy_candidates(session):
    """case2/case3 keep their historical grid + penalty ranking, isolated from the shared layer."""
    from .legacy_candidates import candidates as legacy
    return legacy(session)


def candidates(session):
    """Qualified candidates for the session's case, ordered by the shared same-dimension rank.

    New cases return support-local line candidates carrying `rank_evidence` (out-of-range count,
    then translation, then yaw); ordering is a tuple comparison over exactly those, never a penalty
    sum across dimensions.
    """
    session.active()
    if session.config.case_type in ('case2', 'case3'):
        return _legacy_candidates(session)
    rows, _, _ = generate(session)
    for row in rows: row.pop('_identity', None)
    return sorted(rows, key=candidate_rank)
