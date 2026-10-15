# 仓库协作指南

## 项目目标与边界

LastMileDataFlow 是面向快速科研实验的机器人数据工程：
**场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测**。

- 只维护五个 CLI 命令：`build-scenes`、`evaluate-stations`、`collect-lastmile`、`build-bench`、`evaluate-bench`。
- 规划固定 cuRobo V2（0.8.0），策略为 MolmoBot multitask，导航为 A*。连续采集当前仅支持 cuRobo。
- 旧五阶段路线已退出当前实现，不自行恢复旧入口、人工审批、审计发布体系或历史格式兼容。
- 不导入、不调用、不 source `lastmile_pipeline`；相邻工程的资产不是 Python 运行依赖。
- 当前能力与未验证边界查 README 和 `reports/README.md`；历史局部成功不证明当前主链闭环。

## 工作方式：最小实现，直接表达

- 开始时检查 `git status --short`，保留用户已有修改和未跟踪文件，不覆盖、不回滚。
- 修改前阅读相关实现与测试，优先范围明确的增量修改，不顺带扩展功能。
- 先删无用逻辑、合并重复处理，再整理目录；不以移动文件或压缩排版冒充精简。
- 外部配置、资产、模型响应在边界做必要检查；内部按明确约定直接使用，不逐层重复校验和兜底。
- 保留实际物理约束、任务判定、资源关闭与批量实验失败隔离；不穷举低价值异常组合。
- 不添加通用校验框架、封存协议、重复索引、内部旧路径兼容壳或无实际职责的包装。
- 不生成文件指纹或逐文件校验清单；实验只保存必要的有效配置、seed、输入引用与真实结果。
- 代码保持四空格缩进、清晰命名和适度分层；不为每个概念建包，不按 case 复制底层实现。
- 较复杂任务结束时提供 Markdown 总结，说明实际改动、验证、结果和限制；不为每个小步骤堆积报告附件。

## 资料入口与维护

- [README.md](README.md)：当前能力、五个命令与阅读入口。
- [docs/setup.md](docs/setup.md)：独立环境、数据准备与路径规则。
- [docs/运行手册.md](docs/运行手册.md)：构建、标定、连续采集和 benchmark。
- [docs/architecture.md](docs/architecture.md)：源码职责与实际调用链。
- [docs/data-format.md](docs/data-format.md)：主记录、轨迹、动作与结果含义。
- [configs/README.md](configs/README.md)、[tests/README.md](tests/README.md)：配置和验证分工。
- [reports/README.md](reports/README.md)：当前验证边界；`reports/REPORT.md` 保存最近一次整理结果。

当前说明只维护必要主题，不把实施步骤当作运行指南。`docs/archives/`、`reports/archives/` 是历史文本，不作为运行输入或当前能力承诺。
整理旧资料时先提取仍适用知识，再归档过时说明；历史生成附件不继续堆积。涉及真实采集数据、权重或外部资产的删除必须有明确授权，不能因它们被 Git 忽略就清理。
修改公开命令、配置或数据格式时同步当前说明；历史结论不改写为当前验证结果。

## 目录职责

源码位于 `src/lastmile_dataflow/`，六个领域目录如下：

| 目录 | 职责 |
|---|---|
| `scenes/` | 场景与资产加载、任务发现、站位采样、障碍物增强；子目录为 `loading/tasks/stations/augmentation` |
| `runtime/` | 实际 stepping、控制、构建事务、执行与任务判定；子目录为 `control/construction/execution/outcomes` |
| `planning/` | cuRobo V2、机器人规划适配、碰撞/FOV 和 A* |
| `policies/` | MolmoBot 配置、客户端/服务及 `multitask/` 必要推理代码 |
| `data/` | `recorder.py` 与 `evaluation/export/rendering/visualization`，负责记录、统计、导出和图像 |
| `workflows/` | 场景构建、逐点标定、连续采集和 benchmark 的薄编排 |

包根 `cli.py` 只做命令分发，`config.py` / `io.py` 管理共用配置与读写。
算法放领域模块，编排放 `workflows`，实际运动放 `runtime`；不另起平行执行器或记录链路。

- 根目录 `configs/`：显式 JSON 配置；解析规则见下节。
- 根目录 `data/`：可选的本地资产、权重和 tokenizer 存放位置，不是源码；不需要克隆整个 MolmoBot 仓库作为运行依赖。
- `tests/`：按 `scenes/runtime/planning/policies/data/workflows` 分组；`helpers.py` 保存必要共享 fixture，`smoke/` 保存显式真实验证脚本。
- `outputs/`：运行产物，默认日志写 `outputs/logs/`，诊断放 `outputs/diagnostics/`；自定义输出根目录时日志放其 `logs/`，不散落在根目录。
- 不提交环境、缓存、大型资产或运行产物；根目录数据忽略规则保持 `/data/`，不能误忽略源码和测试中的同名目录。

## 环境与路径

- 使用本仓库 `.venv`，不借相邻 worktree 的环境、Python 包或 `.pth`。依赖统一声明在 `pyproject.toml`。
- `data_root` 在含数据输入的配置中显式填写，绝对路径或相对仓库根目录均可。
- 数据、快照、权重、tokenizer 路径相对于 `data_root`；配置文件和解释器路径相对于仓库根目录，也可使用绝对路径。不是相对于当前 JSON 文件位置。
- CLI 文件系统参数按调用工作目录解释；从其他目录调用时优先传绝对路径。
- 各配置加载器明确解析所需字段，不递归扫描嵌套记录。已有运行配置不自动重定位，搬迁后显式调整输入位置并重建环境。
- MJB 恢复要求匹配生成时的 MuJoCo 版本；不能把可查看的 XML 宣称为已验证的独立可搬迁资产包。
- GPU 0–7 按实时利用率与空闲显存选择，CLI 显式传 `--gpu <index>`；不在代码中固定卡号，不干预其他进程。

## 必须保持的实验语义

1. 原始 XML、外部资产和既有采集结果只读。每次新建 run/attempt，禁止覆盖，不伪造失败或成功证据。
2. 机器人位姿只在初始化阶段设置。`Simulation.begin()` 后不恢复快照、重启、重置或传送；独立试验创建新 Simulation 和 attempt。
3. 场景编辑走构建会话的事务、静置、检查与回滚；保留数量、碰撞、支撑、源场景影响和站位有效性约束，放不下就记 rejected。
4. RBY-1 20D 动作布局：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
   底盘/臂为增量；position 控制的夹爪/躯干为绝对目标。native motor 夹爪按实际适配器解释，不把力控值当位置或米。定义见 `runtime/control/`。
5. position 与 native 控制、相机和标定 profile 不混用。场景采样只检查初始化与可见性，逐点成败必须来自实际 rollout。
6. 场景构建、导航到达、操作成功、执行终止和记录完整分别报告。未知保持 unknown，退出码 0、文件存在或 `execution_complete` 都不代表任务成功。
7. 规划无解、超时、初始化/仿真/记录错误与共享模型/GPU 故障分开处理；共享故障停止当前批次，不继续消费失效服务。
8. 成功率读取原始 trial，不从平滑热图选困难起点。不完整或不匹配的标定不用于困难配对；无配对、无路径是有效负结果，不补造 rollout。
9. benchmark 同时报告覆盖率与完整计划集合的成功率；基础设施错误或未知结果不能冒充任务失败，未标定 smoke 不能冒充困难 S0 评测。
10. 每阶段一个主 JSON/JSONL，复用已有标定，不复制第二套 trial；轨迹由实际执行回调记录一次，无真实物理步就不生成执行行。

## 安装与验证

以下命令从仓库根目录执行：

```bash
scripts/install.sh
.venv/bin/lastmile-dataflow --help

# 离线回归；无需真实资产、权重或 GPU 任务
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v

# 改动相关领域或文件时先跑局部测试
.venv/bin/python -m unittest discover -s tests/scenes -t . -v
.venv/bin/python -m unittest tests.data.test_recording -v
```

- 先跑相关测试，再跑全套回归；测试跟随现有行为，不为已删除功能或内部旧路径保留回归。
- 四个显式真实入口位于 `tests/smoke/`：`curobo_rollout.py`、`head_fov.py`、`multitask.py`、`pipeline.py`。先看 `--help`，按测试说明准备资产、权重和 GPU；不随 unittest 自动执行。
- 简化模型、mock、import 和 `--help` 只验证各自软件边界。真实 GPU rollout、权重推理、视频或端到端任务必须单独执行并如实报告；未运行和依赖缺失不能降级伪造为通过。
- 不因整理文档或软件回归通过，就升级真实任务成功、数据可复现性或主链闭环的结论。
