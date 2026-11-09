# mjlab / SAC 实施记录

- 状态：active
- 日期：2026-11-09
- 替代：无
- 关联：../plan/01-implementation.md、../requirement/01-fixed-base-sac.md

## 当前结果

已在 actor-critic 工作区加入独立研究依赖、安装脚本、val103-cup30 配置、局部场景准备、mjlab manager 环境、Base Frame DLS、连续夹爪、共享 strict-pick 保持窗口、图观测、GAT SAC / Replay、训练 / 评分 / 评估入口和软件测试。真实 GPU 环境和训练验收正在推进，**尚未获得新策略真实抓取成功、训练 checkpoint 或有效 Q 图结论**。

主 checkout 及其 Python 环境未混装研究依赖。原有五个公开 CLI 不变。用户已有 reports/README.md 和本主题规划文档修改已保留。

## 实施过程与选择

1. 检查两个 checkout 的修改，读取实施计划及本地 mjlab 源码。上游基线 revision 为 `936f7a125`，本项目实现开始时为 `e917cf5`。
2. 默认执行权限看不到 GPU；完整主机权限能够访问 CUDA 0–7。实测 0–3 利用率 99–100%，4–7 利用率 0%、空闲显存约 72 GB，首轮选择物理 GPU 4，未干预其他进程。
3. 原数据路径 `/data1/wenyifan/molmospaces_data` 不存在，实际资产位于 `/data1/wenyifan/lmdf/molmospaces_data`。研究配置显式使用后者。真实目标 body 为 `cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2`，资产为 Cup_30。
4. 用现有 Simulation / task_local_model 恢复原始 XML，局部半径 2 m；准备时源模型 150 bodies，裁剪后含机器人 105 bodies，去除机器人供 mjlab 单独挂接后的场景 84 bodies。保留原目标及周边真实家具，不载入旧 MJB。
5. 保持窗口成为 NumPy / Torch 共用函数；CPU evaluate_pick 的公开接口与证据检查保留。双指力、支撑、滑移与固定部件漂移在 GPU 子步监测，普通碰撞作为惩罚和遥测。
6. SAC 使用独立 Actor/Q1/Q2 GAT、两个 target、tanh logπ 修正和温度更新。Replay 拥有独立存储，终止样本先入库、再局部 reset；20 秒时限不 bootstrap。恢复为参数 / 优化器热恢复，Replay 重新 warmup。
7. 评分恢复每个真实站位初态，使用在线 min-Q 的 16 次动作采样。评估先保存空间验收集合的分组及 seeds，再运行 rollout；保留初始化 Actor 基线入口、分母、失败原因和状态 / 动作回放数据。
8. 真实 val103 上的 0.1 m / 16 yaw 候选共 5,072 点，初始化检查合法 2,263 点：训练 1,486、验证 291、验收 486；非法点为 Home 穿插 2,795、地面支撑 14。筛选仅用物理初始化约束，不使用 IK / FOV / 规划成功过滤。该检查使用原 CPU 环境，GPU reset 的稳定性仍需单独确认。

## 已运行的验证

| 检查 | 实测结果 |
|---|---|
| 原始 val103 / Cup_30 场景重编译和统一局部裁剪 | 通过，使用真实资产 |
| 旧抓取判定局部回归 | 19 项通过 |
| SAC 数值、图排列、Replay 所有权、checkpoint 恢复 | 6 项通过（原 torch 2.7.1 的 CPU 软件测试，未替代研究环境验证） |
| 图几何分块、动态目标、Base Frame yaw、共享保持窗口及旧判定 | 与 SAC 合计 27 项通过 |
| 站位分组、空间 split、基础设施失败分母 | 3 项通过 |
| 全套离线回归 | 407 项通过、2 项跳过；此后新增图几何 / 站位统计测试在局部通过 |

全套首轮的子进程失败来自主机缺少 libEGL.so.1；从 Ubuntu 官方包提取用户态库至 `/tmp/lastmile-gl/lib` 后复测通过，未修改系统驱动。测试设置 OMP/MKL 单线程，图网络临时依赖在 `/tmp/lastmile-pyg`，没有写入主 Python 环境。

## 当前环境问题与后续检查

官方 CUDA 13 PyTorch 包可用，固定选择 2.14.0+cu130。整包传输在本会话会停滞，已确认 Range 请求可正常下载，使用临时分段下载工具补齐 NVIDIA wheels；安装脚本提供常规官方源安装路线。参考 mjlab 源码未修改。

仍需完成：研究环境实际安装 / 版本检查、上游 smoke、项目单环境与 32 环境 stepping、脚本抓取抬升保持正例、子步接触时序实测、真实采样到 learner 更新、吞吐测量、训练、checkpoint 选择、验收与初始化基线、图和视频。P0–P6 不能因源码存在或 CPU 软件测试通过而标为全部完成。

当前抓取 TCP 输入为数据抓取库的固定 row 0；其 Panda TCP 对齐与实际可抓取性仍须在 P2 实测。当前 reset 从统一准备阶段的 settled 物体状态恢复，需继续核对各站位伺服初始化后的稳定性及局部 / 全房间代表 rollout。
