# 独立并行评估与指定best模型验证

- 状态：done
- 日期：2026-11-17
- 时间：2026-11-17T15:34:53.410038+08:00
- 替代：无
- 关联：[公共并行评估](18-parallel-evaluation.md)、[运行指南](running.md)

## 实现

公共修改先在actor-critic提交ce7fba9，再merge到rl-cube-contact，保留flat协议并接入共享向量rollout。独立评估默认最多20环境，方块仍20回合；每个trial使用seed+i的独立随机流。graph的随机grasp也绑定到对应trial，原训练采样方式不变。

方块默认保存全部20回合的实际状态回放与视频，文件名带trial编号，元数据记录trial/seed。完成回合立即冻结统计并刷新trials.jsonl；异步结束、异常回合、末批补齐不产生重复结果。保留S0246、Home和方块初态、50s上限、随机Actor、同一物理子步双指承力且抬升0.0086m成功的协议。异常回合排除成功率分母。

## 软件验证

actor-critic的evaluation/SAC/video/sampling局部24项通过。全仓回归356项，1失败、9错误、3跳过，均为planning测试受RL环境缺少cuRobo/rtree影响；未声称全仓通过。rl-cube-contact的全部test_rl*.py共68项通过。首次局部测试误列不存在的test_rl_terms模块，改用实际RL测试发现后通过。

## 指定checkpoint

只评估用户指定训练run `20261117-131857-s0246-cube-stage-peak-success-3m-b512-g0123/best.pt`，对应470,016条transition、3,515次更新。使用该run的config.yaml并严格校验checkpoint有效配置；不评估latest，不改训练参数。

## 真实评估

- 开始：2026-11-17T15:35:34+08:00；结束：2026-11-17T15:43:45.610258+08:00；核查：2026-11-17T15:45:54.413914+08:00。
- 执行提交：`057dd5fa9cdca47b0d03582f6cb24b0274db6c3a`；物理GPU0，20环境并行；seed20261109–20261128。运行期间未修改源码。
- 退出0，总耗时491.071s（约8分11秒）；其中20段视频渲染合计142.422s。没有同条件串行对照，不报告加速倍数。
- 原始结果：[summary.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-153534-best-parallel20/summary.json)、[trials.jsonl](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-153534-best-parallel20/trials.jsonl)、[launch.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-153534-best-parallel20/launch.json)。
- 全部视频：[20回合视频索引](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-153534-best-parallel20/videos.md)；每回合均有对应状态回放。

| 指标 | 结果 |
| --- | --- |
| 总回合 / 健康回合 / 异常回合 | 20 / 20 / 0 |
| 成功率 | 0/20（0%） |
| 无非法碰撞成功率 | 0/20（0%） |
| 整回合无非法碰撞 | 5/20 |
| 失败分类 | collision_failure 15、control_failure 1、timeout 4 |
| 平均回合回报 | -11.441552 |

13回合记录到首次双指同子步接触，但仍未满足完整成功判定。失败分类沿用原有优先级；其中collision_failure表示回合不成功且全程曾记录非法碰撞，不单凭这个标签认定碰撞是唯一终止原因。

视频已全部通过FFmpeg实际解码并核对帧数；20份回放各包含steps+1帧，保留终止帧、排除终止后推进，初始qpos逐元素一致，trial/seed/视频/回放/结果一一对应。已抽看第0回合20s处画面，场景与夹爪近景均可见方块。[verification.json](/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/rl-cube-contact/outputs/experiments/rl-cube-lift-v2-s0246/runs/20261117-153534-best-parallel20/verification.json)记录核查结果。

本次只说明该best.pt在固定训练场景与初态下的冻结随机Actor表现。该模型保存于470,016条经验，当时best选择窗口只有1个成功回合；训练末尾98/100属于另一训练窗口，不能代替本次模型评估。没有评估latest，也没有测试新站位或新场景。
