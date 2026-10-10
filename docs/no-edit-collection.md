# 原始 ProcTHOR val 场景无编辑构建

直接操作请看[运行手册](无场景编辑数据构造运行手册.md)。

复用 **RBY1M + 20D 全向底盘协议**。每次采样点评测及 S1 操作都由 cuRobo **联合规划 base_x/base_y/base_theta 与单臂，共 10D**；不是机械臂规划加零底盘命令。不进入场景编辑、Agent/人工审批或旧成功标签链路。
默认半径 **2 m**，间距为实际底盘/轮子外接圆直径的一半；每个有效点 **5 次**独立评测。
总成功率 **<5%** 丢弃该任务；**≥1 段**连续导航+操作真实成功即可进入最终数据集，优先采三段。

## 运行

在本分支 Worktree 根目录运行。资产路径是外部只读数据，不导入相邻 Python 工程。

```bash
export DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export MPLCONFIGDIR=/tmp/lastmile-mpl

# 全部匹配 val_<id>.xml 的原始场景；不重复统计 *_ceiling.xml
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --run-id no-edit-val-four-view-full-20261010 --workers 4 --gpu-ids 0 1 2 3 4 5 6 7

# 仅查看状态，不启动任何仿真
bin/lastmile-dataflow no-edit-status outputs/no_edit/no-edit-val-four-view-full-20261010

# 中断后继续已完成 attempt 边界；配置、代码和输入身份必须不变
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --run-id no-edit-val-four-view-full-20261010 --workers 4 --gpu-ids 0 1 2 3 4 5 6 7 --resume
```

每次派发读取 GPU 0–7 的实际利用率/显存，优先最空闲卡；避开利用率 >50% 或空闲显存 <10 GB 的卡。
每张卡最多一个场景进程。CUDA 按可见卡编号使用，EGL 使用对应物理卡编号。
没有满足条件的卡时等待，不挤占忙卡。默认 CLI 并发为 2；上述完整运行示例显式用 4。

`--spacing-m`、`--trials-per-station` 可覆盖默认值。`configs/no_edit/val.json` 控制其余参数。
`--houses`、`--targets`、`--max-tasks`、`--max-trials` 是明确的子集/调试预算；有这些参数不能宣称全量完成。
`--index-only` 只列场景，不进行操作评测。

## 构建步骤

1. 加载原始 XML 和 metadata，只挂接现有机器人；冻结原场景一次。
2. 依据编译后的实际 free/hinge/slide 关节和局部碰撞几何枚举任务。
3. pick：最小边 ≤0.10 m 且根物体能移动。此尺寸门槛不代表必能夹住。
4. open：有可开启门/抽屉关节；不解冻、不把已经开启的门关回去。每个部件独立成任务。
5. 生成目标圆盘上的均匀方格；只在 begin 前设置机器人 base/head。目标使用真实 head 分割可见性。
6. 过滤无地面支撑、机器人初始交叠/穿模和不可见点；不因远而提前删去合法站位。
7. 五次独立操作；抓法/手臂/合法 1D torso 候选策略和预算对所有点相同，种子明确。
8. 生成实测站位表、失败分类与 Gaussian 热图。低成功率任务不发布示教，但保留热图和原因。
9. 低成功率站位优先，同档尽量最远分散；每个 S0 的 S1 按实测率排序且须有成功见证和 A* 路径。
10. 同一 Simulation 连续从 S0 移动到 S1，再按到站实测状态规划操作。每个 S1 一次重试；不恢复现场。
11. S1 失败时重新选择次高点，创建新的独立 rollout 从 S0 开始。≥1 段成功即发布，尽力补齐三段。

## 操作与失败证据

pick 使用独立 `mobile-pick-v2`：复用原协议的双指真实受力、抬升 ≥5 cm、无支撑稳定 ≥2 s、滑移与碰撞检查，但允许已规划底盘移动，躯干误差只记录、不硬终止（`torso_error_policy=record_only`）。实际联动目标与物理限位仍保留，Head/闲置臂检查不变。原编辑/固定站位流水线的 `strict-pick-v3` 仍锁底盘，不更改默认验收。

cuRobo 使用规划器构建时固定的实测底盘坐标系；目标、碰撞世界和已移动底盘状态始终保持一致，不在更新世界时换坐标系。底盘虚拟关节限位写入输出目录的派生 URDF，不修改资产。
执行通过 20D 的 `[0:3]` 下发真实底盘增量，与机械臂同步限速；逐物理步检查 2 m 工作空间、全身碰撞、torso/闲置臂/head 约束，不传送、不把正常底盘移动判为漂移失败。
先真实抬升离开支撑，再向规划器附着保守持物碰撞球；MuJoCo 中绝不焊接物体或改写其姿态。

open 沿实际 hinge 轴/锚点或 slide 方向分段规划，从实测部件状态更新目标和碰撞世界。
验收采用声明的开启行程和接触保持，不能用 TCP 到位或规划成功替代门/抽屉真实运动。
无显式把手时尝试部件几何的 pinch 候选，可能失败，不能宣称等价于专用把手模型。

归因保留 Reachability、Collision（双方 geom/body、位置、深度、时刻）、PlanningFailure、
MissedGrasp、GraspSlip、DroppedObject、LostHandleContact、TrackingFailure 与 Unknown。
无碰撞 IK 诊断无解只声明已测试候选/种子范围，不证明所有抓法都不可达。

规划无解计入操作策略失败率，但其物理结果仍为 unknown/not_executed，动作流为空，不生成执行视频。
设施异常、中断和未测试不计零；站位图未评完整时不应用 5% 丢弃规则。
只保留机器人相关的必要初始化/物理检查，不增加全场景语义审批或重新编辑。

## 热图

每个任务的 `maps/` 必须生成：

- `success_heatmap.png` / `.svg`：地面 XY 图，标实际站位和障碍、未测区域。
- `success_heatmap.npz`：原点、分辨率、自由地面、平滑率、加权样本数。
- `heatmap.json`：核/观测单位/未知处理声明。
- `index.html`：离线可读、内嵌 PNG。

Gaussian 核基于地面栅格绕障碍的最短路径距离，σ 默认一个采样间距，截断 3σ。
成功数和有效终局次数分别平滑后相除；障碍、不可见和未测点不是零成功率。
区域图是插值，不是新增实验；S0/S1 使用原始实测率选择。成功率以采样点为初态，操作中允许移动底盘，不是固定底盘机械臂可达率。

## 输出与存储

`outputs/no_edit/<run>/` 包含冻结配置、1000 场景清单（按本机导出文件实查）、GPU 派发和批量状态。
每个 `scenes/val_<id>/` 保存一份原场景快照、任务/跳过清单和日志。
每个任务保存站位、可见性、尝试索引、统计、热图、选点和结果。

站位尝试保存紧凑机器人/目标状态、20D 动作、逐物理步 `physics.jsonl.gz`、真实 qpos replay 和终态快照；
head 初态图来自站位记录。**所有真实执行的站位试验保留四路 MP4**，以便选点后提供 S0 的真实失败操作视频；为控制空间，站位试验不额外保存逐步相机 PNG。
**连续 rollout 保存完整四相机 RGB/MP4 和逐步全状态**，用于最终数据集。失败 rollout 证据也保留。
可用空间低于 50 GB 时停止派发，保留未完成状态，不删除用户已有数据。
默认单次 attempt 上限 300 s、单场景上限 24 h，均可配置；超时记录 incomplete，不记作真实操作失败。

`dataset_index.jsonl` 仅引用真正成功的连续 rollout；每条含任务语言、动作、四相机、S0 失败证据及 S0/S1。
S0 失败归因和 `s0_failure_videos` 来自独立站位评测，不冒充从失败现场恢复。
无解且尚未执行的 S0 保留初态图/规划证据，视频状态明确为 `not_executed`，不会伪造失败操作视频。连续视频覆盖 navigation、arrival、操作及实际发生的重试，逐帧时间在 `videos.json`，阶段在轨迹中。
有一段/两段的 retained 任务会明确写实际段数，不能说采满三段。

## 验证边界

```bash
PYTHONPATH=src "$DATAFLOW_PYTHON" -m unittest discover -s tests -v
```

单元 fixture 仅证明程序逻辑。真实 CPU/GPU/场景结果以对应 run 产物为准。
原始场景里有物体在关闭的冰箱/柜子内部，找不到 head 可见无碰撞站位是正常跳过结果，不隐藏遮挡物。
保守导航栅格可能漏掉狭窄可行路径；不是完整连续空间可达性的证明。
基础设施故障会停止新派发，正在跑的其他场景收尾；失败证据不伪造成功。

## 四路视频与复用相机

每个实际执行的 S0 失败操作与 S0→S1 连续 rollout 均保存四路同步视频：
`head_camera.mp4`、`wrist_camera_l.mp4`、`wrist_camera_r.mp4`、`third_person_camera.mp4`。
前三路保持原机器人 RGB；第四路是参考闭环视频风格的 **1280×720 分析合成画面**，不是另加第五路。
主画面是真实第三人称相机，右侧是同步活动夹爪近景，叠加阶段、仿真时间、目标抬升、实际接触/支撑和规划/实走路线。未测接触显示 `--`，不假定成功。
连续视频包含移动与操作，并记录真实重试；无物理执行的规划无解不伪造视频。

第三人称相机自动围住机器人和目标并保留周围场景；通过实际分割选择初始视角，不隐藏墙壁/物体。严重遮挡可能仍存在，初始可见像素记录于 `third_person_initial.json`。
可独立复用：`sim.enable_third_person()` 后 `sim.render(width, height)` 返回新增的 `third_person_camera` RGB；原编辑流水线默认不启用。
连续 rollout 的第三人称逐帧 PNG 是未叠加分析信息的原始画面；MP4 是分析合成版，声明见 `analysis_video.json`。

## 整体生成时间

运行根目录的 `timing.json` 记录 `started_at_utc`、`ended_at_utc`、`elapsed_wall_s`、`elapsed_active_s` 及每次进程 session。
`summary.json` 同步提供 `generation_timing`。运行中每约10秒更新，结束或正常中断写终止时间。
墙钟耗时包含准备、GPU 等待、规划、渲染编码及断点续跑间隔；活动耗时是各次主进程运行时长之和，**不是并行 worker 时长相加**。
突发 kill 后无法知道的 session 终止时间明确标为未知，不补造时间。全量未结束时 `ended_at_utc` 为 null。
