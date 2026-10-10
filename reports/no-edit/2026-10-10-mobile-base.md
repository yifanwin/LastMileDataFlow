> 更新：原 mobile-full 队列已因新增第四路分析视频与整体计时停止；新全量运行为 `no-edit-val-four-view-full-20261010`。历史结果保留，见 [最新报告](2026-10-10-four-view-timing.md)。

# 无编辑构建：自由底盘与失败视频增量交付

**采样点操作已改为 cuRobo 联合规划底盘 x/y/yaw 与单臂；两类视频记录已实现，新全量队列正在运行，尚未全量完成。** 保留规则仍为每任务 1–3 段真实成功连续轨迹，至少 1 段、优先 3 段。

## 修改过程

1. 核对旧队列：已自动因两个资产的空 grasp 数组 `(0,)` 停止，没有存活 worker，不需要误停其他进程。旧结果/视频/日志保留，[旧队列终态](data/locked-base-final-state.json)不混入新统计。
2. 为 NativePlanner 新增显式 mobile-base 模式：active joint 顺序为 `base_x/base_y/base_theta + 7 个单臂关节`；原流水线默认仍只规划机械臂。
3. 保持固定的规划参考坐标系，正确转换实际移动后的底盘、世界目标和碰撞网格；在输出目录生成有工作空间限位的派生 URDF，不改外部资产。
4. 通过现有 20D `[0:3]` 和对应机械臂分量同步执行，所有操作和重试取消固定底盘。只取消不适用的“固定肩点不可达”提前拒绝。
5. 增加 `mobile-pick-v1`：允许底盘移动，保留真实双指受力、抬升/稳定保持、滑移、碰撞、torso 联动、head/闲置臂约束，并逐物理步检查 2 m 工作空间。旧 `strict-pick-v3` 默认仍拒绝底盘漂移。
6. 所有真实执行的站位评测录三相机 MP4，选定 S0 后关联失败试验；连续 rollout 保留 S0→S1 + S1 操作/重试的同一视频流及逐步 PNG。
7. 修复空 grasp 数组：声明来源后使用现有几何 pinch fallback；非空但格式损坏的数据仍不伪装为有效抓法。

## 两类视频与索引

| 数据 | 保存位置与语义 |
|---|---|
| S0→S1 移动 + S1 操作 | 同一 attempt 的 `videos/head_camera.mp4`、`wrist_camera_l.mp4`、`wrist_camera_r.mp4`；不是拼接独立重置试验 |
| S0 失败操作 | 原始独立站位失败 attempt 的相同三路视频；`s0_failure_videos` 指向视频 manifest、归因和原 attempt |
| 时间/阶段 | `videos.json` 保存真实仿真帧时间；`trajectory.jsonl` 保存动作、状态和 navigation/arrival/操作阶段 |
| 未执行的规划失败 | 保留初态图和规划/IK 证据；`video_status=not_executed`，没有伪造失败动作视频 |

为控制空间，站位试验不重复保存逐步 PNG，但保留真实 MP4、紧凑状态、逐物理步证据和 replay。连续 rollout 同时保存 PNG/MP4。编码异常单独列为基础设施异常。

## 实际验证

| 验证 | 观测结果 | 不能据此宣称 |
|---|---|---|
| 原始 val_103 / Cup_30 / S0033 | **mobile_pick 成功**，321 控制步，底盘最大实际平移 **0.2865 m**、yaw 变化 **1.0842 rad（约 62°）**；278 步有非零底盘命令 | 所有采样点成功或整个任务统计完成 |
| 逐物理步重算 | 4012 个样本；最大抬升 **0.0975 m**，连续满足抓取保持约 **4.792 s**，新协议重算为 success | 放宽了双指受力/碰撞/滑移阈值 |
| 三相机原始 MP4 | 每路 **322 帧**，320×240；均实际解码，帧数与 manifest 相符 | 初态可见等于移动过程中 head 始终看见目标 |
| 连续 S0023→S0032 smoke | A* 约 0.45 m，导航真实成功；S1 cuRobo 规划失败，一次重试也失败，三路视频与证据保留 | 成功的最终连续示教；该 smoke 未入最终索引 |
| 软件回归 | **188 项：180 项通过、8 项跳过**；`git diff --check` 通过 | 单元 fixture 等于真实全链路成功 |

可复查的紧凑数据见 [真实 mobile probe](data/mobile-probe-summary.json)。回归日志为 `outputs/no-edit-mobile-regression-final.log`；真实运行位于 `outputs/no_edit/no-edit-val-mobile-probe-20261010/`。

![真实抓取成功试验的左腕相机末帧](../../outputs/no_edit/no-edit-val-mobile-probe-20261010/wrist_camera_l-station-last.png)

图：真实三相机 MP4 中解码的左腕末帧，已打开检查；成功判定来自逐物理步受力/抬升/保持证据，而不是这张截图。head 保持现有协议的固定关节指令，因此操作中底盘转向后目标可能出画；保证的是初始化 head 可见，非全程 head 跟踪。

首个连续 smoke 原本选 S0033，但保守导航栅格将该站位排除；改用导航可达的 S0032，仅用于显式组件验证，不冒充按最高实测成功率完成的最终选点。

## 新全量运行

- 分支/Worktree 不变：`feat/no-edit-lastmile-val` / `LastMileDataFlow/.worktrees/no-edit-lastmile-val`；未提交或合并。
- 新运行 ID：**`no-edit-val-mobile-full-20261010`**；独立后台进程 PID **1354573**。
- 1000 个原始场景，实际已派发 GPU **1/5/2/7 → val_0/1/2/3**。启动快照为 4 个活动场景、996 个待派发、0 个完成、0 个最终成功段。
- 默认采样半径/间距、每点 5 次、<5% 丢弃和 1–3 段保留规则不变；热图含义改为“该初始站位出发、允许操作时移动底盘”的成功率。
- 新配置身份冻结，不能拿旧固定底盘输出续跑。详见 [新队列启动快照](data/mobile-full-startup.json)和[操作文档](../../docs/no-edit-collection.md)。

```bash
DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python \
  bin/lastmile-dataflow no-edit-status outputs/no_edit/no-edit-val-mobile-full-20261010
```

**剩余边界：**新全量任务仍在实际采集，当前尚不能宣称已有满足 1–3 段规则的最终成功数据。open 的真实成功、新协议下的完整连续成功示教与实际 S0 失败视频质量仍需以后续产物验收；已有单元回归、真实站位成功视频和连续失败证据不替代这些验收。
