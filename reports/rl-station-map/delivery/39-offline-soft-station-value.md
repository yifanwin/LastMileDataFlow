# 方块站位离线软价值图

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T00:22:36.708549+08:00
- 替代：无
- 关联：[随机站位69D输入](36-random-stations-physical-base.md)、[800万训练](37-random-stations-8m-training-launch.md)、[运行指南](running.md)

## 最终实现

正式打分直接读取现有任务YAML和checkpoint，完全绕过MuJoCo及项目仿真模块导入，不加载场景/模型、不创建环境、不执行reset或静置。没有模板类、模板文件参数、模板校验管理或仿真fallback。先前诊断run的NPZ仅保留为验证证据，不再是运行输入。

已核对的固定初态数值直接记入`configs/station_map/rl/val103_cube_random_stations_facing.yaml`的observation：静置方块世界位姿、Home TCP相对底盘位姿、底盘高度、夹爪开度及归一化范围。关节Home、升降和方块尺寸继续取原scene配置；速度与上一动作置零。该任务初态固定，因此后续站位只需刚体变换，无需再算机器人FK。原场景标称方块高度比reset后高约66微米，正式值使用已核对的静置后高度，不把标称值当成训练初态。

训练`flat_state()`的坐标变换、误差和拼接提取为共享纯函数，保留65/69维顺序、旋转编码和归一化。训练环境仍读取动态物理状态，不使用配置中的静态值替代运行观测。原graph min-Q评分入口保持不变；新增`score.py --value-mode soft`直接走离线路径，物理GPU号仍由入口统一绑定，关闭渲染设备查询。

选择最接近730万条采样的`step-007200126.pt`：7,200,126 transitions、56,094次更新，距离目标99,874条；下一份7,600,126更远。冻结Actor、在线Q1/Q2和alpha均来自同一checkpoint；alpha=0.0064580244943499565。推理加载不构建target网络或优化器，不恢复训练随机状态，不执行反向传播。

## 初态与离线验证

一次性真实核对已经完成：一个GPU0参考环境，分别reset有效池中最小X、最大X、最大Y的三个不同站位。真实69维观测与数值构造的最大差如下；该过程不在正式入口保留。

| 站位 | 69维最大绝对差 |
| --- | ---: |
| S0057-y00 | 7.1525574e-7 |
| S0190-y00 | 2.0027161e-5 |
| S0232-y00 | 2.1934509e-5 |

三站逐维均满足atol=1e-4、rtol=1e-5。初次参考准备8.376秒，核对及保存共8.522秒，之后关闭环境。原始观测、差值与字段名保留在首次诊断run。最终直接读配置构造的96站观测与此前已验证数组逐元素一致，且三个站位再次对照原真实观测通过，不重复启动仿真。

最终独立验证使用导入拦截器，禁止导入mujoco、lastmile_dataflow.sim及RL场景/环境模块，并禁止创建Adam。在此条件下，仍完成96站观测及全部12,288个动作推理，证明评分没有暗中加载仿真。批次改为1023（含尾批）后，动作/log_prob/Q均在1e-5容差内一致，最大Q差7.6294e-6。

## 价值计算与结果

每站固定初始观测，从原Actor随机采样128个tanh动作及带Jacobian修正的log_prob，计算`mean(min(Q1,Q2)-alpha*log_prob)`。动作使用训练时归一化坐标。预生成独立Gaussian噪声，样本身份与批次划分无关。单GPU0，batch size=2048，seed=20261109。

直接使用checkpoint内233个候选及96个有效标记，不重复检查站位合法性。96站全部有分数；136个Home碰撞点和1个目标中心无朝向点保留空值。价值范围25.820710–32.975715，平均29.761219。最高为S0078-y00，世界(x,y,yaw)=(6.918997,1.901580,0.463648)，价值32.975715、平均min-Q=33.143297、平均熵价值项=-0.167582、均值标准误0.002380。

连续动作密度可以大于1，因此log_prob可为正，本次平均熵价值项为负并非公式符号错误。标准差使用ddof=1，标准误为std/sqrt(128)，只描述动作采样差异，不代表模型不确定性或真实成功率误差。

| 最终运行阶段 | 秒数 |
| --- | ---: |
| 冻结模型加载 | 0.225315 |
| 96站69维观测构造（含首次数学内核开销） | 0.384974 |
| 12,288动作的Actor和双Critic推理 | 0.032916 |
| score函数完整执行（含保存和绘图） | 0.825297 |

时间不包含Python进程及模块导入，不包含历史的一次性参考核对。最终评分没有仿真准备时间。

## 验证、版本与交付

- 最终station_map全部119项测试通过，8.764秒；`score.py --help`无初态模板参数，`git diff --check`通过。初版相关43项测试还覆盖了层级检查；不重复运行无关cuRobo全仓库测试。
- 从保存的动作、log_prob、Q1/Q2和alpha用NumPy独立复算全部站位均值及样本标准差，通过；价值均值最大绝对差1.2064e-7。96站各128个样本，所有观测及数值有限。
- 运行基线提交`eedce7954d906ce5eeff8228a96acf7b99744c62`，实际工作区dirty，包含本次代码与配置修改；原有36号报告改动、37/38号报告及环境链接保留。最终命令与输入引用记录于run的execution.json，不复制源码或权重。

最终run：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/runs/20261118-soft-config-7p2m-g0`，包含`station-value.json`、`samples.npz`、`verification.json`、`execution.json`和日志。配置的固定初态数值也记录在评分JSON中，支持独立复算。

最终分析：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/analysis/20261118-soft-config-7p2m-g0`，包含价值图和站位排序CSV，每个XY只代表正对方块的朝向，色阶使用实际价值范围。

首次三站原始验证：`/home/wenyifan/.codex/worktrees/rl-cube-random-stations/LastMileDataFlow/outputs/experiments/rl-cube-station-value/runs/20261118-soft-7p2m-g0/initial-state/observation-comparison.npz`；逐维误差CSV保留在同实验单元首次analysis中，只作诊断证据。

本次没有执行完整抓取episode；交付的是冻结策略的长期软价值估计图，没有把排序或高分宣称为实际抓取成功率验收。
