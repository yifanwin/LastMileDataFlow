# RoboCasa365 原生场景运行指南

- 状态：active
- 日期：2026-11-13
- 关联：01-native-pick.md

从 worktree 根目录执行。RoboCasa 用独立环境 `.venv-robocasa`，不要装进 LMDF 的 `.venv`。

## 1. 安装

```bash
scripts/install/robocasa.sh                       # 环境；robosuite/robocasa 默认取 reference_repos 下的检出
scripts/install/robocasa.sh "" "" tex fixtures_lw objs_objaverse objs_lw   # 同时下载默认资产（约 4 GB）
source .venv-robocasa/bin/activate                # 每个新 shell 都要激活（接上 libEGL）
```

资产落在 robocasa 检出目录的 `robocasa/models/assets/`。默认环境的物体库只用 objaverse 和 lightwheel，`tex_generative`、`objs_aigen` 不需要。

## 2. 导入场景

```bash
python scripts/scenes/build.py --config configs/scenes/robocasa365_pick.yaml \
  --output outputs/experiments/robocasa-native-pick/runs/<run> --gpu <物理卡号>
```

输出 `scenes.jsonl`、`summary.json` 和 `scenes/<实例>/`（`env.json`、`ep_meta.json`、`model.xml`、`initial_state.npz`、`initial_facts.json`、预览图）。实例在配置的 `instances` 里改；只用 `--gpu` 的第一张卡渲染。

从冻结厨房添加台面障碍、重查共用站位并导出选片图，见[场景构建与人工选集](03-pick-scene-selection.md)的复跑命令。

## 3. 验证

```bash
python tests/smoke/robocasa_native.py --scenes outputs/experiments/robocasa-native-pick/runs/<场景run> \
  --output outputs/experiments/robocasa-native-pick/runs/<验证run> --gpu <物理卡号>
```

`summary.json` 给出每个实例各项检查；`<实例>/validation.json` 给出数值。随机动作 rollout 不构成抓取验收。

## 4. 在代码里使用

```python
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime
from lastmile_dataflow.operation.outcomes.robocasa_pick import PickJudge

runtime = RoboCasaRuntime("<场景run>/scenes/<实例>")      # 加载即为冻结初态
state = runtime.set_station(x, y, yaw)                    # 可选：先 reset 再放底盘
judge = PickJudge(runtime.target, runtime.reset().target_pose[2])
step = runtime.step(action)                               # action 形状 (runtime.action_spec.dim,)，见 action_spec
status = judge.update(runtime.get_state())                # status.success / lift_m / hold_s / conditions
frame = runtime.render("robot0_agentview_left")
runtime.close()
```

`step.native["task_success"]` 是原生放置任务结果，与派生 pick 判定分开。
