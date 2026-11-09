# 轨迹级质量评分：实现、离线验收与 GPU 阻塞

- 状态：superseded（当次代码与离线交付完成；后续 GPU 验收见 04）
- 日期：2026-11-06
- 替代：无；历史静态 IK 结果保持原含义
- 关联：[需求](../requirement/02-feasible-trajectory-quality.md)、[方案](../plan/02-feasible-trajectory-quality.md)、[接口核对](../analyze/03-trajectory-interface-check.md)

后续已通过沙箱外访问恢复 GPU 4，最新优化、实测和剩余验收见[性能交付](04-trajectory-performance.md)。下文保留当次会话的历史结果。

本次直接实现了轨迹候选、独立验证、瓶颈评分、主链记录与展示，并完成相关测试和全套离线回归。**整体 P0–P6 未完成**：当前 CLI 会话看不到可用 CUDA 设备，真实入口实际报 `PlannerUnavailable: V2 requires CUDA`，无法完成 P0 GPU 验收，也没有通过 P5 或运行 P6 全图。没有将历史静态图、CPU 检查或 mock 当作真实轨迹验收。

## 实际改动

| 位置 | 改动 |
|---|---|
| `config.py`、原 base-map JSON | 物理范围归一化距离 0.05、最多 4 代表、每代表两初值、最多保留 4、接近/验证/采样参数；删除旧最大关节差去重配置 |
| `planning/trajectory_quality.py` | 稳定最远点代表选择、直径受限终态区域、历史端点适配与扰动初值、分段多项式曲线及解析极值、归一化弧长 16→32 点自适应双瓶颈、阶段与瓶颈记录、正式比例与排名 |
| `planning/trajectory_validation.py` | 独立 MuJoCo FK/几何检查、初态和终态检查、曲线位置/速度/加速度极值、整段离散细分、最终连续接近段识别；完整硬碰撞集合与软评分集合分离 |
| `planning/curobo/planner.py`、`costs.py`、`context.py` | 每初值一个 lane、`num_seeds=return_seeds=1`、固定 Pose-goal 与显式初值；末段接近软代价、目标接触窗口；原生实际有效长度及结点 q/v/a 解码，不把控制点当执行轨迹 |
| `workflows/station_map.py` | 按行/递增 yaw 确定性处理；先验初态，再筛 IK、规划、独立验证和评分；全部有效轨迹评分后留 4；有界邻行缓存保存全部有效实际终态/路径，旧 yaw 结果只作初值 |
| `station_map_plot.py`、`quality_map.py` | 保持主分地图和有效 yaw 均值；增加 R_plan 图、主辅分/阶段/实际终态区域详情、绑定查询与候选的 TCP 评分点路径预览；仍可读取旧静态 kind |
| README、运行/架构/格式文档、配置和测试说明 | 同步当前语义、参数、读取方法及尚未完成的真实验收 |

仍只有原五个 CLI。新输出 `kind=base-trajectory-quality-map-v1`；旧 `base-quality-map-v1` 不改写含义，两者均不能当物理标定传给采集或 benchmark。

候选块使用不依赖 pickle 的 NPZ：JSON 字符串保存逐查询/候选摘要，拼接数值数组与 offsets 保存最多 4 条获选曲线的 time/q/v/a。`best_q` 为赢家实际终态；R_plan 在保留前计算，不完整查询无正式比例。保存主辅瓶颈、样本编号、q/TCP/t/s/u、阶段、每轮最低值、接近边界及验证点数。辅助分不参与排名。

最小有效接近长度首试值设为 **0.01 m**，与 **0.10 m 软目标**分别配置，尚未经过真实轨迹标定。必要目标接触仅允许实际 pad 几何与目标在终态窗口内接触；其余碰撞只留 1e-6 m 数值容差，地面底盘支撑沿用既有独立许可。

## 阶段验收状态

| 阶段 | 结果与边界 |
|---|---|
| P0 | 独立环境、0.8.0 版本和真实 MuJoCo 模型检查完成；GPU 自由终态对照、实际 lane/曲线运行未完成，不能判 P0 通过 |
| P1 | 算法和记录结构离线验收通过 |
| P2 | 多候选、接近代价与热启动已实现；CPU 接口/梯度检查通过，真实 GPU 优化未验收 |
| P3 | 几何、运动、接近和评分软件验收通过，真实场景初态/历史终态检查通过；没有新生成轨迹的模型通过证据 |
| P4 | 已接入原 CLI、记录与展示，直接相关文档已更新；最终全套离线回归通过 |
| P5 | 未完成：没有三 yaw 的 GPU 规划、单/多 grasp 对照、密评分或冷/热同预算对照 |
| P6 | 未运行：没有新轨迹全图、真实完整候选记录或真实获选路径预览，不能用旧静态结果替代 |

本次没有物理 stepping、闭爪抓取或控制跟踪实验。

## 环境与测试

在本 worktree 新建 `.venv`，项目 editable 安装指向当前源码；只将父环境的第三方依赖目录加入路径，不执行其 editable `.pth`。启动检查确认正常导入来自 base-map，真实入口检查确认导入来自本次冻结 `src/src`，均未运行 main 源码。

依赖为 Python 3.11.15、MuJoCo 3.5.0、PyTorch 2.7.1+cu128、cuRobo 0.8.0。首次 EGL import 因公共 GL 库缺失失败；在 `.venv/gl-libs/` 内解包 Ubuntu 的 libegl1/libgl1/libglx0/libglvnd0 后修复，未修改系统。默认 apt 镜像下载曾返回 502，改用 Ubuntu HTTPS 包源成功。默认 uv cache 只读，改用工作区 `.venv/uv-cache`。

```bash
export LD_LIBRARY_PATH="$PWD/.venv/gl-libs/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl

.venv/bin/python -m unittest \
  tests.planning.test_trajectory_quality tests.planning.test_trajectory_validation \
  tests.planning.test_trajectory_adapter tests.workflows.test_station_map \
  tests.workflows.test_cli -q

.venv/bin/python -m unittest discover -s tests -t . -v
```

- 先跑既有相关测试，再跑新增局部验收；最终相关测试 **41 项通过**。
- 最终全套回归 **428 项，426 通过、2 跳过，34.030 s**。
- `git diff --check` 通过，实际 `evaluate-stations --help` 已核对。
- HTML/PNG 生成与新增诊断字段有离线测试；没有浏览器交互验收。附加尝试 `node --check` 因 Node 未安装未执行，不计为通过。

关键例包括真实简化 MuJoCo 段内碰撞反例（端点均合法）、跨 yaw 重新检查、错误最终接近、短于软目标但超过独立最小长度、结点间速度极值、原生曲线内部与导数重建、共同起点瓶颈、长路径高质量胜出、辅助区间边界/单调性、同终态区域不同路径仍可入选、8 尝试保留 4 的原分母、未完成查询空比例及无 pickle 记录。首次新增 fixture 错误已修复并重跑，最终测试无失败。

## 真实入口与无法自行解除的阻塞

实验单元为 `outputs/experiments/trajectory-quality-20261106/`，未删除、移动旧实验或资产。

真实检查前尝试 `git add README.md configs/execution docs src tests`，实际失败：

```text
fatal: Unable to create '.../.git/worktrees/base-map/index.lock': Read-only file system
```

因此未能提交，Git index 未改变。依 outputs 规则第 3 节备用方式，以 **2d20a1d 为基准、未提交工作区副本**冻结源码，同时保存 working-tree.diff/status。冻结源没有替换，main 源码未改；该环境不能满足“先提交”的首选要求。P0 结束后仅在 worktree 补齐样本编号、逐查询耗时、IK 不完整状态，并修正热启动为全部有效候选缓存；最终离线回归覆盖这些修正。冻结副本保留实际失败运行版本，未宣称它包含最终补充。

实际入口命令保存在单元 `logs/p0-launch.sh`；它从冻结源码运行原 `evaluate-stations`，取旧静态图已知有解的 xy（相对中心 dx=0、dy=0.6）、偏角 −60°，保留原五 grasp。显式 `--gpu 0` 仅用于设备入口诊断，**未能根据实时资源选择空闲卡**。

入口成功载入 val103/Cup_30，1000 条来源选出 5 个 grasp，构造 1×1×1 查询网格；随后在创建 V2Planner 时实际报：

```text
lastmile_dataflow.planning.curobo.planner.PlannerUnavailable: V2 requires CUDA
```

随后再次检查：`nvidia-smi` 退出码 9；PyTorch `cuda_available=false, device_count=0`；实际 CUDA 分配报 `No CUDA GPUs are available`；`cuInit=100`。`/proc/driver/nvidia/gpus` 能看到 8 个设备记录，但 `/dev` 没有 nvidia0–7 或 nvidiactl。问题不在工作区代码或父环境 editable 绑定；本会话无法修改外部设备映射/宿主驱动，也没有干预其他进程。

另外从冻结源码完成真实 MuJoCo CPU 检查：**1517 个 geom**，两块指定 pad 存在；该 xy 的 −60°/−45°/−30° 三 yaw 固定初态均通过严格检查；旧静态 g2 赢家的终态通过新终态检查。这只验证模型与接口前置条件，没有轨迹规划或轨迹成功率含义。

单元保留 `logs/p0-cli.log`、`logs/p0-model*.log`、`analysis/environment.json`、`analysis/p0-model.json`、最终测试日志、输入与冻结源码。模型检查脚本首次生成的换行语法错误已修正，重跑成功；两次日志均保留。所有检查进程已经结束，没有只启动后台任务后交付。

## 剩余验收与限制

外部 GPU 设备可用性解除前，P0 的可控冗余目标自由终态验证、真实各 lane/曲线检查、P5 的三 yaw 与多 grasp 规划及规定对照、P6 的 val103/Cup_30 全图均为**未完成**。没有产生这些阶段的合格产物，不能声明整体完成。

即便软件通过，有限候选不保证分支/路径穷尽，0.05 只表示构型区域，32 点只给出采样瓶颈；5 ms/0.01 rad/近障 2 mm 的整段离散模型复核不等于连续无碰撞证明，模型通过不等于真实抓取成功。真实 GPU 稳定性、全图耗时和质量排名仍未测得。
