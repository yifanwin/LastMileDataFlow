# 自动 Case 编辑运行说明

当前唯一入口仍为 `case-edit`，已升级为四角色、局部上下文、真实 head 与动态辅助观察的单场景构造流程。**不是已完成的移动抓取验收器。**

## 运行

从 `LastMileDataFlow/` 执行：

```bash
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow case-edit \
  --request configs/case_edits/case1-val103.json \
  --api-settings configs/agent_api.json --provider xera \
  --run-id <新的唯一ID>
```

其他请求：`case1-5-val103.json`、`case3-val103.json`。三个配置均不指定物体实例、支撑实例、机器人 base 或编辑位姿。路径相对请求文件；部署时修改数据和机器人资产位置。

- 请求版本为 `0.2`，详见 `construction/scene_request.py`。
- `scene_input.kind=single`：精确解析 `val-103` / `val_103`，或显式 XML；不调用房屋检索。
- `kind=dataset`：保留输入契约，但返回 unsupported，不偷偷挑第一栋房屋。
- `verification_mode=construction_only`：可以产生 construction_accepted。
- `verification_mode=mobile_task`：当前返回 unsupported；不会用固定底盘结果替代。
- `target_variant_count` 是构造数量，不是输入房屋数量。
- `budgets` 管调用、轮次、采样和总时间；CLI 使用可终止子进程覆盖 native 编译、渲染与 HTTP。
- `budgets.agent_timeout_s` 控制单次模型服务等待，类默认 120 s；三个真实请求均显式设为 240 s，以覆盖实际长提议响应。实际等待仍取该值和剩余总预算的较小者，不增加调用次数或放开 900 s 总期限。

## Agent 与编辑

中文公共/角色指令统一在 `agents/prompts.py`，分别由原有 normalizer、proposer、reviewer 和新增 strategy 模块使用。

模板保留原有规则子契约：`template_version=0.1`；DSL 和 Review 规则子契约也仍为 0.1。新增任务假设和请求属于新流程契约，不意味着旧谓词引擎已经支持机器人可达判断。

`semantic_checks` 和 `task_hypotheses` 为字符串数组，视觉检查 ID 由程序生成，格式为 `layout:N`。原始整段意图不再作为额外必过视觉检查。策略的 `contrast_spec` 使用 `work_region_id`、`role`、`expected_mechanism`。case1.5 要求三个不同区域的 A/B/C 假设。

资产当前接入 THOR 类别目录；按需实际加载，派生 XML 只写入本次输出。Objaverse、语言向量检索和资产预览尚未接入。障碍资产不要求人工 verified 标签或抓取数据。

机器人实际采样并放置少量合法初态，默认目标3个；至少一个合法候选即可继续。保存各候选 head 预览并选择目标实际可见像素最多者，不证明导航可达。

当前拍摄 head 和动态辅助配对图，前图直接复用于 proposer/reviewer。辅助图依据物体与工作区域采样，检查实际地面覆盖、附近几何、视锥、射线和分割可见性，墙体不隐藏。Agent3/4 的结构化补图请求会实际执行并返回原阶段，不重新编辑；有界失败记录 information_insufficient。这只是可观察性规则，不是可操作性证明。

原始 XML 通过 `scenes/mjcf.py` 只读解析为字符串，并保留原模型目录；include XML 使用唯一虚拟文件名，资源仍指向原目录，避免原生文件解析反复探测网络资产。复杂 include 目录覆盖保留原生路径处理。此路径仍从原始 XML 编译，不加载历史已编辑快照或继承成功标签。

统一 wire Schema 在 `agents/contracts.py`，同时进入提示输入、HTTP 结构化输出和本地校验。Reviewer 输出 checks/layout:N、information_request、agent_assessment；程序附身份、保留任务假设并汇总 verdict。移动收益意见不作为硬验收。格式错误带上一响应和路径修复，最终协议错误停止该运行，不重新编辑。

Agent3/4 使用局部图的几何投影：保留全部局部节点 ID、边、位姿、世界包围盒及区域坐标轴，省去重复支撑射线和网格足迹，并将提示数值舍入到 1e-6。完整图和原精度物理计算不变；投影显式标记 source_graph_id，不能当成完整物理快照。

## 当前使用宽松构造阈值

按用户 2026-10-07 的要求，`case-edit` 默认读取 `configs/case_edits/settling.json` 和 `views.json`。低层配置类保留严格默认值，不改变其他阶段历史协议。

- 原始场景及机器人初始化后的源场景：实际演进 1 s，不要求全场景动态物体满足稳定窗口；记录测量和 `stable=false`，不伪称已经稳定。
- 编辑分支：仅编辑、新增和关联跟随移动的动态对象要求稳定；未编辑的原始动态物体不因稳定窗口阻断，但仍检查碰撞和被扰动物体的支撑。完整物理场景不裁剪。
- 非有限状态、新求解器警告和超过配置的严重穿透仍阻断；支撑条件仍在前后端点检查。

| 检查 | 原阈值 | 当前构造配置 |
|---|---:|---:|
| 稳定窗口 | 0.25 s | 0.12 s |
| 线速度 | 0.025 m/s | 0.15 m/s |
| 角速度 | 0.10 rad/s | 0.5 rad/s |
| 窗口位移 | 0.006 m | 0.03 m |
| 窗口旋转 | 0.04 rad | 0.15 rad |
| 严重穿透深度 | 0.01 m | 0.03 m |
| 支撑高度容差 | 0.008 m | 0.02 m |
| 支撑边界容差 | 0.001 m | 0.005 m |
| 前后机器人组漂移 | 0.002 | 0.02（底盘 XY 为米，其余相应关节单位） |
| head 相机位置漂移 | 0.002 m | 0.02 m |
| head 旋转矩阵差范数 | 0.003 | 0.03 |

前图仍复用同一个初态，编辑后不重新设置机器人或移动相机；允许自然演进产生上述容差内的漂移。实际阈值随运行配置、检查和配对图落盘，不由 Agent 临时修改。宽松构造通过不等于严格物理稳定或机器人任务成功。

## 结果位置

```text
outputs/case_construction/<run_id>/
  request.json / configuration.json / source.json / template.json
  source_baseline/{scene,graph.json,preparation.json}
  agents/<调用编号>-<角色>.json
  contexts/round_NNN/
    candidates.json / construction_plan.json / local_graph.json / proposals.json
    initialization/{baseline,candidates/*.png,observation/,initialization.json}
    assets/{catalog.json,retrieval.json,派生资产目录}
  samples/sample_NNNNNN/
    sample.json / executable_edit.json / rule_checks.json / graph_after.json
    rgb/{before_head.png,after_head.png,before_aux_*.png,after_aux_*.png,pair.json}
    reviews/*.json / semantic_review.json / scene/ / task_validation/status.json
  result.json / progress.json
```

没有通过相应阶段时，不会伪造后续文件。构造接受数为 `construction_accepted_count`；`case_verified_count` 目前为 0。`completed` 仅指构造目标达到，不能作为整个版本交付完成。

## 模型服务

`agent_api.json` 当前默认且仅启用 Xera，DMX 禁用，不再作为 fallback。`--provider auto` 也只会访问 Xera；显式选择禁用的 DMX 会拒绝。所有 HTTP 调用计入共享预算，不输出密钥。

按用户指定使用 **Chat Completions + JSON Schema**：`POST /v1/chat/completions`，`response_format.type=json_schema`；不改用纯文本 `/completions`，也不混用 Responses 的 input/output 格式。接口参考：[Chat Completions](https://docs.newapi.pro/zh/docs/api/ai-model/chat/openai/createchatcompletion)、[Responses](https://docs.newapi.pro/zh/docs/api/ai-model/chat/openai/createresponse)。

当前 Xera 设置 `json_schema_strict=false`：模板/DSL 包含动态角色字典和可选字段，不满足 strict 子集约束。仍发送原 wire Schema，本地 JSON Schema 与领域检查保持严格。显式 json_schema 不静默降级；遇到临时服务错误可以重试，但不会改为 text 或丢弃图片。

### Agent 请求重试

每个逻辑请求**最多尝试5次，包含首次请求**，即最多额外重试4次。网络重试、格式修复和通用后端能力/provider 切换共用这个上限，不叠加成25次。每次 HTTP 都扣共享调用预算；整轮时间预算优先，余额不足可以提前停止。

- 超时、连接异常、HTTP 408/409/425/429 和 5xx 按2、4、8、16秒退避；余额/计费不足不盲目重试。
- 结构或领域解析失败携带上一响应和格式错误反馈，在同一个逻辑请求、同候选和同配对图上修复。
- 非法参数、鉴权/权限失败及显式 Schema 能力拒绝不重复请求相同错误；不换格式绕过。截断/拒绝仍拒收。
- 每次调用记录 `attempt`、`max_attempts`、状态与退避时间，不记录密钥。重试不处理 worker SIGTERM，也不重新编辑场景。

旧三 Agent/全图/六视图批量入口和旧请求配置已替换，不维护平行版本。底层 DSL、事务、几何及 provider 回归继续保留。历史输出只读，不作为新三类 case 成立证据。

## 模型和观察配置

- `--model glm-5.3` 可显式覆盖本次模型，不修改 `agent_api.json` 或本地独立密钥；默认仍使用配置的 gpt-5.6-sol。
- provider 可选 `structured_output=auto/json_schema/json_object/text`。auto 只在服务明确拒绝格式能力时有界降级；所有请求计入预算。本地 Schema 和领域检查不降级。
- 上一条是通用后端能力；当前 Xera 配置明确为 json_schema，不启用格式自动降级。无图时使用字符串消息；有图时保留 text/image_url 内容，图像不支持不会静默丢图。
- 配置 `construction.initialization_candidates`（默认3）、`max_information_requests`（默认2）；ViewConfig 的 `initial_aux_views`（默认2）、`max_aux_views`（默认3）、`max_camera_trials`（默认32）管理观察预算。
- 本轮没有底盘或抓取任务；CuRobo 比较为后续接口，task_validation 明确 not_tested。偶发 SIGTERM 不专项修改信号处理。
