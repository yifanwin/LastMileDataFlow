# val103 单站位实体方块双指接触 SAC 实验

- 状态：done（训练完成，已生成独立策略重放与代表视频）
- 日期：2026-11-16（UTC+8）
- 替代：无；这是独立于旧 Cup 抓取及站位 Q 图的实验
- 关联：[需求](../requirement/01-fixed-base-sac.md)、[方案](../plan/01-implementation.md)、[运行指南](running.md)、[配置](../../../configs/station_map/rl/val103_cube_s0246_facing.yaml)

## 实施范围

保留 val103 局部场景支撑和 Cup 原中心 XY，运行期将 Cup 替换为 0.04m、0.1kg 的 freejoint 物理方块；固定 S0246 面向方块。独立协议 `rl-pick-contact-v1` 以同一物理子步两指对目标法向力各 >1e-6 N 成功，步尾终止；无需离开支撑面。旧协议和图网络默认值保持不变。新配置使用 45D flat（字段、底盘变换及尺度见 `env/observation.py`），双独立 critic、连续 SAC、只储存张量的 Replay；无 Cup 抓取候选。5 项奖励是位置势差、姿态势差、碰撞、首次成功、近目标闭爪。训练不作启动/周期验证，按最近 100 个健康非随机预热训练回报选择 best.pt；固定初态 20 次随机策略评估仅在正式训练完成后执行。

## 软件与物理验证（与策略评估分开）

- 完整依赖使用已有 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/actor-critic/.venv/bin/python`：torch 2.14.0+cu130、torch-geometric 2.7.0、MuJoCo 3.16.0，PYTHONPATH 指向本工作区 src。指定主 `.venv` 虽有 torch 2.7.1+cu128，却缺 mjlab / torch-geometric，因此其跳过测试或 ImportError 不计通过；不安装/下载。EGL 库路径 `/tmp/lmdf-egl-libs/root/usr/lib/x86_64-linux-gnu`。
- 专项离线 RL 回归 47 项通过，含 45D 每字段值/顺序/底盘坐标/尺度、flat 模式采样回放 SAC 重载、图模式回归、五奖励和同子步接触边界。整个 station_map 软件回归 79 项通过。全仓离线测试 354 项中 9 项 ERROR、1 项 FAIL、3 项 SKIP；错误/失败均在未改动的规划模块，所用 actor-critic RL 环境缺 cuRobo/rtree（主 .venv 有 cuRobo/rtree，但缺 RL mjlab/torch-geometric），因此不宣称全仓测试全绿；79 项站位专题测试无跳过全通过。
- 真实 scene 编译和 1s 静置：原 Cup XY=(7.31345946,2.09881134)m，原桌面 z=0.7503371m，静置 cube 中心 z≈0.77027079m；S0246 XY=(6.613459458748234,2.5988113404423916)m、yaw≈-0.62024949rad 合法且无 home 穿透。3 次 GPU reset 逐位复现 cube qpos [7.313459396,2.098811388,0.770270765,1,0,0,0]、站位 [6.613459587,2.598811388,-0.62024951]，初始两指力为 0。
- 真 GPU0 单环境用现有 7D DLS 以 cube **中心**作最终目标（含预抓取阶段，不改最终目标），第 81 个策略步/4.05s，目标中心距离 0.0112687m、姿态差 0.00031427rad，同一原生物理子步两指力分别 7.09356N/7.57618N；success、terminated=True，单步 reward=50.0286，collision_free=True，collision_time=0，failed/unhealthy=False。此为物理/DLS 可接触性证明，**不是训练策略成功**。

## 训练与策略评估

真实物理 GPU1 128 环境短验证在 `/outputs/experiments/scratch/rl-cube-contact/runs/20261116-short-gpu1/` 完成 1024 健康 transition、预热128、7次 SAC 更新，batch256、Replay1024已入库，13.60s 采集+更新（稳定尾段约78.73 transition/s，包含采样与更新；启动编译另计），未作任何自动评估；正式吞吐会随 episode 重置和状态演化变化，粗略按 70–80/s 估算 1e6 约 3.5–4.0h，不视作保证。待固定提交后填入正式 run；正式训练为 1,000,000 条健康 transition、种子 42、单卡 128 环境、20,000 预热、batch256、Replay100000、每128条更新约7656次。训练结果与 best.pt 的 20 次随机策略评估另行补入；不设通过线，不声称跨初态泛化。

## 2026-11-16T21:18:41+08:00 训练完成与代表视频

此前各节为启动前记录；本节补充实际四卡训练和执行验证。

- 正式 run：`outputs/experiments/rl-cube-contact-s0246/runs/20261116-204926-s0246-cube-contact-1m-g0123`；源码未改动，实际全局采样1,000,000条、更新7,656次，采样与更新882.86s。
- best.pt由最近训练回报选择，实际位于370,048 transitions、2,734 updates；不是最终模型。
- GPU0独立加载best.pt，seed20261109，20个并行环境同一固定初态，每个执行一次随机策略；健康20/20、成功1/20（5%）。并行随机数流不同于顺序seed+i评估，不能混为同一试验。样本较小，不推断稳定成功率或跨初态泛化。
- 代表样例按各类别回报排序选中间样本：成功lane06，19.75s，return52.29，无碰撞；无碰撞超时lane09，50s，return0.55；伴随碰撞的超时lane16，50s，return-39.23。最后一类仅描述伴随碰撞，不宣称碰撞是唯一失败根因。
- 已验证3个MP4可解码，20fps，双视角（全景+夹爪近景），抽查中间和末帧；成功视频19.8s，失败各50.05s（含初始帧）。成功依据物理接触协议，不要求抬升；画面CONTACT表示瞬时接触力，终止成功依据同子步锁存事件，两者可在步尾不同。
- 原始结果、奖励分解、视频、状态npz、执行脚本和日志：上述run下`representative-videos/`；未修改训练代码或checkpoint。训练末段成功率不能代替best.pt评估结果。

## 2026-11-16T22:29:15+08:00 后续任务调整

本报告保留原 `rl-pick-contact-v1` 的45维输入、接触即成功和旧训练结果。当前任务已改为65维输入、双指接触且相对抬升0.0086 m，七项奖励与正值闭爪动作，详见[v2交付记录](20-cube-contact-lift-v2.md)；历史5%评估结果不代表新任务表现。
