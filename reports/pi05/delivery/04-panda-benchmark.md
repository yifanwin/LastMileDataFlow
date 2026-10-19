# Panda-Omron π0.5 lastmile-bench 运行

状态：done  
日期：2026-10-18  
替代：无  
关联：[π0.5 构建](03-implementation.md)、[运行指南](../docs/01-running.md)、[benchmark 指南](../../benchmark/docs/运行指南.md)

本轮目标是使用匹配 RoboCasa 控制 profile 的困难 S0 benchmark，实际运行 Panda π0.5 zero-shot，检查完整率、任务成功、区域进入和实际位移。已有依据是固定场景短 rollout 可执行，但尚未完成抓取；本轮不预设成功率。若没有合格标定配对或路径，保留负结果，不改阈值。

后续范围纠正见[全量标定需求](../requirement/02-embodiment-full-calibration.md)：本次只复用了 coverage 保留的 3 个旧站位，半径 1.6 m 也没有依据 Panda 臂长配置。本报告的 0/6 和空 benchmark 仅描述该局部输入，不能代表用户要求的臂长范围全量标定结果。

## 配置与来源

- 原场景：`../panda-omron-phase01/outputs/diagnostics/panda-omron/flow-scene-007`，原 val103 Cup 与 3 个站位。
- 原 `position-v1` benchmark 不直接复用；从原 MJCF 重建 `robocasa-v1`，复制非机器人关节状态，保留站位初始机器人姿态，重新测量三相机 profile 下的可见性并重新标定。
- 重建脚本：`outputs/diagnostics/pi05-benchmark/prepare.py`；检查结果：新场景 `preparation.json`。
- 机器人：`configs/robots/panda_omron_robocasa.json`，20 Hz / 2 ms，三路 RGB。
- 参考标定：`configs/execution/panda_omron_robocasa_curobo.json`，3 站位 × 2 trial；GPU 5。
- 策略：`configs/policies/pi05_panda_omron.json`，RoboCasa365 checkpoint `pi05_pretrain_human300/multitask_learning/75000`，配套 mean/std，项目 `.venv-pi05`。
- benchmark：`configs/benchmark/v0.1.json`，low=0.2、high=0.6、horizon=400、wall_timeout_s=600、seed=42；策略 GPU 0。
- 原数据只读，新产物均位于当前工作区 `outputs/`；保留配置、trial、轨迹、视频、结果图片和日志，不修改 Git 历史。

## 运行命令

从本项目根目录执行，输出目录必须不存在：

```bash
MUJOCO_GL=egl CUDA_VISIBLE_DEVICES=5 MUJOCO_EGL_DEVICE_ID=5 \
  .venv/bin/python outputs/diagnostics/pi05-benchmark/prepare.py

.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-robocasa-bench-01 \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_robocasa_curobo.json \
  --output-dir outputs/station_evaluations \
  --run-id panda-robocasa-bench-01 --gpu 5

.venv/bin/lastmile-dataflow build-bench \
  --dataset outputs/scene_datasets/panda-robocasa-bench-01 \
  --calibration outputs/station_evaluations/panda-robocasa-bench-01 \
  --config configs/benchmark/v0.1.json \
  --output outputs/benchmarks/panda-robocasa-01

.venv/bin/lastmile-dataflow evaluate-bench \
  --benchmark outputs/benchmarks/panda-robocasa-01 \
  --policy-config configs/policies/pi05_panda_omron.json \
  --output outputs/benchmark_evaluations/panda-pi05-01 --gpu 0
```

运行日志位于 `outputs/logs/pi05-bench-*.log`。最终结果以原始 trial、benchmark summary 和评测 summary 为准；命令退出码不替代任务判定。

## 实际结果

**已执行场景准备、完整标定、benchmark 构建与评测入口；本轮没有合格 episode，π0.5 模型未加载。** 停止于实测恢复站位不足，没有降低阈值或补造配对。

| 站位 | 成功 / 试验 | 失败原因 |
|---|---:|---|
| S0021 | 0 / 2 | 抓取后抬升/接触保持未达标；最大抬升约 9.6 mm |
| S0020 | 0 / 2 | planning_no_solution |
| S0013 | 0 / 2 | 抓取后抬升/接触保持未达标；最大抬升约 7.7 mm |

标定完整，耗时 513.1 s，无共享基础设施故障。3 个站位均为低率点，没有达到 high=0.6 的站位。`build-bench` 正常完成，`episodes=0`，排除原因 `no_high_success_station=3`。随后 `evaluate-bench` 返回 `ValueError: no benchmark episodes`；没有生成评测 run，也没有策略 rollout、TSR 或 Recovery Success 结果。

原 position 标定中的成功站位不能作为新 RoboCasa controller 下的成功证据。本轮是控制 profile 更换后的参考标定负结果，无法据此评价 π0.5 在困难 S0 上的任务能力。后续应先定位参考抓取的接触/末端跟踪误差，并取得新 profile 下实测成功的恢复站位，再构建和运行 benchmark；保持原筛选标准。

- [原始标定 trials](../../../outputs/station_evaluations/panda-robocasa-bench-01/trials.jsonl)
- [标定 summary](../../../outputs/station_evaluations/panda-robocasa-bench-01/summary.json)
- [benchmark summary](../../../outputs/benchmarks/panda-robocasa-01/summary.json)
- [评测入口日志](../../../outputs/logs/pi05-bench-evaluate-01.log)

![本轮 benchmark 构建结果：1 个源场景，0 个合格 episode](../../../outputs/benchmarks/panda-robocasa-01/visualization/summary.png)

图为本轮实际构建结果，已打开检查；没有策略执行数据。此处用原始计数表和构建汇总图表达结果，不绘制成功率或策略轨迹。

## 本轮代码与文档修改

修复评测组合图只读取 `head` 视频名的问题：支持 Panda 的 `observation` 视频名，保持 RBY-1 原有行为。实际评测为空，本轮只完成该兼容修改与相关工作流回归，未验证 Panda 评测组合图中的真实最终帧。`tests.workflows.test_benchmark_progress` 3 项通过；修改文件空白检查通过。运行指南已补充双本体 π0.5 配置和 profile 要求。
