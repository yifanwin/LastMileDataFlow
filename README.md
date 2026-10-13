# LastMileDataFlow

## 工作区分支关系

目前.worktrees的上下游分支关系

```
scenes-datasets-design
├── lastmile-datasets
│   └── model-train
└── lastmilebench-design
```

## 当前状态（2026-10-13）

**原定五阶段数据采集管线目前属于失败、半废弃状态，不是已交付或持续推进中的完整可用管线。**
历史阶段代码、测试和报告保留供参考与局部复用；

当前独立入口包括无场景编辑采集、程序化障碍物增强和 MolmoBot 站位评测；它们不代表原五阶段管线恢复，
各自可用范围及真实结果仍以对应文档和 run 证据为准。旧场景编辑与阶段入口保留，不作为推荐的完整采集路线。

## 独立采集与评测入口

当前命令入口见[运行手册（cuRobo + MolmoBot 合并版）](docs/运行手册.md)；其中本机 worktree、环境和 GPU 参数须按实际部署调整。
数据契约集中于 `docs/datas/`，历史设计与旧运行说明集中于 `docs/archives/`。

独立 `build-scene-datasets` / `collect-lastmile-datasets` 已切换到 [Research 数据协议](docs/datas/research-data-protocol.md)：
保留构造期物理与 case 检查、连续执行和采集期数据语义检查，删除默认全量复审、嵌套封存与重复 episode 索引。
`episode.json` 是实际 attempt 完成记录；`episodes.jsonl` 为唯一全局视图，`summary.json` 可重建。
新 run 使用新 ID；旧数据与历史报告保留，不自动迁移。入口存在不代表真实 CUDA 导航／抓取闭环已验证。

纯程序化障碍物增强使用独立 `augment-obstacles` 入口，不调用 Agent；配置位于 `configs/case_edits/procedural_obstacles_*.json`。
构造与审计命令见[增强场景构造运行指南](docs/增强场景构造运行指南.md)。
最新 [稠密站位 Case1 / Case2 验收报告](reports/obstacle_augmentation/2026-10-11-dense-cases/REPORT.md)：各 5 份、60 张真实 RGB；全 161 点独立初始化与覆盖压缩已验证，导航／抓取未验证。

可选 [MolmoBot VLA 站位操作评测（归档说明）](docs/archives/molmobot-station-evaluation.md)：
`configs/no_edit/molmobot.json`，2 m / 每点5次、原生 motor 夹爪、1024×576 + upstream
头部鱼眼、独立物理验收及热图。仅站位评测，不调用 cuRobo 或 A*；真实状态以 run 为准。

独立的[原始 val 场景无编辑采集（归档说明）](docs/archives/no-edit-collection.md)：`collect-no-edit` 枚举 pick/open，
执行 2 m 均匀站位评测、Gaussian 成功率热图与 A* 连续移动操作。
操作由 cuRobo 联合规划底盘 x/y/yaw 与单臂；连续移动+操作及 S0 真实失败操作保存 head、双夹爪、第三人称分析四路视频，并记录整体生成时间。新 run 使用物体语义目录名、pick/open 站位分层、成功/失败快捷入口和独立 `datasets/` rollout 包；地图提供普通版及逐点站位标注版，不生成 HTML。
至少 **1 段**真实成功示教即可纳入数据集，优先采 **3 段**。旧场景编辑入口保留不变。
源码/单元测试通过不表示全量场景或真实 open/导航已验证；实际状态看对应 run 的 `summary.json`。

## 历史五阶段设计与局部实现

本工程原计划构建完整五阶段数据采集管线，但该路线目前已失败、半废弃。历史代码包含第一阶段底座、第二阶段规则式场景构建与第三阶段 case1 固定底盘成败采集的局部实现：原始场景加载、RBY-1 20 维动作、
可回滚编辑/静置验收、任务候选冻结与独立恢复、轨迹/相机/视频/结果记录。

- [代码导览（初学者版）](docs/代码导览.md)：代码树、架构图、数据流图、流程图、时序图与推荐阅读顺序。
- [Case 构造完整设计](docs/archives/case-to-edit-pipeline.md)：四个 Agent、局部上下文、观察基准、资产与独立任务验收。
- [Case 编辑运行说明](docs/archives/case-edit-pipeline.md)：唯一 `case-edit` 入口和单场景请求。
- [完整架构与目录职责](docs/architecture.md)：五阶段数据流、功能边界、依赖方向和后续扩展目录。
- [第一阶段运行与验收](docs/archives/phase1.md)：实现范围、验证方式和限制。
- [配置与数据格式 v1](docs/datas/data-format.md)：动作语义、attempt 格式、状态分类和快照协议。
- [cuRobo Raw-first 统一采集协议](docs/datas/canonical-data-protocol.md) · [使用说明](docs/datas/raw-first-implementation.md)：新采集默认无损 RGB/depth、完整状态与实时标定；仅实现 MolmoBot Adapter，旧 20D 接口保留，CUDA/VLA 端到端待验收。
- [阶段二运行、v2 构建配置与边界](docs/archives/phase2.md) · [阶段二交付总结](reports/archives/phase2/v1.0/phase2-delivery.md)。
- [阶段三站位图、原生 cuRobo 与成败采集](docs/archives/phase3.md) · [阶段三交付报告](reports/archives/phase3/phase3-delivery.md)。
- [阶段一交付总结](reports/archives/phase1/phase1-delivery.md)。

**不依赖 `lastmile_pipeline` 的代码、运行入口、配置、人工批准或历史输出。**
仅借鉴旧工程的控制/快照思路；旧样例以小型输入 fixture 的形式保留一次性转换回归。
MuJoCo、NumPy、imageio/ffmpeg、jsonschema 是显式第三方依赖；ProcTHOR 与机器人模型是只读外部资产。

## 历史底座入口（局部运行，不代表管线可用）

从 `LastMileDataFlow/` 执行：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
DATAFLOW_PYTHON=.venv/bin/python bin/lastmile-dataflow run --house 2
```

本机已有具备依赖的环境也可直接指定，不需要 source 相邻工程：

```bash
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python \
  MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 \
  bin/lastmile-dataflow run --house 2 --steps 6
```

`MUJOCO_EGL_DEVICE_ID` 请按实际空闲 GPU 设置；入口不硬编码 GPU。
默认配置使用相邻 `molmospaces_data/assets`，这是本机的资产位置，不是 Python 工程依赖。
更换部署位置时修改 `configs/robots/rby1.json` 的模型路径，或传入新的机器人配置；房屋用 `--dataset-dir` 指定。

```bash
# 原始场景，不需要 benchmark episode，也不绑定 Cup_30 或某个 grasp
bin/lastmile-dataflow run --house 0 --dataset-dir /path/to/procthor-10k-train \
  --robot-config /path/to/robot.json --task-config configs/tasks/foundation.json \
  --collection-config configs/collection/smoke.json

# 自定义 MJCF，显式选择底盘初态与目标（世界 x,y,yaw）
bin/lastmile-dataflow run --scene-xml /path/to/house.xml \
  --metadata /path/to/house_metadata.json --base 2 3 0 --target <instance-id>

# 使用前一次 scene 初态创建全新的独立试验，不在同一轨迹中重置
bin/lastmile-dataflow run --snapshot outputs/attempts/<id>/scene \
  --target <instance-id> --attempt-id new-independent-attempt

# 检查文件、配置摘要、轨迹连续性与视频对应
bin/lastmile-dataflow audit outputs/attempts/<id>
```

需要使用上述源码入口时先设置 `DATAFLOW_PYTHON`，或激活安装依赖的环境。
pip 的安装入口为 `lastmile-dataflow`；非 editable 安装时显式传入配置路径。
`--no-video` 仅关闭 MP4，仍保留 RGB 图像；渲染环境缺失会作为基础设施异常记录，不能伪造视频通过。

## 输出与结果

每次生成 `outputs/attempts/<唯一ID>/`：冻结配置、完整模型/初态、实例映射、实际轨迹、三相机图像/可选 MP4、
最终现场、异常事件、三个独立结果和原子 episode 完成记录。已有 attempt 不允许覆盖。
源 XML、原始资产及旧工程不修改。

`execution_complete` 只表示有界短动作执行完毕；case 条件与任务完成在本阶段为 `unknown`，
不表示抓取、把手抓取或导航成功。加载错误、无效初始化、非法控制、真实物理异常和记录设施异常分别保存。

### 输出目录约定

`outputs/` 已被 Git 忽略，统一按用途分目录，**不要再往 `outputs/` 根目录直接落文件**：

| 目录 | 内容 |
|---|---|
| `outputs/logs/` | **全部** `*.log` / `*.pid` / `*.txt` 运行日志（启动、回归、诊断、CLI 帮助）。唯一日志入口。 |
| `outputs/no_edit/<run>/` | `collect-no-edit` 采集产物，每个 run-id 一个目录。 |
| `outputs/diagnostics/` | 诊断与验收 run，每个 run 一个子目录（含 `v080-pilot/`）。 |
| `outputs/dependencies/` | 环境与缓存证据：`warp-cache/`、`mpl/`、`uv-cache/`、`environment-v080.json`、安装日志。 |

新跑批的 stdout 请 `tee` 到 `outputs/logs/<run-id>.log`；已有散落文件用 `scripts/tidy_outputs.sh`
一键归集（幂等，可重复执行，只移动 `outputs/` 根目录下的日志类文件，不覆盖同名文件、不触碰 run 目录）。

## 验证

```bash
# 无需 GPU、无需真实资产的代码回归；简化模型只用于测试程序逻辑
PYTHONPATH=src ../molmospaces/.venv/bin/python -m unittest discover -s tests -v

# 可选：转换已随新工程保存的小型旧样例输入，不读取旧工程运行代码
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow import-legacy \
  --episode tests/fixtures/legacy_case1_episode.json \
  --assets-dir ../molmospaces_data/assets --output outputs/imports/legacy_case1.json

# 显式真实仿真：两所原始房屋、独立快照恢复、旧样例初态回归与三相机视频解码
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python tests/real_smoke.py --houses 0 2 \
  --legacy-import outputs/imports/legacy_case1.json
```

真实验证每个 attempt 最多 6 步，不重跑旧抓取/导航，不继承旧成功标签。
历史阶段记录包含第二阶段规则式编辑、严格 Agent 协议及 Cup 静置图外部视觉模型语义复查。
第三阶段记录包含 case1 原生 cuRobo、站位图、真实成败采集和浏览导出；case2/3/1.5 的真实任务覆盖仍未知。
原五阶段路线未形成完整闭环，第四、五阶段未按原设计交付；独立入口中的 VLA 评测或连续移动操作不等同于这些阶段完成。
**上述局部记录不改变原五阶段管线失败、半废弃的当前结论。**

### 运行时接触策略（2026-10-11）

所有执行后端使用 `runtime-record-only-v1`：MuJoCo 求解真实接触，接触/穿透本身不在线终止或判任务失败。
初始化仍过滤非法重叠；仿真数值异常单列 `simulation_failure`，不计入任务成功率。
详见[接触策略及证据](reports/no_edit/contact-record-only-20261011.md)。
