"""Mode algorithms propose edits and inspect shared geometric evidence."""
from abc import ABC, abstractmethod


class ModeBuilder(ABC):
    exhausted_reason = 'no_legal_candidate'

    def begin_variant(self, ctx, rng):
        self.exhausted_reason = 'no_legal_candidate'
        self._proposals = iter(self.candidates(ctx, rng))

    def propose(self, ctx, rng):
        if not hasattr(self, '_proposals'):
            self.begin_variant(ctx, rng)
        return next(self._proposals, None)

    @abstractmethod
    def candidates(self, ctx, rng):
        """Yield construction proposals; each instance belongs to one task."""

    @abstractmethod
    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        """Return pass/fail/unknown from geometry, without IK or rollout."""

    def repair(self, ctx, proposal, checks):
        return None
