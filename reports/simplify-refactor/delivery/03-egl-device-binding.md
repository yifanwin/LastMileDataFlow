# GPU 渲染设备绑定修复

- 状态：done
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md、10-final-summary.md
- 修复提交：`a51e622`
- 最后更新：2026-11-12T00:18:13+08:00

## 根因与改动

CUDA_VISIBLE_DEVICES 不能约束 EGL 使用哪块物理卡。原入口和场景/benchmark 流程直接将物理 GPU 编号当作 EGL 序号，规划子进程又只设置 CUDA。CPU 准备与执行子进程没有独立明确绑定渲染设备。

本机 EGL 枚举与 GPU 编号相反：原始无 CUDA 可见性限制的查询中，EGL 0–7 对应 CUDA 7–0。进一步定位发现 EGL_CUDA_DEVICE_NV 返回的 CUDA 序号本身也受 CUDA_VISIBLE_DEVICES 影响；例如只暴露 GPU 0 时，查询只得到 {0: 7}，隐藏 CUDA 时不能据此重建完整映射。

- gpu.py 在独立、移除 CUDA_VISIBLE_DEVICES 且按 PCI_BUS_ID 排序的进程中查询 EGL_CUDA_DEVICE_NV，不创建显示或渲染上下文。没有硬编码反序映射。
- 入口解析所选 GPU 对应的 EGL 序号，映射通过环境传入 spawn 子进程。显式 --egl-devices 覆盖继续生效。
- 场景构建、站位进程、benchmark 使用共享设备绑定，避免流程内再次用 GPU 序号覆盖 EGL 序号。
- PlanningPool 在父进程解析设备映射，并将对应 EGL 序号显式传入每个规划进程。
- CPU 准备与执行进程先绑定首张指定 GPU 的渲染设备，再隐藏 CUDA 计算设备。
- RL 公共入口与 sampler_entry 同步绑定渲染设备；未修改 RL 专属文件、算法、观测或训练逻辑。
- build_scenes.py 和 bench_evaluate.py 补充可选 --egl-devices；禁用渲染的离线测试无需加载 EGL。

## 修复提交时的验证

17 项相关现有/新增测试通过：test_gpu、test_scripts、test_pool、test_split_calibration；新增测试覆盖显式映射跨 CPU/规划绑定、查询不受继承 CUDA 可见性影响、禁用渲染不依赖 EGL。git diff --check 通过。

真实最小验证前检查 GPU：0–3 空闲，4–7 有其他用户负载。仅选择 GPU 0，按顺序启动一个准备子进程和一个实际 PlanningPool 子进程；使用真实 MuJoCo Renderer 生成 64×64 RGB，并在上下文存活时读取 nvidia-smi 进程表按 PID 核对 GPU UUID。

| 进程 | CUDA_VISIBLE_DEVICES | EGL | 实际 GPU | 显存 |
|---|---|---|---|---|
| 准备，PID 3352520 | 空 | 7 | GPU 0 | 109 MiB |
| 规划，PID 3352709 | 0 | 7 | GPU 0 | 109 MiB |

两个进程仅在 GPU 0 上出现；GPU 4–7 没有本次验证进程。退出后 GPU 0 显存回到 0 MiB。原始结果及探针保存在 outputs/experiments/scratch/egl-binding/runs/01/，未提交运行产物。

## 后续已完成的验证

以下运行使用后续重构版本，不能归入 a51e622 当时的17项测试结果：

- 场景构建与出图：原场景/编辑场景2/2接受，自动预览2张成功；原场景站位、MJB和初始状态与基线一致。
- 带录像标定：2026-11-11 23:46:31–23:47:15 +08:00，c1b5b7f，在GPU0完成Panda S0020单个trial，抓取成功、235个控制步，三路RGB和视频各236帧，记录完整。
- 连续采集：2026-11-11 23:48:15–23:51:02 +08:00，c1b5b7f，复用旧完整标定，一个场景3/3配对成功，导出3条自包含示教；轨迹数组与基线一致。
- π0.5短调用链：2026-11-12 00:05:29–00:05:47 +08:00，72e44e4，两个episode各执行2步、记录完整，均预算耗尽，不能视作策略任务成功。

## 当前验证边界

渲染设备隔离已经修复，并有真实准备/规划子进程的GPU归属核对。后续也已运行带录像的单trial标定和局部连续采集，因此“录像标定、采集尚未重跑”不再适用于当前状态。

仍未重跑全量5站位×3次标定成功率/耗时对照、全部6条采集示教、RBY-1真实站位及RL训练。后续RGB与旧基线存在最大2/255的像素差异，原因未定位；不能将其认定为渲染设备隔离再次失效，也不能声称图像逐位一致。

用户已要求停止所有实验、对比和验证。后续结果、未通过项和未执行项以[最终总结](10-final-summary.md)为准；本次只更新文档，没有启动新实验。
