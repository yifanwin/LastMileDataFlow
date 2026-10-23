# 异常处理精简执行结果

- 状态：done
- 日期：2026-10-23
- 替代：无
- 关联：../plan/01-implementation.md、../requirement/02-simplification-and-readability.md、../../../docs/architecture.md、../../../docs/data-format.md

## 执行过程

在 `.worktrees/exception-cleanup`、`refactor/exception-cleanup` 分支实施。先读取异常处理实现及相关测试，按场景查询、渲染与资源收尾、执行隔离、模型客户端的顺序修改，再进行局部回归。主工作区未修改，未合并或提交。

该 worktree 原来没有环境，已建立自己的 Python 3.11 `.venv`，没有链接或借用主工作区的环境与包。完整依赖安装受大体积 PyTorch/CUDA 下载速度影响而停止，改为安装项目声明中的离线依赖及相同版本的 CPU PyTorch/torchvision；`pyproject.toml` 未修改。本轮没有取得修改前的完整软件回归基线，不作前后性能或真实任务成功率比较。

## 实际修改

- 名称存在性改为 MuJoCo 直接查询，覆盖状态迁移、目标解析、任务发现、场景图和渲染可见性。合并取景几何中的重复处理。
- 执行器保留明确的操作失败、非法动作、成功证据与超时分支；删除重复的通用异常捕获。正常结果计算从长 `finally` 移回正常流程，非预期异常交给已有 episode 隔离边界。
- episode 边界保留原始调用栈，异常时报告 unknown 任务结论，并保存已经发生的物理步。用户中断继续传播；关闭资源时的错误保留独立 traceback，仍关闭其他资源，共享故障仍停批。记录器最终写入只尝试一次，清理阶段不重试失败的最终写入。
- 渲染和深度采集错误直接上抛，不再用通用 `RenderError` 隐去原始原因，不静默跳过请求的深度文件。站位采样仅将 `InvalidAction` 作为候选过滤，普通配置和相机错误直接报出；资产筛选不再静默跳过 I/O 错误。
- 用标准库资源上下文替换部分手写收尾，覆盖场景采样、导出、采集、benchmark、重建视频及相关测试。成对渲染的资源关闭和 FOV 恢复统一由 `ExitStack` 管理。
- MolmoBot 服务使用连接上下文释放资源；共用客户端保留服务端 traceback，合并子进程退出等待中重复的超时捕获。π0.5 继续复用该客户端，不新增协议层。
- 同步当前架构与数据格式说明；新增 8 项针对错误传播、轨迹保存、中断和资源恢复的测试。

按 AST 统计当前维护的 Python 代码，不统计归档副本；一处 `try` 可同时有 `except` 和 `finally`。

| 范围 / 指标 | 修改前 | 修改后 |
|---|---:|---:|
| 源码 try | 84 | 60 |
| 源码 except 分支 | 67 | 46 |
| 源码 finally | 37 | 22 |
| 源码 Exception/BaseException 捕获分支 | 18 | 11 |
| 测试 try | 21 | 12 |
| 测试 finally | 20 | 11 |

## 保留的处理

剩余 11 个广泛捕获分支均位于有实际职责的位置：构建/客户端启动失败后的资源清理并原样重抛 5 处，模型服务跨进程错误回传 2 处，episode 隔离及逐资源关闭 2 处，视频 writer 批量关闭 1 处，场景预览批处理隔离 1 处。

同时保留明确的任务失败和超时处理、构建事务清理、部分结果落盘、物理证据边界校验，以及已有说明的 MuJoCo 分割 `IndexError` 处理。没有添加通用校验框架、异常框架、文件指纹或逐文件检查清单。

## 验证

- 关键局部回归：54 项，53 项通过，1 项按原设置跳过真实 OpenGL 渲染。
- 场景、数据、采集与 benchmark 局部回归：58 项全部通过。
- 记录器最终写入调整后的局部回归：19 项全部通过。
- 完整离线回归：247 项，245 项通过，2 项跳过；跳过项为未安装 `pi05-data` 的 LeRobot 导出测试与显式 OpenGL 渲染测试。
- `compileall` 与 `git diff --check` 通过。

当前服务器缺少可加载的 EGL 库，`MUJOCO_GL=egl` 在 MuJoCo 导入阶段报 `eglQueryString` 错误。离线回归改用 `MUJOCO_GL=disable`，没有修改系统图形库，也没有降低测试断言。

实际测试版本为 MuJoCo 3.5.0、PyTorch 2.7.1+cpu、torchvision 0.22.1+cpu、transformers 5.4.0；解释器和已安装源码路径均指向本 worktree。测试日志保存在 `/tmp/lastmile-exception-focused.log`、`/tmp/lastmile-exception-domains.log`、`/tmp/lastmile-exception-finalization.log` 和 `/tmp/lastmile-exception-full.log`，不提交到仓库。

```bash
cd .worktrees/exception-cleanup
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v
```

新增测试中的执行轨迹来自简化模型的实际 MuJoCo stepping；渲染错误与模型故障通过注入验证软件边界。它们不证明真实机器人、GPU 规划或大模型任务成功。

## 边界

本环境用于 CPU 软件回归，未完整安装 cuRobo/CUDA 运行依赖，不能直接视为 GPU 实验环境。未运行真实渲染、GPU rollout、策略权重推理或五命令端到端任务。旧实验数据、外部资产和权重未改动。
