# MolmoBot 接入记录：接口已通，完整采样尚未完成

已在指定 worktree 接入独立 VLA 后端，保留旧 cuRobo 后端和历史结果。
真实 pick/open 已执行并留存证据；**两次均因双指自碰撞失败，不代表任务能力验证通过**。
正式任务为 val_103 全目标、2 m 工作空间、每个有效点5次；当前进度以运行文件为准。

## 执行过程与来源

- worktree：`LastMileDataFlow/.worktrees/no-edit-lastmile-val`，分支 `feat/no-edit-lastmile-val`。
- 起点 commit：`6c9bf36d093a8f66c22e5d4aa397dfa6ca1cd931`；本次修改未提交、未合并。
- 模型实现只读参考：upstream `33c0ca77bf6062a23d60ffd4a6859334c4a46d30`。
- 检查点：`/nas/wenyifan/models/MolmoBot-RBY1Multitask`；`model.pt` 为19,992,232,602字节，
  SHA256 `009fa4dc60493da7b4aad9ffa58481fdd87298c2c11d95f6d472b67b56789a3d`。
- 先核对 SIM 配置、机器人控制文档与原始执行器，再实现服务隔离、原生控制桥、物理判定和记录。
  随后执行回归、真实接口 smoke、独立夹爪响应测试；修复零增量保持、被拒绝动作记录、初帧视频时序及旧快照兼容。

## 已落地的接口

入口为[采样配置](../../configs/no_edit/molmobot.json)和[模型配置](../../configs/policies/molmobot.json)，
完整操作说明见[评测文档](../../docs/molmobot-station-evaluation.md)。

| 部分 | 当前实现 |
|---|---|
| 策略 | 独立模型解释器、私有本机 IPC；每场景常驻，每次 attempt 重置策略和随机种子 |
| 默认夹爪 | pick 连续；open 阈值5二值化为±100；可选统一 binary / continuous |
| 控制 | 显式 native 20D 协议；原生 motor 不转换为旧位置伺服；躯干绝对关节参数 |
| 输入/时序 | 实测22D；预测16步、执行8步；100/20/4 ms；默认400策略动作 |
| 图像 | 实际1024×576；right wrist / head / left wrist；upstream鱼眼只处理head一次 |
| open 条件 | 首帧可见部件点及首帧鱼眼head图；裁剪后无有效点则拒绝，不静默降为无点策略 |
| 采样 | 2 m半径，间距默认实测底盘包络半径0.443518 m，每有效点5次独立仿真 |
| 证据 | 原始预测、后处理值、实际ctrl、预测批次22D输入与点提示、物理步记录、qpos replay、四视频 |
| 热图 | 复用原Gaussian输出；未知不计零，低成功率不删除；不转入A*示教采集 |
| 兼容性 | 旧位置协议不变；历史v1/v2快照缺少profile时仅视作position，不允许冒充native |

相机当前使用模型名义视场head139°、wrist58°，**未加入额外随机外参**。
鱼眼函数内部的微小随机畸变按attempt种子管理；这不等于完整复现上游所有场景随机化。
语言按类别生成；重复同类目标存在消歧限制，不提供目标3D位姿或grasp给模型。

## 真实接口试跑结果，不是完整成功率

接口 run：`molmobot-val103-interface-smoke-20261011`，仅两个目标、每任务最多1次。
GPU 2在连续3次、间隔5 s采样中利用率均为0%，空闲显存均为39,627 MiB。
模型加载280.726 s；显存 allocated 峰值如下（不是整卡总占用）。

| 目标 | 实际动作/物理步 | 夹爪方式 | 仿真/attempt墙钟时间 | CUDA峰值 | 结果 |
|---|---:|---|---:|---:|---|
| Cup_30，S0033 | 98 / 2444 | 连续 | 9.776 / 65.060 s | 10410.135 MiB | 双右指自碰撞，failure |
| Fridge门4，S0026 | 170 / 4240 | 二值 | 16.960 / 60.528 s | 10412.297 MiB | 双左指自碰撞，failure |

两个attempt的文件摘要/轨迹审计均通过。八段视频均成功解码：机器人三路1024×576、
第三人称1280×720，均10 fps。pick首批模型输入为3图，open为4图；
鱼眼head为308×232，open第4图与首帧鱼眼head逐像素一致。

![真实open首帧鱼眼输入](../../outputs/no_edit/molmobot-val103-interface-smoke-20261011/scenes/val_103/attempts/open/S0026/open-fridge2-open4-S0026-t0-59bdc77f/model_input_1.png)

图1：真实open首帧的模型head输入，未放大。open提示点变换后为归一化坐标约(0.536, 0.405)，位于裁剪图内。

![接口试跑的部分观测热图](../../outputs/no_edit/molmobot-val103-interface-smoke-20261011/scenes/val_103/tasks/cup2/maps/success_heatmap.png)

图2：只观测了一个Cup站位的一次失败，**该点仅1/5，其他有效点未执行**。
颜色为基于已观测终止trial的Gaussian插值；白色未观测、灰叉过滤、黑色障碍/场外。
没有置信区间，也不能据此宣称完整0/5、不可达或整个场景成功率为零。

独立真实原生夹爪测试使用三个新仿真，均不在begin后重置：−100保持打开约−0.05 m，
+100闭合至约0 m，0不主动闭合。+100空夹在100 ms采样中出现最大约6.50 mm双指穿透、
1423 N接触力，随后接近闭合位置。该结果说明闭合瞬态与工程碰撞门槛有冲突；
不通过隐藏裁剪、删除接触或放宽阈值来消除失败。它限制了对“纯策略能力”的解释。

## 验证与正在执行的工作

- 最终源码回归：**257项，8项跳过，其余通过**；包含native原子控制、时序、零命令保持、旧v1/v2快照、
  被拒绝预测留存、open裁剪点检查及5次采样不调用grasp/A*。fixture结果不作真实成功证据。
- `git diff --check`通过。最终源码与早期接口smoke不同；二者各自保留独立冻结摘要，不混合统计。
- 正式run：`molmobot-val103-2m-5trials-20261011-r2`，不设target/max-trials限制，单场景全目标。
  仍从CUDA 0–7连续采样选择利用率≤20%、空闲≥24576 MiB的设备；场景墙钟上限86400 s。
  已实际调度至**GPU 2**（3次均0%/39627 MiB空闲），场景冻结、模型加载已完成（264.704 s）。
  实际枚举24任务：20 pick、4 open；已进入真实每点5次采样。
  源码摘要`e5b3f7915609e83689ecfab2f7f011176f5e302ac255964e002cdcfe94133f36`与当前源码一致；
  `subset.targets/max_tasks/max_trials`均为null，半径2.0 m、`trials_per_station=5`已核对。
- 2026-10-11 **13:15:34（北京时间）**进度快照：首个SprayBottle任务S0017已完成**5次，0/5成功**，
  均为碰撞失败（3次双指自碰撞、2次环境碰撞）；五份真实attempt审计均通过。
  新日志中的实际预测状态均22D、原始输出均20D；正在继续下一站位。
  当前仅该任务的5/45次终止trial完成，不是全任务或全场景0/5。
  [冻结进度证据](../../outputs/diagnostics/molmobot-final-source-audit-20261011/progress-snapshot.json)、
  [该任务实时热图](../../outputs/no_edit/molmobot-val103-2m-5trials-20261011-r2/scenes/val_103/tasks/spraybottle2/maps/success_heatmap.png)。
- 使用真实upstream配置类额外构造了三种gripper mode × pick/open的6种组合，均通过；
  [配置核查日志](../../outputs/logs/molmobot-real-policy-config-preflight.log)不涉及额外模型任务试验。
- 两次启动阶段记录保留：最初nohup进程未持久；r1在模型摘要阶段主动停止以补齐v2兼容。
  二者没有物理attempt，不计机器人失败；正式运行使用新ID。

当前最大不确定性是：native闭合碰撞在各站位出现的比例，以及通过安全检查后是否能真实完成任务。
完整采样尚未结束，不宣称完整热图交付；没有跑全部val场景，也没有验证实时机器人部署。
旧cuRobo的24任务结果见[既有报告](2026-10-11-no-edit-v080-val103-all-cuda1-221315.md)，
但相机/夹爪和有效站位过滤不同，当前两个smoke不支持与它作配对性能结论。

## 查看进度与保留产物

- 正式日志：[主日志](../../outputs/logs/molmobot-val103-2m-5trials-20261011-r2.log)、
  [PID](../../outputs/logs/molmobot-val103-2m-5trials-20261011-r2.pid)，worker/model日志也在`outputs/logs/`。
- 正式结果根目录：[run目录](../../outputs/no_edit/molmobot-val103-2m-5trials-20261011-r2/)；
  查看`summary.json`、`scenes/val_103/tasks/*/progress.json`与`station_statistics.json`。
- 核查证据：[接口审计](../../outputs/diagnostics/molmobot-interface-audit-20261011/results.json)、
  [夹爪响应](../../outputs/diagnostics/molmobot-native-gripper-20261011/results.json)、
  [最终回归日志](../../outputs/logs/molmobot-full-regression-delivery.log)。
- 源码、最小配置、测试和本文可保留；权重/缓存/视频/完整预测与运行日志留在忽略的outputs或只读NAS，不进Git。
  图1/2属于接口诊断而非最终性能图，已检查实际图像与路径；最终每点5次图由正式run输出替代。

## 2026-10-11 接触判定更新（替代旧运行期碰撞阈值）

上述接口/严格规则 run 的失败证据保持原样。按用户新要求，运行时接触只记录，不因双指互碰、
环境接触或毫米级穿透中止；初始化仍严格检查，仿真异常与任务失败分开。
新 run 不续接旧分母，见[简化接触策略记录](contact-record-only-20261011.md)。
