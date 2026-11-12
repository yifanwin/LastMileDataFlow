# 运行手册

- 状态：active
- 日期：2026-11-12
- 替代：无
- 关联：../reports/simplify-refactor/plan/01-refactor-plan.md

从仓库根目录执行。先按 setup.md 安装依赖并准备资产。下列 GPU 编号仅示范，运行前用 nvidia-smi 选择空闲卡；每次使用新输出目录。

```bash
source .venv/bin/activate   # 渲染需要的 libEGL.so.1 在激活时接上
PY=.venv/bin/python
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

逐场景重开快照，在目标周围 1m、间距 0.25m 的格点上按覆盖顺序做真实初始化，每场景最多取 max_final_stations 个合法站位（编辑场景另测目标可见性）。结果写回场景库：stations.jsonl、stations_summary.json、index.html 与 previews/。没有合法站位的场景在这一步淘汰。后面各步只认采过站位的场景库，缺 stations.jsonl 会报错。采样参数仍在场景配置的 station 段。

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

## 7. 渲染与失败结果

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
