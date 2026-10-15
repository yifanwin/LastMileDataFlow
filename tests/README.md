# 回归与真实 smoke

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -v
```

测试覆盖实际控制语义、简化 MuJoCo 模型、碰撞/支撑、导航、标定分母、一次轨迹记录和薄 workflow。
`test_light_dataflow.py` 覆盖简化新格式、两个采集入口独立分发、复用标定、当前 run 只标定一次和不完整标定不执行恢复。mock、几何规则和简化模型不能证明真实资产/CUDA/VLA 成功。
`curobo_v2_real_smoke.py` 为显式真实场景/GPU 执行；`head_fov_v080_gradient_smoke.py` 检查 V2 梯度，不随自动测试运行。
旧封存、重复审阅、canonical 双写、capacity 例外与历史输入兼容测试随对应功能删除。
安装入口、配置解析和移动简化场景的回归见 [步骤六、七报告](../reports/第一轮精简/2026-10-14-refactor-light-step67.md)。

## 最小 multitask 策略

`test_multitask.py` 检查 pick/open 的真实差异、22D state、动作缓冲和推理模块独立性。
`multitask_real_smoke.py` 是显式真实权重测试，需要本地 checkpoint/tokenizer 和指定 GPU；用合成观测检查 pick/open 与 service/client，不执行机器人。命令见 [setup](../docs/setup.md)。

## val103 / Cup_30 主链复现

`refactor_step8_real.py` 接收已接受的场景数据集、显式资产路径和 GPU，执行有限逐点标定、连续采集、独立 native 策略标定及 bench。它不会补造成功率或困难配对，不随单测自动运行。实际负结果、连续导航和复现命令见 [步骤八报告](../reports/第一轮精简/2026-10-14-refactor-light-step8-val103-cup30.md)。

`test_asset_retrieval.py` 检查注册资产检索不扫描整个 Prefabs；`test_policy_batch_stop.py` 与生命周期测试检查真实 RPC 超时后的关闭和停批行为。

## 第二轮精简

`scenes` 按 loading/tasks/stations/augmentation 分组，`runtime` 按 control/construction/execution/outcomes 分组。
测试直接导入新路径，不保留旧路径兼容壳。
`test_recording.py` 用真实简化 MuJoCo stepping 检查观测不重复、phase 在执行前设置、部分物理间隔记录一次、跨场景编号不重读文件；传感器与视频 writer 使用替身，不代表真实 GPU 验收。
