"""Run a native script under MuJoCo 3.16: bypass robocasa import-time version assert and the numpy-int vs enum
membership test in robosuite get_joint_qpos_addr. Physics, controllers and judge are unchanged."""
import runpy, sys
import mujoco
real = mujoco.__version__
mujoco.__version__ = "3.3.1"
import robocasa  # noqa: F401
mujoco.__version__ = real
from robosuite.utils import binding_utils as bu


def get_joint_qpos_addr(self, name):
    j = self.joint_name2id(name)
    t, addr = int(self.jnt_type[j]), self.jnt_qposadr[j]
    ndim = {int(mujoco.mjtJoint.mjJNT_FREE): 7, int(mujoco.mjtJoint.mjJNT_BALL): 4}.get(t, 1)
    return addr if ndim == 1 else (addr, addr + ndim)


def get_joint_qvel_addr(self, name):
    j = self.joint_name2id(name)
    t, addr = int(self.jnt_type[j]), self.jnt_dofadr[j]
    ndim = {int(mujoco.mjtJoint.mjJNT_FREE): 6, int(mujoco.mjtJoint.mjJNT_BALL): 3}.get(t, 1)
    return addr if ndim == 1 else (addr, addr + ndim)


bu.MjModel.get_joint_qpos_addr = get_joint_qpos_addr
bu.MjModel.get_joint_qvel_addr = get_joint_qvel_addr

_full_m = mujoco.mj_fullM


def mj_fullM(m, a, b):
    # 3.16 signature is (m, d, dst); robosuite calls (m, dst, d.qM) with qM mapped to the raw MjData
    return _full_m(m, b, a) if isinstance(b, mujoco.MjData) else _full_m(m, a, b)


mujoco.mj_fullM = mj_fullM
bu.MjData.qM = property(lambda self: self._data)
print("mujoco", real, mujoco.__file__, flush=True)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
