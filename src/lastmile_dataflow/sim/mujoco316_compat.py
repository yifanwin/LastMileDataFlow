"""Run robosuite/robocasa on MuJoCo 3.16 (protocol decision 2026-11-24, reports/robocasa/decisions/01-mujoco-3.16.md).

Three API gaps, no physics change: robocasa asserts mujoco 3.3.1 at import; 3.16 jnt_type is numpy.int32 and
fails `in (enum, ...)`; mj_fullM takes (m, d, dst) and MjData.qM is gone.
"""
from functools import cache

import mujoco

_ROBOCASA_PIN = "3.3.1"


def _joint_addr(adr, sizes):
    def get(self, name):
        j = self.joint_name2id(name)
        start = getattr(self, adr)[j]
        n = sizes.get(int(self.jnt_type[j]), 1)
        return start if n == 1 else (start, start + n)
    return get


def _full_m(original):
    def full_m(m, a, b):
        return original(m, b, a) if isinstance(b, mujoco.MjData) else original(m, a, b)
    return full_m


@cache
def import_robocasa():
    """Import robocasa under the running MuJoCo and patch robosuite where 3.16 changed the API."""
    real = mujoco.__version__
    if real == _ROBOCASA_PIN:
        import robocasa  # noqa: F401
        return
    mujoco.__version__ = _ROBOCASA_PIN
    try:
        import robocasa  # noqa: F401
    finally:
        mujoco.__version__ = real
    from robosuite.utils import binding_utils as bu

    free, ball = int(mujoco.mjtJoint.mjJNT_FREE), int(mujoco.mjtJoint.mjJNT_BALL)
    bu.MjModel.get_joint_qpos_addr = _joint_addr("jnt_qposadr", {free: 7, ball: 4})
    bu.MjModel.get_joint_qvel_addr = _joint_addr("jnt_dofadr", {free: 6, ball: 3})
    mujoco.mj_fullM = _full_m(mujoco.mj_fullM)
    bu.MjData.qM = property(lambda self: self._data)
