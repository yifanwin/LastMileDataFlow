# 躯干关节误差解除硬停止：执行报告

## 结果与范围

根据用户明确要求，当前无编辑 pick/open 改为 `mobile-pick-v2` / `mobile-open-v2`，配置显式 `torso_error_policy=record_only`。取消0.002rad躯干跟踪误差的立即停止及最终失败判定；不是提高阈值到另一个数。每个监测物理步仍记录实际6关节、command_h和torso_tracking_error_rad。

20D控制与h→[0,h,-2h,h,0,0]目标不变；关节/执行器限位、动作限幅、真实碰撞、数值有限性、工作空间、抓取/保持/滑落验收保留。Head、闲置臂、活动臂/TCP跟踪检查不变。原编辑/固定站位strict-pick-v3默认check_torso=True，未放宽。

已同步项目根目录 `RBY-1自由度控制.md`，解释h是角度联动参数而非米，并写明新旧协议及误差记录范围；无编辑构造说明、运行手册也更新。

## 验证

- 全套195项：187通过、8跳过；新增躯干偏差放行、旧strict仍拒绝、非有限证据仍无效、调整阶段误差计算回归。
- 将此前10次torso失败的真实已记录前缀用新检查重算，10次均不再触发该停止，且前缀没有其他检查失败。这里只验证已记录前缀，绝不推断后续必然抓取成功。见 [前缀核查](data/torso-v2-recorded-prefix-audit.json)。
- 独立原生重跑旧val_0/Potato_1/S0008同种子：使用新协议执行21步，误差遥测与四路视频生成；预抓取重新规划返回FINETUNE_TRAJOPT_FAIL，任务仍失败。不是成功抓取，也未达到旧试验中发生躯干超阈值的状态。证据目录 `outputs/no_edit/no-edit-torso-v2-native-probe-20261010/`。
- `git diff --check` 通过。没有宣称解除躯干误差能修复IK/碰撞等其他失败。

## 运行切换

对已核实的三个旧主进程正常发TERM；它们的summary/interruption均记录incomplete，历史attempt未覆盖。没有将旧失败重新标为成功，也不在原身份resume新源码。用下列新ID从原始场景重新完整评测：

| 新运行ID | 范围 | GPU候选集合 |
|---|---|---|
| no-edit-val103-cup30-v2-20261010 | val103/Cup_30全部合法点，每点5次 | 5 |
| no-edit-val103-full-v2-20261010 | val103所有合格目标、全部合法点，每点5次 | 1 |
| no-edit-val-four-view-full-v2-20261010 | 所有原始val场景，workers上限4 | 0/2/3/4/6/7 |

GPU集合分开以避免三个独立调度器选到同卡；全量仍需满足实际利用率与显存条件，未必同时派满4个worker。整体时间分别见新目录timing.json。本报告不声明新运行已经完成或成功。
