> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 程序化障碍物增强

独立入口 `augment-obstacles`，不调用 Agent，不走旧 `case-edit` 的提案或审查流程。
复用现有真实资产检索、事务构建、静置、机器人初始化和 RGB 组件。
构造通过不表示导航或抓取任务成功。

## 运行与配置

从本工作区根目录执行；安装可用 `pip install -e '.[augmentation]'`。
本机复用相邻已有解释器，不修改环境。GPU 编号由启动参数选择，不写死在源码里。

```bash
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=<可用设备> \
MPLCONFIGDIR=/tmp/mpl-procedural-obstacles \
DATAFLOW_PYTHON=../no-edit-lastmile-val/.venv-curobo-v080/bin/python \
bin/lastmile-dataflow augment-obstacles \
  --config configs/case_edits/procedural_obstacles_val103_cup30_case1.json \
  --output-dir outputs --run-id <唯一 ID>
```

Case2 改用 `procedural_obstacles_val103_cup30_case2.json`。
两份均为 `variant_count=5`，数量在每个独立子种子下抽取，不是每站五次操作评测。

| 配置 | `construction.case` | 每份新增物体 |
|---|---|---|
| `..._case1.json` | `Case1` | 随机 1–3 个，恰好 1 把地面 Chair；其余放目标附近实际桌面 |
| `..._case2.json` | `Case2` | 随机 6–12 个，全部放目标自身支撑面；至少两个实测尺寸达标的大／高物体 |
| `..._chair_debug.json` | 旧配置，无 case | 每份 1 把 Chair，保留兼容 |
| `..._random_1to3.json` | 旧配置，无 case | 保留旧随机池语义，不保证其余全部桌面，推荐改用 Case1 |

`procedural-obstacles-v1` 增加可选的 `construction`，未知键和未实现 case 显式报错。
新增 case 应扩展 `CaseConstruction` 的验证与 `case_assets` / `case_construction_check`，
复用几何、事务、静置和站位检查，不复制一套机器人或仿真代码。

```json
"construction": {
  "case": "Case2",
  "min_prefilter_points": 5,
  "max_final_stations": 20,
  "surface_spacing_m": 0.055,
  "surface_alignment_jitter_rad": 0.12,
  "near_target_m": 0.85,
  "substantial_categories": ["Kettle", "Toaster", "CoffeeMachine", "DeskLamp", "Vase"],
  "min_substantial": 2,
  "substantial_height_m": 0.20,
  "substantial_width_m": 0.25,
  "registered_assets_only": true
}
```

大／高物体须同时属于配置类别，且实测高度 ≥0.20 m 或宽／深 ≥0.25 m，不能只按名字判断。
可用时混合不同大物体类别；无法刚体加载的资产记录原因，不硬塞或降级数量。
当前桌面候选宽／深不超过 0.65 m、高不超过 0.75 m，不缩放资产。

路径相对于配置文件解析。当前相对路径对应指定 worktree；迁移时须显式调整。
`input_snapshot=null` 时从源 XML 构建；非空时核对原快照摘要、机器人配置和源身份。
原场景、资产和旧采集产物始终只读，新 run 禁止覆盖。

## 完整构造顺序

1. 加载原场景与唯一目标。机器人在 `begin()` 前初始化到远离增强工作区的合法停放位。
2. 原场景真实静置、核对求解器健康，重新测量目标锚点与支撑家具。
3. 生成目标工作区**全部原始稠密候选站位**；逐点在新 Simulation / MjData 中做静态机器人几何和地面预筛。
   此步不做可见性或操作 rollout。预筛少于 5 点则 `status=skipped`，不检索／添加资产。
4. 在预筛通过站位的八邻接边上寻找 A* 通道；合法算术中点独立检查。
   Case1 再加入 station→target 接近段的地面空闲点；采样优先级为中点／接近点 5、普通 station 1。
   点位空闲不代表整个椅子可放；后续仍检查完整形状。
5. 支撑面取目标下方最高合格水平碰撞面及同一支撑物的共面区域，不把桌腿顶／低层板当桌面。
   从实际边界提取表面点；完整 footprint 和多点支撑射线检查边界、孔洞。
6. 根据 DiningTable / CounterTop / Desk / CoffeeTable / SideTable 环境规则筛类别。
   优先且默认仅使用 `asset_id_to_object_type.json` 中注册的 THOR canonical XML。
7. 每份独立 SeedSequence 子种子抽数量和资产。Case1 一把椅子加 0–2 件桌面小物体；
   Case2 至少两件尺寸达标的大／高物体，较大 footprint 优先放置。
8. 椅子以桌面中心方向作为根位姿 yaw 的采样参考，并随机扰动；桌面物体按表面轴的四个方向加小幅随机扰动。
   桌面点先按完整旋转 footprint 筛边界，再用源物体实测占用量偏好空闲区，保留探索概率。
   已新增物体之间使用保守 footprint 排斥；全部仍经过真实 MuJoCo 窄相碰撞检查。
9. 连续 48 次无进展时程序化重采同类资产，保持必需的大／高条件；每三次重采可回退一个已放物体。
   不移动原物体、不改比例、不减少抽到的数量。
10. 构建会话按名字迁移原状态。所有新增 free joint 写入提案位姿后才调用 `mj_forward`，
    避免一批尚未放置资产默认重叠在原点。每个候选用新 MjData，拒绝只关闭候选，不恢复运行中的仿真。
11. 全高度逐 geom 检查 `mj_geomDistance` 和实际接触深度；只豁免指定支撑面的容差内接触。
12. 实际静置新增物体，检查稳定、承重接触、完整支撑、倾斜、非法碰撞和 solver warning。
    独立无编辑 control 静置到相同仿真时刻，比较所有未编辑源物体的额外漂移和源接触变化。
13. 对**原始全部候选站位**逐点创建独立 Simulation / MjData，调用真实机器人初始化和放置碰撞／地面／可见性检查。
    新 solver warning 不能算通过；增强前被筛掉的点也重新检查。不使用前筛通过集合替代全量集合。
14. 只保留真实通过点；若超过 20，按 8 个方位区间、2 个距离区间选代表，再用含方位／距离特征的最远点补足。
    保留最近／最远距离代表，禁止简单前缀截断。原始全量结果和压缩丢弃原因均保存。
15. 以静置后位姿重算导航网格、站位连通关系，冻结前后场景并渲染 RGB。
    椅子用全高度形状投影，不能只使用椅腿；桌面物体不无条件投影成地面障碍。

静置阈值保持：1–3 s、0.25 s 稳定窗口、线速度 ≤0.025 m/s、角速度 ≤0.10 rad/s、
窗口漂移 ≤0.006 m、旋转 ≤0.04 rad；支撑容差 0.008 m、倾斜 ≤0.10 rad。
非支撑穿透容差 0.001 m；源物体相对等时对照位移 ≤0.006 m、旋转 ≤0.04 rad。
源场景绝对异常如实记录，不把原有源接触当新增干预。

Case1 / Case2 当前分别最多 256 / 1200 个几何候选，每份最多 12 次物理尝试、900 s，模型缓存最多 4 份。
`no_feasible_placement` 是构造失败，`incomplete` 是预算未完成，`infrastructure_error` 是设施异常；不要混淆。

## 稠密站位配置

`configs/no_edit/curobo_v080_arm_reach_half_base_radius.json` 的文件名沿用旧名；
本工作区间距已从 0.2217592080002628 m 减半为 **0.1108796040001314 m**。
半径仍为 0.7928056080178096 m，产生 **161 点**，其余 no-edit 字段不变。
这不是修改相邻 no-edit worktree 或旧结果；也不表示本次执行过 cuRobo 抓取。

## RGB 与产物

每份三个配对视角：俯视、斜视 A、斜视 B，前后各一张 640×480，同一取景与光照。
前图来自等时无编辑 control，后图来自真实静置后的增强场景，不是生成式图片。
只改变观察相机，不隐藏墙／实体／可视网格。
Case1 要求每视角均能看到目标和所有新增物体；Case2 允许密集遮挡，但三视角联合须覆盖目标与每个新增物体，
每个至少在一张后图中达到 24 个真实分割像素。每对图还须前图看到原场景目标、后图看到至少一个新增物体，拒绝被墙挡住的空视角。观察相机最多搜索 8 组配对角度，包含不依赖 +45° 的开放侧组合。分割统计与相机搜索完整保留。
新增资产非物理 receptacle 调试 site 设为默认隐藏组 5，不修改几何或材质。

输出位于 `outputs/diagnostics/<run>/`，日志位于 `outputs/logs/<run>.log`。

| 文件 | 内容 |
|---|---|
| `frozen_config.json` / `provenance.json` | 配置、源／快照／源码哈希、设备 |
| `baseline/stations.json` | 全部 161 点的增强前静态预筛 |
| `assets/retrieval.json` | 注册信息、实测尺寸、加载失败原因、来源摘要 |
| `variants/vNNN/attempts.jsonl` | 所有几何／物理拒绝、重采、回溯 |
| `variants/vNNN/edits.json` | 类别、资产、提案与静置后位姿、支撑面 |
| `variants/vNNN/navigation/comparison.json` | 全部原始点增强前后实际初始化结果、导航网格变化 |
| `variants/vNNN/stations_final.json` / `station_selection.json` | 最终通过点、覆盖式压缩方法与丢弃原因 |
| `variants/vNNN/checks.json` / `scene/` / `before_scene/` | 物理和构造检查、独立冻结场景 |
| `variants/vNNN/rgb/` | 六张 RGB、分割统计和配对身份 |
| `summary.json` / `index.html` / `before_after_overview.jpg` | case、seed、前筛／后验／最终点数、失败原因与浏览入口 |

```bash
MUJOCO_GL=egl PYTHONPATH=src <python> scripts/audit_procedural_obstacles.py \
  outputs/diagnostics/<run> --output outputs/diagnostics/<run>/audit.json
```

审计只读加载冻结场景，重做物理／case 检查、覆盖压缩，核对全量站位集合、注册来源、RGB 和链接。
属于同一上下文第二遍检查，不声称独立 Agent 复核。
导航网格保守，初始化通过只说明该状态可以放置且目标可见；未执行连续导航、抓取或其他 task rollout。

媒体覆盖修复可用 `scripts/rerender_obstacle_cases.py <物理父 run> <新 run>`。
只允许相机覆盖模块与父源码摘要不同；快照和全部站位记录必须字节一致，图像重录零 physics step、零新增机器人初始化。
新 run 的 `media_repair.json` 与 `provenance.json` 显式记录继承关系；不能把继承的初始化算作又跑了一遍。
