# Case 工厂首批实施报告

**已建立新分支并完成首批代码，但第 0–8 阶段没有全部完成。当前 L2 任务为 0。**
抓取生成是当前首要阻断：真实资产共筛选 480 个几何候选，仿真通过数为 0；
val-103 原始场景也未通过默认静置检查。没有通过放宽物理或机制条件增加产出。

- 分支：`feat/case-factory-restructure`；起点：`main` / `0ffded1dc15cc25808e0a3954c773b1b77c6478a`。
- 开始时工作区干净；实现保留在此分支的未提交改动中，未提交或合并 Git 历史。
- 依据：[重构提议](pipeline-restructure-proposal.md)、[Case 定义](case-definition-proposal.md)、[实施计划](case-factory-implementation-plan.md)。
- 用户追加要求：第 5 阶段先放宽复核，不要求金标准样本。已落实；不宣称已经完成错误率校准。

## 执行过程与代码结果

先写规格、比较、复核和基础算法测试，再实现领域模块和薄 CLI，随后运行离线回归与真实资产/GPU smoke。
旧 `case-edit` 的 Agent 层未增加功能；不导入旧工程，不修改外部资产、原始 XML、历史结果或历史标签。

| 实施阶段 | 本次实现 | 验收状态 / 缺口 |
|---|---|---|
| 0 准备与冻结 | 五份 CaseSpec（含 case1-S）、严格加载、能力配置、任务记录/校验、新文档入口 | 规格与回归通过；这是已完成的首批基础 |
| 1 离线基础 | 原生底盘/夹爪几何测量；IK 接口和 reach-table 查询格式；支撑/目标索引 API；点对/方向/深度采样、孤立夹爪过滤、版本缓存 | 真实几何测量和 IK smoke 通过；可达范围表未标定，train/THOR 全量索引未完成，抓取验收未通过 |
| 2 不编辑 + case1 | 局部边采样、逆向选 S0、公平 C0/C-yaw/C1、四段规划、保守地面 A*、独立 strict-pick-v3/audit 接口、漏斗/冻结、硬期限 | 合成证据链测试通过；原生完整配对/严格执行尚未验证；val-103 静置失败；至少 5 个 L2 目标未达到 |
| 3 case2/3 | 把手人工确认、杯身对照、去杂物及回滚等判定门禁 | 仅纯逻辑回归；资产语义标注、把手子集及原生反事实事务未接通 |
| 4 编辑分支 | N 优先、失败转 E 的编排接口和明确的 pending 记录 | 原生配方/编辑事务、地面带余量与局部重算尚未接通；没有 case1.5 L2 |
| 5 合理性复核 | P1–P6 合同、程序汇总、宽松配置、一次调用、去标签输入、不编辑分支固定相机/分割框编号、unknown 人工队列 | 逻辑回归通过；没有真实服务/固定相机整体验收；编辑前后配对图未接通；金标准/校准按用户要求暂缓 |
| 6–8 | 地面路径几何为后续导航提供基础 | 连续导航跟踪/到站抓取、调度续跑/导出、train 全量运行、Agent A/B 均未完成 |

核心代码分别位于 `construction/case_spec.py`、`grasping/`、`robots/capability.py`、
`planning/reach.py`、`stations/start_selection.py`、`validation/comparison.py`、
`workflows/factory_backend.py`、`workflows/case_factory.py` 和 `agents/plausibility_reviewer.py`。
运行与限制见 [case-factory.md](../../docs/case-factory.md)。

### 不可绕过的边界

- C0、C-yaw、C1 绑定相同场景、目标、抓取集合、双臂、躯干采样、种子与预算；未知不当作无解。
- case1 要求起始整条边的合法采样在预算内够不着；同边平移另存 case1-S，不计入 case1 配额。
- 缺少 yaw/整边覆盖、成功区域不足、地面路径未知或必要反事实时不能通过。
- L1 不冒充 L2。L2 需要双方严格 attempt 的有效审计及 C1 的真实成功。
  C0 的预算内规划无解不伪造物理失败、动作或视频。
- 工厂默认纯场景 train，调试仅 val-103。默认关闭 Agent。
- 当前启动前检查仿真通过的 RBY-1 缓存；无缓存返回 `dependencies_missing`，不回退到 DROID。
- 网格路径仅为构造检查，不是连续导航证据；所有任务的导航成功保持 unknown。

## 第 5 阶段的实际宽松规则

`configs/case_factory/plausibility.json` 使用 `relaxed_uncalibrated`：
仅 P1 在置信度 ≥ 0.98 且引用实际视图时阻断，P2–P6 先标记；
阻断项未知/低置信度失败、响应或渲染异常进入人工队列。每任务最多一次调用，无补图重试。
不提供规划、执行或比较成败标签。金标准及误杀率/漏检率验收暂缓，未声称这些指标合格。

这不改变原有静置、穿透、严格抓取或机制阈值。

## 验证结果

### 离线回归

基线为 158 项、8 项跳过；新增 34 项 Case 工厂测试。
最终回归为 **192 项：184 项通过、8 项跳过**；跳过项不是新增真实验收的替代。
测试日志：[factory-restructure-20261008-verified.txt](../../outputs/case_factory_checks/factory-restructure-20261008-verified.txt)。
覆盖公平性、未知/异常分类、完整四段规划、L2 审计门禁、反事实、宽松复核、调用上限、
孤立状态隔离、缓存不兼容、冻结/不覆盖、硬期限回收、局部边、路径、索引和 XML 符号链接资源根。
简化 fixture 只证明程序行为。

### 真实测量与 GPU IK

在用户指定的 CUDA 4 上显式运行；代码不硬编码 GPU。

| 检查 | 实际结果 | 能证明什么 |
|---|---|---|
| 原生底盘碰撞几何 | 保守低位包络约 0.703 × 0.607 m，旋转半径 0.444 m | 当前配置模型的几何量，不是标称硬件尺寸 |
| 原生夹爪碰撞几何 | 最大开口约 0.0993 m，手指宽 0.0327 m、长 0.0627 m | 原生模型测量，不使用 DROID 参数 |
| cuRobo IK | 实测 TCP 有解；10 m 偏移预算内无解 | IK 接口的原生 smoke；不是可达范围表标定、抓取或导航成功 |

证据：[能力测量](../../outputs/case_factory_preparation/capability-56a0b0cbba.json)、
[原生 IK smoke](../../outputs/planner_checks/factory-native-ik-20261008/result.json)。
几何值已写入 `configs/robots/rby1_capability.json`，带模型/夹爪摘要；`reach_table` 仍为 null。

### 真实抓取候选筛选：尚未通过

| 批次 | 几何候选数 | 孤立仿真通过数 | 主要结果 |
|---|---:|---:|---|
| Cup_30 初版 | 32 | 0 | 31 个接近碰撞，1 个夹持/抬升失败 |
| Cup_30 增加深度、多点对与接触中心校正 | 128 | 0 | 全部接近碰撞 |
| Apple_30 / Bottle_1 / Salt_Shaker_2 / Tomato_28 | 256 | 0 | 252 个接近碰撞；盐罐 4 个进入物理筛选但未保持双侧夹持/足够抬升 |
| Apple_30 支撑方向引导 | 64 | 0 | 全部接近时与物体碰撞 |

这些是相关候选，不是 480 个独立抓取任务；不据此报告任务成功率。
方向引导后 Apple 仍全部在夹爪–物体接近检查中失败，说明仅改变接近方向不足以闭合抓取生成：
下一步应核对原生夹爪接触坐标、碰撞几何与全物体外包络，而不是继续随机加预算或放宽碰撞条件。

证据：
[Cup_30 初版](../../outputs/case_factory_assets/grasps/Cup_30/result.json)、
[Cup_30 深度采样](../../outputs/case_factory_assets/grasp-diverse-depth-v1/Cup_30/result.json)、
[四类资产诊断](../../outputs/case_factory_assets/common-assets-filter-20261008/results.json)、
[Apple 方向引导](../../outputs/case_factory_assets/grasp-support-up-v1/Apple_30/result.json)。
失败候选与测量也保留，不能被工厂当作已验证抓取加载。
早期 CLI 发现的 XML 符号链接资源根问题已修复并补回归；未覆盖任何旧输出。

### val-103 原始场景 smoke：被物理检查阻断

原始场景静置 3 s 后仍有刀、勺、莴苣等动态物体未稳定；没有绕过该检查。
因此这一运行尚未进入真实站位配对，漏斗任务数和 L2 数均为 0。

证据：[漏斗与汇总](../../outputs/case_factory/factory-val103-r0-r3-20261007/summary.json)、
[静置失败细节](../../outputs/case_factory/factory-val103-r0-r3-20261007/failures.json)。
增加前置缓存门禁后的命令按预期返回
[dependencies_missing](../../outputs/case_factory/factory-val103-dependency-gate-20261008/summary.json)。
它是依赖未就绪，不是机器人抓取失败。

## 下一步与交付限制

1. **先解决 R1b 抓取生成。** 用一个可抓资产校准接触坐标与完整夹爪碰撞，再通过孤立筛选和真实场景 strict-pick-v3；当前不能宣称 R1b 完成。
2. 区分 val-103 源场景动态物体问题与候选目标合法性，保留硬检查；需要明确构造静置策略后再复跑，不能静默降低阈值。
3. 标定可达范围表、补齐场景/资产索引，在主链路通过后达到至少 5 个 L2 case1，再接通反事实、编辑、导航及批量发布。
4. 既有已接受 case1 样本保持原状，**按新定义尚未做机制检查**，本次不重新标注。

没有长时间 train 全量运行，没有金标准样本，也没有正式数据集交付。
本报告用状态/证据表区分实现和验收；没有可支持性能趋势或统计图的数据，故不添加装饰图。
运行输出保留在忽略的 `outputs/` 中，代码、配置、测试和此报告可提交；本次未自动提交。
