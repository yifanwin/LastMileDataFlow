"""Strict force-aware pick evidence. Every physics tick, no agent override.

【这个文件决定“抓取算不算成功”】协议 strict-pick-v3 是硬编码常量，**配置无法放宽**。
定义见下方 PROTOCOL：双指真实法向力、抬升高度、无支撑保持时长、相对滑移、底盘/头/闲置臂漂移、
躯干联动误差、采样间隔上限、接触力下限。

【两种层次，别混淆】
  PickMonitor.sample()      每个物理 tick 采一行“事实”（力、位姿、接触日志）。
  constraint_failure()      逐行判违约——任何一项违规立刻给 failure 并记录首个失败阶段。
  evaluate_pick()           汇总全部样本，给出 success/failure/infrastructure_error。

【为什么要有 infrastructure_error 这一档】
  如果证据本身缺失、时间戳断裂、四元数不合法，那不是“机器人抓失败了”，
  而是“我们没有可信证据”。这种情况必须单独归类，绝不能写成 failure——
  否则会把数据采集故障误当成物理结论。
"""
import mujoco
import numpy as np
from ...scenes.geometry import body_pose, tcp_pose
from ...scenes.geometry import descendants
from .contacts import CONTACT_POLICY, SIMULATION_FAILURES, ContactSampler

# 任务成功阈值不可通过配置放宽。
PROTOCOL={'version':'strict-pick-v3','lift_m':.05,'hold_s':2.,'base_translation_m':.002,
          'base_yaw_rad':np.deg2rad(.2),'lift_hold_error_m':.002,'torso_error_rad':.002,'head_error_rad':.002,
          'idle_arm_error_rad':.002,'relative_translation_m':.01,
          'relative_rotation_rad':np.deg2rad(5),'max_sample_gap_s':.0041,
          'finger_force_min_n':1e-6, 'contact_policy':CONTACT_POLICY}


def rotation_error(a,b): return float(np.arccos(np.clip((np.trace(a.T@b)-1)/2,-1,1)))


class PickMonitor:
    """逐物理 tick 的事实采样器 + 接触记录器。

    初始化时锁定“基线”：底盘/头/闲置臂的初值、目标高度、两个指尖 body 的 id。
    之后每次 sample() 都相对基线判断，所以任何漂移都会被记录。
    """

    def __init__(self,sim,side, *, allowed_target_fingers=()):
        self.sim,self.side=sim,side
        # 机器人自身 body 集合 vs 目标（含其子部件）集合，用来区分“指-目标接触”和“环境接触”。
        self.robot_bodies={b for b in range(sim.model.nbody) if sim.model.body(b).name.startswith(sim.robot.config.namespace)}
        self.target_bodies=descendants(sim.model,sim.target_id)
        groups = sim.robot.finger_bodies(side)
        self.fingers = {min(group) for group in groups}
        self.finger_index = {body: min(group) for group in groups for body in group}
        self.allowed_target_fingers = set(self.finger_index) | set(allowed_target_fingers)
        self.initial = {"base": sim.robot.base_pose().tolist(),
                        **sim.robot.holding_state(side),
                        "target": sim.model.body(sim.target_id).name,
                        "height_m": float(sim.data.xpos[sim.target_id, 2]),
                        "fingers": sorted(self.fingers)}
        self.phase='torso_adjust'; self.command_h=0.
        self.contact_sampler = ContactSampler(sim)

    def sample(self, contacts=None):
        """采集一行物理事实。包含：力、支撑、接触标记列表、相对位姿（TCP←目标）。"""
        sim=self.sim; m,d=sim.model,sim.data; forces={}; support=False; illegal=[]; contact_flags=[]
        if not all(np.isfinite(x).all() for x in (d.qpos,d.qvel,d.ctrl,d.qacc)): illegal.append('nonfinite_state')
        if any(w.number for w in d.warning): illegal.append('mujoco_warning')
        if contacts is None:
            contacts = self.contact_sampler.capture()
        for _,pair,force,active,_,_ in contacts.contacts:
            a,b=pair.bodies
            bearing=bool(active and force>PROTOCOL['finger_force_min_n'])   # 是否真的承力
            ta,tb=a in self.target_bodies,b in self.target_bodies
            ra,rb=a in self.robot_bodies,b in self.robot_bodies
            if ta!=tb:
                # 目标 vs 其它：可能是指尖抓握、桌面支撑、或手指以外的机器人部位非法压目标
                other=b if ta else a
                if other in self.finger_index and bearing:
                    finger = self.finger_index[other]
                    forces[finger] = forces.get(finger, 0.) + force
                elif other not in self.robot_bodies and bearing: support=True      # 目标仍被桌面支撑
                elif other in self.robot_bodies and other not in self.allowed_target_fingers: contact_flags.append('nonfinger_target')
            elif ra and rb: contact_flags.append('self_collision')
            elif ra!=rb:
                # 机器人 vs 环境：豁免“底盘/轮 vs 地板”的正常支撑接触
                floor='floor' in pair.geom_names[1 if ra else 0].lower()
                name=pair.body_names[0 if ra else 1]
                base='base' in name or 'wheel' in name
                if not (floor and base): contact_flags.append('robot_environment_collision')
        target=body_pose(sim,sim.target_id); tcp=tcp_pose(sim,self.side)
        # relative_pose = TCP^-1 · 目标，即“目标相对手爪”的位姿；滑移检测就看它是否稳定。
        extra = {'command_h': float(self.command_h)} if 'torso' in self.initial else {}
        return {'time_s':float(d.time),'phase':self.phase, **extra,
                'base':sim.robot.base_pose().tolist(), **sim.robot.holding_state(self.side),
                'target':m.body(sim.target_id).name,'height_m':float(d.xpos[sim.target_id,2]),
                'support_contact':support,'finger_forces_n':{str(k):v for k,v in forces.items()},
                'relative_pose':np.linalg.solve(tcp,target).tolist(),'illegal':sorted(set(illegal)),
                'contact_flags':sorted(set(contact_flags)), 'contact_policy':CONTACT_POLICY,
                'sampler':'force-aware-v3'}


def constraint_failure(row,initial, *, check_base=True,check_torso=True,check_idle=True):
    """逐行判违约，返回首个违约原因字符串，全部合规返回 None。

    检查顺序：目标没换 → 底盘漂移 → 头/闲置臂漂移 → 躯干联动 → 仿真健康。
    """
    health=next((issue for issue in row['illegal'] if issue in SIMULATION_FAILURES),None)
    if health: return health
    if row['target']!=initial['target']: return 'wrong_target'
    base=np.array(row['base']); delta=base-np.array(initial['base'])
    if check_base and (np.linalg.norm(delta[:2])>PROTOCOL['base_translation_m'] or abs(np.arctan2(np.sin(delta[2]),np.cos(delta[2])))>PROTOCOL['base_yaw_rad']): return 'base_drift'
    for key,tol in (('head','head_error_rad'),('idle_arm','idle_arm_error_rad')):
        if key not in initial or key == 'idle_arm' and not check_idle: continue
        if np.max(np.abs(np.array(row[key])-initial[key]))>PROTOCOL[tol]: return key+'_drift'
    if check_torso and 'torso' in row:
        torso=np.array(row['torso']); h=torso[1] if row['phase']=='torso_adjust' else row['command_h']
        if np.max(np.abs(torso-np.array([0,h,-2*h,h,0,0])))>PROTOCOL['torso_error_rad']: return 'torso_protocol'
    if check_torso and 'lift' in initial and abs(row['lift']-initial['lift'])>PROTOCOL['lift_hold_error_m']:
        return 'lift_drift'
    # Legacy contact labels remain readable, but no longer determine task failure.
    for issue in row['illegal']:
        if issue not in ('nonfinger_target','self_collision','robot_environment_collision',
                         'robot_collision','severe_penetration','minor_contact'):
            return issue
    return None


class PickEvidence:
    """逐行累加的 strict-pick 判定；结论与对完整样本列表一次性判定完全相同。

    长 episode 每次查询都重扫全部样本是平方开销；这里只处理新行，
    并按原判定的优先级（字段缺失 → 时间断裂 → 首个逐行结论 → 保持时长）给结论。
    """

    def __init__(self, initial, *, constraint_checker=constraint_failure):
        self.initial, self.constraint_checker = initial, constraint_checker
        self.required={'time_s','phase','base','target','height_m','support_contact','finger_forces_n','relative_pose','illegal','sampler'}
        self.required |= {'lift'} if 'lift' in initial else {'command_h','torso','head','idle_arm'}
        self.count=0; self.missing=False; self.gap=False; self.row_result=None
        self.last_time=None; self.last_phase=None
        self.start=None; self.reference=None; self.best=0.; self.max_lift=0.

    def extend(self, samples):
        for r in samples:
            self.add(r)
        return self

    def add(self, r):
        self.count+=1
        # 先做“证据完整性”检查：字段缺失或采样器版本不对 → 这是设施问题，不是抓取失败。
        if self.missing or not self.required<=set(r) or r['sampler']!='force-aware-v3':
            self.missing=True; return
        # 时间必须严格递增且采样间隔不过大，否则“保持 2 秒”无从谈起。
        t=r['time_s']
        if not np.isfinite(t) or self.last_time is not None and (t-self.last_time<=0 or t-self.last_time>PROTOCOL['max_sample_gap_s']):
            self.gap=True
        self.last_time=t; self.last_phase=r['phase']
        if self.row_result is None:
            try:
                self.row_result=self._row(r)
            except (TypeError,ValueError,KeyError):
                self.row_result={'status':'infrastructure_error','reason':'malformed_physics_evidence'}

    def _row(self, r):
        initial=self.initial
        numeric=np.concatenate([np.atleast_1d(r[key]) for key in ('base','torso','head','idle_arm','lift','height_m','command_h') if key in r])
        pose=np.asarray(r['relative_pose']); forces={int(k):float(v) for k,v in r['finger_forces_n'].items()}
        # 数值合法性：四元数/旋转矩阵/力的非有限值或非法值都算证据无效。
        if (not np.isfinite(numeric).all() or pose.shape!=(4,4) or not np.isfinite(pose).all() or
            not np.allclose(pose[3],[0,0,0,1]) or not np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-5) or not np.isclose(np.linalg.det(pose[:3,:3]),1) or
            any(not np.isfinite(v) or v<=PROTOCOL['finger_force_min_n'] for v in forces.values())):
            return {'status':'infrastructure_error','reason':'invalid_physics_evidence'}
        fail=self.constraint_checker(r,initial)
        # 任何一行违约 → 直接 failure，并记录首个违约来自哪个阶段/时刻（便于归因）。
        if fail: return {'status':'simulation_failure' if fail in ('nonfinite_state','mujoco_warning') else 'failure','reason':fail,'first_failure_phase':r['phase'],'first_failure_time_s':r['time_s']}
        lift=r['height_m']-initial['height_m']; self.max_lift=max(self.max_lift,lift)
        held=(set(forces)==set(initial['fingers']) and len(forces)==2 and lift>=PROTOCOL['lift_m'] and r['support_contact'] is False)
        if not held: self.start=None; self.reference=None; return None   # 没抓住的瞬间：清零保持计时
        if self.start is None: self.start=r['time_s']; self.reference=pose
        # 相对位姿漂移过大 = 滑了：重开保持计时窗口
        if np.linalg.norm(pose[:3,3]-self.reference[:3,3])>PROTOCOL['relative_translation_m'] or rotation_error(self.reference[:3,:3],pose[:3,:3])>PROTOCOL['relative_rotation_rad']:
            self.start=r['time_s']; self.reference=pose
        self.best=max(self.best,r['time_s']-self.start)
        return None

    def verdict(self):
        if not self.count or self.missing:
            return {'status':'infrastructure_error','reason':'missing_physics_evidence'}
        if self.count<2 or self.gap:
            return {'status':'infrastructure_error','reason':'physics_trace_gap'}
        if self.row_result is not None:
            return dict(self.row_result)
        tail=self.last_time-self.start if self.start is not None else 0.
        ok=tail>=PROTOCOL['hold_s']-1e-9
        return {'status':'success' if ok else 'failure','reason':'strict_pick' if ok else 'lift_contact_hold_or_slip',
                'first_failure_phase':None if ok else self.last_phase,'tail_held_s':float(tail),
                'max_held_s':float(self.best),'max_lift_m':float(self.max_lift),'physics_samples':self.count}


def evaluate_pick(samples,initial, *, constraint_checker=constraint_failure):
    """汇总全部物理样本，判定这次抓取尝试的最终结论。

    返回 status 为 success / failure / infrastructure_error 三者之一，并带诊断。
    判定成功需要**同时满足**：两个指尖都承力、目标抬升 ≥ 5cm、目标已不靠桌面支撑，
    且上述状态连续保持 ≥ 2s（允许期间因滑移重开计时窗口）。
    """
    return PickEvidence(initial,constraint_checker=constraint_checker).extend(samples).verdict()
