# 最小数据格式

主记录用 JSONL，实验配置只保存一次。没有封存清单、校验摘要、重复索引或兼容旧协议的读取分支。

| 阶段 | 主记录 | 内容 |
|---|---|---|
| scene-datasets | `scenes.jsonl` | 场景 ID、accepted/rejected、相对场景路径、任务、站位、障碍物放置 |
| 逐点标定 | `trials.jsonl` | 场景/任务/站位、试验号、种子、状态、原因、相对 rollout 路径 |
| lastmile 配对 | `pair_index.jsonl` | 全量 S0×S1 计划、导航难度及执行结果，包含未执行和无路径 |
| lastmile 训练入口 | `train_index.jsonl` | 两类成功完整示教；引用型指向来源 run，物化后指向内部 rollout |
| benchmark 定义 | `episodes.jsonl` | 场景、任务、困难起点、种子 |
| benchmark 内部证据 | `oracle.json` | 每场景共享恢复区域、原始计数引用、可达路径、难度和分组；策略不可见 |
| benchmark 执行 | `results.jsonl` | episode 对应结果、终止与记录完整性 |

`config.json` 保存有效参数、种子、机器人/相机、后端或策略配置。`summary.json` 和热图是可重建的阅读产物，不作为协议入口。引用型生成读取原标定，不复制第二份 trials；独立物化仅复制训练索引所选的唯一轨迹与必需观测。

标定 run 的 `config.json.resources` 保存 `station_workers/gpu_ids/egl_devices/reuse_planners`，不参与物理执行配置匹配。新 trial 行包含原场景内的 `station_index`；seed 为 `config.seed + station_index × trials_per_station + trial`。并行时父进程预分配唯一 `rollout_id`，JSONL 行可按完成顺序排列，读取端不依赖行序。

## 场景库与标定关系

场景构建完成发现任务、资格筛选、增强与站位采样，accepted/rejected 都保留在 `scenes.jsonl`。
每个 accepted 场景给出任务和最终站位，采集端按这些站位执行，不重新生成一套点集。
`evaluate-stations()` 写 `trials.jsonl`；`collect-lastmile()` 读取该标定，或在当前 run 标定一次，再写全量配对与两类成功示教视图。
平滑热图仅用于阅读，选点读取原始 trial 计算出的实测率。场景、任务与控制 profile 必须匹配。

## lastmile 训练来源与物化

选点：`rate <= low` 为 S0，`rate >= high` 为 S1；配对为同场景任务的完整笛卡尔积，不受附近距离和数量上限截断。`pair_index.jsonl` 先保存全部计划，再更新结果；不再写重复的连续采集 `episodes.jsonl`。benchmark 的同名文件不变。

训练索引的公共字段包括场景、任务、站位、seed、真实任务结论和记录完整性：

- `s1_direct_operation`：所有真实成功且记录完整、有实际控制步的 calibration attempt；不受高率阈值限制，没有 `s0` 或 `navigation`。
- `s0_to_s1_continuous`：导航成功、操作成功且记录完整的真实连续 attempt；另含 `pair_id`、`s0/s1`、导航结论，以及 `euclidean_distance_m`、`astar_path_length_m`、`navigation_steps`。

距离单位米；A* 长度按发送给跟踪器的完整折线计算；导航步数为实际 `navigation` 控制间隔数，不含 `arrival`、操作或底层物理子步。失败保留已执行步数，未知不填 0。

引用型条目以 `source_run`（相对本数据集 run）与 `rollout_id` 定位源 attempt。
独立物化将成功轨迹统一复制到新输出的 `rollouts/dXXXXXX/`，训练条目改为 `rollout_path`（相对新数据集根目录），并以 `source.run/rollout_id` 保留历史来源。同一来源只复制一次，不同 run 的相同局部编号不合并。

内部 rollout 含原始 `trajectory.npz`、`recording.json`、必要相机 RGB/已启用深度，以及 `metadata.json`（相机列表、录制模式、动作定义及历史有效配置）。历史来源中的外部路径仅供追溯，不参与训练读取。物化不包含仿真重放资产或模型权重；非必需诊断视频不复制。

`pair_index.jsonl` 在物化后仍保留全集；成功 pair 指向内部副本，失败 attempt 只保留来源信息，不承诺有轨迹副本。`summary.json` 的 `completed/incomplete` 描述文件处理是否完成，不是机器人任务成功率。

原始训练读取器为 `data.export.lastmile_dataset.load_training_batch()`：返回 `[B,T,22]` state、`[B,T,20]` action、phases/time 和按相机分组的 RGB/深度，不做动作语义转换或补帧。具体命令见运行手册第 3 节。

## 场景载入

`scene-files/<scene>/` 包含 `model.mjb`、`initial.npz`、机器人及载入配置；有构建 spec 时另存 `model.xml` 便于查看。
MJB 是实际载入来源，需使用生成时的 MuJoCo 版本。XML 不是跨版本自动回退契约。
实例映射只用于定位物体，不是审计 registry。原始外部资产只读。

## 实际执行轨迹

`rollouts/<id>/trajectory.npz`：`time_s/qpos/qvel/ctrl/base_xyyaw` 和本体状态数组为 N+1 个边界状态（RBY-1 为 `state22`，PandaOmron 为 `state`），`actions/requested_actions/phases` 为 N 个实际控制间隔。`base_xyyaw` 是实测世界坐标 x/y/yaw，单位 m/rad；旧轨迹未记录该字段时恢复诊断为 unknown，不从数组下标猜测。没有真实物理步就不伪造轨迹行。
`phases` 在下发动作前设置，标明该实际间隔的执行阶段；MolmoBot 使用 `navigation/arrival/vla_operation`，不再用记录器的隐式 `operation` 默认值。
`recording.json` 记录是否完整、步数和错误；事件用于诊断。RGB 图片默认保留，视频、深度、逐物理步日志按需选择。
RGB PNG 使用无损压缩级别 1，减少在线编码耗时，文件体积相应增大。
没有 canonical/raw 双份记录，也不保存独立 replay、final-state、outcome 或物理日志派生索引。

关闭逐物理步日志时仍保留 4 ms 任务事实与原接触统计，只省去完整遥测行构造和序列化；开启日志时保留原字段。复用的闭爪前 planner 目录用 `planner_reuse.json` 引用同 run 内首次构造的资源目录，路径相对于 run 根目录；当前 trial 的 seed、规划、FOV 与 solver 日志仍独立记录。

RBY-1 的 20D 动作：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
底盘/手臂是增量，夹爪/躯干是绝对目标；机器人配置决定控制模式和合法范围。

PandaOmron 使用语义 `base_xyyaw / lift / arm_q / gripper_width`，在记录边界按明确字段顺序展平为绝对目标；位置与开口单位 m，角度 rad，base 使用世界参考系。`recording.json` 的 `layout` 保存 state/action 字段、布局与 `state[t], action[t], state[t+1]` 时间约定；物化后的 `metadata.json` 同样保留。Panda 不填充左臂或头部，也不写 `state22`。不同本体布局分别读取 batch，运行步骤见 [Panda 指南](../reports/panda-omron/docs/panda-omron.md)。

## 结果含义

Benchmark v0.1：每个合格 S0 一条 episode，不按 S0×S1 复制。标定开放底盘仍有效；≤20% / ≥60% 表示从初始站位启动的参考协议任务成功率。区域由高率初始站位构造，不从成功轨迹终点补造高率站位。

`oracle.json` 的 `regions` 按 scene 保存网格、朝向、锚点、原始计数和局部 patch；`episodes` 按 episode ID 保存 case/task/failure_family、S0 计数及 difficulty。difficulty 包含 `path_length_m`、`euclidean_distance_m`、`detour_factor`、`recovery_ratio` 及分子/分母；欧氏距离为零时 detour 为 null。区域近似不是连续空间的实测成功概率图。

评测 `results.jsonl` 同时保存任务结论和 `recovery_success/recovery_first_time_s/base_path_length_m/final_displacement_m/max_displacement_m/base_yaw_travel_rad/final_yaw_delta_rad/max_yaw_delta_rad/effective_adjustment/diagnostic_status`。恢复结果独立于 TSR，轨迹缺失不当作恢复失败。

`summary.json` 给出总体/分组 SR、覆盖率、`macro_by_case`（主 Macro）、`macro_by_task`、`macro_by_failure_family` 及恢复诊断覆盖率。分组有未知任务结果时正式 Macro 为 null；另报有效子集 SR，不移除未完成组。

真实失败、规划无解、超时、初始化/记录/基础设施错误分开记录。逐点成败率只计算已完成的可评测试验；试验数未满不用于困难起点选择。导航成功不等于操作成功；记录完整也不等于任务成功。

MolmoBot 转换按需生成 `data.npz`，引用原图像。非原生控制模式的夹爪动作需要显式标定；不能直接声称此简化导出是完整的上游训练格式。

## 路径与环境

完整输入准备与路径规则以 [setup](setup.md) 为准。

用户配置由各配置加载器明确解析：data_root 显式设置，数据路径相对 data_root，配置与解释器路径相对 repo root 或写绝对路径。保存的 scene/run 配置不做通用字段扫描或目录重定位；普通主记录用 `io.rows()` 读取。
移动输入/输出树时保持相对关系，并重新安装环境；MJB 只承诺同版本恢复，不把导出的 XML 当作已验证的独立资产包。


### 场景库预览

`build-scenes` 的 accepted 场景新增 `before_scene`（相对数据集根目录的同步静置无新增物体对照快照）
和 `before_stations`（该对照的最终站位，沿用 `stations` 的字段）。仅编辑场景保存这两个字段；
旧记录可缺失，不重写历史记录。对照使用与编辑后相同的候选网格、初始化/可见性检查和最终选点规则。

`index.html` 引用 `previews/<scene_id>.png`，每个场景一张组合图，不生成散落子图。
这些是只读派生预览，不是机器人相机输入，也不是操作成败证据。预览错误只在日志和页面展示，
不改写主记录。补生成可覆盖派生图片和页面，不覆盖场景快照或采样记录。

## Benchmark 可视化与视频

`<benchmark>/visualization/` 输出每条 episode 的 `<episode_id>.png` 与 `summary.png`，仅含静态构建信息和私有 oracle。`<evaluation>/visualization/` 独立输出实际轨迹、结果和两个视角最终帧的组合 PNG。评测读 benchmark 原始数据，不回写 benchmark，也不依赖其图片是否已生成。不生成 HTML 或 JavaScript。

评测 rollout 默认输出 `videos/head.mp4`、`videos/third_person.mp4`；录制器在初始状态及每个实际控制边界各捕获一次。`recording.json.videos` 按视角保存 `path`、`frames`、`fps`、`sample_times_s`、`source`。`source` 为 `live_render`、`recorded_images` 或 `recorded_state_reconstruction`。相机逐帧 PNG 不再默认输出于 benchmark 评测；其他采集流程保持各自配置。操作步骤见 [benchmark 指南](../reports/benchmark/docs/运行指南.md)。

## 实测状态离线回放

`trajectory.npz` 同时保存初始帧及每个实际控制步后的 `integration_state`、`state_spec`、`control_json`、`clock_remainder`、相机位姿和内参。`actions`、`requested_actions`、`phases` 仍各对应一次实际控制步，不为无执行的 trial 补行。

`recording.json` 保存 MuJoCo 版本、`model_dir`、相机别名/名称、尺寸、控制频率和 `replay_complete`。公共准备资料位于 `preparations/<scene>/<station>/`，trial 引用同一准备状态，但独立执行。数值记录完整与视觉示教模态完整分别判定。

离线输出的 `replay.json` 标明 `source=measured_state_replay`、实测时间戳、视频播放时间及原始帧索引。回放只恢复实测状态并调用 `mj_forward`，不积分、不重新判定成败。
