# cuRoboV2 v0.8.0 无编辑采集运行手册

本后端用于原始 ProcTHOR val；旧场景编辑后端和 `configs/no_edit/val.json` 保留。**不要用旧 run-id 跨版本续跑。**

## 环境与入口

在 `feat/no-edit-lastmile-val` 的 worktree 根目录运行：

```bash
scripts/setup_curobo_v080.sh /data0/wenyifan/MoMaTrajGen/curobo \
  /data0/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export DATAFLOW_PYTHON="$PWD/.venv-curobo-v080/bin/python"
export WARP_CACHE_PATH="$PWD/outputs/dependencies/warp-cache"
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
```

脚本导出精确标签 `4ea77366ca48ee453e7df139e39fa6532af49f3b`，安装独立 cuRobo/CUDA 后端。Torch、MuJoCo 等依赖显式借用现有解释器的 site-packages；**不是所有依赖都完全独立**。版本和导入位置写入 `outputs/dependencies/environment-v080.json`。不修改相邻资产或 `curobo/` checkout。

v0.8.0 非 editable 安装在本机缺少 task YAML，故使用固定标签源码的 editable 安装。启动校验版本与关键源码哈希，不允许静默退回旧库。

## 先跑 S0042 真实验收

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES=1 MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 \
  "$DATAFLOW_PYTHON" -u tests/curobo_v080_real_smoke.py \
  --frozen-run outputs/no_edit/no-edit-val103-cup30-20261010-113028 \
  --output "outputs/diagnostics/v080-pilot/execute-cuda1-$(date +%Y%m%d-%H%M%S)" \
  --station S0042 --mode execute
```

`--mode model` 检查左右臂五个 h 值的 FK；`--mode plan` 验证完整三段，但**不代表物理抓取成功**。输出目录必须未存在。默认使用相同 Cup 场景快照，只在新的 Simulation 初始化阶段放置机器人。

## val103 / Cup_30 完整采样

物理验收之后再执行，避免把规划或跟踪问题扩散成大批失败：

```bash
DATAFLOW_PYTHON="$PWD/.venv-curobo-v080/bin/python" bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --dataset-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets/scenes/procthor-10k-val \
  --config configs/no_edit/curobo_v080.json \
  --run-id "no-edit-v080-val103-cup30-$(date +%Y%m%d-%H%M%S)" \
  --houses 103 --targets Cup_30 --gpu-ids 1 --workers 1
```

删除 `--targets Cup_30` 采集 val103 的全部任务；删除 `--houses 103` 扩展整个 val。当前按用户要求只用 CUDA 1。批处理自行设置 worker 的 CUDA/EGL 映射，不在父进程另加 `CUDA_VISIBLE_DEVICES`。`--max-trials` 只用于 smoke，截断结果标为 incomplete，不冒充完整采样。

## 完整 val_103：全部原始目标任务

不指定 `--targets`，枚举 val_103 中所有符合当前 pick/open 任务规则的目标（不是每个装饰物都生成任务），并完成各有效点的默认5次操作：

```bash
cd /data0/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/no-edit-lastmile-val
export DATAFLOW_PYTHON="$PWD/.venv-curobo-v080/bin/python"
export WARP_CACHE_PATH="$PWD/outputs/dependencies/warp-cache"
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
export PYTHONUNBUFFERED=1
mkdir -p outputs/logs
RUN_ID="no-edit-v080-val103-all-cuda1-$(date +%Y%m%d-%H%M%S)"
set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --dataset-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets/scenes/procthor-10k-val \
  --config configs/no_edit/curobo_v080.json \
  --run-id "$RUN_ID" --houses 103 --gpu-ids 1 --workers 1 \
  2>&1 | tee "outputs/logs/$RUN_ID.log"
```

## 全部原始 val 场景

不指定 `--houses` 或 `--targets`，枚举整个 `procthor-10k-val`，保持原始场景、不做场景编辑。按当前要求仍只使用 CUDA1：

```bash
# 在上述 worktree 中执行；复用上一节的环境变量。
mkdir -p outputs/logs
RUN_ID="no-edit-v080-val-all-cuda1-$(date +%Y%m%d-%H%M%S)"
set -o pipefail
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --dataset-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets/scenes/procthor-10k-val \
  --config configs/no_edit/curobo_v080.json \
  --run-id "$RUN_ID" --gpu-ids 1 --workers 1 \
  2>&1 | tee "outputs/logs/$RUN_ID.log"
```

**运行前注意：** Cup_30 测试 run `no-edit-v080-val103-cup30-20261010-173735` 于 2026-10-10 20:05 异常停止，错误为 rollout 重试时的 `FileExistsError`（证据目录命名冲突），已于本次修复（见文末）。更早的 `InvalidAction:torso height out of range`（15:55）与原 S0024/相同种子重测成功（259步）；yaw 越界问题亦已修复并验收。其他目标仍需验收后再启动全场景；以上是运行命令，不表示全量已启动或通过验收。新版 open 尚未完成真实成功验收。

长任务建议在 `tmux` 的持久终端中运行；不要仅依赖临时工具会话的 `nohup`。不要同时启动多个 CUDA1 批次。父进程不要设置 `CUDA_VISIBLE_DEVICES`，worker 自行映射 GPU。

同一版本、同一源码与配置恢复时，复用**实际原 run-id**并在原命令末尾增加 `--resume`；不要重新生成时间戳。源码/配置修复后应使用新 run-id，不能强行绕过冻结摘要检查。最终以 `summary.json` 与 `timing.json` 的状态为准：`infrastructure_error` 表示本 run 存在基础设施异常的证据（单场景故障已按场景隔离，不再中止其余场景），不是完整完成；查 `scene_results` 定位失败场景，再查该任务 `progress.json` 的 `terminal_trials/expected_trials` 确认进度。

## 不混用三层预算

| 层级 | 默认规则 |
|---|---|
| 站位独立试验 | 每点 5 次 |
| S1 外层 | 首次＋最多 1 次重试；真实状态接续，安全问题可以提前停止 |
| 原生每次 plan_pose | 默认最多 5 次；原生可能提前返回 |
| 每次操作规划查询 | pick 12、open 128；原生目标集、接近、抓取、抬起分别计数 |

抓法最多 4、IK seeds 32、trajectory seeds 4、单 attempt 300 秒，未加搜索预算。新后端忽略仅用于旧后端的 `torso_heights`。h 连续优化，下发 `[0,h,-2h,h,0,0]`；关节按名称转为现有 20D 协议。h 增量上限来自联动速度限位、控制频率与原有 time_dilation，不沿用离散调高的固定慢步长。

## 输出与判定

- `planner_identity.json`：精确版本、11D、唯一活动 TCP、碰撞开关。
- `grasp_result.json`：原生三段；`solver_trace.json`：实际调用已有候选的 IK 误差、约束和碰撞 link，不额外搜索。
- `target_query_range.json`：小网格无命中距离修正；只扩查询范围，不改物体几何。
- `carried_geometry.json`：原生 mesh/SDF 拟合、覆盖与穿出指标。球拟合是近似，不能宣称等同精确物体网格。
- `outcome.json` / `result.json`：真实任务结论；`trajectory.jsonl` / `physics.jsonl.gz` / `replay.npz`：真实动作与物理事实。
- 实际执行后保留 `videos/{head_camera,wrist_camera_l,wrist_camera_r,third_person_camera}.mp4`。第三人称为已有分析型拼图；规划无解、未 stepping 时不伪造视频。
- 批处理沿用 Gaussian 成功率图、S0 失败证据、A* 连续段、最少 1 段成功入集规则与整体 `timing.json`。

收到 `infrastructure_error` 时先看异常和环境身份；IK 有精确位姿候选但碰撞/动态约束不满足时，不标成“物体不可达”。新采集须使用新 run-id，源码变化后旧冻结摘要拒绝续跑属于正常保护。

## 连续 S0→S1 接口 smoke

使用已经成功的 S1 记录来验证导航后接续操作；这不替代全采样成功率选点：

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES=1 MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 \
  "$DATAFLOW_PYTHON" -u tests/curobo_v080_real_smoke.py \
  --frozen-run outputs/no_edit/no-edit-val103-cup30-20261010-113028 \
  --output "outputs/diagnostics/v080-pilot/continuous-cuda1-$(date +%Y%m%d-%H%M%S)" \
  --mode continuous --station S0032 --goal-station S0042 \
  --winning-attempt outputs/diagnostics/v080-pilot/execute-synced-S0042-cuda1-153129/attempts/v080-S0042-20261010
```

该 smoke 的 S0 是接口验收起点，**未宣称为新版低成功率点**，也不写入最终数据集。正式采集仍从新版全点统计选择 S0/S1。

## 躯干边界容差修复（2026-10-10）

新版 no-edit 命令 h 保持 `[0,0.738]`：输入超出边界不超过 **0.003rad** 时裁剪回边界；更大越界或非有限输入仍拒绝。裁剪记录在 attempt 的 `torso_command_adjustments.jsonl`，动作记录同时保留原始输入和实际提交命令。此处 h 是联动关节参数，不是米。

实际反馈 h 允许超出边界 **0.003rad（约0.172°）**；物理记录包含 `torso_measured_h`、`torso_limit_excess_rad`、`torso_feedback_tolerance_rad`。超出该容差归为 `torso_feedback_limit` 操作失败，不伪装成成功。躯干联动跟踪误差仍记录，原碰撞与模型/执行器限位不变；不修改实际 qpos。

关闭夹爪和保持阶段沿用最后已提交的合法 h 目标，不把实测超调写成新目标。此修复改变源码摘要，请使用新 run-id 验收/采集，不强行续跑旧冻结 run。原始异常记录保持不变。

CUDA1 原失败点 S0024、相同种子1578639238重测成功：259步、114.306秒；关闭夹爪时实测越界最高0.001510rad，容差内未停止。证据：`outputs/diagnostics/v080-torso-fix-S0024-seed1578639238/summary.json`。回归206项，8项跳过，其余通过。

## yaw 与其他命令限位、单次警告策略（2026-10-10）

- 当前物理模型的 yaw 是有界关节 `[-3.14,3.14]`，不是可无限旋转的 continuous joint。新版规划将这一区间转换到固定规划基准的局部 yaw 范围；状态/目标使用同一连续标量，导航及跟踪不再走跨越硬限位的最短角差。不改模型、不通过写qpos绕过边界；需要时可能走较长旋转路径。
- 所有20D可控制目标使用关节/执行器/协议范围的交集。角度小幅越界≤0.003rad、底盘平移及夹爪小幅越界≤0.001m时投影回边界；更大越界拒绝。躯干h继续考虑联动关节范围。额外记录在 `command_limit_adjustments.jsonl`，保留原始动作和实际提交命令；底层严格校验仍保留。
- `InvalidAction`（关节、执行器、增量或协议限位错误）不再是全局 infrastructure_error：本次记 `failure / control_limit:*`，归因 TrackingFailure，写 `control_limit_warning` 事件和控制台 WARNING。结束当前attempt，不再执行非法命令；其他独立试验/点位继续。**警告不等于将失败当成成功。**
- 真正的环境、GPU、磁盘或视频记录设施异常仍按基础设施错误停止；本次没有将所有异常一概吞掉。原碰撞与成功判定不变。

CUDA1 原S0044、相同种子432661044重测成功：390步、92.818秒，四视角视频已保存。回归211项，跳过8项，其余通过；包括单次限位警告分类与两个点位10次操作全部继续的故障测试。源码已变化，后续用新run-id；未自动启动新全量批次。

## 证据目录唯一性与场景级故障隔离（2026-10-10）

- 证据目录改按单调 `plan_serial` 命名（`v080-<serial>-<side>`、`v080-closed-<serial>-<side>`）。`plan_count` 仍按 attempt 重置（预算按 attempt 计），重试不再落到第一次 attempt 的目录名；两次 attempt 证据各自保留，不互相覆盖。正常路径目录名不变。
- open 分支的 `world-<index>` 与上述目录统一为 `parents=True, exist_ok=True`。本次修复前，rollout 重试命名冲突会在裸 `mkdir()` 处抛 `FileExistsError`，被归为 `infrastructure_error` 并终止整批（`174137`、`173735` 两次实例）。
- 单场景 worker 的 `infrastructure_error` 不再清空待办：剩余场景继续派发，证据保留；终态在全部完成后判定，存在 infra 记为 `infrastructure_error`，否则 `completed` / `incomplete`。`worker_no_summary` 同理隔离。
- legacy 后端 `choose_control` 的 `dry_plans-*` / `measured_planner-*` / `world-*` 仍是裸 `mkdir()`；v2 不进入该路径，本次未改。

`173735` 的 80 次成功试验不因本修复恢复（该 run `retained_segments` 仍为 0），需新 run-id 重采。回归223项、跳过8项，其余通过。

## Head camera FOV 约束（2026-10-10）

只对 `curobo_v2_v080` 生效；station xy 采样、base/head 朝向初始化和 initial visibility check 不变，不修改 upstream 或机器人资产。

- `HeadFOVCost` 在正式 `MotionPlannerCfg.create(cost_manager_config_instance_type=...)` 扩展中加入 IK / TrajOpt 的 **soft cost_cfg**，覆盖整个优化 horizon；使用 cuRobo CUDA FK + torch，optimizer 内不调用 MuJoCo。
- 相机挂载来自真实 `cam_xpos` / `cam_xmat` 相对 `link_head_2` 的 optical frame；水平 FOV 由 `cam_fovy` 和记录图像宽高比推导。不把 base 朝向当作 camera 朝向，不新增 camera pose goal，不释放 head DOF。
- v0.8.0 此模型的 quaternion FK 梯度未通过 yaw 有限差分。辅助 FK 用固定 optical/TCP 轴探针的 **position FK** 恢复旋转矩阵，保持 GPU 可微；探针只在辅助 FK 配置里，不改变碰撞球或 MuJoCo 模型。
- v0.8.0 原生 seed ranking 按 pose error / 时间 / 平滑度，不按新增 soft cost 排名。因此正式 metrics 接口额外检查 **终点** 几何 FOV，让原生有限次数重试选择可见目标状态；不把 soft margin 当 hard constraint，不逐点做 MuJoCo 检查。
- cuRobo 成功后，在返回实际轨迹上做 scratch `MjData` 稀疏硬验证。包含起止、B-spline knot 的时间边界和各 phase 边界；控制点不是物理关节状态。base/torso 变化、camera pose 变化或接近 FOV 边缘时自适应补 midpoint；camera 不动且 target 未搬运时复用检查。检测区间内关节范围，避免起止相同而中间转出的漏检。
- lift 按 TCP-relative target-origin 预测物体位置，机械臂运动也会触发检查。真实抓取滑移/跟踪误差可能不同于规划近似。
- 关键状态出 FOV 时，`PlanResult.status=fov_constraint_failed`，无 executable waypoints；attempt 的兼容顶层状态仍为 `planning_no_solution`（未执行）或 `failure`（此前已执行），`reason=fov_constraint_failed`，归因 `PlanningFailure`。原生有限预算内没有成功解时用 `reason=constrained_planning_failure` 区分，不宣称全局无解。

配置（默认开启）：`head_fov_enabled=true`、`head_fov_margin=0.9`、`head_fov_weight=10000`、`head_fov_min_depth_m=0.01`。margin 仅用于 soft cost，hard FOV 使用完整视锥。关闭开关只用于明确的 baseline 对照。配置进入冻结摘要；后续采集必须使用新 run-id。

证据：`head_camera_fk_check.json`、`head_fov_validation.json`（key / adaptive / failed indices 与 geometry check 数）、`solver_trace.json` 的 endpoint feasibility、`planner_identity.json`。`finite_budget_not_impossibility=true` 表示有限种子/attempt 下没找到符合约束的解，不表示全局无解。

**边界**：FOV 约束检查 target body origin 的几何视锥，不保证全物体入画、无遮挡像素或连续时间数学证明。导航不纳入本次操作规划约束。运动目标在 lift 中使用刚性抓持近似；open 逐段刷新实测 target，段内未建立关节物体运动预测模型。

正式扩展接口参考：[cuRobo MotionPlannerCfg.create](https://nvlabs.github.io/curobo/latest/api/curobo.motion_planner.html)。实际行为以本工程 pin 的 v0.8.0 源码为准。
