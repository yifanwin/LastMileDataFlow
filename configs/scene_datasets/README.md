# 场景库配方

用于 `build-scenes --config`。加载器：`scenes/dataset_config.py`；构造流程：`workflows/scene_datasets.py`。

## 重点手动配置

- **必须核对路径**：`data_root`、`dataset_dir`、`asset_library`、`robot_config`；机器人文件中的 `model_path` 也要核对。
- **换实验时修改**：`houses`、`targets`、`cases`、`variants_per_case`、两个 `max_*_targets_per_house`。先用 minimal 配方确认单目标构造。
- v3 需要明确填写 `pick_target_types`；`case1_support_types` 应与预期桌面/台面类别一致，不为增加样本随意放宽物理条件。
- `schema_version` 不随意改；修改 `rule_version` 会改变筛选和构造规则，不是备注字段。

## 文件区别

| 文件 | 房屋与目标 | 规则 / Case | 计划构造数（全部合格时） |
|---|---|---|---|
| `val103_val104.json` | 两房屋，每房最多 3 pick、1 open | v2；Case1/2/3；pick 每 Case 5 变体 | 62 |
| `val103_val104_countertop_targets_v3.json` | 两房屋，所有合格目标；pick 有类型白名单 | v3；Case1/2/3；pick 每 Case 5 变体 | `10P + O`，P/O 为合计 pick/open 数 |
| `val103_cup30_minimal_v3.json` | val103，仅 pick `Cup_30` | v3；Case1/2；每 Case 1 变体 | 2 |
| `rby1_position_head320.json` | 共用机器人，不是场景配方 | position 控制 | 不单独构造场景 |

以上不是已通过数。`countertop_targets` 也允许桌类，不是仅 CounterTop。

## 配方字段

| 字段 | 含义 / 当前设置 |
|---|---|
| `data_root` | 数据根目录；当前为仓库根目录下 `data` |
| `schema_version` | JSON 格式版本，当前 `scene-dataset-recipe-v1` |
| `rule_version` | `scene-cases-v2/v3`，决定构造和资格规则 |
| `dataset_dir` | ProcTHOR 场景目录，相对 data_root |
| `asset_library` | 新增物体资产库，相对 data_root |
| `robot_config` | 机器人 JSON，相对仓库根目录 |
| `houses` | 房屋身份列表，支持 `val103` 等表示 |
| `input_snapshots` | 房屋编号字符串到原场景快照目录的映射，如键 `"103"`；路径相对 data_root；空字典则加载原场景，缓存需匹配来源和机器人 |
| `targets` | 房屋到 pick 目标选择器列表，支持 instance_id / asset_id / task_id 匹配；不限制 open；省略则不额外限定 |
| `variants_per_case` | 每个 pick 目标、每个 Case1/2 的变体尝试数；Case3 不使用此倍数 |
| `max_pick_targets_per_house` | 每房 pick 目标上限；`-1` 保留所有合格目标 |
| `max_open_targets_per_house` | 每房 open 目标上限；`-1` 保留所有合格目标 |
| `cases` | 启用的 Case 列表，见下表 |
| `pick_target_types` | pick 目标类型白名单，不是新增障碍物类别；v3 必须显式设置 |
| `case1_support_types` | Case1 的目标支撑面类别白名单；当前包含台面和多种桌类 |
| `min_prefilter_points` | 原场景静态预筛至少有效站位数，当前 5；发现阶段达到门槛可提前停止计数 |
| `min_final_points` | 最终至少保留的有效站位数，当前 5 |
| `station_selection` | `coverage` 按方位/距离覆盖压缩；`all_valid` 保留全部有效站位 |
| `max_final_stations` | coverage 模式每场景站位数上限，当前 20；all_valid 不受此上限限制 |
| `seed` | 随机种子；控制选择与放置随机性 |
| `max_candidates` | 每变体放置候选迭代上限，当前 2400；不是完整场景数 |
| `max_physical_trials` | 每变体完整摆放后的物理静置/检查尝试上限，当前 12 |
| `variant_timeout_s` | 单变体构造时间预算，当前 1800 秒；不是整个运行的时限 |

正数目标上限同时用于构建阶段的期望数量检查；目标不足默认报错，可用 CLI `--allow-partial-targets` 允许部分目标。`-1` 不意味着跳过资格检查。

### Case 与未显式填写的默认值

| Case | 任务 / 构造条件 |
|---|---|
| Case1 | pick；同一目标支撑面加 5–8 个表面物体，地面加 1 把椅子；表面物体靠近目标 |
| Case2 | pick；同一目标支撑面加 10–20 个表面物体，至少 2 个满足类别及大/高尺寸要求 |
| Case3 | open；不加障碍物，每 open 目标保存 1 个原场景及站位 |

| 可选字段（当前 JSON 省略） | 默认含义 |
|---|---|
| `categories` | 空列表，不额外按任务原始类别过滤 |
| `min_target_separation_m` | 有限额 pick 选择默认最小间距 0.75 m；不限量时取消距离筛除 |
| `assets_per_category` | 每类检索最多 6 个候选资产 |
| `case1_added_surface_count` / `case2_added_surface_count` | 闭区间 `[5,8]` / `[10,20]`；前者不含椅子 |
| `case1_surface_categories` / `case2_surface_categories` | 新增表面物体类别池，区别于 pick 目标白名单；具体默认列表见加载器 |
| `min_substantial` | Case2 至少 2 个大/高物体 |
| `settle` | 静置参数，默认 `require_source_stability: false`；说明见 [case_edits](../case_edits/README.md) |
| `views` | 构造对照图尺寸，默认 640×480；不等于站位可见性分辨率 |
| `station_config` | 站位几何覆盖项；默认半径约 0.793 m、间距约 0.111 m、320×240、目标至少 24 像素，地图分辨率 0.05 m、导航余量 0.02 m；seed 由配方统一设置 |

v3 额外要求 pick 类型白名单和 Case1 桌/台支撑面；同时启用 Case1/2 时，支撑面条件在共用目标筛选阶段生效。v3 还调整表面资产池和朝向/包围盒预筛，实际碰撞与静置检查仍保留。白名单只是语义约束，不代表实测成功率。

## 共用机器人文件

`rby1_position_head320.json` 中 `data_root`、`model_path`、`control_profile`、`control_hz`、`physics_dt`、`cameras` 的含义见 [robots](../robots/README.md)。该文件显式设置 `position-v1`；名称中的 head320 不设置分辨率，站位图像尺寸由 `station_config` 默认值决定。

站位是机器人底盘候选位姿，不是点云点或抓取点。构造 accepted 只说明场景和站位通过相应检查，不证明机器人任务成功。
