> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# LastMileBench v0：最小实现与运行报告

第一版只接入 **MolmoBot VLA**，从封存的 `scene_datasets` 原站位连续执行移动操作，保存逐 episode 结果并统计 Task Success Rate。动作统一使用 MolmoBot 原生 20D 协议。**不重新生成场景，不估计难度，不运行复杂路径验证器。**

本次实际运行是两条 **未标定 smoke episode**，不是已经证明“当前位置困难、附近可恢复”的正式测试集。指定 demo 的 manifest 原本为 `task_execution: not_performed`，尚无与当前原生控制配置匹配的 cuRobo 标定。正式 episode 筛选器已支持导入成功率站位图，不把其他场景或控制配置的旧热力图拿来充数。

## 1. 当前交付范围

工作区：`LastMileDataFlow/.worktrees/lastmilebench-design`，分支 `feat/lastmilebench-design`，基础提交 `8dfc84200cebdf31778687d67a3831c1ca571884`。未提交、未合并。

输入：`../procedural-obstacle-augmentation/outputs/scene_datasets/scene-val103-cup30-minimal-v3-20261012-r2`。

| 输入样本 | 合法站位数 | selected 数 | 本次起点 | 初始化目标距离 | 原生 head 可见像素 |
|---|---:|---:|---|---:|---:|
| `val_103-case1-f2871d23d0f128da` | 33 | 20 | S0048 | 0.400 m | 264 |
| `val_103-case2-0812d0f8b44be59d` | 60 | 20 | S0048 | 0.400 m | 264 |

两条都是同一 house `val_103`、同一目标 `Cup_30` 的 Case1/Case2 变体，任务是 Pick。各运行一次，不代表跨 house 泛化或 Open 验收。本次目标是验证真实模型、真实动作、物理判定、结果汇总整条链路，不预设任务必须成功。

保留单份 Markdown 报告。两条结果用表格比热力图更准确，因此不生成附加图、HTML 或多份说明文件。

## 2. 最简单的正式 episode 筛选

按最新要求，同一个冻结样本的合法采样点中：

1. cuRobo 实测存在低成功率点：默认 `successes / trials ≤ 0.2`；
2. 其附近存在高成功率点：默认 `successes / trials ≥ 0.8`；
3. 两点 XY 直线距离默认不超过 **1.0 m**；
4. S0 的基本初始化、地面、碰撞、可见性检查通过且位于局部工作范围。

满足即可选低率点为 S0。**不加底盘路径验证，不计算 D1/D2/D3，不强制模型移动到 S+，也不强制发生底盘移动。** 阈值目前是首版约定，不是经统计优化的结论；重复次数和成功计数必须保存，禁止用空试验数或失败的基础设施运行充当低成功率。

S+ 只作为构造证据。这里的“附近有解”是不同原站位的实测对比，并不额外证明两点之间存在可执行通路。Case1/2/3 仍指场景构造模式。

筛选器位于 benchmark/episodes.py。正式构建必须提供绑定正确的 cuRobo 数据；未提供时默认拒绝，只有显式 `--allow-uncalibrated-smoke` 才可构建 smoke。没有低/高邻近组合时拒绝封存，不偷偷退回任意起点。

### cuRobo 热力图输入

`--calibration-dir` 下每个样本一个 `<sample_id>.json`，格式如下；绑定值来自对应原生场景派生包的 `samples/<sample_id>/binding.json`：

```json
{
  "schema_version": "lastmilebench-station-rates-v1",
  "backend": "curobo",
  "scene_dataset_id": "<dataset-id>",
  "sample_id": "<sample-id>",
  "source_package_sha256": "<源 package checksums.json SHA256>",
  "robot_sensor_config_id": "<原生配置 rs-id>",
  "compiled_model_sha256": "<原生 compiled model SHA256>",
  "task_id": "<task-id>",
  "stations": [
    {"station_id": "S0001", "trials": 5, "successes": 0},
    {"station_id": "S0002", "trials": 5, "successes": 5}
  ]
}
```

示例计数不是本次实测。可追加失败原因、attempt 引用等字段；若提供 `success_rate`，必须与计数一致。实现校验身份、合法站位、重复站位、计数以及邻近距离。v0 **只接收实测率表，不在此次交付中新增或运行 cuRobo 固定底盘实验**；不能把读取热力图说成已经生成热力图。

## 3. 冻结场景不变，机器人控制配置明确派生

源 demo 是 `position-v1` 位置夹爪、20 Hz、320×240；MolmoBot 原生协议是夹爪电机控制、10 Hz、1024×576。这两套数据不能假称同一个 `robot_sensor_config_id`。

runtime/benchmark_scene.py 从已编译的冻结 MJB 复制模型，只恢复两侧机器人夹爪的原生 motor 参数、设置机器人相机 FOV 和离屏渲染缓冲。机器人 XML 只读，不重新编译或构造场景几何，不添加障碍，不重新静置物体。原冻结 qpos/qvel 保留，原场景模型及文件不改写。派生模型单独封存，生成新 rs-id，并记录父模型摘要及源 package 摘要。

执行起点使用已保存站位的完整 `initial`，不重新采样、不自动优化朝向。初始化检查独立创建 Simulation；实际 episode 再创建一个新 Simulation。`begin()` 后不 restore、不传送、不重启。所有策略以后都应使用该封存 episode 的同一初始化和预算。

## 4. 原生动作与模型输入

| 数据项 | v0 契约 |
|---|---|
| 模型动作 | chunk `16×20`；每次提交一个 20D 动作 |
| 20D 布局 | base `[0:3]`，左臂 `[3:10]`，左夹爪 `[10]`，右臂 `[11:18]`，右夹爪 `[18]`，torso `[19]` |
| 动作语义 | base/双臂增量；夹爪绝对 motor command `[-100,100]`；torso 为绝对耦合关节量 |
| 模型状态 | 实测 22D，不用目标状态替代实测状态 |
| 模型图像 | 右腕、head、左腕 RGB，1024×576；沿用上游单次 fisheye 预处理 |
| 时间 | policy 100 ms、控制 tick 20 ms、物理步长 4 ms；一个相对动作只应用一次 |
| Pick 指令 | `Pick up the cup.`，不提供 S+、率表、路径或目标真值位姿 |
| 数据证据 | 原始预测、后处理动作、实际 submitted action、ctrl、真实状态、物理事实、RGB/视频 |

复用 [MolmoBot service](../../../src/lastmile_dataflow/policies/molmobot_service.py)、连续执行、原生机器人适配器 和现有 raw-first recorder。没有 mock 策略或第二个 baseline。夹爪 motor 数值不是米，不能把旧 position 动作直接当原生训练动作。

如需 MolmoBot 数据包，沿用已有 `export-molmobot --attempt <实际 attempt> --output <新目录>`；它先审计 canonical，再导出状态 22D、动作 20D 和图像，保留原始记录不改写。

## 5. 成功率怎么算

沿用项目的 `mobile-vla-physical-v2` oracle。Pick 必须有真实双指接触力、抬升至少 5 cm、保持 2 s、无环境支撑并满足滑移约束；任一手完成即成功。**不是夹爪靠近目标或执行完预算就算成功。** 这是本项目物理判定，不声称与 MolmoSpaces leaderboard 默认判定完全等价。

预算在 benchmark manifest 中固定：最多 **400 次 policy 动作（40 s 仿真时间）**、每 episode 最多 **600 s 墙钟时间**、目标周围 **1.5 m** 局部工作范围。第三人称分析视频关闭，保留三个真实输入相机的视频和 raw-first 数据。

`summary.json`：

- `task_success_rate = successes / scheduled`，仅在整个计划集合都有有效任务结论时给出；
- 正常失败与 episode 超时算失败；
- simulation failure、recording error、infra、未执行或中断为 unknown，不伪造任务失败；有这些情况时完整 TSR 为 `null`；
- 同时输出 `observed_task_success_rate`、coverage、状态计数和 unknown 数，避免仅统计完成子集造成虚高；
- 重复或外来 episode 结果拒绝进入统计。正常完成但零次成功也属于评测链路跑通。

模型资源异常停止共享模型运行，保留已完成结果和失败证据。每次 build/evaluation 使用新的输出目录，禁止覆盖旧结果。未实现细分“方向错误”等启发式归因，第一版以真实 status/reason 为准。

## 6. 复现命令与产物

从本工作区根目录执行，使用本机已有 MuJoCo **3.5.0** 环境；源快照不能用其他 MuJoCo 版本强行读取。

```bash
export PYTHONPATH=src MUJOCO_GL=egl
PY=../no-edit-lastmile-val/.venv-curobo-v080/bin/python

# 两条未标定 smoke；正式构建应改为 --calibration-dir <真实率表目录>
$PY -m lastmile_dataflow.cli build-lastmilebench \
  --dataset ../procedural-obstacle-augmentation/outputs/scene_datasets/scene-val103-cup30-minimal-v3-20261012-r2 \
  --output outputs/benchmark/lastmilebench-val103-v0-smoke-r2 \
  --allow-uncalibrated-smoke --gpu-ids 0 1 2 3 4 5 6 7

$PY -m lastmile_dataflow.cli evaluate-lastmilebench \
  --benchmark outputs/benchmark/lastmilebench-val103-v0-smoke-r2 \
  --output outputs/benchmark/molmobot-val103-v0-r2 \
  --gpu-ids 0 1 2 3 4 5 6 7
```

重跑时更换输出目录名。GPU 在 0–7 中连续采样三次（间隔 5 s），要求利用率不超过 20%、空闲显存至少 24 GiB，然后优先低利用率、高空闲显存。CUDA 与 EGL 显式绑定同一物理 GPU，不干预其他进程。策略配置沿用 `configs/policies/molmobot.json`，真实 checkpoint 为 `/nas/wenyifan/models/MolmoBot-RBY1Multitask`。

构建产物：`manifest.json`、`episodes.json`、`checksums.json`、原生派生场景、task/robot/binding、标定证据。评测产物：`run.json`（权重及源码身份/GPU）、`model_service.json`、追加式 `results.jsonl`、`summary.json`、每 episode 的 attempt/原始动作/物理记录/真实视频。模型客户端只收到 RGB、实测状态、任务指令，不把标定文件传给模型；v0 为本地 trusted runner，不实现生产级文件系统访问隔离。

所有运行日志位于 `outputs/logs/`。实现编排见 [workflows/lastmilebench.py](../../../src/lastmile_dataflow/workflows/lastmilebench.py)，命令入口见 [cli.py](../../../src/lastmile_dataflow/cli.py)。参考 MolmoSpaces 的“固定 episode + 策略评测”组织方式，但本次沿用 DataFlow 原生快照执行，**未实现 MolmoSpaces EpisodeSpec 导入或 eval_main 接口**。

## 7. 验证与实际结果

相关回归 **43/43 通过**，包括新 benchmark 选择/计数/原生派生、冻结物体速度保持，以及现有 MolmoBot、scene_datasets、站位迁移测试。全套 **399 项：387 通过、10 跳过、2 失败**。两个失败分别为已有 recorder 异常分类和站位 preset 差异断言，均已在 `git archive HEAD` 的未修改副本中复现，没有为本任务修改这些无关模块。

发现并修复站位初始化将物体速度清零的问题：`place_frozen_station` 现在只重置机器人速度，保留全部非机器人 qvel。源 demo 中非机器人速度最大值分别为 0.1652、0.1347（混合线/角速度分量，不当作统一 m/s 指标），因此该修复不是纯理论问题。新增回归覆盖非零物体速度和 begin 后禁止站位重置。

r1 在模型加载阶段主动中断，没有实际执行 episode；`summary.json` 为 0/2 coverage、TSR=null，不用于交付数字。修复后重新构建并启动 r2，保留 r1 原记录。真实 r2 已完成，结果如下。

验证日志：`outputs/logs/lastmilebench-v0-tests.log`、`lastmilebench-v0-full-regression-r2.log`、`lastmilebench-v0-base-recheck.log`。

只读真实场景审计：两份模型各比较 **454 个数组**，仅 `actuator_biastype`、`actuator_ctrlrange`、`actuator_gainprm`、`cam_fovy` 发生允许的机器人变化；派生初态及站位初始化后的非机器人 qpos/qvel 均逐值一致。证据：`outputs/benchmark/lastmilebench-v0-frozen-state-audit-r2.json`。

### 真实 MolmoBot 结果

**Task Success Rate：0/2（0%）；coverage：100%；infra/unknown：0。** 两条 episode 都实际执行了移动操作，但均因统一的 600 s 墙钟预算结束而超时。退出码 0 表示评测结果完整，不表示任务成功。

| Case | 状态 | 实际 policy 动作数 | 仿真执行时间 | 墙钟时间 | 底盘最终位移 | 底盘路径长度 |
|---|---|---:|---:|---:|---:|---:|
| Case1 | timeout | 392 | 39.2 s | 601.2 s | 0.240 m | 1.153 m |
| Case2 | timeout | 325 | 32.5 s | 600.1 s | 0.200 m | 1.007 m |

路径长度来自真实 policy 边界的 XY 采样，不是规划长度。超时未计为基础设施异常。这里的 0% 是这两条 smoke 在当前统一预算下的结果：没有完整走到 400 步上限，不能据此断言模型在更长预算下也必然失败，更不能作泛化性能结论。后续如需跑完 40 s 仿真，可在**新建 benchmark 时**增加 `--wall-timeout-s`，对所有 episode 使用相同新预算，不能改写本次结果。

实际 GPU：**CUDA 6 / NVIDIA RTX 5880 Ada Generation**，选择时三次样本均为利用率 0%、空闲 49105 MiB。模型实际加载 185.8 s（不占 episode 墙钟预算），峰值已分配约 9.43 GiB。checkpoint SHA256：`009fa4dc60493da7b4aad9ffa58481fdd87298c2c11d95f6d472b67b56789a3d`。

交付产物：

- 冻结两条 smoke episodes
- 完整成功率汇总
- 逐 episode 结果
- 权重及运行身份
- 场景状态只读审计

剩余唯一构造缺口：提供或另行运行与原生派生配置匹配的 cuRobo 站位成功率图，才能把 smoke 起点替换成符合低率/附近高率规则的正式 S0。v0 不要求再补复杂路径验证或难度分级。此次交付证明执行/计数链路闭合，不证明 Last-mile 恢复能力。

两条实际 attempt 的 canonical 审计均通过（分别 392/325 个实际动作、393/326 帧，无 issues），证据为评测目录下 `canonical-audit-case1.json`、`canonical-audit-case2.json`。

另外实际导出并校验了 Case1 MolmoBot 原生数据包：**392×22 状态、392×20 原生动作、392×16×20 action chunks**，三个相机图像、有效性 mask 与文件校验完整。夹爪为 native motor，不使用位置到力的猜测映射。该包来自 timeout attempt，不能称为成功轨迹或直接混入 success-only 训练集；它是原生数据协议跑通证据。导出图像是另行可复现的派生预处理，不假称逐帧复原运行时所有随机增强。
