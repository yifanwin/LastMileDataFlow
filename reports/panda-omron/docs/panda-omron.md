# PandaOmron 首期运行指南

状态：active  
日期：2026-10-17  
替代：无  
关联：[需求](../requirement/01-panda-omron.md)、[实施方案](../plan/01-panda-omron-phase01.md)、[实施结果](../delivery/phase_01/2026-10-17-panda-omron-implementation.md)

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

查看 `result.json` 的 `task_outcome`，视频及逐物理步事实保存在同一运行目录。成功要求双指承力、无支撑、抬升 ≥5 cm 并连续保持 ≥2 s。首期未接入 station sampling、S0/S1、A*、生产 recorder/dataset 或 MolmoBot。

## 5. 软件回归

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/panda-omron-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -q
```

该命令不自动执行真实 smoke；已有一个 OpenGL 渲染测试需要单独显式启用。
