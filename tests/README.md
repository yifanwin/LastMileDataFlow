# 测试

**默认只运行离线软件回归。** 简化 MuJoCo stepping、mock 结果和合成观测不能证明真实 RBY-1、CUDA 规划或策略任务成功。

## 离线回归

从仓库根目录运行，使用已安装本项目的 `.venv`：

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v

# 只跑一个领域或文件
.venv/bin/python -m unittest discover -s tests/scenes -t . -v
.venv/bin/python -m unittest tests.data.test_recording -v
```

| 子目录 | 覆盖内容 |
|---|---|
| `scenes/` | 场景/资产加载、任务发现、支撑图、增强规则、站位采样与选择 |
| `runtime/` | 动作与限位、仿真生命周期、构建事务、native 控制、任务判定与失败隔离 |
| `planning/` | cuRobo 模型与轨迹适配、A*、FOV 及梯度 |
| `policies/` | multitask 输入、22D state、pick/open 夹爪差异与动作缓冲 |
| `data/` | 场景/轨迹读写、标定分母、物化去重与搬迁 batch 读取、导出与图像逻辑 |
| `workflows/` | CLI、配置路径、标定复用、连续采集、停批和 benchmark 汇总 |
| `smoke/` | 手动运行的真实资产/权重/GPU 检查，不随单测执行 |

`helpers.py` 只放共享简化模型、场景 fixture 和合成物理 trace；不放真实资产或运行产物。
单测按当前模块职责组织，不为旧内部路径、已删除功能或历史交付状态保留测试。

`workflows/test_station_parallel.py` 会启动真实 spawn 进程，使用简化场景与合成执行结果检查 seed、编号、预算和停批；它不验证 GPU 并行性能。`planning/test_planner_reuse.py` 检查资源复用边界、新 seed 管理器及仿真/FOV/日志重绑，不替代真实 GPU planner 验证。

## 可选真实渲染

增强事务中的分割渲染测试默认跳过。准备好 EGL 后，可选一张空闲卡显式运行：

```bash
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=<chosen-index> CASE_EDIT_RENDER_TESTS=1 \
  .venv/bin/python -m unittest \
  tests.runtime.test_construction.TransactionTests.test_observer_search_records_actual_segmentation -v
```

它使用简化模型做真实渲染，不是完整机器人任务验收。

## 显式真实验证

导航修复的局部软件检查：

```bash
MUJOCO_GL=disable MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest tests.planning.test_geometry_navigation \
  tests.data.test_recovery tests.runtime.test_torso tests.workflows.test_collection \
  tests.workflows.test_mode_downstream -v
```

这些测试覆盖宽窄路线的软代价择优、低净空通行、旋转与严重碰撞、预算内保留可行路线、显式 yaw 和恢复区域重载，使用简化模型，不代表真实任务成功。原硬门槛版本见[几何修复交付](../reports/navigation/delivery/07-navigation-geometry-fix.md)，当前软目标版本见[软净空交付](../reports/navigation/delivery/08-navigation-soft-clearance.md)。

先按 [setup](../docs/setup.md) 准备对应输入。每个脚本都有 `--help`，输出必须使用新目录；GPU 从 0–7 中按实际利用率和显存选择。

- `smoke/curobo_rollout.py`：指定场景和站位，执行一次真实规划/rollout。
- `smoke/head_fov.py`：真实机器人模型上的 CUDA FOV 梯度与管理器隔离检查，不执行任务。
- `smoke/multitask.py`：本地真实权重加载、pick/open 动作与 service/client；观测是合成的，不执行机器人。
- `smoke/pipeline.py`：单场景逐点标定与连续采集；只有显式提供来源 cuRobo 标定才附加 benchmark。没有手工恢复配对、自动降级 smoke 或搬迁探针。

```bash
.venv/bin/python tests/smoke/pipeline.py \
  --dataset <scene-dataset> --scene-id <accepted-scene-id> --assets-dir data/assets \
  --output outputs/diagnostics/pipeline-small --gpu <chosen-index>
```

pipeline 默认每站位两次试验，仍会遍历所选场景的全部站位；应先准备少量站位的输入。任务失败或没有合格配对都是有效负结果。
若提供 `--benchmark-calibration`，benchmark 使用整个输入数据集及该来源标定；小规模检查应传单场景数据集。
详细任务结果读取 run 主记录，不能用脚本退出码或目录存在代替成功判定。

## PandaOmron

`smoke/panda_omron.py` 保留 factory、控制、固定 pick 与对齐；`smoke/panda_omron_flow.py` 提供真实站位输入准备、整机绕障导航和移动参考系对齐。标定、连续采集与物化直接调用现有生产 CLI。命令与数据说明见 [Panda 运行指南](../reports/panda-omron/docs/panda-omron.md)。这些实际 GPU smoke 不随 unittest 自动执行。

`smoke/pi05.py` 提供真权重 model、实际 observation/control/tracking 和固定场景 rollout。合成观测推理与实际策略执行分别标记，命令与已验证边界见 [π0.5 指南](../reports/pi05/docs/01-running.md)。可选 LeRobot 转换测试需要 `pi05-data` extra。

## Panda 双池软件检查

```bash
LD_LIBRARY_PATH=/data1/wenyifan/opt/lib MUJOCO_GL=egl \
  .venv/bin/python -m unittest tests.planning.test_batch tests.planning.test_pool \
  tests.planning.test_resources tests.runtime.test_active_budget \
  tests.workflows.test_split_calibration tests.workflows.test_split_commands -v
```

这些检查使用原生 CPU 随机采样、实际 spawn 和简化 MuJoCo；覆盖三次映射、跨场景独立执行、实测后续请求、活跃预算、共享停批与唯一记录，不代表真实 CUDA batch 或 Panda rollout 通过。正式输入按执行计划在软件回归之后运行。
