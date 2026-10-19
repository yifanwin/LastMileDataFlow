# Last-mile benchmark 组织说明

状态：active
日期：2026-10-18
替代：无
关联：[需求](../requirement/01-lastmile-bench-v0.1.md)、[实施记录](../delivery/phase_01/2026-10-18-build.md)、[运行指南](../docs/运行指南.md)

## 1. Benchmark 定义

Benchmark 复用已有 scene-datasets 与逐站位标定结果。每个 episode 从低成功率站位 S0 开始，评测策略能否自主调整底盘并完成 Pick / Open。最终目标是任务成功，不要求到达固定 S1。

构建条件：S0 标定成功率 ≤20%；同一 scene + object + task 至少存在一个成功率 ≥60% 的站位；S0 到恢复区域存在合法底盘路径。标定允许底盘移动，成功率沿用原标定协议，表示从该初始站位出发的任务完成率。

Recovery Region 由高成功率站位及其局部邻域近似，并受导航自由空间约束。高成功率站位、区域和恢复路径属于私有 oracle，不提供给策略。

## 2. 目录结构

以下路径均相对于本工作区根目录：

```text
outputs/
├── benchmarks/all-existing-v0.1/
│   ├── rby1-current/                 # 当前 RBY-1：2 个 episode
│   │   ├── config.json              # 构建配置与数据来源
│   │   ├── episodes.jsonl           # 公开 episode 定义
│   │   ├── oracle.json              # 私有恢复证据与难度信息
│   │   ├── summary.json             # 构建数量与筛选结果
│   │   ├── visualization/            # 静态构建 PNG，不含策略执行
│   │   └── scene-files/<scene_id>/   # 场景快照
│   │       ├── model.mjb
│   │       ├── initial.npz
│   │       └── config.json
│   ├── panda-current/               # 当前 Panda：1 个 episode
│   └── ...                          # 其余历史或不完整标定来源
└── benchmark_evaluations/
    └── rby1-full-v0.1/
        ├── config.json              # 策略与评测配置
        ├── results.jsonl            # 逐 episode 结果
        ├── summary.json             # 成功率及分组汇总
        ├── visualization/            # 本次评测组合 PNG
        └── rollouts/<episode_id>/
            ├── trajectory.npz       # 实际状态与底盘轨迹
            ├── videos/              # head.mp4、third_person.mp4
            ├── images/              # 已有运行保留；新评测不默认保存
            ├── model_input_0.png    # 实际策略输入图片
            ├── model_input_1.png
            ├── model_input_2.png
            ├── policy_instruction.json
            └── recording.json
```

`episodes.jsonl` 每行一个 episode，包含 `episode_id`、`scene_id`、`scene`、`task`、`station`、`seed`。`station` 定义初始站位 S0；恢复目标和最短路径不写入公开 episode。

`oracle.json` 保存高成功率站位、恢复区域、可达路径及 Path Length、Recovery Ratio、Detour Factor。评测汇总以 Task Success Rate 为主，另报告 Recovery Success、底盘运动、按任务/失败类型分组结果及 Macro Average。

## 3. 已有可视化与结果

真实 RBY-1 评测已保存三个相机的逐帧 PNG：第一个 episode 每个相机 401 帧，第二个每个相机 15 帧。可以直接查看[实际头部相机模型输入](../../../outputs/benchmark_evaluations/rby1-full-v0.1/rollouts/e000000/model_input_1.png)。

当前按最新要求输出 PNG，不生成 HTML。每个 benchmark / 评测目录下的 `visualization/<episode_id>.png` 是单条图片，`summary.png` 是汇总。可查看 [benchmark 静态图片](../../../outputs/benchmarks/all-existing-v0.1/rby1-current/visualization/e000000.png)和[评测组合图片](../../../outputs/benchmark_evaluations/rby1-full-v0.1/visualization/e000000.png)。前者不含实际执行信息；后者展示实际轨迹、结果和同一最终时刻的 head / 第三人称截图。完整双视角 MP4 单独保存在 rollout 中，已有两条 RBY-1 rollout 共四个视频保持不变。

当前版本共有 RBY-1 2 条、Panda 1 条 episode；历史来源单独保存，不视为新增独立样本。RBY-1 实际策略评测覆盖率 100%，Task Success 与 Recovery Success 均为 0/2。当前没有可复用的 Open 标定，Panda 的 VLA 策略尚未接入。
