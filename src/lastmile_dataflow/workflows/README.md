# workflows

`build.py`：低层规则式构建、累计预算、冻结独立恢复与阶段一交接。

`case_edit.py`：指定单场景的四 Agent 构造闭环；`case_edit_supervisor.py`：包含原生编译、渲染、HTTP 的进程级硬期限。主命令仍为 `case-edit`，不保留平行的版本后缀入口。

当前仅支持构造验收，移动任务验收未实现。契约与限制见 [case-edit-pipeline.md](../../../docs/case-edit-pipeline.md)。

`case_edit.py` 实际执行补图、局部扩展及资产请求；补图回到原提议或检查阶段，不重新编辑。实体机器人只做初始化放置，未执行移动和抓取。
