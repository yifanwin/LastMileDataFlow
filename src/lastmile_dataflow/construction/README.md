# construction

`config.py`：v2 契约。
`generate.py`：case 中立的候选生成原语（沿支撑局部线取样、同量纲 `rank_evidence`）。
`cases/`：**case 共用层**——`base.py` 提供 `Frame`、`Requirement` 标注与 `CaseConstructor`
四步接口（`preflight` / `generate` / `local_check` / `pending_hypotheses`）；
`case1.py` 距离困难、`case1_5.py` 侧向差异、`legacy.py` 保留 case2/case3 既有规则行为。
`candidates.py` 按 case 分发；`legacy_candidates.py` 保存 case2/case3 的历史网格与惩罚排序，互不污染。

## 标注约定

每条要求都带两项标注，写在 `checks/*.json`、`task_candidate.json` 与观察包中：

- `strength`：`geometric_measurement`（几何测量）、`physical_evidence`（真实静置物理证据）、
  `geometric_proxy`（几何代理，明确不是导航或任务证据）、`model_semantic`（模型语义判断）。
- `layer`：`scene_validity`（场景有效性，物理门槛）或 `case_intent`（case 构建意图）。

**只有 `scene_validity` 的物理证据参与 `scene_valid` 判定。** 失败 `case_intent` 要求表示
"case 条件尚未成立"，是编辑循环存在的理由，不能被读成场景无效，也不能被场景有效性掩盖。

## 坐标系规则

凡相对家具或支撑区域的量，一律用该对象的局部系表达，并在证据里同时写世界值：

- `Frame.of_support_region` 用支撑区域的水平轴（局部 x/y 在支撑面内，z 向上）。
- `Frame.of_body` 用家具 body 的水平偏航 + 重力对齐 z（THOR 家具自身坐标系常带 roll/pitch，
  直接用会在方向语义里混入倾角，详见 `base.py`）。
- 只有面向机器人的朝向用世界系；`convention` 字段随证据落盘。

## 显式零编辑

构建结果与 `task_candidate.json` 都有 `edited` 布尔值：`false` 表示零编辑即满足要求，
仍走完整验证、冻结与独立恢复。`baseline` 只比较真正被编辑的对象。

## 故障回归

`tests/test_case_shared_layer.py` 覆盖必测故障：冻结初态被绕过（`measurement_source_inconsistent`）、
侧向向量指向家具内部（preflight 拒绝且不生成候选），以及标注完整性、零编辑标记、
同量纲排序与 `rank`/`shortlist` 决策语法。

能力范围与未验证部分见 [phase2.md](../../../docs/phase2.md)。
