# A* 导航视频测试

## 过程与结果

使用既有 val103 / Cup30 冻结场景，新建独立诊断目录和 Simulation；重新构建包含支撑物完整投影与底盘膨胀的 A* 网格，选择网格合格的 S0006 → S0021 路线，连续执行导航，不进行 cuRobo 抓取、不修改场景物体、不在执行后重置位姿。

- 结果：`navigation_only_arrived`，导航到站成功。
- A* 规划距离：1.2521 m；实际物理执行：17.5 s，350 个控制步。
- 到站 XY 误差：约 1.1 mm；机器人碰撞记录：0。
- GPU：2（运行前探测空闲设备；受限沙箱不能访问驱动，已通过沙箱外 GPU 执行完成）。
- 每路视频记录 351 帧、20 fps，原始机器人三相机与第三人称分析视频均保存。

## 视频

[视频浏览页](../../outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf/index.html)

- [真实场景 + 同步导航小图](../../outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf/attempts/navigation-only/videos/third_person_camera.mp4)：真实执行时同步渲染，棕色为支撑物、灰色为规划路线、蓝色为实走轨迹。
- [放大 A* 导航图](../../outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf/astar_navigation_map.mp4)：按同一次记录的真实底盘状态逐帧回放，无轨迹插值，不是新增物理执行。

## 证据与限制

诊断根目录：`outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf/`。
`test_plan.json` 保存执行前目标、停手条件及配置；`navigation_request.json` 保存 S0/S1 和 A* 路线；`summary.json` 保存真实执行结论；`attempts/navigation-only/` 保存逐步 20D 动作、实际状态、每个物理 tick 的碰撞检查和最终状态。

单次导航通过不代表其他路线、抓取或全量采集通过。终点仅用作导航测试站位，不据此声明抓取成功。旧采集数据和视频没有覆盖。

复现脚本：`diagnostics/astar-navigation-video-probe.py`、`diagnostics/astar-navigation-video-package.py`；首个脚本重新选择空闲 GPU，并使用新唯一目录。

## 视频核验

两段视频均完整解码为 351 帧、20 fps、17.55 s（其中连续物理状态覆盖 17.5 s）。已检查初始/中间/末帧，支撑物在起步前显示，实走轨迹随实际状态累计；浏览页所有视频与 poster 链接均可解析。校验摘要见诊断目录 `video_checks.json`。

![实测回放末帧](../../outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf/map_final_decoded.png)

图：同一次实际仿真记录的末帧，蓝线到达 S1，棕色支撑物与膨胀障碍不参与可行路径；不含抓取验收结论。

## 显示修正：不画规划膨胀，不标注家具内文字

此前视频深色栅格显示的是 A* 机器人中心禁入区，额外包含实际底盘外接圆 0.4435 m 和导航余量 0.02 m，即约 0.4635 m 的安全膨胀，因此看起来比家具本身大。现将导航显示与规划网格分离：画面使用零底盘膨胀的碰撞投影，A* 原始安全网格、规划路线和真实执行记录不变。取消家具内部“支撑物 / 障碍物”文字，保留颜色轮廓。

- 生产静态导航图使用物理投影显示，`navigation_display.json` 明确记录显示与规划分离；三人称导航小图取消文字。
- 使用同一次 351 个实测状态重绘导航视频，完整解码核验为 351 帧、20 fps、17.55 s；没有新增物理仿真或覆盖旧视频。
- [新版导航视频浏览页](../../outputs/diagnostics/astar-physical-footprint-video-val103-20261010/index.html) · [新版 MP4](../../outputs/diagnostics/astar-physical-footprint-video-val103-20261010/astar_navigation_map.mp4)。
- 定向回归验证显示改变不会修改规划 `free` 网格，并检查取消文字与显示模式元数据。

![实际轮廓显示末帧](../../outputs/diagnostics/astar-physical-footprint-video-val103-20261010/map_final_decoded.png)

图：同一条实走路线，底图只显示实际几何范围；白色不代表机器人中心能进入所有位置，路径仍来自保留安全膨胀的原 A* 网格。
