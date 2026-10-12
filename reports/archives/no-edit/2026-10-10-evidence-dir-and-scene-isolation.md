# 证据目录唯一性与场景级故障隔离修复

## 背景

两个 run 在 rollout 重试时以 `infrastructure_error` 异常停止：

- `no-edit-v080-val103-all-cuda1-20261010-174137`：`FileExistsError` on `v080-0-left`，运行 3604s 后停止。
- `no-edit-v080-val103-cup30-20261010-173735`：`FileExistsError` on `v080-0-right`，运行 8872s、105 次试验成功 80 次后停止。

两者同一根因：`manipulate_v2` 用 `ctx.plan_count` 命名证据目录，而 rollout 重试前 `run_raw_attempt` 会把 `plan_count` 归零（`no_edit_execution.py:458`），重试因而重新落到第一次 attempt 已创建的同一目录名，裸 `mkdir()` 抛 `FileExistsError`。该异常被兜底 `except Exception` 判为 `infrastructure_error`（`no_edit_execution.py:476`），再经批处理编排传播为整批终止。

## 本次修复

1. **证据目录改以单调序号命名。** 新增 `ContinuousContext.plan_serial`，只增不减；planning 查询与证据目录均使用它。`plan_count` 保留原语义（每次 attempt 重置，预算按 attempt 计），两者职责分离：预算属于 attempt，证据属于整次运行。
   - `v080-{plan_serial}-{side}`、`v080-closed-{plan_serial}-{side}`；open 分支的 `world-{index}` 一并改为 `parents=True, exist_ok=True`。
   - 正常路径目录名不变（首个 attempt `v080-0-left` → 第二侧 `v080-1-right`），重试改为 `v080-2-left`，两次 attempt 各自保留证据，不再互相覆盖。
2. **场景级故障隔离（已在工作区，本次核对）。** `collect_batch` 不再因单场景 worker 的 `infrastructure_error` 清空 pending；剩余场景继续派发，证据保留，终态在全部完成后按证据判定（存在 infra → `infrastructure_error`）。

## 验证

- `tests/test_no_edit_fault_isolation.py`：6 项通过，含
  - 重试两次后 `v080-0-left` 与 `v080-1-left` 并存（证明不复用、不覆盖）；
  - closed 目录复用后原 marker 仍存在；
  - open 的 `world-0` 复用不抛异常；
  - 单场景 infra 不停后续场景、worker 无 summary 不停后续场景、completed/incomplete 终态不变。
- 全套离线回归 **223 项，8 项跳过，其余通过**（30.685s）。

以上均为 fixture 级程序逻辑验证，**不是物理抓取成功证据**。未运行真实 GPU 采集，未重启全量批次。

## 未处理

- legacy 后端 `choose_control` 内的 `dry_plans-*` / `measured_planner-*` / `world-*` 目录仍为裸 `mkdir()`。v2 不进入该路径，本次不改，避免扩大未验证改动面。
- 173735 的 80 次成功 trial 不会因此恢复：该 run 的 `retained_segments` 仍为 0，需用新 run-id 重采。
