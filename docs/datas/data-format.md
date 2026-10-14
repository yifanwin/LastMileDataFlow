# 最小数据格式

主记录用 JSONL，实验配置只保存一次。没有封存清单、校验摘要、重复索引或兼容旧协议的读取分支。

| 阶段 | 主记录 | 内容 |
|---|---|---|
| scene-datasets | `scenes.jsonl` | 场景 ID、accepted/rejected、相对场景路径、任务、站位、障碍物放置 |
| 逐点标定 | `trials.jsonl` | 场景/任务/站位、试验号、种子、状态、原因、相对 rollout 路径 |
| 连续采集 | `episodes.jsonl` | S0、S1、导航和任务结论；没有可行路径时只保存负结果 |
| benchmark 定义 | `episodes.jsonl` | 场景、任务、困难起点、种子 |
| benchmark 执行 | `results.jsonl` | episode 对应结果、终止与记录完整性 |

`config.json` 保存有效参数、种子、机器人/相机、后端或策略配置。`summary.json` 和热图是可重建的阅读产物，不作为协议入口。复用标定读取原目录，不复制第二份 trials。

## 场景载入

`scene-files/<scene>/` 包含 `model.mjb`、`initial.npz`、机器人及载入配置；有构建 spec 时另存 `model.xml` 便于查看。
MJB 是实际载入来源，需使用生成时的 MuJoCo 版本。XML 不是跨版本自动回退契约。
实例映射只用于定位物体，不是审计 registry。原始外部资产只读。

## 实际执行轨迹

`rollouts/<id>/trajectory.npz`：`time_s/qpos/qvel/ctrl/state22` 为 N+1 个边界状态，`actions/requested_actions/phases` 为 N 个实际控制间隔。没有真实物理步就不伪造轨迹行。
`recording.json` 记录是否完整、步数和错误；事件用于诊断。RGB 图片默认保留，视频、深度、逐物理步日志按需选择。
没有 canonical/raw 双份记录，也不保存独立 replay、final-state、outcome 或物理日志派生索引。

机器人 20D 动作：底盘 `[0:3]`、左臂 `[3:10]`、左夹爪 `[10]`、右臂 `[11:18]`、右夹爪 `[18]`、躯干 `[19]`。
底盘/手臂是增量，夹爪/躯干是绝对目标；机器人配置决定控制模式和合法范围。

## 结果含义

真实失败、规划无解、超时、初始化/记录/基础设施错误分开记录。逐点成败率只计算已完成的可评测试验；试验数未满不用于困难起点选择。导航成功不等于操作成功；记录完整也不等于任务成功。

MolmoBot 转换按需生成 `data.npz`，引用原图像。非原生控制模式的夹爪动作需要显式标定；不能直接声称此简化导出是完整的上游训练格式。

## 路径与环境

用户配置由各配置加载器明确解析：data_root 显式设置，数据路径相对 data_root，配置与解释器路径相对 repo root 或写绝对路径。保存的 scene/run 配置不做通用字段扫描或目录重定位；普通主记录用 `io.rows()` 读取。
移动输入/输出树时保持相对关系，并重新安装环境；MJB 只承诺同版本恢复，不把导出的 XML 当作已验证的独立资产包。
