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
