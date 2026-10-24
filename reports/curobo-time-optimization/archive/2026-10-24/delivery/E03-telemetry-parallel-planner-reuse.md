# E03：遥测精简、站位并行与 planner 复用

- 状态：archived
- 日期：2026-10-23
- 替代：[当前文档](../../../info/01-current-status.md)
- 关联：[优化方案](../plan/2026-10-22-time-optimization-plan.md)、[E02](E02-png-write-probe.md)、[运行手册](../../../../../docs/运行手册.md)

**2026-10-24 归档：保留当时的设置、结论与未验证边界；当前实验以 PandaOmron、每站位 3 次为准。本文中的旧预算与待办不作为当前方案。**

**三项软件实现已完成：249 项回归中 245 通过、4 跳过。站位并行和 planner 复用尚无真实 GPU rollout 与整场耗时结果，不能宣称已达到 30 分钟。** 原有 PNG 压缩改动和用户工作区文件均保留；未提交或合并。

## 实际改动

| 项目 | 实现 | 保留的实验语义 |
|---|---|---|
| 遥测精简 | `ContactSampler` 缓存接触名称/分类，每步读取一次接触力，共用给 `PickMonitor` 与统计；不写 physics trace 时不构造完整日志行、不调用 canonical 序列化 | 4 ms monitor、健康检查、额外 forward、接触计数单位和 32 条示例均保留；开启 trace 时保留原字段 |
| 站位并行 | `--station-workers`，spawn 进程常驻当前场景；一任务为原站位的全部 trial；父进程唯一写 `trials.jsonl` | 原站位索引和 seed；每 trial 独立仿真、attempt；父进程预分配编号，预算仍为每场景总量 |
| planner 复用 | 每站位/手臂缓存一个闭爪前 planner，复用机器人描述、碰撞世界、solver 和 warmup；仅接受相同模型及完全相同积分初态 | 每 trial 重新选候选、重新规划；更新 sim、PlanningModel.sim、FOV scratch、目标、阶段状态和日志；闭爪后新建 |

资源参数记录在 run 的 `config.json.resources`，不混入物理配置匹配。新增 `station_index` 明确标注原站位索引。并行原始行按结果返回顺序写入，不创建分片 trial 文件或第二套汇总入口。

复用只发生在 `sim.started == False`。导航、静置或连续重试已经推进物理后，继续构造独立 planner。站位结束销毁缓存，并打断原生回调与 owner 的引用环，让 solver 资源及时释放。

## 原生 seed 处理

核对固定 cuRobo v0.8.0 源码后确认：`MotionPlannerCfg.create(random_seed=...)` 将 trial seed 传给 IK 和 TrajOpt 的 `SolverCore.seed_manager`；其他 rollout、优化器、PRM 采样器使用各自固定配置种子。

仅调用 `motion.reset_seed()` 会回到旧 trial 的种子，因此复用时先按新 seed 重建两个原生 SeedManager，再调用原生 reset 重置其余随机流和 PRM 缓冲。原生求解入口会重新初始化优化状态；没有复用上一 trial 的计划轨迹。

CPU 原生组件探针使用实际 v0.8.0 `SeedManager`，11 维，horizon 1/32，seed 42–46。每个 seed、每个管理器抽取 64 个样本，与该 seed 的全新管理器完全一致；相邻 seed 的样本不同。两管理器重建及 reset 的中位时间约 **3.13 ms**。这只验证采样组件，不验证完整 GPU solver、碰撞与 FOV 链。

复用 trial 用 `planner_reuse.json` 引用同一 run 中首次构造的静态资源；当前 trial 的 solver、规划和 FOV 日志写入当前 attempt。

## 验证过程与结果

1. 阅读现有实现和测试、保留 E02 修改，核对原生 v0.8.0 API。
2. 实施三项改动，并更新原测试中已变化的遥测接口；不恢复旧接口壳。
3. 新增共享接触力读取、完整任务事实、接触统计与关闭 trace 不序列化检查；使用简化 MuJoCo 物理接触验证。
4. 用真实 spawn 进程配合简化场景与合成执行结果，验证跨场景唯一编号、原始 seed、部分预算、共享故障停批、worker 非零退出不补造 trial，以及 worker 报错后继续排空其他已执行 trial 的真实返回记录再报错。
5. 检查相同冻结初态才复用、闭爪后新建、模型/FOV/日志重绑；另执行上述原生 CPU 种子探针。
6. 全套软件回归 **249 项：245 通过、4 跳过**，退出码 0；跳过可选数据导出、两项策略预处理及需显式启用的 OpenGL 检查。CLI 两入口 `--help` 与 `git diff --check` 通过。

另用简化 MuJoCo 场景实际运行遥测对比脚本：两种实现各预热一次、交错三轮，每轮 10 个控制 tick / 125 个物理样本。优化前后最终积分状态、全部任务事实和接触统计完全一致；中位时间为 63.85 ms → 42.26 ms，约快 1.51 倍。该模型的物理成本很低，这个倍率不用于外推真实 RBY 或整场耗时。结果保存在 `outputs/diagnostics/curobo-time/E03-telemetry-synthetic-02/result.json`。

回归命令：

```bash
LD_LIBRARY_PATH=/data1/wenyifan/opt/lib \
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=0 \
__EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json \
MPLCONFIGDIR=/tmp/lastmile-curobo-mpl \
.venv/bin/python -m unittest discover -s tests -t . -v
```

本轮日志：`/tmp/lastmile-curobo-E03-unittest-final.log`。原生种子结果：`outputs/diagnostics/curobo-time/E03-native-seed/result.json`。独立环境安装了 cuRobo 0.8.0 Python 包及 CPU 采样依赖，PyTorch 仍为 CPU 版本，不是 GPU 规划环境。

## 运行与边界

参数及完整命令见运行手册。默认单 worker、启用同站位 planner 复用；先用 `--station-workers 2 --gpu <gpu-a> <gpu-b>` 做真实小规模验证，再按实际争用决定扩大。EGL 编号不同则显式加 `--egl-devices <egl-a> <egl-b>`。对照实验加 `--no-planner-reuse`。`collect-lastmile` 的内置标定同样支持并行，连续采集阶段仍串行。

共享故障设置停批事件，不再新增 trial 或派发新站位；已进入执行的独立 trial 自然收尾并保留真实结果。worker 初始化/退出错误使命令报错、run 汇总不完整，不补造物理失败记录。
已知的 CUDA 不可用、cuRobo 依赖缺失或版本不匹配在规划边界归类为共享基础设施故障，避免批次反复消费失效的规划环境。

本轮准备了 `scripts/experiments/curobo_telemetry_probe.py`，可与指定 Git revision 的遥测实现在独立真实快照 stepping 上比较状态、任务事实、统计和时间，不生成任务成败标签：

```bash
.venv/bin/python scripts/experiments/curobo_telemetry_probe.py \
  --scene <frozen-scene-directory> --target <target-body> \
  --baseline-ref <revision-before-E03> --ticks 10 --repeats 3 \
  --output outputs/diagnostics/curobo-time/E03-telemetry-new
```

准备执行时发现旧主仓库 `outputs/` 已不存在，用户确认已删除。因此没有执行该真实场景对比，也没有复制或恢复已删除数据。真实 GPU planner 跨 trial 复用、1/2 worker 资源争用、完整 20×5 标定及全部成功图像就绪时间，均未验证。旧 E02 的绝对耗时外推不能作为三项新改动的真实收益；30 分钟仍是待验证目标。
