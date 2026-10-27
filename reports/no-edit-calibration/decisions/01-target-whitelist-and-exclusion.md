# 目标白名单与类别剔除规则

- 状态：active
- 日期：2026-10-27
- 替代：早期 `trials >= 30`、`houses >= 2` 的自动排除口径
- 关联：[实验方案](../plan/01-experiment-plan.md)、[计划表](../plan/02-schedule.md)、[实时状态](../delivery/status.md)

## 1. 初始目标白名单

### pick

Cup、Mug、Bowl、SoapBottle、SprayBottle、Plate、PepperShaker、SaltShaker、Vase、Apple、Tomato、Potato、TeddyBear、Statue、Book、Candle、Pan、Pot。

资产级限制：

- `Vase`：仅 `Vase_Flat*`、`Vase_Medium*`、`Vase_Tall*`；排除 `Vase_Decorative*`、`Vase_Open*`、`RoboTHOR_vase_stilren*`。
- `Statue`：仅 `Dog_Statue*`、`Stone_Statue*`、`Fertility_Statue*`、`Hand_Statue*`、`Waving_Statue*`；排除 `Towel_Statue*`。

### open

Dresser、Cabinets、Fridge、Microwave、Doorway、SideTable、Safe、ShelvingUnit、Desk、Stove、Dishwasher、Toaster。

映射说明：

- `Drawer`、`Dresser_*` 使用 `Dresser`。
- `Cabinet` 使用 `Cabinets`。
- `Doorway_Double` 属于 `Doorway` 类别，资产前缀为 `Doorway_Double*`。
- `Side_Table_*` 使用 `SideTable`。
- `Safe_*` 使用 `Safe`。
- `Shelving_Unit_*` 使用 `ShelvingUnit`。
- `Desk_*` 使用 `Desk`。
- `oven_*` 当前映射 `Stove`；该数据集的 Stove 资产为 `StoveKnob*`。
- `dishwasher_*` 映射 `Dishwasher`；当前资产索引中没有该类别，发现为空是有效结果。
- `Toaster` 使用 `Toaster`。

## 2. 自动剔除规则

- 按 `category|operation` 累计。
- 当 `trials >= 1000` 且 `successes / trials < 0.1%` 时，类别进入 `exclude_categories`；零成功是其中一种情况。
- 不再要求 ≥10 个房屋。
- 按累计 trial 计算，不要求连续段；success rate 高于 0.1% 的类别不会被该规则剔除。
- 永久白名单：Cup、Mug、Bowl、SoapBottle、SprayBottle、Vase。即使累计 1000 trial 零成功，也不自动剔除。
- 手动永久排除：Pen、Pencil、CellPhone、CD、Newspaper、RemoteControl。它们同时不在目标白名单中。

## 3. 补跑口径

c000–c005 已按旧规则完成。增量补跑：

- 使用新白名单和资产前缀。
- 每个 chunk 通过 `exclude_tasks` 精确排除原始 run 中已接受的 `房屋:task_id`。
- 其余白名单任务重新发现、采样和标定；不重复已有 task，也不会因类别级粗粒度排除漏掉 Vase/Statue 变体。
- 补跑 run-id：`no-edit-val-supp-c000` … `no-edit-val-supp-c005`。
- 完成并入主 state 后，c006 起主链按白名单和累计零成功规则继续。

## 4. 边界

- 旧 c000–c005 结果保留，不覆盖；补跑只补缺失白名单任务。
- 旧 c006 的 1,430 trial 按旧规则产生，已归档，不并入统计。
- 目标白名单是任务发现边界；剔除规则只影响后续 chunk，不回删历史结果。
