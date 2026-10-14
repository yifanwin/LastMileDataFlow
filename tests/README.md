# 核心回归与真实 smoke

历史 Agent/DSL、legacy 导入、五阶段专用工作流和交付测试已删除。
保留动作、仿真、场景准备、资产/障碍物、站位统计、导航、记录、cuRobo/VLA 与两类下游回归。
混合测试中只删除历史专用部分；共享简化模型和任务证据放 `helpers.py`。

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH=src:tests \
  <python> -m unittest discover -s tests -v
```

- `test_cli.py`：当前保留命令集合与增强命令分发，不执行真实机器人。
- `test_foundation.py`：保留共享动作、初始化/快照和仿真逻辑，不运行旧短动作 workflow。
- `test_scene_graph.py`、`test_local_construction.py`、`test_edit_views.py`：保留共享图、静置、资产和相机几何，不依赖旧 DSL/Agent。
- `curobo_v080_real_smoke.py`、`head_fov_v080_gradient_smoke.py`：显式真实资产/GPU 路径，不随 unittest 自动执行。

旧 `real_smoke.py`、阶段二/三 smoke 和基于旧 runner 的 canonical smoke 已删除。
下一轮最小主链真实复现尚未执行；单测、mock 与简化 MuJoCo 模型不证明真实 cuRobo/MolmoBot 成功。
当前仍有旧协议相关核心回归，随步骤三的生产者/消费者同步修改，不恢复已删除的审计 CLI。
本轮测试结果及已存在的基线失败见 [执行报告](../reports/2026-10-14-refactor-light-step12.md)。
