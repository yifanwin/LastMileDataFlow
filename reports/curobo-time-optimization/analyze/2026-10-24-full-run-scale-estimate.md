# 100 个 val 场景全量运行的规模与时长预估

- 状态：draft
- 日期：2026-10-24
- 替代：无
- 关联：[当前需求](../requirement/02-panda-omron-time-optimization.md)、[最新方案](../plan/01-panda-omron-time-optimization.md)、[执行计划表](../plan/03-execution-schedule.md)、[当前状况](../info/01-current-status.md)

**本文只做运行前的数量与时长估算，不是实测结果。** 加速链（P03–P08）尚未实现，标定口径取方案中未验证的“满 20×3 ≤30 min/场景”作为下限，取历史 Panda 实测 69.3 s/trial 作为上限。

## 1. 当前配置下一个场景选几个目标物体

依据 [panda_omron_val103.json](../../../configs/scene_datasets/panda_omron_val103.json) 与 [dataset_config.py](../../../src/lastmile_dataflow/scenes/dataset_config.py)：

| 字段 | 当前值 | 效果 |
|---|---|---|
| `max_pick_targets_per_house` | 1 | 每个房屋最多选 1 个 pick 目标 |
| `max_open_targets_per_house` | 1 | 每个房屋最多选 1 个 open 目标；当前未启用 Case3，不生效 |
| `cases` | `["Case2"]` | 只构建 Case2 场景，pick 之外的分支不产生场景 |
| `station_config.operations` | `["pick"]` | 任务发现只产出 pick |
| `targets` | `{"val103": ["Cup_30"]}` | 仅 val103 限定为 Cup_30 |
| `variants_per_case` | 1 | 每个 case 只做 1 个增强变体 |

**结论：当前 val103 场景 = 1 个场景、1 个目标物体（Cup_30）。** 目标数不是 20；20 是站位上限。

若把 `cases` 扩到全部 Case1/Case2/Case3：

- 每个房屋仍是最多 1 个 pick 目标 + 1 个 open 目标（共 2 个目标物体）；
- Case1 与 Case2 复用同一个 pick 目标，各生成 1 个增强场景；
- Case3 使用 open 目标，生成 1 个不做障碍物增强的场景；
- 因此每房屋最多 **3 个场景**，每个场景仍只有 1 个目标物体。

目标数为 0 的情况：发现/资格检查要求至少 2 个静态预筛点、最终至少 3 个合法站位；不满足时该房屋没有目标。`build_scene_dataset` 默认执行配额检查，任一房屋目标不足会直接报错；`--allow-partial-targets` 才允许跳过。

## 2. 100 个 val 场景目前并不自动成立

以 val103–val202 这 100 个房屋为口径，扫描原始场景 JSON：

| 条件 | 含目标候选的房屋数 |
|---|---:|
| 限定 `Cup_30`（当前 selector 写法） | 14 / 100 |
| 白名单 pick 类型（Cup/Mug/Wine_Bottle/Spray_Bottle/Salt_Shaker/Pepper_Shaker/Soap_Bottle 等） | 85 / 100 |

85 只是“场景里存在候选资产”，还要再过尺寸、自由关节、支撑面、最少站位等资格条件，最终 accepted 会继续减少。因此：

- 若全量仍要求 Cup_30，只会得到约 14 个场景，不是 100 个；
- 若要 100 个场景，必须去掉按房屋的 `targets` 限定、按白名单类型选目标，并接受实际 accepted 可能明显少于 100（需 `--allow-partial-targets` 或换足够多的房屋）。

## 3. 单场景工作量

| 项目 | 数值 | 来源 |
|---|---|---|
| 最终站位数 | 3–20，上限 20 | `max_final_stations=20`、`min_final_points=3` |
| 每站位 trial | 3 | `trials_per_station=3` |
| 每场景 trial | ≤60 | 20 × 3 |

实际站位数取决于覆盖采样，3–20 之间；下面的墙钟按“每个场景都满 20 点”估上限。

## 4. 时长预估

### 4.1 标定（evaluate-stations）

两个锚点：

- 历史 Panda 整屋、2 次/站位、图像开启、单进程：40 trial / 2,773.5 s = **69.3 s/trial**（旧流程，未加速）；
- 本轮方案验收目标：满 20×3 ≤ **30 min/场景**，即约 30 s/trial 的串行等效值（未验证）。

| 口径 | 场景数 | trial 上限 | 按 30 min/场景目标 | 按旧 69.3 s/trial |
|---|---:|---:|---:|---:|
| 100 房屋 × 当前 Case2 | 100 | 6,000 | ≈ 50 h（2.1 天） | ≈ 115 h（4.8 天） |
| 100 房屋 × 全 Case1/2/3 | ≤300 | ≤18,000 | ≈ 150 h（6.3 天） | ≈ 346 h（14.4 天） |

说明：

- 上表假定 8 卡规划、CPU 执行池 60 worker 能按方案落地；单卡串行时 GPU 规划本身就会成为瓶颈（旧口径规划约 11.4 s/trial，6,000 trial ≈ 19 GPU·小时）。
- trial 的 `attempt_timeout_s=300` 是软预算；超时 trial 会显著拉高上限，表中未单独计入。
- 若实际平均站位数低于 20（例如 10），标定部分大致按比例下降，但场景加载、准备、收尾等固定成本不随之下降。

### 4.2 构建（build-scenes）

历史口径：val534 为 527.5 s（1 accepted + 1 rejected）；22 样本批次约 175 s/样本。当前 Panda 配方 `max_physical_trials=2`、`variant_timeout_s=180`，Case2 还要增加 10–20 个台面物体。

| 口径 | 预估 |
|---|---:|
| 100 房屋 × Case2 | 5–15 h |
| 100 房屋 × 全 Case（Case1/Case2 各做一次增强） | 10–30 h |

加速方案里的局部模型、编译缓存与构建并行尚未实现，上表按历史口径给区间。

### 4.3 合计

| 口径 | 构建 | 标定 | 合计 |
|---|---:|---:|---:|
| 100 房屋 × Case2 | 5–15 h | 50–115 h | **≈ 2.3–5.4 天** |
| 100 房屋 × 全 Case | 10–30 h | 150–346 h | **≈ 6.7–15.7 天** |

## 5. 关键不确定项

1. **加速链未实现**：本文下限来自方案目标，不是实测；P05–P08 完成后应以首个场景的分项计时校准。
2. **GPU 规划吞吐**：同站位 3 次 batch、常驻规划进程和多卡数量决定 6,000–18,000 次规划能否摊开。
3. **目标可得性**：Cup_30 在 100 个 val 房屋中只覆盖 14 个；放宽类型后仍有 15 个房屋无候选。
4. **站位数**：20 是上限而非均值；实际点名分布只能在全量构建后得到。
5. **超时比例**：旧数据里出现过 300 s 级超时，失败/超时 trial 的占比会直接改变墙钟。

## 6. 本次核对范围

只读取配置、场景 JSON 与既有报告做算术估算；未修改代码或配置，未运行构建、标定或任何真实 trial。所有时长均为估算，不能作为已验证运行结果引用。
