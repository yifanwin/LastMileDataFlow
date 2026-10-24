# E02：实际记录路径 PNG 写入优化

- 状态：archived
- 日期：2026-10-23
- 替代：[当前文档](../../../info/01-current-status.md)
- 关联：[E01 结果](E01-visual-recording-probe.md)、[优化方案](../plan/2026-10-22-time-optimization-plan.md)

**2026-10-24 归档：保留当时的设置、结论与未验证边界；当前实验以 PandaOmron、每站位 3 次为准。本文中的旧预算与待办不作为当前方案。**

**实际记录路径中，PNG 压缩级别 1 相对 6 快 2.75 倍，体积增加 20.47%，解码像素全部一致。本工作区记录器已采用压缩级别 1；尚未测量整场标定加速。**

## 执行过程

1. 检查工作区，原有已跟踪文件和未跟踪文件均无修改。
2. 创建本工作区独立 `.venv`，安装本项目及图像记录依赖，后从本机 uv 缓存补装 CPU PyTorch 2.7.1 用于软件回归，没有借用其他工作区环境。完整 cuRobo 和策略依赖未安装，当前不具备 GPU 规划环境验收结论。
3. 原 E01 的 `model-train/outputs/rby1-full-calibration-02` 未迁入当前机器，改用主仓库 `outputs/eval_ds/batch-{1,33,41,54}/scene-files/` 的四个既有 RBY 场景初始快照，只读加载 `model.mjb` 与 `initial.npz`。
4. 使用 MuJoCo 3.5.0 渲染每场景的 head / left wrist / right wrist，共 12 张 320×240 RGB。没有物理积分、规划或新任务成败标签，也没有使用场景预览拼图代替训练相机图像。
5. 调用真实 `AttemptRecorder.observation()`，仅覆盖传给 imageio 的 PNG 压缩参数。两级各预热一次，再交错测量 10 轮；每轮使用独立临时目录，逐张解码对比原 RGB 后删除本轮临时图片，仅保留紧凑结果。
6. 收益明确后，将记录器的 PNG 写入改为 `imageio.imwrite(path, pixels, compress_level=1)`，同步数据格式说明。没有新增公开配置。

## 测量结果

| 项目 | 压缩级别 6 | 压缩级别 1 |
|---|---:|---:|
| 每轮 12 张中位写入耗时 | 111.09 ms | 40.35 ms |
| 每轮最小–最大耗时 | 110.63–111.30 ms | 40.04–40.47 ms |
| 平均每张耗时，按 10 轮总时间计算 | 9.25 ms | 3.36 ms |
| 每轮文件总大小 | 488,825 B | 588,864 B |
| 与输入 RGB 的解码像素一致性 | 全部一致 | 全部一致 |

中位时间减少 **63.68%**，每轮节省 **70.74 ms**。10 轮测量与预热合计检查 264 张输出 PNG。Python 3.11.15、NumPy 2.4.6、imageio 2.38.1、Pillow 13.0.0。

计时包含 `observation()` 内的目录创建、PNG 编码、文件写入和关闭；不包含 RGB 采集、读回验证、临时文件清理及 `fsync`。与当前在线记录路径一致，文件关闭不等于持久化刷盘完成。临时目录与最终运行产物都在本工作区 `outputs/` 所在文件系统。

本轮使用四个场景的初始视角，不是成功抓取轨迹；不能外推全部场景和分辨率。E01 的 4.71 倍来自另一机器、另一组图像和内存编码，本轮 2.75 倍包含真实文件写入，两者不能作为优化前后直接比较。

## 验证

- 实验中两种压缩级别的全部输出均逐像素对比通过。
- 记录领域：40 项，39 通过、1 项因缺少可选 `pi05-data` 依赖跳过。
- 全套软件回归：239 项，235 通过、4 项跳过，退出码 0；可选数据导出及两项策略预处理缺少依赖，一项真实 OpenGL 检查未显式启用。日志：`/tmp/lastmile-curobo-E02-unittest-final.log`。
- 初次记录领域测试因未安装本项目 CLI 失败，安装本工作区项目后通过；初次全套回归的两处错误均为缺少 PyTorch，补装后通过。没有为适配测试修改额外源码。
- `git diff --check` 通过，实验脚本 `--help` 可用。

初次渲染因默认动态库路径缺少 `libEGL` 失败；指定本机已有 `/data1/wenyifan/opt/lib` 后完成。使用 NVIDIA EGL 设备索引 0，不据此推断物理 GPU 编号。未修改系统库、设备权限或原始资产。

## 复现

在本工作区根目录运行，`--output` 使用新目录：

```bash
REPO=/data1/wenyifan/lmdf/LastMileDataFlow
PYTHONPATH=src LD_LIBRARY_PATH=/data1/wenyifan/opt/lib \
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=0 \
__EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json \
.venv/bin/python scripts/experiments/curobo_png_write_probe.py \
  --scenes \
  "$REPO/outputs/eval_ds/batch-1/scene-files/val1-open-0121-0034-Case3" \
  "$REPO/outputs/eval_ds/batch-33/scene-files/val33-open-0077-0019-Case3" \
  "$REPO/outputs/eval_ds/batch-41/scene-files/val41-open-0043-0005-Case3" \
  "$REPO/outputs/eval_ds/batch-54/scene-files/val54-open-0025-0012-Case3" \
  --repeats 10 --output outputs/diagnostics/curobo-time/E02-repeat
```

也可传 `--images <rollout>/images --frames 0 50 100 150 200`，直接读取已有真实相机图像，不加载 MuJoCo。

脚本：`scripts/experiments/curobo_png_write_probe.py`。本轮原始结果：`outputs/diagnostics/curobo-time/E02-png-write-02/result.json`。未复制完整图像树。

## 结果与后续

本次完成 PNG 编码参数的局部优化。现场渲染、采样频率、任务证据、动作和轨迹记录时刻沿用原逻辑。尚未实施异步编码、站位并行或 planner 复用。

下一步对真实执行拆分 `mj_step`、额外 forward、任务 monitor、遥测、render、PNG 写入和初始化/收尾时间，再决定异步编码与遥测精简的优先级。最终仍按完整 20×5 trial、成功图像全部就绪 ≤30 分钟验收；本轮没有对应的完整 rollout 结果。
