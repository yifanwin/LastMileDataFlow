# 回归与真实 smoke

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -v
```

测试覆盖实际控制语义、简化 MuJoCo 模型、碰撞/支撑、导航、标定分母、一次轨迹记录和薄 workflow。
`test_light_dataflow.py` 覆盖简化新格式、两个采集入口独立分发、复用标定、当前 run 只标定一次和不完整标定不执行恢复。mock、几何规则和简化模型不能证明真实资产/CUDA/VLA 成功。
`curobo_v2_real_smoke.py` 为显式真实场景/GPU 执行；`head_fov_v080_gradient_smoke.py` 检查 V2 梯度，不随自动测试运行。
旧封存、重复审阅、canonical 双写、capacity 例外与历史输入兼容测试随对应功能删除。
安装入口、配置解析和移动简化场景的回归见 [步骤六、七报告](../reports/2026-10-14-refactor-light-step67.md)。

## 最小 multitask 策略

`test_multitask.py` 检查 pick/open 的真实差异、22D state、动作缓冲和推理模块独立性。
`multitask_real_smoke.py` 是显式真实权重测试，需要本地 checkpoint/tokenizer 和指定 GPU；用合成观测检查 pick/open 与 service/client，不执行机器人。命令见 [setup](../docs/setup.md)。
