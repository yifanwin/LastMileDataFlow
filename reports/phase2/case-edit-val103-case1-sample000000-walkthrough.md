# 锅铲不动、盐罐换侧：val-103 的 case1 场景编辑完整解读

更新：2026-10-07。本文解读 **construct-29365f8d6330 / sample_000000**，依据该轮实际记录，不是另编的演示。

本例保留锅铲目标与台面，将盐罐移到目标另一侧，并采样约 **+33.41° 的相对旋转**。静置后盐罐的水平位移约 **0.802 m**。规则和配对图片检查通过，得到 **1个构造接受样本**。

**接受的是可见布局变化，不是“底盘移动后抓取更好”。** 机器人确实被加载、采样和放置，但没有执行底盘移动、机械臂规划或抓取。这个样本还不能证明必须移动、某侧不可达或重定位收益。

## 1. 先看图：改变的是障碍物位置，不是把目标移远

| 编辑前 head | 编辑后 head |
|---|---|
| ![case1 编辑前 head](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/before_head.png) | ![case1 编辑后 head](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/after_head.png) |

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

### 图的选择与用途

| 候选图 | 选择 | 原因与类别 |
|---|---|---|
| 同相机head前后图 | 选中 | 布局变化的证据；可识别目标与盐罐 |
| aux_000前后图 | 选中 | 布局证据补充；能看台面与部分地面 |
| 底盘轨迹/抓取收益图 | 不制作 | 未执行任务，无轨迹或收益数据 |

**本例完成了“几何布局换侧”的构造闭环；尚未完成“换底盘位置能改善抓取”的任务闭环。**
