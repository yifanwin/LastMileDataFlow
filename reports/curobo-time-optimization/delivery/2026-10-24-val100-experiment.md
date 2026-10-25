# 百来源全 Case 正式实验交付

- 状态：done
- 日期：2026-10-25
- 替代：本文件此前的运行中记录
- 关联：../plan/03-execution-schedule.md、2026-10-24-stage1-experiment.md、2026-10-24-implementation.md

完成固定 val0–val99 的三类 Case 构建与正式标定。构建接受 183 场景、拒绝 37；标定计划 8,466 次，实际记录 8,463 次，820 成功、284 执行失败、7,359 执行前规划无解。一个站位准备失败，其对应 3 次未生成 trial，未补造失败。6 段实际轨迹离线视频已生成并解码通过。

## 复现命令

从本 worktree 根目录执行。下列是实际 run-id；复跑整组换新 run-id，并同步修改依赖输入路径。原 XML、资产与既有结果不覆盖。

```bash
export LD_LIBRARY_PATH=/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64:${LD_LIBRARY_PATH:-}
export CUDA_HOME=/data1/wenyifan/opt/cuda-12.8
export MUJOCO_GL=egl

.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/panda_omron_val100.json --allow-partial-targets \
  --output-dir outputs --run-id panda-time-val100-build-20261024-r01 \
  --build-workers 12 --gpu 6

# 等完整构建结束；本轮按实际站位更新估时后才启动标定
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-time-val100-build-20261024-r01 \
  --assets-dir /data1/wenyifan/molmospaces_data/assets \
  --config configs/execution/panda_omron_all_cases_curobo.json \
  --output-dir outputs --run-id panda-time-val100-calibration-20261024-r01 \
  --gpu 6 7 --cpu-workers 60 --preparation-workers 12 --recording-mode numerical

# 标定结束后，只渲染该轮实际执行
.venv/bin/lastmile-dataflow evaluate-stations \
  --replay-from outputs/panda-time-val100-calibration-20261024-r01 \
  --rollouts r000328 r000335 r000148 r000177 --replay-camera front_camera \
  --output-dir outputs --run-id panda-time-val100-replay-20261025-r01 --gpu 6

.venv/bin/lastmile-dataflow evaluate-stations \
  --replay-from outputs/panda-time-val100-calibration-20261024-r01 \
  --rollouts r000871 r001370 --replay-camera front_camera \
  --output-dir outputs --run-id panda-time-val100-replay-extra-20261025-r01 --gpu 6
```

日志均为 `outputs/logs/<run-id>.log`。主结果为构建 run 的 `scenes.jsonl`、标定 run 的 `trials.jsonl` / `summary.json`；完整配置保存在各 run 的 `config.json`。

## 构建与来源覆盖

主 profile 为 position-v1；每房最多选 1 个合格 pick 和 1 个合格 open，各 Case 1 变体，不限定 Cup_30。沿用资格、支撑面积与空间覆盖规则，没有按结果替换来源。静态发现预计 226 单元，完整资格后为 220 单元，再执行增强与最终站位验收。

| Case | 合格构建单元 | accepted | rejected | 最终站位 | 计划 trial |
|---|---:|---:|---:|---:|---:|
| Case1 | 66 | 43 | 23 | 510 | 1,530 |
| Case2 | 66 | 52 | 14 | 855 | 2,565 |
| Case3 | 88 | 88 | 0 | 1,457 | 4,371 |
| 合计 | 220 | 183 | 37 | 2,822 | 8,466 |

100/100 来源完成发现，90 个来源至少有一个 accepted 场景。无合格任务的来源为 val6、val8、val24、val28、val35、val48、val56、val69、val81、val99。Case1/2/3 接受来源覆盖分别为 43%/52%/88%，对各自合格构建单元的接受率为 43/66、52/66、88/88。

Case1 拒绝：16 站位不足、5 候选预算耗尽、1 物理验证预算耗尽、1 变体墙钟耗尽。Case2 拒绝：1 站位不足、12 候选预算耗尽、1 物理验证预算耗尽。每个 accepted 场景有 3–20 个最终点；满 20 点的场景分别为 10/32/60 个。183 个预览全部成功。

主记录保留 46,600 条候选站位检查。增强尝试已记录 Case1 14,004、Case2 33,853；真实放置验证已记录 62/59 次，Case3 不做增强。1 条变体墙钟耗尽记录缺少这两个计数，不补造完整总数。资格原因字符串 `fewer_than_minimum_baseline_visible_points` 在本轮指合法站位不足；资格实际不以可见像素筛选，可见性只在最终点记录。

## 标定结果与完整性

| Case | 计划 / 已记录 | 成功 | 执行失败 | 执行前规划无解 | 完整标定场景 |
|---|---:|---:|---:|---:|---:|
| Case1 | 1,530 / 1,530 | 250 | 116 | 1,164 | 43/43 |
| Case2 | 2,565 / 2,562 | 570 | 168 | 1,824 | 51/52 |
| Case3 | 4,371 / 4,371 | 0 | 0 | 4,371 | 88/88 |
| 合计 | 8,466 / 8,463 | 820 | 284 | 7,359 | 182/183 |

计划 trial 覆盖率 99.9646%。完整计划集合系统成功率为 820/8,466（9.69%）；按已记录结果为 820/8,463。实际物理执行 1,104 次，其中 820 成功；该子集成功率为 74.28%，不代表全部起点。规划无解的任务判定仍为 unknown；真实任务结果为 820 success、284 failure、7,359 unknown，没有把 unknown 改成任务失败。

`val88-pick-0063-Case2-v000` 的第 0 个站位 S0088 在真实静置后准备失败：`InitializationError:prepared_station_collision_or_floor`。准备原因单独保存在 `preparations/<scene>/S0088/preparation.json` 和 summary，未生成其 3 次 trial。该场景不参与正式困难配对；其余已执行结果保留。共享故障和 trial 基础设施错误均为 0。

| Case | 0/3 | 1/3 | 2/3 | 3/3 | 未取得三次结果 |
|---|---:|---:|---:|---:|---:|
| Case1 | 377 | 51 | 47 | 35 | 0 |
| Case2 | 585 | 76 | 85 | 108 | 1 |
| Case3 | 1,457 | 0 | 0 | 0 | 0 |

低率含 0/3、1/3，高率仅 3/3；按完整原始标定率筛得候选有向配对 1,540，未从平滑热图选点，未验证这些配对的路径或执行连续采集。逐点系统成功次数/已记录次数可直接从主记录复现，不另存第二套 trial：

```bash
.venv/bin/python - <<'PY'
import json
from collections import defaultdict
from pathlib import Path
points = defaultdict(list)
path = Path('outputs/panda-time-val100-calibration-20261024-r01/trials.jsonl')
for line in path.open():
    row = json.loads(line)
    points[row['scene_id'], row['station_id']].append(row['status'] == 'success')
for (scene, station), outcomes in sorted(points.items()):
    print(f'{scene} {station} {sum(outcomes)}/{len(outcomes)}')
PY
```

284 次执行失败分为：162 抓取保持/滑移、65 后续规划无解、37 底盘越界、20 跟踪超时。跟踪超时是控制阶段的失败原因，本轮没有耗尽 300 s 活跃预算的 timeout trial。

全部 8,463 个 rollout 与 trial 身份唯一，记录和回放资料完整；共 514,823 个实际执行边界、523,286 个含初态边界。逐条核对了 NPZ 时间、帧数、积分状态规格、相机数组和 183 个稳定模型引用；每条初始积分状态与对应公共准备状态完全相同。2,821 个三次 batch 的身份/seed 与原始记录一致，1,047 次后续查询均指向该 trial 已执行的边界。3,868 次 FK 检查最大平移误差 0.000420 m、转角 0.001074 rad；3,864 次 solver 复用记录均更新了实测锁定关节和世界。

最终软件回归 276 项，274 通过、2 条件跳过；日志 `outputs/diagnostics/curobo-time/final-software-regression-20261025.log`。跳过为 pi05-data 可选依赖和既有 opt-in OpenGL 检查，不计为真实实验通过。

## 实际耗时与估时偏差

| 阶段 | 实际墙钟 |
|---|---:|
| 构建，含预览和清理 | 3,257.385 s（54.29 min） |
| 数值标定、加载及收尾 | 10,363.174 s（2.88 h） |
| 其中全局标定调度 | 10,250.066 s |
| 6 段离线渲染累计 | 51.205 s；另有 CLI 启动、解码检查 |

构建分项：发现/保存 1,158.386 s、资格/保存 998.604 s、增强/预览 1,079.539 s、清理 20.851 s。两阶段墙钟合计 13,620.559 s；按日志从 10 月 24 日 21:25:35 构建启动到 25 日 01:13:30 标定 CLI 结束，约 3 h 47 min 55 s，含命令间隔。

标定 GPU 活跃累计 20,368.130 卡秒；实际查询峰值 2、CPU trial 峰值 18（容量 60）、准备峰值 12。60 个 CPU worker 均处理过任务，但没有 60 条同时活动。CPU 物理累计 21,024.351 进程秒、准备累计 671.638 进程秒、记录收尾累计 420.602 进程秒；逐 trial 排队累计 582,113.820 s。这些已并行，不能相加为墙钟。逐 trial 规划累计 58,770.097 s 包含 batch 的三次完整归属，不是设备活跃总时间。

运行前按满额上限 300 场景/18,000 trial 估计：构建正常 1–3 h、保守 3–6 h；标定正常 18–30 h、保守 40–60 h；约 6 段视频正常 1–5 min、保守 5–15 min。依据是首阶段 GPU 工作量 374.379 卡秒/60 trial，双卡占用率 0.830；Case1 成本系数假设 1–1.5、Case3 为 1–2，CPU 按实测峰值 8 外推，未再将已经并行的墙钟除以 GPU 数。

构建完成、启动标定前，按实际站位将工作量更新为 141.1–226.7 份首阶段（Case1 510/20×系数、Case2 855/20、Case3 1457/20×系数），对应约 52,825–84,867 卡秒，预计正常 **10–17 h**、保守 **20–35 h**。实际仅 2.88 h，原估计偏保守：Case3 全部在规划阶段无解，未进入物理及后续规划；已记录 trial 的实际执行比例为 13.05%（1,104/8,463），低于首阶段 36.67%，后续查询/站位也更少。最终双卡相对总墙钟的活跃占比约 98.27%（相对调度阶段 99.36%），高于首阶段。以上用于解释估时偏差，不是相对旧流程的加速倍数。

## 实测状态视频与限制

| Case | rollout | 结果 | 帧数 | 实测时长 s |
|---|---|---|---:|---:|
| Case1 | r000328 | 成功 | 228 | 11.348 |
| Case1 | r000335 | 底盘越界 | 253 | 12.576 |
| Case1 | r000871 | 底盘/臂跟踪超时 | 401 | 20.000 |
| Case2 | r000148 | 成功 | 259 | 12.900 |
| Case2 | r000177 | 后续规划无解 | 590 | 29.448 |
| Case2 | r001370 | 抓取保持/滑移失败 | 221 | 11.000 |

输出为 `outputs/replays/<replay-run-id>/<rollout>/front_camera.mp4`；r000871/r001370 在 extra run，其余在主 replay run。全部 320×240、20 fps，帧数、解码与按实测时间保持前帧的索引检查通过；`replay.json` 保存来源、实测/播放时间与帧索引。回放仅恢复完整状态并 mj_forward，不重新积分或规划。

Case3 没有真实执行，因此没有执行视频，也未验证该轮实际开门后的物理续规划；软件覆盖不升级为开门成功。全部 observation_complete=false，数值标定和这些视频不自动成为完整视觉训练样本。资产引用依赖当前 data_root 和 MuJoCo 3.5.0；像素逐位一致性、RoboCasa profile 及连续导航采集不由本轮实验证明。没有同配置旧基线，不报告加速倍数。首阶段单任务 20×3 的 ≤30 min 目标已由 225.458 s 实测达成，不能据此承诺所有任务的耗时或成功率。
