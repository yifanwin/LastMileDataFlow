# 运行手册

- 状态：active
- 日期：2026-11-23
- 替代：无
- 关联：../reports/simplify-refactor/plan/01-refactor-plan.md

从仓库根目录执行。先按 setup.md 安装依赖并准备资产。下列 GPU 编号仅示范，运行前用 nvidia-smi 选择空闲卡；每次使用新输出目录。

```bash
source .venv/bin/activate   # 渲染需要的 libEGL.so.1 在激活时接上
PY=.venv/bin/python
export PYTHONPATH="$PWD/src"  # 当前工作区源码，避免 .pth 指向其他工作区
nvidia-smi
```

simplify worktree 没有独立环境时，激活主仓库的环境，使用主仓库已有解释器并设置 PYTHONPATH=src；不要重复安装到其他 worktree 的环境。

## 1. 场景构建

修改 configs/scenes/panda_omron.yaml 中的数据路径、资产索引和 houses（配置均为 YAML，只写与默认值不同的字段）：

```bash
$PY scripts/scenes/build.py --config configs/scenes/panda_omron.yaml --output outputs/batches/example/runs/scenes --gpu 0
```

每任务默认原场景一份、编辑场景一份；桌面1–3个、目标0.3m内同一支撑面，地面0–2个。edits.enabled=false 只构建原场景。输出 scenes.jsonl 与 scene-files/。这一步只做物理检查，不采站位；接受场景不等于任务成功。

## 2. 站位采样

```bash
$PY scripts/station_map/sample.py --scenes outputs/batches/example/runs/scenes --gpu 0
```

逐场景重开快照，在目标周围 1m、间距 0.25m 的格点上按覆盖顺序做真实初始化，每场景最多取 max_final_stations 个合法站位（编辑场景另测目标可见性）。结果写回场景库：stations.jsonl、stations_summary.json、index.html 与 previews/。没有合法站位的场景在这一步淘汰。读取场景库本身不需要站位；标定、benchmark 等用到站位的入口会显式检查，缺 stations.jsonl 时报错。采样参数仍在场景配置的 station 段。

## 3. 站位标定

`--config` 是 configs/station_map 下的执行配置。进程数、记录模式和试验预算用命令行给出（`--assets-dir --max-trials --record-video --recording-mode --planning-workers-per-gpu --cpu-workers --preparation-workers`），见 configs/README.md。

```bash
$PY scripts/station_map/planner_trials.py --dataset outputs/batches/example/runs/scenes --config configs/station_map/panda_omron_curobo.yaml --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output outputs/batches/example/runs/stations --gpu 0
```

只用 cuRobo；已有站位不重采。--max-trials 可做少量检查，但不完整标定不能用于正式配对。--recording-mode numerical（默认）不记录训练RGB；visual 记录RGB，--record-video 额外录像。逐物理步日志和深度录制已移除。

## 4. 连续采集

```bash
$PY scripts/lastmile_dataset/collect.py --dataset outputs/batches/example/runs/scenes --calibration outputs/batches/example/runs/stations --config configs/station_map/panda_omron_curobo.yaml --assets-dir /data1/wenyifan/lmdf/molmospaces_data/assets --output outputs/batches/example/runs/collect --gpu 0
```

省略 --calibration 时先在当前 run 标定。输入、机器人、传感器和物理执行参数必须匹配。保留全部合格 S0/S1 配对及负结果；成功且完整的示教导出为内部 rollout_path，直接用 load_training_batch 读取，无独立物化步骤。

## 5. Benchmark

```bash
$PY scripts/lastmile_benchmark/build.py --dataset outputs/batches/example/runs/scenes --calibration outputs/batches/example/runs/stations --config configs/lastmile_benchmark/panda_omron.yaml --output outputs/batches/example/runs/bench
$PY scripts/lastmile_benchmark/evaluate.py --benchmark outputs/batches/example/runs/bench --config configs/policies/pi05_panda_omron.yaml --output outputs/batches/example/runs/bench-eval --gpu 0
```

构建读取实测成功率，不重新标定。MolmoBot 使用 RBY-1 native profile；π0.5 的 embodiment 必须匹配本体。场景控制模式不同不能直接替换策略或复用标定。

## 6. RL 站位图

保持独立 RL 环境，使用 scripts/station_map/rl/ 下的 train.py、score.py、evaluate.py；先查看 --help。`--gpu` 填物理卡号，train.py 可给多张（每张一个 Sampler，第一张同时放 Learner），原来的 `--gpus 0,5` 写成 `--gpu 0 5`。配置入口为 configs/station_map/rl/val103_cup30.yaml。不要用 cuRobo 主环境缺少 mjlab 的结果冒充 RL 通过。

### Cup_30 物体级抓取库

当前三个 Cup_30 RL 配置及 Panda cuRobo 配置使用同一物体级预处理库：21×21指腹间配对射线命中杯体至少30%（133/441），再FPS最多128条。杯壁也计入，不使用5mm杯沿限制。库中的位姿相对物体，未叠加roll；同一资产、相同尺寸、相同夹爪及缩放可跨场景复用。可达性、场景碰撞和轨迹仍在各场景检查。

先离线生成库（CPU静态查询，无动力学；重新生成时使用新的run目录）：

```bash
MUJOCO_GL=disable PYTHONPATH="$PWD/src" $PY scripts/operation/preprocess_grasps.py \
  --scene /data1/wenyifan/lmdf/molmospaces_data/assets/scenes/procthor-10k-val/val_103.xml \
  --metadata /data1/wenyifan/lmdf/molmospaces_data/assets/scenes/procthor-10k-val/val_103_metadata.json \
  --target Cup_30 \
  --grasps /data1/wenyifan/lmdf/molmospaces_data/mujoco/grasps/droid/20251116/Cup_30/Cup_30_grasps_filtered.npz \
  --robot-config configs/robots/panda_omron.yaml --robot-scale 0.9 \
  --roll-rad 1.5707963267948966 --resolution 21 --min-hit-ratio 0.3 --hand-clearance-m 0.003 --fps-count 128 \
  --output outputs/experiments/grasp-fps-all/runs/cup30-panda09-rays21-ratio30-hand3mm-fps128
```

RL的`grasp.preprocessed_path`指向生成的`grasps.npz`，路径相对仓库根目录。`grasp.sample_count`（默认16）和`grasp.sample_seed`（默认42）控制训练/评估开始前的均匀无放回抽样，不再FPS；所有站位共用该子集，episode再从中均匀选一条并固定。

cuRobo用`grasp_preprocessed_paths: {Cup_30: <文件路径>}`按物体资产选择库，以`max_grasp_candidates`和`grasp_sample_seed`控制同样的抽样；每次试验使用相同子集，规划整池。相同库、数量和seed与RL得到相同行号。产物缺失时需先预处理，不自动回退。库不保存场景实例body名，加载时绑定当前目标。

当前库适用于Panda缩放0.9、局部Z +π/2。改变物体尺寸、夹爪、缩放或roll后重新预处理。生成抽样图时，沿用下方可视化命令，把`--grasps`改为预处理NPZ，并加`--sampling uniform --count 16 --seed 42`。

### 真实杯子随机站位 SAC

从 `rl-cup-random-stations` 工作区运行。使用本工作区源码和研究环境的图形动态库：

```bash
export PYTHONPATH="$PWD/src"
export MUJOCO_GL=egl
export LD_LIBRARY_PATH="$PWD/.venv/lib/native${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
.venv/bin/python scripts/station_map/rl/train.py \
  --config configs/station_map/rl/val103_cup30_random_stations_facing.yaml \
  --gpu 0 1 2 3 --seed 42 \
  --output outputs/experiments/rl-cup-random-stations/runs/<新run>
```

GPU编号按实时负载选择。配置为800万条健康经验、每卡128环境、batch512、预热20,000、每128条更新一次。每回合随机选择正对目标的有效站位和16条子集中的一个候选，底盘物理固定、机械臂从Home开始，候选回合内固定。`grasp.preprocessed_path`引用已有128条库，`sample_count: 16`和`sample_seed: 42`明确控制均匀无放回抽样；所有进程共享同一子集。更换物体前先离线预处理，不允许真实物体接触训练回退到原始库。

69维观测使用真实碰撞几何的局部尺寸及最低点，候选误差随物体姿态更新。成功要求同一物理子步双指法向力各>0.5 N、碰撞几何最低点离原桌面≥1 cm、无支撑接触，连续保持0.25 s。训练采用当前方块接触抬升奖励，关闭周期评估；`grasp-subset.json`和checkpoint记录实际子集，episode记录原始抓取row。

训练结束后独立评估 `best.pt`（完整100个健康非预热回合窗口的训练成功率选择）：

```bash
.venv/bin/python scripts/station_map/rl/evaluate.py \
  --config configs/station_map/rl/val103_cup30_random_stations_facing.yaml \
  --checkpoint outputs/experiments/rl-cup-random-stations/runs/<run>/best.pt \
  --gpu 0 --num-envs 20 \
  --output outputs/experiments/rl-cup-random-stations/runs/<run>/evaluation
```

默认20个随机有效站位各一次，trial种子为 `evaluation.seed + trial_index`，独立选择候选和动作，保存每次视频与回放。普通失败不重试，分别统计成功和无碰撞成功，基础设施异常单列。现有无仿真soft-Q入口仍仅支持方块。

### 原始抓取库 FPS

未配置预处理库的 RL 任务默认从全部有效位姿中 FPS 选 16 个，首点使用独立固定种子 42；所有站位共用一组，每个 episode 均匀随机选一个并在 episode 内固定。默认距离尺度为 0.10 m / π/2；不参考 Home TCP，不做最近 64 个预筛选，也不默认截断为 1024 行。Cube 解析抓取分支不读取抓取库。

在 RL YAML 的 `grasp` 段覆盖 `fps_count`、`fps_seed` 即可调整数量与种子；`fps_position_scale_m`、`fps_rotation_scale_rad` 调整距离尺度。cuRobo 配置使用 `grasp_candidate_order: fps`、`max_grasp_candidates`、`grasp_fps_seed`，仍规划整池；旧 `tcp_distance` 值已替换为 `fps`，`facing` 模式保留。相同位姿库、顺序、参数与种子生成相同候选池。

从仓库根目录生成 Cup_30 的全部 16 个抓取叠加图（静态出图，不使用 GPU）：

```bash
MUJOCO_GL=disable PYTHONPATH="$PWD/src" $PY scripts/operation/visualize_grasps.py \
  --scene /data1/wenyifan/lmdf/molmospaces_data/assets/scenes/procthor-10k-val/val_103.xml \
  --metadata /data1/wenyifan/lmdf/molmospaces_data/assets/scenes/procthor-10k-val/val_103_metadata.json \
  --target Cup_30 \
  --robot-config configs/robots/panda_omron.yaml --robot-scale 0.9 \
  --grasps /data1/wenyifan/lmdf/molmospaces_data/mujoco/grasps/droid/20251116/Cup_30/Cup_30_grasps_filtered.npz \
  --roll-rad 1.5707963267948966 \
  --output outputs/experiments/grasp-fps-all/runs/val103-cup30-grippers
```

数量和首点种子可通过 `--count`、`--seed` 覆盖。输出全部16个原生Panda夹爪与杯子的叠加图 `grasps.png`、逐个位姿的4×4图 `individual-grasps.png`、`selection.json` 与 `selected-grasps.npz`；NPZ 位姿在目标 body 局部坐标系中，已包含 `--roll-rad`。图中杯子和16个 TCP 位姿统一映射到场景世界坐标，显示时仅减去杯子原点平移；夹爪使用原生visual mesh，双指全开，按模型测得的root-to-TCP变换放置；`--robot-config` 指定Panda配置，`--robot-scale` 与RL场景一致。NPZ 另存 `world_transforms` 和 `object_world`，便于核对映射；图片不代表可达性验证。场景 XML 使用原输入路径，保留符号链接所在目录的资产解析语义。

## 7. 渲染与失败结果

## 7. RoboCasa365 原生场景

用独立环境 `.venv-robocasa`。场景构建沿用同一入口，配置的 `scene_source: robocasa365` 会走 RoboCasa 导入：

```bash
source .venv-robocasa/bin/activate
python scripts/scenes/build.py --config configs/scenes/robocasa365_pick.yaml --output outputs/experiments/<单元>/runs/<run> --gpu 0
python tests/smoke/robocasa_native.py --scenes outputs/experiments/<单元>/runs/<run> --output outputs/experiments/<单元>/runs/<验证run> --gpu 0
```

只导入并冻结实例，不采站位、不标定；细节见 [RoboCasa 运行指南](../reports/robocasa/delivery/running.md)。

## 8. 渲染与失败结果

入口自动解析物理 GPU 和 EGL 的对应关系。手动覆盖示例为 --gpu 0 --egl-devices 7（本机2026-11-11映射），其他机器重新查询，不硬编码。多卡参数一一对应；CPU trial 不暴露 CUDA，仍在指定卡渲染。

规划无解、导航失败、任务失败属于真实负结果。记录不完整或基础设施失败不能冒充任务失败。共享 GPU/文件系统故障停止新增工作并保存已完成记录。数据格式见 data-format.md。

## π0.5 val103 / Cup_30 多初始站位推理

先激活仿真环境（包含 EGL 动态库），并指定已有 π0.5 环境的解释器：

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD/src"
python scripts/lastmile_benchmark/station_policy.py \
  --output outputs/experiments/pi05-val103-cup30/runs/pilot \
  --gpu 0 --station-limit 1 \
  --policy-python /home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.venv-pi05/bin/python
```

试运行检查完成后，改用新 run 路径，去掉 `--station-limit 1`，按实时资源填写 `--gpu 0 1 2 3 4 5 6 7`。每卡常驻一个模型服务，依次完成所分配站位的 seed 42、43、44，不因首次成功跳过后两次。

默认读取 RL 的 val103-cup30 场景和 XY 采样配置，只保留正对杯子几何中心的朝向；使用 RoboCasa 控制和完整房间，初态合法性按本次模型重新检查。不使用规划器、导航或可达性筛选。成功条件为 `rl-pick-1cm-1s-v1`，允许底盘和升降运动，因此不能直接视为固定底盘 SAC 的同条件比较。

`--prepare-only` 仅保存本 run 的场景、候选与筛选结果；`--summarize` 汇总已有 run。查看 `progress-gpu-*.json`、`logs/worker-*.log` 和 `trials-gpu-*.jsonl` 跟踪进度。`summary.json` 列出完整性、成功率、覆盖率、碰撞和设施错误；缺少的 seed 明确保留。热图在单元 `analysis/<run>/station-success.png`；回合轨迹、物理事实和四路视频在 run 的 `attempts/` 中按站位分组。
