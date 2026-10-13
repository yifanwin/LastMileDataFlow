> 本文描述旧协议。当前 scene/lastmile 默认入口采用 [Research 数据协议](research-data-protocol.md)，不再执行封存和默认全量复审。旧记录仅供读取与参考，不原地迁移。

# scene_datasets v1：策略无关的场景输入契约

`scene_datasets` 是场景输入，不是 cuRobo/MolMoBot 执行 datasets。
未来 benchmark 独立组织，通过 dataset/sample/config/station 身份引用本数据，不复制或修改源包。

## 1. Case 规则

当前新增规则为 `scene-cases-v3`；旧 `scene-cases-v2` 配方不改写。数量为新增数量，原有物体保留。

| Case | 操作 | 新增物体 |
|---|---|---|
| Case1 | pick | 1 把地面 Chair + 5–8 件目标支撑面物品，共6–9件 |
| Case2 | pick | 10–20 件目标支撑面物品；默认至少两件实测大／高物体 |
| Case3 | open | 未增强；具体门／抽屉关节绑定单独任务 |

旧 `procedural-obstacles-v1` 保留旧规则；此前新增数量规则使用 v2，本次台面与容量规则使用 v3，禁止原地重新解释历史案例。
Case 成立仅指构造成立，不证明导航、抓取或开门成功。

### v3：支撑物、目标与容量例外

- Case1 仅 CounterTop、Table、DiningTable、CoffeeTable、SideTable、Desk；Bed、Stand、Shelf、Dresser 不接受。按实际支撑面的语义类别，不按物体名字猜测。
- pick 目标仅 cup、mug、wine bottle、atomizer、salt shaker、pepper shaker、soap dispenser。这是用户指定的“假定高成功率”白名单，没有策略成功率实测。
- 数据类别映射：SprayBottle → atomizer；SoapBottle → soap dispenser；WineBottle 保持独立。普通 Bottle、SoapBar 不因名字相近被放行；open 不受 pick 白名单限制。
- 正常表面新增数量仍为 Case1 5–8、Case2 10–20；Case1 的1把地面 Chair 另计。旧底层 obstacle_count 包含 Chair，公开清单另记 added_surface_count 和 surface_obstacle_bounds，不混作最终总数。
- 支撑面放不下时，先检查实测 footprint 最小的合格候选资产，再检查其它允许资产，避免只因某个随机大物体放不下就提前结束。不缩放资产。
- 容量检查穷举当前注册候选资产、配置的支撑面点及12个对齐角度（四个90°方向 × 三档 jitter）。正常 v3 表面采样使用同一角度域。证据只证明**声明的离散放置域**没有合法追加姿态，不宣称连续空间的数学不可放证明。
- 必须经过完整几何／窄相碰撞检查；静置后重新穷举，绑定最终 qpos/qvel／域摘要。只有该证据成立，才允许表面新增数低于 min_count；预算、超时、检索失败均不是“容量满”。
- Chair、Case2 至少两件实测大／高物体、静置／支撑／源影响／最终>=5个站位等其他约束仍保留。Case1 可在认证容量不足时只有 Chair；完全空的 Case2 不发布为增强样本。
- 混合 Case1/Case2 配方共用目标集合，因此采用 Case1 支撑限制；Case2-only 配方可选择其它几何合格支撑物。包仍与策略无关。

低于最小数量的样本保存 construction/capacity.json，含范围、最小资产、候选域、总／已检查姿态数、拒绝统计及耗时；物理审计须重新播放穷举检查。重放依赖 provenance 指向的构造基线和注册候选资产证据；缺失时不得声称物理审计通过。

最新最小配方：`configs/scene_datasets/val103_cup30_minimal_v3.json`，仅 val103 / Cup_30，Case1/Case2 各1个变体，不含 open。两场景新规则配方为 `val103_val104_countertop_targets_v3.json`，未自动全量启动。

## 2. 身份与绑定

- source：ProcTHOR split/house、XML/metadata 摘要。
- task：精确实例、operation；open 加具体 joint/range/goal。
- sample：source + task + Case + 规则版本 + 变体种子和实际 placements。
- robot_sensor_config：机器人模型、控制/物理配置、初始姿态、相机别名及站位/可见性参数。
- station：冻结候选集合中的稳定 station_id、xy、grid_index、真实初始化姿态。

机器人/传感器绑定，与 planner/backend/policy 无关；同配置各策略共用同包。
换模型、控制配置、相机或检查参数须重新构建/验证，不沿用旧有效性。
现有 MolMoBot collector 另要求 native motors 和1024×576；320×240 position 包不冒充 native 包。

## 3. 目录

```text
outputs/scene_datasets/<dataset-id>/
  manifest.json / checksums.json
  build.json / discovery.json / timing.json
  samples.jsonl / packages.jsonl / exclusions.jsonl
  samples/<sample-id>/
    manifest.json / checksums.json
    environment.json / task.json / objects.json
    construction/ / validation/ / media/
    robot_sensor_configs/<config-id>/
      manifest.json / checksums.json / task.json / initialization.json
      scene/model.mjb / initial.npz / version.json / instances.json / checksums.json
      stations/candidates.json / validated.json / selected.json / selection.json
      validation/checks.json
outputs/benchmarks/<benchmark-id>/          # 未来评测定义，独立于场景包
outputs/no_edit/<execution-run>/            # 实际执行数据，独立于场景包
outputs/diagnostics/<build-unit-or-run>/    # 全部构造尝试及失败
outputs/logs/                              # 日志唯一入口
```

大型模型和图像不入 Git；公开包保留摘要及外部资产来源，当前不是可脱离源资产的通用 XML 分发包。
同 house 的多目标/多变体不是独立 house；训练/评测划分按源 house 分组，上游 val 来源保留。

## 4. 发现与站位

只处理 recipe 显式 houses，不隐式全量扫描。house 支持103、val103、val_103。
默认每 house 3个 pick、1个 open；pick 优先跨房间/支撑物，再最大化最近距离，默认目标间距>=0.75m。
targets 可按实例/asset/task ID 筛选 pick；categories 是任务类别筛选。
若配额不足，停止正式构造并报告，不用同位置重复目标补数。

发现阶段可在找到5个真实静态几何通过点后停止；计数明确为下界，不冒充完整候选统计。
正式构造前、构造后均检查完整候选；构造后每点新建 Simulation/MjData，检查初始化、碰撞、地面、非机器人状态不变及 head 可见像素。
不调用 IK、路径规划或 VLA 判断站位资格。
覆盖模式默认最多20点，不取前20；all_valid 模式保留全部通过点。
正式包默认最终点>=5；失败点保留原因。执行时必须使用 selected，禁止重新生成 lattice。

## 5. 构造、发布和时间

构造使用真实注册资产、完整几何、静置和等时无编辑对照；不修改源资产、删除原物体或降低物理阈值。
v2 根据实际支撑面尺寸筛资产，并对普通物品使用70%表面面积的单件平均 footprint 预算；这是提案筛选，不代替真实碰撞/承重检查。
Case3 不新增物体、不额外静置或 physics stepping；保存原关节/非机器人 qpos 不变证据。
每目标/Case unit 最多3轮独立构造，仅重试缺失变体，失败/超时/基础设施异常分开保留。
单个 accepted 变体独立封存，父 run 部分失败不影响它的输入资格。

记录 discovery wall、batch wall、unit wall、variant wall，及 settle/control-settle/render/station-validation 分项。
并行 batch wall 不等于各 unit 时间之和；variant wall 不含共享基线和资产检索。
版本/清单封存后不可改写；变更构造、配置、选点规则使用新 dataset/sample/config 身份。
resume 只接受完全一致的冻结输入；已完成 unit 不重跑。异常中断且没有完整 result 的 unit 当前要求新 dataset-id，旧证据保留。
目标配额不足时默认拒绝构造；用户批准部分交付后可显式使用 `--allow-partial-targets`。该参数不会减少原始 expected_samples；清单另记 qualified_target_capacity、missing_samples，批次保持 partial，单个样本仍须全部检查通过。

## 6. 公共输入接口

`stations.scene_package.package_manifest(package, robot, geometry)` 只检查物理/传感器身份、摘要和站位，不读取策略名称。
`NoEditConfig.scene_package` 是 collector 的公共输入，与旧 augmented_variant 互斥。
collect-no-edit 复制不可变输入到新 run，在策略模型加载前绑定 task/stations，station_input 保存 dataset/sample/config/station 来源。
读取或准备入口通过不等于已运行策略。机器人控制配置不兼容时拒绝，不静默切换配置或重建环境。

## 7. 运行

```bash
export DATAFLOW_PYTHON="$PWD/../no-edit-lastmile-val/.venv-curobo-v080/bin/python"
export MUJOCO_GL=egl PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p outputs/logs

bin/lastmile-dataflow discover-scene-datasets \
  --config configs/scene_datasets/val103_val104.json \
  --output outputs/diagnostics/<unique-discovery>.json

bin/lastmile-dataflow qualify-scene-datasets \
  --discovery outputs/diagnostics/<unique-discovery>.json \
  --output outputs/diagnostics/<unique-qualified-discovery>.json

bin/lastmile-dataflow build-scene-datasets \
  --config configs/scene_datasets/val103_val104.json \
  --discovery outputs/diagnostics/<unique-qualified-discovery>.json \
  --dataset-id <unique-dataset-id> --gpu-ids <low-load-gpus> --workers 4

bin/lastmile-dataflow audit-scene-datasets outputs/scene_datasets/<dataset-id> \
  --physical --output outputs/diagnostics/<unique-audit>.json
```

GPU由运行前显存/利用率选择；不在实现中硬编码固定设备。所有 worker 日志落 outputs/logs。
qualification 对空间选择的目标执行完整独立站位初始化／可见性检查；不合格目标排除后按同一空间规则补选。保留静态 discovery 和所有 qualification 失败，不将静态几何通过视为可见性通过。
审计重载实际冻结模型、复核碰撞/支撑/Case/源影响/媒体/摘要；不冒充新的连续任务执行。

### 迁移后的普通样本物理审计

`audit-scene-datasets <root> --physical --audit-inputs <companion/audit_inputs> --output <outside-root.json>` 可使用与根 manifest 校验绑定的 `scene-audit-inputs-v1` 冻结增强配置。机器人描述从包内 `scene/version.json` 加载，不依赖原机器人 JSON 路径。模型与状态仍按逐级校验和检查；companion 不属于封存根目录，不修改旧根清单。
本次 val103-Cup_30 两个普通样本已经迁移目录复查通过。含容量不足证书的未来样本重放仍依赖原构造基线和候选资产，不能声称该 companion 已支持完整容量重放迁移。

### 不限目标数量（-1）

`max_pick_targets_per_house`、`max_open_targets_per_house` 支持 -1：无强制最低目标数且不截断合格目标；空间分散仅排序，不因近距离排除。正整数仍是固定期望配额；0与小于-1非法。不限字段的 expected_samples 按已资格检查目标数计算，空house保留为空；所有语义/物理资格不变。本次42样本复用旧有界筛选的合格子集，显式标记非穷举范围，不声称覆盖不限配额的全部目标。
