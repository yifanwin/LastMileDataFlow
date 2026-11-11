"""构造期按关节/执行器名称迁移真实状态。"""
import mujoco

# qpos / qvel width per joint type.
_JOINT_WIDTH = {mujoco.mjtJoint.mjJNT_FREE: (7, 6), mujoco.mjtJoint.mjJNT_BALL: (4, 3),
                mujoco.mjtJoint.mjJNT_SLIDE: (1, 1), mujoco.mjtJoint.mjJNT_HINGE: (1, 1)}


def _matching_id(model, kind, name):
    """Id of the same-named object in ``model`` or -1."""
    return mujoco.mj_name2id(model, kind, name) if name else -1


def migrate_state(old, new, *, forward=True):
    """把旧模型的状态按名字迁到编辑后的新模型上（编辑会改变 qpos 布局，不能盲拷数组）。

    名字存在才迁移，类型不一致就报错：宁可拒绝也不猜。
    """
    if old.started or new.started or old.closed or new.closed:
        raise RuntimeError('state migration requires construction/preparation sessions')
    m0, m1, d0, d1 = old.model, new.model, old.data, new.data
    if len(d0.plugin_state) or len(d1.plugin_state):
        raise ValueError('plugin state migration is unsupported')
    _migrate_joints(m0, m1, d0, d1)
    _migrate_actuators(m0, m1, d0, d1)
    _migrate_bodies(m0, m1, d0, d1)
    _migrate_equalities(m0, m1, d0, d1)
    for a, b in zip(d0.warning, d1.warning):
        b.number, b.lastinfo = a.number, a.lastinfo
    if d1.userdata.shape != d0.userdata.shape:
        raise ValueError('userdata migration unsupported')
    d1.userdata[:] = d0.userdata
    d1.time = d0.time
    new.robot.copy_control_from(old.robot)
    new.clock_remainder = old.clock_remainder
    if forward:
        mujoco.mj_forward(m1, d1)


def _migrate_joints(m0, m1, d0, d1):
    for j in range(m1.njnt):
        k = _matching_id(m0, mujoco.mjtObj.mjOBJ_JOINT, m1.joint(j).name)
        if k < 0:
            continue
        if m1.jnt_type[j] != m0.jnt_type[k]:
            raise ValueError('joint type changed during migration')
        nq, nv = _JOINT_WIDTH[mujoco.mjtJoint(m1.jnt_type[j])]
        a, b = int(m1.jnt_qposadr[j]), int(m0.jnt_qposadr[k])
        d1.qpos[a:a + nq] = d0.qpos[b:b + nq]
        a, b = int(m1.jnt_dofadr[j]), int(m0.jnt_dofadr[k])
        d1.qvel[a:a + nv] = d0.qvel[b:b + nv]
        d1.qacc_warmstart[a:a + nv] = d0.qacc_warmstart[b:b + nv]
        d1.qfrc_applied[a:a + nv] = d0.qfrc_applied[b:b + nv]


def _migrate_actuators(m0, m1, d0, d1):
    for a in range(m1.nu):
        k = _matching_id(m0, mujoco.mjtObj.mjOBJ_ACTUATOR, m1.actuator(a).name)
        if k < 0:
            continue
        d1.ctrl[a] = d0.ctrl[k]
        n = int(m1.actuator_actnum[a])
        if n != int(m0.actuator_actnum[k]):
            raise ValueError('actuator state shape changed')
        if n:
            d1.act[m1.actuator_actadr[a]:m1.actuator_actadr[a] + n] = \
                d0.act[m0.actuator_actadr[k]:m0.actuator_actadr[k] + n]


def _migrate_bodies(m0, m1, d0, d1):
    for b in range(m1.nbody):
        k = _matching_id(m0, mujoco.mjtObj.mjOBJ_BODY, m1.body(b).name)
        if k < 0:
            continue
        d1.xfrc_applied[b] = d0.xfrc_applied[k]
        a, c = int(m1.body_mocapid[b]), int(m0.body_mocapid[k])
        if a >= 0 and c >= 0:
            d1.mocap_pos[a] = d0.mocap_pos[c]
            d1.mocap_quat[a] = d0.mocap_quat[c]


def _migrate_equalities(m0, m1, d0, d1):
    for e in range(m0.neq):
        name = m0.equality(e).name
        if not name:
            # 匿名 equality 无法按名字配对：只有状态与初始值一致（未被编辑）时才允许。
            if d0.eq_active[e] != m0.eq_active0[e]:
                raise ValueError('cannot migrate edited anonymous equality state')
            continue
        k = _matching_id(m1, mujoco.mjtObj.mjOBJ_EQUALITY, name)
        if k < 0:
            continue
        if m0.eq_type[e] != m1.eq_type[k]:
            raise ValueError('equality type changed during migration')
        d1.eq_active[k] = d0.eq_active[e]
