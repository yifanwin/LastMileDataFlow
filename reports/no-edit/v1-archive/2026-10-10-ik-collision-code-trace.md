# pick 规划失败的代码追溯与对照诊断

日期：2026-10-10，Asia/Shanghai。分支：`feat/no-edit-lastmile-val`；基础提交：`9280253`。

## 结论与执行范围

**当前 pick 没有调用 cuRobo 的 `plan_grasp`。更重要的是，S0042 已确认存在 warmup 残留的额外 link 世界姿态目标，使名义放开的底盘受到不应有的姿态代价约束。** 仅在独立诊断中消除这些目标，保留全部关节锁定和环境/自碰撞检查，原来失败的接近段规划成功。

另两类问题也找到可复现的具体例子：Potato_17 的唯一抓法始终绑定躯干 `h=0`，漏掉有 IK 解的其他高度；AlarmClock_12 被低于 0.8m 的规则强制选 `h=.738`，在 9 个原本合法站位引入诊断姿态碰撞，9×5 正好对应 **45 次**未执行失败。

本次执行了源码追溯、结果快照读取、独立姿态检查和真实 GPU 规划对照。**没有修改运行源码、旧产物、物理阈值或正在运行的队列；没有执行新的闭爪、抬起或完整连续轨迹。** 规划成功不等于抓取成功，也不能宣称已修好全部 243/164 次。

- [最小对照证据及校验和](data/ik-root-cause-ablation-20261010.json)
- [源码注释目录](annotated-src/README.md)：五份仅加注释的镜像，AST 与运行源码一致。
- [前一份失败统计](2026-10-10-failure-causes-comparison.md)：本报告是进一步机制诊断，不覆盖其历史快照。

## 1. 当前流程确实是手工分段，不是 grasp planning

实际调用入口是 [`NativePlanner.plan`](../../src/lastmile_dataflow/planning/curobo.py)，原始行 214–226：

```python
result = self.motion.plan_single(state, goal_pose, self.options)
```

[`manipulate`](../../src/lastmile_dataflow/runtime/no_edit_execution.py)，原始行 269–298，逐段规划并执行：

| 阶段 | 当前实现 | 是否由 `plan_grasp` 统一返回 |
|---|---|---|
| 候选筛选 | dry 只检查预抓取可规划，首个成功即返回 | 否 |
| 预抓取 | TCP Z 方向后退 8cm，`ctx.follow` → `plan_single` | 否 |
| 接近 | 抓取姿态再前进默认 1cm；从实际状态重新规划，距预抓取 9cm | 否 |
| 闭爪 | 20 次真实控制步，不是规划器动作 | 否 |
| 抬起 | 世界 Z 抬升 6cm；附加物体碰撞球，再规划抬升 4cm | 否 |
| 保持 | 50 次真实控制步，独立验收接触、物体位移等 | 否 |

因此，**预抓取可行不保证接近/抬起可行**。`approach` 返回无解就结束该试验，视频中的机械臂停在预抓取位置。站位的 5 次是独立试验，不是这段动作的 5 次在线重试。

### 示例版本与真正导入版本不同，但旧版也有该 API

用户引用的本地 [`motion_planning.py`](../../../../../curobo/curobo/examples/getting_started/motion_planning.py)，行 275–301，使用现代接口 `MotionPlanner.plan_grasp(current_state=..., grasp_poses=GoalToolPose(...))`。其实现是 `curobo/curobo/_src/motion/motion_planner.py:420`。

真正运行的 cuRobo 来自：

```text
/data0/wenyifan/.cache/uv/git-v0/checkouts/6211baea487f99a9/87e857d/src/curobo/
```

该版本没有 `curobo.motion_planner`，但 **`wrap/reacher/motion_gen.py:4199` 已实现 `MotionGen.plan_grasp`**，不必仅为使用 grasp planning 就升级整套依赖。其调用形式是：

```python
# 接口说明，不是本工程已实施的调用。
result = motion.plan_grasp(
    start_state=state,
    grasp_poses=goalset_pose,       # 旧版 Pose，候选形状 (1, N, 7)
    plan_config=options,
    grasp_approach_offset=offset_pose,
    retract_offset=lift_offset_pose,
    retract_constraint_in_goal_frame=False,  # 需要世界/规划基准 Z 抬升时
    disable_collision_links=[],
)
```

它先用 `plan_goalset` 选择抓法，再分别调用 `plan_single` 规划预抓取、最终接近和撤离，最后拼接返回。**一次 API 调用不等于三段联合全局优化**，也不会自动闭爪、真实附着物体或证明夹住。也不能假定只更换 API 就会正确处理下文的 warmup 缓存，必须单独回归验证。

碰撞禁用也不是“所有阶段默认关闭手指”：旧版 `disable_collision_links` 默认空列表；若指定，目标集选解及接近抓取/撤离阶段禁用所列 link 的世界碰撞，规划预抓取时恢复。现代版默认从 `grasp_contact_link_names` 读取。不能将这类世界碰撞禁用当作仅允许“手指接触目标”的精确规则；本工程仍须逐物理步拒绝非允许接触。

## 2. free IK 为假：没有充分覆盖躯干配置

243 是之前 465 次统计快照中的终点标签。其准确含义是**最后保存的候选及有限种子预算下，无环境/自碰撞的 IK 没找到解**，不是整个物体的不可达证明。

具体代码：`choose_control` 原始行 242–245。

```python
for index, candidate in enumerate(pool):
    h = cfg.torso_heights[index % len(cfg.torso_heights)]
```

程序没有遍历“抓法 × 躯干高度 × 左右臂”。Potato_17 只有一个资产抓法，候选索引永远为 0，物体中心 z≈0.984m 又不触发低目标规则，始终选 `h=0`。重复 5 次仍不增加高度覆盖。有效资产抓法直接返回，也不会追加 bbox 备选（`tasks/raw_scene.py`，注释 G01）。

对 val_0 的 S0015 失败目标保持同一个世界目标姿态，独立执行 128 种子 free IK：

| 躯干联动参数 h | 左臂 | 右臂 | 候选初始姿态碰撞 |
|---:|---|---|---|
| 0 | 无解 | 无解 | 无 |
| .369 | 有解 | 有解 | 无 |
| .738 | 有解 | 有解 | 无 |

这确认了**该样本漏搜了有运动学解的高度**，不能解释全部 243 次，也没有证明替代高度有无碰撞轨迹及真实抓取成功。`h` 是联动关节参数，不是机器人世界高度。

复现脚本：[torso_ik_sweep.py](diagnostics/torso_ik_sweep.py)。完整输出：`outputs/diagnostics/potato17-torso-20261010/sweep.json`。参数：原失败 attempt、`--gpu 2`、新的 `--out`；所有姿态改变均在独立诊断仿真的 begin 之前。

## 3. free IK 为真却 MotionGen 失败：确认了额外 link 目标残留

164 次的旧描述“无碰撞可解、带碰撞失败”只能说明两套求解流程结果不同，不能直接证明是障碍物堵住。free IK 是新建 solver，其目标缓冲、种子和优化状态也不同。

### 从本工程追到安装的 cuRobo

| 位置 | 实际行为 | 影响 |
|---|---|---|
| `planning/curobo.py:47–55` | 放开 base 三关节和活动臂七关节；锁定另一臂、head 等相对关节；注册双 TCP、`link_head_2` | 注册 FK link 本身合理，不应隐含固定其世界姿态 |
| `planning/curobo.py:151` | 调用 `warmup(enable_graph=False, ...)` | 关闭 CUDA graph 不等于不执行 warmup 规划 |
| 安装版 `motion_gen.py:1929–1944` | `link_poses = state.link_pose`，warmup 把它传入 `plan_single` | 给 head 和闲置 TCP 等建立辅助姿态目标 |
| 安装版 `wrap/reacher/types.py:198–229` | 后续 `link_poses=None` 不清空已有 `links_goal_pose` | 缓存目标继续存在 |
| 安装版 `rollout/rollout_base.py:324–330` | 仅当新 link 目标非 None 才复制 | rollout 中也不会自行清空 |
| 安装版 `rollout/arm_reacher.py:270–284` | 对非活动 EE 的额外 link 继续计算姿态代价 | head/闲置臂相对关节锁定，却又被要求维持规划基准中的固定姿态，阻碍底盘移动 |

这里不是 base 从活动关节表被删除，而是**增加了与底盘移动相冲突的绝对姿态目标/代价**。报告中的“世界姿态”指冻结规划基准中的绝对姿态，不是相对机器人底盘姿态。

### S0042 的单变量对照

原 attempt：`pick-bf2b057e9916-S0042-t0-beb9268c`。重建相同 frozen 场景、原配置和规划世界；接近段使用原记录中的实际预抓取关节状态与原目标。没有回滚真实运行，也没有执行诊断轨迹。

| 独立诊断条件 | 原预抓取段 | 从原实测预抓取状态到接近目标 | 结论 |
|---|---|---|---|
| 原 MotionGen 配置及 warmup | 成功 | `IK_FAIL`；位置残差最低约 5.55cm | 复现原失败 |
| 新建独立 IK，保留环境/自碰撞 | 未测该段 | 成功；解还通过原 MotionGen 的碰撞有效性检查，碰撞球 FK 差 0 | 该接近目标并非必须碰撞才可达 |
| 只替换成新 IK，保留原缓存目标 | 未测该段 | `FINETUNE_TRAJOPT_FAIL` | 仅增加/换 IK 不能消除轨迹层的辅助目标 |
| 仅抑制 warmup 辅助 link 目标，保留关节锁、link 注册与全部避碰 | 成功，49 个插值点 | **成功，38 个插值点** | 确认辅助目标残留能造成该案例失败 |

成功接近段中底盘轨迹跨度为 x **8.60cm**、y **2.55cm**、yaw **0.0682rad**；跨度是各轴 max−min，不是累计路程。缓存中确实存在 `ee_right_tcp`、`ee_left_tcp`、`link_head_2` 三个目标。

这比“杯子难抓”“真实碰撞”“只需多加种子”更具体。**但只对这个失败实例做了严格对照，尚未量化它解释 164 次中的多少。** 一次独立规划器初始化也是样本级对照，不是全批次修复效果估计。

不能简单从 `link_names` 删除 head：诊断中这样做会触发锁定关节相关的 `KeyError: head_0`。修复应保留完整 FK/关节锁定，正确管理辅助目标缓存，不去改外部 cuRobo 安装源码，也不取消避碰。

复现脚本：[motiongen_stage_probe.py](diagnostics/motiongen_stage_probe.py)；额外碰撞开关矩阵：[ik_collision_matrix.py](diagnostics/ik_collision_matrix.py)。关键输出：`outputs/diagnostics/cup-s0042-motiongen-20261010-r5/probe.json`，GPU 2。对照先使用原设置，再只改变 warmup 的辅助 pose 传递。完整轨迹文件仅是诊断规划输出，不进入最终数据集。

## 4. 45 次候选碰撞：低目标规则把机器人改成碰撞姿态

`choose_control:245`：

```python
if not forced and task['anchor_world'][2] < .8:
    h = max(cfg.torso_heights)
```

AlarmClock_12 中心 z=**0.796774m**，因此所有候选都被强制使用 `h=.738`。随后行 250–252 检查这个改变后的诊断姿态，一旦碰撞就跳过，**不回退测试安全高度**。

对该任务全部 16 个已通过原始站位初筛的位置做独立 CPU 姿态检查：

| h | 发生碰撞的站位 | S0068 的穿透接触记录数 |
|---:|---:|---:|
| 0 | 0/16 | 0 |
| .369 | 2/16 | 6 |
| .738 | **9/16** | **20** |

9 个站位每个重复 5 次，得到 45 次。S0068 包括床体与 `robot_0/link_torso_1/2`、窗体与右臂 `link_right_arm_2/3/4` 等穿透；原始 body/geom 名和距离见 [完整姿态证据](data/dry-pose-collision-sweep-20261010.json)。

**这不是最初站位过滤漏掉同一姿态的碰撞**：初筛是 `h=0`，候选诊断后变成 `.738`。也不是已经实际撞击：检测发生在新建、未 begin 的 dry 仿真中。它证明策略不合理，不证明 `h=0` 一定能抓到闹钟。

复现脚本：[pose_collision_sweep.py](diagnostics/pose_collision_sweep.py)。输入是原四视角 run 的 frozen 配置及合法站位，不使用 GPU、不执行动作、不改资产。

## 5. Cup_30 最近结果与“经常停在预抓取”的对应关系

下表是保存于 2026-10-10 的**完成 attempt 快照**，不把尚未完成的 attempt 算作失败，不称为整批最终统计。

| run | 完成尝试 | 成功 | dry 规划无解 | 已执行预抓取、接近规划无解 | 其他执行中止 |
|---|---:|---:|---:|---:|---|
| `no-edit-val103-cup30-20261010-113028` | 22 | 2 | 6 | **11** | 3 次 torso_protocol |
| `no-edit-val103-cup30-v2-20261010` | 45 | 3 | 29 | **11** | 2 次 arm_tracking_timeout |

旧 Cup 的 17 次规划无解、v2 的 40 次规划无解，最终 free IK 均为真。v2 的 `record_only` 躯干策略不是本次修改。它不能自行清除规划器的辅助目标，也不能修复只筛预抓取的问题。

来源：[逐 attempt 快照](data/cup30-code-trace-audit-20261010.json)。这个杯子确实已有独立真实抓取成功，因此不能一概归因于“物体不可抓”；它同时存在规划链断裂，S0042 是其中已做严格原因对照的一例。

## 6. 修复顺序、仍未验证的内容

1. **先修辅助 pose 缓存语义**，确保 head/闲置臂只锁相对关节，不固定绝对姿态。用 S0042 原输入回归；再用少量不同站位检查 base 活动、碰撞约束和三段可行性。
2. **遍历抓法 × 高度 × 左右臂**，取消单个高度失败后直接放弃。低目标高度规则改为排序建议，不强制唯一值；每个姿态保留具体碰撞对。
3. **接入现有安装版 `MotionGen.plan_grasp` 的目标集/整链预检查**，而不是复制现代版示例参数。抬起偏移的坐标系必须明确，保留 base 三自由度。
4. **真实闭爪与持物规划仍需闭环**。整链预规划成功后，在连续仿真中执行接近和闭爪，依据真实物体位置建立持物碰撞模型，必要时从实测状态重新规划抬起。不得用预规划的成功替代实际保持验收。
5. 重新分类失败：有限搜索未解、辅助姿态目标冲突、具体环境/自碰撞、轨迹优化失败、控制跟踪失败、未夹住/滑落；不要把所有 `freeIK=true` 统称为 Collision。

不建议现在只增加全量试验次数。已确认的候选覆盖和目标缓存问题会把大量计算花在相似失败上。**当前交付是原因分析、诊断证据和注释，生产修复及新的真实采集仍未实施。**

## 7. 验证、视觉选择与收敛记录

- 五份注释镜像 AST 一致，运行源码和已有结果不修改。
- 独立诊断使用真实模型和本机安装版 cuRobo；GPU IK/规划、MuJoCo 穿透检查的证据分开存储，不冒充真实抓取。
- 诊断脚本只初始化新仿真、输出新目录；规划轨迹不作为动作视频或最终数据。
- 本次对照是事后针对失败机制设计，不做总体成功率显著性或因果占比推断。

| 候选视觉 | 选择 | 原因 |
|---|---|---|
| 源码调用链表 | 选中（解释） | 准确定位本工程到安装版库，保留行号 |
| 两种高度/辅助目标对照表 | 选中（证据） | 样本小，直接保留各条件结果，不伪造误差条 |
| 总成功率热图、失败饼图 | 拒绝 | 已有热图；新的总体图不能识别此处的机制 |

| 问题 | 诊断前 | 诊断后 |
|---|---|---|
| S0042 是否必须去掉碰撞才有解 | 可能 | 被带完整碰撞检查的 IK 与轨迹反例排除 |
| 名义放开的 base 是否有效参与移动 | 仅核对活动关节表 | 确认 warmup 辅助目标阻碍移动，消除后有非零移动规划 |
| 45 次 dry 碰撞具体来源 | 标签不够具体 | 找到低目标强制高度、9 个站位及具体碰撞 body |
| 243 次是否证明物体不可达 | 证据不足 | 找到漏搜可解高度的实例；总体占比仍未知 |
| 下一步 | 可能继续扩大尝试 | 优先修缓存与高度搜索，再做小范围真实闭环验证 |

本次实验价值：**有信息**。边界：局部机制已定位；全批次根因覆盖率、接入 `plan_grasp` 的物理成功率和 1–3 段最终连续采集仍须新运行验证。
