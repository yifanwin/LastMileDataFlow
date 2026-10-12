# 两把水壶怎样变成不同朝向：一个真实场景编辑样本的完整解读

本文解读 **train_0 / direction / sample_000002**，所有数值和结论来自该样本的实际记录，不是另编的示例。

系统选择同一台面上的两把水壶，保留 A 的布局，给 B 施加约 **−128.49° 的相对旋转**。稳定后，两把水壶的局部 X 轴夹角约为 **128.49°**，满足本次几何目标。首次图片没有充分拍到 A，Agent3 判为不确定；重新拍摄前后配对图后，才确认可见朝向不同并通过验收。

**通过的是场景编辑，不是机器人任务。** 本例没有验证机器人能否抓取，也没有证明这样的朝向会使操作更困难。

## 1. 先看图：一把水壶转了，另一把作为参照

| 最终验收使用的编辑前斜视 A | 最终验收使用的编辑后斜视 A |
|---|---|
| ![重拍后的编辑前斜视 A](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_oblique_a.png) | ![重拍后的编辑后斜视 A](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_oblique_a.png) |

对照两把水壶的位置，以及壶嘴和深蓝色手柄的方向。画面中央附近的水壶 B 发生明显转向，另一把 A 作为比较对象。相机在这一对前后图中保持一致，所以不是靠换个视角制造“旋转”的错觉。

这组图片来自 **`rgb_retry_1/`**，不是最初的 `rgb/`。后文会解释为什么要重拍。

## 2. 三个 Agent 和规则程序分别负责什么？

Agent 可以理解为负责理解、提议或看图的 AI 助手。它不直接任意改仿真，而是交出结构化结果，由程序执行。

| 参与者 | 初学者可以这样理解 | 本例的工作 |
|---|---|---|
| Agent1 | 整理需求单的人 | 把“两个物体朝向不同”拆成角色、数值代理和看图标准 |
| 场景程序 | 给房间建物体清单的人 | 记录水壶、台面的位置、朝向、几何和支撑关系 |
| Agent2 | 提出编辑方案的人 | 选两把水壶，建议绕竖直轴旋转其中一把 |
| 规则程序 | 实际动手并测量的工具 | 采样角度、执行旋转、运行物理仿真和硬规则检查 |
| Agent3 | 对照前后照片验收的人 | 确认两把水壶可见，并确实表现出不同朝向 |

```text
抽象描述 → Agent1 的 Case Template
                    ＋ 稳定场景的 Scene Graph
                    ↓
               Agent2 的 Symbolic DSL
                    ↓
               采样角度 → 编译成具体操作 → 执行并稳定化
                    ↓
               规则检查通过 → 首次拍照 → Agent3 不确定
                    ↓
               成对重拍 → Agent3 通过 → 保存场景
```

本例最重要的一点是：**数学上夹角合格，不代表图片已经证明物体朝向不同。** 两种检查缺一不可。

## 3. 输入：人只说需要的现象，没有指定水壶

本次原始描述是：

> 让两个仍被支撑的可见物体呈现明显不同的朝向，以其局部坐标轴夹角作为几何代理；物体形状无法显示朝向时不能判定视觉通过。

这里没有指定“水壶”，也没有要求旋转某个具体角度。人描述的是最终想看到的现象。

[原始请求](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/request.json)

请求指定房屋 `procthor-10k-train/train_0`，目标生成 3 个通过场景，最多 4 轮、48 次样本尝试、24 次 Agent 调用，总时限 600 秒，种子为 42。

`sample_000002` 表示从零开始编号的第 3 次样本尝试，不是第 3 个通过结果，也不是一段视频的第 3 帧。

## 4. Agent1：把“朝向不同”整理成需求单

Case Template（案例模板）是暂时不绑定具体房屋物体的需求单。本次包含两个角色：

```text
object_a：物体 A
object_b：物体 B
```

`$object_a` 是“稍后填入具体对象”的占位符，像表格里的空格。

模板要求：

1. A、B 在编辑前后都受支撑。
2. 它们的局部 X 轴夹角在指定范围内。
3. 图片中能区分两个物体，并看出最终朝向不同。
4. 如果物体近似旋转对称、没有清晰特征或被挡住，不能仅凭数值宣布视觉通过。

本次模板使用的参数是：

```text
orientation_proxy_axis = x_axis
orientation_difference_angle = [π/4, π] 弧度
                             = [45°, 180°]
source = heuristic_default
```

**45° 门槛和 X 轴选择是启发式默认值，不是用户给出的角度，也不是天然正确的视觉朝向标准。** 模板把来源明确记为 `heuristic_default`，并保留“X 轴是否对应可见方向尚未验证”的假设。

[Agent1 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/template.json)

初版每次请求重新规范化，不先检索历史相似 case。这个模板在本次运行中生成一次，再供后续提案和样本使用。

### “局部 X 轴”是什么？为什么不能只看它？

可以想象每个物体都随身带着三根互相垂直的小箭头：X、Y、Z。物体旋转时，这些箭头一起旋转。程序把两把水壶的 X 箭头换算到房间坐标，就能计算夹角。

但模型作者定义的 X 箭头，未必正好指向壶嘴。场景节点没有提供“X 轴就是壶嘴方向”的已验证标注。因此本例把 X 轴夹角当作**可计算的几何代理**，另外用壶嘴和手柄检查**人眼看见的朝向**。

例如，光滑圆球即使局部 X 轴转了 90°，图片也可能完全看不出变化；这样的样本不能靠数值直接通过视觉验收。

## 5. 场景准备：先让物体落稳，再记录真实状态

Scene Graph（场景图）不是照片，而是一份结构化房间清单：

- 节点：水壶、台面等对象，记录位置、姿态、类别、几何和可用编辑能力。
- 边：对象间关系，例如“水壶由台面支撑”。

程序先初始化并运行物理仿真，再生成稳定状态的场景图。本次基线图的仿真时间约为 **7.408 秒**，不是程序运行的实际耗时。

Agent2 后面选中的两个对象如下：

| 角色 | 实例编号末尾 | 实际类别与资产 | 编辑前世界坐标，单位米 |
|---|---|---|---|
| A | `…_1_0_2` | `Kettle` / `Kettle_1` | `(0.413146, 3.051078, 1.028589)` |
| B | `…_2_0_2` | `Kettle` / `Kettle_1` | `(0.489916, 2.441026, 1.028577)` |

完整编号都以 `boiler_335af387651d9869b84abf1fb099bf69` 开头。虽然名字带 `boiler`，本次场景记录的类别是 **Kettle（水壶）**，不能只靠英文编号猜物体类别。

[初始化与稳定化记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/graph.json) → [编辑前冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/scene)

支撑关系结合几何、落点、射线与承重接触观测建立，不只是“看起来在台面附近”。构建程序能够旋转某物体，也不等于机器人已经被证明能操作它。

## 6. Agent2：选两把水壶，提出带范围的 DSL

Agent2 接收模板和场景图，把两个角色绑定到真实对象：

```text
$object_a → boiler_335af387651d9869b84abf1fb099bf69_1_0_2
$object_b → boiler_335af387651d9869b84abf1fb099bf69_2_0_2
```

本例使用第 0 轮的 `kettle_pair_relative_yaw` 提案。DSL 是“专门表达场景编辑的小语言”。下面是核心操作的实际字段节选：

```json
{
  "op": "rotate",
  "subject": "$object_b",
  "axis": [0, 0, 1],
  "frame": "world",
  "angle": {"range": [-3.141592653589793, 3.141592653589793]}
}
```

逐行翻译：

- `rotate`：旋转，不是移动到另一个落点。
- `$object_b`：修改 B，不给 A 下发编辑操作。
- `axis: [0,0,1]`：绕世界竖直 Z 轴转。
- `frame: world`：这根旋转轴按房间的世界坐标解释。
- `angle.range`：在 −π 到 π 弧度之间选本次相对旋转角。

**角度是相对原姿态的增量，不是把物体“绝对朝向设置为 −128°”。** 本例长度用米、时间用秒、角度用弧度。

提案还要求：最终两者局部 X 轴夹角在 `[45°,180°]`，并且编辑前后两把水壶都受同一个指定台面支撑。前后支撑条件在整组编辑前和稳定后的端点检查，不要求构建操作每一瞬间都满足，也不代表执行了机器人搬运路径。

[Agent2 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0001-proposer.json) → [第 0 轮 DSL 提案](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/proposals/round_000.json)

本例只旋转 B，是实际选择的方案；**不是系统强制最小编辑**，也不是 A 获得了不可编辑权限。

## 7. 程序采样：把角度范围变成一次确定操作

Symbolic DSL 给范围；Executable DSL 给这一次真正执行的数值。

该提案使用 `coverage` 采样选择策略，样本使用种子 `42`、索引 `2`。实际角度为：

```text
relative_angle_rad = −2.242504588359857 弧度
                   ≈ −128.4869°
```

编译后的操作记录 B 的位置仍为：

```text
(0.48991624499126896, 2.441026278422985, 1.0285773299124576) 米
```

朝向则转换成四元数保存：

```text
quaternion_wxyz =
[0.6678076976240511, 0.6673921108337211,
 −0.23281600967095895, −0.2332752773696048]
```

四元数可以先理解成“计算机记录三维朝向的四个数”，顺序是 w、x、y、z，不是四个旋转角。程序把相对旋转与原姿态组合后，才得到这一组最终输入姿态。

[样本执行记录 sample.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/sample.json) 同时保存：

| 字段 | 看什么 |
|---|---|
| `proposal` | 原始角色绑定、范围型 DSL、目标和前后条件 |
| `executable.operations` | 实际旋转对象和编译后的位姿 |
| `executable.sampled_parameters` | 相对角度、种子、索引及参数来源 |
| `checks`、`settling` | 执行后的规则检查与稳定化记录 |
| `review`、`view_pair_id`、`status` | 最终视觉结论、选中的图片组和样本状态 |

这些内容在同一个文件中，没有另一个必须寻找的 `executable.json`。

## 8. 执行与规则校验：数字合格，但还不能宣布视觉通过

程序从编辑前冻结基线建立独立分支，执行 B 的旋转，再运行 **1.056 秒仿真、264 步**。记录为稳定、有效，没有新增求解器警告，未报告严重接触穿透。

| 对象 | 稳定后世界坐标，单位米 | 如何理解 |
|---|---|---|
| A | `(0.413088, 3.051113, 1.028642)` | 没有收到编辑命令，但物理仿真仍产生微小变化 |
| B | `(0.490053, 2.440986, 1.028601)` | 基本原地旋转，稳定后有微小位移 |

所以“原地旋转”不是指每个坐标的小数位绝对不变，也不能把正常稳定化漂移误认为程序主动移动了 A。

本例规则检查覆盖角色存在、前后支撑、稳定性、求解器健康、严重接触穿透、附近几何穿透和最终夹角。记录还检查了附近碗的物理支撑，不只检查 B。

最终局部 X 轴夹角为：

```text
2.2424931779820323 弧度 ≈ 128.4867°
```

它落在 `[45°,180°]` 中，相关规则检查通过。

[样本执行记录 sample.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/graph_after.json)

### 为什么相对转角带负号，最终夹角却是正数？

旋转增量有方向，负号表示按右手规则绕所指定轴的负方向旋转；两根轴的夹角则是不带转动方向的几何量，通常在 0 到 180° 之间。

程序把两根局部 X 轴转到世界坐标，得到方向向量 a、b，再计算：

```text
夹角 θ = arccos((a · b) / (|a| × |b|))
```

这里比较的是两把水壶最终的轴方向，不是直接把采样角取绝对值当成检查结果。两者在本例数值很接近，是由于两把水壶初始姿态接近；一般情况下不一定相等，稳定化也会造成小变化。

## 9. 首次 Agent3 检查：B 看清了，A 没拍全，因此不确定

程序先在 `rgb/` 中拍摄前后三角度共六张图，再把图像、模板、DSL、前后场景图和规则结果发送给 Agent3。

第一次调用是 [首次 Agent3 对话 0004-reviewer.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0004-reviewer.json)，结果保存在 [首次视觉检查 review_0.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/review_0.json)：

```text
verdict = uncertain
```

原因不是数学夹角不合格，而是：

- B 的壶嘴和手柄清晰可见，确实发生旋转。
- A 仅在一个斜视方向的画面边缘局部出现，其他视图中缺失。
- 因此，无法可靠比较“两个物体”的最终可见朝向，也无法从图片确认两者的可见支撑状态。

[首次配对记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb/pair.json) · [首次编辑前斜视 A](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb/before_oblique_a.png) · [首次编辑后斜视 A](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb/after_oblique_a.png)

有个容易看错的地方：首次 `semantic:1` 检查为 `pass`，不代表整份视觉检查通过。该条要求“证据不足时不能强行通过”，Agent3 确实保持了未知，因此这一条执行正确；整体意图和其他必要条目仍为未知，总体是 `uncertain`。

这一步体现了系统的边界：**规则给出的夹角不能补足缺失的视觉证据。**

## 10. 重新配对拍摄：不是改场景凑结果，而是补足观察

面对不确定结果，程序对同一个样本执行有限的配对重拍，输出 `rgb_retry_1/`。重拍更换相机观察方位，并扩大取景范围；每个新视角仍同时拍编辑前和编辑后，保证这一对图的相机一致。

重拍改变的是观察方式，不是另给 B 采一个角度，也不是把 A 搬过来。原场景编辑、采样角度和规则检查仍属于同一个 `sample_000002`。

| 角度 | 最终验收编辑前 | 最终验收编辑后 |
|---|---|---|
| 俯视 | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_top.png) | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_top.png) |
| 斜视 A | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_oblique_a.png) | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_oblique_a.png) |
| 斜视 B | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_oblique_b.png) | [打开图片](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_oblique_b.png) |

[最终验收配对记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/pair.json) → [重拍后的 Agent3 对话 0005-reviewer.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0005-reviewer.json) → [最终视觉检查 review_1.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/review_1.json)

第二次 Agent3 判定为 `pass`，依据是：

1. 两把水壶在前后斜视 A 图中可见且可按位置区分。
2. B 的壶嘴与深蓝手柄明确显示转向，并与 A 呈不同方向。
3. 俯视图也支持 B 的朝向变化，前后背景构图一致。
4. 前后视图与图结构支持两者没有离开台面。

第二次检查仍保留两个未验证结论：局部 X 轴是否直接对应壶嘴/手柄没有被标注验证；机器人可达性、实际操作难度和任务成功没有得到证明。

样本的 `view_pair_id` 对应 `rgb_retry_1/pair.json`。因此阅读交付结果时，应配对查看 **`rgb_retry_1/` + `review_1.json` + `0005-reviewer.json`**，不能拿首次图片和最终通过结论混在一起。

## 11. 最后保存什么？为什么编号中间有空缺？

规则通过、最终视觉通过且布局不重复后，`sample.json` 状态为 `accepted`，程序保存 [编辑后冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/scene)：

| 文件 | 用途 |
|---|---|
| `model.mjb` | 编译后的 MuJoCo 场景模型 |
| `initial.npz` | 恢复场景所需的状态数据 |
| `instances.json` | 物体实例映射 |
| `version.json` | 格式和版本信息 |
| `checksums.json` | 文件校验信息 |

这不仅是截图交付，还留下可恢复的编辑后场景。原始房屋资产保持不变。

该运行最终尝试了 5 个样本，接受 3 个：`sample_000000`、`sample_000002`、`sample_000004`，达到目标后停止。编号反映尝试顺序，所以通过清单可以有空缺。它们从同一基线独立编辑，不是上一份结果继续旋转后的连续三帧。

[最终运行结果](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/result.json) 中的结论分开记录：

| 项目 | 结果 | 能说明什么 |
|---|---|---|
| 场景合法性 | `valid` | 通过本次构建规则，不是完整物理证明 |
| 编辑满足度 | `pass` | 几何目标及已检查条件满足 |
| 语义对齐 | `pass` | 可用图片支持所需的可见朝向差异 |
| case 条件 | `unknown` | 没有证明真实机器人困难条件成立 |
| 任务完成 | `unknown` | 没有执行机器人任务验证 |

## 12. 自己跟着文件读，按这条路径就够了

1. [原始请求](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/request.json)：人要什么现象？
2. [Agent1 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/template.json)：角色、默认角度范围和视觉要求从哪里来？
3. [编辑前 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/graph.json)：两把水壶在哪里，由谁支撑？
4. [Agent2 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0001-proposer.json) → [第 0 轮 DSL 提案](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/proposals/round_000.json)：为什么选它们，提案怎么写？
5. [样本执行记录 sample.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/graph_after.json)：实际转了多少，稳定后的夹角与位置是多少？
6. [首次配对记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb/pair.json) → [首次视觉检查 review_0.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/review_0.json)：首次为什么不能通过？
7. [最终验收配对记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/pair.json) → [最终视觉检查 review_1.json](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/review_1.json)：重拍如何补足证据？
8. [编辑后冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/scene) → [最终运行结果](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/result.json)：保存了什么，哪些结论仍未知？

**本例不是“让 AI 说一句旋转就完成了”。真正的闭环是：理解现象 → 绑定真实对象 → 采样具体操作 → 程序执行与测量 → 图像证据不足时保持不确定 → 配对重拍 → 通过后保存可恢复场景。**
