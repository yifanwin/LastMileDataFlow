# 锅铲不动、盐罐换侧：val-103 的 case1 场景编辑完整解读

更新：2026-10-07。本文解读 **construct-29365f8d6330 / sample_000000**，依据该轮实际记录，不是另编的演示。

本例保留锅铲目标与台面，将盐罐移到目标另一侧，并采样约 **+33.41° 的相对旋转**。静置后盐罐的水平位移约 **0.802 m**。规则和配对图片检查通过，得到 **1个构造接受样本**。

**接受的是可见布局变化，不是“底盘移动后抓取更好”。** 机器人确实被加载、采样和放置，但没有执行底盘移动、机械臂规划或抓取。这个样本还不能证明必须移动、某侧不可达或重定位收益。

[新增：逐模块输入输出与Agent1模板详解](#module-io)。

## 1. 先看图：改变的是障碍物位置，不是把目标移远

| 编辑前 head | 编辑后 head |
|---|---|
| ![case1 编辑前 head](../../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/before_head.png) | ![case1 编辑后 head](../../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/after_head.png) |

图中央黑柄白头的是锅铲目标。橙色顶盖、透明瓶身的是本轮记录为 `SaltShaker / Salt_Shaker_2` 的盐罐：从左侧移到右后侧。画面中部另一只白色小罐不是此次编辑对象。

目标没有收到移动命令；盐罐相对目标换侧。这与“仅把目标放得更远”不同。但**障碍换侧是否足以迫使机器人换站位，仍未验证**。

## 2. 四个 Agent 与程序怎样接力？

本例是四角色流程，不能沿用旧示例的三个 Agent 编号。

| 参与者 | 本例输入与工作 | 实际输出 |
|---|---|---|
| Agent1：整理需求 | 原始抽象描述；不选具体物体 | 抽象模板、可见检查、待验证任务假设 |
| 场景与局部程序 | 原始 val-103；建立完整场景图，再裁剪信息 | 目标/支撑候选，附近墙体、家具和物体 |
| Agent2：选编辑方向 | 模板、局部候选、资产类别 | 选锅铲台面，建议移动和旋转已有障碍 |
| 初态与观察程序 | 选定目标、真实机器人 | 3个合法可见初态、主初态S0、head和辅助观察 |
| Agent3：写具体提议 | 模板、局部图、计划、真实图片 | 带采样范围的Symbolic DSL |
| 编辑程序 | DSL、种子、未编辑基准 | 确定位姿、实际编辑、静置、规则检查及后图 |
| Agent4：对图检查 | 同相机前后图、前后图结构、规则结果 | 可见布局pass；移动收益单独记为辅助意见 |

```text
需求 → 模板 → 原始场景图与局部候选 → 选锅铲台面
     → 采样并放置机器人S0 → 观察 → 提议盐罐换侧
     → 采样位姿 → 独立编辑与静置 → 规则通过
     → 固定配对图 → 可见布局通过 → 保存可恢复场景
```

[本轮最终结果](../../outputs/case_construction/construct-29365f8d6330/result.json)。四次Agent调用均完成解析，分别是 normalizer、strategist、proposer、reviewer；没有把历史成功标签搬到本轮。

## 3. 人输入的只是现象，模板把可见变化与任务假设分开

原始需求是：

> 由于目标、支撑家具和周围障碍的几何布局，原来的接近或工作位置不利于抓取，需要通过底盘平移和转向调整到更合适的工作位置。请构造实际空间或接近方向的差异，不能仅把目标移得更远。

[原始请求](../../outputs/case_construction/construct-29365f8d6330/request.json)指定单场景 `val-103`、pick任务、`beneficial_reposition`（移动后更有利）和 `construction_only`（本轮只验收构造）。目标接受数1；最多2轮、24次样本尝试、20次Agent调用、900秒，单次Agent超时240秒，种子42。

Agent1生成四个抽象角色：target、support、obstacle、robot_station。模板没有填写物体名，也没有虚构机械臂可达距离。

- 硬条件：`supported_by($target, $support)=true`，在整组操作前和静置后检查。
- 可见检查：目标/支撑/障碍布局发生变化；不同方向有几何差异；目标保持同一对象并在支撑面上。
- 任务假设：底盘能否到达新位置、手臂能否绕障、抓取是否成功、重定位是否改善表现。

`parameters={}`：本轮没有模板数值阈值。**DSL中的采样范围仍由Agent3根据具体场景提出**，不等于用户给定的尺寸标准。

[Agent1原始调用](../../outputs/case_construction/construct-29365f8d6330/agents/0000-normalizer.json) → [实际Case Template](../../outputs/case_construction/construct-29365f8d6330/template.json)。

## 4. Agent2 为什么选这个台面？

Agent2选中锅铲所在局部上下文，半径 **2.5 m**，不是把整个多房间图直接交给后续Agent。完整物理场景仍保留；局部裁剪只减少输入信息。

| 抽象角色 | 实际对象 | 记录的类别/资产 |
|---|---|---|
| `$target` | `spatula_291e31b285474249721c8a7b1bac877a_1_0_2` | Spatula / Spatula_1 |
| `$support` | `countertop_aa26fd5b3d56251659034cbb8b20053d_1_0_2` | CounterTop / Countertop_C_10x4 |
| `$obstacle` | `saltshaker_4a6645cf2cdf6c8afa19b4998a0f4061_1_0_2` | SaltShaker / Salt_Shaker_2 |
| `$robot_station` | `station_start` | 实际初始化后的机器人基座参考位置 |

策略阶段还提出三个方向假设：`y_plus`靠墙、空间可能受限；`x_plus`可能不利于手臂操作；`y_minus`作为相对有利的候选方向。

**这些是世界坐标轴方向，不是图片左/右，也不是已标注的北/南。** 工作区域中心由支撑几何给出，只是搜索提示，不是合法站位证书。

本轮没有新增资产，`asset_requests=[]`。只编辑一个物体是本次提议的选择，**不是最小编辑规则**，目标和台面也没有获得不可编辑权限。

[Agent2方向选择](../../outputs/case_construction/construct-29365f8d6330/agents/0001-strategist.json) → [实际构造计划](../../outputs/case_construction/construct-29365f8d6330/contexts/round_000/construction_plan.json) → [局部场景图](../../outputs/case_construction/construct-29365f8d6330/contexts/round_000/local_graph.json)。

## 5. 实体机器人真的放置了吗？前后是否同一个位置？

程序实际找到3个合法、目标可见的初态，目标像素分别为 **170、209、1701**，选可见性最高的第13号候选。

```text
主初态S0：base = [6.486487, 3.069226, 1.958171]
                   x(m)       y(m)       yaw(rad)
head = [0, 0.6] rad
```

这些点由独立初始化分支试验，不是机器人导航过去的轨迹。导航可达性保持unknown。S0按可见性选择，没有程序证明它是一个困难站位；不能读成机器人已经在A失败、随后走到C成功。

编辑前后不重新采样机器人位置。配对记录中底盘组漂移为0，head相机位置与旋转矩阵漂移为0；目标前后均可见 **1701像素**。

[实际初态试验](../../outputs/case_construction/construct-29365f8d6330/contexts/round_000/initialization/initialization.json) → [固定配对与可见性检查](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/pair.json)。

## 6. Agent3 的DSL：局部范围与相对旋转怎样读？

实际采用 `obstacle_yplus_reposition`。下列是操作节选；对象绑定和完整提议见样本文件，不是可直接独立运行的完整配置。

```json
{
  "carry_supported": false,
  "op": "move",
  "search_space": {
    "region": "countertop_aa26fd5b3d56251659034cbb8b20053d_1_0_2:countertop_aa26fd5b3d56251659034cbb8b20053d_1_1_2_collision_2:top",
    "rotation": {
      "angle": {
        "range": [
          -0.6,
          0.6
        ]
      },
      "axis": [
        0,
        0,
        1
      ],
      "frame": "world"
    },
    "support": "$support",
    "xy": {
      "margin": 0.03,
      "mode": "uniform",
      "x": [
        -1.1,
        -0.45
      ],
      "y": [
        0.1,
        0.24
      ]
    }
  },
  "subject": "$obstacle"
}
```

逐项理解：

- `move`修改盐罐；`rotation`在同一次落点采样中组合朝向变化。
- `support`和`region`选择真实台面顶部区域，不是在空中自由放置。
- `xy`是支撑区域局部坐标下的采样约束，不是世界坐标，也不是机械臂可达范围；程序还要同时检查物体足迹能否落在支撑区域内。
- `margin=0.03`单位米；采样角范围`[-0.6,0.6]`单位弧度。
- 旋转是绕世界Z轴的**相对增量**，不是设置绝对yaw。
- 新增的after goal要求盐罐仍由台面支撑；目标支撑作为前后invariant。

[Agent3调用](../../outputs/case_construction/construct-29365f8d6330/agents/0002-proposer.json) → [完整Symbolic DSL与样本记录](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/sample.json)。

## 7. 从范围到确定操作：实际采到了什么？

种子42、全局样本索引0，采到：

```text
台面局部xy = [-0.4727955634, 0.2056391329] m
相对角增量 = +0.5830467036 rad ≈ +33.4061°
编译世界坐标 = [6.5240744366, 4.1482171329, 1.0387309713] m
```

四元数保存的是组合后的三维朝向，顺序为w、x、y、z，不是四个角。编译坐标与静置后的坐标不同，后者才是最终物理观测。

| 对象 | 编辑前世界坐标m | 静置后世界坐标m |
|---|---|---|
| 锅铲目标 | (6.161114, 3.866731, 0.947213) | (6.161114, 3.866731, 0.947213) |
| 盐罐障碍 | (5.851764, 3.709517, 1.036608) | (6.522233, 4.149743, 1.036628) |

盐罐约0.802m的水平位移由前后世界位置计算；锅铲在列出的精度下不变，仍有正常的微小数值演进。**采样角不是静置后重新测量的绝对朝向变化值。**

[实际Executable DSL](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/executable_edit.json) → [静置后完整场景图](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/graph_after.json)。

## 8. 规则通过了哪些？没有通过哪些“更强的说法”？

本次静置 **1.0秒仿真、250步**；有效、编辑对象稳定，无新增求解器警告，未报告严重穿透。稳定性作用域是被编辑的盐罐，不要求官方原房屋每个物体都完全静止。

检查覆盖角色、目标前后支撑、盐罐after支撑、编辑对象稳定、求解器健康、严重接触穿透和附近几何。记录也包含附近杯子的物理支撑检查。

[实际规则结果](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rule_checks.json)的阶段状态仍写`pending_review`：它是视觉检查前保存的规则记录，不是最终样本状态。最终状态应看`sample.json`与`result.json`，不能把规则文件的旧阶段状态当成最终未通过。

通过这些低成本检查不等于完整物理证明，更不等于存在无碰撞抓取轨迹。

## 9. 辅助图看见了什么？Agent4为什么接受？

| 编辑前 aux_000 | 编辑后 aux_000 |
|---|---|
| ![case1 编辑前辅助图](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/before_aux_000.png) | ![case1 编辑后辅助图](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/after_aux_000.png) |

辅助图保留房屋墙体，能看到机器人、台面和部分地面；同名相机前后固定。它不是机器人head输入，而是构造检查的额外证据。

Agent4对三项`layout:0/1/2`均返回pass，分别引用匹配的head或aux前后图：盐罐换侧可见、存在侧向布局差异、同一锅铲保持支撑。

收益意见另记`likely_beneficial`，优选`y_minus`；这只是建议后续测试的方向，不参与最终任务验证。部分背墙工作区域没有被辅助覆盖，不能将这4张图说成全部方向已验证。

[Agent4调用](../../outputs/case_construction/construct-29365f8d6330/agents/0003-reviewer.json) → [正式可见检查](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/semantic_review.json)。

## 10. 保存了什么，结论停在哪里？

样本最终状态为 **`construction_accepted`**；本轮1次尝试、接受1个。[接受场景目录](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/scene)包含`model.mjb`、`initial.npz`、`instances.json`、`version.json`和`checksums.json`，不只是截图。

| 结论层 | 本轮结果 | 不应扩大为 |
|---|---|---|
| 场景与构造 | valid / pass | 完整物理安全或最优布局 |
| 可见语义 | pass | 底盘必须移动或某侧不可达 |
| case成立 | unknown；case_verified_count=0 | 真实最后一公里任务已成立 |
| 机器人任务 | unknown；执行记录not_tested | 抓取成功或移动收益已测得 |

[未执行任务的明确记录](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/task_validation/status.json)。`version_delivery_complete=false`，不能用一个构造接受样本替代三类任务验收。

## 11. 自己按文件复读

1. [需求](../../outputs/case_construction/construct-29365f8d6330/request.json) → [模板](../../outputs/case_construction/construct-29365f8d6330/template.json)：希望什么现象，哪些只能后续验证？
2. [局部方向计划](../../outputs/case_construction/construct-29365f8d6330/contexts/round_000/construction_plan.json) → [机器人S0](../../outputs/case_construction/construct-29365f8d6330/contexts/round_000/initialization/initialization.json)：选了什么对象和观察点？
3. [Symbolic DSL](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/sample.json) → [具体操作](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/executable_edit.json)：范围如何落成一次编辑？
4. [规则](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rule_checks.json) → [前后图身份](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/pair.json) → [视觉结果](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/semantic_review.json)：为何接受？
5. [冻结场景](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/scene) → [最终结果](../../outputs/case_construction/construct-29365f8d6330/result.json)：可恢复什么，还有什么未知？


<a id="module-io"></a>

## 补充：每个模块到底接收什么、输出什么？

本节把上面的故事还原成数据接口。**“抽象模板→方向计划→具体提议→可执行操作→观测与结论”是五种不同数据**；不能把它们都叫“Agent生成的JSON配置”。

### A. 全部模块的输入、操作、输出与本例停点

| 模块 | 实际输入 | 做什么 | 输出交给谁 | 本例产出/状态 |
|---|---|---|---|---|
| 请求解析 | case_description、scene_input、case_type、任务/目标、seed、budgets | 精确定位指定单场景；校验请求结构 | 规范请求与来源→场景程序、Agent1 | val-103，不检索其它房屋 |
| Agent1规范化 | 抽象描述、任务类型、目标模式、难度参数或null、条件/输出契约 | 不绑定实体，分开可执行条件、可见要求和任务假设 | Case Template→Agent2/3/4与规则程序 | 实际模板见B节 |
| 原场景准备 | XML/元数据、机器人配置、静置与图构建配置 | 加载完整物理场景，观察状态，建立节点和关系 | 源基准、Scene Graph→局部候选程序 | 原场景保持只读；不要求全部原物体稳定 |
| 局部候选 | 全局图、半径、节点与候选预算、种子 | 围绕目标/支撑裁剪输入信息，生成方向搜索提示 | contexts→Agent2 | 物理场景不裁剪；本轮候选以candidates.json为准 |
| Agent2方向计划 | 模板、候选摘要、资产类别、半径预算、失败反馈 | select/expand/no_context；选择上下文、操作方向与对比侧 | ConstructionPlan→初始化、资产与Agent3 | 锅铲/台面上下文；不请求新增资产 |
| 实体初态采样 | 选定目标/局部几何、源基准、真实机器人、试验预算 | 独立放置、检查地面/碰撞/关节和head目标分割 | 初始化记录与选定S0→观察与编辑基准 | 3个合法可见初态，选第13号；head与aux_000 |
| 资产检索 | plan.asset_requests、THOR库、数量/尺寸/种子 | 枚举、加载、测量和派生可用刚体资产 | asset_id/几何/来源→Agent3与编译器 | 无新增资产请求；使用已有物体 |
| 构造前观察 | 未编辑基准S0、目标、局部图、相机预算 | 保存head，动态选择辅助相机并记录覆盖 | observation与真实images→Agent3 | 观察结果可能明确information_insufficient |
| Agent3具体提议 | template、context、plan、observation/images、assets、DSL/谓词契约、反馈 | 生成proposals，或请求信息，或no_proposal | 带bindings和范围的Symbolic DSL→采样编译 | 本例实际提议见C节 |
| 信息请求执行 | Agent3的typed information_request；当前基准与剩余预算 | 真补图/扩图/资产检索，或安排换目标重新初始化 | 更新观察/上下文、resolution反馈→再调用Agent3 | 没有把一般格式修复当成补图；两者是不同分支 |
| 联合采样/编译 | 模板、具体提议、局部图、可加载资产、seed/index | 绑定角色、采样位姿、检查放置与联合条件 | Executable DSL，或SearchExhausted→编辑/反馈 | Executable DSL、实际后图、规则均存在 |
| 事务编辑与物理规则 | 确定操作、未编辑基准、物理配置、截止时间 | 独立分支执行整组操作并静置；前后端点检查 | EditTrial：checks/settling/after_graph/status→配对观察 | 规则通过；该阶段尚无最终可见结论 |
| 固定配对观察 | 前基准和后状态、固定head/辅助相机、身份与可见阈值 | 拍后图，核对相机、S0、图身份和目标可见性 | pair.json与前后images→Agent4 | 4张RGB，pair.valid=true |
| Agent4可见检查 | 配对图、前后context、template/plan/proposal/executable、规则摘要 | 逐项检查可见布局；收益意见另列；必要时请求配对补图 | wire checks/info_request/assessment→本地解析 | 3项布局检查pass；样本接受并保存scene |
| 程序汇总与保存 | 规则、有效Review、去重与预算 | 接受才保存可恢复场景；分开构造、case、任务结果 | sample.json/result.json，接受时有scene | 1构造接受；任务未测 |
| 共用HTTP网关 | 系统提示、角色payload、图片、Schema、调用/时间预算 | 聊天式请求；校验输出并在预算内重试/格式修复 | 解析结果或明确异常→当前调用模块 | 新机制最多5次含首次；不是每个模块各额外套5层重试 |

**输入输出记录要按运行身份配套读取。** 不把case1的后图、case1.5重跑模板或case3的Review互相借用。上表接口说明不表示每个模块在失败运行中都执行过。

### B. Agent1的抽象模板，逐字段翻译

#### B1. Agent1真正看到了什么？它没有看到什么？

下面摘出本轮实际请求的业务字段：

```json
{
  "case_description": "由于目标、支撑家具和周围障碍的几何布局，原来的接近或工作位置不利于抓取，需要通过底盘平移和转向调整到更合适的工作位置。请构造实际空间或接近方向的差异，不能仅把目标移得更远。",
  "case_type": "case1",
  "task_type": "pick",
  "objective_mode": "beneficial_reposition",
  "difficulty_profile": null
}
```

除此之外，同一次请求还提供`registered_predicates`、`condition_contract`、`output_shape_example_not_case_requirements`和网关加入的`output_contract`；系统提示是中文规范化职责。

- `registered_predicates`/`condition_contract`规定程序会执行哪些条件及正确写法，不是让Agent发明新函数。
- `output_shape_example_not_case_requirements`只是输出结构示例，不是要求照抄某个场景。
- `output_contract.schema`统一HTTP返回格式和本地结构校验，不表示内容已经真实成立。
- `difficulty_profile=null`表示没有输入难度档位，不能谎称数值来自difficulty_profile。

**Agent1没有收到Scene Graph、RGB、资产候选或机器人具体初态。** scene_input由程序定位场景，不等于把房屋数据传给Agent1。Xera/model/provider是调用配置，也不是模板中的物体信息。

[Agent1完整输入与原始响应](../../outputs/case_construction/construct-29365f8d6330/agents/0000-normalizer.json)。

#### B2. 这次实际落盘的模板是什么？

以下覆盖本轮模板全部顶层字段。为阅读仅省略值为null的可选字段；其余字段和文字来自实际记录，**不是建议版或人为补全版**。完整含null原文见[template.json](../../outputs/case_construction/construct-29365f8d6330/template.json)。

```json
{
  "assumptions": [
    "图片、静态几何和代理条件不能证明底盘可达、机械臂可达、真实路径无碰撞或抓取成功率",
    "未提供具体尺寸、距离、角度或机械臂可达阈值，因此不设定数值范围",
    "完整物理场景中的其他物体仍可能影响底盘、机械臂和抓取路径，局部布局描述不表示范围外没有障碍",
    "支撑关系仅在完整操作组静置后的编辑前和编辑后检查，不要求中间步骤始终保持该关系"
  ],
  "case_type": "case1",
  "intent": "保留目标、支撑家具与周围障碍形成的几何布局差异，使原接近或工作位置不利于抓取，并通过更有利的底盘平移与转向工作位置改善接近条件；不得仅通过增大目标距离构造差异",
  "invariants": [
    {
      "args": [
        "$target",
        "$support"
      ],
      "predicate": "supported_by",
      "required": true,
      "tolerance": 1e-06,
      "value": true
    }
  ],
  "objective_mode": "beneficial_reposition",
  "parameters": {},
  "pending_hypotheses": [
    "底盘平移和转向是否能到达更有利的工作位置未知",
    "调整后的工作位置是否改善机械臂接近与抓取未知",
    "真实场景中的路径碰撞风险未知"
  ],
  "requirements": [],
  "roles": {
    "obstacle": {
      "required": true,
      "type": "obstacle_object"
    },
    "robot_station": {
      "required": true,
      "type": "robot_station"
    },
    "support": {
      "required": true,
      "type": "support_surface"
    },
    "target": {
      "required": true,
      "type": "manipulable_object"
    }
  },
  "semantic_checks": [
    "编辑前后目标、支撑与障碍的相对空间布局发生可见变化，形成不同的接近方向或工作位置条件，而不是仅使目标远离机器人",
    "至少存在障碍位于目标相对于可用工作区域的某一侧，使原有接近或工作位置与调整后的有利工作位置具有几何差异",
    "编辑前后目标仍位于支撑表面上并保持同一抓取任务对象"
  ],
  "source_description": "由于目标、支撑家具和周围障碍的几何布局，原来的接近或工作位置不利于抓取，需要通过底盘平移和转向调整到更合适的工作位置。请构造实际空间或接近方向的差异，不能仅把目标移得更远。",
  "task_hypotheses": [
    "需要通过底盘平移和转向改变机器人相对于目标、支撑和障碍的工作位置，而非仅把目标移得更远",
    "原始几何布局使接近或工作位置不利于抓取，调整后的布局应提供不同且更有利的接近方向或工作位置",
    "底盘是否可达调整后的工作位置、机械臂是否可达目标、接近路径是否碰撞以及抓取是否成功，必须通过实际仿真或执行验证",
    "只有在正式任务中允许底盘平移和旋转并比较任务结果后，才能验证重新定位是否带来抓取收益"
  ],
  "task_type": "pick",
  "template_version": "0.1"
}
```

模板是“稍后到具体房屋里满足的需求单”，不是编辑脚本。没有bindings、operations、asset_id、具体落点或采样角，不应直接拿来修改仿真。

落盘版本经过领域解析及序列化；一些条件的默认`required/tolerance`和空/null字段可以由数据类补齐，`source_description`由程序确认原始输入。不要把序列化默认值都当成用户指定阈值。要区分模型原始response和template.json，可对照上面的调用记录；本例两者都留存。

#### B3. 每个顶层字段由谁使用？

| 字段 | 本例内容/含义 | 谁消费、不能推出什么 |
|---|---|---|
| `case_type` | 本例`case1` | 识别机制类别，保持与输入一致；不是一个成功标签 |
| `intent` | 把需求整理成一段意图 | 给后续Agent理解，不直接作为程序可执行断言 |
| `roles` | 下表所列的抽象角色 | Agent3经bindings绑定真实节点；没有坐标、资产ID或操作 |
| `parameters` | 本例为空对象 | 命名的数值/枚举参数与来源；不是场景物体参数表 |
| `requirements` | 本例为空列表 | 编辑后需成立的程序条件，与提案goals合并；空不代表免做物理检查 |
| `invariants` | 目标被支撑的条件 | 编辑前、完整编辑组静置后的端点都检查；不检查每个中间操作瞬间 |
| `semantic_checks` | 3条可见布局要求 | 按顺序映射为Agent4的layout:0、layout:1等，不自动编译成几何函数 |
| `pending_hypotheses` | 待确认的假设清单 | 保留不确定性供Agent理解；不是当前规则硬门槛 |
| `task_type` | pick | 当前任务类型是抓取，不是这次编辑已经抓取 |
| `objective_mode` | beneficial_reposition | 希望移动后更有利，不是已验证必须移动或收益为正 |
| `task_hypotheses` | 真实可达、碰撞、收益等原始任务要求 | 本轮construction_only不执行，交给后续移动操作验证 |
| `assumptions` | 方法限制、信息范围等说明 | 提醒哪些不能从图片推出，不是可见检查项或授权条件 |
| `source_description` | 原始描述原文 | 保留需求来源；规范化程序会用输入原文确认该字段 |
| `template_version` | 0.1 | 沿用规则子契约；请求版本0.2与它描述不同层，不是版本写错 |

`pending_hypotheses`和`task_hypotheses`存在语义重叠，但用途不同：前者描述还不确定什么；后者明确保留用户的真实任务要求。**当前程序没有把前者自动提升成任务验收器，也没有靠文字把后者变成pass。**

#### B4. roles里的占位符怎样变成真实物体？

| 角色名（不带$） | type | required |
|---|---|---|
| `obstacle` | `obstacle_object` | `true` |
| `robot_station` | `robot_station` | `true` |
| `support` | `support_surface` | `true` |
| `target` | `manipulable_object` | `true` |

`type`描述角色需要什么类型的对象，不是检索到的资产类别。例如`manipulable_object`并不是Spatula或SprayBottle；`obstacle_object`也没有指定SaltShaker或Pot。

`required=true`主要要求落到具体提议后该角色能找到对应实体，并按已实现类型检查；它不是“禁止编辑”的权限位，也不是“机器人抓取可行”的保证。`robot_station`稍后绑定`station_start`，代表实际初始化后的参考位置，不是Agent1已经选好了A或C。

```text
模板：target / support / obstacle / robot_station（只有角色）
计划：锅铲台面 + 障碍换侧方向（选场景上下文）
提议bindings：target→实际锅铲；support→实际台面；obstacle→实际盐罐
可执行操作：盐罐的世界位姿 + 采样到的相对转角
最终观测：静置后的图结构 + 固定配对照片
```

`roles`定义“需要谁”；Agent3的`bindings`回答“由场景里的哪一个实体充当”。角色类型不是物理可操作性的证明；图中的free运动能力也不等于机器人已经能抓取。

#### B5. requirements、invariants、goals为什么不能混写？

程序当前实际合并方式为：

```text
编辑前：必需角色 + template.invariants + proposal.invariants
编辑后：必需角色 + template.invariants + proposal.invariants
                   + template.requirements + proposal.goals
```

角色检查对新增角色有编辑前例外；新物体在add之后才存在，不能要求它在编辑前受支撑。

本轮`requirements=[]`，但`invariants`包含目标由支撑承托的布尔条件：```json
{
  "predicate": "supported_by",
  "args": [
    "$target",
    "$support"
  ],
  "value": true,
  "required": true,
  "tolerance": 1e-06
}
```

逐项翻译：predicate是程序已有函数名；args是稍后绑定的两个角色；value=true要求关系成立；required=true表示不满足会阻断；tolerance是条件数值比较容差，不是物理支撑高度容差。支撑几何、射线、接触等阈值由实际物理配置决定。

`$support`是“支撑角色”，不是名为support的真实物体；更不是支撑区域局部坐标原点。需要经过bindings找到实际节点，再由程序观测关系。

`requirements`和`invariants`只接受注册的结构化条件，不能填写“B侧机械臂不可达”这样的自然语言。当前常用谓词为supported、supported_by、inside_region、distance_xy、distance_3d、direction_angle；真实路径可达性不是其中的一个已实现谓词。

#### B6. parameters为空，到底有没有数值？

本轮模板`parameters={}`，表示**没有命名的模板数值条件**，不是后续所有模块都不用数值。物理配置仍有阈值；Agent3仍可基于具体几何给位姿采样范围；编译器仍会采样具体坐标和相对角。

若以后确需几何阈值，写法应是下面这种接口示意（**不是本例实际输出，不代表推荐阈值，也不代表臂可达**）：

```json
{
  "parameters": {
    "example_distance": {"source": "heuristic_default", "range": [0.2, 0.4]}
  },
  "requirements": [
    {"predicate": "distance_xy", "args": ["$target", "$obstacle"],
     "range": {"parameter": "example_distance"}}
  ]
}
```

数值条件引用命名参数，来源必须保留user_input、difficulty_profile或heuristic_default；没有难度输入不能写difficulty_profile。**Agent建议一个数，不会自动变成用户指定的数或物理规律。**

#### B7. semantic_checks怎样变成Agent4检查项？

| 模板字符串 | 本轮要求 |
|---|---|
| `layout:0` | 编辑前后目标、支撑与障碍的相对空间布局发生可见变化，形成不同的接近方向或工作位置条件，而不是仅使目标远离机器人 |
| `layout:1` | 至少存在障碍位于目标相对于可用工作区域的某一侧，使原有接近或工作位置与调整后的有利工作位置具有几何差异 |
| `layout:2` | 编辑前后目标仍位于支撑表面上并保持同一抓取任务对象 |

程序按列表顺序分配layout编号，Reviewer必须覆盖全部条目。pass必须引用至少一对匹配的before/after视图；unknown需要保留未知，不能拿规则结果替代缺失图片。

这些是静态可见检查，不是完整任务目标的替代品。`task_hypotheses`明确留下真正的底盘/手臂/抓取/收益问题；构造接受只说明较弱的静态层已满足，**不表示原始最后一公里目标已经实现**。本例已产生图片，但是否形成有效Review还要看最后的解析状态。

### C. 其它关键接口，也不要只看“一个JSON文件”

#### C1. Agent2输入是候选摘要，输出是方向计划

Agent2输入`template`、`contexts`、`available_asset_categories`、`radius_budget_m`、`feedback`。contexts是各候选的目标/支撑、附近物体几何、supported_by关系和work_regions摘要，不是已裁掉物理障碍的房屋。

输出中的`decision`控制程序分支；`context_id`选择候选；`radius_m`给信息范围；`edit_directions`列操作方向；`contrast_spec`将工作区域关联到预期机制；`asset_requests`只请求类别/尺寸/用途；`rationale`解释选择；`unverified_claims`保留未证内容。

本例实际摘录：

```json
{
  "decision": "select",
  "context_id": "context:spatula_291e31b285474249721c8a7b1bac877a_1_0_2",
  "radius_m": 2.5,
  "edit_directions": [
    "move",
    "rotate"
  ],
  "asset_requests": []
}
```

**contrast_spec说的是“在哪一侧预期什么机制”，不是该侧的真实机器人测量。** Agent2不输出最终物体位姿，也不输出机器人控制。[完整Agent2输入输出](../../outputs/case_construction/construct-29365f8d6330/agents/0001-strategist.json)。

#### C2. 初态、资产和观察程序分别输出三种数据

- 初始化记录：trials记每个位置及失败原因，selected_initial记实际选定的机器人关节/底盘状态；navigation_reachability保持unknown。失败时可输出not_found_within_budget，而不是伪造S0。
- 资产清单：asset_id、类别、测得尺寸、派生XML、源路径/摘要与加载结果。它是可用编辑输入，不是已经新增的房屋实例。
- 观察包：目标、图身份、head相机与分割、image/images、辅助相机及coverage。`images`指向真正发送给模型的RGB；coverage不足可以与“有照片”同时存在。

场景图节点的pose/collision_bounds用于几何，边记录supported_by等关系；support region里的origin/axes/bounds用于局部坐标。work_regions是外围工作方向提示，不能与物体放置region混用。

#### C3. Agent3输出包络、提议和信息请求是三件事

实际响应外层字段只有`decision`、`information_request`、`proposals`。propose时给非空proposals且information_request=null；request_information时不给可执行提案；no_proposal也不是“任务无解证明”。

单个proposal里的`bindings`连接角色与真实节点；`operations`写move/rotate/add/remove及搜索范围；`goals`只追加编辑后条件；`invariants`追加前后条件；`sampling`控制候选采样，不是成功定义。

本例采用的外层响应为：

```text
decision = propose
information_request = null
proposals数量 = 2
```

[Agent3完整输入输出](../../outputs/case_construction/construct-29365f8d6330/agents/0002-proposer.json)。补图/扩图/资产请求经程序执行后以resolution反馈回到原阶段；format_feedback/previous_response则是结构修复反馈。**缺信息与格式错误不应混为“重新编辑一次”。**

#### C4. 编译、执行、配对包各自新增了什么事实？

| 数据 | 相比上一层新增什么 | 不能当作什么 |
|---|---|---|
| Symbolic DSL | 绑定实体，但仍有范围和采样选择 | 实际已经执行的布局 |
| Executable DSL | sample_id、确定instance、世界position/quaternion、sampled_parameters中的角度/xy/seed/index | 静置后真实观测；机器人搬运轨迹 |
| EditTrial / rule_checks | before/after图、checks、settling、pending_review/rolled_back等状态 | 最终视觉通过或任务成功 |
| pair.json | 图身份、pair_id、固定相机、S0漂移、目标可见性、images/view引用 | 图已自动证明所有工作侧或所有任务条件 |
| 正式Review | 经结构、引用、覆盖和前后证据检查的布局结论 | 底盘/手臂实际可达性与收益结论 |

本例所有模块走到接受保存。要看最终通过，应组合sample.json、semantic_review.json和result.json；rule_checks.json中的pending_review只是视觉检查前保存的阶段状态。

#### C5. Agent4的输入与原始输出、正式Review要分开

Agent4接收`template/plan/proposal/executable`、`before_context/after_context`、`rule_checks`摘要、`pair`、`view_registry`和真实图像。程序从模板生成`required_visual_checks`；`review_scope=visible_construction_only`限制职责；`task_hypotheses_not_acceptance_checks`提醒任务假设不是本轮图片硬门槛。

原始响应的字段含义：checks以layout编号为键，每项给status/reason/views；information_request为null或配对补图请求；agent_assessment单列conclusion/reason/preferred_regions。它不应直接生成最终任务成功标签。

```json
{
  "agent_assessment": {
    "conclusion": "likely_beneficial",
    "preferred_regions": [
      "context:spatula_291e31b285474249721c8a7b1bac877a_1_0_2/y_minus"
    ],
    "reason": "编辑后的盐罐位于锅铲的y_plus/右后侧，而机器人初始位置和台面前侧对应的y_minus方向保留了不同的候选工作区域，因此从几何布局上看向y_minus重新定位可能比原布局更有利。底盘实际可达性、旋转空间、机械臂路径碰撞和抓取收益均未由图片或静态检查验证。"
  },
  "checks": {
    "layout:0": {
      "reason": "同名before/after视图中锅铲和台面保持不变，盐罐从目标前侧附近移动到目标右后侧并改变朝向，形成了可见的侧向布局差异，不是仅增加目标距离。",
      "status": "pass",
      "views": [
        "before/head",
        "after/head",
        "before/aux_000",
        "after/aux_000"
      ]
    },
    "layout:1": {
      "reason": "前后辅助视图均可见盐罐相对锅铲和台面工作区域发生侧向重布置；编辑后盐罐位于目标的后侧/右侧，前侧仍保留不同的接近方向。该判断仅针对可见几何构造，不证明底盘或机械臂可达。",
      "status": "pass",
      "views": [
        "before/aux_000",
        "after/aux_000"
      ]
    },
    "layout:2": {
      "reason": "前后head视图中同一锅铲仍位于同一台面支撑面上，程序检查同时报告目标supported_by支撑面通过，未见目标对象被替换。",
      "status": "pass",
      "views": [
        "before/head",
        "after/head"
      ]
    }
  },
  "information_request": null
}
```

这份原始输出通过本地解析，程序再生成正式Review并接受场景。

程序在解析成功后才添加sample_id、将checks映射转为检查列表、汇总verdict，并把任务假设保存在unverified_claims。这些**程序补充的字段不是要求模型另造一份输出**。收益意见likely_beneficial也不参与把未知任务改成pass。

#### C6. 汇总模块输出什么，为什么两种“没有结果”不同？

sample.json记录单次候选的提议、执行/规则/可见状态与原因；result.json汇总attempted_count、construction_accepted_count、accepted_samples、case_verified_count及分层results。接受才有scene快照；construction_only的任务记录是not_tested，而非任务失败。

明确错误或未运行应保留各自状态：SearchExhausted是编译/搜索未找到候选；rolled_back是已编辑但规则未过；pending_review是等待有效可见检查；budget_exhausted是预算停止。**没有生成后续文件，说明该阶段未完成，不能用上游提议补成事实。**

[本例最终分层结果](../../outputs/case_construction/construct-29365f8d6330/result.json)。本节只是解释既有数据，没有补跑Agent、编辑场景、增加成功样本或改写原始产物。

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| 同相机head前后图 | 选中 | 布局变化的证据；可识别目标与盐罐 |
| aux_000前后图 | 选中 | 布局证据补充；能看台面与部分地面 |
| 底盘轨迹/抓取收益图 | 不制作 | 未执行任务，无轨迹或收益数据 |

**本例完成了“几何布局换侧”的构造闭环；尚未完成“换底盘位置能改善抓取”的任务闭环。**
