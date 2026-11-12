# 候选构建场景预览（build-scenes）

- 状态：active
- 日期：2026-10-26
- 替代：无
- 关联：[构建交付](01-build-delivery.md)、[运行清单](05-run-inventory.md)

本文件汇总 12 个 build run 的 accepted 场景渲染。图片由 `build-scenes --preview-dataset` 生成，保留站位指示（蓝点/箭头）、目标框和新增物体框；头部标注 `proposal_intent`。编辑候选为“编辑前/编辑后”双栏。

- 共 **38 个 accepted 场景**，其中 **4 个编辑候选**。
- 系统无 CJK 字体，图像标签为英文；HTML 索引页仍为中文。
- 编辑前站位未写入 v2 构建记录，前栏显示 `Top-down stations (not recorded)`，不推测补造。

## 汇总

| run | 来源 | accepted | 编辑 | 场景库索引 |
|---|---|---:|---:|---|
| [p3-cup30-20261026-r03](#p3-cup30-20261026-r03) | house 103（val） | 4 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/index.html) |
| [p3-open-20261026-r01](#p3-open-20261026-r01) | house 0（train） | 2 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p3/p3-open-20261026-r01/scene_datasets/build/index.html) |
| [p3-parallel-20261026-r01](#p3-parallel-20261026-r01) | house 103（val）、house 0（train） | 8 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/index.html) |
| [p3-public-20261026-r02](#p3-public-20261026-r02) | house 0（train） | 2 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p3/p3-public-20261026-r02/scene_datasets/build/index.html) |
| [p3-spatial-20261026-r01](#p3-spatial-20261026-r01) | house 0（train） | 2 | 1 | [index.html](../../../outputs/diagnostics/task-modes/p3/p3-spatial-20261026-r01/scene_datasets/build/index.html) |
| [p6-bottle-build-20261026-r01](#p6-bottle-build-20261026-r01) | house 1（val）、house 0（train） | 13 | 2 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/index.html) |
| [p6-high-open-build-20261026-r01](#p6-high-open-build-20261026-r01) | house 1（val）、house 0（train） | 2 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-high-open-build-20261026-r01/scene_datasets/build/index.html) |
| [p6-obstruction-build-20261026-r01](#p6-obstruction-build-20261026-r01) | house 1（val）、house 2（train） | 0 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r01/scene_datasets/build/index.html) |
| [p6-obstruction-build-20261026-r02](#p6-obstruction-build-20261026-r02) | house 4（train）、house 3（train） | 1 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r02/scene_datasets/build/index.html) |
| [p6-obstruction-build-20261026-r03](#p6-obstruction-build-20261026-r03) | house 6（train）、house 7（train）、house 8（val）、house 5（train）、house 9（train） | 0 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r03/scene_datasets/build/index.html) |
| [p6-soap-build-20261026-r01](#p6-soap-build-20261026-r01) | house 0（train）、house 2（train） | 4 | 1 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/index.html) |
| [p6-visibility-build-20261026-r01](#p6-visibility-build-20261026-r01) | house 103（val） | 0 | 0 | [index.html](../../../outputs/diagnostics/task-modes/p6/build/p6-visibility-build-20261026-r01/scene_datasets/build/index.html) |

## p3-cup30-20261026-r03

来源：house 103（val） · accepted 4（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/index.html)

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-ground_obstacle-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-ground_obstacle-v000](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-ground_obstacle-v000.png)

接受意图 `ground_obstacle` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-narrow_space-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-narrow_space-v000](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-narrow_space-v000.png)

接受意图 `narrow_space` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p3/p3-cup30-20261026-r03/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 20

## p3-open-20261026-r01

来源：house 0（train） · accepted 2（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p3/p3-open-20261026-r01/scene_datasets/build/index.html)

### `val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-reachability-v000`

![val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-open-20261026-r01/scene_datasets/build/previews/val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 15

### `val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-articulated_interaction-v000`

![val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-articulated_interaction-v000](../../../outputs/diagnostics/task-modes/p3/p3-open-20261026-r01/scene_datasets/build/previews/val0-open-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_0_2-safe_9ec7e597d8dc9e38b4aedf3b754b6971_1_2_2_safedoor18joint0_1-articulated_interaction-v000.png)

接受意图 `articulated_interaction` · 新增物体 0 · 目标移动 0 · 站位 15

## p3-parallel-20261026-r01

来源：house 103（val）、house 0（train） · accepted 8（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/index.html)

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v001`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v001](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-reachability-v001.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 20

### `val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v001`

![val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v001](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val103-pick-cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2-orientation-v001.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 20

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 5

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v001`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v001](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v001.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 5

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 7

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v001`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v001](../../../outputs/diagnostics/task-modes/p3/p3-parallel-20261026-r01/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v001.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 7

## p3-public-20261026-r02

来源：house 0（train） · accepted 2（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p3/p3-public-20261026-r02/scene_datasets/build/index.html)

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-public-20261026-r02/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 5

### `val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000`

![val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p3/p3-public-20261026-r02/scene_datasets/build/previews/val0-pick-Irishpotato_7c3f16774c3f2d6de70e09dd23897534_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 7

## p3-spatial-20261026-r01

来源：house 0（train） · accepted 2（编辑 1） · [场景库索引](../../../outputs/diagnostics/task-modes/p3/p3-spatial-20261026-r01/scene_datasets/build/index.html)

### `val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-reachability-v000`

![val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-reachability-v000](../../../outputs/diagnostics/task-modes/p3/p3-spatial-20261026-r01/scene_datasets/build/previews/val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 1 · 站位 5 · 编辑前后双栏

### `val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-orientation-v000`

![val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-orientation-v000](../../../outputs/diagnostics/task-modes/p3/p3-spatial-20261026-r01/scene_datasets/build/previews/val0-pick-alarmclock_edafd5ff3a20b9a31dfe003fe979a6f6_1_0_7-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 3

## p6-bottle-build-20261026-r01

来源：house 1（val）、house 0（train） · accepted 13（编辑 2） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/index.html)

### `val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-reachability-v000`

![val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 2

### `val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-orientation-v000`

![val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val1-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_3-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 4

### `val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-reachability-v000`

![val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 2

### `val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-arm_path_collision-v000`

![val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-arm_path_collision-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-arm_path_collision-v000.png)

接受意图 `arm_path_collision` · 新增物体 1 · 目标移动 0 · 站位 2 · 编辑前后双栏

### `val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-orientation-v000`

![val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-peppershaker_d3a7b2ab4bd62388f7ea7dd2ef7c1fdc_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 3

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-reachability-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 4

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-orientation-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_2_0_5-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 6

### `val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-reachability-v000`

![val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 5

### `val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-orientation-v000`

![val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-cup_4697b73732349085e30935e0f35566dc_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 7

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-reachability-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 12

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-arm_path_collision-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-arm_path_collision-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-arm_path_collision-v000.png)

接受意图 `arm_path_collision` · 新增物体 0 · 目标移动 0 · 站位 12

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-narrow_space-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-narrow_space-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-narrow_space-v000.png)

接受意图 `narrow_space` · 新增物体 1 · 目标移动 0 · 站位 10 · 编辑前后双栏

### `val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-orientation-v000`

![val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-bottle-build-20261026-r01/scene_datasets/build/previews/val0-pick-atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 14

## p6-high-open-build-20261026-r01

来源：house 1（val）、house 0（train） · accepted 2（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-high-open-build-20261026-r01/scene_datasets/build/index.html)

### `val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1-articulated_interaction-v000`

![val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1-articulated_interaction-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-high-open-build-20261026-r01/scene_datasets/build/previews/val1-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_2-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_2_DrawerMesh021_1-articulated_interaction-v000.png)

接受意图 `articulated_interaction` · 新增物体 0 · 目标移动 0 · 站位 11

### `val0-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_8_DrawerMesh021_1-articulated_interaction-v000`

![val0-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_8_DrawerMesh021_1-articulated_interaction-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-high-open-build-20261026-r01/scene_datasets/build/previews/val0-open-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_0_8-chestofdrawers_7a2e462b2666d3558113b2d84da9dc74_1_4_8_DrawerMesh021_1-articulated_interaction-v000.png)

接受意图 `articulated_interaction` · 新增物体 0 · 目标移动 0 · 站位 9

## p6-obstruction-build-20261026-r01

来源：house 1（val）、house 2（train） · accepted 0（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r01/scene_datasets/build/index.html)

无 accepted 场景；索引页仅含 rejected/skipped 卡片，无图片。

## p6-obstruction-build-20261026-r02

来源：house 4（train）、house 3（train） · accepted 1（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r02/scene_datasets/build/index.html)

### `val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_8_6_DrawerMesh031_3-approach-v000`

![val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_8_6_DrawerMesh031_3-approach-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r02/scene_datasets/build/previews/val3-open-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_0_6-chestofdrawers_6e3a4148a857a4222c764362660c370a_2_8_6_DrawerMesh031_3-approach-v000.png)

接受意图 `approach` · 新增物体 0 · 目标移动 0 · 站位 17

## p6-obstruction-build-20261026-r03

来源：house 6（train）、house 7（train）、house 8（val）、house 5（train）、house 9（train） · accepted 0（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-obstruction-build-20261026-r03/scene_datasets/build/index.html)

无 accepted 场景；索引页仅含 rejected/skipped 卡片，无图片。

## p6-soap-build-20261026-r01

来源：house 0（train）、house 2（train） · accepted 4（编辑 1） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/index.html)

### `val0-pick-soapdispenser_b8726504edf0a781770524a38cc81949_1_0_2-reachability-v000`

![val0-pick-soapdispenser_b8726504edf0a781770524a38cc81949_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/previews/val0-pick-soapdispenser_b8726504edf0a781770524a38cc81949_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 2

### `val2-pick-soapdispenser_f1b1886ddc2b7f9192d0fa92bd7535d0_1_0_2-reachability-v000`

![val2-pick-soapdispenser_f1b1886ddc2b7f9192d0fa92bd7535d0_1_0_2-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/previews/val2-pick-soapdispenser_f1b1886ddc2b7f9192d0fa92bd7535d0_1_0_2-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 1 · 站位 3 · 编辑前后双栏

### `val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-reachability-v000`

![val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-reachability-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/previews/val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-reachability-v000.png)

接受意图 `reachability` · 新增物体 0 · 目标移动 0 · 站位 2

### `val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-orientation-v000`

![val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-orientation-v000](../../../outputs/diagnostics/task-modes/p6/build/p6-soap-build-20261026-r01/scene_datasets/build/previews/val2-pick-soapdispenser_c44a2e4b26bddaf96812e05d7baf4f49_1_0_9-orientation-v000.png)

接受意图 `orientation` · 新增物体 0 · 目标移动 0 · 站位 3

## p6-visibility-build-20261026-r01

来源：house 103（val） · accepted 0（编辑 0） · [场景库索引](../../../outputs/diagnostics/task-modes/p6/build/p6-visibility-build-20261026-r01/scene_datasets/build/index.html)

无 accepted 场景；索引页仅含 rejected/skipped 卡片，无图片。
