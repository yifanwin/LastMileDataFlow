# 百来源全 Case：估时、命令与结果

- 状态：active
- 日期：2026-10-24
- 替代：无
- 关联：../plan/03-execution-schedule.md、2026-10-24-stage1-experiment.md

## 运行前估计（P13）

固定 `configs/scene_datasets/panda_omron_val100.json` 中 val0–val99，不按结果替换来源。每房最多选择 1 个合格 pick、1 个合格 open；pick 同时生成 Case1/Case2，open 生成 Case3，各 1 变体。目标按已有资格、支撑面积和空间覆盖规则确定；不限定 Cup_30。上限为各 Case 100 场景、合计 300，实际资格/增强拒绝会减少数量，每个 accepted 场景最多 20 点、各 3 次，上限 18,000 trial。

资源：构建 12 worker、GPU 6；标定 GPU 6/7、CPU 容量 60、准备并发 12，数值记录。首阶段实测构建 54.315 s，标定 60 trial/225.458 s，GPU 活跃 374.379 卡秒；该墙钟已经包含双 GPU 并行，不再次除以 2。

| 阶段 | 正常预计 | 保守预计 | 外推依据与限制 |
|---|---|---|---|
| 发现、资格、构建及预览 | 1–3 h | 3–6 h | 首阶段资格约 5 s/单目标、构建约 47 s/场景；最终候选更多、资格串行，Case1 放置与 Case3 关节几何未实测，按多倍成本留范围 |
| 数值标定及结果落盘 | 18–30 h | 40–60 h | 满额 100 个 Case2 约 6.26 h；Case1 假设每场景 1–1.5 倍、Case3 假设 1–2 倍首阶段成本；保守考虑难任务反复查询与 300 s 活跃预算，不是已测性能 |
| 按需视频（约 6 条） | 1–5 min | 5–15 min | 首阶段两条渲染约 9.3 s；最终长轨迹、渲染启动及抽帧检查另计，不渲染所有 trial |

这不是时长上界：实测 CPU 峰值仅 8，不能按容量 60 计算加速；来源大小、最终站位、可规划比例和后续查询会改变吞吐。构建完成后按实际 accepted/站位数更新标定预计，再开始标定。不增加抽点 rollout。

### 资源吞吐口径

首阶段每 60 trial 的独占 GPU 工作量为 374.379 卡秒；CPU 物理为 570.717 进程秒，准备为 11.039 进程秒，记录收尾为 3.439 进程秒。双卡有效占用率为 `374.379/(2×225.458)=0.830`；逐 trial 累计的 batch 规划时间已重复归属，不能用于总工作量。

满额正常情景：Case2 成本系数 1、Case1 为 1–1.5、Case3 为 1–2，相当于 300–450 份首阶段工作量。按 `GPU工作量/(2×0.830)` 得 18.8–28.2 h；CPU 用实际峰值 8 而非容量 60 估计为 5.9–8.9 h，准备池 12 对应 4.6–6.9 min，记录收尾可与其他工作重叠。因此目前估计由双 GPU 供给主导，采用正常 18–30 h，未把 CPU 容量当作实际吞吐。保守情景采用约 600–900 份 GPU 工作量及 0.80 有效占用率，对应 39–59 h，取 40–60 h。

以上是资源工作量与实测调度效率的外推，尚非最终批次实测；来源/阶段切换、Case3 多次查询及未知任务可使效率降低。构建和数值写入已各列时间，不再重复叠加首阶段墙钟。

## 复现命令

从本 worktree 根目录执行，复跑换新 run-id。

```bash
export LD_LIBRARY_PATH=/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64:${LD_LIBRARY_PATH:-}
export CUDA_HOME=/data1/wenyifan/opt/cuda-12.8
export MUJOCO_GL=egl

.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/panda_omron_val100.json --allow-partial-targets \
  --output-dir outputs --run-id panda-time-val100-build-20261024-r01 \
  --build-workers 12 --gpu 6
```

全部接受场景写入完成后，更新估时并执行：

```bash
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-time-val100-build-20261024-r01 \
  --assets-dir /data1/wenyifan/molmospaces_data/assets \
  --config configs/execution/panda_omron_all_cases_curobo.json \
  --output-dir outputs --run-id panda-time-val100-calibration-20261024-r01 \
  --gpu 6 7 --cpu-workers 60 --preparation-workers 12 --recording-mode numerical
```

## 实际结果（P14）

已启动正式构建（2026-10-24）；日志 `outputs/logs/panda-time-val100-build-20261024-r01.log`。无合格目标和 rejected 保留真实原因，不承诺 300 个 accepted；本轮原始结果写入上述 run，最终统计与视频命令在完成后补充。

- 21:41:24 发现阶段收尾：静态选择预计 Case1=69、Case2=69、Case3=88，合计 226 个构建单元；这是资格与增强前的候选数量，非 accepted。

- 21:56:13 完整资格检查收尾：Case1=66、Case2=66、Case3=88，共 220 个待构建单元；资格前的 226 不是最终数量。增强验收与 accepted 尚待完成。

- 22:01:32 资格记录保存完成，进入 220 单元增强与保存阶段；构建并发 12，GPU 6。

## 标定前更新估时

构建已退出 0，220 条构建记录完整，183 accepted、37 rejected；183 个预览全部成功，临时基线已按流程清理。接受场景全部最终点的计划如下，不使用 max-trials：

| Case | 构建单元 | accepted | rejected | 最终站位 | 标定 trial |
|---|---:|---:|---:|---:|---:|
| Case1 | 66 | 43 | 23 | 510 | 1,530 |
| Case2 | 66 | 52 | 14 | 855 | 2,565 |
| Case3 | 88 | 88 | 0 | 1,457 | 4,371 |
| 合计 | 220 | 183 | 37 | 2,822 | 8,466 |

Case1 拒绝：16 站位不足、5 候选预算耗尽、1 物理验证预算耗尽、1 变体墙钟耗尽。Case2 拒绝：1 站位不足、12 候选预算耗尽、1 物理验证预算耗尽。原始原因留在 scenes.jsonl。

按首阶段工作量与相同 GPU 6/7、C≤60、准备并发 12 更新：Case2 为 855/20=42.75 份首阶段工作量，Case1 采用 510/20×(1–1.5)，Case3 采用 1457/20×(1–2)，合计 141.1–226.7 份；对应 GPU 工作量约 52,825–84,867 卡秒。按实测双卡占用率 0.830 预计 8.8–14.2 h，考虑更多模型切换和较小站位集合后的正常范围为 **10–17 h**，保守范围 **20–35 h**。CPU 物理约 80,530–129,377 进程秒，按峰值 8 推算 2.8–4.5 h，可与 GPU 重叠；写入/收尾已含于首阶段成本，不重复相加。

这是在启动标定前按实际集合更新的估计，Case1/Case3 系数仍属未测假设。构建已完整结束，随后运行前述全量标定命令。

- 构建实际墙钟 **3,257.385 s（54.29 min）**：发现（含保存）1,158.386 s，资格（含保存）998.604 s，增强/预览 1,079.539 s，清理 20.851 s。预览 183 成功、0 失败。低于初始正常预计的 1–3 h；88 个 Case3 不做增强，独立单元并行及候选/物理预算带来的拒绝降低了实际工作量。发现/资格的中间记录序列化成本已包含，不当作纯几何计算。

- 全量标定 `panda-time-val100-calibration-20261024-r01` 已于构建完成、更新估时后启动：GPU 6/7、CPU 容量 60、准备并发 12、数值记录，全部 8,466 trial。日志 `outputs/logs/panda-time-val100-calibration-20261024-r01.log`；最终结果尚待完成。

## 构建覆盖与计数

输入 100/100 来源均经过发现；90 个来源至少有一个 accepted 场景（来源覆盖 90%）。无合格任务的来源为 val6、val8、val24、val28、val35、val48、val56、val69、val81、val99；没有替换来源。Case1/Case2/Case3 的接受来源覆盖分别为 43%/52%/88%，对各自合格构建单元的接受率分别为 43/66、52/66、88/88。

最终站位每场景 3–20 个；满 20 点的场景为 Case1 10、Case2 32、Case3 60，其余均按实际最终点数各标定 3 次。主记录保留 46,600 条候选站位检查（包含有该字段的 rejected 记录）。增强尝试已记录 Case1 14,004、Case2 33,853；真实放置验证已记录 62/59 次，Case3 不做增强。1 条变体墙钟耗尽记录不含这两个计数，以上不是补造出的完整总次数。

资格原因中的 `fewer_than_minimum_baseline_visible_points` 是沿用的原因字符串，本轮实际资格为静态几何/物理初态合法站位，不以可见像素筛选；可见性只在最终点记录。
