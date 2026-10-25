# E01：成功轨迹图像生成与 PNG 编码探针

- 状态：archived
- 日期：2026-10-22
- 替代：[当前文档](../../../info/01-current-status.md)
- 关联：[探索计划](../plan/00-curobo-optimization-overview.md)、[主仓库复核](2026-10-22-calibration-time-review.md)

**2026-10-24 归档：保留当时的设置、结论与未验证边界；当前实验以 PandaOmron、每站位 3 次为准。本文中的旧预算与待办不作为当前方案。**

**优先继续降低 PNG 编码成本、保留现场 RGB；离线状态渲染暂不替换主链。** 本轮 PNG 压缩 1 相对 6 的内存编码总耗时降为 21.2%，解码像素完全相同，体积增加 21.1%。这个比例只描述编码探针，不代表标定整体加速。

状态渲染比较有 7/15 张完全一致，其余 8 张最多相差 1 个灰度值，没有达到事先要求的逐像素一致。差异很小，但不能事后把标准改宽后宣布验证通过。

## 问题与设置

主要问题：保留成功 rollout 的视觉训练数据时，能否把图像生成移出在线物理循环？当前最大不确定性是状态还原是否足够一致，以及编码本身有多少成本。

- 分支：exp/01-curobo-time-optimization；从创建时的 main 开出。
- 只读输入：主仓库 .worktrees/model-train/outputs/rby1-full-calibration-02，成功 trial r000000。
- 5 个实际状态：frame 0、50、100、150、200；head_camera、wrist_camera_l、wrist_camera_r，共 15 张 320×240 RGB。
- 模型来自该 run 的 config.json → 场景库 → scene/model.mjb，MuJoCo 3.5.0；先加载原 initial.npz，再填该帧 qpos/qvel/ctrl/time。
- 模型 nq=341、nv=297、nbody=175、ngeom=2569、nmocap=2。两个 mocap 体为 target_ee_pose_right/left，无 geom；当前 runtime 没有逐步写这些目标体。未验证其他模型的状态完整性。
- 没有规划、物理积分或试验重放，没有生成新的任务成败标签。

原主链仍使用现场 RGB；这次只在独立 MjData 中验证候选渲染方法。

## 预期与实际

| 量 | 基线/事前标准 | 实际 | 结论 |
|---|---|---|---|
| 状态渲染图像 | 与原 PNG 逐像素一致 | 7 张一致，8 张不完全一致 | 未通过这一标准 |
| 不一致图像的最大通道差 | 0 | 全部为 1 / 255 | 几何明显错位未被观察到；差异来源尚未隔离 |
| 单张最大不同像素比例 | 0 | 0.02995%（23/76,800 像素） | 只限所检查的 15 张图 |
| PNG 编码解码一致性 | 像素完全一致 | 两种压缩级别全部一致 | 无损编码条件成立 |
| 压缩 1 的速度/体积 | 预计更快、体积更大，无倍率预期 | 见下表 | 值得测实际记录路径 |

| PNG 压缩级别 | 15 张总编码时间 | 单张编码中位数 | 总字节数 |
|---|---:|---:|---:|
| 6 | 400.55 ms | 25.42 ms | 808,790 |
| 1 | **85.10 ms** | **5.87 ms** | 979,388 |

总编码时间比为 **4.71 倍**，体积增加 **21.09%**。测的是 Pillow → 内存 PNG，不含文件打开、磁盘写入、现场渲染或末尾排空。样本来自同一 trial，压缩 6 先测、压缩 1 后测，尚未隔离首次初始化和测量顺序影响；不能外推不同场景、相机尺寸或所有磁盘环境。

状态渲染单相机中位耗时约 1.05 ms，包含 update_scene 和 render，不含 Renderer 初始化、每状态 mj_forward 或 PNG。它不是原主链完整“渲染耗时”，不能用来直接替换历史微基准的 38 ms/tick。

## 失败诊断与下一步

| 解释 | 目前证据 | 最小区分方式 |
|---|---|---|
| 不同 GPU/渲染执行带来的末位差异 | frame 0 三相机一致；后续差异都仅 1 级，但未记录原 GPU/驱动，尚不能归因 | 同进程现场状态与独立 MjData 配对渲染，固定 GPU/驱动 |
| 记录未覆盖的状态或派生量重算差异 | 当前只使用初始化固定状态和逐帧 qpos/qvel/ctrl/time | 同一个真实 trial 中比较复制前后的渲染状态与图像 |

先选择保留现场 RGB 的路线。下一项决策实验只改 PNG 压缩级别，用真实记录路径、预热并交错两种级别，比较完整写入成本和解码一致性。如果收益仍明显，再在本工作区实施压缩 1；之后才评估异步编码与执行分项。

不要求为这次编码探针重新跑整场 100 trial。原始 trial 的状态、动作和 4 ms 任务证据保持原协议。

## 收敛记录

| 项 | 运行前 | 运行后 |
|---|---|---|
| 最大不确定性 | 现有状态能否直接当作原始视觉数据来源 | 有轻微差异，不能宣称逐像素还原 |
| 路线选择 | 离线渲染 / 现场 RGB 延后编码 | 优先现场 RGB 与编码优化 |
| 已支持的机制 | 尚无本工作区测量 | 压缩级别可改变编码成本而保持解码像素 |
| 剩余问题 | 整体耗时如何下降 | 实际文件写入路径的收益与执行项内部占比 |
| 新问题 | 无 | EGL 设备编号和 NVIDIA-SMI GPU 编号不能直接假设一致 |

实验价值：有信息。方向判定：继续，仅指现场 RGB/编码优化；未验证 30 分钟目标。研究文件生命周期不动。

## 环境问题、产物与复现

初次准备发现模型有 mocap，补读初始化快照；原始状态文件未修改。默认 EGL 混入 Mesa，且 NVIDIA EGL 只枚举 7 个设备，直接用设备编号 7 无效。最终显式选择 NVIDIA vendor 和 EGL 索引 6 完成测量；未证明 EGL 索引与物理 GPU 编号相同。不修改服务器设备权限或系统配置。

脚本：[curobo_visual_probe.py](../../../../../scripts/experiments/curobo_visual_probe.py)。紧凑结果保留于本工作区 outputs/diagnostics/curobo-time/E01/result.json；运行产物不提交，不复制源图像。当前环境只完成渲染依赖安装，完整 cuRobo Git 依赖离线缓存缺失，不构成 cuRobo 规划运行环境验收。

从本工作区运行：

```bash
__EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json \
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=6 \
.venv/bin/python scripts/experiments/curobo_visual_probe.py \
  --run /home/wenyifan/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/model-train/outputs/rby1-full-calibration-02 \
  --rollout r000000 --output outputs/diagnostics/curobo-time/E01-repeat
```

设备索引取本机实际枚举结果；输出使用新目录。

视觉检查：选用数值对比表，未选柱状图/误差棒，因为只有一个 trial、固定少量帧，不能虚构独立样本或不确定区间；未保存装饰性图像。
