# A侧窄、B侧不利、C侧更合适：val-103 的 case1.5 构造与失败完整解读

更新：2026-10-07。本文解读两轮独立运行：**construct-1246813fc0b7**及修正后的**construct-bc802bb13cdb**。两轮都没有接受样本。

第一轮选锅铲台面，检索到2把可加载椅子，真实执行了辅助补图请求，但**9个候选全部在编译采样时被拒绝**，没有执行出可用的编辑后场景。第二轮采用分层局部候选，选书本后未找到合法可见初态，后续扩展并返回no_context，编辑尝试数0。

**所以这是一份完整失败过程解读，不是A/B/C已经构造成功的案例。** 没有编辑后RGB，也没有A不可达、B臂不可达、C成功率高的仿真结论。

[新增：逐模块输入输出与Agent1模板详解](#module-io)。

## 1. 先看实际拥有的图：只有构造前观察

| 第一轮构造前head | 请求后新增的构造前aux_001 |
|---|---|
| ![case1.5 构造前 head；不是编辑后图](../../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/before_head.png) | ![case1.5 补充的构造前辅助图；不是编辑后图](../../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/auxiliary/before_aux_001.png) |

两张都是**未编辑场景**，只是不同相机。不能把右图当作“新增椅子之后”，也不能用两视角的差异冒充编辑变化。补图中没有本次新增的椅子，因为`add`提议没有完成编译和执行。

head看见锅铲，不代表看清其背墙侧地面。新增辅助图提供更多室内布局信息，但仍没覆盖计划中的`y_plus`工作区域。

## 2. 原始目标：比较同一目标周围的三种操作侧

[原始请求](../../outputs/case_construction/construct-1246813fc0b7/request.json)要求：

- A：家具或障碍导致空间太窄，底盘无法合法到达或站立。
- B：能够接近，但机械臂难以到达并抓取目标。
- C：有更合适的站立和操作空间，底盘调整到这里后表现更好。

请求是单场景`val-103`、pick、`beneficial_reposition`、`construction_only`。每轮独立运行目标接受数1；最多2轮、24个样本、20次Agent调用、900秒，单次240秒，种子42。

A/B/C不是三次先后编辑，也不是三个不同抓取对象。它们应当是**同一个最终布局中，同一目标周围的三个不同工作区域**。

本轮先构造三侧几何对比；“底盘不能到A”“手臂在B无解”“C成功率更高”完整保留为任务假设，不能凭照片直接宣布成立。

## 3. 四个Agent做到哪一步？

| 阶段 | 第一轮实际发生的事 | 是否完成 |
|---|---|---|
| Agent1 | 生成三侧差异模板，保留真实可达与收益假设 | 是 |
| 场景与Agent2 | 从局部候选选择锅铲台面，明确A/B/C映射并请求Chair资产 | 是 |
| 初态/资产/观察 | 放置真实机器人，检索椅子，拍head与辅助图 | 是；背侧覆盖仍不足 |
| Agent3信息请求 | 请求背墙侧辅助观察，程序增加aux_001并返回同一步 | 实际执行，但信息仍不足 |
| Agent3提议与格式修复 | 三次尝试后完成领域解析 | 是；出现条件丢失问题 |
| 编辑编译 | 三个提案各尝试3个采样候选 | 全部拒绝 |
| 物理执行、后图、Agent4、接受快照 | 没有有效可执行编辑供下游处理 | 未发生 |

```text
第一轮：模板 → 锅铲台面计划 → 椅子检索/机器人/观察
       → 实际补图（背侧仍未知）→ 格式修复 → 9次编译拒绝
       → 下一轮策略请求未完成 → 900秒硬期限 → 0接受
第二轮独立重跑：新模板 → 分层候选 → 书本 → 可见初态未找到
              → 另一书本扩展 → no_context → 轮数耗尽 → 0接受
```

这与case1已接受、case3已实际编辑的状态不同，不能强行套用成功样本的所有章节和图片。

## 4. Agent1模板：三侧假设留下了，但硬支撑条件并未写全

第一轮[实际模板](../../outputs/case_construction/construct-1246813fc0b7/template.json)包含target，以及可选的A/B/C区域和障碍角色。三项可见要求是：保持同一目标并有三侧差异；A窄、B操作受限、C相对充足；差异不只是改变距离。

需要注意实际JSON：`requirements=[]`、`invariants=[]`、`parameters={}`。**不能因为后文叙述写着“保持支撑”，就说模板已经编码了前后支撑硬条件。** 该轮的支撑条件一度写进提案goals，但最终解析版本又把它们删掉了，见第8节。

第二轮[重新生成的模板](../../outputs/case_construction/construct-bc802bb13cdb/template.json)的角色定义与第一轮不同，这是每次重新规范化的结果；不能把第二轮模板的角色套进第一轮提案。

## 5. Agent2把A/B/C映射到哪里？

第一轮选中与case1相同的锅铲和台面，但这是独立原场景运行，**不是在case1编辑后的结果上继续增加物体**。

| 抽象操作侧 | 实际工作区域后缀 | 计划想构造的机制 | 当前证据边界 |
|---|---|---|---|
| A | `y_plus` | 墙、台面后缘与新增落地椅压缩站立/转向空间 | 尚未放入椅子；背侧未拍全 |
| B | `x_minus` | 多个台面物体集中，限制侧向伸入、抓取和撤离 | 只是未执行提案，不是臂不可达结论 |
| C | `y_minus` | 保留相对连续的地面和操作余量 | 不是已找到的无碰撞底盘路径或成功站位 |

这些方向以世界坐标定义，不是直接等同北、西、南。工作区域编号只是图中的搜索提示，不能因为有一个区域ID就认为该处有合法地面或足够空间。

[Agent2原始计划](../../outputs/case_construction/construct-1246813fc0b7/agents/0001-strategist.json) → [落盘计划](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/construction_plan.json) → [选定局部图](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/local_graph.json)。

## 6. 椅子从哪里来？确实检索到了吗？

Agent2向THOR库请求`Chair`，要求宽/深各`[0.4,0.7]m`，高`[0.7,1.2]m`。程序按类别枚举资产、实际加载和测量，不是让Agent编造asset_id。

| 资产 | 实测宽×深×高m | 结果 |
|---|---|---|
| Chair_205_1 | 0.416951 × 0.467975 × 0.874939 | loadable |
| Chair_210_1 | 0.625608 × 0.486624 × 0.832251 | loadable |
| Chair_307_2 | 本文不列未接受尺寸 | measured_dimensions_outside_request |

源库是`molmospaces_data/cache/objects/thor/20251117`的`Common Objects/Chair/Prefabs/`。检索记录保存原XML路径及摘要；派生刚体资产放在本轮`assets/`内，不修改官方原资产。

**loadable只表示资产可作为构建输入，不表示已经放进房屋，也不表示这个落点合法。**

[真实检索与尺寸记录](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/assets/retrieval.json) → [可加载资产清单](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/assets/catalog.json)。

## 7. 补图请求不是空话，但“拍了新图”不等于“问题解决”

第一轮机器人实际找到3个合法可见初态，选择第13号候选；底盘初态约为`[6.486487,3.069226,1.958171]`，前两项米、最后一项弧度。

Agent3第一次没有直接提编辑，而是提出`request_information / aux_view`：要看目标、台面后缘、墙和`y_plus`工作区域。

程序增加`aux_001`，下一次Agent3输入确实包含`before/head`、`before/aux_000`、`before/aux_001`。记录同时明确：

```text
view_count = 2
status = information_insufficient
uncovered = […/y_plus]
```

这证明补图闭环被执行了，也证明没有假装覆盖背墙区域。**即使局部图里有墙的节点，仍不能用没拍到的地面补出“椅子一定能放下”的证据。**

[Agent3补图请求](../../outputs/case_construction/construct-1246813fc0b7/agents/0002-proposer.json) → [相机与覆盖记录](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/auxiliary/views.json) → [补图后的实际输入](../../outputs/case_construction/construct-1246813fc0b7/agents/0003-proposer.json)。

## 8. 三次格式尝试：完成解析，却不代表语义约束完整保留

后续同一个逻辑提议请求经历：

| 日志 | 尝试号 | 结果 |
|---|---:|---|
| [0003-proposer](../../outputs/case_construction/construct-1246813fc0b7/agents/0003-proposer.json) | 1 | supported_by条件缺value；格式/领域检查拒绝 |
| [0004-proposer](../../outputs/case_construction/construct-1246813fc0b7/agents/0004-proposer.json) | 2 | 同类错误仍存在 |
| [0005-proposer](../../outputs/case_construction/construct-1246813fc0b7/agents/0005-proposer.json) | 3 | 完成解析 |

布尔条件应是`{"predicate":"supported_by","args":["$target","$support"],"value":true}`，不能只写predicate和args。

实际第一版第一个提案有6个goals；最后解析版本却变成`goals=[]`、`invariants=[]`。**这是删除条件后消除了结构错误，不等于原来的支撑约束得到修复。** 该问题没有导致本轮误接受，因为所有候选随后都在编译阶段拒绝；但它说明“parsed”不能代表提案忠实保留了需求。

最多5次是包括首次的总尝试上限，格式修复和网络重试共用。第一轮这次逻辑请求用了3次，没有25次叠加；900秒整轮预算仍可先耗尽。

## 9. 具体提案打算怎么编辑？为什么没有实际后图？

第一个提案`layout_a_chair_b_left_cluster`准备先新增Chair_205_1，再移动长柄勺、盐罐、勺子和胡椒罐。新增椅子的实际Symbolic DSL节选如下，**只是未执行提案，不是Executable DSL**：

```json
{
  "asset_selector": {
    "asset_id": "Chair_205_1"
  },
  "bind_as": "$a_chair",
  "op": "add",
  "search_space": {
    "rotation": {
      "angle": {
        "range": [
          -0.35,
          0.35
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
    "support": "world:floor",
    "xy": {
      "mode": "uniform",
      "x": [
        6.0,
        6.9
      ],
      "y": [
        4.05,
        4.24
      ]
    }
  }
}
```

它请求在世界地面x`[6.0,6.9]`、y`[4.05,4.24]`中采样，并相对旋转`[-0.35,0.35]rad`。其它桌面移动使用支撑局部坐标，不能混成同一套世界坐标解释。

三份提案的候选分布是：

| 提案 | 样本编号 | 结果 |
|---|---|---|
| layout_a_chair_b_left_cluster | 000000—000002 | 3次编译拒绝 |
| layout_a_chair_b_front_sidewall | 000003—000005 | 3次编译拒绝 |
| layout_a_two_chair_b_target_adjacent | 000006—000008 | 3次编译拒绝 |

9次均记录`SearchExhausted:footprint_exceeds_region_or_xy_constraint`。含义是某个操作找不到同时满足支撑区域、足迹及xy约束的落点，**不是机器人真的走过去撞了，也不是物理执行后倒下**。

椅子深约0.468m，而所写y区间跨度0.19m，值得排查。但**不能仅凭两个尺寸断言错误一定发生在新增椅子的第一步**：xy还可能表示中心采样约束，完整足迹由支撑区域共同限制；同一提案后续还有多个物体移动。本轮异常没有记录失败操作索引，因此只能确认联合编译不可行，不能唯一定位失败对象。

[第一个被拒绝候选](../../outputs/case_construction/construct-1246813fc0b7/samples/sample_000000/sample.json)保存的是提案和错误，没有`executable_edit.json`。也没有该候选的后图、后图结构、Agent4正式检查或接受快照。

## 10. 第二次独立运行：候选更多样，仍没走到编辑

[重跑结果](../../outputs/case_construction/construct-bc802bb13cdb/result.json)接受数0、编辑尝试0，状态`budget_exhausted / round_budget_exhausted`。这次不是900秒硬期限，而是配置的2轮用完。

程序按支撑面分层随机采样，候选确实包含餐桌：第0轮有餐桌瓶子，第1轮有餐桌生菜。Agent2仍选择书架书本作为三侧机制载体。

第0轮选中`book_c62db15891607d44173a3d995755f638_1_0_2`。真实96次初态试验得到：

| 拒绝原因 | 次数 |
|---|---:|
| 地面支撑或碰撞不合格 | 67 |
| 目标像素不足 | 17 |
| 目标不在垂直视锥 | 12 |
| 合法且可见 | 0 |

[96次真实初态试验](../../outputs/case_construction/construct-bc802bb13cdb/contexts/round_000/initialization/initialization.json)。它只说明**该目标在这次采样预算内没找到初态**，不是证明房屋中任何目标都不适合。

第1轮Agent2选择另一书本并请求把半径从2.5m扩到4.5m。扩展后输入只剩这个候选，随后Agent2返回no_context。**扩展信息没有解决前一目标的可见初始化问题，也不代表新书本已经实际做完96次试验。**

[选第一个书本](../../outputs/case_construction/construct-bc802bb13cdb/agents/0001-strategist.json) → [另一书本的扩展请求](../../outputs/case_construction/construct-bc802bb13cdb/agents/0002-strategist.json) → [no_context响应](../../outputs/case_construction/construct-bc802bb13cdb/agents/0003-strategist.json)。

## 11. 两轮分别停在哪里？后续需要补什么证据？

| 项目 | 第一轮 | 独立重跑 |
|---|---|---|
| 原场景、模板、局部计划 | 有 | 有，且模板重新生成 |
| 合法可见机器人初态 | 有 | 所选目标未找到 |
| 资产检索 | 2把椅子可加载 | 本轮策略不请求新增资产 |
| 可执行编辑与物理后图 | 无 | 无 |
| Agent4正式检查 / 接受快照 | 无 | 无 |
| case成立 / 任务完成 | unknown | unknown |

还缺的不是一句Agent“认为可以”，而是：不丢失支撑条件的可编译提案；可见初态；同一最终布局中三侧真实可观察证据；后续允许底盘平移/旋转的路径、手臂与抓取对照。应保留其它候选并带上失败上下文身份反馈，而不是放宽碰撞或把no_context当成全房屋无解。

## 12. 按这条文件路径复读

1. [需求](../../outputs/case_construction/construct-1246813fc0b7/request.json) → [第一轮模板](../../outputs/case_construction/construct-1246813fc0b7/template.json)：A/B/C要验证什么？
2. [三侧映射](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/construction_plan.json) → [椅子来源](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/assets/retrieval.json)：选了哪块地方，实际资产是什么？
3. [补图请求](../../outputs/case_construction/construct-1246813fc0b7/agents/0002-proposer.json) → [实际补图与不足](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/auxiliary/views.json)：还有什么没看见？
4. [第一次提议](../../outputs/case_construction/construct-1246813fc0b7/agents/0003-proposer.json) → [完成解析的提议](../../outputs/case_construction/construct-1246813fc0b7/agents/0005-proposer.json) → [编译拒绝](../../outputs/case_construction/construct-1246813fc0b7/samples/sample_000000/sample.json)：字段和条件发生了什么？
5. [第一轮900秒停止](../../outputs/case_construction/construct-1246813fc0b7/result.json) → [独立重跑轮数停止](../../outputs/case_construction/construct-bc802bb13cdb/result.json)：两个不同失败阶段不要混淆。


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
| Agent2方向计划 | 模板、候选摘要、资产类别、半径预算、失败反馈 | select/expand/no_context；选择上下文、操作方向与对比侧 | ConstructionPlan→初始化、资产与Agent3 | 锅铲/台面，A=y_plus、B=x_minus、C=y_minus；请求Chair |
| 实体初态采样 | 选定目标/局部几何、源基准、真实机器人、试验预算 | 独立放置、检查地面/碰撞/关节和head目标分割 | 初始化记录与选定S0→观察与编辑基准 | 第一轮3个合法可见初态；head与2个aux，A侧覆盖不足 |
| 资产检索 | plan.asset_requests、THOR库、数量/尺寸/种子 | 枚举、加载、测量和派生可用刚体资产 | asset_id/几何/来源→Agent3与编译器 | 2把椅子loadable，不表示已放入房屋 |
| 构造前观察 | 未编辑基准S0、目标、局部图、相机预算 | 保存head，动态选择辅助相机并记录覆盖 | observation与真实images→Agent3 | 观察结果可能明确information_insufficient |
| Agent3具体提议 | template、context、plan、observation/images、assets、DSL/谓词契约、反馈 | 生成proposals，或请求信息，或no_proposal | 带bindings和范围的Symbolic DSL→采样编译 | 本例实际提议见C节 |
| 信息请求执行 | Agent3的typed information_request；当前基准与剩余预算 | 真补图/扩图/资产检索，或安排换目标重新初始化 | 更新观察/上下文、resolution反馈→再调用Agent3 | 实际补出aux_001，背侧仍信息不足 |
| 联合采样/编译 | 模板、具体提议、局部图、可加载资产、seed/index | 绑定角色、采样位姿、检查放置与联合条件 | Executable DSL，或SearchExhausted→编辑/反馈 | 联合编译拒绝；没有Executable DSL或物理后图 |
| 事务编辑与物理规则 | 确定操作、未编辑基准、物理配置、截止时间 | 独立分支执行整组操作并静置；前后端点检查 | EditTrial：checks/settling/after_graph/status→配对观察 | 未到达；不能声称发生过真实椅子碰撞 |
| 固定配对观察 | 前基准和后状态、固定head/辅助相机、身份与可见阈值 | 拍后图，核对相机、S0、图身份和目标可见性 | pair.json与前后images→Agent4 | 仅构造前观察；没有样本前后配对包 |
| Agent4可见检查 | 配对图、前后context、template/plan/proposal/executable、规则摘要 | 逐项检查可见布局；收益意见另列；必要时请求配对补图 | wire checks/info_request/assessment→本地解析 | 未调用Agent4；没有接受场景 |
| 程序汇总与保存 | 规则、有效Review、去重与预算 | 接受才保存可恢复场景；分开构造、case、任务结果 | sample.json/result.json，接受时有scene | 0接受，编译与重跑失败各自保留 |
| 共用HTTP网关 | 系统提示、角色payload、图片、Schema、调用/时间预算 | 聊天式请求；校验输出并在预算内重试/格式修复 | 解析结果或明确异常→当前调用模块 | 新机制最多5次含首次；不是每个模块各额外套5层重试 |

**输入输出记录要按运行身份配套读取。** 不把case1的后图、case1.5重跑模板或case3的Review互相借用。上表接口说明不表示每个模块在失败运行中都执行过。

### B. Agent1的抽象模板，逐字段翻译

#### B1. Agent1真正看到了什么？它没有看到什么？

下面摘出本轮实际请求的业务字段：

```json
{
  "case_description": "构造同一目标周围三个方向的差异：A位置因家具或障碍布局太窄，底盘无法合法到达或站立；B面虽然能够接近，但机械臂难以到达并抓取目标；C面有更合适的站立和操作空间，调整底盘到C面后抓取表现更好。具体可达和成功必须通过真实仿真验证，不能只用距离代理。",
  "case_type": "case1.5",
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

[Agent1完整输入与原始响应](../../outputs/case_construction/construct-1246813fc0b7/agents/0000-normalizer.json)。

#### B2. 这次实际落盘的模板是什么？

以下覆盖本轮模板全部顶层字段。为阅读仅省略值为null的可选字段；其余字段和文字来自实际记录，**不是建议版或人为补全版**。完整含null原文见[template.json](../../outputs/case_construction/construct-1246813fc0b7/template.json)。

```json
{
  "assumptions": [
    "A、B、C表示目标周围的抽象方向或操作区域，不绑定具体物体、场景坐标或机器人姿态。",
    "图片、静态几何和距离代理不能证明底盘可达、机械臂可达、抓取成功率或底盘移动必然有收益。",
    "完整物理场景中可能存在未在局部描述中列出的障碍物，范围外没有障碍未知。",
    "编辑前后复用同一个机器人初态S0，静态语义检查不要求从图像中观察到机器人实际平移或转向。",
    "三个假设A空间太窄不能到达、B可以接近但机械臂操作困难、C更有利于操作均属于待验证的构造假设。"
  ],
  "case_type": "case1.5",
  "intent": "构造同一目标周围三个方向的抽象几何布局差异，保留A空间过窄、B可接近但机械臂操作困难、C更有利于操作的机制，并将底盘重定位收益留待真实仿真验证。",
  "invariants": [],
  "objective_mode": "beneficial_reposition",
  "parameters": {},
  "pending_hypotheses": [
    "A侧底盘无法合法到达或站立仍待真实仿真验证。",
    "B侧底盘可接近但机械臂到达并抓取目标困难仍待真实仿真验证。",
    "C侧底盘位置是否更有利于机械臂操作仍待真实仿真验证。",
    "调整底盘到C侧是否提高真实抓取表现仍待真实仿真验证。"
  ],
  "requirements": [],
  "roles": {
    "a_approach_region": {
      "required": false,
      "type": "region"
    },
    "a_layout_obstacle": {
      "required": false,
      "type": "obstacle_object"
    },
    "b_approach_region": {
      "required": false,
      "type": "region"
    },
    "b_layout_obstacle": {
      "required": false,
      "type": "obstacle_object"
    },
    "c_operating_region": {
      "required": false,
      "type": "region"
    },
    "target": {
      "required": true,
      "type": "manipulable_object"
    }
  },
  "semantic_checks": [
    "编辑前后保持同一目标，并呈现目标周围A、B、C三个方向的抽象布局差异。",
    "编辑后的A侧空间相对狭窄，B侧保留可接近但操作空间受限的布局，C侧具有相对更有利的站立和操作空间。",
    "布局变化应由目标周围的区域或障碍物空间关系体现，而不是仅通过改变目标与机器人之间的距离体现。"
  ],
  "source_description": "构造同一目标周围三个方向的差异：A位置因家具或障碍布局太窄，底盘无法合法到达或站立；B面虽然能够接近，但机械臂难以到达并抓取目标；C面有更合适的站立和操作空间，调整底盘到C面后抓取表现更好。具体可达和成功必须通过真实仿真验证，不能只用距离代理。",
  "task_hypotheses": [
    "A侧空间太窄，底盘可能无法合法到达或站立；该可达性必须通过真实仿真验证。",
    "B侧可以接近目标，但机械臂可能难以到达并抓取；该操作可行性必须通过真实仿真验证。",
    "C侧提供更有利的站立和操作空间，调整底盘到C侧后抓取表现可能更好；该收益必须通过允许底盘平移和旋转的正式任务测试验证。",
    "目标的抓取接近、抬升和撤离路径需要在真实仿真中验证碰撞与成功结果。"
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
| `case_type` | 本例`case1.5` | 识别机制类别，保持与输入一致；不是一个成功标签 |
| `intent` | 把需求整理成一段意图 | 给后续Agent理解，不直接作为程序可执行断言 |
| `roles` | 下表所列的抽象角色 | Agent3经bindings绑定真实节点；没有坐标、资产ID或操作 |
| `parameters` | 本例为空对象 | 命名的数值/枚举参数与来源；不是场景物体参数表 |
| `requirements` | 本例为空列表 | 编辑后需成立的程序条件，与提案goals合并；空不代表免做物理检查 |
| `invariants` | 空列表 | 编辑前、完整编辑组静置后的端点都检查；不检查每个中间操作瞬间 |
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
| `a_approach_region` | `region` | `false` |
| `a_layout_obstacle` | `obstacle_object` | `false` |
| `b_approach_region` | `region` | `false` |
| `b_layout_obstacle` | `obstacle_object` | `false` |
| `c_operating_region` | `region` | `false` |
| `target` | `manipulable_object` | `true` |

第一轮的A/B/C区域角色都是`required=false`。这只表示“模板角色存在检查”不会因这些可选角色缺失就单独阻断；**不代表A/B/C机制变成可选要求**。三侧差异仍在semantic_checks和Agent2的contrast_spec中，策略解析另要求三种机制对应三个不同工作区域。

还要区分两个命名空间：`roles`里的region是抽象角色；`work_regions`中的`…/y_plus`等是方向搜索提示。一个提示ID不必然是`graph.nodes`里的可绑定region节点，不能不经检查直接塞进bindings。独立重跑重新生成了另一份模板，区域角色甚至变成required=true；不能将它拿来替换本节第一轮模板。

```text
抽象层：target是“稍后选择的同一个抓取目标”
计划层：选锅铲台面，指定A=y_plus、B=x_minus、C=y_minus
提议层：target绑定具体锅铲ID；add请求Chair_205_1；move绑定具体杂物
执行层：没有得到可执行整组操作，因联合采样不可行而停止
```

上述四层不能压缩成“Agent1已经决定在北侧放一把椅子”。模板不知道这间房屋、哪个方向有地面，也不知道具体椅子尺寸。

`roles`定义“需要谁”；Agent3的`bindings`回答“由场景里的哪一个实体充当”。角色类型不是物理可操作性的证明；图中的free运动能力也不等于机器人已经能抓取。

#### B5. requirements、invariants、goals为什么不能混写？

程序当前实际合并方式为：

```text
编辑前：必需角色 + template.invariants + proposal.invariants
编辑后：必需角色 + template.invariants + proposal.invariants
                   + template.requirements + proposal.goals
```

角色检查对新增角色有编辑前例外；新物体在add之后才存在，不能要求它在编辑前受支撑。

本轮落盘`requirements=[]`、`invariants=[]`。因此模板**没有编码前后支撑硬条件，也没有数值型几何条件**。三侧自然语言要求不自动变成程序谓词。

首次提案曾补了支撑goals，格式修复后却删成空列表。这与“补齐value字段”不是一回事；不能把解析成功说成条件已经保留。之后编译全部失败，所以没有进入缺失条件可能造成误接受的阶段。

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
| `layout:0` | 编辑前后保持同一目标，并呈现目标周围A、B、C三个方向的抽象布局差异。 |
| `layout:1` | 编辑后的A侧空间相对狭窄，B侧保留可接近但操作空间受限的布局，C侧具有相对更有利的站立和操作空间。 |
| `layout:2` | 布局变化应由目标周围的区域或障碍物空间关系体现，而不是仅通过改变目标与机器人之间的距离体现。 |

程序按列表顺序分配layout编号，Reviewer必须覆盖全部条目。pass必须引用至少一对匹配的before/after视图；unknown需要保留未知，不能拿规则结果替代缺失图片。

这些是静态可见检查，不是完整任务目标的替代品。`task_hypotheses`明确留下真正的底盘/手臂/抓取/收益问题；构造接受只说明较弱的静态层已满足，**不表示原始最后一公里目标已经实现**。本例甚至未产生前后图，因此这些检查没有实际执行。

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
    "add"
  ],
  "asset_requests": [
    {
      "categories": [
        "Chair"
      ],
      "intended_role": "在目标后方墙侧构造A侧底盘空间约束的落地障碍",
      "size_constraints_m": {
        "depth": [
          0.4,
          0.7
        ],
        "height": [
          0.7,
          1.2
        ],
        "width": [
          0.4,
          0.7
        ]
      }
    }
  ]
}
```

**contrast_spec说的是“在哪一侧预期什么机制”，不是该侧的真实机器人测量。** Agent2不输出最终物体位姿，也不输出机器人控制。[完整Agent2输入输出](../../outputs/case_construction/construct-1246813fc0b7/agents/0001-strategist.json)。

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

[Agent3完整输入输出](../../outputs/case_construction/construct-1246813fc0b7/agents/0005-proposer.json)。补图/扩图/资产请求经程序执行后以resolution反馈回到原阶段；format_feedback/previous_response则是结构修复反馈。**缺信息与格式错误不应混为“重新编辑一次”。**

#### C4. 编译、执行、配对包各自新增了什么事实？

| 数据 | 相比上一层新增什么 | 不能当作什么 |
|---|---|---|
| Symbolic DSL | 绑定实体，但仍有范围和采样选择 | 实际已经执行的布局 |
| Executable DSL | sample_id、确定instance、世界position/quaternion、sampled_parameters中的角度/xy/seed/index | 静置后真实观测；机器人搬运轨迹 |
| EditTrial / rule_checks | before/after图、checks、settling、pending_review/rolled_back等状态 | 最终视觉通过或任务成功 |
| pair.json | 图身份、pair_id、固定相机、S0漂移、目标可见性、images/view引用 | 图已自动证明所有工作侧或所有任务条件 |
| 正式Review | 经结构、引用、覆盖和前后证据检查的布局结论 | 底盘/手臂实际可达性与收益结论 |

本例编译器返回SearchExhausted而不是Executable DSL；后面的物理执行、前后配对、Agent4和接受保存均未发生。辅助补图输出属于构造前观察模块，不能补到不存在的编辑后结果上。

#### C5. Agent4的输入与原始输出、正式Review要分开

Agent4接收`template/plan/proposal/executable`、`before_context/after_context`、`rule_checks`摘要、`pair`、`view_registry`和真实图像。程序从模板生成`required_visual_checks`；`review_scope=visible_construction_only`限制职责；`task_hypotheses_not_acceptance_checks`提醒任务假设不是本轮图片硬门槛。

原始响应的字段含义：checks以layout编号为键，每项给status/reason/views；information_request为null或配对补图请求；agent_assessment单列conclusion/reason/preferred_regions。它不应直接生成最终任务成功标签。

本轮没有Reviewer调用，因而没有实际输出可以展示。不能从case1复制一份Review充当本例结果。

下面关于Reviewer字段的说明是接口职责解释，不是声称本轮执行到了这里。

程序在解析成功后才添加sample_id、将checks映射转为检查列表、汇总verdict，并把任务假设保存在unverified_claims。这些**程序补充的字段不是要求模型另造一份输出**。收益意见likely_beneficial也不参与把未知任务改成pass。

#### C6. 汇总模块输出什么，为什么两种“没有结果”不同？

sample.json记录单次候选的提议、执行/规则/可见状态与原因；result.json汇总attempted_count、construction_accepted_count、accepted_samples、case_verified_count及分层results。接受才有scene快照；construction_only的任务记录是not_tested，而非任务失败。

明确错误或未运行应保留各自状态：SearchExhausted是编译/搜索未找到候选；rolled_back是已编辑但规则未过；pending_review是等待有效可见检查；budget_exhausted是预算停止。**没有生成后续文件，说明该阶段未完成，不能用上游提议补成事实。**

[本例最终分层结果](../../outputs/case_construction/construct-1246813fc0b7/result.json)。本节只是解释既有数据，没有补跑Agent、编辑场景、增加成功样本或改写原始产物。

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| 原始head与补充aux_001 | 选中 | 观察诊断；两张均为构造前图，不作编辑证据 |
| 编辑前后对照 | 不制作 | 没有编辑后图，不能合成或借用case1后图 |
| A/B/C抓取成功率图 | 不制作 | 没有实际操作对照，三侧优劣未验证 |

**本例说明流水线可以理解三侧机制、检索资产和执行补图，但目前没有交付一个真正完成编辑的case1.5场景。**
