"""构造期按名称迁移状态；增删物体后不能直接拷贝 qpos 数组。"""
import mujoco


def migrate_state(old, new, *, forward=True):
    if old.started or new.started or old.closed or new.closed:
        raise RuntimeError('state migration requires construction/preparation sessions')
    old_model, new_model = old.model, new.model
    old_data, new_data = old.data, new.data
    if len(old_data.plugin_state) or len(new_data.plugin_state):
        raise ValueError('plugin state migration is unsupported')

    for new_joint in range(new_model.njnt):
        name = new_model.joint(new_joint).name
        old_joint = mujoco.mj_name2id(old_model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if old_joint < 0:
            continue
        joint_type = int(new_model.jnt_type[new_joint])
        if joint_type != old_model.jnt_type[old_joint]:
            raise ValueError('joint type changed during migration')
        position_size, velocity_size = {0: (7, 6), 1: (4, 3), 2: (1, 1), 3: (1, 1)}[joint_type]
        new_position = int(new_model.jnt_qposadr[new_joint])
        old_position = int(old_model.jnt_qposadr[old_joint])
        new_data.qpos[new_position:new_position + position_size] = old_data.qpos[old_position:old_position + position_size]
        new_velocity = int(new_model.jnt_dofadr[new_joint])
        old_velocity = int(old_model.jnt_dofadr[old_joint])
        new_slice = slice(new_velocity, new_velocity + velocity_size)
        old_slice = slice(old_velocity, old_velocity + velocity_size)
        new_data.qvel[new_slice] = old_data.qvel[old_slice]
        new_data.qacc_warmstart[new_slice] = old_data.qacc_warmstart[old_slice]
        new_data.qfrc_applied[new_slice] = old_data.qfrc_applied[old_slice]

    for new_actuator in range(new_model.nu):
        name = new_model.actuator(new_actuator).name
        old_actuator = mujoco.mj_name2id(old_model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        if old_actuator < 0:
            continue
        new_data.ctrl[new_actuator] = old_data.ctrl[old_actuator]
        activation_size = int(new_model.actuator_actnum[new_actuator])
        if activation_size != int(old_model.actuator_actnum[old_actuator]):
            raise ValueError('actuator state shape changed')
        if activation_size:
            new_address = int(new_model.actuator_actadr[new_actuator])
            old_address = int(old_model.actuator_actadr[old_actuator])
            new_data.act[new_address:new_address + activation_size] = old_data.act[old_address:old_address + activation_size]

    for new_body in range(new_model.nbody):
        name = new_model.body(new_body).name
        old_body = mujoco.mj_name2id(old_model, mujoco.mjtObj.mjOBJ_BODY, name)
        if old_body < 0:
            continue
        new_data.xfrc_applied[new_body] = old_data.xfrc_applied[old_body]
        new_mocap = int(new_model.body_mocapid[new_body])
        old_mocap = int(old_model.body_mocapid[old_body])
        if new_mocap >= 0 and old_mocap >= 0:
            new_data.mocap_pos[new_mocap] = old_data.mocap_pos[old_mocap]
            new_data.mocap_quat[new_mocap] = old_data.mocap_quat[old_mocap]

    for old_equality in range(old_model.neq):
        name = old_model.equality(old_equality).name
        if not name:
            # 匿名约束无法按名字配对，只允许未编辑的初始状态。
            if old_data.eq_active[old_equality] != old_model.eq_active0[old_equality]:
                raise ValueError('cannot migrate edited anonymous equality state')
            continue
        new_equality = mujoco.mj_name2id(new_model, mujoco.mjtObj.mjOBJ_EQUALITY, name)
        if new_equality < 0:
            continue
        if old_model.eq_type[old_equality] != new_model.eq_type[new_equality]:
            raise ValueError('equality type changed during migration')
        new_data.eq_active[new_equality] = old_data.eq_active[old_equality]

    for old_warning, new_warning in zip(old_data.warning, new_data.warning):
        new_warning.number = old_warning.number
        new_warning.lastinfo = old_warning.lastinfo
    if new_data.userdata.shape != old_data.userdata.shape:
        raise ValueError('userdata migration unsupported')
    new_data.userdata[:] = old_data.userdata
    new_data.time = old_data.time
    new.robot.copy_control_from(old.robot)
    new._clock_remainder = old._clock_remainder
    if forward:
        mujoco.mj_forward(new_model, new_data)
