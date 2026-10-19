# π0.5 双本体构建与验证

状态：done  
日期：2026-10-18  
替代：无  
关联：[接入需求](../requirement/01-pi05-integration.md)、[构建方案](../plan/01-pi05-integration-build.md)、[运行指南](../docs/01-running.md)

**首轮构建完成：两份真权重均可推理，两本体均完成短闭环执行；Panda 路线 B 固定抓取与 A* 绕障通过，RBY-1 的 48 条示教已转换并生成本体统计。** 两次 π0.5 短 rollout 均未完成抓取；尚未进行微调或正式困难 S0 批量评测。

## 实现内容

模型代码位于 `policies/pi05/`，只移入必要的 OpenPI JAX 模型与变换，保留许可。主进程用项目 `.venv`，模型用 `.venv-pi05`，安装依赖由 `pyproject.toml` 声明；模型启动不读取参考仓库。两本体复用权重恢复、tokenizer、图像处理、50 步采样与本地 IPC，默认每 5 步重新预测；episode 切换清空缓存并重置随机状态。

Panda 新增显式 `robocasa-v1`：三相机、state16、action12、20 Hz / 2 ms、OSC、底盘 legacy velocity、躯干 delta position 与夹爪语义。原 position profile 的默认配置未迁移。示教与策略共用 `PandaRoboCasaAdapter`：

```text
cuRobo → scratch FK → TCP/底盘反馈跟踪 → RoboCasa action12 ─┐
π0.5 → 反归一化与动作块 ───────────────→ RoboCasa action12 ─┴→ 同一 controller → 物理执行
```

参考 FK 不写 live qpos，控制器零空间始终指向初始化关节位置。每个物理步重新计算控制，记录实际 actuator ctrl。共享 rollout 从 `runtime/execution/molmobot.py` 改为 `vla.py`，按本体选观测与执行，不复制任务判定和记录器。`evaluate-stations`、`evaluate-bench` 支持策略配置选择 π0.5；连续采集仍仅 cuRobo。

## 已完成的物理与模型检查

下列目录均相对项目根 `outputs/diagnostics/pi05/`，原始 JSON、图像、视频与轨迹保留在运行产物中。

| 检查 | 实测结果 | 证据目录与边界 |
|---|---|---|
| Panda 真权重恢复与采样 | 恢复约 216.2 s，首次采样 13.39 s，有限值 `50×12` | `panda-model-03/`；合成零图像/状态，只证明模型接口 |
| Controller 对照 robosuite | 3 组 achieved/desired 探针，目标位置误差 0，最大力矩误差约 `6.49e-8` | `reference-02/`；开发期读取参考实现，生产无此依赖 |
| 保持与 10 mm TCP 命令 | 保持关节漂移约 `8.66e-11` rad；实际位移 `[10.097,-0.078,0.339]` mm | `control-02/`；空场景控制检查 |
| 底盘平移 15 cm、侧移 8 cm、转向 0.2 rad | 摩擦补偿后 5 s 的底盘 XY 误差约 0.71 mm；TCP 误差 0.682 mm / 0.000961 rad | `mobile-tracking-02/`；补偿前 TCP 误差约 6.53 mm |
| A* 绕障、转向与保持 | 2.96 m 路径，333 步，到站误差 0.685 mm / 0.000391 rad，手臂关节漂移 0.00352 rad，禁止接触计数 0 | `robocasa-navigation-02/`；独立导航，不是 S0→S1→抓取全流程 |
| 路线 B 固定 Cup 抓取 | **任务成功**，抬升约 97.0 mm，末端持有 3.512 s，8450 个物理采样 | `route-b-pick-02/`；补偿后复测，真实 cuRobo、FK 跟踪与共用 controller |
| 三相机与 state16 | 实际渲染，移除碰撞网格/调试 site 的可视遮挡 | `observation-02/`；只改可视，保留碰撞 |
| Panda π0.5 短 rollout | 100 步 / 5 s，20 次动作块，2500 次物理采样；记录完整，**抓取未成功** | `panda-rollout-03/`；固定未标定站位，不能估计成功率 |
| RBY-1 真权重与本体统计 | 真实示教 RGB/state22，恢复约 183.5 s，首次采样 13.48 s，有限值 `50×20` | `rby1-model-01/`；不是 RBY-1 微调权重 |
| RBY-1 π0.5 短 rollout | 100 步 / 5 s，1250 次物理采样；记录完整，**抓取未成功** | `rby1-rollout-02/`；12 步触发协议限幅，原值保留 |

固定抓取使用原 val103 Cup_30、底盘 `[6.55,2.0988,0]`、lift=0.2 m、初始夹爪宽度 0.08 m。它不同于 RoboCasa 默认 lift=0 的启动姿态；此单例不能证明默认 profile 应全量迁移，也不能替代导航、移动抓取、多任务与困难 S0 评测。

Panda 策略短 rollout 的 state/actions/ctrl 全部有限，shape 分别为 `101×16` / `100×12` / `101×13`，已保存三路机器人视频与第三人称视频。预热后单次动作块采样约 0.07 s，100 步约 47.9 s 墙钟时间，包含逐步渲染和记录，不是实机实时性测试。该 5 s 片段没有有效持物，不据此断言权重没有抓取能力。

RBY-1 短 rollout 同样保存三路机器人视频与第三人称视频；state22/actions/ctrl 分别为 `101×22` / `100×20` / `101×27`，全部有限，约 31.1 s 墙钟时间。该次诊断使用 256×256 渲染；生产 RBY 配置与训练源为 320×240，smoke 的 RBY 默认分辨率现也采用 320×240。此结果只支持物理执行链路接通，未证明 RBY 操作技能或跨分辨率任务表现。

## RBY-1 示教、转换与统计

指定 `refactor-light/outputs` 没有 `kind=materialized_lastmile` 或引用型 lastmile dataset。实际定位到两份带 RGB 的 position 标定 run：`stations` 中 30 条、`station_evaluations/small3/curobo_v2_001` 中 18 条完整成功示教。使用原 trial 成功与 recording 完整条件选择，共 48 条；源数据只读，没有重采、改标签或将失败轨迹纳入训练。

先生成引用型训练视图，再复用 `materialize_lastmile` 物化，随后用 LeRobot 0.3.3 写 v2.1 图像 parquet。state22 与执行 actions20 逐步配对，保留 head/left wrist/right wrist RGB、task、episode 来源。原 20 Hz 示教的物理间隔为 48/52 ms；原始时间留在物化轨迹，LeRobot 使用名义 20 Hz 索引。

统计覆盖 **13,167 条训练行**，每行计算一次，提供 mean/std 与精确 q01/q99，按 OpenPI JSON 保存到 `outputs/training/pi05-rby1-position/lerobot/assets/norm_stats.json`，并由同目录 `data_config.json` 引用。`Rby1DataConfig` 读取器返回 OpenPI 模型变换前的字典、32D 状态与 `50×32` 动作；保留 episode 内末端补齐标记，不二次差分动作。

实际读取首尾样本已通过：三相机均为 `240×320×3`，state/actions 有限，补齐维为零，最后一个样本的未来 49 步标记为 padding。证据为 `outputs/diagnostics/pi05/rby1-data-01/result.json`。原文 task 为中文；DataConfig 保留原文并保存 `task_prompts` 映射，训练读取与 rollout 共用英文任务生成函数，避免提示词约定分离。

数据集中于 val103 的单一 Cup pick 任务，全部作为训练行，没有将同任务数据拆成一个貌似独立的泛化评测集。基础权重没有经过 RBY-1 微调；拥有本体统计不等于已学会 RBY-1 操作。

## 实际问题与修复

Panda 模型首次 GPU JIT 曾两次原生崩溃。通过 faulthandler、gdb 和独立矩阵运算，将问题定位到宿主 `LD_LIBRARY_PATH` 中 CUDA 12.8 库覆盖 pip JAX CUDA 库；去除该变量后真权重采样成功，已加入 π0.5 子进程环境处理。

第一次短 rollout 的固定站位 fixture 缺少视频叠图所需的 `xy`，在物理执行前终止；第二次执行一个物理步后发现共享遥测读取了 RBY-1 的 `torso` 字段。已补齐 fixture，并对 Panda 记录 lift 跟踪米制误差，RBY-1 保留躯干角度误差。这两次为程序错误，不能计作策略任务失败。数据读取测试曾因默认 Hugging Face 缓存位于只读 HOME 报错，已将数据缓存放到输出目录的 dependencies 下。

首次原厂 profile A* 导航因 `navigation_tracking_timeout` 失败。轨迹显示底盘低速指令落入摩擦死区：原厂平移 frictionloss=250、速度增益=1000，死区约 0.25 m/s，高于默认导航速度 0.2 m/s。参考动作跟踪现按原厂参数补偿摩擦，在 1 mm / 0.001 rad 内停止补偿；仍输出同一种 RoboCasa action，不改 controller、执行器或 π0.5 动作。复测导航通过。

RBY-1 base 首次实际预测左夹爪约 -0.05199 m，超出 position 协议下限 -0.05 m，执行前被拒绝。π0.5 的 RBY 输出现在显式限制到底盘/手臂增量上限与夹爪/躯干协议范围，然后沿用现有 live joint/actuator 校验；请求原值与实际提交值分别记录，不改原始训练数据。

## 软件验证

最终离线回归已通过 **237 项：236 项通过，1 项跳过**，日志为 `outputs/logs/pi05-all-tests-final.log`。包含原工作流、MolmoBot 生命周期与新增动作块/episode 切换、LeRobot 时间对齐/归一化/末端补齐检查。真实 GPU 验证独立运行，不随 unittest 自动执行；MolmoBot 本轮没有重跑真权重评测。安装脚本语法、修改文件空白与报告链接检查通过。

## 尚未验收的范围

正式 RBY-1 微调、批量 zero-shot 成功率、open 任务、完整路线 B 的 S0→S1→移动抓取评测，以及真实机器人部署尚未完成。已有 Panda position 场景快照需要按 RoboCasa profile 重建后才能用于新策略；不将旧 position benchmark 静默改造成新 controller 输入。五命令已接入后端分派，本轮未新增困难 S0 benchmark 或批量重标定。
