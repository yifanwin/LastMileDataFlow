# 仓库协作指南

## 工作方式

- 修改前阅读相关模块及测试，优先做范围明确的增量修改，避免无关重构。
- 开始工作时检查 `git status --short`；保留用户已有的修改及未跟踪文件，不覆盖、不回滚。
- 完成较复杂任务时先提供 Markdown 总结报告，说明执行过程、结果、验证情况和剩余限制。
- 不把规划、简化 fixture、规则判断或未实际执行的代码描述为已完成的真实验证。

## 项目定位与资料入口

LastMileDataFlow 是独立的五阶段机器人数据采集工程，使用 Python 3.11+、MuJoCo、NumPy、imageio 与 ffmpeg。
阶段一底座、阶段二规则式场景构建已有文档和回归；阶段三存在开发中的代码，应按实际实现与证据判断能力，不能仅凭目录存在宣称完成。

按任务查阅：

- `README.md`：运行入口、依赖、当前能力和真实验收命令。
- `docs/architecture.md`：模块职责、依赖方向、五阶段设计。
- `docs/data-format.md`：动作、attempt、状态与快照契约。
- `docs/phase1.md`、`docs/phase2.md`：已交付阶段的运行与边界。
- `configs/README.md`、`tests/README.md`：配置与验证分工。
- `reports/`：阶段交付和运行证据，不作为运行输入。
- `数据采集管线方案与计划.md`：用户原始方案；未经明确要求不修改。

文档可能落后于开发中的代码。发现冲突时核对实现、测试与报告，明确标注已交付、开发中和未验证的区别。

## 目录职责与代码约定

- `src/lastmile_dataflow/` 为源码；采用现有四空格缩进、模块划分和命名风格。
- `cli.py` 保持薄命令入口，`config.py` / `io.py` 管理共用配置与序列化。
- `scenes/`：场景来源、几何、实例映射与初始化；`robots/`：动作语义、限位与机器人适配。
- `runtime/`：仿真生命周期、实际 stepping、快照及构建期编辑会话。
- `recording/`：轨迹、图像、视频、事件和原始证据；`validation/`：独立的物理、协议与任务检查。
- `catalog/`、`construction/`、`agents/`：检索、编辑候选、受限结构化决策。
- `stations/`、`planning/` 及其余领域目录：按各自职责扩展；跨模块编排放在 `workflows/`。
- `integrations/` 只做外部格式转换，不包装旧工程运行代码。
- `configs/` 保存显式 JSON 配置；路径以配置文件所在目录为基准。
- `tests/` 保存单元/故障回归和显式真实 smoke；`fixtures/` 不放大型模型或视频。
- `outputs/` 保存采集产物，已被 Git 忽略；勿提交虚拟环境、缓存、大型资产或运行输出。
  统一布局：`outputs/logs/` 放全部 `*.log`/`*.pid`/`*.txt` 运行日志（唯一日志入口，不要再往 `outputs/` 根目录落日志），
  `outputs/no_edit/<run>/` 放采集产物，`outputs/diagnostics/` 放诊断/验收 run，`outputs/dependencies/` 放环境与缓存证据；
  散落日志用 `scripts/tidy_outputs.sh` 归集。

算法属于领域模块，编排属于 `workflows`，真实运动属于 `runtime`。不要按 case 复制仿真、机器人和记录代码，也不要把 `runtime/runner.py` 扩成全阶段巨型入口。

## 必须保持的约束

1. 不导入、不调用、不 source `lastmile_pipeline`，不依赖旧人工审批或历史成功标签。
   相邻 ProcTHOR/机器人资产是只读外部数据，不是 Python 工程依赖。
2. 不修改原始 XML、外部资产、旧工程或既有采集结果。attempt 使用唯一 ID，禁止覆盖。
3. 机器人位姿只允许在初始化阶段设置。`Simulation.begin()` 后禁止恢复快照、重启或传送；独立试验必须创建新 Simulation 和新 attempt。
4. 场景编辑仅通过构建会话的事务、静置、检查和回滚机制，不绕过连续执行约束。
5. 动作遵循集中定义的 RBY-1 20D 协议：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
   底盘/双臂为增量，夹爪/躯干为绝对量；具体语义与合法范围查 `robots/action.py` 和机器人配置。
6. 场景构建、case 成立、机器人任务完成分别给结论。`execution_complete` 不表示抓取或导航成功；未知结果保持 `unknown`。
7. 规划无解、基础设施异常、未测试和真实执行失败不得混淆；无实际执行时不生成虚假执行轨迹或失败视频。
8. Agent 只能返回经程序验证的结构化建议/动作，不直接取得仿真编辑权限，不覆盖硬检查或调整物理阈值。
9. 预算、种子、配置摘要、场景/检查点身份和输入输出证据应可追溯。失败证据保留，下游反馈追加，不篡改原结果。

## 安装、运行与验证

所有命令从仓库根目录执行。优先使用本项目环境；相邻环境只是本机已有解释器的可选来源。

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .

# 离线单元回归：无需真实资产或 GPU，但需要 Python 依赖
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v

# 源码命令入口
DATAFLOW_PYTHON=.venv/bin/python bin/lastmile-dataflow --help
DATAFLOW_PYTHON=.venv/bin/python bin/lastmile-dataflow run --house 2 --steps 6
DATAFLOW_PYTHON=.venv/bin/python bin/lastmile-dataflow audit outputs/attempts/<id>
```

- `bin/lastmile-dataflow` 设置源码路径，默认 `MUJOCO_GL=egl`；解释器由 `DATAFLOW_PYTHON` 指定。
- GPU 编号通过 `MUJOCO_EGL_DEVICE_ID` 显式配置，按实际资源选择，不在代码中硬编码。
- 外部资产位置通过机器人配置、`--dataset-dir` 或其他显式参数指定，不假定每台机器存在本机默认路径。
- 真实验收使用 `tests/real_smoke.py` / `tests/phase2_real_smoke.py`，按 README 与阶段文档准备资产和参数；它们不随 unittest 自动执行。
- 优先运行改动相关测试，再运行全套回归。修改动作、快照、事务、异常分类或输出契约时补充相应回归测试。
- 简化模型测试只证明程序逻辑；真实 GPU/模型/视频验收须单独报告。未运行、依赖缺失或基础设施失败时如实说明，不降级伪造通过。
- 修改公开接口、配置或数据格式时同步相关文档，明确版本与兼容性；新增依赖同步 `pyproject.toml`。
