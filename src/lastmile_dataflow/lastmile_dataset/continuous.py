"""S0 → navigation → S1 → operation: one continuous cuRobo attempt of the last-mile dataset."""

from lastmile_dataflow.operation.attempt import finish_navigation
from lastmile_dataflow.operation.curobo import CuroboAttempt
from lastmile_dataflow.operation.episode import isolated_episode
from lastmile_dataflow.planning.navigation.follow import navigate


class ContinuousAttempt(CuroboAttempt):
    """Follow the planned base path from S0 to S1 in the same simulation, then operate (with retries)."""

    navigation_status = "running"

    def __init__(self, *args, path, goal, **kwargs):
        super().__init__(*args, **kwargs)
        self.path, self.goal = path, goal

    @property
    def retries(self):
        return self.config.operation_retries

    def before_operation(self, ctx, outcome):
        outcome.navigation = navigate(ctx, self.path, self.goal)

    def finish_before_operation(self, ctx, outcome):
        outcome.navigation = finish_navigation(
            outcome.navigation,
            ctx.navigation_contacts,
            outcome.reason,
            steps=sum(phase == "navigation" for phase in ctx.recorder.phases),
        )


@isolated_episode
def run_continuous_attempt(baseline, task, station, *args, **kwargs):
    return ContinuousAttempt(baseline, task, station, *args, **kwargs).run()
