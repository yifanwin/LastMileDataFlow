# 新例子测试：val0 喷雾瓶近目标导航

## 执行与结果

更换为 val0 / `pick-5a86ecebea93`（喷雾瓶），禁止沿用前例的远端站位 fallback。新终点必须距目标不超过 1 m，且比起点至少近 0.4 m、原膨胀避障网格中可达。已有成功点仍被保守网格过滤，因此本次是近目标导航站位，不是已验证的抓取站位。

| 检查 | 结果 |
|---|---|
| S0 / S0058 | XY=(4.9536, 5.7714)，距目标 1.5991 m |
| S1 / S0052 | XY=(5.8406, 5.3279)，距目标 0.8870 m |
| 目标 | XY=(5.8406, 4.4409)，位于支撑家具投影内 |
| 规划路线 | 1.0864 m |
| 真实执行 | 14.748 s、295 控制步，导航成功到站 |
| 末端 XY 误差 | 1.34 mm |
| 机器人碰撞记录 | 0 |
| 目标 XY 漂移 | 0.0184 mm |

视频保留真实物理状态，场景未编辑，执行开始后无恢复/重置。显示未膨胀的实际轮廓，不加“支撑物/障碍物”文字；A* 安全网格、余量与碰撞检查未放宽。

## 视频与核验

[视频浏览页](../../outputs/diagnostics/astar-near-target-val0-video-20261010/index.html) 包含真实场景 + 同步导航小图、放大导航图（同一次记录回放）。两段均完整解码为 296 帧、20 fps、14.8 s，检查初/中/末帧及所有页面链接。

![近目标导航末帧](../../outputs/diagnostics/astar-near-target-val0-video-20261010/map_final_decoded.png)

图：S1 比 S0 更接近目标；标记是底盘中心，不是夹爪。图中路径是同一次真实执行状态，不证明抓取成功。

## 故障与重试边界

1. `astar-navigation-near-target-val_0-20261010-062e72c1`：自动分割相机选角索引异常，0 执行步。保留设施故障记录。
2. `astar-navigation-near-target-val_0-20261010-ab63a219`：固定观察相机后起步，躯干从下限发生轻微负向越界，安全检查在 0.356 s 停止；8 步、无机器人碰撞，保留失败短视频。
3. `astar-navigation-near-target-val_0-20261010-1be520ae`：全新独立 Simulation，在 begin 前将躯干初始化到合法中间位置 0.369，并检查起终位姿碰撞；沿相同 A* 路线成功。固定观察视角 135° / -70°，不隐藏场景物体，不降低物理阈值。

运行证据在第三次目录的 `test_plan.json`、`navigation_request.json`、`initialization_adjustment.json`、`observer_policy.json`、`summary.json` 及 attempt 动作/状态/物理日志。前两次结果保留、不改写为成功。

复现：`diagnostics/astar-other-scene-selector.py`、`diagnostics/astar-near-target-video-probe.py --selection /tmp/selected-other-astar.json`（选择数据亦冻结于第三次 `test_plan.json`）、`diagnostics/astar-navigation-map-replay.py`。本次未执行抓取，未生成全量数据集结论。

## S0/S1 按实际底盘半径绘制

- 从本场景 `robot_geometry.json` 读取底盘及车轮几何绕底盘原点的外接圆，半径 **0.443518416 m**、直径 **0.887036832 m**。不包含规划安全余量，也不使用热力图的 0.35 m 半径。
- S0/S1 的圆心保持原站位，圆圈以地图米/像素比例绘制；目标仍为点标记。生产视频同步从当前模型 `robot_footprint(sim)` 读取半径，后续采集自动应用。
- 复用上述真实执行的 296 个记录状态，20 fps、14.8 s；仅重绘，不新增物理执行，不改变 A* 网格、路径或障碍物。
- 新产物：[视频预览](../../outputs/diagnostics/astar-val0-base-footprints-video-20261010/index.html)、[MP4](../../outputs/diagnostics/astar-val0-base-footprints-video-20261010/astar_navigation_map.mp4)。已解码并检查首、中、末帧；半径缩放及非法半径测试通过。

![实际底盘半径的 S0/S1 圆圈](../../outputs/diagnostics/astar-val0-base-footprints-video-20261010/map_final_decoded.png)
