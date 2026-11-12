# LastMileDataFlow

场景输入 → 场景准备 → 站位图 → Last-mile 数据集 → 数据使用。

支持 PandaOmron 与 RBY-1。规划使用 cuRobo V2（0.8.0），导航使用 A*，benchmark 策略保留 MolmoBot 与 π0.5。RL 站位图是独立研究流程；精简时可以调整 RL 代码的结构和位置，训练与评测行为保持不变。

安装与路径见 [setup](docs/setup.md)，完整步骤见 [运行手册](docs/runbook.md)。安装后从仓库根目录执行：

```bash
scripts/install/main.sh
.venv/bin/python scripts/scenes/build.py --help
```

| 阶段脚本 | 用途 |
|---|---|
| scripts/scenes/build.py | 找目标，生成原场景和桌面/地面障碍编辑场景，导出快照 |
| scripts/station_map/sample.py | 对场景库逐场景采候选站位并导出预览，没有合法站位的场景在这一步淘汰 |
| scripts/station_map/planner_trials.py | cuRobo 逐点标定与热图 |
| scripts/lastmile_dataset/collect.py | 复用标定或先标定，执行 S0→S1→操作，直接导出自包含训练数据 |
| scripts/lastmile_benchmark/build.py | 按实测成功率选择困难 S0、参考站位与恢复区域 |
| scripts/lastmile_benchmark/evaluate.py | MolmoBot/π0.5 闭环评测 |

当前不提供统一命令入口、八类模式构造、多标签诊断、recipe 直接标定或独立物化入口。

GPU 按实时空闲情况选择。--gpu 使用物理编号，EGL 自动映射并传给子进程；需要覆盖时传 --egl-devices。不要将物理 GPU 编号直接当作 EGL 编号。

本轮真实检查覆盖场景构建、Panda单trial、3个连续配对、benchmark重建及π0.5两步调用链。整体回归未全绿，RGB与旧基线有最大2/255差异，未完成全部真实验收；已按用户要求停止所有实验与验证。详见 [第一轮总结](reports/simplify-refactor/delivery/10-final-summary.md)。第二轮精简与目录分层后，在 GPU 0 上以最小规模跑通了场景构建、站位采样、标定、采集和 benchmark 构建，只验证能运行，策略评测未跑；见 [第二轮总结](reports/simplify-refactor/delivery/12-second-pass-summary.md)。

- [架构](docs/architecture.md)（源码按 pipeline 分层：utils → sim → robots → planning → operation → policies → scenes → station_map → lastmile_dataset / lastmile_benchmark）、[数据格式](docs/data-format.md)、[配置](configs/README.md)、[测试](tests/README.md)。
- [报告入口](reports/README.md)与 [产物规则](outputs/RULES.md)。过时报告在 `reports/archives/`，历史成功不代表当前主链已验证。
