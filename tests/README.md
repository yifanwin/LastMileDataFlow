# 验证

从仓库根目录执行相关领域的现有测试，再视改动范围执行整套离线回归：

```bash
MUJOCO_GL=disable PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t .
```

simplify worktree 可借用主仓库解释器；RL 测试（tests/station_map/test_rl_*.py）用独立 mjlab 环境。缺少对方依赖时如实记录，不能把 import 失败算通过。

子目录与源码顶层目录一一对应（utils、sim、robots、planning、operation、policies、scenes、station_map、lastmile_dataset、lastmile_benchmark）；test_layers.py 检查 import 只向下引用、`__init__.py` 为空，test_scripts.py 检查全部阶段脚本能在仓库外启动；helpers.py 保存必要共享简化模型和合成trace。真实spawn测试使用合成规划/执行结果，验证调度、编号、预算和失败隔离，不代表GPU规划或任务成功。

真实入口在 smoke/，先查看 --help 并准备资产与权重。curobo_rollout.py 检查指定站位；head_fov.py 检查FOV；multitask.py 检查权重推理；pipeline.py 检查主链。GPU按实时资源选择，使用生产设备映射，禁止直接把GPU编号当作EGL序号。

科研精简按需做局部验证，不新增镜像实现的测试；真实构建、规划、训练、视频和端到端结论分别记录。当前重构验证范围见 ../reports/simplify-refactor/delivery/。

PPO 数值与同步传输测试：`MUJOCO_GL=disable .venv/bin/python -m unittest tests.station_map.test_rl_ppo -v`。覆盖全局优势归一化、GAE、Actor/V 独立裁剪、KL 停止、多个 epoch 与尾批、checkpoint、V 排名和算法分派；真实 GPU 短训练与视频边界见 [PPO 交付报告](../reports/rl-station-map/delivery/36-ppo-integration.md)。

随机站位方块的物理固定底盘使用显式GPU入口 `tests/smoke/rl_fixed_base.py`，先看 `--help`。检查69维输入、有效池随机初始化、局部reset、底盘在受力与机械臂运动下不漂移；结果写入 `--output` 所指run。离线拓扑、位姿换算及回放测试位于 `tests/station_map/test_rl_fixed_base.py`。

真实移动底盘RL控制验证：`PYTHONPATH="$PWD/src" .venv/bin/python tests/smoke/rl_mobile.py --help`。该入口需真实资产和GPU，不随离线unittest运行。
