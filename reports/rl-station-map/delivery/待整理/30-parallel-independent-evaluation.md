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

## 2026-11-17T15:48:48+08:00 训练与验证差异复核

重新读取两个checkpoint和原始metrics.jsonl：best为470,016条经验/3,515次更新，选模窗口1/1；latest为3,000,000条经验/23,281次更新，最近窗口98/100。最后50万经验对应7,080/7,314个有效回合成功（96.80%），其中全程无非法碰撞成功26个；最后100回合全程无碰撞成功0个。独立验证summary明确加载best，结果0/20，20回合均健康。

直接原因是模型阶段不一致；选模规则允许不足100个回合比较且仅严格提高才替换，使早期1/1的100%锁住best。训练和本次验证均使用随机Actor及同一成功协议，不能用训练后期的高成功率解释早期best的冻结表现。训练回合中Actor持续更新，其统计也不是某个冻结checkpoint的独立成功率。应先在相同验证配置下评估latest，才能判断训练末期表现能否复现；本次只复核已有产物，未修改代码或发起新评估。

## 2026-11-17T16:17:27+08:00 抓取目标朝向与实际夹爪倾斜核查

用户截图中的“3515 train S0246 seed=20261109 stochastic”对应上述best评估第0回合。读取其保存的qpos，以相同场景/机器人配置重建CPU模型并进行前向运动学，核对TCP旋转；20回合抽样时计算的完整姿态误差与保存的angle一致，最大偏差约0.0005 rad（视频标签保留三位小数）。此次只核对旋转，不将重建模型的位置用于距离分析，也未重新执行Actor或物理rollout。

目标局部旋转为`[[0,1,0],[1,0,0],[0,0,-1]]`，TCP的+Z轴指向cube的-Z轴；cube平放时是从上往下抓取，目标TCP位于cube中心。世界目标为`cube_rotation @ grasp_rotation`，因此它跟随cube姿态，不是固定世界竖直方向。第4、9回合在20s处cube已翻转，目标TCP轴与世界向下方向夹角也变成90°；不能声称整个episode始终要求世界竖直向下。

第0回合20s处目标倾角0°，实际TCP +Z相对世界向下倾斜52.029°，完整目标姿态误差68.192°（标签1.190 rad）。这说明截图中的倾斜确实存在，并非仅由相机视角造成。对20回合分别抽取20s或更早的终止帧，其中17回合实际倾角大于30°；这是抽样帧统计，不是逐帧比例。

当前Actor自由输出6D TCP增量，DLS不锁定朝下姿态，也没有“先到正上方、再垂直下降”的执行阶段。姿态仅通过`0.5 * (previous_angle - angle)`奖励改善，偏离会产生负奖励但不会被控制器阻止；success仍不检查角度。故目标平放时朝下，不代表学到的接近动作必然朝下。该best仅训练至470,016条经验，现有20回合独立评估为0成功；这些回放证明该模型没有可靠跟随目标姿态，但不足以单独归因奖励权重、探索噪声或其他学习因素。本次未修改训练代码、成功协议或已有运行产物。
