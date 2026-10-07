# 把锅移到喷雾瓶近旁：val-103 的 case3 场景编辑完整解读

更新：2026-10-07。本文解读 **construct-b1bfbe3383e3 / sample_000006**，同时解释前6个候选和最终停止位置。

本例实际把搁架上的锅向喷雾瓶靠近，静置后水平移动约 **8.20 cm**，采样相对转角约 **+2.57°**。编辑与规则检查通过，生成 **6张真实前后RGB**。但Reviewer的一项pass没有引用匹配前后图，格式修复尚未完成就到900秒硬期限，样本保持 **pending_review，接受数0**。

**锅靠近目标是已发生的编辑；真实抓取路径会碰撞、底盘换位能避免碰撞都没有验证。** 未完成的检查不能当作已接受样本。

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

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| head与aux_001前后图 | 选中 | 实际位移/遮挡的布局证据，不是碰撞轨迹证据 |
| aux_000前后图 | 保留目录链接，不作主图 | 部分被机器人遮挡，适合观察诊断 |
| 手臂碰撞或底盘收益图 | 不制作 | 未执行手臂/底盘任务，没有相应测量 |

**本例已完成真实编辑和低成本规则检查；还差正式可见检查闭环，更没有完成路径碰撞与底盘换位收益验证。**
