# 数据格式

- 状态：active
- 日期：2026-11-12
- 替代：无
- 关联：architecture.md、运行手册.md

## 运行与场景

所有产物位于实验单元的 runs/<run>/；规则见 ../outputs/RULES.md。config.json 保存有效配置、seed 和输入引用，summary.json 保存真实完成情况。路径搬迁按产物规则处理，不修改原数据冒充新实验。

场景库由两步写成。build_scenes.py 写 config.json、scenes.jsonl、scene-files/；sample_stations.py 再写 stations.jsonl、stations_summary.json、index.html 与 previews/。scenes.jsonl 保留接受与拒绝结果；接受记录包含 scene_id、house、task、scene、edits、construction_checks，不含站位（2026-11-12 起；此前的记录里还有 stations、station_sampling、station_hints，那时没有合法站位的场景在构建时就被拒绝）。stations.jsonl 每个接受场景一行：scene_id、status（accepted/rejected）、reason（rejected 时，如 no_valid_initial_stations）、stations、station_sampling（候选数、初始化次数、被滤掉的站位及原因）。下游用 scenes/library.load_scenes 读取，按 scene_id 把站位并进场景记录，status=rejected 的场景不出现。proposal_intent 现在只取 no_edit/edit，保留字段名以便查看既有结果，不再生成八模式标签或 diagnosis.jsonl。

scene 是相对场景库根目录的快照目录；内有 model.mjb、model.xml、initial.npz 和 config.json。MJB 为实际载入来源，要求匹配生成时的 MuJoCo 版本。XML 可能引用外部资产；场景快照不承诺独立搬迁。

每个任务生成原场景和一个编辑场景（edits.enabled=false 时只有原场景）。edits.placements 记录资产ID、姿态、support_geom/support_instance、measured_size_m 和 world_bounds；kind=surface/ground。编辑不移动目标。编辑场景记录另有 before_scene，指向同任务原场景快照（原场景未接受时为 null），预览按它配对。

站位保留 station_id、xy_id、xy、base、initial、geometry、碰撞/地面与可见性事实，以及 clearance_base_m。默认采样半径1m、间距0.25m，最多20个合法初始姿态；可见性是否作为筛选由配置决定。操作工作区 radius_m 与采样圆分开；导航使用完整房间地面。

资产索引为 assets.jsonl，每项为 AssetProfile，包含 source_xml、xml_path、adapter_version、尺寸、可移动/可加载状态、支撑兼容和角色。真实几何按需加载；资产可加载不证明候选场景物理有效。

## 标定和配对

trials.jsonl 每行一个真实 trial，绑定 scene_id、task_id、station_id、trial、seed、rollout_id。planning_ref 是相对本 run 的规划记录路径。失败、超时与基础设施未知必须保留，不把缺测当成失败或成功。

完整站位需达到 trials_per_station 的有效次数；有效状态为 success、failure、planning_no_solution、timeout（station_map/aggregate.py 的 EVALUABLE）。eligible_stations 按含等号的 lastmile_low/high 划分 S0、S1，配对为所有合格起终点的全组合，不再提供模式分类、标签配对或诊断IK补测。

标定热图写在 figures/<scene_id>/：success_heatmap.npz（origin、resolution_m、free、success_rate、weighted_trials、sigma_m、max_support_distance_m）、success_heatmap_stations.svg 和 heatmap.json（sigma_m、configured_support_distance_m、max_support_distance_m、support_obstacles 及两个文件名）。热图是对每个站位实测 trial 按障碍感知的栅格最短路距离做高斯加权：先累加全部样本的权重再归一化，比率为加权成功数除以加权终止 trial 数；障碍格在插值后遮掉，不代表导航可达；未观测处为空而不是 0。热图只用于展示，配对只看原始标定。

pair_index.jsonl 保存计划全集，包括未执行、无路径、执行失败与成功；成功不代表目录里每个 attempt 都是训练示教。导航终止和操作结果单独判断。

## 原始 attempt

rollouts/<attempt>/ 包含 trajectory.npz、recording.json、result/执行证据、可选 images/<camera>/<frame>.png 与 videos/。当前 recorder 仅存数值边界、RGB及视频，不写逐物理步 gzip 日志或深度。

trajectory.npz 的 n 个动作对应 n+1 个边界状态。包含 actions、requested_actions、phases、time_s、qpos/qvel/ctrl、integration_state、control_json、clock_remainder、相机内外参与本体布局对应 state。state[t]、action[t]、state[t+1] 为实际控制时间关系；不补造物理步。

recording.json 的 layout 给出 state_key、state_size/action_size 或明确字段列表。RBY-1 使用 state22 和20D动作；Panda position 使用语义字段，RoboCasa 使用 state16/action12，不伪填RBY左臂或头部。同一 batch 只能使用兼容布局和相机集合。

相机时刻、控制状态与实际动作属于原始记录；视频用于查看，不代替轨迹或物理任务判定。外部模型、资产、权重不复制进训练数据。

## 自包含训练数据

采集收尾在本 run 的 rollouts/dXXXXXX/ 导出成功完整示教及必需观测；train_index.jsonl 使用内部相对 rollout_path，不依赖 source_run 或外部原始轨迹。source_type 区分 s1_direct_operation 与 s0_to_s1_continuous；同一来源去重，来自不同 run 的相同局部编号分别处理。原 attempt 仍保留供执行分析。

导出轨迹包含 trajectory.npz、recording.json、metadata.json 和必要 RGB。metadata 保存布局、相机及有效配置；配置中的历史外部资产引用仅供解释，训练读取不加载资产。

load_training_batch(dataset, indices, start=0, steps=16) 按真实窗口读取数组与图像，不补帧、不转换动作。步数、动作维度和边界帧数量必须一致。不读取深度。

summary.json 只在导出全部完成后写出，status=completed；中途出错不写 summary.json，读取方据此拒绝读取。它不能替代 task_outcome。训练副本可搬迁读取，场景MJB仍有版本和外部资产约束。

## Benchmark

episodes.jsonl 保存任务与困难S0；oracle.json 保存恢复区域、实测成功率和参考锚点。构建仅消费已有cuRobo标定，按成功率阈值选S0/S1，不输出模式标签。summary 区分计划数、覆盖率、已观察成功率与完整总体成功率；缺失/基础设施错误不冒充任务失败。

## 物理结果与离线查看

execution_termination 表示完成/超时/异常等执行终止，task_outcome 表示物理任务成功/失败/未知。抓取 strict-pick-v3 的双指力、抬升、无支撑保持和滑移阈值仍固定；逐物理步事实继续用于 monitor，只删除冗余日志输出。

离线回放入口为 scripts/replay.py（参数：rollout 目录、输出目录、相机、fps），按记录的状态边界重新渲染，不新执行动作。补预览保留为 Python 函数 data.visualization.scene_preview.export_scene_previews。
