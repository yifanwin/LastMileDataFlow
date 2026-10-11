# LastMileDataFlow 接触策略简化记录

## 结论与执行范围

按用户要求把物理接触与任务验收分开。运行时不实现穿透类型校准、趋势分析或白名单终止器。
新接触策略 **`runtime-record-only-v1`**；VLA 协议 **`mobile-vla-physical-v2`**。
模型原始输出、上游 pick/open 后处理、实际控制值和物理模型均未因本次修改改变。

- 阶段一轻量 runner、固定站位执行、no-edit 规划后端和 MolmoBot VLA 后端：接触仅记录。
- 同手双指互碰、手指/物体、环境接触及穿透：由 MuJoCo 求解，不在线终止。
- 已有明确动作/工作空间/固定底盘协议仍独立保留；本次不把命令校验或任务接触力验收删掉。
- 初始化和场景构建继续过滤非法初态；构建静置只检查最终严重重叠，短暂软接触不作为累计失败。
- NaN/Inf、MuJoCo 仿真警告单列 `simulation_failure`，任务结论 unknown，成功率不计零。
- 成功但发生环境接触的轨迹不自动删除；后处理可按质量标记分级。

MuJoCo 软接触允许穿透与推开物体的接触力并存；接触本身不是非法物理状态。
依据：[MuJoCo 官方软接触模型](https://mujoco.readthedocs.io/en/stable/computation/#soft-contact-model)。

## 指标与可追溯性

`trajectory_metrics` 提供 `task_success`、`simulation_valid`、`forbidden_contact_count`。
最后一项是每个物理 tick 的无序 body 对数，不是接触点数/碰撞事件数。
底盘地面支撑、指定手指/目标、同手双指不标为非期望接触；其余机器人接触只记质量标记。
逐 tick 保留 geom/body、接触距离、力和时间。摘要最多32条示例，避免长轨迹接触列表无界膨胀。
不因为存在接触日志就把失败归因改成 Collision，也不因接触日志停止后端重试。

旧严格规则 run `molmobot-val103-2m-5trials-20261011-r2` 已收到 TERM 后停止，其模型子进程已退出。
原证据保持不变：trials.json 共63条，旧标签均 failure，其中57条 robot_collision、
5条 base_outside_workspace、1条 policy_step_budget_exhausted。它们不代表新规则效果，不能混合分母。

## 验证

- 单元/故障回归：267项，259通过、8项显式跳过，18.291s；见 `outputs/logs/contact-policy-delivery-regression-20261011.log`。
- 简化 VLA fixture：20mm 环境接触下两个动作/50个物理 tick 完整执行，夹爪 raw/submitted 保持 +100；
  没抬起仍判 task failure，而不是碰撞失败。MuJoCo 警告另一个 fixture 归 simulation_failure。
- **真实 RBY-1 原生空夹**：`outputs/diagnostics/contact-policy-native-rby1-20261011-r2/results.json`。
  +100 连续闭合0.5s、125个物理 tick；全程检查通过，最深接触 **7.067mm**，无仿真健康异常。
  原生 motor 输出未改变，未关闭碰撞、未修改原始资产、未重写执行期 qpos。
  这是空夹物理/检查器验证，**不是模型推理或真实抓取成功**。
- 首个诊断脚本因空接触列表求 min 的错误中止，原现场保留；修复脚本后另建 r2，不覆盖。

## 新采样与预期

新 run：`molmobot-val103-contact-record-only-20261011`，沿用 val_103、2m、每有效站位5次、
1024×576及模型头部鱼眼。单个受控变量是执行期接触判定；不调整模型、任务成功阈值或相机。
预期：接触发生后仍继续动作，采样不再由 robot_collision 结束；不预设抓取成功率会提高。
仿真/记录/墙钟异常仍保留诊断及 incomplete，不能伪造五次有效统计。

当前真实全量任务与最终热图尚未完成。视觉规划：本次是判定规则修复，日志及短证据表更准确，
不为0.5s空夹制造成功率图；最终工作空间热图由新 run 的真实 trials 生成，未知点保持未知。

### 启动核对

新 run 已启动，主进程 PID **3585599**，自动选择 **CUDA 5**。
选择时10s窗口内3次利用率均0%，最低空闲显存49080MiB；选择证据为 run 内 `gpu_selection.json`。
源码摘要 `283b5d79de94b78c6fa55dadf2cd246806086d5f02c4104a57ba0bf02a0d4ab9`，
冻结输入摘要 `138ce402d586c0d3dcae0c421e3fc8204212732bca8b7b6214016909f3aba083`。
日志入口 `outputs/logs/molmobot-val103-contact-record-only-20261011*.log`。
截至启动核对，已完成模型权重摘要核验及场景冻结，模型进程处于加载阶段；
此记录不把已启动写成全量完成，后续以 run 的 progress/trials/heatmap 为准。

### 真实模型已开始动作（后续核对）

模型已加载到 CUDA 5，显存分配9645.5MiB（模型日志），第一站 S0017 正在执行。
只读活动证据前缀：202个已记录动作、5059个物理 tick；首次机器人接触在5.448s，
最深机器人接触6.605mm，接触后已另执行147个动作，没有被接触终止。
完整前缀摘要为 `outputs/diagnostics/contact-policy-live-prefix-20261011/results.json`。
这些是顺序读取的活动文件前缀，不是封存 attempt 审计，不代表任务成功或完整五次统计。
