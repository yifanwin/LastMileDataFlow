# 把锅移到喷雾瓶近旁：val-103 的 case3 场景编辑完整解读

更新：2026-10-07。本文解读 **construct-b1bfbe3383e3 / sample_000006**，同时解释前6个候选和最终停止位置。

本例实际把搁架上的锅向喷雾瓶靠近，静置后水平移动约 **8.20 cm**，采样相对转角约 **+2.57°**。编辑与规则检查通过，生成 **6张真实前后RGB**。但Reviewer的一项pass没有引用匹配前后图，格式修复尚未完成就到900秒硬期限，样本保持 **pending_review，接受数0**。

**锅靠近目标是已发生的编辑；真实抓取路径会碰撞、底盘换位能避免碰撞都没有验证。** 未完成的检查不能当作已接受样本。

[新增：逐模块输入输出与Agent1模板详解](#module-io)。

## 1. 先看head：锅更靠近蓝色喷雾瓶，并增加遮挡

| 编辑前head | 编辑后head |
|---|---|
| ![case3 编辑前 head](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/before_head.png) | ![case3 编辑后 head](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/after_head.png) |

蓝色物体是喷雾瓶目标，灰色方形双耳锅是障碍。锅从原位置向目标邻近处移动，喷雾瓶下半部在后图更受遮挡。目标没有收到移动命令。

这是一份新的障碍布局，不是机器人已经越过锅去抓瓶子的过程。图中机器人没有执行抓取轨迹；不存在可用于证明碰撞的执行视频。

## 2. 输入需要的是路径机制，不是随便增加杂物数量

[原始请求](../../outputs/case_construction/construct-b1bfbe3383e3/request.json)描述抓取接近、抬升或撤离过程中被桌面物体阻挡，并希望底盘平移/转向后存在更有利方向。允许增加真实小物体，但不能仅靠数量或距离宣布成立。

请求指定`val-103`、pick、`beneficial_reposition`、`construction_only`。接受目标1；最多2轮、24个候选、20次Agent调用、900秒，单次240秒；种子42。

Agent1将要求分成两层：

- 可见构造：目标、支撑、障碍形成潜在路径相关布局；前后同一目标保持支撑，障碍关系发生变化。
- 任务假设：真实手臂接近/抬升/撤离是否碰撞；底盘能否到另一侧；换位是否改善实际抓取。

模板没有给机械臂可达阈值。**“潜在路径相关”不是已经算出一条抓取路径。**

[Agent1调用](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0000-normalizer.json) → [Case Template](../../outputs/case_construction/construct-b1bfbe3383e3/template.json)。

## 3. 为何第7个候选换成喷雾瓶，而不是继续锅铲？

`sample_000006`是从0计数的第7次尝试，不是第7个接受样本。该轮前6次都没被接受：

| 样本 | 目标与编辑方向 | 实际结果 |
|---|---|---|
| 000000 | 锅铲台面附近布置调味罐与勺子 | 联合编译不满足距离条件，未执行 |
| 000001—000005 | 同一锅铲周围不同障碍落点 | 实际执行后规则不通过，回滚 |
| 000006 | 第二轮选择喷雾瓶与同一搁架的锅 | 规则通过，进入视觉检查；尚未接受 |

前5个实际执行候选的失败包含编辑物体稳定性检查，不能将原场景免稳定门槛误解成“编辑后任何物体都不用检查”。每个候选从未编辑基准独立生成；不是在失败布局上继续堆物体。

第二轮Agent2选择喷雾瓶局部上下文，半径2.5m，建议调整锅或苹果等障碍，避免反复复用失稳布局。最终执行的第一个提案只移动锅，未新增资产，也未实际移动苹果。

[第一轮锅铲计划](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0001-strategist.json) → [第二轮喷雾瓶计划](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0003-strategist.json) → [第二轮落盘计划](../../outputs/case_construction/construct-b1bfbe3383e3/contexts/round_001/construction_plan.json)。

## 4. 四个Agent各负责什么？

```text
Agent1：保留路径碰撞机制，但不提前宣布真实碰撞
Agent2：选局部区域与编辑方向
程序：加载真实机器人，采样S0和辅助观察
Agent3：绑定喷雾瓶、搁架、锅，给范围型DSL
程序：编译 → 独立编辑 → 静置与硬规则 → 固定前后图
Agent4：检查可见布局；收益意见单列
最后：必须正式视觉检查通过才能接受并保存快照
```

前三步不能替代最后的正式检查。Agent输出“pass”也必须通过字段、引用与证据配对检查。

## 5. 具体物体和工作方向是什么？

| 角色 | 实际实例 | 场景类别/资产 |
|---|---|---|
| `$target` | `atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2` | SprayBottle / Spray_Bottle_1 |
| `$support` | `shelf_058ab92428573ff5337fedd152a33ae3_1_0_2` | ShelvingUnit / RoboTHOR_shelving_unit_kallax_small_2_v |
| `$obstacle` | `pot_c9b50543a288eda0bcfc3bc47492ef75_1_0_2` | Pot / Pot_28 |
| `$robot_station` | `station_start` | 本轮实际基座初始化参考 |

实例名里的`atomizer`不改变记录的实际类别：图中的蓝色目标是喷雾瓶。`shelf`是家具整体，操作选择其真实顶部region。

Agent2提出`y_plus`潜在路径受阻、`y_minus`相对有利，另外记录`x_minus`底盘空间受限、`x_plus`手臂不利的假设。它们是世界轴方向下的搜索提示，**不是已经测试过的A/C站位**。

[局部图与方向区域](../../outputs/case_construction/construct-b1bfbe3383e3/contexts/round_001/local_graph.json)。完整房屋仍参加物理检查，没有为了方便而删除其它房间的碰撞物。

## 6. 机器人初态与相机：看得见目标，但不代表抓得到

第二轮初态采样种子是 **43（请求种子42加轮次1）**。找到3个合法可见初态，目标像素为1022、2395、1045，选择第14号候选、2395像素。

```text
base S0 = [3.208198, 3.188284, 2.296376]
              x(m)       y(m)       yaw(rad)
head = [0, 0.6] rad
```

本轮编辑采样仍用请求种子42、全局样本索引6。两种种子用途不同，不是记录冲突。

同一S0用于前后图。配对记录中底盘组漂移为0，head相机位置与旋转矩阵漂移为0；目标可见像素由 **2395降至1333**，仍超过本次24像素门槛。

下降与图中遮挡增加相符，但分割像素不是抓取难度、碰撞概率或移动收益指标。

[实际初态试验](../../outputs/case_construction/construct-b1bfbe3383e3/contexts/round_001/initialization/initialization.json) → [相机配对及目标分割](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/pair.json)。

## 7. Agent3的提案与一次角色引用修复

第二轮初次提议的另一方案，直接把真实苹果实例名填入操作subject，而DSL要求`$role`。因此 **整次提议响应被领域解析拒绝**，不是只有那个操作被程序悄悄忽略。

- [第1次尝试](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0004-proposer.json)：`operations[1].subject: bound role required`。
- [第2次尝试](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0005-proposer.json)：完成解析。

随后采用`pot_near_target_side`，绑定上表对象。以下是实际操作节选：

```json
{
  "op": "move",
  "search_space": {
    "region": "shelf_058ab92428573ff5337fedd152a33ae3_1_0_2:shelf_058ab92428573ff5337fedd152a33ae3_1_1_2_collision_2:top",
    "rotation": {
      "angle": {
        "range": [
          -0.15,
          0.15
        ]
      },
      "axis": [
        0,
        0,
        1
      ],
      "frame": "world",
      "pivot": "object_origin"
    },
    "support": "$support",
    "xy": {
      "margin": 0.01,
      "mode": "uniform",
      "x": [
        0.0,
        0.08
      ],
      "y": [
        -0.04,
        0.04
      ]
    }
  },
  "subject": "$obstacle"
}
```

`xy`按搁架顶部region局部坐标解释；程序仍要检查完整物体足迹在支撑区域内。`rotation`为绕世界Z轴的相对角；`margin`单位米。不能把局部`[0,0.08]`误当世界坐标，也不能把这8cm直接当机械臂可达标准。

实际提案显式保留目标前后` supported_by`，after goal也要求目标支撑。锅的落点与物理支撑由构建操作和实际物理检查继续验证；不是Agent的文字保证。

[完整提议和样本状态](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/sample.json)。

## 8. 程序到底移动了多少？

```text
局部采样xy = [0.0212481128, 0.0172138226] m
相对旋转增量 = +0.0447930149 rad ≈ +2.56645°
编译世界位置 = [2.5747581128, 4.0670138226, 0.8515737784] m
```

| 对象 | 编辑前世界坐标m | 静置后世界坐标m |
|---|---|---|
| 喷雾瓶 | (2.359772, 4.144809, 0.897815) | (2.359772, 4.144809, 0.897815) |
| 锅 | (2.650280, 4.098875, 0.849383) | (2.574758, 4.067014, 0.849383) |

根据上述前后图结构计算：锅水平移动 **0.081968m**；锅与喷雾瓶的body原点水平距离从 **0.294117m降至0.228629m**。这些不是两物体表面的最小间距，更不是机械臂与障碍的距离。

编译高度略高于静置后的高度，是落点输入与物理演进观测的区别，不应把两者混称“最终坐标”。角增量也不是最终姿态的绝对yaw。只移动锅、位移不大是这次采样结果，不是系统的最小编辑目标。

[实际Executable DSL](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/executable_edit.json) → [静置后图结构](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/graph_after.json)。

## 9. 实际执行与硬检查：为什么它能进入视觉阶段？

静置 **1.0秒仿真、250步**，编辑物体锅稳定且有效，无新增求解器警告、未报告严重穿透。稳定检查作用域为锅，不要求未编辑官方场景完全静止。

规则覆盖目标/锅/支撑角色、目标前后支撑、锅和喷雾瓶物理支撑、求解器、严重穿透及附近几何，记录项均pass。因此`rule_checks.json`状态为`pending_review`，允许进入视觉阶段。

**规则允许两个物体合法邻近，不等于已经证明手臂轨迹会碰到锅。** 没有抓取姿态搜索、手臂路径或底盘到另一侧的轨迹，真实路径碰撞机制还只是待验证假设。

[规则与静置结果](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rule_checks.json)。

## 10. 辅助俯视图能补充什么？

| 编辑前aux_001 | 编辑后aux_001 |
|---|---|
| ![case3 编辑前辅助俯视](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/before_aux_001.png) | ![case3 编辑后辅助俯视](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/after_aux_001.png) |

这一对俯视图更清楚地显示锅向左侧目标靠近。另一个`aux_000`视角被机器人部分遮挡，不宜独自拿来证明目标附近全部几何。新增辅助视图不隐藏墙，也不是机器人head输入。

[实际RGB目录](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb)含head、aux_000、aux_001各一对，共6张RGB；另有目标mask，不计RGB。所有前后同名相机属于同一pair_id，不是重新编辑后凑出的图。

## 11. Agent4看起来给了pass，为什么仍然不是接受？

第一次Reviewer响应的两项检查都写pass，但`layout:0`只引用：

```json
{"status":"pass","views":["before/head","before/aux_000"]}
```

缺少同名after图。程序要求pass有匹配前后证据，所以领域解析拒绝，记录：

```text
views: pass needs matching before/after view
```

`layout:1`的引用本身有前后配对，也不能挽救整份响应的非法证据引用。响应中的`likely_beneficial`只是移动收益意见，**既不是正式视觉验收，也不是任务结果**。

[第1次Reviewer尝试与拒绝原因](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0006-reviewer.json)后，[第2次格式修复请求](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0007-reviewer.json)仍使用同一候选、同一配对图，不重采样、不重新编辑。修复未返回最终结果时，整轮900秒硬期限触发。

每个逻辑请求最多5次尝试不代表必须尝试满5次：整轮预算优先。这里不是“重试上限只给了2次”，而是第2次尚未完成整轮就结束了。

## 12. 最终状态与缺失产物怎样读？

[最终运行结果](../../outputs/case_construction/construct-b1bfbe3383e3/result.json)为`budget_exhausted / hard_wall_clock_deadline`，接受0。样本`sample.json`保持`pending_review`。

| 已存在 | 未存在、不能补写成成功 |
|---|---|
| 需求、模板、方向计划、真实初态 | 完成解析并正式通过的最终Reviewer |
| Symbolic与Executable DSL | 正式semantic_review.json |
| 实际后图结构、规则结果 | 接受后的scene冻结快照 |
| 6张前后RGB及有效pair.json | 抓取/底盘执行轨迹和任务成功证据 |

调用日志0000—0007共有8个已发起请求，最后一个停在calling。`result.json`中`agent_calls=6`是硬终止前最后一次检查点值；**不能据此说真实只发了6个请求，也不能将未返回响应计为通过**。

原始记录保持不变；本文不是补跑Reviewer，也没有把pending_review升级成accepted。

## 13. 跟着文件复读，重点区分三种“通过”

1. [路径机制需求](../../outputs/case_construction/construct-b1bfbe3383e3/request.json) → [构造与任务分层](../../outputs/case_construction/construct-b1bfbe3383e3/template.json)。
2. [喷雾瓶方向计划](../../outputs/case_construction/construct-b1bfbe3383e3/contexts/round_001/construction_plan.json) → [实际机器人S0](../../outputs/case_construction/construct-b1bfbe3383e3/contexts/round_001/initialization/initialization.json)。
3. [非法角色引用](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0004-proposer.json) → [修复后的DSL](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0005-proposer.json)。
4. [具体操作](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/executable_edit.json) → [规则通过](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rule_checks.json) → [固定前后图](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/pair.json)。
5. [未被接受的视觉响应](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0006-reviewer.json) → [未完成的修复](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0007-reviewer.json) → [硬期限结果](../../outputs/case_construction/construct-b1bfbe3383e3/result.json)。


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
| Agent2方向计划 | 模板、候选摘要、资产类别、半径预算、失败反馈 | select/expand/no_context；选择上下文、操作方向与对比侧 | ConstructionPlan→初始化、资产与Agent3 | 第二轮喷雾瓶/搁架；锅作为障碍，不新增资产 |
| 实体初态采样 | 选定目标/局部几何、源基准、真实机器人、试验预算 | 独立放置、检查地面/碰撞/关节和head目标分割 | 初始化记录与选定S0→观察与编辑基准 | 第二轮3个合法可见初态，选第14号；head与2个aux |
| 资产检索 | plan.asset_requests、THOR库、数量/尺寸/种子 | 枚举、加载、测量和派生可用刚体资产 | asset_id/几何/来源→Agent3与编译器 | 无新增资产请求；使用已有物体 |
| 构造前观察 | 未编辑基准S0、目标、局部图、相机预算 | 保存head，动态选择辅助相机并记录覆盖 | observation与真实images→Agent3 | 观察结果可能明确information_insufficient |
| Agent3具体提议 | template、context、plan、observation/images、assets、DSL/谓词契约、反馈 | 生成proposals，或请求信息，或no_proposal | 带bindings和范围的Symbolic DSL→采样编译 | 本例实际提议见C节 |
| 信息请求执行 | Agent3的typed information_request；当前基准与剩余预算 | 真补图/扩图/资产检索，或安排换目标重新初始化 | 更新观察/上下文、resolution反馈→再调用Agent3 | 没有把一般格式修复当成补图；两者是不同分支 |
| 联合采样/编译 | 模板、具体提议、局部图、可加载资产、seed/index | 绑定角色、采样位姿、检查放置与联合条件 | Executable DSL，或SearchExhausted→编辑/反馈 | 具体移动、物理后图和规则存在，状态pending_review |
| 事务编辑与物理规则 | 确定操作、未编辑基准、物理配置、截止时间 | 独立分支执行整组操作并静置；前后端点检查 | EditTrial：checks/settling/after_graph/status→配对观察 | 规则通过；该阶段尚无最终可见结论 |
| 固定配对观察 | 前基准和后状态、固定head/辅助相机、身份与可见阈值 | 拍后图，核对相机、S0、图身份和目标可见性 | pair.json与前后images→Agent4 | 6张RGB，pair.valid=true |
| Agent4可见检查 | 配对图、前后context、template/plan/proposal/executable、规则摘要 | 逐项检查可见布局；收益意见另列；必要时请求配对补图 | wire checks/info_request/assessment→本地解析 | Reviewer响应证据引用不合规，修复遇硬期限；不接受 |
| 程序汇总与保存 | 规则、有效Review、去重与预算 | 接受才保存可恢复场景；分开构造、case、任务结果 | sample.json/result.json，接受时有scene | 0接受；规则通过不覆盖Reviewer未完成 |
| 共用HTTP网关 | 系统提示、角色payload、图片、Schema、调用/时间预算 | 聊天式请求；校验输出并在预算内重试/格式修复 | 解析结果或明确异常→当前调用模块 | 新机制最多5次含首次；不是每个模块各额外套5层重试 |

**输入输出记录要按运行身份配套读取。** 不把case1的后图、case1.5重跑模板或case3的Review互相借用。上表接口说明不表示每个模块在失败运行中都执行过。

### B. Agent1的抽象模板，逐字段翻译

#### B1. Agent1真正看到了什么？它没有看到什么？

下面摘出本轮实际请求的业务字段：

```json
{
  "case_description": "抓取目标的接近、抬升或撤离路径会受到桌面其他物体阻挡，例如需要越过杂乱桌面的物体直接抓取，存在机械臂与环境碰撞风险。通过底盘平移和转向改变操作方向后存在更有利的抓取路径。可增加真实的小物体资产，但不能仅靠杂物数量或目标距离作为case成立证据。",
  "case_type": "case3",
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

[Agent1完整输入与原始响应](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0000-normalizer.json)。

#### B2. 这次实际落盘的模板是什么？

以下覆盖本轮模板全部顶层字段。为阅读仅省略值为null的可选字段；其余字段和文字来自实际记录，**不是建议版或人为补全版**。完整含null原文见[template.json](../../outputs/case_construction/construct-b1bfbe3383e3/template.json)。

```json
{
  "assumptions": [
    "图片和几何代理只能表明目标、支撑面与障碍物的静态空间关系，不能证明底盘可达、机械臂可达、真实路径碰撞、抓取成功或移动必然有益。",
    "障碍物数量和目标距离本身不作为case3成立证据。",
    "完整物理场景中可能存在局部图像未显示的其他物体或障碍。"
  ],
  "case_type": "case3",
  "intent": "保留抓取接近、抬升或撤离路径受环境物体阻挡并可能通过底盘平移和转向获得更有利操作方向的抽象机制",
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
    "目标的抓取接近、抬升或撤离路径是否存在真实机械臂与环境碰撞风险未知。",
    "底盘平移和转向是否能够改变有效操作方向并改善真实抓取路径未知。",
    "目标是否在当前底盘状态下可达以及重新定位后的抓取是否成功未知。"
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
    "场景中存在目标、支撑面和至少一个环境障碍物的静态布局关系，使目标的抓取接近、抬升或撤离潜在路径受到障碍物空间占据的影响。",
    "编辑前后保持目标由支撑面承托，并可见目标与障碍物的相对布局发生与潜在抓取路径方向相关的变化。"
  ],
  "source_description": "抓取目标的接近、抬升或撤离路径会受到桌面其他物体阻挡，例如需要越过杂乱桌面的物体直接抓取，存在机械臂与环境碰撞风险。通过底盘平移和转向改变操作方向后存在更有利的抓取路径。可增加真实的小物体资产，但不能仅靠杂物数量或目标距离作为case成立证据。",
  "task_hypotheses": [
    "真实仿真中，目标的抓取接近、抬升或撤离路径可能与环境障碍物发生碰撞。",
    "允许底盘平移和转向后，某些重新定位和操作方向可能形成更低碰撞风险或更有利的抓取路径。",
    "是否存在可执行且收益为正的重新定位方案必须通过完整任务执行结果验证，不能由静态图像或几何代理单独推出。"
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
| `case_type` | 本例`case3` | 识别机制类别，保持与输入一致；不是一个成功标签 |
| `intent` | 把需求整理成一段意图 | 给后续Agent理解，不直接作为程序可执行断言 |
| `roles` | 下表所列的抽象角色 | Agent3经bindings绑定真实节点；没有坐标、资产ID或操作 |
| `parameters` | 本例为空对象 | 命名的数值/枚举参数与来源；不是场景物体参数表 |
| `requirements` | 本例为空列表 | 编辑后需成立的程序条件，与提案goals合并；空不代表免做物理检查 |
| `invariants` | 目标被支撑的条件 | 编辑前、完整编辑组静置后的端点都检查；不检查每个中间操作瞬间 |
| `semantic_checks` | 2条可见布局要求 | 按顺序映射为Agent4的layout:0、layout:1等，不自动编译成几何函数 |
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
第二轮计划：喷雾瓶搁架 + 邻近障碍布局（选场景上下文）
提议bindings：target→实际喷雾瓶；support→实际搁架；obstacle→实际锅
可执行操作：锅的世界位姿 + 采样到的相对转角
最终观测：静置后图结构与照片；正式视觉验收仍未完成
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
| `layout:0` | 场景中存在目标、支撑面和至少一个环境障碍物的静态布局关系，使目标的抓取接近、抬升或撤离潜在路径受到障碍物空间占据的影响。 |
| `layout:1` | 编辑前后保持目标由支撑面承托，并可见目标与障碍物的相对布局发生与潜在抓取路径方向相关的变化。 |

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
  "context_id": "context:atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2",
  "radius_m": 2.5,
  "edit_directions": [
    "move",
    "rotate"
  ],
  "asset_requests": []
}
```

**contrast_spec说的是“在哪一侧预期什么机制”，不是该侧的真实机器人测量。** Agent2不输出最终物体位姿，也不输出机器人控制。[完整Agent2输入输出](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0003-strategist.json)。

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
proposals数量 = 3
```

[Agent3完整输入输出](../../outputs/case_construction/construct-b1bfbe3383e3/agents/0005-proposer.json)。补图/扩图/资产请求经程序执行后以resolution反馈回到原阶段；format_feedback/previous_response则是结构修复反馈。**缺信息与格式错误不应混为“重新编辑一次”。**

#### C4. 编译、执行、配对包各自新增了什么事实？

| 数据 | 相比上一层新增什么 | 不能当作什么 |
|---|---|---|
| Symbolic DSL | 绑定实体，但仍有范围和采样选择 | 实际已经执行的布局 |
| Executable DSL | sample_id、确定instance、世界position/quaternion、sampled_parameters中的角度/xy/seed/index | 静置后真实观测；机器人搬运轨迹 |
| EditTrial / rule_checks | before/after图、checks、settling、pending_review/rolled_back等状态 | 最终视觉通过或任务成功 |
| pair.json | 图身份、pair_id、固定相机、S0漂移、目标可见性、images/view引用 | 图已自动证明所有工作侧或所有任务条件 |
| 正式Review | 经结构、引用、覆盖和前后证据检查的布局结论 | 底盘/手臂实际可达性与收益结论 |

本例编译、编辑、规则、配对观察已经产出；Agent4只有被拒绝的原始响应和未返回的修复请求。不能将wire响应里的pass直接当成正式Review，更没有接受保存后的scene目录。

#### C5. Agent4的输入与原始输出、正式Review要分开

Agent4接收`template/plan/proposal/executable`、`before_context/after_context`、`rule_checks`摘要、`pair`、`view_registry`和真实图像。程序从模板生成`required_visual_checks`；`review_scope=visible_construction_only`限制职责；`task_hypotheses_not_acceptance_checks`提醒任务假设不是本轮图片硬门槛。

原始响应的字段含义：checks以layout编号为键，每项给status/reason/views；information_request为null或配对补图请求；agent_assessment单列conclusion/reason/preferred_regions。它不应直接生成最终任务成功标签。

```json
{
  "agent_assessment": {
    "conclusion": "likely_beneficial",
    "preferred_regions": [
      "context:atomizer_e0cab0ef1fe80f6fe596048b9bc862a1_1_0_2/y_minus"
    ],
    "reason": "编辑后锅更靠近目标的一侧，形成了明确的受限方向，同时目标前方和侧方仍存在不同空间方向；这支持通过改变操作方向或底盘位置进行后续验证。但图像和几何代理不能证明底盘可达、机械臂路径无碰撞、抓取成功或移动收益已验证。"
  },
  "checks": {
    "layout:0": {
      "reason": "before/head和before/aux_000中可见喷雾瓶目标、搁架支撑面及同一搁架上的锅；锅位于目标邻近侧方并占据目标周围空间，构成与接近、抬升或撤离方向相关的潜在障碍布局。",
      "status": "pass",
      "views": [
        "before/head",
        "before/aux_000"
      ]
    },
    "layout:1": {
      "reason": "before/head与after/head以及before/aux_000与after/aux_000显示目标仍位于搁架上，且锅相对目标发生了可见的侧向位移和朝向变化；支撑关系在图像中保持，变化与目标周围潜在操作方向相关。",
      "status": "pass",
      "views": [
        "before/head",
        "after/head",
        "before/aux_000",
        "after/aux_000"
      ]
    }
  },
  "information_request": null
}
```

这是**未被本地解析接受**的原始输出。layout:0只有before引用，即使status=pass，仍不构成有效Review；后续修复也没有返回最终结果。

程序在解析成功后才添加sample_id、将checks映射转为检查列表、汇总verdict，并把任务假设保存在unverified_claims。这些**程序补充的字段不是要求模型另造一份输出**。收益意见likely_beneficial也不参与把未知任务改成pass。

#### C6. 汇总模块输出什么，为什么两种“没有结果”不同？

sample.json记录单次候选的提议、执行/规则/可见状态与原因；result.json汇总attempted_count、construction_accepted_count、accepted_samples、case_verified_count及分层results。接受才有scene快照；construction_only的任务记录是not_tested，而非任务失败。

明确错误或未运行应保留各自状态：SearchExhausted是编译/搜索未找到候选；rolled_back是已编辑但规则未过；pending_review是等待有效可见检查；budget_exhausted是预算停止。**没有生成后续文件，说明该阶段未完成，不能用上游提议补成事实。**

[本例最终分层结果](../../outputs/case_construction/construct-b1bfbe3383e3/result.json)。本节只是解释既有数据，没有补跑Agent、编辑场景、增加成功样本或改写原始产物。

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| head与aux_001前后图 | 选中 | 实际位移/遮挡的布局证据，不是碰撞轨迹证据 |
| aux_000前后图 | 保留目录链接，不作主图 | 部分被机器人遮挡，适合观察诊断 |
| 手臂碰撞或底盘收益图 | 不制作 | 未执行手臂/底盘任务，没有相应测量 |

**本例已完成真实编辑和低成本规则检查；还差正式可见检查闭环，更没有完成路径碰撞与底盘换位收益验证。**
