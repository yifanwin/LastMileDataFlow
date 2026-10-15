# 独立障碍物构造配方

加载器：`scenes/augmentation/config.py` 的 `load_obstacle_config`；Python 构建入口：`workflows/obstacle_augmentation.py` 的 `augment_obstacles`。当前 CLI 没有独立 case-edits 命令；常规构造优先使用 [scene_datasets](../scene_datasets/README.md) 和 build-scenes。

## 重点手动配置

- **换场景必须改** `data_root`、`dataset_dir`、`asset_library`、`house`、`target_instance`；后者是目录/metadata 中的实例 ID，不能直接填展示名 Cup_30。
- 核对 `baseline_config` 的站位/几何参数；可选 `input_snapshot` 只接受匹配的当前快照。无快照且不显式传 robot_config 时，代码按资产库位置推导机器人 XML，需满足现有资产布局。
- 按实验改 `variant_count`、`obstacle_count`、类别池、预算和 seed。**这些现有文件使用旧的 v1 障碍物规则，不能当成场景库 v2/v3 Case 参数。**
- 一般保持碰撞、支撑、静置约束，不为凑数量放宽。

## 四个文件区别

共同目标为 val103 的同一个杯子，每个尝试 5 个变体，物理检查最多 12 次、每变体 900 秒。

| 文件尾缀 | 新增总数闭区间 | 类别 / 条件 | max_candidates |
|---|---|---|---|
| `case1` | 1–3 | construction Case1；恰好一把地面椅子，余下表面物体 | 256 |
| `case2` | 6–12 | construction Case2；纯表面，至少两个符合大/高物体条件 | 1200 |
| `chair_debug` | 1 | 仅椅子；无 construction，不声明 Case 条件 | 64 |
| `random_1to3` | 1–3 | 必选椅子，表面候选 Bowl/Plate/Bottle；无 construction | 64 |

v1 Case1 不要求新增 5–8 个表面物体；v1 Case2 是 6–12，不是现代场景配方的 10–20。带 construction 的两个文件还会检查目标抓取类别资格。

## 顶层字段

| 字段 | 含义 |
|---|---|
| `data_root` | 数据根目录，相对仓库根目录 |
| `schema_version` | 当前文件均为 procedural-obstacles-v1；与 construction.rule_version 必须匹配 |
| `baseline_config` | 站位几何/执行配置 JSON，相对仓库根目录；不是机器人文件 |
| `dataset_dir` / `asset_library` | 原场景目录 / 新增物体资产库，相对 data_root |
| `house` | 房屋编号，当前 103 |
| `target_instance` | 目标实例 ID，用于在该房屋中查找 pick 任务 |
| `variant_count` | 变体尝试数，不保证全部 accepted |
| `seed` | 放置和资产选择随机种子 |
| `obstacle_count` | 新增物体总数的闭区间，包含椅子；不包含原场景已有物体 |
| `mandatory_categories` | 必须选入的类别；当前 Case1 强制一把 Chair，Case2 必须为空 |
| `ground_categories` | 允许放在地面的新增资产类别池 |
| `surface_categories` | 允许放在目标支撑面上的新增类别池；实际选择还受环境映射与资产可用性约束 |
| `assets_per_category` | 每类别最多检索的资产数量，当前 6 |
| `max_candidates` | 每变体候选放置迭代上限，不是完整场景数 |
| `max_physical_trials` | 完整摆放后物理静置/检查尝试上限 |
| `variant_timeout_s` | 单变体构造时间预算，单位秒 |
| `settle` | 静置参数对象，见下节 |
| `views` | 对照图渲染设置；width/height 当前 640×480 像素；仅 record_views 开启时生成图 |
| `construction` | Case 约束对象；省略则不声明 Case 构造条件 |

可选 `input_snapshot` 为原场景快照目录，相对 data_root；`record_views` 默认 false，需显式设 true 才生成构造对照图。

## construction 嵌套字段

| 字段 | 含义 |
|---|---|
| `case` | Case1 / Case2，采用与 schema 对应的规则 |
| `min_prefilter_points` | 原场景至少有效站位数，当前 5 |
| `max_final_stations` | coverage 选点的最大站位数，当前 20 |
| `registered_assets_only` | true 时只从注册资产中检索 |

未写的常用默认项：`rule_version=obstacle-cases-v1`；`min_final_points=1`（不同于场景配方的 5）；`station_selection=coverage`；`surface_spacing_m=0.055` 为表面候选点间距；`near_target_m=0.85` 为 Case1 表面物体靠近目标的范围；`surface_alignment_jitter_rad=0.12` 为表面对齐朝向扰动。

Case2 的 `min_substantial=2`，候选类别默认 Kettle/Toaster/CoffeeMachine/DeskLamp/Vase，且实测高度 ≥ `substantial_height_m=0.20` 或宽深最大值 ≥ `substantial_width_m=0.25` 才计数。类别和尺寸同时要求，不凭名字认定大物体。

## settle 嵌套字段

现有文件仅填 `require_source_stability=false`：不要求所有源场景物体绝对静止，仍保留新增物体的稳定性、碰撞、支撑及与等时长未编辑场景的影响比较，不是关闭物理检查。

需要调整静置预算时可显式填写 `settle_s`（至少静置时长，默认 1 秒）、`max_settle_s`（最多 3 秒）、`window_s`（观察窗口 0.25 秒）。速度/漂移等物理阈值保持默认，完整定义见 `runtime/construction/preparation.py` 的 SettleConfig。构造通过不等于任务成功。
