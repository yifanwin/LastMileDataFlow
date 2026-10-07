# 一个番茄如何被自动移远：从自然语言到场景编辑的完整解读

本文只讲一个已经完成的真实样本：**train_0 / distance / sample_000000**。

最终发生的事情很简单：系统选中台面上的一个番茄，把它移到同一台面的另一处，让它离机器人初始站位更远。水平距离从 **4.830 米变成 5.315 米，增加约 0.485 米**。编辑前后番茄都由同一台面支撑，规则检查和视觉检查通过。

**这不等于“机器人抓不到了”。** 本次没有运行机器人导航、抓取或任务成功测试。这里构造的是“距离变远”的几何现象。

## 1. 先看图：究竟改了什么？

| 编辑前：番茄在台面一端 | 编辑后：番茄移到同一台面另一处 |
|---|---|
| ![编辑前俯视图](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_top.png) | ![编辑后俯视图](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_top.png) |

两张图使用同一个俯视相机。寻找台面上的红色小物体，对照它与台面端部、其他物体的位置。不是换个拍摄方向制造“移动”的错觉，而是场景中的番茄位置确实变了。

图片中的左右不等于世界坐标的 X、Y 方向；距离应以场景记录计算，而不是拿图片上的像素长度直接当米。

## 2. 整个系统可以理解成三位助手和一套工具

| 谁来做 | 用大白话解释 | 本例做了什么 |
|---|---|---|
| Agent1：规范化助手 | 把人的话整理成统一需求单 | 提取小物体、台面、初始站位，以及“仍受支撑、明显移远”的要求 |
| 场景程序 | 给房间建立一份物体与关系清单 | 记录物体位置、几何、台面支撑区域和真实支撑关系 |
| Agent2：提案助手 | 看需求单和房间清单，提出可执行方案 | 选择番茄和具体台面，提出移动范围及目标距离 |
| 规则程序 | 选具体数值、调用编辑函数、检查物理结果 | 采样落点，移动番茄，运行物理仿真并校验 |
| Agent3：看图助手 | 对照图片和数据判断是否符合原意 | 确认番茄确实移远，且仍在台面上 |

Agent 负责理解和提议，程序负责实际编辑及硬规则检查。不是让模型自由写代码直接控制整个仿真，也不是人工再翻译一遍 JSON。

```text
人的描述 → Agent1 的需求单
                  ＋ 已稳定场景的 Scene Graph
                  ↓
             Agent2 的 Symbolic DSL
                  ↓
             程序采样、编译、编辑、稳定化
                  ↓
             规则检查 → 前后 RGB → Agent3 检查
                  ↓
             保存通过样本和可恢复场景
```

## 3. 第一步：输入的是现象，不是“把番茄搬到某坐标”

本次原始描述为：

> 让一个小物体仍被支撑，但与机器人初始站位在水平面上明显拉开距离，布局变化应可见；这只是距离困难的几何代理，不要求宣称真正不可达。

人没有指定番茄，也没有指定操作坐标。这些具体选择留给后续步骤。

[原始请求](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/request.json)

请求还指定了真实房屋 `procthor-10k-train/train_0`，以及生成预算：目标 3 个场景、最多 4 轮、最多 48 次样本尝试、最多 24 次 Agent 调用、总时限 900 秒、随机种子 42。

`sample_000000` 是这一运行的第一个尝试，不是另一个房屋，也不是第 0 帧视频。本文只拆解这个样本。

## 4. 第二步：Agent1 把描述整理成可复用的需求单

Case Template 可以理解为“不绑定某个房间的需求单”。本次提取出三个角色：

- `small_object`：一个可移动的小物体。
- `support_surface`：承托它的表面。
- `robot_station`：机器人初始站位。

`$small_object` 这样的写法像一个空格：先写“某个小物体”，后面再填入具体番茄的编号。

需求单还规定两个 invariant（前后必须保持的条件）：

```text
supported($small_object)                         小物体受到支撑
supported_by($small_object, $support_surface)    小物体由指定表面支撑
```

这里检查的是**整组编辑前和整组编辑完成、稳定后**的状态，不是在构建过程的每一个瞬间都要求支撑关系成立。它也不是机器人真实搬运轨迹的约束。

原始描述没有说“必须超过 5 米”，所以 Agent1 没有凭空设置绝对距离门槛。这个模板的 `requirements` 是空列表，但不是没有要求：支撑条件在 `invariants` 中，明显移远和变化可见在 `semantic_checks` 中。

[Agent1 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/template.json)

初版每次请求都重新规范化，没有先检索历史相似 case。本次模板生成后，供后面几轮提案共同使用。

## 5. 第三步：让房间稳定下来，再建立 Scene Graph

Scene Graph（场景图）不是 RGB 图片，而是一份结构化“房间清单”：

- **节点**记录番茄、台面、初始站位等对象的位置、类别、几何和可用编辑操作。
- **边**记录对象之间的关系，例如“番茄由台面支撑”。

系统先初始化机器人和场景，再运行物理仿真，让物体落稳。本次编辑前图的仿真时间约为 **7.408 秒**；这是仿真中的时间，不是程序实际耗时。

本例之后绑定到的三项是：

| 角色 | 真实对象 | 编辑前世界坐标，单位米 |
|---|---|---|
| 小物体 | 番茄，资产 `Tomato_5` | `(0.257403, 1.830643, 0.992237)` |
| 支撑表面 | 台面，资产 `Countertop_I_8x2` | 节点原点 `(0.330276, 2.745280, 0.468733)` |
| 初始站位 | `station_start` | `(5.082954, 1.614674, 0)` |

台面节点原点不是台面顶面的高度。本例支撑顶面约为 `0.937470 m`。番茄节点原点也不是番茄底部，因此不能仅用两个节点 Z 值比较来断言是否接触。

图中的支撑关系结合碰撞几何、落点范围、射线与承重接触观测建立，不只是相信物体名字或原始元数据。

[初始化与稳定化记录](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/graph.json) → [编辑前冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/scene)

`station_start` 是固定的初始站位参照，其中 Z=0 表示地面上的站位锚点。它不是“机械手抓取点”，也不表示程序已经知道机器人的真实可达范围。

## 6. 第四步：Agent2 把角色填成真实物体，再写编辑提案

Agent2 接收模板和场景图，选中了：

```text
$small_object    → tomato_476e436539c70e8978e15a59750bfb92_1_0_2
$support_surface → countertop_69b3f961475324e78e62712dca967ad6_1_0_2
$robot_station   → station_start
```

这叫角色绑定：从“某个小物体”变成“这个番茄”。长编号用于准确定位对象，初学者不需要记住它。

本例提案名为 `move_tomato_far_on_countertop`。Symbolic DSL（专门表达场景编辑的小语言）的核心意思是：

```text
移动这个番茄：
  在指定台面顶面区域内选择落点；
  区域局部 x 范围为 [0.95, 1.10] 米；
  区域局部 y 范围为 [0.12, 0.20] 米；
  设置 0.08 米边界 margin，并结合物体实际占地检查落点；
  可绕世界 Z 轴相对旋转 [-π, π] 弧度。

编辑后的目标：
  与 station_start 的水平距离至少为 5.18 米；
  番茄落在指定台面区域内。

编辑前后都要满足：
  番茄受支撑，且由同一指定台面支撑。
```

**5.18 米是 Agent2 为这个具体场景提出的目标，不是用户输入，也不是所有 distance case 的统一标准。** 它高于该番茄的初始距离约 4.830 米，用来把本次“移远”落实成可计算的后态条件。

注意上面的局部 x、y 是台面区域自身的二维坐标，不是房间的世界 X、Y。台面区域有自己的原点和朝向，程序会负责换算。

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0001-proposer.json) → [第 0 轮 DSL 提案](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/proposals/round_000.json)

这里只是在解释这个样本实际采用的方案。它没有采用“必须最小编辑”的目标，也不意味着系统只能编辑一个物体或不能移动家具。

## 7. 第五步：程序把“范围”变成一组具体数值

Symbolic DSL 像“在这个范围里选一个位置”；Executable DSL 则像“这一次就用这个准确位置”。

本例使用种子 `42`、样本索引 `0`，提案的采样选择策略为 `coverage`，旨在覆盖候选范围，而不是每次手填一个坐标。实际采样记录为：

| 参数 | 本样本取值 | 怎么理解 |
|---|---|---|
| 区域局部二维落点 | `(0.952809, 0.137302)` 米 | 台面区域内的坐标 |
| 相对旋转角 | `-2.837661` 弧度，约 `-162.58°` | 绕世界 Z 轴，在原姿态基础上旋转 |
| 编译后的世界位置 | `(0.192971, 3.698169, 0.996414)` 米 | 程序交给编辑函数的节点位置 |

长度采用米，角度采用弧度。最终姿态保存为四元数 `quaternion_wxyz`：可以先把它理解成“计算机记录三维朝向的四个数”，顺序为 w、x、y、z，不是四个旋转角。

程序会结合旋转后的几何尺寸和台面边界确定落点，并根据物体底部几何计算放置高度。因此它不是只把模型原点硬放在台面顶面上。

实际数值保存在 [样本执行记录 sample.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/sample.json)：

- `proposal`：带范围的原始提案。
- `executable.operations`：本次具体操作及世界位姿。
- `executable.sampled_parameters`：局部落点、相对角度、种子和来源。

这些内容在同一个文件里，不存在一个必须另找的独立 `executable.json`。

## 8. 第六步：真正编辑，再让物理仿真检查结果

规则程序从冻结的编辑前基线建立独立分支，执行移动，然后运行物理仿真。这个样本不是接着上一份已编辑场景继续改，也没有修改原始房屋资产。

本次编辑后又仿真了 **2.732 秒、683 步**，记录为稳定、有效，无新增求解器警告，未报告严重接触穿透。最终稳定的位置为：

```text
(0.192970, 3.698170, 0.992238) 米
```

它与执行前设置的 Z≈0.996414 米略有不同，是因为放置后还经过了物理稳定化。**编译坐标是放置输入；graph_after 中的坐标才是稳定后的观测结果。**

程序检查了必要角色、前后支撑、稳定性、求解器健康、严重接触穿透、附近几何穿透，以及最终目标。检查涉及的不只番茄：记录中也有附近盐罐的物理支撑检查。

本样本所有列出的检查均为 `pass`。这表示它通过当前规则，并非对所有复杂物理情况作了完整证明。

[样本执行记录 sample.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/graph_after.json)

### 用初中数学验证“移远”

水平距离只计算 X、Y，不计高度：

```text
d = √((物体X − 站位X)² + (物体Y − 站位Y)²)

编辑前 d ≈ 4.830382 米
编辑后 d ≈ 5.315346 米
增加量   ≈ 0.484964 米
```

因此编辑后距离超过提案要求的 5.18 米。番茄在水平面内移动的路程约为 1.869 米，但它离站位的距离只增加约 0.485 米；“移动多远”和“离机器人远了多少”不是同一个量。

## 9. 第七步：拍摄六张图，Agent3 判断是否符合人的意思

数值检查通过还不够。例如，位置虽然改变了，但完全藏进柜子后面，就不一定符合“布局变化可见”。因此程序用配对相机拍摄前后各三个角度。

| 角度 | 编辑前 | 编辑后 |
|---|---|---|
| 俯视 | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_top.png) | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_top.png) |
| 斜视 A | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_oblique_a.png) | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_oblique_a.png) |
| 斜视 B | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_oblique_b.png) | [打开图片](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_oblique_b.png) |

[配对视图记录 pair.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/pair.json) 保存这一组配对图片及相机信息；本样本采用 `rgb/`，没有使用 `rgb_retry_1/`。

Agent3 输入包含原始意图、模板、提案、可执行操作、前后场景图、规则结果和六张 RGB。它检查四项：整体意图，以及“水平距离增大”“同一物体真实移位”“前后保持指定台面支撑”三个语义条件。

本例输出 `verdict: pass`，但有一个重要限制：**四张斜视图被墙遮挡，不能作为物体位置或支撑状态的独立证据。** Agent3 明确记录了这一点，最终通过依据是可用的固定俯视前后图，并结合场景图和规则结果，而不是声称三个角度都拍清楚了。

[Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0002-reviewer.json) → [视觉检查 review_0.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/review_0.json)

这些文件是 Agent 的输入、结构化回复和检查结论，不是模型内部思考过程。

## 10. 第八步：通过后保存场景，但不宣称机器人任务完成

通过规则、视觉检查和最终布局去重后，样本状态变成 `accepted`，程序保存 [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/scene)：

| 文件 | 用途 |
|---|---|
| `model.mjb` | 编译后的 MuJoCo 场景模型 |
| `initial.npz` | 恢复这个场景所需的初始状态数据 |
| `instances.json` | 物体实例映射 |
| `version.json` | 快照格式及相关版本信息 |
| `checksums.json` | 文件校验信息 |

“保存可恢复场景”是为了后续加载和使用，不只是保存几张截图。

该运行最后得到 3 个通过样本，尝试次数也是 3，停止原因是达到目标数量。本文的 `sample_000000` 是其中之一，另外两个是 `sample_000001`、`sample_000002`；它们是独立结果，不是同一个番茄连续移动的三帧。

[最终运行结果](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/result.json) 中分别记录：

| 结论 | 实际值 | 含义 |
|---|---|---|
| 场景合法性 | `valid` | 通过当前构建规则 |
| 编辑满足度 | `pass` | 编辑符合已检查的要求 |
| 语义对齐 | `pass` | 可用图像及数据支持描述的现象 |
| case 条件 | `unknown` | 未验证真实机器人困难条件成立 |
| 任务完成 | `unknown` | 未运行真实任务完成验证 |

## 11. 自己打开文件时，按这个顺序看

1. [原始请求](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/request.json)：人到底要求什么？
2. [Agent1 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/template.json)：Agent1 如何整理需求？
3. [编辑前 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/graph.json)：编辑前有什么、在哪里、谁支撑谁？
4. [Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0001-proposer.json) → [第 0 轮 DSL 提案](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/proposals/round_000.json)：为什么选择这个番茄、提议怎么改？
5. [样本执行记录 sample.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/sample.json)：范围具体采到了什么、哪些检查通过？
6. [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/graph_after.json)：稳定后的实际位置是什么？
7. [配对视图记录 pair.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/pair.json) 与上面的六张图：视觉上发生了什么？
8. [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0002-reviewer.json) → [视觉检查 review_0.json](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/review_0.json)：Agent3 看到了什么、哪些没看清？
9. [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/scene) → [最终运行结果](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/result.json)：保存了什么、最终结论有哪些边界？

这一例子展示的闭环是：**人描述现象，Agent 把现象变成具体提案，规则程序执行和测量，再用图像检查可见效果，最后留下能恢复的场景与完整中间记录。**
