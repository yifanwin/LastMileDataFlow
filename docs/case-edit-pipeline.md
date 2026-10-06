# 自动 Case → 场景编辑（v0.1）

**新入口已实现，2×2 真实批量验收通过；结果单独记录在[交付报告](../reports/case-edit-delivery.md)。**
这是独立于旧 `build` 的 Agent + 规则程序入口，不再人工填写 target/support/编辑坐标。
旧人工配置路径见 [case-to-edit-pipeline.md](case-to-edit-pipeline.md)，任务细目见[阶段计划](case-to-edit-pipeline-plan.md)。

## 1. 数据流与职责

```text
抽象描述 + 场景来源 + 生成预算
  → Agent1：本次规范化 CaseTemplate（无检索）
  → 加载、机器人自动初始化、真实静置 → 不可变 baseline + Graph₀
  → Agent2：模板 + 实际图 + 操作能力 + 失败反馈 → 多个 SymbolicDSL
  → 程序：联合采样 → ExecutableDSL → 独立事务执行整组操作
  → 静置、重新测量 Graph₁、端点规则与严重穿透检查
  → 固定相机 before/after 三视图 → Agent3：pass/fail/uncertain
  → 规则通过 + 视觉通过 + 新布局 → 冻结场景；否则丢弃分支
  → 达到目标数或共享预算终止
```

无最小编辑排序、对象权限表、审批或人工资产资格标签。家具、多对象、大位移和大角度不被降权。
操作实际能力、格式、引用和物理检查仍保留；这不等同于机器人抓取能力证明。

| 模块 | 程序接口 / 实现 |
|---|---|
| 契约 | `construction.case_schema`、`construction.dsl`：严格字段、版本、引用与单位 |
| 场景事实 | `runtime.preparation.prepare_scene`、`scenes.graph.build_scene_graph` |
| 查询 | `validation.predicates`：supported / supported_by / distance_xy / distance_3d / direction_angle / inside_region |
| 编译 | `construction.compiler.compile_sample`：按序临时布局、联合足迹约束、带种子参数 |
| 事务 | `runtime.case_edit_session.CaseEditSession`：基线独立分支、整组执行、规则检查、接受/回滚 |
| 资产 | `catalog.edit_assets.EditAssetCatalog`：普通可编译 MJCF，无 verified 标签 |
| Agent | `agents.case_normalizer` / `edit_proposer` / `edit_reviewer`，共享 `CaseGateway` |
| 视图 | `recording.edit_views.render_edit_pair`：六张真实 RGB、统一并集构图 |
| 批量 | `workflows.case_edit.run_case_edit`；CLI 用 `supervised_case_edit` 的可终止进程 |

## 2. 契约与语义

六种 v0.1 契约和 JSON 样例位于 [tests/fixtures/case_edit](../tests/fixtures/case_edit/)。

- **Request**：`case_description`、`scene_source`；可选 task_context / difficulty_profile / generation。
- **CaseTemplate**：intent、roles、parameters、requirements、invariants、semantic_checks、pending_hypotheses；保留原描述，不绑定具体实例。语义检查只描述单个编辑前后现象，不把“采样覆盖/去重”等流程要求强加给每张图。
- **Condition**：结构化 predicate / args；布尔 value 或数值 range/min/max。模板数值范围通过命名参数记录 user_input / difficulty_profile / heuristic_default 来源；启发式不是实证困难阈值。
- **SymbolicDSL**：bindings、按序 operations、附加 goals/invariants、sampling。附加条件与模板取合取，不能覆盖模板。
- **ExecutableDSL**：稳定实例名、世界 position 与归一化 quaternion_wxyz、采样参数和实际绑定。
- **Review / RunResult**：required unknown 不通过；accepted 与 attempted 分开，机器人 case/task 结论始终 unknown。

长度 m、时间 s、质量 kg、角度 rad，右手系世界 z 向上，四元数 `[w,x,y,z]`。
相对旋转绕物体原点：world 轴 `q_delta * q_before`，object 轴 `q_before * q_delta`，不暗中转换为绝对 yaw。
距离用对象原点 / 初始机器人底盘 XY 投影到 z=0 的站位；后者是几何代理锚点，不是完整可达区域。
方向可用局部 x/y/z 轴或显式向量；未标注的“物体正面”返回 unknown。

### 操作边界

- `move`：支撑区域的 xy 是区域局部坐标；无限 floor 需有限世界 x/y 范围。没有 support/具体 region 时，可显式 `frame: world` 指定有限 xy，保留当前高度。offset 是世界轴偏移。
- `rotate`：相对 axis-angle，可 world/object；对象原点不平移。
- `add`：资产选择与放置，新角色只在 add 之后可引用；新对象的支撑检查放 after goals，不能放 before invariant。
- `remove`：删除顶层实例，更新模型索引/图；删除 after 必需角色是目标冲突，不是越权。
- `carry_supported`：仅展开实际基线支撑边；此前显式移走的子物体不再联动，预测放置不能冒充已测支撑。

Invariant 在**编辑前静置状态**与**整组编辑后静置状态**各检查一次，不检查中间步骤。
新试次始终从同一基线开始，不累计上一试次位姿。执行 begin 后不允许编辑或恢复；源 XML/外部资产只读。

## 3. 运行

从工程根目录执行；正常输入不需要人工实例绑定或目标坐标：

```bash
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python MUJOCO_GL=egl \
  bin/lastmile-dataflow case-edit \
  --request configs/case_edits/distance-train2.json \
  --api-settings configs/agent_api.json \
  --settle-config configs/case_edits/settling.json \
  --view-config configs/case_edits/views.json \
  --run-id my-case-edit
```

场景路径相对 request 文件；其他 CLI 路径相对当前目录。可用 `--initial-snapshot <matching-scene/>`
节省编译，但仍实际初始化/静置且核对来源，不继承旧 build 对象白名单。
`--asset-catalog` 启用添加；默认空资产目录，不能凭空猜资产。
服务配置沿用 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL；密钥不写入产物。
配置了外部服务就会发送描述、图与六张 RGB，应先确认数据可发送；本次 DMX 和频道二服务均由用户显式指定用于验收。

### 两个 provider 与选择

`agent_api.json` 使用独立 provider 配置（示例不含真实密钥）：

```json
{
  "provider": "auto",
  "providers": [
    {"name": "dmx", "LLM_API_KEY": "", "LLM_BASE_URL": "https://www.dmxapi.cn/v1", "LLM_MODEL": "gpt-5.6-sol"},
    {"name": "xera", "LLM_API_KEY": "", "LLM_BASE_URL": "https://newapi.x-era.com/v1", "LLM_MODEL": "gpt-5.6-sol"}
  ]
}
```

填入各自密钥后启用；空密钥默认禁用，显式 `enabled:false` 可关闭。
`provider: auto` 在**每个逻辑 Agent 调用**先访问列表第一个已启用 provider，服务错误再访问第二个；下一次调用仍优先第一个，不无限循环。
`provider: dmx/xera` 只使用指定 provider，不自动访问其他服务。CLI `--provider auto|dmx|xera` 优先于 JSON 配置。
HTTP 错误/超时触发 auto 切换；输出格式错误做有限格式重试。每次实际请求，包括 provider 切换，都计入共享调用预算。
调用日志只含 provider 名、主机、模型及选择模式，不含密钥。旧三个顶层字段的 JSON / `.env` 保持兼容。

共享预算：轮数、样本次数（含编译失败）、Agent 调用（含格式重试/补拍）、墙钟时间。
单次服务超时不超过 120 s 或剩余总预算；格式重试有界；参数失败换采样；连续 4 次拒绝后换策略并保留具体测量反馈；before 不适用 / 补拍后仍 uncertain 换绑定或提议；不重置预算。
布局按实际资产/最终位姿去重，排除无变化，忽略随机新增名、提议 ID 与四元数符号。

## 4. 接受条件、产物与终止

接受必须同时满足：必需角色、两端 invariants、after requirements、静置/支撑/严重穿透规则、Agent3 pass、布局唯一。
全量 graph facts 由实际模型/接触/几何测量得到，parent metadata 不算支撑证据。
相机 fit 覆盖前后受影响几何并集；top 带站位上下文，两个斜视角近看受影响对象及自由根参照物，避免远站位使全部视图过小。每个视角两端同位姿、FOV、分辨率和基线光照。补拍成对换角度，不单独追踪 after。
遮挡不是“图中有物体”的证明，有限补拍后 unknown 仍拒绝。

```text
outputs/case_edits/<run_id>/
  request.json, configuration.json, template.json, result.json, progress.json
  baseline/{scene/, graph.json, preparation.json}
  agents/<call>-<role>.json                 # 无密钥，含调用/解析状态
  proposals/round_NNN.json
  samples/<id>/{sample.json, graph_after.json, rgb/, review_N.json}
  samples/<accepted-id>/scene/              # 真正通过后才冻结
```

`completed` 仅表示目标样本数达到；budget_exhausted 保留已经通过的子集。
设施错误、无效基线、格式失败、中断有独立分类；真实渲染失败不生成替代图。CLI 仅 completed 返回 0。

## 5. 已知限制

- 初版支持普通顶层固定/自由根实例；关节化根、嵌套根的移动及复杂/倾斜支撑尚不支持。
- 几何足迹保守，可能拒绝实际上可放置的候选；采样耗尽不代表数学无解。
- before visibility 尚无可靠自动证明，依赖 Agent3 与换绑定反馈，可能耗尽预算。
- 六图证明的是可见编辑语义，不证明 reachability、导航、抓取或实际任务失败。
- train_0 需要更长真实静置预算（上限 10 s），没有降低稳定/穿透阈值。
- 当前环境真实 EGL 可用；没有据此声称 NVIDIA 驱动/规划 GPU 验收通过。
