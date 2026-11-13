"""Derived pick judgement ``robocasa-pick-1cm-1s-v1`` checked once per control step.

Success needs, continuously for at least ``hold_s``: the runtime's native two-finger grasp check holds for
the designated target, the target touches no environment support, and the target body origin is at least
``lift_m`` above its frozen initial height. Any broken condition restarts the window. Sampling is per
control step, so this is not the per-physics-step ``strict-pick-v3`` and makes no claim of equivalence.
The native task result (place in cabinet) is recorded separately; a derived pick is not a placement.
"""

from dataclasses import dataclass

PICK_PROTOCOL = {"version": "robocasa-pick-1cm-1s-v1", "lift_m": 0.01, "hold_s": 1.0, "sampling": "control_step"}


@dataclass(frozen=True)
class PickStatus:
    time_s: float
    lift_m: float
    grasped: bool
    unsupported: bool
    lifted: bool
    hold_s: float
    success: bool

    @property
    def conditions(self):
        return {"grasped": self.grasped, "unsupported": self.unsupported, "lifted": self.lifted}


class PickJudge:
    """Hold window over runtime facts; the reference height is the frozen initial target height."""

    def __init__(self, target, initial_z, protocol=PICK_PROTOCOL):
        self.target, self.initial_z, self.protocol = target, float(initial_z), protocol
        self.start = None
        self.succeeded_at = None

    def update(self, state):
        """Advance with one ``RuntimeState``; success latches once the hold reaches ``hold_s``."""
        if state.target != self.target:
            raise ValueError(f"state describes {state.target}, judge tracks {self.target}")
        lift = float(state.target_pose[2]) - self.initial_z
        grasped = self.target in state.grasped_objects
        unsupported = not state.target_supported
        lifted = lift >= self.protocol["lift_m"]
        if grasped and unsupported and lifted:
            self.start = state.time_s if self.start is None else self.start
        else:
            self.start = None
        hold = 0.0 if self.start is None else state.time_s - self.start
        if self.succeeded_at is None and hold >= self.protocol["hold_s"] - 1e-9:
            self.succeeded_at = state.time_s
        return PickStatus(state.time_s, lift, grasped, unsupported, lifted, hold, self.succeeded_at is not None)
