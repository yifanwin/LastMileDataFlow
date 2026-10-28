# 仓库协作指南

## 项目目标与边界

LastMileDataFlow 是面向快速科研实验的机器人数据工程：
**场景增强与采样 → 场景库 → 逐点成败图 → S0→S1→操作 / 困难 S0 评测**。

- 只维护五个 CLI 命令：`build-scenes`、`evaluate-stations`、`collect-lastmile`、`build-bench`、`evaluate-bench`。
- 规划固定 cuRobo V2（0.8.0），策略为 MolmoBot multitask，导航为 A*。连续采集当前仅支持 cuRobo。
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
- [reports/README.md](reports/README.md)：报告目录约定与当前验证边界；已有 `reports/REPORT.md` 保留最近一次整理结果。

`reports/` 按主题组织，每个主题内部按文档用途分类，目录按需创建：

- `requirement/`：需求文档。
- `plan/`：方案、计划文档。
- `delivery/`：结果、交付文档。
- `analyze/`：分析文档。

尚未明确分类的文档先放在所属主题根目录，例如 `reports/build-lastmile-datasets/`，不另建 `other/`。已有 `reports/REPORT.md` 和 `reports/archives/` 暂时保留，不因分类规则批量搬迁历史文档。

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
- `outputs/`：运行产物，细则见 [outputs/outputs_rules.md](outputs/outputs_rules.md)。硬规则：
  1. 顶层只有 `campaigns/`（按配方批量运行）、`experiments/`（少数「场景 + 目标物体」上的定位与对照；临时试跑放 `experiments/scratch/`，7 天内转为正式主题或删除）、本地索引 `README.md`，以及 `outputs_rules.md`。
  2. 每个 campaign 或实验主题自成一体，带 `README.md`（用途、状态、代码提交号、对应报告）；子目录只用 `src/ inputs/ runs/ replays/ views/ analysis/ logs/`（campaign 另有 `state.json`），按需创建。
  3. 每个目录的直接子目录不超过 25 个，超出时按序号（`c000-c009`）或对照组分组；名称只用小写字母、数字、连字符和 `YYYYMMDD`，不复用。
  4. 运行期间代码不变：先提交并记录提交号，从 `git archive` 导出的 `src/` 或固定在该提交上的 worktree 启动；运行中替换任何文件都要在该单元的 README 登记。
  5. 结束、作对照保留、被替代，都只改 README 里的状态，不移动目录；有进程在读写时，不移动、改名或删除该单元、它所在的 worktree 以及 `outputs/`；确需搬迁时按细则第 7 节执行。
  6. 删除前须经用户确认，并记入 `outputs/README.md`；不删被报告引用的结果、对照基线，以及已有 run 依赖的冻结输入。
- 不提交环境、缓存、大型资产或运行产物（`outputs/outputs_rules.md` 除外）；根目录数据忽略规则保持 `/data/`，不能误忽略源码和测试中的同名目录。

## 环境与路径

- 使用本仓库 `.venv`、Python 包或 `.pth`。依赖统一声明在 `pyproject.toml`。
- `data_root` 在含数据输入的配置中显式填写，绝对路径或相对仓库根目录均可。
- 数据、快照、权重、tokenizer 路径相对于 `data_root`；配置文件和解释器路径相对于仓库根目录，也可使用绝对路径。不是相对于当前 JSON 文件位置。
- CLI 文件系统参数按调用工作目录解释；从其他目录调用时优先传绝对路径。
- 各配置加载器明确解析所需字段，不递归扫描嵌套记录。已有运行配置不自动重定位；`outputs/` 内的产物搬迁按 `outputs/outputs_rules.md` 第 7 节改写路径，其他位置搬迁后显式调整输入位置并重建环境。
- MJB 恢复要求匹配生成时的 MuJoCo 版本；不能把可查看的 XML 宣称为已验证的独立可搬迁资产包。
- GPU 0–7 按实时利用率与空闲显存选择，CLI 显式传 `--gpu <index>`；不在代码中固定卡号，不干预其他进程。

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
