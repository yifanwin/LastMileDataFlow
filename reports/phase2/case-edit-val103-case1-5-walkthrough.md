# A侧窄、B侧不利、C侧更合适：val-103 的 case1.5 构造与失败完整解读

更新：2026-10-07。本文解读两轮独立运行：**construct-1246813fc0b7**及修正后的**construct-bc802bb13cdb**。两轮都没有接受样本。

第一轮选锅铲台面，检索到2把可加载椅子，真实执行了辅助补图请求，但**9个候选全部在编译采样时被拒绝**，没有执行出可用的编辑后场景。第二轮采用分层局部候选，选书本后未找到合法可见初态，后续扩展并返回no_context，编辑尝试数0。

**所以这是一份完整失败过程解读，不是A/B/C已经构造成功的案例。** 没有编辑后RGB，也没有A不可达、B臂不可达、C成功率高的仿真结论。

## 1. 先看实际拥有的图：只有构造前观察

| 第一轮构造前head | 请求后新增的构造前aux_001 |
|---|---|
| ![case1.5 构造前 head；不是编辑后图](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/before_head.png) | ![case1.5 补充的构造前辅助图；不是编辑后图](../../outputs/case_construction/construct-1246813fc0b7/contexts/round_000/initialization/observation/auxiliary/before_aux_001.png) |

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

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| 原始head与补充aux_001 | 选中 | 观察诊断；两张均为构造前图，不作编辑证据 |
| 编辑前后对照 | 不制作 | 没有编辑后图，不能合成或借用case1后图 |
| A/B/C抓取成功率图 | 不制作 | 没有实际操作对照，三侧优劣未验证 |

**本例说明流水线可以理解三侧机制、检索资产和执行补图，但目前没有交付一个真正完成编辑的case1.5场景。**
