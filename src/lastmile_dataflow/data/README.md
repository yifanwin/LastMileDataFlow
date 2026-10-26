# data

`recorder.py` 留在根层，负责一套实际轨迹和观测记录。

| 子目录 | 职责 | 模块 |
|---|---|---|
| `rendering/` | 相机、观测图像、场景对照与视频；不改物理状态 | camera_calibration、edit_views、head_views、obstacle_views、third_person、analysis_video、rollout_video |
| `evaluation/` | 实测率、完整标定与 S0/S1/benchmark 选择 | statistics、selection、benchmark |
| `visualization/` | 热图与导航图；不参与任务结果判定 | heatmap、navigation_map |
| `export/` | lastmile 物化/batch 读取、MolmoBot 导出及图像变换 | lastmile_dataset、molmobot_dataset、molmobot_images |

模型导出入口为 `data.export.molmobot_dataset.export_molmobot()`。
非原生夹爪动作仍需显式标定；图像处理不等于物理相机标定。不保留旧模块路径转发。
