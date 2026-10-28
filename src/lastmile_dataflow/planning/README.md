# planning

cuRobo V2 规划适配、机器人模型、碰撞导出和导航工具。

## 主要模块

### cuRobo 适配（`curobo/`）

- **planner.py**：V2Planner 主类，cuRobo 0.8.0 适配
  - `PlannerResources`：GPU 进程保留至多 2 个兼容 solver 家族
  - `StationPlannerCache`：每侧臂至多 1 个预闭合 planner，限同一冻结站位
  - `V2Planner`：规划主类，grasp/diagnostic_ik/grasp_batch/plan 入口
- **context.py**：PlannerContext
- **costs.py**：ContactManager，目标/把手接触掩码，FOV 代价；导入即加载 cuRobo，仅由 planner 建 solver 时导入
- **tracing.py**：solver/phase 追踪
- **collision_world.py**：MuJoCo → cuRobo 碰撞世界导出，per-geom mesh；`exported_geoms()` 是导出与诊断共用的 geom 选择规则
- **head_fov.py**：head camera FOV 约束与验证，HeadFOVCost、SparseFOVValidator

### 机器人模型（`robots/`）

- **panda_omron.py**：PandaOmron V2 模型（URDF、spheres、FK、bounds）
- **rby1m_mobile_torso.py**：RBY-1 V2 模型 + 轨迹工具（ordered_points、retime_points、chain_state 等）
- **assets/**：机器人资产（URDF、spheres.yml）

### 导航（`navigation/`）

- **geometry.py**：几何导航（GeometryNavigation）
- **astar.py**：A* 搜索、占据栅格、距离场

### 规划工具

- **pool.py**：多进程规划服务（PlanningPool、PlanningService）
- **waypoints.py**：PlanResult、GRASP_APPROACH_DISTANCE_M 等规划结果定义
- **evidence.py**：solver 证据记录（solver_evidence、phase_evidence、ik_probe_evidence）
- **remote.py**：远程规划客户端（PlanningClient、RemotePlanner）

## 使用示例

### 单 planner

```python
from lastmile_dataflow.planning.curobo.planner import V2Planner

planner = V2Planner(sim, config, side='right', path=output_dir, 
                    workspace=(center, radius))
success, index, stages, diagnostics = planner.grasp(
    matrices, on_query=lambda phase: ..., lift=True)
```

### 多进程服务

```python
from lastmile_dataflow.planning.pool import PlanningPool

pool = PlanningPool(gpu_ids=[0, 1], workers_per_gpu=1)
pool.submit(request)
result = pool.poll(timeout=1.0)
```

### 导航

```python
from lastmile_dataflow.planning.navigation.geometry import GeometryNavigation

nav = GeometryNavigation(sim, config, clearance_weight=2.0)
result = nav.plan(start_xyyaw, goal_xyyaw)
```

## 测试

```bash
# planning 模块全部测试
.venv/bin/python -m unittest discover -s tests/planning -t . -v

# 单个模块
.venv/bin/python -m unittest tests.planning.test_curobo -v
```

真实 GPU smoke 测试位于 `tests/smoke/`：
- `curobo_rollout.py`：cuRobo 完整 rollout
- `head_fov.py`：FOV 约束验证
- `multitask.py`：MolmoBot multitask 推理
- `pipeline.py`：端到端 pipeline

## 依赖

- **cuRobo 0.8.0**（可选，V2Planner 需要）
- **MuJoCo**
- **PyTorch**（cuRobo 后端）
- **trimesh**（sphere fitting）

## 设计原则

### V2 pinned to 0.8.0

不维护多版本 cuRobo 兼容。当前实现固定 cuRobo 0.8.0 API。

### 机器人模型统一接口

机器人模型提供：
- URDF 路径和配置字典
- 碰撞球定义（spheres.yml）
- FK 验证与关节限位
- 规划/控制模式适配

planner 统一调用 `robot.planning_model(sim, config, side, path, workspace)`。

### 碰撞导出 per-geom

- 不使用家具级 AABB 作为精确几何
- 导出 MuJoCo 每个 geom 的碰撞形状：primitive 网格化，mesh geom 取其凸包（与 MuJoCo 物理一致）
- 每个导出网格都是封闭的：cuRobo 0.8.0 用 Warp 射线奇偶判断内外，只对封闭网格可靠；把不封闭的原始 mesh 合并成整屋网格时，自由空间会被判成障碍内部，且随坐标系变化
- 凸包重叠的区域内，奇偶会把点判为外部；机器人从外部接近时先触及外表面
- 目标单独导出为 target-only checker，允许手指接触
- Open 任务的 handle 单独导出为 handle-only checker

### 规划无解 ≠ 执行失败

- 规划无解：IK/TrajOpt 在当前预算下未找到解
- 执行失败：规划成功但物理执行未达成任务目标
- 两者分开记录，不混淆

### 任务绑定与复用

- `PlannerResources`：GPU 进程保留至多 2 个兼容家族，跨任务复用 solver
- `StationPlannerCache`：同站位预闭合阶段复用 planner，frozen state 验证
- 跨任务复用时必须重新计算当前任务的 `target_bodies`、`handle_bodies`
- Pick/Open 通过 key 区分，ContactManager 不串用

## 已知限制

- planner 缓存容量为 2（`PlannerResources`）
- 携带物拟合按 `(model, target_id)` 联合判断，不处理目标关节形变
- 底盘/柱体碰撞球为手工拟合，可能存在误拒绝
- buffer/activation 参数硬编码，未外部化配置
- FOV 约束仅支持 head camera，不支持 eye-in-hand

## 相关文档

- [docs/运行手册.md](../../docs/运行手册.md)：构建、标定、连续采集
- [docs/architecture.md](../../docs/architecture.md)：源码职责与调用链
- [tests/README.md](../../tests/README.md)：测试分工与验证范围
