# 独立并行评估与指定best模型验证

- 状态：active
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

待运行；结果、耗时、20段视频核查及限制完成后补充。
