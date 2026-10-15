> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 轻量化步骤一、二执行报告

**步骤一、二已执行：确认核心共享依赖，删除历史专用入口与实现。未开始协议缩减、采集合并、目录迁移或独立环境重建。**

- 日期：2026-10-14。
- 工作树：`LastMileDataFlow/.worktrees/refactor-light`；分支：`refactor/light-all`。
- 范围依据：[实施方案](2026-10-14-refactor-light-implementation-plan.md)步骤一、二。
- 保留了开始时已有的实施方案修改；未修改 `AGENTS.md`，未提交 Git commit。

## 1. 先确认哪些是核心，哪些只是历史调用者

从五个核心 workflow 读取直接调用和模块依赖，再检查动态 worker、模型服务、脚本和混合测试，避免把“没有顶层 import”误判为无用。

| 核心模块 | 保留的真实职责 |
|---|---|
| `scene_datasets` | 发现任务、初始化资格检查、构造单元、组织场景包；Case1/2 调用障碍物增强，Case3 保留 open 场景 |
| `obstacle_augmentation` | 资产选择与放置、构造期编辑/静置、支撑和碰撞检查、增强前后站位比较 |
| `no_edit` | 逐点 cuRobo / MolmoBot 执行、成败统计和热图；cuRobo 分支的 A* 与连续操作 |
| `lastmile_datasets` | 遍历场景包并调用 `no_edit.collect_batch`；当前输入和输出协议暂时不改 |
| `lastmilebench` | 从场景包和显式标定选起点，直接调用 VLA 执行器评测 |

共享场景加载、机器人动作、仿真、任务判定、规划、导航和记录保留。

特别处理了三处：

1. `runtime/build_session.py` 的历史 `BuildSession` 类已删除，但 `migrate_state` 仍供障碍物编辑使用，两个快照身份函数仍供现有快照 loader 使用，原地保留到步骤三。
2. `planning/curobo.py` 暂时保留：v2 仍调用其中位姿工具；本轮不改规划路线。
3. `policies/molmobot_service.py` 与 `workflows/native_station_input.py` 保留：前者由子进程启动，后者仍被 native 输入适配脚本使用。

## 2. 实际删除了什么

删除 **115 个已跟踪文件**。按用途清理，没有迁移到新的 archive 或保留兼容壳。

| 范围 | 删除内容 |
|---|---|
| 旧公开入口 | `run/build/index/search/feedback/station-map/case-edit/vision-review/import-legacy/export-stations/render-delivery` |
| 审计/复核 CLI | `audit/audit-station/audit-canonical/audit-scene-datasets/review-lastmile-timeouts/index-canonical` |
| 专用源码 | 整个 Agent 包、旧构造 DSL/编译器/合约/模板、SQLite 检索、legacy adapter、旧 runner、旧固定底盘站位工作流、交付导出与独立 timeout 复核 |
| 专用配置 | `builds/`、旧 `stations/`、`tasks/`、Agent API、三种 Agent 场景请求及独立 settling/views 配置 |
| 专用测试 | Agent/DSL、legacy、五阶段及交付专用测试与 fixture，依赖旧 runner 的真实 smoke |
| 历史文档 | 旧五阶段/Agent 运行说明和代码导览；`reports/archives/phase1/phase2/phase3` 与原五阶段方案 |

CLI 从 **28 个子命令缩到 11 个**，其中包括构造 worker 内部命令和暂存的空 `no-edit-status` 分支。当前阶段不改核心命令名，不删除 Shell 包装；空状态分支将在 CLI 收敛时处理，不能当作可用的进度查询。

源码从 **137 个 Python 文件、19,495 行**降到 **96 个 Python 文件、13,165 行**。规模数字仅说明清理范围，不是科研正确性或真实任务成功的指标。

## 3. 为保留核心能力做了哪些局部修改

- `Simulation.from_source()` 删除 legacy `restoration` 参数和专用物体恢复分支；原始场景初始化和核心快照加载保留。
- `build_session.py` 删除历史类及它引入的 Agent、资产池、检索和旧 placement validator 依赖，只保留实际共享函数。
- 混合测试保留动作/仿真、静置、资产、图和相机几何；删除旧 runner、DSL、Agent 和审阅相关部分。
- 共享简化场景和 pick 证据从历史测试模块移入 `tests/helpers.py`，核心测试不再反向依赖已删除的 `test_stations`。
- 修复保留的 `augment-obstacles` CLI 分支缺少 workflow 导入的问题，并增加两个必要 CLI 回归：命令集合和增强分发。
- 更新 README、当前架构、配置/测试入口和模块说明；清除现行运行指南中已删除的命令，不把历史五阶段继续作为使用入口。

没有修改成功阈值、控制语义、导航或操作算法。没有新增摘要、封存或来源追踪系统。

## 4. 验证结果：没有新增失败，但不是全绿

| 验证 | 实际结果 |
|---|---|
| 源码/测试/脚本语法及本包模块引用检查 | 通过，没有残留指向已删除模块的 Python 导入 |
| 五个核心 workflow、native 输入适配和模型服务模块导入 | 通过；未加载策略权重、未启动真实 GPU 规划 |
| `python -m lastmile_dataflow.cli --help` | 通过；旧入口不再出现在命令集合中 |
| 保留测试全集 | **308 项：304 通过、2 失败、2 跳过**，无错误项 |
| 两个失败在改动前代码中的定向复现 | 两项均失败，断言与本轮相同 |
| `git diff --check` | 通过 |

已有失败如下，本轮不为让测试全绿而更改其语义：

1. `test_mid_execution_recorder_failure_classifies_as_recording_error`：测试期待 `recording_error`，现有实现返回 `episode_error`。
2. `test_only_requested_sampling_fields_differ_from_v080`：测试期待两个配置的 radius/spacing 不同，但当前基线配置的这两个字段相同。

跳过项分别为可选上游鱼眼对照，以及需要显式开启的实际 OpenGL 分割渲染。

测试解释器使用当前机器已有的 `no-edit-lastmile-val/.venv-curobo-v080/bin/python`。本轮只是复用它做回归，**没有把它新增成项目运行依赖，也没有完成独立环境重建**。基础系统 Python 缺少部分测试依赖；新环境属于步骤七。

测试日志：

- 本轮全集日志。
- 改动前两项失败复现日志。

日志属于本机忽略的运行输出，不作为发布协议或必须提交的报告附件。

## 5. 没有执行的工作与下一步

- 核心仍调用的 canonical 审计、快照/场景包身份检查、容量证书与索引保留到步骤三/四，不宣称已经全部符合轻量化原则。
- `no_edit` 与 `lastmile_datasets` 尚未合并，不产生 `collection.py` 或新主 JSONL；目录与当前模型格式暂不改变。
- 保留已有采集结果、源资产、非五阶段实验报告及必要输入适配脚本；只删除本工作树内明确属于步骤二的对象。
- `AGENTS.md` 中历史入口/文档指针仍待所有者同步，本轮按只读约定未修改。
- 未运行真实 ProcTHOR/RBY-1 采集、cuRobo CUDA、MolmoBot 推理或困难 S0 bench。

下一步按步骤三同步缩减生产者和消费者的数据协议，再解除现有审计与多层绑定依赖；不恢复已删除的历史入口。
