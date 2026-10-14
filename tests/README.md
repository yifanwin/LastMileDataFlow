# 回归与真实 smoke

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH=src:tests \
  python -m unittest discover -s tests -v
```

测试覆盖实际控制语义、简化 MuJoCo 模型、碰撞/支撑、导航、标定分母、一次轨迹记录和薄 workflow。
`test_light_dataflow.py` 覆盖简化新格式及不重复标定。mock、几何规则和简化模型不能证明真实资产/CUDA/VLA 成功。
`curobo_v2_real_smoke.py` 为显式真实场景/GPU 执行；`head_fov_v080_gradient_smoke.py` 检查 V2 梯度，不随自动测试运行。
旧封存、重复审阅、canonical 双写、capacity 例外与历史输入兼容测试随对应功能删除。
结果见 [步骤三、四报告](../reports/2026-10-14-refactor-light-step34.md)。
