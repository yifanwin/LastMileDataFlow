# PandaOmron 运行指南

状态：active  
日期：2026-10-17  
替代：无  
关联：[全流程需求](../requirement/02-panda-omron-full-flow.md)、[全流程方案](../plan/02-panda-omron-full-flow.md)、[需求](../requirement/01-panda-omron.md)、[实施方案](../plan/01-panda-omron-phase01.md)、[实施结果](../delivery/phase_01/2026-10-17-panda-omron-implementation.md)

以下命令在 `.worktrees/panda-omron-phase01` 根目录执行。资产路径按本机填写；GPU 在每次运行前选择。

## 1. 环境

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[panda-factory]'
nvidia-smi --query-gpu=index,utilization.gpu,memory.free --format=csv,noheader
```

环境使用 MuJoCo 3.5.0、cuRobo V2 0.8.0。真实 GPU/渲染运行需要能访问 NVIDIA 驱动的执行环境；沙箱中看不到设备时，须在可见设备的执行权限下运行。

## 2. Factory 生成整机资产

首次运行生成 `data/assets/robots/panda_omron/`；目录已存在时改用新的资产目录并更新机器人配置，不覆盖原资产。

```bash
.venv/bin/python tests/smoke/panda_omron.py --stage factory \
  --factory-root /data0/wenyifan/MoMaTrajGen/robosuite \
  --asset-output data/assets/robots/panda_omron \
  --gpu 2 --output outputs/diagnostics/panda-omron/factory-new
```

机器人配置为 [panda_omron.json](../../../configs/robots/panda_omron.json)。生成阶段读取本地 robosuite factory；后续运行只加载生成的 XML/网格，不依赖 molmospaces Python 包。

## 3. 控制与对齐

```bash
.venv/bin/python tests/smoke/panda_omron.py --stage control \
  --gpu 2 --output outputs/diagnostics/panda-omron/control-new
.venv/bin/python tests/smoke/panda_omron.py --stage align --seed 42 \
  --gpu 2 --output outputs/diagnostics/panda-omron/align-new
```

每次指定新的输出目录。`control` 验证底盘、臂、夹爪与逐物理步升降保持，并保存视频；`align` 验证独立 pivot、20 个随机 TCP/FK 姿态、指关节坐标及分离/重叠碰撞查询。

## 4. 固定场景 pick

编辑 [固定任务配置](../../../configs/execution/panda_omron_fixed_pick.json) 的 `data_root`，确认场景、metadata 和 `assets/grasps/droid/Cup_30/` 存在。其底盘、升降高度和目标均显式指定，不读取站位标定。

```bash
.venv/bin/python tests/smoke/panda_omron.py --stage pick \
  --case configs/execution/panda_omron_fixed_pick.json \
  --seed 42 --gpu 2 --output outputs/diagnostics/panda-omron/pick-new
```

`approach_offset_m=0.01` 控制接近深度；`grasp_roll_rad=π/2` 将候选绕接近轴旋转为本例杯沿径向夹持。这两个值是此固定案例的参数，不代表所有物体的通用抓取修正。

查看 `result.json` 的 `task_outcome`，视频及逐物理步事实保存在同一运行目录。成功要求双指承力、无支撑、抬升 ≥5 cm 并连续保持 ≥2 s。全流程步骤见下节；MolmoBot/VLA 及 benchmark 不在本轮范围。

## 5. 软件回归

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/panda-omron-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -q
```

该命令不自动执行真实 smoke；已有一个 OpenGL 渲染测试需要单独显式启用。

## 6. 移动与全流程

移动配置：[panda_omron_mobile.json](../../../configs/robots/panda_omron_mobile.json)；执行配置：[panda_omron_curobo.json](../../../configs/execution/panda_omron_curobo.json)。其中前向相机与腕相机是机器人实际安装相机；不使用 RBY-1 头部。`head_fov_enabled=false` 表示不启用 RBY 头部 FOV 优化约束，站位仍执行真实相机可见性筛选。

先将以下命令中的 GPU 0 改为当前选中的设备，并给每次运行使用新名称：

```bash
# 独立整机绕障及移动参考系对齐
.venv/bin/python tests/smoke/panda_omron_flow.py --stage navigation \
  --gpu 0 --output outputs/diagnostics/panda-omron/navigation-new
.venv/bin/python tests/smoke/panda_omron_flow.py --stage align-mobile \
  --gpu 0 --output outputs/diagnostics/panda-omron/align-mobile-new

# 原始 val103 Cup 小规模流程输入：真实采样，不做障碍物增强
.venv/bin/python tests/smoke/panda_omron_flow.py --stage prepare \
  --gpu 0 --max-stations 3 --output outputs/diagnostics/panda-omron/scene-new

# 完整标定；不传 --max-trials，避免标定不完整
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/diagnostics/panda-omron/scene-new \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json --gpu 0 \
  --run-id calibration-new --output-dir outputs/diagnostics/panda-omron --record-physics

# 复用相同执行配置的完整标定，真实导航到站并操作
.venv/bin/lastmile-dataflow collect-lastmile \
  --dataset outputs/diagnostics/panda-omron/scene-new \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json --gpu 0 \
  --calibration outputs/diagnostics/panda-omron/calibration-new \
  --run-id collect-new --output-dir outputs/diagnostics/panda-omron --record-video --record-physics

# 物化无需 GPU；该命令只读取已有成功轨迹
.venv/bin/lastmile-dataflow collect-lastmile \
  --materialize-from outputs/diagnostics/panda-omron/collect-new \
  --run-id materialized-new --output-dir outputs/diagnostics/panda-omron
```

需要原有 Case 场景增强时，使用 [Panda 场景配方](../../../configs/scene_datasets/panda_omron_val103.json)：

```bash
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/panda_omron_val103.json --gpu 0 \
  --run-id panda-scenes-new --output-dir outputs/diagnostics/panda-omron
```

`build-scenes` 的 accepted 场景库同样可送入上述 evaluate/collect 命令；构建可能因物理或 Case 条件拒绝，拒绝不代表获得可采集场景。

查看标定 `trials.jsonl/summary.json`、采集 `pair_index.jsonl/train_index.jsonl/summary.json`；没有合格配对或没有路径时保留负结果。默认每站两次仅用于小规模接入验证，不能据此推断跨场景成功率。

`local_base_radius_m` 限制局部优化坐标的平移范围，A* 负责站间路径。默认闭爪后固定底盘抬升，lift 全程保持初始值；`operation_base_mode=fixed` 可用于 Panda 的固定底盘对照，但切换模式后须重新标定。

物化后的 Panda 样本使用 `state` 与 `actions`，字段、单位和绝对目标语义见每条轨迹的 `recording.json` / `metadata.json` 中的 `layout`：

```python
from lastmile_dataflow.data.export.lastmile_dataset import load_training_batch
batch = load_training_batch("outputs/diagnostics/panda-omron/materialized-new", [0], steps=16)
print(batch["state"].shape, batch["actions"].shape)
```

这是本次执行记录布局；训练可显式选择字段，不将记录向量长度视为物理自由度或统一策略动作维数。Panda 与 RBY-1 保留各自数据布局，不能作为同一布局直接混合成 batch。场景配方的 `max_pick_object_size_m` 是任务发现粗筛上界，实际开口仍为 `gripper_width_m=0.08`，实际可抓取性由规划与物理结果决定。实际阶段结果见[全流程实施报告](../delivery/phase_02/2026-10-17-panda-omron-full-flow.md)。
