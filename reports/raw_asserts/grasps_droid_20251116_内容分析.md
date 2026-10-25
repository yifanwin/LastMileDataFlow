# grasps/droid 20251116 抓取库内容分析报告

- 状态：done
- 日期：2026-10-25
- 替代：无
- 关联：`reports/raw_asserts/`（本目录）、`reports/raw_asserts/thor_20251117_资产库内容分析.md`、molmo_spaces 文档（Grasp libraries / Asset libraries）

---

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| 实际路径 | `/data1/wenyifan/lmdf/molmospaces_data/mujoco/grasps/droid/20251116` |
| 对外路径 | `MLSPACES_ASSETS_DIR/grasps/droid`（软链，当前**失效**，见 §2.3） |
| 版本登记 | `grasps.droid = ["20251116"]`（`mujoco/mjthor_data_type_to_source_to_versions.json`） |
| 总体积 | 约 5.8 GB（`du`）；其中未过滤 JSON 5.31 GB、OBJ 0.40 GB、NPZ 仅 0.04 GB |
| UID 目录 | 1,675 个（另有 3 个顶层元数据文件；16 个目录没有 NPZ） |
| NPZ 文件 | 2,129 个：pickup 685、joint 1,444 |
| 有效 NPZ | 2,031 个；**98 个为空**（shape `(0,)`，仅 pickup） |
| 抓取总数 | 1,756,774 个（pickup 508,635 + joint 1,248,139） |
| 有有效抓取的 UID | 1,577 个（pickup 587、joint 1,036、两者都有 46） |
| NPZ schema | 仅一个键 `transforms`，`(N,4,4) float16`，位姿在物体/关节局部坐标系 |
| 数值质量 | 非空文件全部有限值；旋转正交误差 ≤ 8.2e-4、det ∈ [0.99913, 1.00089]（float16 精度） |
| 与当前 thor 库匹配 | thor 1,699 个资产中 800 个有可用抓取；pickup 587 个全部命中，joint 另有 777 个 UID 不在 thor 库内 |
| 主要问题 | 软链断链、98 个空 NPZ、`Lettuce` 全类无抓取、82 个 UID 无任何有效抓取、未过滤 JSON 占 99% 空间、大量 joint UID 无法通过内置查找命中 |

整体判断：这是一份**由 molmo_spaces 自带 mjcf2grasp 流水线生成、以 UID 为键的离线抓取库**，pickup 与 joint 两类数据格式统一、几何一致性良好，可以直接用于抓取采样。但它与当前安装的 thor 资产库只部分对齐：**pickup 部分对齐良好，joint 部分有约 3/4 的 UID 属于当前 thor 库中不存在的哈希命名资产**；同时存在少量空文件和断链，使用前需要按 §10 的清单做过滤。

---

## 2. 数据位置、来源与版本

### 2.1 目录树

```
molmospaces_data/
├── assets/
│   ├── grasps/droid -> /data1/wenyifan/molmospaces_data/mujoco/grasps/droid/20251116   # 断链
│   ├── objects/thor -> 同类断链
│   └── robots/rby1m -> 同类断链
└── mujoco/
    ├── mjthor_data_type_to_source_to_versions.json
    └── grasps/droid/20251116/          # 本报告对象
```

### 2.2 顶层元数据

| 文件 | 大小 | 内容 |
|---|---:|---|
| `pipeline_summary_stages0123.json` | 187 KB | 一次 Stage 0–3 运行的汇总：267 个对象、195 个产出过滤后抓取、0 个完成关节轴分析；**只覆盖该次运行的子集，不是全库清单** |
| `mjthor_resources_combined_meta.json.gz` | 35 KB | 81 个上游归档（`droid_<Category>.tar.zst`）到内部文件的映射 |
| `mjthor_resource_file_to_size_mb.json` | 3.1 KB | 上述归档的压缩体积（最大 `droid_RoboTHOR.tar.zst` 12.2 MB、`droid_Dresser.tar.zst` 9.1 MB） |

### 2.3 断链问题（会影响内置 API）

`assets/grasps/droid`、`assets/objects/thor`、`assets/robots/rby1m` 三个软链都写成 `/data1/wenyifan/molmospaces_data/...`，缺少 `lmdf` 一级，`[ -e ]` 判定为 BROKEN。真实数据在 `/data1/wenyifan/lmdf/molmospaces_data/...`。

修复（任选其一）：

```bash
B=/data1/wenyifan/lmdf/molmospaces_data
ln -sfn "$B/mujoco/grasps/droid/20251116" "$B/assets/grasps/droid"
ln -sfn "$B/mujoco/objects/thor/20251117" "$B/assets/objects/thor"
ln -sfn "$B/mujoco/robots/rby1m/20251224" "$B/assets/robots/rby1m"
# 或让代码整体指向真实根目录（仍需修内部软链）
export MLSPACES_ASSETS_DIR="$B/assets"
```

### 2.4 生成来源

数据由仓库内 `molmo_spaces/grasp_generation/`（即 mjcf2grasp）生成，关键约定：

- 采样器默认 `--num_samples 100000`；
- 夹爪为 `RobotiqGripper`（2F-85 变体），`tcp_offset = [0, 0, 0.155]`，写入文件前已把位姿平移到 TCP；
- 关节抓取在过滤阶段转换为关节体局部坐标：`T_joint_grasp = inv(T_world_joint) @ T_world_grasp`（`pipeline/articulation_test.py:307-316`）；
- 物理过滤每对象最多保留 `--max_successful_grasps 1000` 个，因此大量文件恰好为 1000。

---

## 3. 目录结构与命名约定

### 3.1 两种 NPZ

```
<uid>/
├── <uid>_grasps_filtered.npz                          # pickup
└── <joint_name>_grasps_filtered.npz                   # joint（joint_name 已含 uid 前缀）
```

示例：

```
Bowl_1/Bowl_1_grasps_filtered.npz
Laptop_14/Laptop_14_laptop_lid_14_joint_0_grasps_filtered.npz
Toaster_4/Toaster_4_joint_grasps_filtered.npz
```

这与文档中**用户抓取库**的 `<uid>/<robot>/grasps.npz`、`joint_grasps_<joint_name>.npz` 布局不同；内建 `droid` 库不按 robot 分目录，是 robot-agnostic 的。

### 3.2 每个 UID 目录的附属文件

| 文件 | 数量 | 说明 |
|---|---:|---|
| `<uid>_grasps_filtered.npz` / `<joint>_grasps_filtered.npz` | 2,129 | 本文分析对象 |
| `<joint>.obj` | 2,716 | 关节/整模预览网格（与 NPZ **不同坐标系**，见 §5.2） |
| `main.obj` | — | 整个可动物体的合并网格 |
| `joint_meshes_info.json` / `_filtered.json` | 1,051 / 1,049 | 关节几何、类型、轴、父体位姿 |
| `<...>_grasps.json` | 1,591 | 未过滤抓取（含质量、接触深度、采样点） |
| `<...>_grasps_object_info.json` | 960 + 少量 | 生成参数（`num_grasps`、`approach_distance` 等） |
| `*_filtered_grasps_visualization.png` | 13 | 过滤结果可视化 |
| `material_0.png` / `.mtl` | 1,093 / 1,080 | 网格材质 |
| `.processing_lock` / `.completed` | 37 / 36 | 流水线残留标记 |

---

## 4. NPZ 数据格式与数值特征

### 4.1 Schema

```python
with np.load(path) as z:
    assert z.files == ["transforms"]
    T = z["transforms"]          # (N, 4, 4), float16
```

- 每行是物体/关节局部坐标系下的齐次变换；`T[:3, :3]` 为旋转，`T[:3, 3]` 为 TCP 位置；
- 最后一行为 `[0,0,0,1]`，2,031 个非空文件全部精确成立；
- 无 NaN/Inf；
- float16 带来的精度：旋转矩阵正交误差最大 `8.2e-4`，det ∈ `[0.99913, 1.00089]`；如需高精度请先 `astype(np.float64)`。

### 4.2 N 的分布

| 指标 | pickup | joint |
|---|---:|---:|
| 文件数（非空） | 587 | 1,444 |
| 抓取数 | 508,635 | 1,248,139 |
| 中位数 | 1,000 | 1,000 |
| 恰好 1,000 | 483 | 1,033 |
| < 50 | 60 | 32 |
| < 10 | 42 | 16 |
| 最小 / 最大 | 1 / 1,000 | 1 / 1,000 |

全库 2,031 个非空文件中有 1,516 个恰好 1,000，符合“过滤后最多保留 1000”的设定。

### 4.3 平移范围

| 指标 | pickup | joint |
|---|---:|---:|
| 文件内 \|t\| 中位数（再取中位） | 0.094 m | 0.295 m |
| 单抓取最大 \|t\| | 0.477 m | 4.36 m |
| 全局包围盒 | x[-0.358,0.340] y[-0.476,0.466] z[-0.337,0.331] | 见 §5 |

joint 的大 \|t\| 不代表错误：NPZ 在关节体局部系，父体可能离物体根很远（最大 `parent_position` 约 4.46 m）。把 NPZ 换回 `<joint>.obj` 帧后，平移落点与网格一致（§5.2）。

### 4.4 重复与异常

- 2,027 / 2,031 个文件内完全无重复；
- 仅 4 个文件各有 1–2 个重复位姿：`Cabinet_33711c56`、`Cabinet_a2350162`、`Cabinet_f810e87c`、`Laptop_11_..._joint_0`；
- **98 个空 NPZ** 的 shape 是 `(0,)` 而不是 `(0,4,4)`，文件 214 B。`has_valid_pickup_grasps()` 能正确返回 `False`，但若直接 `np.load(...)["transforms"][:, :3, 3]` 会抛 `IndexError`，下游必须先判断 `ndim == 3`。

---

## 5. 坐标系与几何验证

### 5.1 存储坐标系

| 类型 | 坐标系 | 消费方式 |
|---|---|---|
| pickup | 物体局部坐标系（物体根） | `grasps_world = obj.pose @ grasps` |
| joint | 关节所属 body 的局部坐标系 | `grasps_world = joint_body_pose @ grasps`，`joint_body_pose` 取 `xpos/xmat[bodyid]` |

朝向约定可从数据侧确认：z 轴与“抓取点指向物体原点”的方向平均余弦为 -0.49，46% 的抓取小于 -0.7，说明 **+z 为接近方向（指向物体）**；x 轴为手指开合方向。

### 5.2 数值验证（joint）

把 NPZ 平移用 `joint_meshes_info_filtered.json` 的 `parent_position / parent_rotation` 变换回对象根帧，再与同名 `<joint>.obj` 的顶点求最近距离（随机抽 370 个 joint 文件）：

| 指标 | 结果 |
|---|---:|
| 最近顶点距离中位数 | **0.021 m** |
| P90 | 0.060 m |
| < 2 cm / < 5 cm / < 10 cm 的文件数 | 179 / 322 / 359 |
| `\|parent_position\| > 0.3 m` 的 115 个文件 | 中位 **0.0196 m** |

残差 2 cm 量级来自 TCP 前移与夹爪几何（抓取点位于表面外侧），不是坐标错误。结论：**NPZ 确实是关节体局部坐标，`joint_body_pose @ T` 能还原到场景世界系**，与文档和源码一致。

> 重要：`<joint>.obj` 与 NPZ 不在同一坐标系。直接用 NPZ 包围盒去比 `<joint>.obj` 会得到“抓取全在网格外”的假象；校验时必须先用 `parent_position/parent_rotation` 变换。

---

## 6. 规模与内容统计

### 6.1 按类别（Top 25，按 UID 数）

| 类别 | UID | pickup 文件 | 其中空 | pickup 抓取 | joint 文件 | joint 抓取 |
|---|---:|---:|---:|---:|---:|---:|
| Apple | 30 | 30 | 2 | 11,245 | 0 | 0 |
| Book | 30 | 30 | 0 | 30,000 | 0 | 0 |
| Bowl | 30 | 30 | 0 | 29,877 | 0 | 0 |
| Box | 30 | 30 | 7 | 15,032 | 120 | 116,785 |
| Bread | 30 | 30 | 21 | 4,127 | 0 | 0 |
| Cup | 30 | 30 | 0 | 29,113 | 0 | 0 |
| Egg | 30 | 30 | 0 | 29,306 | 0 | 0 |
| Egg_Cracked | 30 | 30 | 0 | 27,767 | 0 | 0 |
| Fridge | 30 | 0 | 0 | 0 | 73 | 61,492 |
| Laptop | 30 | 30 | 8 | 13,019 | 30 | 30,000 |
| Lettuce | 30 | 30 | **30** | **0** | 0 | 0 |
| Microwave | 30 | 0 | 0 | 0 | 30 | 26,173 |
| Pan | 30 | 30 | 0 | 30,000 | 0 | 0 |
| Plate | 30 | 30 | 0 | 30,000 | 0 | 0 |
| Pot | 30 | 30 | 0 | 29,465 | 0 | 0 |
| Potato | 30 | 30 | 1 | 23,719 | 0 | 0 |
| Soap_Bottle | 30 | 30 | 0 | 30,000 | 0 | 0 |
| Toaster | 30 | 0 | 0 | 0 | 35 | 14,095 |
| Alarm_Clock | 29 | 29 | 20 | 31 | 0 | 0 |
| Tomato | 19 | 19 | 3 | 2,888 | 0 | 0 |
| Cloth | 12 | 12 | 0 | 12,000 | 0 | 0 |
| Doorway | 10 | 0 | 0 | 0 | 18 | 14,775 |
| Doorway_Double | 9 | 0 | 0 | 0 | 30 | 30,000 |
| Cabinet | 8 | 0 | 0 | 0 | 8 | 7,121 |
| Cellphone | 8 | 8 | 0 | 8,000 | 0 | 0 |

全部 940 个“类别前缀”中，774 个只有 1 个实例（大量哈希命名的 Cabinet/Drawer/Dresser）。

### 6.2 空 NPZ 的类别分布（共 98）

| 类别 | 空文件 / 总文件 |
|---|---:|
| Lettuce | 30 / 30（整类为空） |
| Bread | 21 / 30 |
| Alarm_Clock | 20 / 29 |
| Laptop | 8 / 30 |
| Box | 7 / 30（仅 pickup） |
| Tomato | 3 / 19 |
| Apple | 2 / 30 |
| Clothes_Dryer | 2 / 2 |
| Potato / RoboTHOR_book_ai2_6_v / RoboTHOR_side_table_hopen / Toilet_Paper / Toilet_Paper_Used_Up | 各 1 |

这些对象仍有未过滤 JSON（候选抓取存在），说明是**物理/碰撞过滤后全部被拒绝**，不是数据缺失。`Lettuce`、`Bread`、`Alarm_Clock` 这类薄壁/易滑对象过滤通过率极低。

### 6.3 每个 UID 的 joint 文件数

| joint 文件数 | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 11 | 12 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| UID 数 | 841 | 106 | 24 | 47 | 2 | 8 | 5 | 1 | 1 | 1 |

多关节集中在 Dresser / Desk / Coffee_Table（最多 `Dresser_224_1` 12 个抽屉）。

### 6.4 过滤保留率（抽样 73 个未过滤 JSON）

- 候选抓取合计 692,719，过滤后 65,202，**保留 9.4%**；
- 单文件保留率中位数 **27.5%**，P10 3.0%，P90 76.3%，最大 95.0%；
- 没有任何文件保留率 > 99.9%，且过滤后统一截断到 1,000。

未过滤 JSON 中还保留 `roll_angles`（0–2.88 rad）、`standoffs`（0.10–0.174 m）、`contact_depths`（0–1）、`mesh_points`、`mesh_normals`、`collisions`，可用于重新过滤或质量排序；当前 NPZ **不含质量分数**，加载后无法按质量重排。

---

## 7. 覆盖与可用性

### 7.1 与当前 thor 20251117 资产库

| 指标 | 数量 |
|---|---:|
| thor 磁盘资产（同名 XML 判定） | 1,699 |
| 有任意可用抓取 | 800 |
| 有 pickup | 587（全部落在 thor 库内） |
| 有 joint | 259 |
| 同时有 pickup + joint | 46 |
| thor 有资产但无抓取目录 | 817 |
| 抓取目录但不在当前 thor 库 | 777 |

结论：

- **pickup 库与 thor 库对齐良好**（587 个 UID 全部存在）；
- **joint 库大部分是“孤儿”**：1,036 个 joint UID 中只有 259 个能对应到当前 thor 资产，其余 777 个是 `Cabinet_<hash>`、`Drawer_<hash>`、`Dresser_<hash>` 形式的旧版可动资产，当前 `objects/thor/20251117` 中找不到同名 XML。

影响：`get_grasp_libraries_for_object()` 会先 `locate_uid_package(uid)`，找不到包就返回空列表，因此这些孤儿 UID 用默认查询**取不到**。但 `get_joint_grasp_path()` 对“只指定一个库”的情况会跳过 UID 定位（源码注释明确说明这是 thor articulated 的 workaround）：

```python
# 可以命中孤儿 joint 文件
load_joint_grasps("Cabinet_7adc1205", "Cabinet_7adc1205_joint", grasp_libraries=["droid"])
```

### 7.2 与 procthor-10k-val 场景

| 指标 | 数量 |
|---|---:|
| 场景数 | 1,000 |
| 场景引用唯一 `asset_id` | 346 |
| 其中可 pickup | 141 |
| 其中可 joint | 49 |
| 任一可用 | 184（141 + 49 − 6） |
| 完全没有抓取目录 | 143 |
| 含至少 1 个可用抓取对象的场景 | **1,000 / 1,000** |

高频但无抓取的对象主要是不可抓的家具/结构件：`Dining_Table_203_1`、`Window_*`、`Chair_*`、`Wall_Decor_*`、`Sink_1`、`Television_*` 等。它们不构成本库缺陷，但做“全场景可抓取对象枚举”时应先按 UID 过滤。

### 7.3 与文档中的查找机制

- 资产库 → 抓取库映射：`thor → ["droid"]`；本机只安装了 `droid`，没有 `droid_objaverse`；
- 内建路径由 `get_pickup_grasp_path` / `get_joint_grasp_path` 拼出：
  - `grasps/droid/<uid>/<uid>_grasps_filtered.npz`
  - `grasps/droid/<uid>/<joint_name>_grasps_filtered.npz`
- 用户库 `<uid>/<robot>/grasps.npz` 的布局叠加在内建库之前，可用于覆盖个别对象的抓取；
- 内建库没有 robot 维度，所以文档里“按 robot 索引”的说明对这份库不适用。

---

## 8. 质量与异常清单

| # | 现象 | 影响 | 建议 |
|---|---|---|---|
| 1 | `assets/grasps/droid`、`objects/thor`、`robots/rby1m` 软链缺 `lmdf` 一级 | 内置 API 找不到文件 | 按 §2.3 重建软链 |
| 2 | 98 个 pickup NPZ 为空且 shape 为 `(0,)` | 直接索引 `[:, :3, 3]` 抛 `IndexError`；`get_pickup_grasp_path` 仍返回路径 | 用 `has_valid_pickup_grasps()` 或先判 `ndim==3 and len>0` |
| 3 | 82 个 UID 没有任何有效抓取（`Lettuce` 全 30 个、`Bread` 21 个、`Alarm_Clock` 20 个等） | 这些对象无法抓取 | 采样前用 `has_valid_pickup_grasps` / `has_valid_joint_grasps` 过滤 |
| 4 | 16 个 UID 目录没有 NPZ（只有未过滤 JSON/OBJ） | 目录存在但不可用 | 以 NPZ 是否存在为准 |
| 5 | `joint_meshes_info_filtered.json` 列了 1,553 个关节，其中 109 个没有对应 NPZ | 关节枚举与抓取能力不一致 | 以 `<joint>_grasps_filtered.npz` 是否存在为准 |
| 6 | 777 个 joint UID 不在当前 thor 库 | 默认查询取不到 | 显式传 `grasp_libraries=["droid"]`，或忽略 |
| 7 | float16 存储 | 旋转 det 有 ~1e-3 偏差 | 需要精确计算时转 float64 |
| 8 | 4 个文件内有个别重复位姿 | 采样去重时可忽略 | 可选去重 |
| 9 | 未过滤 JSON 占 5.31 GB（99% 体积） | 备份/传输成本高 | 只保留 NPZ 后约 0.04 GB；连同 OBJ 约 0.44 GB |
| 10 | 37 个 `.processing_lock`、36 个 `.completed` 残留 | 无功能影响 | 可清理 |
| 11 | `pipeline_summary_stages0123.json` 只统计 267 个对象 | 误当全库清单会低估规模 | 以 §6 的实测统计为准 |
| 12 | NPZ 不含质量/宽度/接触深度 | 无法按质量重采样 | 需要时回读同目录未过滤 JSON |

---

## 9. 在 MolmoSpaces 中的使用

```python
from molmo_spaces.utils.grasps import (
    get_pickup_grasp_path, has_valid_pickup_grasps, load_pickup_grasps,
    get_joint_grasp_path, has_valid_joint_grasps, load_joint_grasps,
)

# pickup：物体局部系 -> 世界系由 get_pickup_grasps(env, obj) 完成
if has_valid_pickup_grasps("Bowl_1"):
    T_local = load_pickup_grasps("Bowl_1", num_grasps=50)   # (50,4,4) float16

# joint：关节体局部系
joint = "Laptop_14_laptop_lid_14_joint_0"
if has_valid_joint_grasps("Laptop_14", joint):
    T_local = load_joint_grasps("Laptop_14", joint, num_grasps=50)
```

要点：

1. `load_*_grasps` 在 `N > num_grasps` 时**随机抽** `num_grasps` 个；`get_pickup_grasps/get_joint_grasps` 则按 `1e6` 取全量并可选附加翻转版本（`include_flipped=True`，`flip_grasps` 绕 z 轴转 180°）。
2. `get_joint_grasps` 返回 `(world_grasps, joint_body_pose)`；`joint_body_pose` 来自当前 `data.xpos/xmat`，会随关节运动更新。
3. 查找结果有缓存（`lru_cache`），用户库必须在首次调用前注册。
4. `has_valid_*` 只检查 `transforms.shape[0] >= num_grasps`，空 NPZ 会正确返回 `False`。

---

## 10. 使用建议

1. **先修软链**，否则所有内置路径都失效。
2. **采样前过滤**：`has_valid_pickup_grasps(uid)` / `has_valid_joint_grasps(uid, joint)`；不要假设目录存在就有抓取。
3. **只依赖 NPZ**：`<joint>.obj`、`main.obj`、未过滤 JSON 都只作调试/回溯，坐标系不完全一致。
4. **joint 查询显式传库名**：对哈希命名的 Cabinet/Drawer，用 `grasp_libraries=["droid"]` 绕过 UID 定位。
5. **pickup 与 joint 互斥性**：46 个 UID 两者都有，990 个 UID 只有 joint；对冰箱、微波炉、门等不要期待 pickup 抓取。
6. **抓取数按需抽样**：绝大多数文件 1,000 个，直接全量加载 2,129 个文件约 1.76M 位姿 × 16 个 float16 ≈ 56 MB 内存，可全量缓存；否则用 `num_grasps` 抽样。
7. **不要用 `Lettuce`/`Bread`/`Alarm_Clock` 做抓取评测**，这三类通过率接近 0。
8. **质量重排**需要回读未过滤 JSON 的 `quality`/`contact_depths`（NPZ 未保存）。

---

## 附录 A：复现命令

```bash
D=/data1/wenyifan/lmdf/molmospaces_data/mujoco/grasps/droid/20251116

# 规模
du -sh "$D"
find "$D" -mindepth 1 -maxdepth 1 -type d | wc -l
find "$D" -name '*.npz' | wc -l

# 空间构成
for ext in npz json obj; do
  find "$D" -name "*.$ext" -printf '%s\n' | awk -v e=$ext '{s+=$1} END{printf "%s %.2f GB\n", e, s/1073741824}'
done

# 空 NPZ 与 N 分布
/data1/wenyifan/lmdf/molmospaces_data/.venv-download/bin/python - <<'PY'
import glob, numpy as np
D="/data1/wenyifan/lmdf/molmospaces_data/mujoco/grasps/droid/20251116"
ns=[]; empty=[]
for p in glob.glob(D+"/*/*.npz"):
    with np.load(p) as z: t=z["transforms"]
    if t.ndim!=3: empty.append(p); ns.append(0)
    else: ns.append(len(t))
print("files",len(ns),"empty",len(empty),"total grasps",sum(ns))
PY
```

逐文件明细见同目录 `grasps_droid_20251116_files.csv`（2,129 行：`uid,file,kind,joint_name,num_grasps,empty,size_bytes`）。

## 附录 B：统计口径

- “有效抓取文件”指 `transforms.ndim == 3` 且 `N > 0`；
- “UID 有可用抓取”指该 UID 下至少有一个有效 NPZ；
- 类别前缀 = UID 去掉末尾 `_<数字>`；
- 与 thor 库的对齐以“磁盘上存在 `<...>/<uid>/<uid>.xml`”为准（1,699 个），不使用 `asset_id_to_object_type.json`（该表只有 1,653 条且缺 41 个资产）；
- 场景覆盖取 `procthor-10k-val/20251217_with_occupancy/*_metadata.json` 的 `objects[*].asset_id`；
- 几何验证使用 `joint_meshes_info_filtered.json` 的 `parent_position/parent_rotation`，对随机抽取的 370 个 joint 文件计算最近顶点距离（网格最多 2,500 顶点、抓取最多 250 个）。
