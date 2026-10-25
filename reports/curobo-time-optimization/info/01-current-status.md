# PandaOmron 构建与 cuRobo 标定当前状况

- 状态：active
- 日期：2026-10-25
- 替代：本文件此前的纯方案状态
- 关联：[需求](../requirement/02-panda-omron-time-optimization.md)、[主方案](../plan/01-panda-omron-time-optimization.md)、[执行计划](../plan/03-execution-schedule.md)、[实施记录](../delivery/2026-10-24-implementation.md)

本 worktree 已完成双池等本轮软件实现和回归，P12 首阶段已完成，P13 估时已记录，P14 百来源正式批次及视频交付已完成。首阶段证据限定于 val103/Cup_30/Case2。原有修改和历史运行结果保留。

## 固定范围

| 阶段 | 输入与口径 |
|---|---|
| P12 首阶段正式全量 | position-v1、val103 / Cup_30 / Case2、1 变体；全部最终合法站位，最多 20 点，各 3 次 |
| P13 估时 | 首阶段实测分项＋固定 100 来源和任务清单；正常/保守时间，Case1/Case3 未测成本列假设 |
| P14 最终正式全量 | val0–val99 共 100 来源，Case1–3；每房最多 1 个 pick/1 个 open，每任务/Case 1 变体；实际 accepted 场景全部最终点各 3 次 |

先完成全部加速及 P11 回归，再开展首阶段正式全量。首阶段结论限于该任务；不新增抽点预实验或逐项 A/B。最终批次运行前估时，构建完成后按实际 accepted 数量更新标定预计时间。无任务、rejected、中断保留真实原因，不按成败更换来源。

每点三次从同一真实静置后的操作初态新建独立仿真；seed 为 `42 + station_index * 3 + trial`。低率含 0/3、1/3，高率仅 3/3，2/3 为中间组。只有完整标定可正式配对。300 s 活跃规划/执行预算已由首次 batch 接续；准备、排队、落盘单列，总墙钟保留。

## 实施与验收结果

| 工作包 | 当前状态 |
|---|---|
| P00 配置 | 首阶段与最终 100 来源配方、Panda execution/lastmile/benchmark 阈值、资源和状态结构已落地；资源 CLI 已接入 |
| P01 环境 | 独立 .venv 为 MuJoCo 3.5.0、cuRobo 0.8.0、PyTorch 2.7.1+cu128；沙箱外 CUDA/8 卡可见；全部输入路径检查通过。P12 已创建实际 solver |
| P02 状态/记录 | 完整积分状态、控制目标、时钟、相机和模型/准备引用；规划段补坐标与控制 dt。累计预算和跨进程计划归属已接通 |
| P03 局部模型 | 按完整对象/结构子树裁剪，保留目标/支撑和相关几何；发现数据传入资格检查，同模型复用候选前缀；全物理资格后选点与可见性记录。fixture 及三类 Case 正式构建通过 |
| P04 构建调度 | 房屋发现、任务/Case/变体 spawn，父进程统一写 scenes.jsonl；沿用组合编译缓存，临时目录唯一。首阶段真实构建通过，百来源构建完成（183 accepted/37 rejected） |
| P05 公共准备 | 站位摆位/真实静置一次，三次独立状态；准备失败单列，fixture 通过。已供双池共同使用 |
| P06–P08 规划/执行 | 常驻 GPU 与独立 CPU 池、原生 batch、静态资源、实测状态后续请求、活跃预算和全局调度已接通。fixture 软件检查通过，首阶段与百来源双 GPU 实测完成；最终 3,868 查询、1,047 次实测后续规划，Case3 无物理执行 |
| P09 回放 | evaluate-stations 内已有按 run/rollout 选择的离线实测状态回放；无积分/重新规划，fixture 验证状态、时间及相机。首阶段及最终 6 段成功/失败视频已生成并通过解码/时序检查 |
| P10–P11 集成 | Panda 单独标定默认关闭在线图像；内置标定、资源参数与观测完整性已接入。P11 初始 272 通过/2 跳过；实际 XML 修复后的最终全套 276 项：274 通过、2 条件跳过 |
| P12–P14 实验 | P12 全部 20×3 完成，20 成功/38 执行前无解/2 执行后无解，无基础设施错误；3.76 min 达到该任务 30 min 目标。P13 估时完成，P14 完成：183 accepted/37 rejected，计划 8,466、记录 8,463，820 成功/284 执行失败/7,359 执行前无解，另 1 站准备失败；标定 2.88 h，6 视频通过 |

完整实测状态回放不能替代任务判定，也不自动构成完整视觉训练数据。旧 E01 的像素末位差异仍是历史边界，本轮验收运动/物体状态、相机和时序。

## 路径与入口

- 首阶段配方：`configs/scene_datasets/panda_omron_val103.json`。
- 最终配方：`configs/scene_datasets/panda_omron_val100.json`，没有复制 Cup_30 限制。
- 最终全 Case 执行配置：`configs/execution/panda_omron_all_cases_curobo.json`。
- 首阶段执行/采集配置：`configs/execution/panda_omron_curobo.json`、`configs/lastmile_datasets/panda_omron_curobo.json`。
- 机器人：`configs/robots/panda_omron_mobile.json`。RoboCasa 配置保留，非本轮第二条主实验。
- 本机数据根：`/data1/wenyifan/molmospaces_data`；EGL 库：`/data1/wenyifan/opt/lib`。
- 新 run/attempt 不覆盖既有结果；本轮使用 GPU 6/7，避免其他训练卡。

历史耗时和局部实验见 [archive](../archive/2026-10-24/)。旧整屋、两次协议或 RBY/FOV 不同配置均不是新局部三次协议的匹配基线，不能计算优化倍率。当前实施过程、局部检查和剩余项统一记在[实施报告](../delivery/2026-10-24-implementation.md)。

首阶段实测与复现命令见 [首阶段交付](../delivery/2026-10-24-stage1-experiment.md)，百来源估时与命令见 [最终批次交付](../delivery/2026-10-24-val100-experiment.md)。

最终边界：完整标定 182/183 场景，准备失败场景不配对。Case3 全部执行前规划无解，没有开门执行或物理续规划证据；数值标定不自动构成视觉训练数据。当前计划无待运行批次，详细覆盖与估时偏差见最终交付。
