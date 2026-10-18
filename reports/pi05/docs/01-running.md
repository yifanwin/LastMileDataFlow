# π0.5 安装、数据准备与运行

状态：active  
日期：2026-10-18  
替代：无  
关联：[构建方案](../plan/01-pi05-integration-build.md)、[构建结果](../delivery/03-implementation.md)

以下命令均从项目根目录运行。项目使用 `.venv` 管理仿真与 cuRobo，使用 `.venv-pi05` 管理 JAX 推理；普通单仓库工作区使用相同目录。

## 1. 安装与资源

```bash
scripts/install.sh
uv pip install --python .venv/bin/python '.[panda-factory,pi05-data]'
scripts/install_pi05.sh
mkdir -p data/models
curl -L https://storage.googleapis.com/big_vision/paligemma_tokenizer.model \
  -o data/models/paligemma_tokenizer.model
```

配置中填写实际资源位置：

| 配置 | 输入 |
|---|---|
| `configs/policies/pi05_panda_omron.json` | RoboCasa365 checkpoint 的 `params/` 与 `assets/norm_stats.json`；本机为 `pi05_pretrain_human300/multitask_learning/75000` |
| `configs/policies/pi05_rby1.json` | `/nas/models/pi05_base`；本体统计由第 2 节生成 |
| `configs/robots/panda_omron_robocasa.json` | 项目 `data/assets/robots/panda_omron/model.xml` 及网格 |
| `configs/scene_datasets/rby1_position_head320.json` | RBY-1 position 模型与三相机 |

Panda 资产沿用已有 [Panda 准备步骤](../../panda-omron/docs/panda-omron.md)。开发时可从 robosuite 导出到项目数据目录；导出后启动不需要参考仓库。NAS 权重也可复制到项目数据目录并修改配置。

模型启动不联网。π0.5 子进程自动去除宿主 `LD_LIBRARY_PATH`，使用其环境安装的 CUDA 库。直接执行模型 smoke 时也要使用下面的 `env -u LD_LIBRARY_PATH`。

## 2. 准备 RBY-1 数据与统计

输入优先使用 `config.json` 中 `kind=materialized_lastmile` 的数据集：

```bash
.venv/bin/python -m lastmile_dataflow.data.export.pi05_dataset \
  --dataset /path/to/materialized_lastmile \
  --output outputs/training/pi05-rby1-position
```

引用型 lastmile dataset 使用同一命令，会先物化。只有标定结果时，显式选择其完整成功示教：

```bash
.venv/bin/python -m lastmile_dataflow.data.export.pi05_dataset \
  --evaluations /path/to/evaluation_run_a /path/to/evaluation_run_b \
  --output outputs/training/pi05-rby1-position
```

输出目录必须为新目录。本轮实际源为 `refactor-light/outputs/stations` 和 `refactor-light/outputs/station_evaluations/small3/curobo_v2_001`。

产物：

```text
outputs/training/pi05-rby1-position/
  reference/                  # 从标定结果建立引用时生成
  materialized/               # 输入尚未物化时生成；包含原始轨迹、RGB、任务
  lerobot/
    data/                     # LeRobot v2.1 parquet，含图像
    meta/                     # episode、task 与原示教来源
    assets/norm_stats.json    # OpenPI JSON：state/actions 的 mean/std/q01/q99
    data_config.json          # RBY-1 本体、相机、动作块与归一化约定
```

每个真实动作只统计一次，配对其执行前 state22。动作保留 rby1-20d：底盘和双臂为增量，夹爪和躯干为绝对目标；不再做 delta 转换。统计补至 32 维，训练读取在物理维度归一化后补零。末帧只有观测、没有动作，不进入训练行或统计。

读取 OpenPI 模型变换前的训练样本：

```python
from lastmile_dataflow.policies.pi05.data_config import Rby1DataConfig

config = Rby1DataConfig.load('outputs/training/pi05-rby1-position/lerobot/data_config.json')
dataset = config.create_dataset()
sample = dataset[0]  # image、image_mask、state(32)、actions(50,32)、prompt
```

图像为 HWC uint8；state/actions 已归一化，训练时不要再套一层 Normalize 或 DeltaActions。模型侧再做 resize、离散状态提示词和 tokenizer。LeRobot 的末端动作重复及 `actions_is_pad` 保留。此读取器与数据转换不启动训练；完整微调 trainer 尚未交付。LeRobot task 保留原文，DataConfig 中 `task_prompts` 将训练提示词映射为与 rollout 共用的英文任务短句。

## 3. 真实 smoke

GPU 编号按实时空闲显存选择。每次使用新输出目录。

```bash
# Panda 真权重推理；输入为明确标注的合成观测
env -u LD_LIBRARY_PATH .venv-pi05/bin/python -m tests.smoke.pi05 \
  --stage model --gpu 1 --output outputs/diagnostics/pi05/panda-model-new

# RBY-1 真权重 + 本体统计
env -u LD_LIBRARY_PATH .venv-pi05/bin/python -m tests.smoke.pi05 \
  --stage model --policy-config configs/policies/pi05_rby1.json \
  --gpu 1 --output outputs/diagnostics/pi05/rby1-model-new

# 实际相机 / 保持与 10mm TCP 步进 / 底盘平移转向跟踪
.venv/bin/python -m tests.smoke.pi05 --stage observation --gpu 5 \
  --output outputs/diagnostics/pi05/observation-new
.venv/bin/python -m tests.smoke.pi05 --stage control --gpu 5 \
  --output outputs/diagnostics/pi05/control-new
.venv/bin/python -m tests.smoke.pi05 --stage tracking --gpu 5 \
  --output outputs/diagnostics/pi05/tracking-new

# 固定 val103 Cup，π0.5 实际闭环执行；不是已标定的困难 S0
.venv/bin/python -m tests.smoke.pi05 --stage rollout --gpu 1 --horizon 100 \
  --output outputs/diagnostics/pi05/panda-rollout-new
```

模型 smoke 的 `--normalized` 只用于检查基础权重采样，不应用本体统计，也不能作为机器人策略结果。常规策略运行始终要求匹配统计。

`model` 模式默认使用合成零观测；`--observation /path/to/observation.npz` 可提供真实物理 `state`、三路模型图像键和字符串 `prompt`。

## 4. 共用控制与五命令工作流

Panda 的 cuRobo 与 π0.5 都显式选择 `robocasa-v1`。原 position profile 保留已有入口；不要将其已有场景快照直接当作 RoboCasa profile 复用。

```bash
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/panda_omron_robocasa_val103.json \
  --output-dir outputs/scene_datasets --run-id panda-robocasa --gpu 5

.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-robocasa \
  --assets-dir /path/to/assets --config configs/execution/pi05_panda_omron.json \
  --output-dir outputs/station_evaluations --run-id panda-pi05 --gpu 1 \
  --record-video --record-physics

# 路线 B 示教使用相同场景库，改选：
# --config configs/execution/panda_omron_robocasa_curobo.json

.venv/bin/lastmile-dataflow evaluate-bench \
  --benchmark /path/to/matching-profile-benchmark \
  --policy-config configs/policies/pi05_panda_omron.json \
  --output outputs/bench/pi05-panda --gpu 1
```

RBY-1 使用 position 场景库和 `configs/execution/pi05_rby1.json`。MolmoBot 继续使用 native 场景适配与原配置，分别执行。`collect-lastmile` 的连续执行仍只支持 cuRobo。

## 5. 当前动作约定

Panda 20 Hz 控制、2 ms 物理反馈；左外视、腕部、右外视 RGB，state16。动作 12D 为 eef 平移 3、旋转 3、夹爪闭合、底盘 3、躯干、模式。平移/旋转范围为 0.05 m / 0.5 rad，躯干为相对测量位置增量，模式决定使用测量 TCP 或上次目标。OSC 在每个物理步更新。

cuRobo 先以 scratch FK 求参考 TCP，反馈跟踪产生同一种 12D 动作；规划关节目标不会直接驱动机械臂，也不会更新 controller 的零空间目标。轨迹、请求动作和实际 actuator ctrl 分别记录。

参考底盘跟踪通过动作补偿原厂速度执行器摩擦死区，controller 保持原厂参数。RBY-1 策略先限制到 position 协议范围，再检查实际关节/执行器边界；原始预测保存在请求记录中。
