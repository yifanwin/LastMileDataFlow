# procthor-10k-val 20251217_with_occupancy 场景库内容分析报告

- 状态：done
- 日期：2026-10-25
- 替代：无
- 关联：`reports/raw_asserts/thor_20251117_资产库内容分析.md`、`mujoco/mjthor_data_type_to_source_to_versions.json`、MolmoSpaces Resources 文档（Assets naming / Installation / Symlink directory structure）

---

## 1. 结论摘要

| 项目 | 结果 |
|---|---|
| 实际路径 | `/data1/wenyifan/lmdf/molmospaces_data/mujoco/scenes/procthor-10k-val/20251217_with_occupancy` |
| 对外视图 | `MLSPACES_ASSETS_DIR/scenes/procthor-10k-val/`（按文件软链到上述目录） |
| 版本登记 | `scenes.procthor-10k-val = ["20251217_with_occupancy"]` |
| 场景数量 | 1,000 个（`val_0` … `val_999`，索引连续无缺口，无重复场景） |
| 数据规模 | 1.81 GB（`du` 报 1.9 GB，含块开销）；XML 占 86.2%，其中近一半是 `_ceiling.xml` 变体 |
| 文件构成 | 每场景 6 项：`val_N.json`、`val_N_metadata.json`、`val_N.xml`、`val_N_ceiling.xml`、`val_N_map.png`、`val_N_assets/`；另有 3 个共享索引文件 |
| 内容类型 | ProcTHOR 程序化生成的室内住宅场景，导出为 MuJoCo MJCF；附带俯视占据栅格图（occupancy map） |
| 平均场景 | 3.5 个房间、28.3 段墙、3.2 扇门、2.7 扇窗、68.8 个物体（含子物体）、97 m² 地面 |
| 完整性 | 1,000 个场景文件全齐；1,000 个 MJCF 全部可解析；1,244,142 条 mesh/texture 引用 0 缺失 |
| 可加载性 | 通过 `assets/` 视图可被 MuJoCo 直接加载（实测 4 个场景，0.38–2.08 s/场景） |
| 软链视图 | `molmospaces_data/assets/` 下 59,118 个软链已全部重建为相对链接，指向同级 `mujoco/` 实际文件，0 失效 |
| 变体差异 | `_ceiling.xml` 是主 XML 的严格超集：全库 +3,534 个天花板 body；另有 315 个场景多出 1,067 个物体 body。`metadata` 的 body/site 计数与 `_ceiling.xml` 精确一致，而不是与主 XML 一致 |
| 语义缺口 | 3,309 个 JSON 物体（全部 `CreditCard`/`KeyChain`/`Watch` 及部分 `GarbageCan` 等，占 4.8%）没有导出到任何 MJCF/metadata；此外主 XML 在 315 个场景中比 `_ceiling.xml` 少 1,067 个物体 body |

整体判断：这是一份**结构完整、几何与物理信息自包含、路径视图有效**的 ProcTHOR-10k 验证集 MuJoCo 导出。场景 XML、物体资产、占据图三部分齐全，可通过 `MLSPACES_ASSETS_DIR` 直接加载，适合导航、操作与占据栅格相关实验。使用前注意两点：① 必须经 `assets/` 视图（或等价布局）加载，XML 中的 `../../objects/thor` 才能解析；② JSON 中的少量小物体没有进入仿真。

---

## 2. 数据位置与来源

### 2.1 路径与版本

```
molmospaces_data/
├── assets/                                  # 资源管理器对外视图（相对软链，全部有效）
│   ├── objects/thor -> …/mujoco/objects/thor/20251117
│   ├── scenes/procthor-10k-val/             # 每文件软链到本报告对象
│   ├── grasps/droid -> …/mujoco/grasps/droid/20251116
│   └── robots/{panda_omron, rby1m}
└── mujoco/                                  # 实际缓存（版本化目录）
    ├── scenes/procthor-10k-val/20251217_with_occupancy/   # 本报告对象
    ├── objects/thor/20251117/               # 共享物体资产，3.2 GB / 110,148 文件
    ├── robots/                              # 66 MB
    ├── grasps/                              # 5.8 GB
    └── mjthor_data_type_to_source_to_versions.json
```

版本登记文件内容：

```json
{
  "scenes":  {"procthor-10k-val": ["20251217_with_occupancy"]},
  "objects": {"thor": ["20251117"]},
  "robots":  {"rby1m": ["20251224"]},
  "grasps":  {"droid": ["20251116"]}
}
```

### 2.2 构建信息（`housegen_build_settings.json`）

| 字段 | 值 | 说明 |
|---|---|---|
| `flavor` / `split` | `procthor-10k` / `val` | ProcTHOR-10k 验证集 |
| `identifier` | `121525_Linux_332_no_filter` | 2025-12-15 构建，MuJoCo 3.3.2，未做 license 过滤 |
| `mujoco-version` | `3.3.2` | 导出时版本 |
| `settle_time` | `20.0` | 每场景物理沉降 20 s |
| `use_presaved_houses` | `true` | 使用预生成 ProcTHOR house JSON |
| `copy_non_settled` | `true` | 未完全沉降的物体也保留 |
| `bundle_assets` | `false` | 物体资产不打包进场景，走 `../../objects/thor/...` 共享引用 |
| `param_geom_margin` | `0.001` | 几何 margin |
| `param_freejoint_damping` / `frictionloss` | `0.001` / `0.001` | 自由关节阻尼与摩擦损失 |
| `use_sleep_island` | `false` | 关闭 sleep island |
| `export_lights` | `false` | **JSON 中的程序化点光源不导出**，MJCF 每场景只有 1 个方向光 |
| `assets_dir` / `save_dir` | `/root/mujoco-thor/assets` / `/weka/prior-default/...` | 原始构建机路径，与当前机器无关 |
| `datetime` | `12-15-25 04:04:16` | 构建完成时间 |

### 2.3 打包与分片

- `mjthor_resource_file_to_size_mb.json`：**1,001 个 tar.zst 分片**，单分片 0.001–0.366 MB，合计 **90.7 MB（压缩）**。
- `mjthor_resources_combined_meta.json.gz`：每个分片固定 6 个条目（5 个场景文件 + 1 个 `_assets` 目录），每分片资产数 0–170（中位数 48）。
- 压缩后很小、解压后 1.81 GB：XML 为高度重复的文本，且大部分几何/纹理来自共享 `objects/thor`，场景包本身只含墙体/地板/天花板的轻量 OBJ。
- `val_N_map.png` 的 mtime 为 **2026-04-22**，其余场景文件为 **2025-12-15**。结合版本名 `with_occupancy` 可推断：占据图是场景导出后补生成的一层数据。

---

## 3. 目录结构与文件清单

### 3.1 单场景文件

```
20251217_with_occupancy/
├── val_0.json                  # ProcTHOR 原始房屋定义（房间/墙/物体/门窗/程序参数）
├── val_0_metadata.json         # 导出后物体级元数据（hash 名、asset_id、类别、静态性、名称映射）
├── val_0.xml                   # MuJoCo MJCF 主场景（无天花板）
├── val_0_ceiling.xml           # 同上 + 每房间天花板几何
├── val_0_map.png               # 俯视占据栅格图（黄色=自由，黑色=占据/外部）
└── val_0_assets/               # 本场景专用的墙体/地板/天花板 OBJ（94 个文件，380 KB）
```

### 3.2 全库清单

| 文件 | 数量 | 总大小 | 占比 | 格式 |
|---|---:|---:|---:|---|
| `val_N.xml` | 1,000 | 778.9 MB | 43.0% | MuJoCo MJCF（XML） |
| `val_N_ceiling.xml` | 1,000 | 783.2 MB | 43.2% | MuJoCo MJCF（含天花板） |
| `val_N_metadata.json` | 1,000 | 79.9 MB | 4.4% | JSON |
| `val_N.json` | 1,000 | 78.3 MB | 4.3% | JSON |
| `val_N_map.png` | 1,000 | 35.6 MB | 2.0% | PNG，8-bit RGB |
| `val_N_assets/` | 1,000 目录 | 55.2 MB | 3.0% | Wavefront OBJ |
| `housegen_build_settings.json` | 1 | 1.1 KB | — | JSON |
| `mjthor_resource_file_to_size_mb.json` | 1 | 42.8 KB | — | JSON |
| `mjthor_resources_combined_meta.json.gz` | 1 | 145.5 KB | — | gzip JSON |
| **合计** | 6,003 顶层条目 | **1.81 GB** | 100% | — |

> 去掉 `_ceiling.xml` 后，唯一场景内容约 1.03 GB。若磁盘受限，可在预处理阶段删除 `_ceiling.xml`（需要渲染封闭空间时再恢复）。

---

## 4. 各文件格式详解

### 4.1 `val_N.json` — ProcTHOR 房屋定义

顶层字段（以 `val_0` 为例）：

| 字段 | 类型 | 示例/规模 | 说明 |
|---|---|---|---|
| `rooms` | list | 7 | 房间；含 `id`、`roomType`、`floorPolygon`（x/z 平面多边形，y=0）、`floorMaterial`、`ceilings`、`layer` |
| `walls` | list | 46 | 墙段；含 `id`、`roomId`、`polygon`（4 点四边形）、`empty`、`material` |
| `objects` | list | 48 顶层 / 142 递归 | 物体；含 `id`、`position`、`rotation`、`assetId`、`kinematic`、`children`、`layer`；子物体表示“放在某表面上” |
| `proceduralParameters` | dict | 7 键 | `floorColliderThickness`、`receptacleHeight`、`lights`（点光/方向光）等 |
| `doors` | list | 6 | 门/门洞；含 `room0/room1/wall0/wall1`、`assetId`、`openable`、`openness`、`holePolygon` |
| `windows` | list | 4 | 窗；字段与门类似 |
| `metadata` | dict | 5 键 | `agent`、`agentPoses`（4 个：arm/default/locobot/stretch）、`roomSpecId`、`schema`、`warnings` |

全库规模：

| 指标 | 总量 | 每场景 min / 中位 / max / 均值 |
|---|---:|---|
| 房间 | 3,534 | 1 / 3 / 10 / 3.53 |
| 墙段 | 28,338 | 8 / 26 / 90 / 28.34 |
| 门 | 3,226 | 1 / 3 / 10 / 3.23 |
| 窗 | 2,698 | 0 / 2 / 10 / 2.70 |
| 物体（递归含子物体） | 68,838 | 6 / 63.5 / 242 / 68.84 |
| 顶层物体 | 27,178 | 3 / 25 / 84 / 27.18 |
| `kinematic=true` 物体 | 23,426 | 3 / 22 / 69 / 23.43 |

其他事实：

- `metadata.schema` 全部为 `1.0.0`；`warnings` 全部为空；每个场景固定 4 个 agent pose。
- `roomSpecId` 共 **16 种**：`4-room`(242)、`kitchen-living-room`(100)、`bedroom-bathroom`(92)、`living-room`(69)、`bedroom`(58)、`kitchen`(57)、`bathroom`(54)、`kitchen-living-bedroom-room`(50)、`kitchen-living-bedroom-room2`(49)、`2-bed-1-bath`(37)、`12-room-3-bed`(36)、`5-room`(35)、`2-bed-2-bath`(34)、`7-room-3-bed`(31)、`12-room`(29)、`8-room-3-bed`(27)。
- 房间类型仅 4 类：Bedroom 1,101、LivingRoom 869、Bathroom 837、Kitchen 727。
- 地面面积（房间多边形求和）中位数 **78.0 m²**，均值 97.0 m²，范围 5.5–406.4 m²。
- 房屋平面尺寸中位数：x 9.75 m × z 9.48 m；最大 21.8 m × 33.2 m。

### 4.2 `val_N_metadata.json` — 物体级导出元数据

只有一个顶层键 `objects`，是以 **hash 名**为键的字典。每个条目字段固定：

| 字段 | 示例 | 说明 |
|---|---|---|
| `hash_name` | `doorway_eab5e..._1_0_4` | MJCF 中的 body 前缀 |
| `asset_id` | `Doorway_Double_3` | THOR 资产 ID |
| `object_id` | `door\|4\|5` | 对应 `val_N.json` 中的 id；门窗也可对应 |
| `category` | `Doorway` / `Chair` / … | 93 个类别 |
| `object_enum` | `thor_obj` | 全部 71,453 条均为 `thor_obj` |
| `is_static` | `true/false` | 静态 31,733（44.4%），动态 39,720（55.6%） |
| `name_map` | `{bodies:{}, joints:{}, sites:{}}` | hash 名 → 可读名映射，合计 183,173 body、61,766 joint、50,952 site |
| `mjcf_path` | `""` | 全部为空（MJCF 已内联到场景 XML） |
| `parent` / `children` / `room_id` | — | 物体层级与房间归属 |

用途：语义查询（类别、房间归属）、静态/动态筛选、把 MuJoCo body/joint/site 名映射回 THOR 资产。

> 与 JSON 的差异：metadata 缺少 **3,309** 个 JSON 物体（见 §8.4），且没有任何 metadata 条目是 JSON 中不存在的。
> 与 XML 的对应关系：metadata 的物体 body 数（183,173）和 site 数（50,952）与 `_ceiling.xml` 精确一致，主 `val_N.xml` 少 1,067 个物体 body（见 §8.5）。

### 4.3 `val_N.xml` — 主 MJCF（无天花板）

结构（以 `val_0` 为例）：

```
<mujoco model="val_0">
  <compiler angle="radian" autolimits="true" .../>
  <option integrator="implicitfast" cone="elliptic" .../>
  <size njmax="5000" nconmax="5000"/>
  <visual>...</visual>
  <default>
    <default class="/">
      <default class="__VISUAL_MJT__">        # 纯视觉几何，无碰撞、质量 1e-8
      <default class="__STRUCTURAL_MJT__">    # 结构碰撞（地板、物体外壳）
      <default class="__STRUCTURAL_WALL_MJT__"> # 墙体碰撞
      <default class="__DYNAMIC_MJT__">       # 动态物体碰撞
      <default class="__ARTICULABLE_DYNAMIC_MJT__"> # 门/抽屉等活动件
  <asset>
    <texture ... file="../../objects/thor/..."/>
    <material .../>
    <mesh ... file="val_N_assets/..."/>
    <mesh ... file="../../objects/thor/..."/>
  <worldbody>
    <geom name="floor" class="__STRUCTURAL_MJT__" type="plane" .../>
    <light pos="1 -1 1.5" directional="true" .../>
    <body name="room_2">   <geom class="__VISUAL_MJT__" type="mesh" mesh="room_2" .../> </body>
    <body name="wall_2_0"> <geom class="__VISUAL_MJT__" .../>
                            <geom class="__STRUCTURAL_WALL_MJT__" .../> </body>
    <body name="diningtable_<hash>_1_0_2" pos=... quat=...>
      <joint type="free" damping="0.001" frictionloss="0.001"/>
      <body name="..._1_1_2"> <geom class="__VISUAL_MJT__"/><geom class="__DYNAMIC_MJT__"/> </body>
    </body>
    ...
  </worldbody>
  <contact><exclude .../></contact>
</mujoco>
```

关键点：

- **完全内联**：`<include>` 数量为 0；每个场景把用到的物体网格/材质/纹理全部声明在文件内。
- **引用两类路径**：
  - 场景专用：`val_N_assets/wall_*.obj`、`room_*.obj`、`ceiling_*.obj`；
  - 共享物体：`../../objects/thor/<Category>/...`（纹理 PNG、网格 OBJ）。
- **地板**：世界坐标系中一个无限平面 `floor`（z=0），房间地面只是视觉 mesh（`__VISUAL_MJT__`，无碰撞）。
- **墙体**：每段墙一个 body，含 1 个视觉 mesh + 1–4 个 `*_collision_mesh_*.obj` 凸分解碰撞体。
- **自由关节**：用 `<joint type="free">` 表示，不使用 `<freejoint>` 标签；阻尼与摩擦损失均为 0.001。
- **关节统计**：free 41,628、slide 9,126、hinge 8,734（合计 59,488）。门/抽屉/柜门以 hinge/slide 表示。
- **site**：50,896 个，主要是 `*_Receptacle*` 放置区域（box 类型，group 5，红色半透明），用于任务采样/放置。
- **contact exclude**：90,886 条，用于关闭物体内部件之间的接触。
- **光源**：每场景仅 1 个方向光（`export_lights=false`，JSON 里的点光源未导出）。
- **与 ceiling 变体的差异**：主 XML 不含天花板，且在 315 个场景中比 `_ceiling.xml` 少 1,067 个物体 body（Box、Pen、RemoteControl、CellPhone、Candle、ToiletPaper 等小物体）。`metadata` 的 body/site 计数与 `_ceiling.xml` 精确一致；需要完整物体集合时不要使用主 XML。

全库 XML 规模（1,000 场景合计）：

| 元素 | 总数 | 每场景中位数 | 说明 |
|---|---:|---:|---|
| `body` | 213,328 | 196.5 | 物体 182,106 + 墙 27,688 + 房间 3,534 |
| `geom` | 1,721,622 | 1,610.5 | 含 5,000 个 `<default>` 内模板 geom；实际 1,716,622 |
| `mesh` | 1,185,979 | 1,211.5 | 每实例单独声明 |
| `material` | 109,282 | 112 | — |
| `texture` | 58,163 | 60 | — |
| `joint` | 59,488 | 52 | free/slide/hinge |
| `site` | 50,896 | 44 | 放置区域 |
| `exclude` | 90,886 | — | 接触排除 |
| `light` | 1,000 | 1 | 每场景 1 个 |

geom 的 class 分布（实际 geom）：`__DYNAMIC_MJT__` 691,493、`__STRUCTURAL_MJT__` 443,910、`__ARTICULABLE_DYNAMIC_MJT__` 292,665、`__VISUAL_MJT__` 247,962、`__STRUCTURAL_WALL_MJT__` 40,592。

### 4.4 `val_N_ceiling.xml` — 含天花板变体（主 XML 的严格超集）

- 1,000/1,000 场景：主 XML 的 body 名集合是 ceiling 变体的子集（不存在只属于主 XML 的 body）。
- 每个房间增加 1 个 `ceiling_N` body + `ceiling_N.obj` mesh，全库 **+3,534 body / +3,534 mesh**。
- 另外 **315 个场景**中，ceiling 变体还比主 XML 多 **1,067 个物体 body**（Box、Pen、RemoteControl、CellPhone、Candle、ToiletPaper 等小物体；单场景最多多 22 个）。全库 body 增量合计 **4,601**（= 3,534 天花板 + 1,067 物体）。
- 每场景增加 1–26 个 body，中位 4 个。
- `val_N_metadata.json` 的物体 body 数（183,173）与 ceiling 变体完全一致，site 数（50,952）也与 ceiling 变体完全一致；因此 **metadata 对应 ceiling 变体**。
- 主 XML 用于无顶棚导航/操作；需要完整物体集合、渲染封闭空间或与 metadata 严格对齐时用 `_ceiling.xml`。

### 4.5 `val_N_map.png` — 俯视占据栅格图

| 属性 | 值 |
|---|---|
| 数量 | 1,000（每场景一张） |
| 格式 | PNG，8-bit/color RGB，非交错 |
| 尺寸 | 宽 730–7,767 px（中位 2,602），高 834–5,910 px（中位 2,697） |
| 文件大小 | 5.3–117.1 KB（中位 30.8 KB） |
| 颜色 | 仅两类：黑 `(0,0,0)` 与黄 `(255,255,0–8)`；抽样 100 张未发现其他颜色 |
| 语义 | **黄色 = 自由空间；黑色 = 墙体/家具等占据区域 + 房屋外部** |
| 分辨率 | **200 px/m（5 mm/像素）**；实测约 199.3–200.8 px/m，中位 199.8 |
| 边距 | 至少约 1 m（200 px）；中位 1.0–1.6 m，最大约 4.7 m（由外墙/世界包围盒决定） |
| 坐标系 | 图像轴与世界轴交换且反向：**`img_x ≈ -world_z`，`img_y ≈ -world_x`**；用房间多边形栅格化验证，IoU 0.80–0.85（正确方向）vs 0.40–0.60（其他方向） |
| 生成时间 | 文件 mtime 2026-04-22，晚于场景 XML（2025-12-15） |

占据统计（每 5 个场景取 1，共 200 张）：

- 房屋内部自由空间比例：中位 **88.1%**，均值 88.0%，范围 59.4%–100%。
- 图像中“外部”黑色区域比例：中位 54.8%，范围 24.3%–91.5%（取决于房屋在画布中的占比）。
- 黄色面积换算回物理面积后，抽样 8 个场景约为房间多边形面积的 **77%–85%**；差值来自墙厚与家具脚印。

使用注意：

1. 没有配套标定文件（无 JSON/XML sidecar），必须按 200 px/m + 黄区包围盒自行建立像素↔世界坐标映射。
2. 图像轴与 `val_N.json` 的 (x, z) 不是直接对应关系；做 2D 规划/可视化时需转置并翻转。
3. 图中黑色同时表示“占据”和“外部”，若需要区分，应对黑色做边界连通域分析（从图像四边 flood fill 得到外部，其余黑色为室内障碍）。
4. 占据图只表达几何占用，不包含语义类别；语义请查 `val_N_metadata.json`。

推荐的像素↔世界换算（由黄区包围盒标定）：

```
u = -200 * z + (x0 + 200 * z_max)
v = -200 * x + (y0 + 200 * x_max)
```

其中 `(x0, y0)` 是黄色区域左上角像素，`x_max/z_max` 是房间多边形的最大坐标。

### 4.6 `val_N_assets/` — 场景专用 OBJ

- 内容：`room_*.obj`（房间地面，单面片）、`ceiling_*.obj`（天花板）、`wall_*.obj`（墙体视觉）、`wall_*_collision_mesh_*.obj`（凸分解碰撞体）。
- 规模：每场景 16–170 个文件（中位 48），13.7–176.4 KB（中位 50.4 KB），全库 54,112 个文件 / 55.2 MB。
- 几何极简：墙体/地板 OBJ 多为 8–16 顶点、12–26 面；碰撞体甚至只有 8 顶点 / 12 面。它们是低成本代理几何，不是高精度网格。
- 共享物体（椅子、床、杯子等）**不在** `val_N_assets/` 内，而在 `mujoco/objects/thor/20251117/`，由 XML 的 `../../objects/thor/...` 引用。

### 4.7 共享索引文件

| 文件 | 作用 |
|---|---|
| `housegen_build_settings.json` | 构建参数（flavor/split/identifier/settle_time/导出开关等），见 §2.2 |
| `mjthor_resource_file_to_size_mb.json` | 1,001 个 tar.zst 分片的压缩体积（MB） |
| `mjthor_resources_combined_meta.json.gz` | 每个分片包含的文件清单与资产文件列表；100% 分片为 6 个顶层条目 |

---

## 5. 场景内容统计

### 5.1 每场景规模分布

| 指标 | min | P25 | 中位 | P75 | P95 | max | 均值 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 房间数 | 1 | 2 | 3 | 4 | 10 | 10 | 3.53 |
| 墙段数 | 8 | 14 | 26 | 36 | 68 | 90 | 28.34 |
| 门数 | 1 | 1 | 3 | 4 | 9 | 10 | 3.23 |
| 窗数 | 0 | 1 | 2 | 4 | 6 | 10 | 2.70 |
| 物体数（递归） | 6 | 32 | 63.5 | 93 | 159 | 242 | 68.84 |
| 地面面积 (m²) | 5.5 | 39.6 | 78.0 | 126.6 | 261.9 | 406.4 | 97.0 |
| XML body 数 | 29 | 100 | 196.5 | 278 | 505 | 675 | 213.3 |
| XML geom 数 | 103 | 787 | 1,610.5 | 2,346 | 3,811 | 5,150 | 1,721.6 |
| XML mesh 数 | 62 | 577 | 1,211.5 | 1,681 | 2,273 | 2,971 | 1,186.0 |
| XML joint 数 | 2 | 26 | 52 | 82 | 144 | 222 | 59.5 |
| XML site 数 | 2 | 23 | 44 | 69 | 127 | 176 | 50.9 |
| `_assets` 文件数 | 16 | 24 | 48 | 66 | 140 | 170 | 54.1 |
| `_assets` 大小 (KB) | 13.7 | 27.4 | 50.4 | 69.0 | 137.4 | 176.4 | 55.2 |

### 5.2 单文件大小分布

| 文件 | min | 中位 | P95 | max | 总计 |
|---|---:|---:|---:|---:|---:|
| `val_N.xml` | 46.3 KB | 765.2 KB | 1.61 MB | 2.14 MB | 778.9 MB |
| `val_N_ceiling.xml` | 46.6 KB | 768.7 KB | 1.65 MB | 2.16 MB | 783.2 MB |
| `val_N.json` | 14.4 KB | 74.4 KB | 185.3 KB | 236.7 KB | 78.3 MB |
| `val_N_metadata.json` | 7.9 KB | 73.6 KB | 186.9 KB | 261.3 KB | 79.9 MB |
| `val_N_map.png` | 5.3 KB | 30.8 KB | 83.0 KB | 117.1 KB | 35.6 MB |
| `val_N_assets/` | 13.7 KB | 50.4 KB | 137.4 KB | 176.4 KB | 55.2 MB |

### 5.3 物体类别（metadata，共 93 类 / 71,453 个）

| 排名 | 类别 | 数量 | 排名 | 类别 | 数量 |
|---:|---|---:|---:|---|---:|
| 1 | Chair | 5,384 | 11 | Bowl | 1,678 |
| 2 | Painting | 4,164 | 12 | Television | 1,566 |
| 3 | Doorway | 2,956 | 13 | Statue | 1,455 |
| 4 | Window | 2,698 | 14 | HousePlant | 1,399 |
| 5 | Book | 2,383 | 15 | Vase | 1,319 |
| 6 | DiningTable | 2,177 | 16 | Pen | 1,316 |
| 7 | Pencil | 1,813 | 17 | Dresser | 1,277 |
| 8 | RemoteControl | 1,799 | 18 | Bed | 1,229 |
| 9 | CellPhone | 1,790 | 19 | GarbageCan | 1,089 |
| 10 | Pillow | 1,742 | 20 | Egg | 1,047 |

完整长尾还包括 Box 1,036、DeskLamp 973、SprayBottle 958、Mug 932、TVStand 874、Plate 870、Laptop 863、Toilet 836、Sink 826、Faucet 826 等。类别覆盖典型的厨房、卧室、客厅、卫生间、装饰与可操作小物体。

静态/动态：静态 31,733（44.4%），动态 39,720（55.6%）。动态物体在 XML 中带 `type="free"` 关节，可被机器人推动或抓取。

### 5.4 场景语义分层

JSON 中 `layer` 的分布（物体 + 房间）：`Procedural1` 27,195、`Procedural2` 22,636、`Procedural3` 12,493、无 layer 7,539（7,215 个物体 + 324 个房间；物体侧以 Painting、Chair、DiningTable、Book 等为主）、`Procedural0` 2,509。层信息可用于区分固定结构、程序化放置物体与程序化房间。

### 5.5 运行时规模（实测 4 个场景）

通过 `assets/` 视图（`MLSPACES_ASSETS_DIR/scenes/procthor-10k-val/`）用 MuJoCo 3.5.0 加载；2026-10-25 复核 `val_0`/`val_1`/`val_500`，各项计数与下表一致：

| 场景 | 加载耗时 | nbody | ngeom | nmesh | nq | njnt | nsite | 平均质量 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `val_0` | 2.08 s | 415 | 3,492 | 2,458 | 687 | 129 | 112 | 8.2 kg |
| `val_1` | 0.38 s | 141 | 879 | 601 | 161 | 35 | 29 | 10.8 kg |
| `val_100` | 1.23 s | 271 | 2,604 | 2,021 | 424 | 76 | 60 | 12.0 kg |
| `val_500` | 0.51 s | 180 | 1,360 | 1,002 | 240 | 48 | 33 | 13.3 kg |

`val_0` 的 `nq = 687 = 93×7 (free) + 23 (slide) + 13 (默认 hinge)`，与 XML 关节统计一致。`_ceiling.xml` 在 `val_0` 上增加 7 个 body / 7 个 geom / 7 个 mesh，加载时间基本不变。

---

## 6. 占据图（occupancy）专项说明

版本名 `with_occupancy` 对应每场景一张 `val_N_map.png`。结合文件时间和内容，判断这是对场景几何做俯视栅格化得到的 2D 占据图，而不是 MuJoCo 内部的碰撞数据。

- **生成方式推断**：以 200 px/m 将房间/墙体/家具投影到水平面；自由区域填黄，墙体与家具脚印填黑，画布其余部分也是黑。
- **一致性**：黄色区域与房间地板多边形的形状高度一致（IoU ≈ 0.80–0.85；差异来自墙厚与家具遮挡），说明图与场景是配准的。
- **可用性**：适合做 2D 导航、可达性分析、起始点/目标点采样；不适合直接当作 3D 碰撞体（3D 碰撞请用 XML 中的 collision mesh）。
- **缺失项**：没有分辨率、原点、朝向等 sidecar；不同场景的画布大小与边距不同，不能假设统一裁剪。
- **质量**：1,000/1,000 存在；抽样 100 张均为纯双色图，无损坏、无透明通道。

---

## 7. 资源依赖与路径解析（重要）

### 7.1 `assets/` 软链视图

`molmospaces_data/assets/` 是官方资源管理器设计的“对外视图”：每个文件软链到 `mujoco/` 下的版本化实际文件。当前状态：

- `assets/` 下 **59,118 个软链全部有效**（`find assets -xtype l` 为 0），其中 scenes 59,115 个、`objects/thor`、`grasps/droid`、`robots/rby1m` 各 1 个目录链。
- 软链使用**相对路径**，指向同级 `mujoco/`，不绑定绝对根目录：
  ```
  assets/scenes/procthor-10k-val/val_0.xml
      -> ../../../mujoco/scenes/procthor-10k-val/20251217_with_occupancy/val_0.xml
  assets/objects/thor
      -> ../../mujoco/objects/thor/20251117
  ```
- 因此设置 `MLSPACES_ASSETS_DIR=/data1/wenyifan/lmdf/molmospaces_data/assets` 后，可直接按官方方式读取 `scenes/procthor-10k-val/val_N.xml`、`objects/thor/...` 等路径。
- 实测（MuJoCo 3.5.0）：经该视图加载 `val_0` / `val_1` / `val_500` 成功，耗时 2.04 / 0.37 / 0.50 s，nbody·ngeom·nmesh·nq·nsite 与 §5.5 一致。

**路径规则仍需注意**：不要直接用真实路径 `mujoco/scenes/procthor-10k-val/20251217_with_occupancy/val_N.xml` 加载——XML 内的 `../../objects/thor/...` 会解析到不存在的 `mujoco/scenes/objects/thor`。必须经 `assets/` 视图（或同构的 `scenes/`+`objects/` 布局）访问。

**引用完整性**：1,244,142 条 mesh/texture 引用（61,037 个唯一文件）全部存在，缺失数为 0。

### 7.2 软链重建记录（2026-10-25）

数据目录由 `/data1/wenyifan/molmospaces_data` 迁移到 `/data1/wenyifan/lmdf/molmospaces_data` 后，软链按新根重建为相对链接：逐个读取原目标，把前缀映射到新根并校验目标存在（59,118/59,118 命中、0 缺失），再写入相对路径：

```bash
python3 - <<'PY'
import os
ASSETS = "/data1/wenyifan/lmdf/molmospaces_data/assets"
OLD = "/data1/wenyifan/molmospaces_data/"
NEW = "/data1/wenyifan/lmdf/molmospaces_data/"
for root, dirs, files in os.walk(ASSETS):
    for name in dirs + files:
        p = os.path.join(root, name)
        if not os.path.islink(p):
            continue
        t = os.readlink(p)
        if not t.startswith(OLD):
            continue
        new = os.path.normpath(NEW + t[len(OLD):])
        assert os.path.exists(new), new
        os.unlink(p)
        os.symlink(os.path.relpath(new, os.path.dirname(p)), p)
PY
find /data1/wenyifan/lmdf/molmospaces_data/assets -xtype l   # 应为空
```

采用相对链接后，`molmospaces_data/` 整体搬迁不会再破坏链接。

### 7.3 路径规则

- 场景 XML 内的相对引用以 **XML 文件所在目录**为基准：
  - `val_N_assets/...` → 同目录下的场景专用 OBJ；
  - `../../objects/thor/...` → 与 `scenes/` 同级的 `objects/`。
- 因此加载时 XML 必须位于 `<root>/scenes/procthor-10k-val/`，且 `<root>/objects/thor` 可访问；直接把 XML 复制到别处会丢失共享物体引用。
- 场景资产不打包、不 include，`bundle_assets=false`；共享物体库 `mujoco/objects/thor/20251117`（3.2 GB，110,148 文件）是硬依赖。

---

## 8. 数据质量检查

### 8.1 完整性与可解析性

| 检查 | 结果 |
|---|---|
| 场景索引 0–999 | 连续，无缺号 |
| 每场景 6 项文件 | 1,000/1,000 齐全 |
| JSON 解析 | 1,000/1,000 成功 |
| metadata JSON 解析 | 1,000/1,000 成功 |
| MJCF 解析（`val_N.xml`） | 1,000/1,000 成功 |
| MJCF 解析（`_ceiling.xml`） | 1,000/1,000 成功 |
| PNG 头部 | 1,000/1,000 可解析，均为 8-bit RGB；抽样 200 张解码正常 |
| 场景 JSON 去重 | 1,000 个唯一 MD5，无重复场景 |
| `_assets` 目录 | 1,000/1,000 存在 |

### 8.2 引用完整性

| 指标 | 数值 |
|---|---:|
| 主 XML 中 mesh/texture 引用总数 | 1,244,142 |
| 唯一引用文件数 | 61,037（共享 10,459 + 场景本地 50,578） |
| 按正确根路径检查后的缺失数 | **0** |
| 共享物体库文件数 | 110,148（3.2 GB） |

> 注：引用检查以 `assets/` 视图为基准（共享引用落到 `assets/objects/thor/...`，本地引用落到场景目录），1,244,142 条引用全部命中。

### 8.3 结构一致性

| 检查 | 结果 |
|---|---|
| 房间 body 数 = JSON 房间数 | 1,000/1,000 精确相等（共 3,534） |
| 墙体 body 数 = JSON 墙段数 − `empty=true` 墙段数 | 1,000/1,000 精确相等；全库 `empty` 墙 650 段，墙体 body 27,688 |
| `_ceiling.xml` ⊇ 主 XML | 1,000/1,000 成立；主 XML 的 body 集合完全包含于 ceiling 变体 |
| ceiling 变体 body 增量 | +4,601 = 3,534 天花板 body + 315 个场景中的 1,067 个物体 body |
| metadata body/site 与 ceiling 变体一致 | 物体 body 183,173 精确相等；site 50,952 精确相等 |
| metadata joint 与 ceiling 变体 | metadata 多 1,758（61,766 vs 60,008），未完全对齐 |
| `metadata.object_enum` | 100% 为 `thor_obj` |
| `metadata.mjcf_path` | 100% 为空 |
| JSON `metadata.schema` | 100% 为 `1.0.0` |
| JSON `metadata.warnings` | 100% 为空 |
| agent pose | 每场景固定 4 个 |

### 8.4 已知缺口：3,309 个 JSON 物体未进入仿真

逐场景比对 `val_N.json`（物体 + 门窗）与 `val_N_metadata.json` 的 `object_id`：

| 类别 | JSON 数量 | metadata 缺失 | 说明 |
|---|---:|---:|---|
| CreditCard | 1,096 | **1,096** | 全部缺失 |
| KeyChain | 1,072 | **1,072** | 全部缺失 |
| Watch | 881 | **881** | 全部缺失 |
| GarbageCan | 1,309 | 220 | 部分缺失 |
| Candle | 836 | 23 | 部分缺失（23/836） |
| ToiletPaper | 723 | 17 | 部分缺失（17/723） |
| **合计** | — | **3,309** | 占 JSON 物体总数 68,838 的 **4.8%** |

- 门窗没有缺失（3,226 扇门、2,698 扇窗全部有 metadata）。
- metadata 中没有“JSON 不存在”的多余条目。
- 抽检 `val_0`：JSON 有 5 个 CreditCard、3 个 KeyChain、3 个 Watch，XML 中对应 body 数均为 0；metadata 也无这些条目。
- 影响：如果任务依赖信用卡/钥匙链/手表/垃圾桶等小物体，需要回到原始 ProcTHOR JSON 或重新导出；当前 MJCF 中不存在它们。
- 其余物体数量吻合：`68,838 − 3,309 + 3,226 + 2,698 = 71,453`，与 metadata 条目数完全一致，说明缺口就是上述类别，而不是随机丢失。

### 8.5 计数口径：metadata 对应 ceiling 变体

| 项目 | metadata | 主 XML（`val_N.xml`） | ceiling XML | 结论 |
|---|---:|---:|---:|---|
| 物体 body | 183,173 | 182,106 | 183,173 | metadata 与 ceiling 变体精确一致 |
| site | 50,952 | 50,896 | 50,952 | metadata 与 ceiling 变体精确一致 |
| joint | 61,766 | 59,488 | 60,008 | metadata 多 1,758（+2.9%），原因未完全定位 |

- 主 XML 少的 1,067 个物体 body 分布在 315 个场景，全部能在 ceiling 变体和 metadata 中找到。
- 因此：**需要精确使用 metadata（类别、body/joint 名称映射、site 放置区域）时，应加载 `val_N_ceiling.xml`，而不是 `val_N.xml`。**
- joint 仍有 1,758 条只存在于 metadata 名称映射中、未出现在任何 MJCF；规划关节数时以 XML 实测为准。

---

## 9. 使用建议

1. **路径设置**：`export MLSPACES_ASSETS_DIR=/data1/wenyifan/lmdf/molmospaces_data/assets` 后直接经 `assets/` 视图加载（软链已全部重建为相对链接）；不要直接用 `mujoco/scenes/...` 下的真实路径加载，XML 的相对引用会断。
2. **保留共享物体库**：`mujoco/objects/thor/20251117` 是硬依赖（3.2 GB）；场景包只有 1.81 GB，脱离物体库无法加载。
3. **选择 XML 变体**：
   - 导航、抓取、视觉感知（需要看到室内）：`val_N.xml`（无天花板）；注意它在 315 个场景中比 ceiling 变体少少量小物体；
   - 封闭空间渲染、需要完整物体集合、与 metadata 严格对齐：`val_N_ceiling.xml`（严格超集，额外含天花板与 1,067 个物体 body）。
4. **加载性能**：单场景 0.4–2.1 s、879–3,492 个 geom；不建议一次性加载全部 1,000 场景，按需加载/缓存。
5. **占据图使用**：按 200 px/m、`img_x=-z`、`img_y=-x` 建立映射；黑色需用边界 flood fill 区分“外部”和“室内障碍”。
6. **语义查询**：优先用 `val_N_metadata.json`（类别、静态性、body/joint 名称映射、房间归属）；需要门窗、墙、房间多边形和程序参数时用 `val_N.json`。
7. **数据集划分**：本目录只有 **val** 1,000 个场景；train/test 需另找对应版本（MolmoSpaces 资源中为 `procthor-10k-train` / `procthor-10k-test`）。
8. **小物体缺口**：任务若涉及 CreditCard/KeyChain/Watch/Candle/ToiletPaper 或部分 GarbageCan，当前 MJCF 中不可用，需要原始 ProcTHOR 数据或重新导出流程。

---

## 10. 统计口径与可复现性

- 场景统计：解析全部 1,000 个 `val_N.json`、`val_N_metadata.json`、`val_N.xml`、`val_N_ceiling.xml`。
- 占据图统计：每 5 个场景抽样 1 个（200 张），用 NumPy + SciPy 连通域分离外部与室内障碍；分辨率与朝向用房间多边形栅格化对齐验证（另抽 4 个场景全量验证方向）。
- 引用完整性：解析全部 XML 的 `<mesh file>` / `<texture file>`，共享引用映射到 `mujoco/objects/thor/20251117`，本地引用映射到场景目录。
- 加载测试：MuJoCo 3.5.0，经 `assets/` 视图加载（2026-10-25 复核 `val_0`/`val_1`/`val_500`，计数与耗时一致）。
- 所有统计均为只读分析，未修改 `molmospaces_data/` 下任何文件。

---

## 附录 A：一页速查

```
路径      /data1/wenyifan/lmdf/molmospaces_data/mujoco/scenes/procthor-10k-val/20251217_with_occupancy
场景数    1,000（val_0 … val_999）
体积      1.81 GB（不含共享 objects/thor 的 3.2 GB）
单场景    val_N.json + val_N_metadata.json + val_N.xml + val_N_ceiling.xml + val_N_map.png + val_N_assets/
规模      中位 3 房间 / 26 墙 / 3 门 / 2 窗 / 63.5 物体 / 78 m² / 196 body / 1,610 geom
变体      主 XML 无天花板；ceiling 变体是严格超集（+3,534 天花板 body，315 场景 +1,067 物体 body）；metadata 对齐 ceiling 变体
占据图    200 px/m，黄色自由 / 黑色占据+外部，img_x=-z，img_y=-x，室内自由中位 88.1%
加载      MuJoCo 3.3.2 导出；实测 3.5.0 可经 assets/ 视图加载，0.38–2.08 s/场景
加载配置  MLSPACES_ASSETS_DIR=/data1/wenyifan/lmdf/molmospaces_data/assets（软链相对、0 失效）
注意      3,309 个 JSON 小物体未导出（CreditCard/KeyChain/Watch 全部缺失）
```
