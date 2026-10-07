# Case 构造修复与 Xera 联调报告

更新：2026-10-07。**158 项回归通过；Xera / gpt-5.6-sol 下 case1 已交付1个真实构造接受样本。case1.5 两轮独立运行未接受，case3 规则通过候选因检查修复遇到硬期限未接受。底盘移动、抓取与三侧优劣均未验证。**

## 执行过程与修改

沿用现有模块和目录，不建立 v2 平行实现，不改原始资产或历史结果。

1. 统一 `agents/contracts.py`：同一份 wire Schema 进入提示输入、HTTP 和本地校验；领域解析继续检查引用、参数来源和 DSL。格式修复携带错误路径与上一响应，在同候选进行；最终协议失败停止，不重新编辑。
2. 分开物理规则、可见布局、Agent 移动收益意见和真实任务验证。Reviewer 不再生成身份、intent 额外检查或成功标签；未知不变成通过。修正把“不能证明抓取成功”的限制语句误识别为任务执行要求的问题。
3. 实体机器人实际独立放置多个初态，默认3个，选目标像素最多者；保存各 head 预览。编辑前后复用同一初态，不执行底盘移动或抓取。
4. 动态辅助相机检查地面、附近几何、视锥、射线和实际分割；保留墙体。Agent3 补图/扩展/资产请求真正执行并回到原阶段；Agent4 补图后复查同一候选，固定前后相机。
5. Agent3/4 接收局部图几何投影，保留节点、边、包围盒、位姿及区域轴，去掉重复支撑观测和网格足迹。物理计算仍使用原精度完整图。真实对照输入由约56万字符降到15万字符。
6. 保持宽松构造阈值、取消源场景稳定窗口门槛；不增加最小编辑、权限审批或额外审计。不通过忽略 SIGTERM 绕过 worker 退出。

7. 原始 MJCF 改为只读文本解析和 include VFS，保留原资源目录与同名 include 的独立身份；特殊目录覆盖回退原生加载。原生加载曾反复探测 NAS 资源路径，替代入口使原场景和机器人完成新编译；不修改原 XML、网格或物理参数。
8. 局部候选按支撑面分层随机采样，先覆盖不同桌面/柜面再重复同一支撑；避免一个拥挤台面占满候选。仍由Agent选择具体上下文，不手工绑定餐桌/目标，也不将采样覆盖作为可达证明。
9. Agent 每个逻辑请求最多5次尝试（含首次），网络重试、格式修复和通用后端切换共用额度；临时错误按2/4/8/16秒退避，永久错误不重发，共享预算优先。布尔条件的 `value` 已纳入 wire Schema 必填，操作 subject/bind_as 限定 `$role`；明确 xy 是完整足迹容纳区域而非中心点范围。

## 最终服务配置与真实验证

[`agent_api.json`](../../configs/agent_api.json) 当前默认并仅启用 **Xera / gpt-5.6-sol**，DMX 禁用，auto 也无法访问 DMX。密钥未输出、未复用。

采用 `POST /v1/chat/completions` + `response_format.type=json_schema`。模板/DSL 包含动态字典及可选字段，当前服务端设置 **strict=false**；发送的 Schema、本地 JSON Schema 校验和领域检查仍保持一致，不能把模型响应直接当作合规。显式 json_schema 模式不自动退回 json_object/text。无图调用使用字符串消息，有图调用保留真实 image_url。

文档核对：[Chat Completions](https://docs.newapi.pro/zh/docs/api/ai-model/chat/openai/createchatcompletion) 使用 messages；[Responses](https://docs.newapi.pro/zh/docs/api/ai-model/chat/openai/createresponse) 使用 input；[Completions](https://docs.newapi.pro/zh/docs/api/ai-model/completions/createcompletion) 使用纯文本 prompt。本轮不混用三者。

| 验证 | 实际结果 | 证据 |
|---|---|---|
| GPT 模板，最终 JSON Schema 配置 | 一次调用通过 Schema 和模板领域解析，约64 s | [结果](../../outputs/case_construction/protocol-probe-2d30c4430d9d/result.json) |
| GPT 带图 Reviewer，最终 JSON Schema 配置 | 一次通过，保留任务 unknown，不接受任何场景 | [结果](../../outputs/case_construction/model-comparison-dd72b8a7b551/result.json) |
| 同真实 RGB、精简上下文，临时 text 返回诊断 | GPT 通过；GLM 仍 HTTP400，检测到 type 只允许 text | [对照](../../outputs/case_construction/model-comparison-86a97fb2b046/result.json) |
| 两模型极小纯文本诊断 | GPT、GLM 均返回合规 JSON | [诊断](../../outputs/case_construction/protocol-probe-23ff0d3a77bc/result.json) |

**GLM 的带图错误不能只归因于 JSON Schema。** 去掉 response_format 后仍失败，而极小纯文本成功，指向该路由的图像/内容格式兼容限制；具体字段和渠道能力还需确认，不能据此宣称模型本身不支持视觉。没有把图片静默丢弃，也未将 GLM 替换成主模型。临时 text 诊断已结束，最终配置为用户指定的 JSON Schema。

上述 Reviewer 使用历史真实候选 RGB，并明确只测协议和一个可见布局条件；不是新 case3 验收，也不证明路径碰撞或移动收益。

## val-103 全链路执行

每轮均从原始 val-103 XML 重新编译，Agent1 重新规范化，不复用旧编辑或成功标签。源准备前后显式记录阶段。三类请求单次 Agent 超时为240秒，整轮硬期限900秒，共享调用上限20；最多5次不承诺在预算不足时仍尝试满5次。

| case | 运行 | 实际状态 |
|---|---|---|
| case1 | [`construct-29365f8d6330`](../../outputs/case_construction/construct-29365f8d6330/result.json) | 1次真实编辑，1个构造接受，4次 Agent 调用 |
| case1.5 | [`construct-1246813fc0b7`](../../outputs/case_construction/construct-1246813fc0b7/result.json) | 9个采样候选全部被足迹约束拒绝；7次 Agent 调用，900秒硬期限停止，0接受 |
| case1.5 重跑 | [`construct-bc802bb13cdb`](../../outputs/case_construction/construct-bc802bb13cdb/result.json) | 分层候选含餐桌；所选书本未找到合法可见初态，后续扩展后 no_context；4次调用，轮数预算耗尽，0接受 |
| case3 | [`construct-b1bfbe3383e3`](../../outputs/case_construction/construct-b1bfbe3383e3/result.json) | 7个候选；末个规则通过并生成6张RGB；Reviewer配对引用修复未赶上900秒期限，0接受 |

### case1 的接受交付物

[`samples/sample_000000/`](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/) 包含实际 DSL 执行结果、规则检查、前后图、Reviewer、MJB/初态快照和校验摘要。实际找到3个机器人初态，目标可见像素为170、209、1701，选择第13号候选（1701像素）。编辑移动了一个台面障碍物，目标和支撑保留，不执行机器人任务。

| 编辑前 head | 编辑后 head |
|---|---|
| ![case1 编辑前](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/before_head.png) | ![case1 编辑后](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/after_head.png) |

[RGB 目录](../../outputs/case_construction/construct-29365f8d6330/samples/sample_000000/rgb/) 含 head 与 aux_000 共4张 RGB 和 pair.json；另有目标 mask，不计 RGB。已实际查看前后 head/aux 图，并核对配对身份、初态/相机检查、图像发生变化和冻结文件摘要。一个背墙工作区域仍未覆盖，不能据此证明全部 A/B/C。

### case1.5 失败与修正

程序真实从 THOR 检索到2把尺寸合适的椅子，而非虚构资产ID。Agent3 请求背侧辅助图后，程序实际生成 aux_001，返回同一步；背墙区域仍不可见，反馈正确为 information_insufficient，没有伪称覆盖。

随后提议连续漏写布尔支撑条件 value，两次格式错误后第3次尝试才完成解析，日志中 attempt=1/2/3，未突破5次上限。布局采样时，椅子深约0.47m而给定落地区域深仅0.19m，9个候选均被拒绝，无实际有效编辑样本。旧 worker 仍运行启动时契约；新契约要求布尔 value，提示明确完整足迹/墙体空间约束，已用于后续独立运行。不放宽物理规则、不重写旧结果。

分层重跑已将餐桌上的瓶子/生菜列入候选，但Agent先选书架书本。96个初态试验中67个地面/碰撞失败、17个目标像素不足、12个目标不在垂直视锥；不是证明整个房屋无合法初态。下一轮选择另一书本并扩展，当前实现扩展只保留该候选，随后 no_context、轮数耗尽。仍需改进失败上下文身份反馈与保留其它局部候选，而非降低可见性或碰撞门槛。

### case3 的规则通过候选（未接受）

[`sample_000006`](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/) 实际移动锅作为喷雾瓶周围的障碍，规则与固定配对检查通过。已实际查看head及两个辅助视角；head和俯视aux_001能看到锅的位移，aux_000被机器人部分遮挡，不将它冒充良好目标视角。此前5个实际执行候选因编辑物体稳定检查失败，另1个候选在编译采样时不满足距离条件。

| 编辑前 head（待复查） | 编辑后 head（待复查） |
|---|---|
| ![case3 待复查编辑前](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/before_head.png) | ![case3 待复查编辑后](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/after_head.png) |

[RGB目录](../../outputs/case_construction/construct-b1bfbe3383e3/samples/sample_000006/rgb/) 共6张RGB（head、aux_000、aux_001各前后），另有mask。Reviewer第一次返回可见布局pass，但其中一项只引用before图，被领域契约拒绝，未被当作接受。第2次格式修复仍在请求中时触发硬期限，保留 pending_review；没有接受快照或最终 semantic_review，不应作为已交付样本。日志存在8次已发起调用，result中的计数6是硬终止前的最近检查点，不代表少扣了HTTP次数。

### SIGTERM 与源加载

`construct-cc9381746d0d` 在已完成源准备和初态后收到 SIGTERM，exit=-15；supervisor 未主动终止，344.259秒小于900秒硬期限，发送者仍未知。下一轮 `construct-29365f8d6330` 正常完成，因此不是每轮必现。对自身进程的信号跟踪未捕获新的 SIGTERM，不据此猜测根因。

新源加载与历史**未编辑源基准**的15组模型数组完全一致，见[等价性检查](../../outputs/case_construction/construct-cc9381746d0d/source_loading_equivalence.json)，覆盖名称、位姿、质量/惯量、几何、网格、关节和执行器；这只用于加载等价性，不继承历史验收标签。此前缓存原基准的观察组件 [`observation-smoke-dcc0720f9952`](../../outputs/case_construction/observation-smoke-dcc0720f9952/result.json) 仍保留，但未编辑，不能算接受样本。

## 回归与剩余边界

命令：`MUJOCO_GL=egl CASE_EDIT_RENDER_TESTS=1 PYTHONPATH=src ../molmospaces/.venv/bin/python -m unittest discover -s tests -v`。

**158 项通过**，见[日志](../logs/case-edit-construction-regression.log)。覆盖实际 EGL、合成三类构造、多初态、同候选补图/格式修复、MJCF 资源路径与原文件不变、显式 Schema、禁用 provider，以及五次总尝试、退避、永久错误和共享预算。网络故障使用离线注入；不能宣称真实服务发生过并成功恢复了503。

三类真实构造接受样本尚未全部齐备。完整工作区域覆盖、CuRobo/底盘搜索与实际任务对照仍未完成。dataset 房屋检索、Objaverse/向量资产检索未接入。

所有样本 `case_verified_count=0`、`version_delivery_complete=false`；构造通过、case 成立、任务成功分别报告。

## 逐例完整解读

- [case1：锅铲不动、盐罐换侧](case-edit-val103-case1-sample000000-walkthrough.md)：构造接受样本。
- [case1.5：三侧差异的构造与失败](case-edit-val103-case1-5-walkthrough.md)：两轮独立运行均未接受，无编辑后RGB。
- [case3：把锅移到喷雾瓶近旁](case-edit-val103-case3-sample000006-walkthrough.md)：实际编辑和规则通过，视觉检查未闭环。
