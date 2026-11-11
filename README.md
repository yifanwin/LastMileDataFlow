# LastMileDataFlow

场景输入 → 场景准备 → 站位图 → Last-mile 数据集 → 数据使用。

支持 PandaOmron 与 RBY-1。规划使用 cuRobo V2（0.8.0），导航使用 A*，benchmark 策略保留 MolmoBot 与 π0.5。RL 站位图是独立研究流程，本次精简不改其逻辑。

安装与路径见 [setup](docs/setup.md)，完整步骤见 [运行手册](docs/运行手册.md)。安装后从仓库根目录执行：

```bash
scripts/install.sh
.venv/bin/python scripts/build_scenes.py --help
```

| 阶段脚本 | 用途 |
|---|---|
| scripts/build_scenes.py | 找目标，生成原场景和桌面/地面障碍编辑场景，采站位并导出快照、预览 |
| scripts/station_map.py | cuRobo 逐点标定与热图 |
| scripts/collect.py | 复用标定或先标定，执行 S0→S1→操作，直接导出自包含训练数据 |
| scripts/bench_build.py | 按实测成功率选择困难 S0、参考站位与恢复区域 |
| scripts/bench_evaluate.py | MolmoBot/π0.5 闭环评测 |

当前不提供统一 CLI、八类模式构造、多标签诊断、recipe 直接标定或独立物化入口。

GPU 按实时空闲情况选择。--gpu 使用物理编号，EGL 自动映射并传给子进程；需要覆盖时传 --egl-devices。不要将物理 GPU 编号直接当作 EGL 编号。

本轮已验证 house103/Cup_30 的原场景与一个桌面障碍场景；原场景站位、MJB、初始状态与基线一致。完整批量采集和策略评测不能由此推定通过。真实验证范围与当前问题见 [重构交接](reports/simplify-refactor/delivery/01-handover.md)。

- [架构](docs/architecture.md)、[数据格式](docs/data-format.md)、[配置](configs/README.md)、[测试](tests/README.md)。
- [报告入口](reports/README.md)与 [产物规则](outputs/RULES.md)。历史报告保留，历史成功不代表当前主链已验证。
