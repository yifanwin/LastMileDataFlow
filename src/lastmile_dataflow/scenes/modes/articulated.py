"""Natural Open tasks; no asset edits or post-contact failure prerequisite."""
from .base import ModeBuilder
from .geometry import lattice_points
from ..build.proposal import BuildProposal, ProbeResult


class ArticulatedBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        if ctx.task['operation'] == 'open':
            rows = lattice_points(ctx.geometry)
            if rows:
                yield BuildProposal('natural-open', 'articulated_interaction',
                    [rows[0]['station_id'], rows[-1]['station_id']],
                    metadata={'joint_name': ctx.task['joint_name'], 'post_contact_gate': False})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        task = ctx.task if geometry is None else geometry.task
        valid = task['operation'] == 'open' and abs(task['joint_goal'] - task['joint_initial']) > 1e-4
        if stations is not None:
            valid &= bool(stations)
        return ProbeResult('pass' if valid else 'fail', 'valid_open_candidate' if valid else 'no_valid_open_candidate',
                           {'scope': 'articulation_and_initialization_not_full_task_success'})
