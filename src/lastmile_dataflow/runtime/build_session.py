"""场景编辑与快照加载仍使用的共享函数；旧 BuildSession 已删除。"""
from dataclasses import asdict
import hashlib

import mujoco
import numpy as np

from ..io import digest


def model_identity(sim, *, robot_config_payload=None):
    """编译模型的稳定身份：模型字节 + 实例映射 + 机器人配置 + MuJoCo 版本。

    注意用 mj_saveModel 到内存 buffer 再哈希，而不是哈希磁盘文件——
    这样身份只取决于“模型内容”，与文件压缩细节无关。
    """
    buffer = np.empty(mujoco.mj_sizeModel(sim.model),dtype=np.uint8)
    mujoco.mj_saveModel(sim.model,None,buffer)
    return digest({'compiled_model':hashlib.sha256(buffer.tobytes()).hexdigest(), 'instances':sim.catalog,
                   'robot':asdict(sim.robot.config) if robot_config_payload is None else robot_config_payload,
                   'mujoco':mujoco.__version__})


def checkpoint_identity(sim, model_id):
    """检查点身份：模型身份 + 完整状态 + 固定头 + 时钟余数。

    “模型身份”与“检查点身份”分开，是因为同一个模型可以有不同的动态状态。
    """
    return digest({'model_id':model_id,'state_spec':int(sim.state_spec),'state':sim.state_vector().tolist(),
                   'fixed_head':sim.robot.fixed_head.tolist(),'clock_remainder':sim._clock_remainder})


def migrate_state(old, new, *, forward=True):
    """把旧模型的状态迁到编辑后的新模型上（编辑固定家具会触发重新编译）。

    【为什么不能直接拷贝 qpos 数组】编辑可能增删几何/物体，导致 qpos 布局完全变化，
    盲拷会错位。所以这里**按名字**逐个关节/执行器/body/等式约束迁移：
    名字存在才迁移，类型不一致就报错。这是“宁可拒绝也不猜”的一贯做法。
    """
    if old.started or new.started or old.closed or new.closed: raise RuntimeError('state migration requires construction/preparation sessions')
    m0,m1,d0,d1=old.model,new.model,old.data,new.data
    if len(d0.plugin_state) or len(d1.plugin_state): raise ValueError('plugin state migration is unsupported')
    for j in range(m1.njnt):
        name=m1.joint(j).name
        try: k=m0.joint(name).id
        except KeyError: continue
        if m1.jnt_type[j] != m0.jnt_type[k]: raise ValueError('joint type changed during migration')
        # 不同关节类型占用的 qpos/qvel 宽度不同，查表决定拷多少
        nq,nv={0:(7,6),1:(4,3),2:(1,1),3:(1,1)}[int(m1.jnt_type[j])]
        a,b=int(m1.jnt_qposadr[j]),int(m0.jnt_qposadr[k]); d1.qpos[a:a+nq]=d0.qpos[b:b+nq]
        a,b=int(m1.jnt_dofadr[j]),int(m0.jnt_dofadr[k]); d1.qvel[a:a+nv]=d0.qvel[b:b+nv]; d1.qacc_warmstart[a:a+nv]=d0.qacc_warmstart[b:b+nv]
        d1.qfrc_applied[a:a+nv]=d0.qfrc_applied[b:b+nv]
    for a in range(m1.nu):
        try: k=m0.actuator(m1.actuator(a).name).id
        except KeyError: continue
        d1.ctrl[a]=d0.ctrl[k]
        n=int(m1.actuator_actnum[a])
        if n != int(m0.actuator_actnum[k]): raise ValueError('actuator state shape changed')
        if n: d1.act[m1.actuator_actadr[a]:m1.actuator_actadr[a]+n]=d0.act[m0.actuator_actadr[k]:m0.actuator_actadr[k]+n]
    for b in range(m1.nbody):
        name=m1.body(b).name
        try: k=m0.body(name).id
        except KeyError: continue
        d1.xfrc_applied[b]=d0.xfrc_applied[k]
        a,c=int(m1.body_mocapid[b]),int(m0.body_mocapid[k])
        if a>=0 and c>=0: d1.mocap_pos[a]=d0.mocap_pos[c]; d1.mocap_quat[a]=d0.mocap_quat[c]
    for e in range(m0.neq):
        name=m0.equality(e).name
        if not name:
            # 匿名 equality 无法按名字配对：只有当状态与初始值一致时才允许（说明没被编辑过）。
            if d0.eq_active[e] != m0.eq_active0[e]: raise ValueError('cannot migrate edited anonymous equality state')
            continue
        try: k=m1.equality(name).id
        except KeyError: continue
        if m0.eq_type[e] != m1.eq_type[k]: raise ValueError('equality type changed during migration')
        d1.eq_active[k]=d0.eq_active[e]
    for a,b in zip(d0.warning,d1.warning): b.number=a.number; b.lastinfo=a.lastinfo
    if d1.userdata.shape != d0.userdata.shape: raise ValueError('userdata migration unsupported')
    d1.userdata[:]=d0.userdata; d1.time=d0.time
    new.robot.fixed_head=old.robot.fixed_head.copy(); new._clock_remainder=old._clock_remainder
    if forward:
        mujoco.mj_forward(m1,d1)
