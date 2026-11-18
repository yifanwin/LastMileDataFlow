# 方块随机站位实验：原始目标选择与三个扩展任务

- 状态：done
- 日期：2026-11-18
- 替代：无
- 关联：../delivery/37-random-stations-8m-training-launch.md
- 执行时间戳：2026-11-18T11:19:32+08:00

## 范围与当前目标选择

用户确认继续使用相同的 4 cm、0.1 kg 方块，比较不同场景与摆放位置；同时明确记录被替换的原始目标物体。本次只核查、选任务并做 CPU 初态验证，没有修改训练代码或启动训练。

基线为 `outputs/experiments/rl-cube-random-stations/runs/20261117-211346-random-facing-8m-g0123/input.yaml`。该运行固定配置 `scene_xml=val_103.xml`、`scene_metadata=val_103_metadata.json`、`target=Cup_30`，不是自动或随机选择目标。`resolve_target` 将资产名解析到该场景唯一实例；重复资产不能用不唯一的资产名指定。

原始目标是餐桌 `Dining_Table_203_1` 上的杯子 `Cup_30`，源对象 ID 为 `Cup|surface|2|28`。`prepare_rl_scene` 调用 `replace_with_cube`，删除原杯，在原 XY 放入自由关节实体方块 `RL_Cube`。代码从原物体的父家具中提取物理支撑面，要求方块四角能落在支撑面内且通过支撑射线检查，再将方块中心放在支撑高度加 2 cm 处。不会按原杯中心高度放置方块。

场景裁剪半径为 2 m；静置 1 s 后生成站位。`reset` 每回合从有效站位池均匀随机抽取站位，并恢复同一份物体初态；不重选目标、不随机方块位置。方块使用中心抓取点及固定的相对抓取朝向，不读取原杯的 grasp 库。

实现入口：`station_map/rl/env/scene.py:109`、`scene.py:159`、`sim/source.py:84`、`station_map/rl/env/stations.py:82`、`station_map/rl/env/terms.py:538`。

## 选定的三个任务

三者均来自本地 ProcTHOR validation 资产，目标资产名在各自场景中均能唯一解析。沿用基线运行实际保存的输入，只替换场景 XML、metadata 和原始目标；不使用当前 YAML 中另为离线评分保存的 val103 方块位置。

| 场景 | 原始目标 | 原始源对象 ID | 支撑家具 | 支撑高度 m | 方块世界 XY m | 有效站位 / 候选数 |
|---|---|---|---|---:|---|---:|
| `val_3` | `Cup_12` 杯子 | `Cup\|surface\|2\|8` | `Dining_Table_203_1` 餐桌 | 0.7503371 | (9.7617142, 6.1238759) | 16 / 233 |
| `val_29` | `Cup_28` 杯子 | `Cup\|surface\|2\|11` | `Side_Table_313_1` 边桌 | 0.7129622 | (3.8710502, 0.2011001) | 37 / 233 |
| `val_2` | `Cup_12` 杯子 | `Cup\|surface\|2\|18` | `Countertop_C_10x4` 厨房台面 | 0.9383868 | (2.6127100, 15.6804000) | 19 / 233 |

- `val_3 / Cup_12`：与基线使用同款餐桌，先比较房间布局和目标位置变化；有效站位池较小。
- `val_29 / Cup_28`：换成边桌及另一处摆放位置，有 37 个有效站位，扩展支撑家具几何。
- `val_2 / Cup_12`：台面比基线餐桌约高 18.8 cm，用于比较更高支撑面与厨房布局。

这是首批候选选择，不是对全部场景做抓取难度排序。三个任务都将原杯替换成相同方块，实验目标类别、尺寸和质量不变。

## 实际验证与限制

检查使用当前工作区提交 `d681bfa` 的源码，已有未提交报告修改保留；基线原运行源码提交为 `3e3b4ec`。以 `PYTHONPATH=src MUJOCO_GL=disable OMP_NUM_THREADS=1` 使用本工作区 `.venv` 执行 `prepare_rl_scene` 和 `prepare_stations(prepared.reference_model, prepared, config)`，无 GPU rollout。

三个场景均完成原始目标解析、物理支撑检查、替换方块、局部模型编译、1 s 静置及 233 个正对目标候选站位检查。静置后方块 XY 保持原位，Z 相比初始放置降低约 0.0663 mm。

| 场景 | 静置后方块中心 Z m | 初始机器人穿透拒绝 | 地面支撑拒绝 | 中心重合无朝向 |
|---|---:|---:|---:|---:|
| `val_3` | 0.7702707854 | 216 | 0 | 1 |
| `val_29` | 0.7328958854 | 192 | 3 | 1 |
| `val_2` | 0.9583204854 | 213 | 0 | 1 |

有效站位仅表示 Home 初态通过物理接触和地面支撑检查；没有 IK、可达性或完整抓取成功保证。尚未进行策略迁移评估、训练或抓取视频验证。若后续使用离线站位评分，必须更新新场景的物体初态和站位输入，不能复用 val103 的缓存坐标。

原始检查结果：[checks.json](../../../outputs/experiments/scratch/20261118-cube-scene-selection/checks.json)。

## 场景预览

2026-11-18（Asia/Shanghai）：按用户要求使用 MuJoCo 实际渲染三张预览，每张包含俯视与斜视两个视角。红框标记替换后的方块，标题保留原始目标物体名。显示时隐藏机器人及名称匹配的墙顶几何，未修改物理模型或移动家具；相机朝向仅用于观察，不代表机器人站位。使用实时空闲的物理 GPU 0。

- [val_3 / Cup_12 → 方块，餐桌](../../../outputs/experiments/scratch/20261118-cube-scene-selection/val3-cup_12-cube-scene.png)
- [val_29 / Cup_28 → 方块，边桌](../../../outputs/experiments/scratch/20261118-cube-scene-selection/val29-cup_28-cube-scene.png)
- [val_2 / Cup_12 → 方块，厨房台面](../../../outputs/experiments/scratch/20261118-cube-scene-selection/val2-cup_12-cube-scene.png)

渲染需将本机图形库目录 `/data1/wenyifan/opt/lib` 加入 `LD_LIBRARY_PATH`。厨房场景的现有 segmentation 标注触发索引越界，预览改用目标几何投影框及深度检查选视角；未改动公共渲染代码。
