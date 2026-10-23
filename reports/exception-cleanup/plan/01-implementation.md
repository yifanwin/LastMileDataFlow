# 异常处理精简实施方案

- 状态：done
- 日期：2026-10-23
- 替代：无
- 关联：../../../AGENTS.md、../../README.md

## 目标与工作区

清理不必要的 `try/except/finally`，让内部错误直接暴露原始调用栈，减少吞错、默认值兜底、重复分类和嵌套收尾。保留实际资源释放、构建事务和批量实验失败隔离，不以减少关键字数量作为验收目标。

- 工作区：`.worktrees/exception-cleanup`
- 分支：`refactor/exception-cleanup`
- 基线：`ef9b8db`（创建时的 `main`）
- 范围：当前 `src/lastmile_dataflow/`、相关 `tests/` 和实际维护的脚本；不修改历史归档、外部资产、权重和既有实验数据。
- 方案编写阶段只建立工作区、梳理现状；后续已完成代码修改和离线回归，见[执行结果](../delivery/01-result.md)。

## 已确认的现状

对当前 Python 源码做 AST 统计，单位为语法节点；同一处 `try` 可以同时包含 `except` 和 `finally`。

| 源码范围 | 含 try 的文件 | try | except 分支 | finally | Exception/BaseException 捕获分支 |
|---|---:|---:|---:|---:|---:|
| runtime | 13 | 23 | 26 | 10 | 9 |
| scenes | 10 | 18 | 16 | 3 | 0 |
| data | 12 | 19 | 13 | 10 | 6 |
| policies | 4 | 10 | 10 | 1 | 3 |
| workflows | 4 | 11 | 1 | 11 | 0 |
| planning | 1 | 1 | 0 | 1 | 0 |
| utils | 1 | 1 | 0 | 1 | 0 |
| cli.py | 1 | 1 | 1 | 0 | 0 |
| **源码合计** | **46** | **84** | **67** | **37** | **18** |

源码中 26 处是没有 `except` 的 `try/finally`。测试另有 21 处 `try`、3 个 `except` 分支和 20 处 `finally`，多数涉及资源清理。`scripts/` 下未发现 Python 异常处理节点。

已阅读的重点实现表明，数量不能直接等同于可删除数量：

- `runtime/execution/curobo.py`、`vla.py` 自行捕获并分类通用异常，外层 `lifecycle.py` 又提供 episode 异常隔离，存在重复职责。
- `data/rendering/head_views.py`、`edit_views.py`、`camera_calibration.py` 等存在异常转默认结果或渲染状态的路径，需要区分真实已知兼容问题与无必要兜底。
- `scenes/loading/assets.py` 会跳过加载失败资产；`scenes/graph.py` 会把部分几何问题记入 issues。这些分别涉及资产筛选和部分场景表达，不能直接按“吞错”全部删除。
- `runtime/construction/state.py` 用 `KeyError` 判断名称是否存在，可考虑用 MuJoCo 名称查询的返回值直接分支，保留新增/删除对象时按名称迁移的规则。
- `runtime/construction/session.py` 的部分 `BaseException` 分支执行关闭后原样抛出；这类代码的职责是资源交接失败时清理，不是隐藏错误。
- `policies/molmobot_service.py`、`pi05/service.py` 的通用捕获承担跨进程错误回传；删除前必须保证客户端能及时得到原始故障，并保持共享模型/GPU 故障停批。
- `runtime/control/action.py` 把外部非法动作转换为 `InvalidAction`，有实际调用方依赖；`runtime/outcomes/pick.py` 的证据校验影响 unknown/infrastructure_error 语义，不能无差别删掉。

## 修改规则

1. **内部计算直接执行。** 删除仅添加泛化说明、返回空值或再次分类的捕获，让异常上抛；不得把 `except` 换成大批 `getattr`、默认值、预检查或通用校验框架。
2. **正常分支直接表达。** 名称不存在、候选不支持等正常筛选条件，用现有查询接口或明确返回值表达；不重写整套错误类型和业务状态体系。
3. **异常只在有职责的边界处理。** 保留动作输入、资产输入、IPC 和 episode 隔离中确有必要的转换。捕获范围缩到实际操作，内部编程错误不应被误写成 rejected、unsupported、任务失败或空图。
4. **资源有明确所有者。** 优先使用已有上下文管理接口或标准库 `with`；多资源生命周期确有需要时再用 `ExitStack`。无适合接口时保留简短 `try/finally`，不为消除语法增加包装层。
5. **保留原始异常和中断。** 剩余捕获必须处理具体恢复、资源释放、协议回传或批量隔离；保留完整 traceback。`KeyboardInterrupt`、`SystemExit` 不转换为普通 trial 失败。
6. **收尾不承载无关业务。** 检查执行器中较长的 `finally`，将正常结果计算和输出放回正常流程；故障时只写真实已发生的证据并释放资源，不在收尾中掩盖原始错误。

## 实施顺序

### P0：准备基线与确认处理边界

- 后续实施从该 worktree 开始，先检查其 Git 状态。
- 按 `scripts/install.sh` 在该 worktree 内建立独立 `.venv`，不链接或借用主工作区环境，不修改其他环境的安装路径。
- 在改代码前跑一次离线基线；若失败，记录真实失败，先定位实际问题，不扩展为环境审计。
- 结合调用方和已有测试确认 episode、记录器、Simulation、模型子进程的资源所有权；把通用故障分类收敛到已有 episode 边界作为修改方向。

### P1：清理叶子模块的兜底与异常控制流

- 先处理 `scenes/loading/`、`scenes/tasks/discovery.py`、`scenes/graph.py`、`runtime/construction/state.py` 和相关采样代码。
- 删除没有恢复意义的捕获；正常候选筛选改为直接分支，保留真实 unsupported/rejected 结果。
- 保留 `NativeRequired` 等明确选择加载路径的机制，以及外部动作、实际物理证据的必要检查。
- 验证：`tests.scenes`、`tests.runtime.test_construction`、`tests.runtime.test_actions`、`tests.runtime.test_outcomes`。

### P2：整理渲染、导出和工作流的资源收尾

- 处理 `data/rendering/`、`data/visualization/`、`data/export/` 以及 `workflows/` 的长嵌套 `try/finally`。
- 对隐藏任意渲染错误的兜底优先改为抛出；有已知原因和实际需要的局部处理单独保留，不把黑图或空图标成渲染成功。
- 复用已有资源接口，收敛相机、renderer、文件、writer、Simulation 的关闭职责；仍需状态恢复的位置保留必要 `finally`。
- 验证：`tests.data`、`tests.scenes.test_builder_preview` 和受影响的 workflow 测试。

### P3：收敛执行器的异常分类与最终结果处理

- 处理 `runtime/execution/curobo.py`、`vla.py`、`lifecycle.py` 和 `data/recorder.py`。
- 优先移除执行器与外层隔离重复的通用分类，保留 `OperationFailure`、`InvalidAction`、超时和真实成功证据对应的业务处理。
- 故障统一在既有 episode 边界记录原始 traceback；检查移出 `finally` 的逻辑，确保已执行轨迹仍保留、记录故障仍明确、资源仍关闭。
- 保留独立 trial 的失败隔离、共享模型/GPU/存储故障停批，以及中断原样上抛。
- 验证：`tests.runtime.test_lifecycle`、`tests.runtime.test_simulation`、`tests.data.test_recording`、`tests.workflows.test_collection`、`tests.workflows.test_benchmark`。

### P4：简化模型服务与客户端的异常处理

- 处理 MolmoBot 与 π0.5 的客户端/服务，复用现有协议，不新增 RPC 框架。
- EOF、断连、请求超时、推理报错各保留必要边界；服务端处理故障回传后，应使共享故障能终止当前批次，避免继续消费失效服务。
- 删除无职责的重复包装，保留远端 traceback、启动失败清理和子进程退出处理。
- 验证：现有 policy/lifecycle 测试；缺少覆盖时只补关键协议、断连或停批行为测试，不加载真实大模型来代替软件回归。

### P5：合并验证与交付

- 每批先跑受影响的局部测试；全部修改完成后跑一轮完整离线回归，不在每批重复全套。
- 测试自身重复的资源清理可用 `addCleanup` 或已有上下文管理接口精简，不删除必要断言，不为了降低计数改写无关测试。
- 更新当前说明中确实改变的错误表现；在 `reports/exception-cleanup/delivery/01-result.md` 总结实际改动、剩余必要处理、测试结果和限制。
- 对比修改前后的源码异常节点数量作为辅助信息，不设置删除比例，不新增持久化逐文件清单或检查框架。

完整离线回归从本工作区根目录运行：

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v
```

## 验收标准

- 内部模块的无职责通用捕获、静默兜底和重复分类得到清理，实际减少嵌套与分支。
- 任意保留的广泛捕获均有明确边界职责；不要求清零，不把捕获简单搬到新包装函数。
- 非预期异常可追溯到原始调用栈；没有转成成功、普通任务失败或伪造执行记录。
- 资源在正常结束、执行报错和用户中断时仍正确释放；构建事务、初始化边界和禁止执行中复位的语义保持。
- 局部试验错误与共享故障仍分别处理；unknown、任务结果、执行终止和记录完整性不混淆。
- 相关测试和完整离线回归结果如实记录；不以软件回归替代真实 GPU rollout 或模型成功验收。
- 不改变五个公开 CLI、动作布局和现有主数据格式，不引入顺带重构或历史兼容。

## 方案编写阶段执行结果与限制

已检查主工作区状态（创建前干净），从 `main` 建立独立 worktree 和分支；读取仓库规则、异常处理分布、重点实现和已有测试，完成上述方案。工作区创建时 Git 元数据写入受到权限限制，获准后创建成功。

本轮仅新增方案并更新本工作区报告入口，未修改业务代码、安装环境或运行测试。上述统计属于静态现状，候选修改仍需在实施时结合调用方逐处确认，不能提前承诺全部删除或真实任务已验证。

后续实施已完成：源码 `try` 从 84 处减至 60 处，完整离线回归 247 项中 245 项通过、2 项跳过。环境调整、保留处理和真实验证边界见[执行结果](../delivery/01-result.md)。
