# LastMileDataFlow

新增独立的[原始 val 场景无编辑采集](docs/no-edit-collection.md)：`collect-no-edit` 枚举 pick/open，
执行 2 m 均匀站位评测、Gaussian 成功率热图与 A* 连续移动操作。
操作由 cuRobo 联合规划底盘 x/y/yaw 与单臂；连续移动+操作及 S0 真实失败操作保存 head、双夹爪、第三人称分析四路视频，并记录整体生成时间。
至少 **1 段**真实成功示教即可纳入数据集，优先采 **3 段**。旧场景编辑入口保留不变。
源码/单元测试通过不表示全量场景或真实 open/导航已验证；实际状态看对应 run 的 `summary.json`。

面向**完整五阶段数据采集管线**的独立工程，当前实现第一阶段底座、第二阶段规则式场景构建与第三阶段 case1 固定底盘成败采集：原始场景加载、RBY-1 20 维动作、
可回滚编辑/静置验收、任务候选冻结与独立恢复、轨迹/相机/视频/结果记录。

- [无场景编辑数据构造运行手册](docs/无场景编辑数据构造运行手册.md)：val103/单目标/全量启动、四路视频检查、停止续跑与故障排查。
- [代码导览（初学者版）](docs/代码导览.md)：代码树、架构图、数据流图、流程图、时序图与推荐阅读顺序。
- [Case 构造完整设计](docs/case-to-edit-pipeline.md)：四个 Agent、局部上下文、观察基准、资产与独立任务验收。
- [当前实施计划](docs/case-to-edit-pipeline-plan.md)：目标 case1、case1.5、case3；构造通过不表示真实移动操作验证通过。
- [Case 编辑运行说明](docs/case-edit-pipeline.md)：唯一 `case-edit` 入口和单场景请求。
- [完整架构与目录职责](docs/architecture.md)：五阶段数据流、功能边界、依赖方向和后续扩展目录。
- [第一阶段运行与验收](docs/phase1.md)：实现范围、验证方式和限制。
- [配置与数据格式 v1](docs/data-format.md)：动作语义、attempt 格式、状态分类和快照协议。
- [阶段二运行、v2 构建配置与边界](docs/phase2.md) · [阶段二交付总结](reports/phase2/phase2-delivery.md)。
- [阶段三站位图、原生 cuRobo 与成败采集](docs/phase3.md) · [阶段三交付报告](reports/phase3/phase3-delivery.md)。
- [阶段一交付总结](reports/phase1/phase1-delivery.md)。

**不依赖 `lastmile_pipeline` 的代码、运行入口、配置、人工批准或历史输出。**
仅借鉴旧工程的控制/快照思路；旧样例以小型输入 fixture 的形式保留一次性转换回归。
MuJoCo、NumPy、imageio/ffmpeg、jsonschema 是显式第三方依赖；ProcTHOR 与机器人模型是只读外部资产。

## 快速运行

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

每次生成 `outputs/attempts/<唯一ID>/`：冻结配置、完整模型/初态、实例映射、实际轨迹、三相机图像/MP4、
最终现场、异常事件、三个独立结果和文件摘要。已有 attempt 不允许覆盖。
源 XML、原始资产及旧工程不修改。

`execution_complete` 只表示有界短动作执行完毕；case 条件与任务完成在本阶段为 `unknown`，
不表示抓取、把手抓取或导航成功。加载错误、无效初始化、非法控制、真实物理异常和记录设施异常分别保存。

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
第二阶段规则式编辑与严格 Agent 协议已实现；本次 Cup 静置图已完成外部视觉模型语义复查。
第三阶段 case1 原生 cuRobo、站位图、真实成败采集和浏览导出已实现；case2/3/1.5 的真实任务覆盖仍未知。
第四、五阶段的 VLA、连续导航示教和批量发布仍未实现。
