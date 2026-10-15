# 第二轮精简方案：保留五个命令，把实现逻辑写直接

日期：2026-10-15  
范围：`src/lastmile_dataflow`，以及受影响的测试、配置和当前使用文档。  
状态：**方案，尚未实施；本文不代表代码回归或真实仿真已经通过。**

## 1. 结论与执行顺序

**五个命令全部保留。本轮首先精简函数内部的过度防御、重复处理和无效中转，再整理 `scenes`、`runtime` 的子目录。不是再砍一轮功能，也不是另建一套框架。**

建议按以下顺序实施：

1. 删除确定多余的局部判断、占位参数和无效包装。
2. 缩短记录链路，避免为不再使用的接口准备数据。
3. 简化配置检查、标定数据处理和场景构建中的重复工作。
4. 收敛异常处理和资源关闭责任，保留批量实验必要的失败隔离。
5. 按职责整理目录，同步所有在用导入与相关测试。
6. 跑代码回归，再做有界的真实主链检查，明确哪些分支实际执行了。

**不设强制删行比例。** 不能为了达标删掉必要物理判断，也不能用移动文件、压缩排版冒充轻量化。

## 2. 保留什么，不改变什么

### 2.1 五个入口及其现有职责

| 命令 | 必须保留的职责 | 不能混淆的边界 |
|---|---|---|
| `build-scenes` | 发现任务、资格筛选、场景增强、站位采样、保存场景库 | 合格场景不等于机器人任务成功 |
| `evaluate-stations` | 逐点执行 cuRobo / MolmoBot、保存 trial、生成站位统计和热图 | 几何采样不能替代真实 rollout |
| `collect-lastmile` | 复用匹配标定，或在当前 run 标定一次；执行 S0→S1→操作 | 当前连续采集仅支持 cuRobo，不借本轮扩展后端 |
| `build-bench` | 根据标定构建困难 S0，保存可独立加载的场景与 episode | 未标定 smoke 仍只能标为 smoke |
| `evaluate-bench` | 独立 MolmoBot 评测，汇总结果及覆盖率 | 缺失或基础设施错误不能被当作任务成功 |

保留 pick/open、现有场景增强规则、两种实际控制 profile、图像及按需视频/深度/物理日志。现有实验选项不因“不是默认值”就删除。

### 2.2 实验语义不随精简改变

- 不改变动作维度、单位、相对量/绝对量、控制节奏和实际关节限位。
- 不改变初始化与连续执行的界线；`begin()` 后不恢复、传送或重置。
- 不绕过构建期编辑、静置及失败回滚。
- 不改变抓取、开合、导航到达、标定完整性和统计分母的含义。
- 新试验使用新 run/attempt，不覆盖既有结果；原始资产和历史结果不修改。
- 未发生实际运动时不补造执行轨迹、视频或成功证据。

CLI 命令、主要参数和主数据格式默认保持。内部函数签名、模块路径可以直接调整，不为旧的内部调用方式增加兼容壳；仓库内调用方必须同步迁移。

## 3. 当前实现中，精简重心在哪里

以下为本次静态阅读时的物理行数，含注释与空行，不是有效代码量，也不是删除目标。

| 范围 | Python 文件数 | 行数 | 本轮策略 |
|---|---:|---:|---|
| 包根 | 4 | 282 | 配置入口和序列化保持简单 |
| `scenes` | 19 | 2,343 | 局部检查、重复构造、目录聚合 |
| `runtime` | 22 | 2,861 | 属性兜底、记录链路、异常与资源责任 |
| `workflows` | 5 | 735 | 重复读写、重复加载和无效参数 |
| `data` | 15 | 1,167 | 记录接口、标定计算和冗余结构检查 |
| `planning` | 8 | 1,101 | 固定后端接口；不动有依据的算法修正 |
| `policies` | 22 | 4,314 | 先精简客户端/服务边界，不盲删模型实现 |
| **合计** | **95** | **12,803** | **先改善实现，再统计实际减少量** |

其中 `policies/multitask` 为 **4,069 行**。模型算子、权重结构和必要推理逻辑不能因为文件长就认定冗余；本轮优先处理项目自己控制的运行链路。

现有 `execution.py` 为 456 行，混合上下文、导航和 cuRobo attempt 编排；这适合按职责拆开。但文件拆开本身不计作逻辑简化。

## 4. 删除防御代码的判断标准

**外部输入检查必要条件，内部按明确约定直接使用；没有恢复策略就不要用默认值掩盖错误。**

| 位置 | 应当怎么处理 | 不应怎么处理 |
|---|---|---|
| JSON 配置、外部资产、模型响应、落盘标定 | 保留影响运行和实验语义的必要检查 | 建一套通用校验框架，反复验证全部字段 |
| 构造函数已经定义的内部属性 | 直接访问；可选状态显式初始化为 `None` | 到使用处再 `getattr(..., 默认值)` 或 `hasattr` 补属性 |
| 当前函数生成的内部记录 | 必填字段直接读取；按生成端约定消费 | 逐层检查类型、补字段、重算后再互相核对 |
| 真实仿真反馈、任务判定 | 保留物理条件和必要数值有效性判断 | 把算法筛选或物理停止条件当作过度防御删除 |
| 批量执行边界 | 区分单 episode 问题和共享服务/GPU 问题 | 深层函数到处捕获异常，再重复分类和包装 |

不按关键词批量删除 `if`、`try`、`.get()` 或 `np.isfinite()`。例如“类别可以缺失”和“内部必填字段缺失”不是同一情况；前者的默认描述有实际意义。

## 5. 第一批：可以明确下手的局部精简

本节行号对应本次阅读的源码，实施后会变化。文件链接指向当前源码，不代表已经修改。

### 5.1 去掉已初始化属性的存在性兜底

涉及 [runtime/execution_checks.py](../../src/lastmile_dataflow/runtime/execution_checks.py) L18–42、[runtime/execution.py](../../src/lastmile_dataflow/runtime/execution.py) L46–64、L84–89。

- `ContinuousContext.__init__()` 已设置 `timings={}` 和 `pick_initial=None`。
- `timed()` 仍检查 `hasattr(ctx, 'timings')`，`pick_verdict()` 和 `could_have_success()` 仍用 `getattr`。
- `last_legal_torso_target` 已在构造时设置，读取时仍再次兜底。

改法：直接读取属性。保留 `pick_initial is None` 的业务判断，因为 open 或尚未建立 pick monitor 的状态确实存在；只删除“属性可能不存在”的额外防御。简化测试对象按真实上下文补齐属性，不为了 mock 缺字段保留生产兜底。

```python
# 当前：已有构造约定，仍在使用处补属性
if not hasattr(ctx, 'timings'):
    ctx.timings = {}
initial = getattr(ctx, 'pick_initial', None)

# 计划：依赖明确的上下文约定
initial = ctx.pick_initial
```

这类修改逐个确认构造入口，不能把其他确有多种对象类型的访问一并机械替换。

### 5.2 固定 planner 接口，不假装支持不存在的后端

涉及 [runtime/execution.py](../../src/lastmile_dataflow/runtime/execution.py) L187–212、[planning/curobo_v2.py](../../src/lastmile_dataflow/planning/curobo_v2.py) L26、L51。

当前 `mobile_tick()` 为 `planner.torso_active` 提供 `False` 默认值，为 `h_step_limit` 提供 `.00125` 默认值；当前 V2 planner 明确定义了这两个值。

改法：直接读取当前 planner 的实际属性。核对所有生产调用后，删除只为无躯干旧接口保留的分支；测试替身服从当前接口。关节顺序/维度一致性只在规划轨迹进入执行器的交界处检查，不在每个 waypoint 重复构造名字列表检查。

不要顺手改 yaw 语义：cuRobo 当前有限位底盘关节使用标量差，MolmoBot 导航的处理不同，已有测试明确区分。

### 5.3 删除仿真接口中恒为 `None` 的占位参数

涉及 [runtime/simulation.py](../../src/lastmile_dataflow/runtime/simulation.py) L155–195。

`_submit()` 返回 `(command, None, None)`；`step()`、`step_policy()` 拆出 `request_id`、`submitted_id` 后传给 `_advance()`，而 `_advance()` 不使用这两个参数。

改法：`_submit()` 只返回 `command`，`_advance()` 只接收实际使用的参数。同步两个调用入口。保留控制周期余数、策略的 100/20/4 ms 节奏、部分执行步数记录。

### 5.4 删除重复的数组复制

涉及 [runtime/action_limits.py](../../src/lastmile_dataflow/runtime/action_limits.py) 的 `sanitize_action()`。

`vector()` 已返回独立副本，`vector(value, 20).copy()` 再复制一次没有必要。先去掉这一处确定的重复，再沿实际动作调用链检查其他复制；用于保留原始动作或隔离可变数据的复制必须保留。

### 5.5 未初始化变量改成显式局部状态

涉及 [runtime/execution.py](../../src/lastmile_dataflow/runtime/execution.py) L421–432、[runtime/molmobot_execution.py](../../src/lastmile_dataflow/runtime/molmobot_execution.py) L251 附近。

当前通过 `locals().get('verdict')`、`locals().get('exception_detail')` 判断变量是否曾创建。

改法：在 attempt 开始处明确设置 `verdict = None`、`exception_detail = None`，结束时直接使用。保留超时终止与任务物理结果分开表达的现有语义，不借机合并状态含义。

### 5.6 删除不提供独立含义的包装和拒绝型参数

- [scenes/dataset.py](../../src/lastmile_dataflow/scenes/dataset.py) 中 `robot_descriptor()` 只调用 `asdict()`；改由调用处直接使用 `asdict()`。
- [config.py](../../src/lastmile_dataflow/config.py) 中 `construct()` 先检查未知字段，再调用 dataclass；构造器本身已经拒绝未知关键字。计划移除这次重复枚举，直接构造；检查相关测试是否依赖旧异常类型，确需入口提示时只在读取配置处处理一次。
- [workflows/scene_datasets.py](../../src/lastmile_dataflow/workflows/scene_datasets.py) L279–282 的 `workers=1`、`resume=False` 仅用于拒绝其他值；没有实际并行或恢复实现。确认仓库内调用后删除这两个内部参数，而不是继续保留“可配置但不支持”的接口。

不把这一步扩展成删除 `allow_partial_targets` 或未标定 smoke 等实际可用选项。

## 6. 第二批：让数据和调用链更短

### 6.1 记录器不再要求调用方准备它已经不使用的数据

涉及 [data/recorder.py](../../src/lastmile_dataflow/data/recorder.py) L46–47、L79–83，以及 cuRobo/MolmoBot 执行器的调用处。

当前事实：

- `intent(value, producer, metadata)` 实际只读取 `metadata.phase`。
- `record_step(raw_action, command, before, after, check, observations)` 实际只更新最后一项 phase。
- 但调用方仍组装动作列表、完整状态和观测字典。
- 轨迹已由 `Simulation` 的实际执行回调交给 recorder，并不是靠 `record_step()` 写入。

计划：在执行动作前明确设置 `recorder.phase`，让 `executed()` 直接记录正确 phase；删除只为补 phase 存在的 `intent()`、`record_step()` 及无效参数准备。对 `before/after` 逐个检查其他用途：视频时间、策略诊断仍需要的时间直接读时钟，不因删接口误删真实使用的数据。

另外清理未使用的 `strategy` 构造参数、`frame_index` 字段，以及在确认没有消费者后可删除的空返回值/临时字典。不能仅因 recorder 不消费某数据就宣称整个执行器都不消费。

### 6.2 初始帧、PNG、视频共用已有观测，不重复写

当前 `Simulation.begin()/executed()` 的 capture 路径与执行器的 observation 路径重叠，`observation()` 用 `path.exists()` 避免再次写同一 PNG。

计划：先理清初始帧和每次实际控制间隔的唯一记录位置，再移除多余 observation 调用。PNG/深度仍由 recorder 负责；视频及第三人称叠加复用已有帧。确认重复写入已消除后，再判断 `path.exists()` 是否还需要。

不要先删文件存在判断而保留双写，也不要把 `steps + 1` 的状态/动作对齐关系改掉。保留有作用的帧缓存，不另建传感器框架。

### 6.3 用局部计数生成编号，不为编号反复扫描整个文件

涉及 [workflows/collection.py](../../src/lastmile_dataflow/workflows/collection.py) L30–38、[workflows/lastmilebench.py](../../src/lastmile_dataflow/workflows/lastmilebench.py) L67。

当前每次 `_execute()` 都重读 `trials.jsonl` 和 `episodes.jsonl` 计算 attempt 编号；bench 构建每个 episode 也重读已写文件。

计划：在新 run 的串行循环中维护局部计数，生成编号后立即递增。trial 与 recovery 共用同一 attempt 计数，场景之间不重置；无路径记录不创建执行产物。保留新目录禁止覆盖，不加编号注册表、恢复扫描或持久化索引。

### 6.4 配置和机器人信息在一次 run 中读取一次

- `collection._prepare_run()` 与 `_run_dataset()` 重复读取同一场景机器人配置；准备时保留已解析 robot，执行时复用。
- `SceneDatasetRecipe.geometry` 每次访问都会重新构造配置并执行检查；在 discovery/qualification/build 的各自函数入口取局部 `geometry`，循环内复用。
- `build_scene_dataset()` 的 robot 配置移至循环外加载，不在每个场景和增强调用前重复读取。
- 不新增全局缓存、配置注册中心，也不把固定值提成几十个单行工具函数。

### 6.5 配置检查只保留清楚、必要的一次

重点文件：[config.py](../../src/lastmile_dataflow/config.py)、[scenes/dataset_config.py](../../src/lastmile_dataflow/scenes/dataset_config.py)、[scenes/obstacle_config.py](../../src/lastmile_dataflow/scenes/obstacle_config.py)、[runtime/execution_config.py](../../src/lastmile_dataflow/runtime/execution_config.py)。

计划减少字段逐项类型穷举、对 bool/int 的层层区分、摄像头别名字符规则、对内部生成列表反复验证元素类型等代码。优先保留直接影响执行的条件：正步数/时间预算、阈值区间、合法动作范围、后端/profile 匹配、需要的策略路径。

`ExecutionConfig.__post_init__()` 当前根据字段名后缀猜数值约束。改为少数明确的条件，不再隐式根据名字决定业务规则；同时不引入新的通用 schema 校验器。

配置默认值统一由配置对象提供。只有确认字典来自完整 `asdict(CollectionConfig)` 时，才将反复 `.get('record_images', True)` 等改成直接读取；来自外部的可选字段继续按实际含义处理。

### 6.6 标定计算信任自己的输出，但继续检查外部标定

涉及 [data/selection.py](../../src/lastmile_dataflow/data/selection.py)、[data/benchmark.py](../../src/lastmile_dataflow/data/benchmark.py)。

当前 `station_calibration()` 生成站位计数，`select_starts()` 又逐项检查计数类型及一致性，`recovery_choices()` 还会再次计算 rate。

计划：保留外部 trial 载入时的 scene/task/profile 匹配、重复 trial 和完整性检查。内部生成计数后直接选择，不再对自产数据重复做一套格式检查。构建 rate/lookup 一次即可；如果传参更复杂，则宁可保留少量简单重算，不创建“已验证对象”层级。

当前生成端没有写入 `success_rate`，因此 `select_starts()` 对该可选字段再核对的兼容分支是删除候选。正式删除前确认当前配置与外部读取入口，不提供旧格式自动迁移。

保留统计分母和覆盖率规则。空样本、没有配对、部分结果属于实际会出现的实验状态，不因追求短代码而删掉。

## 7. 第三批：异常和资源管理只留明确的责任

涉及 [runtime/episode_guard.py](../../src/lastmile_dataflow/runtime/episode_guard.py)、两个执行器、recorder 和 MolmoBot 客户端。

### 7.1 先去掉反射与重复包装，不先删失败隔离

`execute_isolated_episode()` 当前使用 `inspect.signature().bind()` 从任意函数提取上下文，并支持 `_episode_context`；当前生产使用集中在两个 attempt 入口。

计划改为传入明确的 episode 上下文，删除只为通用函数适配服务的反射和备用入口。上下文只包含实际需要的 task/station/root/attempt 等信息，不建立可扩展任务框架。

内部执行器返回自身构造的结果，不再检查“它是否返回 dict”。station 身份由明确的输入来源写一次，不在外层再次补写和核对内部结果。

### 7.2 一类异常只在一个合适的层处理

- 规划无解、非法策略动作、任务超时：执行层给出相应实验结果。
- 初始化、写盘和资源异常：attempt 边界统一处理，不在内外层重复生成 traceback 和归因字典。
- 共享策略服务失联、GPU 故障：保留停批语义，不能吞掉后继续提交同一故障资源。
- `KeyboardInterrupt`：清理后继续抛出，不改成普通任务失败。

已有测试覆盖 RPC 超时、模型错误与 episode 局部错误，不能把这些当作没有依据的边界情况删掉。错误文字匹配只集中保留现有必要集合，不继续堆新情况。

### 7.3 明确谁关闭资源

优先简化显式重复关闭：sim、trace、recorder 各有一个主要关闭责任；异常阶段也必须能关闭已创建的资源。

不先把现有管理全部换成另一套机制。保留或采用标准上下文管理，只有确实减少代码时才替换；不同时新增资源登记、回调栈、状态机三套方案。

可重入的 `close()` 保持简单。不能为了删除 `finally` 让异常时资源泄漏，也不能为了局部对象从未存在的可能性四处加 `getattr`。

## 8. 不列为直接删除项的逻辑

下列代码看起来包含检查或补偿，但目前有实际含义；本轮最多整理表达、移动到合适位置，不默认删除：

- `pick.py` 的接触、抬升、保持、滑移、时间连续性判定，以及必要的非有限值处理。
- `could_have_success()` 的快速必要条件：它减少完整任务判定调用，不等价于重复做无用校验。
- native motor 与 position 的转换、关节投影容差、限位 yaw 处理和躯干反馈规则。
- 场景编辑后按名称迁移状态、保留非机器人位姿与速度、失败回滚。
- `simulation.observe_state()` 与 `io.diagnostic()` 的处理。前者还记录异常字段位置；不能因为都处理非有限值就直接认定重复并合并。
- cuRobo V2 的静态 IK 状态修正、目标 SDF 查询范围、携带物球拟合和必要缓存清理。
- 原生场景转换后的碰撞、支撑和可见性检查；转换后需要重新确认，不等于重复审核原场景。
- GPU/RPC 的实际失败隔离、视频 writer 关闭，以及新 run 禁止覆盖。

cuRobo 内部对象的 `hasattr(data, 'dims')`、`hasattr(data, 'wp_cache')` 等单独列为候选：固定软件版本不代表所有碰撞数据对象同型，需先核对本地安装实现和真实调用对象再决定，不凭属性名直接删。

## 9. 目录方案：只增加一层职责分组

先做主要逻辑精简，再移动文件，避免“逻辑变化”和“大量导入变化”混在一起。下列为目标布局；无必要不继续往下嵌套。

### 9.1 `scenes`

```text
scenes/
├── __init__.py
├── README.md
├── dataset.py             # 场景记录与站位选择
├── dataset_config.py      # 场景配方与站位参数
├── geometry.py            # 共用几何
├── graph.py               # 共用场景观测图
├── loading/
│   ├── source.py
│   ├── mjcf.py
│   ├── assets.py
│   └── asset_library.py
├── tasks/
│   ├── discovery.py       # 原 tasks.py
│   └── eligibility.py
├── stations/
│   ├── sampling.py
│   └── initialization.py
└── augmentation/
    ├── builder.py         # 原 augmentation.py
    ├── config.py          # 原 obstacle_config.py
    ├── checks.py          # 原 obstacle_checks.py
    ├── regions.py         # 原 obstacle_regions.py
    ├── sampling.py        # 原 obstacle_sampling.py
    └── selection.py       # 原 obstacle_selection.py
```

各子目录补必要的 `__init__.py`，不做大批量再导出。`geometry`、`graph` 留作共用模块，避免硬塞进某个业务子目录后被其他组反向依赖。

### 9.2 `runtime`

```text
runtime/
├── __init__.py
├── README.md
├── simulation.py          # 真实 stepping、快照、观测
├── execution_config.py    # 运行参数与 profile
├── control/
│   ├── robot.py
│   ├── action.py
│   ├── action_limits.py
│   ├── native_actions.py
│   └── torso_command.py
├── construction/
│   ├── preparation.py
│   ├── state.py           # 原 build_session.py：状态迁移
│   ├── session.py         # 原 obstacle_edit_session.py
│   └── benchmark_scene.py
├── execution/
│   ├── context.py         # 原 execution.py 中 ContinuousContext、clone_sim 等
│   ├── navigation.py      # 原 execution.py 中 navigate
│   ├── curobo.py          # 原 execution.py 中 run_curobo_attempt
│   ├── manipulation.py    # 原 curobo_execution.py
│   ├── molmobot.py        # 原 molmobot_execution.py
│   ├── checks.py          # 原 execution_checks.py
│   └── lifecycle.py       # 精简后的 episode_guard.py
└── outcomes/
    ├── contacts.py
    ├── health.py
    ├── pick.py
    ├── evaluation.py      # 原 outcomes.py
    └── vla.py             # 原 vla_outcomes.py
```

`OperationFailure` 放入 context，供执行层共用；context 不导入具体 cuRobo/MolmoBot 编排。导航只依赖它实际需要的上下文接口，两个调用方明确提供接口，不再用默认方法补缺。

不把各文件迁移后再从旧路径转发。原 `scenes/augmentation.py`、`scenes/tasks.py`、`runtime/execution.py` 与新包同名，移动时一次性删除旧文件并更新在用导入，避免文件/包同时存在。

目录多几个有职责的文件是可以接受的；为了拆分而新增纯转发文件不接受。

### 9.3 依赖方向

- `cli` 只解析参数和分发；`workflows` 负责实验步骤编排。
- `runtime` 负责实际运动，`planning` 给规划结果，`policies` 给模型输出。
- `scenes` 中的采样、构建可以使用 runtime 的准备能力，但不导入 workflow。
- `data` 负责记录和统计，不承担规划或模拟执行。
- 不趁本轮追求“完美单向依赖”再加一层协议包；只消除本轮移动或拆分造成的循环依赖。

## 10. 分批实施与对应验证

| 批次 | 修改内容 | 首先验证什么 |
|---|---|---|
| A：局部减法 | 第 5 节的属性、占位参数、复制、局部状态及无效包装 | 动作、躯干、执行语义、配置与路径测试 |
| B：记录链路 | phase 前置、删除旧记录接口、清理重复观测与无用状态准备 | 初始状态 + 实际动作对齐、原始/下发动作、PNG/视频开关、部分执行 |
| C：数据与配置 | 计数编号、加载一次、内部标定处理和必要配置约束 | 跨场景编号唯一、标定复用、完整性、困难选择、分母 |
| D：异常与关闭 | 明确上下文、减少反射/重复捕获/重复关闭 | 初始化失败、执行失败、写盘失败、RPC 超时、主动中断 |
| E：目录迁移 | 按第 9 节移动并更新导入 | 五命令帮助、所有模块加载、全套单测 |
| F：真实主链 | 在已有资产和环境上执行有界实验 | 实际运行到的场景构建、规划、策略、导航及评估分支 |

每批完成后直接更新同一份简短实施记录，不为每处删行产生独立审阅表或派生索引。若某候选没有净收益或改变了科研语义，就保留原实现，不继续强行抽象。

### 10.1 使用现有测试，不扩成边界情况工程

重点测试入口：

- 控制与仿真：`test_action_limits.py`、`test_torso_command.py`、`test_execution.py`、`test_execution_evidence.py`、`test_execution_fault_isolation.py`。
- 记录与生命周期：`test_light_dataflow.py`、`test_episode_lifecycle_isolation.py`、`test_policy_batch_stop.py`。
- 场景：`test_local_construction.py`、`test_obstacle_augmentation.py`、`test_obstacle_cases.py`、`test_mjcf_loading.py`、`test_scene_graph.py`。
- 入口与统计：`test_cli.py`、`test_lastmilebench.py`，以及 `test_light_dataflow.py` 中的标定复用与分发测试。
- 策略/规划相关改动：按需运行 `test_molmobot.py`、`test_multitask.py`、`test_curobo_v2_model.py`、`test_head_fov.py`。

先跑改动直接相关的文件，再在最终目录迁移后跑全套。只补真实语义空缺的测试；不要求继续穷举几乎不出现的类型组合。

测试中为已删除包装、反射或内部格式验证而存在的实现细节断言同步删除/改写；核心语义失败不能通过删除断言来掩盖。测试替身应补齐实际接口，不反过来要求源码照顾不完整的 mock。

```bash
# 在 refactor-light 工作树根目录执行；指定文件按批次替换。
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p 'test_action_limits.py' -v

# 目录迁移完成后的完整离线回归。
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -v

# 入口检查不替代真实运行。
PYTHONPATH=src .venv/bin/python -m lastmile_dataflow.cli --help
```

另分别执行五个子命令的 `--help`。这只确认参数入口可加载，不宣称真实后端已经可用。

### 10.2 真实检查保持有界，不制造成功样本

仅在实施完成且资产可用后执行，先查看 CUDA 0–7 的利用率与显存，显式选择空闲卡；不硬编码固定 GPU，不在本轮方案阶段占卡。

1. 用显式的小配方运行 `build-scenes`，检查接受或拒绝结果及场景可加载。
2. 用少量真实站位分别执行 cuRobo 和 MolmoBot `evaluate-stations`，检查实际步数与输出；不为了结果好看调整物理阈值。
3. `collect-lastmile` 分别确认“复用标定”和“当前 run 标定一次”的路径。有完整困难配对才验收自然选出的连续执行；没有配对则如实记录未执行。
4. 从匹配且完整的 native 标定运行 `build-bench`，有 episode 才实际运行 `evaluate-bench`。空 bench 的合理负结果不等于策略评测已经执行。
5. 必要时复用现有 `tests/refactor_step8_real.py` 的有界主链工具，以及显式 cuRobo/策略 smoke；先同步导入，不复制一套新运行器。该主链工具接收已有场景库，不能替代第 1 步的构建检查。

真实检查比较控制节奏、执行路径、产物对齐和结果分类，不要求随机规划/模型输出逐字一致。若缺资产、依赖或可用 GPU，报告实际未验证项，不用 mock 结果代替。

## 11. 交付与停止标准

源码交付应满足：

- 五个命令和主要实验能力仍在；没有引入新后端或重启旧路线。
- 第 5 节确定的局部冗余得到处理；其他候选仅在收益明确时修改。
- 记录器接口与调用方匹配，不再为无用参数准备整份状态；实际轨迹仍只有一套。
- `scenes`、`runtime` 子目录职责清楚，没有旧路径兼容壳和新增万能工具层。
- 更新当前 README、两个目录说明、`docs/architecture.md`、必要运行说明和测试导入。历史报告不因内部路径变化而全量改写。
- 相关回归通过；真实执行、负结果与未验证分支分别报告。

最终只需补一份 `实施结果.md`：概述修改、实际源码变化、测试结果和真实运行限制。不新增审计发布体系，不要求每个函数提交一份删除证明。

停止条件：主要链路已经直接、剩余代码服务明确的算法或执行语义，就停止。不能为了多删几行继续增加抽象或搬动与任务无关的模型代码。

## 12. 本次方案编制的过程与限制

已阅读当前项目约定、README、架构及目录说明，静态检查五命令分发、workflow、记录器、配置、仿真、执行器、标定选择、相关测试和部分 planner 实现；以实际调用和生成端为依据整理上述候选。

本次**只新增本报告**，未实施精简，未跑单测、GPU 仿真或模型推理。静态候选不等于删除后行为已经验证。

开始编制时，工作树已有 `cli.py` 修改及 reports 的删除/移动记录；这些属于既有状态，不覆盖、不恢复、不纳入本次源码改动。后续实施应以届时的最新工作树为准，保留用户修改。
