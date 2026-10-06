"""Exclusive construction sandbox. Never changes Simulation.started or calls begin().

【这是什么】阶段二的“构建沙盒”：允许在**准备阶段**编辑场景（移动/旋转/增删物体），
然后真实静置、检查、必要时回滚。它与阶段一的执行严格隔离：

  同一份 Simulation 类，但构建期永远不调用 begin()，因此永远可以 restore。
  这既满足了“编辑需要反复试错”，又没有破坏“begin() 后禁止重置”的铁律——
  因为构建期根本不算连续执行。

【核心方法】
  transact()  —— 一次事务：快照 → 校验 → 编辑 → 静置 → 检查 → 提交/回滚
  settle()    —— 只做 mj_step，让物理自己收敛（不逐帧写位姿）
  validate()  —— 调用 validation/placement.py 给结论
  freeze()    —— 冻结成 v2 场景版本（含 model_id/checkpoint_id/mapping_id）
"""
from dataclasses import asdict
from pathlib import Path
import hashlib
import time

import mujoco
import numpy as np

from ..catalog.assets import AssetPool
from ..catalog.index import dependencies
from ..io import digest, file_digest, write_json, read_json
from ..scenes.geometry import extract_regions, descendants
from ..scenes.initialization import initialize_robot
from ..scenes.source import instance_catalog
from ..validation.placement import inspect_build, poses
from .simulation import Simulation


def model_identity(sim):
    """编译模型的稳定身份：模型字节 + 实例映射 + 机器人配置 + MuJoCo 版本。

    注意用 mj_saveModel 到内存 buffer 再哈希，而不是哈希磁盘文件——
    这样身份只取决于“模型内容”，与文件压缩细节无关。
    """
    buffer = np.empty(mujoco.mj_sizeModel(sim.model),dtype=np.uint8)
    mujoco.mj_saveModel(sim.model,None,buffer)
    return digest({'compiled_model':hashlib.sha256(buffer.tobytes()).hexdigest(), 'instances':sim.catalog,
                   'robot':asdict(sim.robot.config),'mujoco':mujoco.__version__})


def checkpoint_identity(sim, model_id):
    """检查点身份：模型身份 + 完整状态 + 固定头 + 时钟余数。

    “模型身份”与“检查点身份”分开，是因为同一个模型可以有不同的动态状态。
    """
    return digest({'model_id':model_id,'state_spec':int(sim.state_spec),'state':sim.state_vector().tolist(),
                   'fixed_head':sim.robot.fixed_head.tolist(),'clock_remainder':sim._clock_remainder})


def migrate_state(old, new):
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
    mujoco.mj_forward(m1,d1)


class BuildSession:
    """构建期仿真独占会话。生命周期：__init__ → (transact/settle/validate)* → freeze/close。"""

    def __init__(self, source, robot_config, config, collection, *, path=None, initial_frozen_dir=None):
        self.config,self.collection,self.path=config,collection,Path(path) if path else None
        self.edits_used=0; self.record_images=False; self.simulated_s=0.
        self.closed=False; self.revision=0; self.transactions=[]; self.placements={}; self.extra_instances={}
        self.last_check=None; self.history=[]; self._undo=[]
        # 墙钟期限：构建必须在有限时间内退出，避免在坏候选上无限打转。
        self.deadline=time.monotonic()+config.budget.timeout_s
        self.pool=AssetPool(config.asset_pool)
        if initial_frozen_dir:
            # 复用阶段一验证过的编译模型：避免为了“状态编辑”重编译远程网格。
            # 但只允许“未编辑、无 legacy restoration”的 v1 冻结模型，防止身份不清。
            version=read_json(Path(initial_frozen_dir)/'version.json')
            if version.get('schema_version') not in (None,'1.0') or version.get('restoration') is not None:
                raise ValueError('cached build initialization requires an unedited v1 foundation model')
            if any(version['source'][k] != v for k,v in asdict(source).items()): raise ValueError('cached source descriptor mismatch')
            self.sim=Simulation.from_snapshot(initial_frozen_dir,robot_config,target=config.target)
            self.spec=None  # lazy description; state-only edits need no remote mesh recompilation
        else:
            self.sim=Simulation.from_source(source,robot_config,target=config.target)
            self.spec=self.sim.spec
        try:
            # 初始化机器人 + 选出“任务目标要放的支撑面 region”
            self.initialization=initialize_robot(self.sim,collection,base=config.robot_base)
            self.initial_robot_base=self.sim.robot.group('base').copy()
            region=self.select_region(config.support,config.region_geom)
            self.placements[config.target]=region
            if config.case_type=='case3':
                # case3 的障碍也要放在同一平面上
                try: self.sim.model.body(config.parameters['obstacle'])
                except KeyError: pass
                else: self.placements[config.parameters['obstacle']]=region
            # 提前确认被保护对象真实存在（否则后面检查无意义）
            for name in config.protected: self.sim.model.body(name)
            # 记录初始位姿，后续用它判断“被保护/邻居对象有没有被碰动”
            self.initial_poses=poses(self.sim)
            if initial_frozen_dir:
                self.dependencies=[{'path':source.xml_path,'exists':None,'sha256':version['source']['xml_sha256'],'kind':'mjcf','resolution':'frozen_manifest'},
                                   {'path':robot_config.model_path,'exists':None,'sha256':None,'kind':'robot','resolution':'embedded_in_verified_mjb'}]
                self.source_digest=version['source']
            else:
                self.dependencies=dependencies(source.xml_path)+dependencies(robot_config.model_path)
                self.source_digest=source.provenance()
        except BaseException:
            self.sim.close(); raise

    def _guard(self):
        """所有公开操作的第一道守卫：已关闭/已开始执行则一律拒绝。"""
        if self.closed or self.sim.closed or self.sim.started or self.sim.robot.execution_started:
            raise RuntimeError('construction is closed or continuous execution has begun')

    def active(self):
        """守卫 + 墙钟检查。每个可能耗时的操作前都要调用。"""
        self._guard()
        if time.monotonic()>self.deadline: raise TimeoutError('build wall-clock budget exhausted')

    def select_region(self, support, geom=None):
        """从支撑物体的碰撞几何里提取“可放东西的水平面”。"""
        regions=extract_regions(self.sim,support,geom)
        if not regions: raise ValueError('no reliable planar support region')
        return regions[0]

    @property
    def model_id(self): return model_identity(self.sim)
    @property
    def checkpoint_id(self): return checkpoint_identity(self.sim,self.model_id)

    def snapshot(self):
        """内部检查点：保存 spec + sim 对象 + 状态 + 放置关系，用于回滚。

        注意它保存的是**整个 sim 对象引用**（不是序列化副本），所以回滚非常快，
        代价是构建期需要同时持有新旧两个仿真实例（编辑固定家具时）。
        """
        self.active()
        return {'spec':self.spec.copy() if self.spec is not None else None,'sim':self.sim,'state':self.sim.state_vector().copy(),
                'head':self.sim.robot.fixed_head.copy(),'clock':self.sim._clock_remainder,
                'warnings':[(int(w.number),float(w.lastinfo)) for w in self.sim.data.warning],
                'placements':self.placements.copy(),'extra_instances':self.extra_instances.copy()}

    def restore(self, checkpoint):
        """回滚到检查点。注意：超时后仍允许回滚（保证能收尾），但 begin() 后不允许。"""
        self._guard()  # Rollback remains legal after timeout, never after begin()/close().
        if checkpoint['sim'].started or checkpoint['sim'].closed: raise RuntimeError('checkpoint is not a construction session')
        if self.sim is not checkpoint['sim']: self.sim.close()
        self.sim=checkpoint['sim']; self.spec=checkpoint['spec']
        mujoco.mj_setState(self.sim.model,self.sim.data,checkpoint['state'],self.sim.state_spec)
        self.sim.robot.fixed_head=checkpoint['head'].copy(); self.sim._clock_remainder=checkpoint['clock']
        self.placements=checkpoint['placements']; self.extra_instances=checkpoint['extra_instances']
        for w,(n,info) in zip(self.sim.data.warning,checkpoint['warnings']): w.number=n; w.lastinfo=int(info)
        mujoco.mj_forward(self.sim.model,self.sim.data)
        self.revision+=1; self.last_check=None; self.history=[]

    def settle(self):
        """真实静置：只调用 mj_step 让物理自然演化，绝不逐帧写位姿或传送。

        流程：先采样一次 → 循环 mj_step 并记录历史 → 达到 settle_s 后做一次检查，
        如果没有“不稳定/飞走”问题就提前结束，否则最多跑到 max_settle_s。
        历史 history 会被 validate() 用来判断稳定性窗口。
        """
        self.active(); self.history=[]
        self.revision+=1; self.last_check=None
        names=list(self.placements)
        def sample():
            objects={}
            for name in names:
                b=self.sim.model.body(name).id; v=np.zeros(6)
                mujoco.mj_objectVelocity(self.sim.model,self.sim.data,mujoco.mjtObj.mjOBJ_BODY,b,v,0)
                objects[name]={'pose':np.r_[self.sim.data.xpos[b],self.sim.data.xquat[b]].copy(),'velocity':v.copy()}
            anomalies=[]
            for contact in self.sim.data.contact:
                if contact.dist < -self.config.protocol.penetration_m and self.sim.model.geom_bodyid[contact.geom1] != self.sim.model.geom_bodyid[contact.geom2]:
                    anomalies.append({'code':'severe_penetration_during_settle','distance_m':float(contact.dist),'geoms':[self.sim.model.geom(int(contact.geom1)).name,self.sim.model.geom(int(contact.geom2)).name]})
            self.history.append({'time':float(self.sim.data.time),'objects':objects,'anomalies':anomalies})
        mujoco.mj_forward(self.sim.model,self.sim.data); sample()
        p=self.config.protocol
        start=float(self.sim.data.time)
        while self.sim.data.time-start < p.max_settle_s-1e-10:
            self.active()
            # Only mj_step; robot ctrl remains constant, never teleport or reset each frame.
            mujoco.mj_step(self.sim.model,self.sim.data); self.simulated_s+=self.sim.model.opt.timestep
            mujoco.mj_forward(self.sim.model,self.sim.data); sample()
            if self.sim.data.time-start >= p.settle_s-1e-10:
                check=inspect_build(self,self.history,{},include_requirements=False)
                if not any(x['code'] in ('unstable_or_flight','stability_window_missing') for x in check['issues']): break
        return self.history

    def validate(self, baseline=None, *, include_requirements=True):
        """跑全部落位/稳定性/邻居/机器人状态检查，记录到 last_check 供失败现场留证。"""
        self.active()
        self.last_check=inspect_build(self,self.history,baseline if baseline is not None else self.protected_baseline(),include_requirements=include_requirements)
        self.last_check['revision']=self.revision
        return self.last_check

    def protected_baseline(self):
        """被保护对象（含不可编辑对象）的初始位姿基线，用于检测“有没有被碰动”。"""
        return {n:p for n,p in self.initial_poses.items() if n not in self.config.editable or n in self.config.protected}

    def _precheck(self, operations, revision):
        """编辑请求的**权限与合法性**审查（在真正动模型之前）。

        这是“Agent 只能返回建议，不能取得仿真编辑权限”的实现位置：
        无论候选来自规则还是模型，都必须通过这里；越权/受保护/已删除依赖一律拒绝。
        """
        self.active()
        if revision != self.revision: raise ValueError('stale operation revision')   # 版本过期拒绝
        if not isinstance(operations,list) or not operations: raise ValueError('empty transaction')
        names={o.get('instance') for o in operations if isinstance(o,dict)}
        for o in operations:
            if not isinstance(o,dict) or set(o)-{'op','instance','pose','asset_id','region_id','reason'} or not {'op','instance'} <= set(o):
                raise ValueError('invalid operation schema')
            op,name=o['op'],o['instance']
            # 白名单：操作类型、可编辑对象；受保护对象和机器人本体永远不可编辑
            if op not in self.config.allowed_operations or name not in self.config.editable or name in self.config.protected or name.startswith('robot_0/'):
                raise PermissionError('edit not allowed/protected')
            if op=='delete' and name in (self.config.target,self.config.parameters.get('obstacle')):
                raise PermissionError('required target/obstacle cannot be deleted')
            if op in ('move','rotate','add'):
                pose=np.asarray(o.get('pose'),dtype=float)
                if pose.shape!=(7,) or not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:])-1)>1e-6: raise ValueError('pose must be finite xyz + normalized wxyz')
            if op=='add':
                self._ensure_spec()
                if self.spec.body(name) is not None: raise ValueError('instance id conflict')
                self.pool.load(o.get('asset_id'))     # 资产资格/摘要检查在这里
            else:
                body=self.sim.model.body(name).id
                if self.sim.model.body_parentid[body]!=0: raise ValueError('only top-level stable instances editable')
                if op=='delete' or self.sim.model.body_jntnum[body]==0:
                    # Metadata dependency is only a conservative rejection hint. Contacts add physical evidence.
                    # 删除/移动固定家具时，必须同时处理“压在上面”或“依附于它”的对象，否则拒绝。
                    dependencies_set={x['instance_id'] for x in self.sim.catalog if x.get('parent_instance_id')==name}
                    dependencies_set |= {n for n,r in self.placements.items() if r.support==name}
                    bodies=descendants(self.sim.model,body)
                    for c in self.sim.data.contact:
                        ba,bb=int(self.sim.model.geom_bodyid[c.geom1]),int(self.sim.model.geom_bodyid[c.geom2])
                        other=bb if ba in bodies and bb not in bodies else ba if bb in bodies and ba not in bodies else None
                        if other is not None and other!=0 and self.sim.data.xpos[other,2] > self.sim.data.xpos[body,2]+.005:
                            for x in self.sim.catalog:
                                if other in descendants(self.sim.model,x['body_id']) and x['instance_id']!=name:
                                    dependencies_set.add(x['instance_id'])
                    if dependencies_set-names: raise ValueError(f'unhandled support dependents: {sorted(dependencies_set-names)}')
                # 固定家具（无关节）只允许“保持高度的水平平移/世界 yaw 旋转”
                if self.sim.model.body_jntnum[body]==0 and op in ('move','rotate'):
                    rotation=np.zeros(9); mujoco.mju_quat2Mat(rotation,np.asarray(o['pose'][3:],dtype=float))
                    relative=rotation.reshape(3,3) @ self.sim.data.xmat[body].reshape(3,3).T
                    if abs(o['pose'][2]-self.sim.data.xpos[body,2])>1e-8 or not np.allclose(relative[:,2],[0,0,1],atol=1e-6):
                        raise ValueError('fixed furniture edits only support horizontal translation/world yaw')
                if op=='rotate' and not np.allclose(o['pose'][:3],self.sim.data.xpos[body],atol=1e-8): raise ValueError('rotate cannot translate')
            if o.get('region_id') and o['region_id'] not in {r.region_id for r in self.placements.values()}:
                raise ValueError('unknown region id')

    def _ensure_spec(self):
        """需要改模型拓扑时才构建 spec（状态编辑不需要，省掉远程网格重编译）。"""
        if self.spec is None:
            if self.sim.source.provenance()!=self.source_digest: raise ValueError('live source changed; cannot rebuild cached foundation')
            self.spec=Simulation.prepare_spec(self.sim.source,self.sim.robot.config)

    def _rebuild(self):
        """重新编译模型并把旧状态迁移过来；返回旧 sim，供调用方在合适时机关闭。"""
        model=self.spec.compile()
        catalog=instance_catalog(model,self.sim.source)
        for name,a in self.extra_instances.items():
            catalog=[x for x in catalog if x['instance_id']!=name]
            catalog.append({'instance_id':name,'mjcf_body':name,'body_id':model.body(name).id,'asset_id':a['asset_id'],
                            'category':None,'name_map':{},'pose_frame':'world','pose_order':'xyz_wxyz'})
        new=Simulation(model,self.sim.robot.config,catalog,self.config.target)
        try:
            new.source=self.sim.source; new.restoration=self.sim.restoration; new.spec=self.spec
            if hasattr(self.sim,"frozen_provenance"): new.frozen_provenance=self.sim.frozen_provenance
            migrate_state(self.sim,new)
        except Exception:
            new.close(); raise
        previous=self.sim; self.sim=new
        return previous

    def _pose(self, name, pose):
        """给某个实例设位姿。

        两条分支：
          - 自由物体（1 个 FREE joint）：直接写 qpos 并清零速度，很快。
          - 固定家具（0 个关节）：必须改 spec 并重新编译整个模型（较慢）。
        """
        b=self.sim.model.body(name).id
        count=int(self.sim.model.body_jntnum[b]); adr=int(self.sim.model.body_jntadr[b])
        if count==1 and self.sim.model.jnt_type[adr]==mujoco.mjtJoint.mjJNT_FREE:
            q=int(self.sim.model.jnt_qposadr[adr]); v=int(self.sim.model.jnt_dofadr[adr])
            self.sim.data.qpos[q:q+7]=pose; self.sim.data.qvel[v:v+6]=0
            if name not in self.placements: self.placements[name]=self.select_region(self.config.support,self.config.region_geom)
        elif count==0:
            self._ensure_spec()
            body=self.spec.body(name); body.pos=pose[:3]; body.quat=pose[3:]
            return self._rebuild()
        else: raise ValueError('articulated root pose/joint edits unsupported')
        mujoco.mj_forward(self.sim.model,self.sim.data)
        return None

    def transact(self, operations, *, revision, reason='rule_candidate', source='rule'):
        """★ 编辑事务：整个阶段二的心脏。

        固定套路（记住这 6 步）：
          1. 预算检查 + 证据可序列化检查
          2. snapshot() 存检查点，记录事务前的状态
          3. _precheck() 权限/合法性
          4. 逐个执行操作（可能触发 _rebuild）
          5. settle() 真实静置 → validate() 给结论
          6. 通过 → committed；不通过 → restore() 回滚
        无论成功失败，事务记录都会追加到 transactions/，失败也保留证据。
        """
        self.active()
        if source not in ('rule','model'): raise ValueError('invalid decision source')
        if not isinstance(operations,list): raise ValueError('invalid operations')
        if self.edits_used+len(operations)>self.config.budget.edits: raise RuntimeError('build_edit_budget_exhausted')
        self.edits_used+=len(operations)
        # Rejected NaN/malformed requests must still leave serializable failure evidence.
        from ..io import canonical
        try: canonical(operations)
        except (ValueError,TypeError) as exc:
            entry={'transaction':len(self.transactions),'status':'rejected','source':source,'revision_before':self.revision,
                   'operations_repr':repr(operations),'error_type':type(exc).__name__,'error':'non_json_or_nonfinite_operation'}
            self.transactions.append(entry)
            if self.path: write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
            return entry
        checkpoint=self.snapshot(); before_id=self.checkpoint_id
        entry={'transaction':len(self.transactions),'revision_before':self.revision,'reason':reason,'source':source,
               'operations':operations,'checkpoint_before':before_id,'status':'rejected','state_before':self.sim.observe_state()}
        intermediates=[]
        try:
            self._precheck(operations,revision)
            affected={o['instance'] for o in operations}
            # 基线 = 所有“没被本次编辑碰到”的物体；用来检测编辑是否意外影响了别的物体
            baseline={n:p for n,p in poses(self.sim).items() if n not in affected}
            for o in operations:
                name=o['instance']; op=o['op']
                if op=='delete':
                    self._ensure_spec()
                    self.spec.delete(self.spec.body(name)); self.placements.pop(name,None); self.extra_instances.pop(name,None)
                    intermediates.append(self._rebuild())
                elif op=='add':
                    # 从资产池加载并挂到世界；新物体的位姿由 _pose 显式设置
                    asset,a=self.pool.load(o['asset_id']); prefix=name+'/'
                    self.spec.attach(asset,prefix=prefix,frame=self.spec.worldbody.add_frame())
                    self.spec.body(prefix+a['root_body']).name=name
                    self.extra_instances[name]=a
                    intermediates.append(self._rebuild())
                    previous=self._pose(name,o['pose'])
                    if previous: intermediates.append(previous)
                    self.placements[name]=self.select_region(self.config.support,self.config.region_geom)
                else:
                    previous=self._pose(name,o['pose'])
                    if previous: intermediates.append(previous)
            self.revision+=1; self.last_check=None
            entry['state_after_unsettled']=self.sim.observe_state()
            # 编辑后、静置前也留一张观察（用于诊断“刚放下时是什么样”）
            if self.path:
                from ..agents.protocol import observe
                observe(self,[],self.path/'transaction_observations'/f'{entry["transaction"]:04d}-unsettled',stage='after_edit_unsettled',images=self.record_images)
            self.settle(); check=self.validate(baseline)
            entry['state_after_settle']=self.sim.observe_state()
            if self.path:
                from ..agents.protocol import observe
                observe(self,[],self.path/'transaction_observations'/f'{entry["transaction"]:04d}-settled',images=self.record_images)
            entry['check']=check
            if not check['valid']: raise ValueError('post-edit physical/build requirements failed')
            entry.update(status='committed',revision_after=self.revision,checkpoint_after=self.checkpoint_id)
            self._undo.append(checkpoint)     # 提交成功才记入可撤销栈
        except Exception as exc:
            # 任何失败都回滚到事务前，并把失败原因写进事务记录（证据不丢）
            entry.update(status='rolled_back',error_type=type(exc).__name__,error=str(exc))
            self.restore(checkpoint)
            entry['revision_after']=self.revision; entry['checkpoint_restored']=self.checkpoint_id
        finally:
            # 关闭中间产生的临时 sim 实例（既不是检查点也不是当前实例的那些）
            for previous in intermediates:
                if previous is not checkpoint['sim'] and previous is not self.sim: previous.close()
            self.transactions.append(entry)
            if self.path:
                write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
        return entry

    def undo(self):
        """显式撤销上一次已提交事务。"""
        self.active()
        if not self._undo: raise ValueError('nothing to undo')
        before=self.checkpoint_id; self.restore(self._undo.pop())
        entry={'transaction':len(self.transactions),'status':'undone','source':'rule','checkpoint_before':before,'checkpoint_after':self.checkpoint_id,'revision_after':self.revision}
        self.transactions.append(entry)
        if self.path: write_json(self.path/'transactions'/f'{entry["transaction"]:04d}.json',entry)
        return entry

    def freeze(self, path, *, build_id):
        """冻结为 v2 场景版本。会先**重新完整校验一次**，不通过不许冻结。"""
        self.active()
        check=self.validate()
        if not check['valid']: raise ValueError('freeze requires fresh valid physics and all required checks')
        if not hasattr(self.sim,'frozen_provenance') and self.sim.source.provenance()!=self.source_digest: raise ValueError('source changed during build')
        path=Path(path)
        if path.exists(): raise FileExistsError(path)
        model_id=self.model_id; checkpoint_id=self.checkpoint_id
        version=self.sim.freeze(path)          # 先写 v1 那套（mjb/npz/version/instances/checksums）
        version.pop('version_id')
        frozen_dependencies=list(self.dependencies)
        for asset in self.extra_instances.values():
            frozen_dependencies.extend(dependencies((self.pool.path.parent/asset['xml_path']).resolve()))
        # v2 额外绑定：模型身份、检查点身份、构建 ID、实例映射身份、依赖清单与构建协议
        version.update(schema_version='2.0',model_id=model_id,checkpoint_id=checkpoint_id,build_id=build_id,
                       mapping_id=digest(self.sim.catalog), dependencies=frozen_dependencies,
                       build_protocol=asdict(self.config.protocol))
        version['version_id']=digest(version)
        write_json(path/'version.json',version)
        write_json(path/'checksums.json',{n:file_digest(path/n) for n in ('model.mjb','initial.npz','version.json','instances.json')})
        self.close()
        return version

    def close(self):
        """关闭当前仿真与所有保留的检查点仿真。"""
        if not self.closed:
            self.sim.close()
            for cp in self._undo: cp['sim'].close()
            self.closed=True
