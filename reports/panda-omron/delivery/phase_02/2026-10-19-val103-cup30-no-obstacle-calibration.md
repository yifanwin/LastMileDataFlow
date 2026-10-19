状态：done
日期：2026-10-19
替代：无
关联：[PandaOmron 运行指南](../docs/panda-omron.md)、[全流程实施记录](phase_02/2026-10-17-panda-omron-full-flow.md)

# val103 Cup_30 无障碍物完整标定（flow-calibration-003）

## 本次执行

在 `.worktrees/panda-omron-phase01` 内，对原始 val103 `Cup_30` 重新执行一遍**无障碍物**的逐点标定。输入场景为真实采样数据集（不是增强场景），执行后端为 cuRobo V2 0.8.0。

### 1. 生成无障碍物数据集

```bash
.venv/bin/python tests/smoke/panda_omron_flow.py --stage prepare \
  --gpu 3 --max-stations 3 \
  --output outputs/diagnostics/panda-omron/flow-scene-008
```

原始 val103 场景、目标 `Cup_30` / `pick-0086`，站位几何 `radius_m=1.6`、`spacing_m=0.4`、`gripper_width_m=0.08`、`navigation_whole_body=true`。结果：候选 49、几何与可见性有效 4、覆盖选点后保留 3 个站位（S0021、S0020、S0013），status=accepted。未做任何 Case 障碍物增强。

### 2. 完整标定

```bash
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/diagnostics/panda-omron/flow-scene-008 \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json --gpu 3 \
  --run-id flow-calibration-003 \
  --output-dir outputs/diagnostics/panda-omron --record-physics
```

未传 `--max-trials`，因此是完整标定（3 站位 × 2 次 = 6 trial）。CPU 侧准备阶段未额外启动 Compose 等外部服务（"cpu30" 指标定入口无 GPU 之外的特殊配置）。

## 结果

| 站位 | trial | status | reason | 记录完整 |
|---|---|---|---|---|
| S0021 | 2 | success ×2 | mobile_pick | true |
| S0020 | 2 | failure ×2 | planning_no_solution | true |
| S0013 | 2 | success ×2 | mobile_pick | true |

- 有效完成站位 3/3，trial 6/6，有效 6，成功 4，异常/未知 0，耗时 332.5 s。
- `calibration_complete=true`，`eligible_pairs=2`（S0020→S0021、S0020→S0013），`shared_failure=false`。
- 与历史 `flow-calibration-002`（同一数据集 flow-scene-007、同一执行配置）逐 trial 结果一致：S0020 两站位均规划无解，其余两站位均成功。

## 产物

- 数据集：`outputs/diagnostics/panda-omron/flow-scene-008/`（`scenes.jsonl`、`sampling.json`、`scene/`）
- 标定：`outputs/diagnostics/panda-omron/flow-calibration-003/`（`trials.jsonl`、`summary.json`、`config.json`、`rollouts/r000000..r000005/`、`figures/panda-val103-cup/`）
- 逐物理步事实保存在各 rollout 的 `physics.jsonl.gz`；本次未开 `--record-video`。

## 限制

- S0020 的 `planning_no_solution` 是有限规划预算下的负结果，未放宽物理约束、未补造 rollout。
- 每站 2 次仅为接入规模验证，不据此估计跨场景成功率。
- 未执行连续采集（`collect-lastmile`）、物化或 benchmark；本次只交付无障碍物标定。
- 本次输出为运行产物（`outputs/` 被 Git 忽略），未提交、未合并。
