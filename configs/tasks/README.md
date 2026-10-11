# `configs/tasks/` 配置说明

阶段一（foundation）任务配置。加载入口 `config.py: load_config(TaskConfig, path)`，
字段见 `src/lastmile_dataflow/config.py` 的 `TaskConfig`。

## `foundation.json`

```json
{"task_id": "foundation-smoke", "case_type": "foundation", "operation": "short_action"}
```

| 字段 | 含义 |
|---|---|
| `task_id` | 任务标识，写进 attempt 的 `attempt.json` 与 `recorder.metadata`。 |
| `case_type` | 取值 `foundation / case1 / case1.5 / case2 / case3`。 |
| `operation` | 阶段一只实现 `short_action`：执行有界短动作流。 |

## 关键约束（`TaskConfig.__post_init__`）

- `operation` 必须是 `short_action`；`success_conditions` 与 `allowed_edits` 必须为空。
  阶段一**不实现场景编辑，也不提供任务成功判定器**——case 与 task 结论保持 `unknown`，
  不会因为短动作跑完就自动判成功。
- 其他字段（`target`、`fixed_base`）当前文件未显式给出，走默认值（`target=null`、`fixed_base=false`）。
  `target` 也可由 CLI `--target` 覆盖，或由 `--imported-scene` 携带。

阶段二/三的任务定义不在本目录：场景构建看 `builds/`，站位抓取看 `stations/`。
