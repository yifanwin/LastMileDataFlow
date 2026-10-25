# Case3 接触过滤与抓取点修复方案

- 状态：active（S1–S3 已实现，G1 通过；G2/G3 待有可用 GPU 时执行）
- 日期：2026-10-25
- 替代：无
- 关联：[假失败核查](../analyze/2026-10-25-case3-false-collision-check.md)、[规划失败原因分布](../analyze/2026-10-25-planning-failure-analysis.md)、[执行配置](../../../configs/execution/panda_omron_all_cases_curobo.json)

针对核查结论的两处缺陷：**掌部 `panda_gripper` 与把手重叠被计为硬碰撞**，以及**抓取点落在把手 bbox 中心、且 `approach_offset_m` 朝里推**。目标是把 Case3（Fridge/Safe/SideTable/Microwave）从"规划必然无解"恢复到"规划可行"，再由真实 rollout 判定任务成功率。

## 1. 结论先行：两步都必须做

| 步骤 | 解决什么 | 能否单独生效 |
|---|---|---|
| S1 抓取点移到把手前表面附近 | 手指够得到把手，掌部侵入从 2–5 cm 降到约 2 cm | 不能。掌面固定在 TCP 后方 3.1 cm，把手深 6–9.5 cm，掌部总会压进把手 |
| S2 target 拆成门体/把手，掌部只对把手放宽 | 残余 2 cm 掌部接触不再判为不可行 | 不能。不先做 S1，掌部会插进把手 2–5 cm，规划放行后物理执行会顶住把手 |

S1 单独做仍会失败（掌部还是碰），S2 单独做会把"掌部插进把手 2–5 cm"变成合法，两者叠加才有意义。

## 2. S1：抓取点从 bbox 中心移到前表面附近

`scenes/tasks/discovery.py:grasp_candidates` 的 pinch 分支（当前 L165–180）把 `pose[:3,3]` 设成 `(lo+hi)/2`。改为沿候选接近轴退到把手前表面，再向内 `inset`：

```python
lo, hi = np.asarray(task.get('handle_local_bounds', task.get('local_bounds')))
center = (lo + hi) / 2
width_axis = int(np.argmin(hi - lo))
size = hi - lo
inset = 0.010 if task['operation'] == 'open' else 0.0   # 只改 open，pick 保持现状
result = []
for axis in range(3):
    if axis == width_axis:
        continue
    for sign in (-1, 1):
        y = np.eye(3)[width_axis]; z = sign * np.eye(3)[axis]; x = np.cross(y, z)
        # 把手沿该候选接近轴的厚度；靠近机器人一侧的表面再往里 inset
        depth = float(np.abs(z) @ size)
        point = center - z * (depth / 2 - inset)
        for flip in (1, -1):
            pose = np.eye(4)
            pose[:3, :3] = np.column_stack((flip * x, flip * y, z))
            pose[:3, 3] = point
            result.append({...})
```

同时把 `runtime/execution/manipulation.py:grasp_goals` 的偏移方向按任务区分（当前 `+=` 是往物体里推 1 cm，与目标相反）：

```python
def grasp_goals(sim, config, pool, *, retreat=False):
    ...
    sign = -1. if retreat else 1.
    for goal in goals:
        goal[:3, 3] += sign * config.approach_offset_m * goal[:3, 2]
        goal[:3, :3] = goal[:3, :3] @ roll
    return goals
```

两个调用点同步：

- `runtime/execution/manipulation.py:86` → `grasp_goals(sim, ctx.config, pool, retreat=task['operation'] == 'open')`
- `planning/pool.py:71` → `grasp_goals(sim, config, pool, retreat=task['operation'] == 'open')`

净效果：`inset=0.010` 减掉 `approach_offset=0.010` 的回退，TCP 落在把手前表面，手指（TCP 后方 3.1 cm 到 TCP）咬住把手前 3 cm，掌部落在把手内约 2 cm——交给 S2 处理。

## 3. S2：target 世界拆成门体与把手，掌部只对把手放宽

### 3.1 让 planner 知道把手是哪个 body

`runtime/simulation.py`：

```python
self.handle_id = None          # import 时新增
```

`runtime/execution/context.py:clone_sim` 增加参数并在构造后赋值：

```python
def clone_sim(sim, *, target=None, handle=None):
    ...
    result.handle_id = (sim.model.body(handle).id if handle else getattr(sim, 'handle_id', None))
```

在三个有 `task` 的执行入口传入：

- `runtime/execution/curobo.py:25`：`clone_sim(baseline, target=task['target_body'], handle=task.get('handle_body'))`
- `planning/pool.py:54`：同上
- `runtime/execution/vla.py:42`：同上

planner 侧统一用 `getattr(sim, 'handle_id', None)`，取不到就退回今天的行为（不建把手检查器），其它入口不需要改。

### 3.2 导出两份 target 世界

`planning/collision_world_v2.py` 增加可选的 body 集合与文件名：

```python
def collision_world(sim, path, range_m=2., *, reference_base=None, target_only=False,
                    bodies=None, filename=None):
    ...
    target = descendants(m, sim.target_id) if bodies is None else bodies
    name_json = filename or ('target_world.json' if target_only else 'collision_world.json')
```

### 3.3 planner 建两个检查器

`planning/curobo_v2.py` 的 `__init__`（当前 L190–196）：

```python
target_bodies = descendants(sim.model, sim.target_id)
handle_bodies = descendants(sim.model, sim.handle_id) if getattr(sim, 'handle_id', None) else set()
target = collision_world(sim, path, self.world_range, reference_base=self.base,
                         target_only=True, bodies=target_bodies - handle_bodies)
handle = (collision_world(sim, path, self.world_range, reference_base=self.base,
                          target_only=True, bodies=handle_bodies, filename='handle_world.json')
          if handle_bodies else SceneCfg())
self.context = {
    'target_checker': SceneCollision.from_config(SceneCollisionCfg(
        device_cfg=self.args, scene_model=target, cache={'mesh': 2})),
    'handle_checker': (SceneCollision.from_config(SceneCollisionCfg(
        device_cfg=self.args, scene_model=handle, cache={'mesh': 2})) if handle_bodies else None),
    ...}
```

`refresh_world`（L651–661）同样加载两份；`planner_identity.json` 里加 `handle_contact_links` 与 `handle_world_geoms`，便于事后核对。

### 3.4 成本管理器加第二个 target cost

`planning/panda_omron.py:73` 增加掌部白名单：

```python
contact_links = ["panda_finger1", "panda_finger2"]
handle_contact_links = ["panda_finger1", "panda_finger2", "panda_gripper"]
```

`planning/curobo_v2_costs.py` 的 `ContactManager.initialize_from_config` 建第二个 cost，`compute_costs` 里用同一套 `contact_allowed` 相位开关：

```python
if config.scene_collision_cfg is not None and context.get('handle_checker') is not None:
    cfg = copy.copy(config.scene_collision_cfg)
    cfg.scene_collision_checker = context['handle_checker']
    self.handle_cost = SceneCollisionCost(cfg)
    self.handle_indices = [i for link in context['handle_contact_links']
                           for i in params.get_sphere_index_from_link_name(link).tolist()]
...
if self.handle_cost is not None and not context['attached']:
    kin = copy.copy(state.cuda_robot_model_state)
    kin.robot_spheres = contact_spheres(state.robot_spheres, self.handle_indices, context['contact_allowed'])
    result.add(self.handle_cost.forward(kin, idxs, trajectory_dt=state.joint_state.dt), 'handle_contact')
```

要点：

- 门体检查器沿用现在的 `contact_links`（只有手指），掌部对门板仍然是硬碰撞——这是本次不改的底线。
- 把手检查器在 `goalset/grasp/approach_contact` 相位屏蔽手指与掌部；`pregrasp` 相位不屏蔽，8 cm 外的预抓取仍不允许碰把手。
- 需要在实际环境确认 0.8.0 的 `SceneCollisionCost` 能接受同一 `scene_collision_cfg` 的拷贝并绑定不同 checker；若不行，退回 `RobotCostManager` 增加第二个 `scene_collision_cfg` 字段。

## 4. 参数取值依据

| 参数 | 取值 | 依据 |
|---|---|---|
| `inset` | 0.010 m | 手指可用夹持带只有 3 cm（掌面 −3.1 cm 到指尖 −0.001 cm）；TCP 进把手 1 cm 时咬合约 2 cm，同时掌部侵入从 2–5 cm 降到约 2 cm |
| `approach_offset_m`（open） | 保持 0.010，方向改为回退 | 与 inset 抵消，TCP 落在前表面上；抽样上每回退 1 cm 掌部余量增加约 1 cm |
| 掌部屏蔽范围 | 只对把手，且只在 goalset/grasp 相位 | 预抓取仍需防碰；门板不能放宽 |

## 5. 验证门

**G1 几何复核（无 GPU，约 5 分钟）**：把核查脚本 `reach_min.py` 的抓取点换成 S1 的新构造，跑 410 个 Fridge 站位。通过条件：

- 掌部∩门板 ≥ +1 cm 的站位 ≥ 90%（当前 48%）；
- link7∩门板 ≥ +1 cm 的站位 ≥ 95%；
- 残余掌部∩把手 ≤ 2.5 cm（只是接触，不是穿模）。

**G2 规划反事实（GPU，只跑规划）**：用现有 val100 数据集对 Fridge 场景重跑 `evaluate-stations`，`--max-trials 1`，只看 `planning_no_solution` 比例。通过条件：Fridge 规划成功从 0% 升到 ≥ 70%。若仍低，看 `solver_trace` 里是哪个约束项还在拦（预期是环境或工作圆，而不是 target）。

**G3 真实 rollout（GPU）**：抽 10 个 Fridge 站位跑 `tests/smoke/curobo_rollout.py`（或 `collect-lastmile` 的小样本）。除成功率外必须核对两件事：

- 夹爪闭合前门板 `qpos` 未被推动（读 `physics.jsonl` 的门关节）；
- 抓取后 `open` 判定是真实开门，不是靠顶门板过关。

**G4 pick 回归**：S1 只改 open 分支、S2 的把手检查器只在有 `handle_body` 时建立，Case1/2 的规划路径应当逐位不变。抽 20 个 pick trial 对比 `solver_trace` 的成功模式即可，不必全跑。

## 6. 风险与回滚

| 风险 | 说明 | 处理 |
|---|---|---|
| 规划放行、物理顶住 | 掌部仍侵入把手约 2 cm，MuJoCo 会把接触力传给门 | G3 专门核对门板 qpos；若被推动，把 inset 调大或把掌部屏蔽改成只屏蔽掌部前 3 个球 |
| 掌部可穿把手 | 屏蔽是二值的，理论上掌部能整块穿过把手 | 把手几何小、接近路径短；G1 的"残余 ≤ 2.5 cm"就是在守这条底线 |
| 第二个 target cost 增加开销 | 每次 rollout 多一次 SDF 查询与一次球体克隆 | 只在存在 `handle_body` 时建立；监控 planner 初始化与单次 IK 时间的回归 |
| 姿态轴问题不存在 | 已核对 `T_tcp_gripper = Trans(0,0,−0.097)`，`grasp_roll_rad=π/2` 已把闭合轴对齐把手窄轴 | 不需要改 roll |
| RBY-1 未覆盖 | 另一模型的掌部链节名不同 | 本次只做 Panda；`handle_contact_links` 在 rby1m 上暂不定义，planner 侧用 `getattr` 退回单检查器 |

回滚：改动集中在 5 个文件（`discovery.py`、`manipulation.py`、`collision_world_v2.py`、`curobo_v2.py`、`curobo_v2_costs.py`）加 3 处 `clone_sim` 调用与 2 行 sim 字段。`handle_id` 缺失时 S2 自动退回今天的行为，S1 可以单独按任务开关（`inset=0`、`retreat=False` 即回到现状）。

## 7. 本方案不解决的部分

| 现象 | 原因 | 归属 |
|---|---|---|
| Dresser 796 站位、Desk 22 站位全部位姿不可达 | 把手高度 0.19–0.60 m，低于 TCP 最低可达高度 0.54–0.62 m | 底盘高度/工作空间问题，另开 |
| Safe 9 个、SideTable 部分站位只有竖直或朝门内接近 | 站位与家具朝向组合 | 站位采样问题，另开 |
| Case1/2 的 goalset TrajOpt 失败（1,484 条） | 与本次接触过滤无关 | 见[原因分布](../analyze/2026-10-25-planning-failure-analysis.md) |

修完之后，Case3 的成功率仍然要靠 G3 的真实 rollout 来定，不能用本次几何复算或规划可行性替代任务成功率。

## 8. 实施记录（2026-10-25）

已实现 S1、S2，并追加了 S3。

**S1 抓取点**：`scenes/tasks/discovery.py:grasp_candidates` 新增 `pinch_inset_m=0.020`，open 分支把 pinch 点放在把手靠机器人一侧表面再向内 2 cm；`runtime/execution/manipulation.py:grasp_goals` 新增 `retreat=`，open 时沿接近轴回退 `approach_offset_m`（1 cm），净深度 1 cm。pick 走原路径不变（`retreat=False`、inset 不生效）。

**S2 接触策略**：`sim.handle_id` 由 `clone_sim(..., handle=task.get('handle_body'))` 注入（`runtime/execution/curobo.py`、`planning/pool.py`、`runtime/execution/vla.py` 三处）；`collision_world_v2.collision_world` 支持 `bodies=`/`filename=`；`V2Planner` 建 `target_checker`（门体）与 `handle_checker`（把手）两个检查器，`refresh_world`、`pad_target_query_range`、`planner_identity.json` 同步；`curobo_v2_costs.ContactManager` 增加第二个 `SceneCollisionCost`，键名 `handle_contact`，屏蔽集合为 `panda_omron.PlanningModel.handle_contact_links`（双手指 + `panda_gripper`）。

**S3 掌部球体（新增，原方案只列为风险）**：复核发现掌部的 5 个球（x=±0.08/±0.04/0，r=0.046）横向覆盖到 ±0.126 m，而夹爪手板只跨 ±0.04 的手指行程、整手约 8–10 cm 宽。在 40 个 Fridge 站位上，40/40 的掌部碰撞由最外侧两个球（x=±0.08）触发，中心球在 35/40 个站位是净空的。因此把 `panda_omron/spheres.yml` 的 `panda_gripper` 收敛为 3 个球（x=±0.04/0，r=0.04，覆盖 ±0.08）。

**G1 结果**（全部 639 个 Case3 可达站位，按第 5 节判据、取 8 个候选中最好的一个）：

| 类别 | 站位 | 掌部余量 ≥ +1 cm | 掌部余量 > 0 | link7 ≥ +1 cm | 手指∩把手 中位 |
|---|---:|---:|---:|---:|---:|
| Fridge | 410 | 261（64%） | 330 | 354 | −0.021 |
| Safe | 109 | 109（100%） | 109 | 109 | −0.013 |
| SideTable | 100 | 80（80%） | 100 | 100 | −0.009 |
| Microwave | 20 | 20（100%） | 20 | 20 | −0.022 |

修复前同一判据是 0/639（第 3 节），即没有任何站位存在可行候选。

**单元测试**：`.venv/bin/python -m unittest discover -s tests -t .` 跑 276 项全过（2 项跳过），覆盖 scenes/runtime/planning/workflows。

**G2/G3 未执行**：本机 `torch 2.7.1+cu126` 的架构列表止于 sm_90，而 GPU 是 RTX PRO 5000 Blackwell（sm_120），`cuRobo` 一建 `SceneCollision` 就报 `no kernel image is available for execution on the device`。需要装 cu128 及以上的 torch 才能在迁移后的机器上跑真实规划与 rollout。这不是本次改动的问题，属于环境前置条件。

**仍需确认**：
- 掌部球体收敛改变了所有任务的夹爪本体碰撞，pick 侧没有做数值回归（G4），需要可用 GPU 后抽 20 条 pick trial 对比 `solver_trace`；
- Fridge 仍有 36% 站位在最优候选下掌部余量不足 1 cm，主要来自把手贴门板太近与候选接近方向，需要在 G2 里看实际失败率再决定是否继续调 `pinch_inset_m`。
