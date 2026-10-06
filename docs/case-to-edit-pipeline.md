# 从 case 描述到具体编辑操作

本文回答一个问题：**设计文档里那份用自然语言写的 case 描述，是怎么变成一次具体的场景编辑操作的？**

结论提前说：

> **目前没有"把自然语言 case 描述自动翻译成编辑"的能力。**
> case 描述先被**人工翻译成一份 JSON 配置**，程序只负责把这份 JSON 里的**参数**变成**候选操作**，
> 再由规则挑一个执行。整条链路里真正的"描述 → 操作"转换点**只有一个函数**：
> [`construction/candidates.py:10` 的 `candidates()`](../src/lastmile_dataflow/construction/candidates.py)。

阅读时请始终记住三条边界：

1. **描述 → 配置是人工的**，程序不读设计文档，没有 case 描述解析器。
2. **程序只做几何计算与阈值比较**，不做"这算不算困难"的语义判断。
3. **生成不是验收**。候选生成与硬门槛验收是两套独立逻辑，共用同一份参数数值。

---

## 1. 完整链路图

```
  ①【人写】设计文档里的自然语言
     "距离困难：目标在合理支撑面上，初态到目标的距离构成'够不到'的困难"
                        │
                        │  ← ★ 人工翻译，代码没有任何辅助
                        ▼
  ②【人写】configs/builds/phase3-case1-cup.json
     { "case_type": "case1",
       "target":    "cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2",
       "support":   "diningtable_f113cf7f8367e89f709b53cbee1a1c05_1_0_2",
       "editable":  ["cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2"],
       "parameters": { "distance_range_m": [1.0, 1.5] } }
                        │                                    ↑
                        │              "距离困难"整段语义 → 只剩这一行
                        │  ← construct() 严格校验，不通过就拒绝
                        ▼
  ③【程序】BuildConfig 对象（frozen dataclass，不可变）
                        │
                        │  ← ★★★ 唯一的"描述 → 操作"转换点
                        ▼
  ④【程序】candidates(session)              construction/candidates.py:10
     │
     ├─ 枚举 (xy, yaw) 组合                          candidates.py:15-23
     │     xy  = 当前落点 + 区域中心 + 5×5 内点网格   → 27 个点
     │     yaw = 当前朝向 + 0/π2/π/-π2               → 5 个朝向
     │     合计 27 × 5 = 135 个组合
     │
     ├─ placement_pose() 把 (xy,yaw) 算成 7 维位姿    scenes/geometry.py:133
     │     ├ 计算旋转后的实际足迹
     │     ├ 足迹须留 1cm 边距在支撑区域内，否则 return None
     │     ├ 5×5 向下射线须全部命中支撑面，否则 return None
     │     └ 返回 [x, y, z, qw, qx, qy, qz]        ← 这就是"具体位姿"
     │
     ├─ 给位姿打分 ← case 语义真正进入的地方           candidates.py:52-71
     ├─ 去重（sub-mm 数值漂移合并）                    candidates.py:47-50
     └─ 按 cost 升序排序                               candidates.py:73
                        │
                        ▼
  ⑤【产物】operations = [
        { "op": "move",
          "instance": "cup_ff2c6ab62ccb690220c97cdcfeb658c5_1_0_2",
          "pose": [x, y, z, qw, qx, qy, qz],
          "region_id": "diningtable_...:geom_name:top" } ]
                        │
                        │  ← 规则取 cs[0]（cost 最小）/ Agent 走协议选一个
                        ▼
  ⑥【程序】session.transact(operations)     runtime/build_session.py:343
     ├─ _precheck()  权限 / 保护 / 支撑依赖审查       build_session.py:245
     ├─ _pose()      自由物体写 qpos；固定家具改 spec 并重编译   build_session.py:322
     ├─ settle()     真实 mj_step 静置，绝不逐帧写位姿  build_session.py:200
     └─ validate()   支撑 / 稳定性 / 邻居 / 机器人检查  validation/placement.py:31
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
         committed            rolled_back
      （进 transactions/）  （恢复检查点，同样留证）
```

---

## 2. 逐段拆解

### 2.1 第 ①②段：设计文档 → JSON，纯人工

这是最关键的一点。

设计文档 [`reports/phase2/phase2-case-construction-design.md`](../reports/phase2/phase2-case-construction-design.md)
对"距离困难"用了一整段文字描述构建意图：

> **构建意图**：目标位于合理支撑面上，机器人初态合法，且该初态到目标的距离在语义上构成"够不到"的困难，
> 而其他可站位置仍能成功。

但落到配置 [`configs/builds/phase3-case1-cup.json`](../configs/builds/phase3-case1-cup.json) 里只有：

```json
"parameters": { "distance_range_m": [1.0, 1.5] }
```

**"构成够不到"这个语义判断，在这一步被压缩成了一个数值区间。**

而且这一步是**纯人工**的：

- 程序不解析设计文档；
- 没有"case 描述语言"或 DSL；
- 没有从意图到参数的推导辅助；
- 设计文档本身也在开头声明"本次只给设计方案，不改代码、不跑仿真"。

设计文档 1.2 节自己承认了这个问题：

> 当前的问题不是规则太多，而是"语义取舍"被写成了规则阈值。

所以第一个问题的答案是：**描述到配置的翻译，完全依赖人工理解。**

### 2.2 第 ③段：JSON → BuildConfig，只做校验不做理解

[`construction/config.py:79`](../src/lastmile_dataflow/construction/config.py) 的 `__post_init__`
做的事是**拒绝非法配置**，不是**理解 case 意图**：

| 检查项 | 位置（均为 `construction/config.py`） | 作用 |
|---|---|---|
| `schema_version` 必须是 `"2.0"` | `:83` | 版本把关 |
| `case_type` 必须是四类之一 | `:84` | 防止未实现的 case 混进来 |
| 每类 case 的**必需参数**必须在 | `:118-121` | 防止参数缺失 |
| `editable` 与 `protected` 不能重叠 | `:92` | 防止"既要改又要保护"的矛盾 |
| `editable` 不能含 `robot_0/` | `:96` | 机器人本体永远不可编辑 |
| `allowed_operations` 只能是 move/rotate/add/delete | `:94` | 关节编辑、缩放明确不支持 |
| case2 的 `handle` 必须有 `source` 且 `verified=true` | `:139` | **把手标注必须可信来源**，不允许猜 |

必需参数的完整定义（`config.py:118-121`）：

```python
required = {'case1':   {'distance_range_m'},
            'case2':   {'handle', 'desired_direction_world'},
            'case3':   {'obstacle', 'approach_offset_m', 'obstacle_distance_range_m'},
            'case1.5': {'frame_body', 'side_a', 'side_b',
                        'min_distance_difference_m', 'clearance_radius_m', 'min_clearance_difference_m'}}
```

**它保证"配置完整且合法"，但不保证"配置符合设计意图"。**
一个 `distance_range_m: [0.0, 999.0]` 的配置完全合法，但它显然不构成"距离困难"。

### 2.3 第 ④段：`candidates()` —— 唯一的转换点

这个 73 行的函数做了三件事。

#### (a) 生成几何上可行的位姿

```python
# candidates.py:16-17
points = [当前落点, 区域中心] + 5×5 内点网格        # 27 个落点
# candidates.py:21
yaws = config.parameters.get('yaw_candidates_rad',
                             [current_yaw, 0, π/2, π, -π/2])   # 5 个朝向
# candidates.py:22-24
for xy in points:
    for yaw in yaws:
        pose = placement_pose(sim, target, region, xy, yaw)
        if pose is None: continue                   # 几何不可行直接丢弃
```

[`placement_pose()`](../src/lastmile_dataflow/scenes/geometry.py:133) 是"2D 落点 + 朝向 → 3D 位姿"的实际计算：

```python
# geometry.py:141
center[2] = region.height - rotated[:,2].min() + gap   # 让物体最低点刚好贴住支撑面
# geometry.py:143-145
footprint = region.local(projected)                     # 算旋转后的实际足迹
if not (足迹留 1cm 边距在区域内): return None
# geometry.py:147
if not support_rays(sim, body, region, projected): return None   # 5×5 向下射线全命中
# geometry.py:148
return np.r_[center, quat_yaw(yaw)].tolist()            # 7 维位姿
```

> **注意**：这里的"可行性"是**几何代理**（足迹 + 射线），不是物理结论。
> 真正的物理结论要等第 ⑥ 段静置完才给。文档里也强调过：
> mesh 上的 5×5 射线是保守采样，**不是任意洞口的数学证明**。

#### (b) 用 case 参数打分 ← case 语义真正进入的地方

```python
# candidates.py:52-71（精简）
penalty = 0.

if 'distance_range_m' in parameters:                  # case1 距离困难
    distance = 目标到机器人底盘的水平距离
    penalty += max(lo - distance, 0, distance - hi) * 100

if 'height_range_m' in parameters:                    # 通用可选
    penalty += max(lo - pose[2], 0, pose[2] - hi) * 100

if config.case_type == 'case2':                       # 把手朝向
    angle = 把手局部轴旋转后的世界方向 vs 期望方向 的夹角
    penalty += max(0, angle - direction_tolerance_rad) * 100

if config.case_type == 'case1.5':                     # 侧向差异
    penalty += max(0, min_distance_difference_m - 两侧距离差) * 100

cost = penalty + 目标移动距离 + 0.02 * |yaw|           # 最小改动偏好
```

`cost` 的组成有两部分，含义不同：

| 部分 | 含义 |
|---|---|
| `penalty` | **case 语义的符合程度**（超出区间越多罚越多） |
| `目标移动距离 + 0.02·\|yaw\|` | **最小改动偏好**（不惜得动就别动） |

#### (c) 去重 + 排序

```python
# candidates.py:47-50  去重
identity = [{**o, 'pose': [round(x,3) for x in o['pose'][:3]] + [round(x,5) for x in o['pose'][3:]]} for o in operations]
key = digest(identity)
if key in seen: continue
seen.add(key)

# candidates.py:72-73  产出与排序
results.append({'candidate_id': key, 'revision': session.revision,
                'operations': operations, 'cost': cost})
return sorted(results, key=lambda x: (x['cost'], x['candidate_id']))
```

`candidate_id` 是 operations 规范化后的 SHA-256，所以同一个候选重复出现会被 `seen` 去重。
注意舍入是**只用于去重键**，不改变实际执行的位姿精度（`candidates.py:46` 有注释说明）。

### 2.4 第 ⑤段：选中

[`workflows/build.py:109`](../src/lastmile_dataflow/workflows/build.py)：

```python
choice = cs[0]                          # 规则路径：永远取 cost 最小的
```

如果传了 `backend`（外部视觉模型），才走 [`DecisionGateway.decide()`](../src/lastmile_dataflow/agents/protocol.py:73)，让模型从候选里挑一个、请求补图或放弃。

**但 CLI 路径下 `supervised_build` 明确拒绝 backend**：

```python
# workflows/build.py:252
raise ValueError('in-process vision callbacks require run_build; supervised CLI is rule-only')
```

所以**命令行永远是规则路径**。Agent 路径只在 Python 进程内调用 `run_build(backend=...)` 时可用。

### 2.5 第 ⑥段：事务执行

[`transact()`](../src/lastmile_dataflow/runtime/build_session.py:343) 的固定六步：

| 步 | 做什么 | 位置 |
|---|---|---|
| 1 | 预算检查 + 证据可序列化检查（NaN 请求也要留失败证据） | `build_session.py:358-368` |
| 2 | `snapshot()` 存检查点 | `build_session.py:369` |
| 3 | `_precheck()` 权限 / 保护 / 支撑依赖审查 | `build_session.py:245`（调用在 `:374`） |
| 4 | `_pose()` 执行编辑 | `build_session.py:322` |
| 5 | `settle()` 真实静置 → `validate()` 给结论 | `build_session.py:200` / `234` |
| 6 | 通过 → `committed`（`:410`）；不通过 → `restore(检查点)`（`:415`） | `build_session.py:410-416` |

**关键点：`candidates()` 生成的候选也必须过 `_precheck()`。**
这是"Agent 只能返回建议、不能取得仿真编辑权限"的实现位置——无论候选来自规则还是模型，
都必须通过同一道门：

```python
# build_session.py:260-263
if op not in self.config.allowed_operations or name not in self.config.editable \
   or name in self.config.protected or name.startswith('robot_0/'):
    raise PermissionError('edit not allowed/protected')
if op == 'delete' and name in (self.config.target, self.config.parameters.get('obstacle')):
    raise PermissionError('required target/obstacle cannot be deleted')
```

最后一条尤其重要：**case3 的障碍永远不能被删除**。
设计文档 2.4 节把它列为"必须防住的一种失败"，这里就是那道防线。

---

## 3. 核心机制：一份参数，两种用途

这是理解整个设计的关键。同一个 `parameters` 里的数值被用了**两次**，而且是**镜像的两次**：

```
                    parameters = {"distance_range_m": [1.0, 1.5]}
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
   【用途一：生成与排序】                        【用途二：验收判定】
   construction/candidates.py:54-56             construction/templates.py:18-20

   penalty += max(lo-d, 0, d-hi) * 100          add('target_robot_horizontal_distance',
                                                    range_check(d, [lo,hi]),
   超出区间 → 加 100 × 超出量                     {'distance_m': d, 'range_m': [lo,hi]})
   → 排序靠后，但**不排除**                      超出区间 → status = 'fail'
                                                 → check['valid'] = False
                                                 → 事务回滚
```

**`* 100` 是软引导**（让不合意的候选排在后面，但不禁止）；**`range_check` 是硬门槛**（过不了就回滚）。
两者用同一个区间参数，但作用完全不同。

这个设计产生一个有意思的性质：**`penalty` 的系数大小几乎不重要**，
因为它只影响候选之间的相对顺序；真正决定"过不过"的是 `templates.py` 里的 `range_check`。
`* 100` 只是确保"严重超区间"的候选一定排在"轻微超区间"的后面。

### 三段式闭环

```
candidates()              只管"生成并排序"           → 不判断对错
validate() / templates    只管"对错"                 → 不生成
workflows/build.py:92     把两者接成闭环
```

```python
# workflows/build.py:92-119（精简）
while not check['valid']:
    cs = [c for c in candidates(session) if c['candidate_id'] not in failed]
    cs = cs[:max(0, config.budget.candidates - budget.used['candidates'])]
    if not cs: raise RuntimeError('no_new_qualified_candidates')
    ...
    choice = cs[0]
    entry = session.transact(choice['operations'], revision=choice['revision'], ...)
    if entry['status'] != 'committed':
        failed.add(choice['candidate_id'])          # 记黑名单，不再重复试
        gateway.failed.add(choice['candidate_id'])
        budget.consume('repairs')
        session.settle()                            # 回滚清空了窗口，重新静置
    check = session.validate()
```

所以"编辑"是**在闭环里试出来的**，不是在生成时就确定正确的。

---

## 4. 四类 case 的差异精确落在哪里

只有三处，全在 `construction/` 目录下：

| 位置 | 代码行 | 做什么 |
|---|---|---|
| **① 必需参数声明** | [`construction/config.py:118-121`](../src/lastmile_dataflow/construction/config.py) | 校验每类 case 该有哪些参数 |
| **② 候选打分** | [`construction/candidates.py:52-71`](../src/lastmile_dataflow/construction/candidates.py) | 把 case 语义变成 penalty |
| **③ 验收判据** | [`construction/templates.py:10-63`](../src/lastmile_dataflow/construction/templates.py) | 把 case 语义变成 pass/fail |

### 4.1 打分侧（`candidates.py`）

| case | 参数 | 打分逻辑 | 特殊操作 |
|---|---|---|---|
| **case1** 距离困难 | `distance_range_m` | 距离超出区间就加罚 | 无 |
| **case2** 把手朝向 | `handle`、`desired_direction_world`、`direction_tolerance_rad` | 把手世界方向与期望方向夹角超容差就加罚 | 无 |
| **case3** 抓取路径障碍 | `obstacle`、`obstacle_asset`、`approach_offset_m`、`obstacle_distance_range_m` | 目标**不动**，只给障碍加罚 | **`candidates.py:30-45`**：允许目标保持原位（`no_target_edit`），改为生成 `add`/`move` 障碍的操作 |
| **case1.5** 侧向差异 | `frame_body`、`side_a`、`side_b`、`min_distance_difference_m` 等 | 两侧距离差不足就加罚 | 无 |

| case1 的打分代码（`candidates.py:54-56`）：

```python
if 'distance_range_m' in parameters:
    distance = np.linalg.norm(np.array(pose[:2]) - sim.robot.group('base')[:2])
    lo, hi = parameters['distance_range_m']
    penalty += max(lo - distance, 0, distance - hi) * 100
```

### 4.2 验收侧（`templates.py`）

```python
# templates.py:18-20   case1（也适用于所有带 distance_range_m 的 case）
if 'distance_range_m' in p:
    distance = float(np.linalg.norm(d.xpos[target,:2] - sim.robot.group('base')[:2]))
    add('target_robot_horizontal_distance', range_check(distance, p['distance_range_m']),
        {'distance_m': distance, 'range_m': p['distance_range_m']})
```

其中 `add()` 的实现（`templates.py:15-16`）决定了它是硬门槛：

```python
def add(name, passed, evidence):
    checks.append({'requirement': name, 'required': True,
                   'status': 'pass' if passed else 'fail', 'evidence': evidence})
```

`required: True` + `status: 'fail'` 会让 `inspect_build()` 最终返回 `valid: False`，
事务随即回滚。其余三类同理：

```python
# templates.py:24-31   case2
if config.case_type == 'case2':
    handle = p['handle']; body = m.body(handle['body']).id
    if body not in descendants(m, target):
        raise ValueError('handle annotation is not a target part')   # ★ 校验指认属于目标
    actual = d.xmat[body].reshape(3,3) @ np.asarray(handle['axis_local'])
    angle = 夹角(actual, p['desired_direction_world'])
    add('verified_handle_world_direction', angle <= p.get('direction_tolerance_rad', .15), {...})

# templates.py:32-43   case3
elif config.case_type == 'case3':
    delta = d.xpos[obstacle] - d.xpos[target]
    distance = float(np.linalg.norm(delta[:2]))
    add('obstacle_approach_corridor',
        range_check(distance, tolerance) and np.linalg.norm(delta[:2] - expected[:2]) <= .08, {...})

# templates.py:44-63   case1.5
elif config.case_type == 'case1.5':
    add('frame_bound_side_distance_difference', distances[1]-distances[0] >= p['min_distance_difference_m'], {...})
    add('frame_bound_side_clearance_difference', clearances[0]-clearances[1] >= p['min_clearance_difference_m'], {...})
```

### 4.3 一个观察

**`case2` 的 `desired_direction_world` 同时在两处出现**：

- `candidates.py:63` —— 用于打分（软引导）
- `templates.py:29` —— 用于验收（硬门槛）

其他三类同理。**四类 case 的"语义"都只是 `parameters` 里几个数值的两种用法。**
这就是"语义取舍被写成了规则阈值"的具体形态。

---

## 5. 补充：其实有两条进入路径

除了自动生成，配置里还有一个 `initial_operations` 字段，是**人工冻结好的、完整的操作列表**：

```json
// configs/builds/phase2-cached-train0-move.json（节选）
"initial_operations": [{
  "instance": "saltshaker_ae8db92b68885e8a5331b49cf1534319_1_0_2",
  "op": "move",
  "pose": [0.33344721416176004, 3.686084941802332, 0.9868304345888047,
           0.5000543249055135, 0.4999490735876039, 0.5000291139133423, 0.49996748014368453]
}]
```

[`workflows/build.py:81-87`](../src/lastmile_dataflow/workflows/build.py)：

```python
if config.initial_operations:
    budget.consume('edits', len(config.initial_operations))
    entry = session.transact(config.initial_operations, revision=session.revision, reason='configured_group')
    if entry['status'] != 'committed': raise ValueError('initial_operations_failed')
else:
    session.settle()
```

所以有两条路径，**最终都汇入同一个 `transact()`**：

| 路径 | 来源 | 用途 | 例子 |
|---|---|---|---|
| **A：自动生成** | `parameters` → `candidates()` | 让程序去搜索一个满足 case 的布局 | `configs/builds/phase3-case1-cup.json` |
| **B：人工冻结** | `initial_operations` 里手写好的 pose | 复现一个已验证过的布局，跳过搜索 | `configs/builds/phase2-cached-*.json` |

**注意 B 路径和 A 路径共用同一个闭环**：如果人工冻结的操作**没能**让 `validate()` 通过，
`while` 循环照样会启动 `candidates()` 去补救。

所以 `initial_operations` 更像是"给程序一个起点"，而不是"绕过程序"。
`configs/builds/phase2-cached-train0-zero.json` 就是 `"initial_operations": []`，
即显式的零编辑入口。

---

## 6. 设计文档里说了但尚未实现的部分

设计文档第三节画的流水线和实际代码有差距。逐条核对：

| 设计文档的要求 | 实际状态 | 证据 |
|---|---|---|
| 检索 → 加载候选 → 提取几何 | ✅ 有 | [`catalog/index.py`](../src/lastmile_dataflow/catalog/index.py)、[`scenes/geometry.py`](../src/lastmile_dataflow/scenes/geometry.py) |
| **零编辑分支显式化** | ⚠️ **部分** | 代码逻辑上确实零编辑优先（`validate()` 通过就跳过 `while`），但 `task_candidate.json` 里**没有显式的"是否编辑过"字段**，只能从 `transactions/` 目录数量反推 |
| 程序生成有限候选 | ✅ 有 | `candidates()` |
| Agent 在候选内取舍 | ⚠️ **有协议但 CLI 默认关闭** | `DecisionGateway` 存在，但 `build.py:252` 在 CLI 路径禁用 backend |
| 权限与前置检查 | ✅ 有 | `_precheck()`，`build_session.py:245` |
| 真实静置 | ✅ 有 | `settle()`，`build_session.py:200` |
| 失败 → 回滚 | ✅ 有 | `transact()` 的 except 分支，`build_session.py:412-416` |
| 失败 → **有限修复**（"修复方向由 Agent 从可行集里选"） | ❌ **未实现** | 实际是"换下一个候选重试"。`budget.consume('repairs')` 计的是**候选失败次数**，不是修复动作 |
| 最终复查全部硬要求 | ✅ 有 | `freeze()` 前的 `validate()`，`build_session.py:439` |
| 冻结 → 独立恢复逐字节验证 | ✅ 有 | `build.py:96-100` |
| **构建要求逐项标注强度**（几何测量 / 物理证据 / 模型语义） | ❌ **未实现** | `requirements` 条目只有 `{requirement, required, status, evidence}`（`templates.py:16`），没有 strength 字段 |
| 把"这个 case 想验证什么"写进产物 | ✅ 有 | [`pending_hypotheses()`](../src/lastmile_dataflow/construction/templates.py:67) —— 设计意图唯一进入产物的地方 |

最后一行值得展开。`pending_hypotheses()` 是设计意图唯一真正落到产物里的地方：

```python
# templates.py:67-70
def pending_hypotheses(case):
    return {'case1':   ['current_station_failure', 'alternative_station_success'],
            'case2':   ['real_handle_grasp_success'],
            'case3':   ['real_planning_path_obstructed', 'possible_bypass'],
            'case1.5': ['real_side_navigation_and_manipulation_difference']}[case]
```

它会写进 `task_candidate.json` 的 `pending_hypotheses` 字段，等于告诉阶段三：
"这个场景是想验证这些假设，请你去证伪它们"。

---

## 7. 四类 case 的实际落地情况

从仓库现状看：

| case | 有配置文件 | 有测试 | 有真实运行 |
|---|---|---|---|
| **case1** 距离困难 | ✅ `phase3-case1-cup.json` 等 5 个 | ✅ | ✅ 阶段三已跑（case1 cup） |
| **case2** 把手朝向 | ❌ | ✅ `test_construction.py:114` 用 synthetic fixture | ❌ |
| **case3** 抓取路径障碍 | ❌ | ✅ `test_construction.py:193` 用 synthetic fixture | ❌ |
| **case1.5** 侧向差异 | ❌ | ✅ `test_construction.py:196` 用 synthetic fixture | ❌ |

也就是说，**case2/3/1.5 的代码路径存在且有测试，但没有真实配置文件，也没有真实运行**。
文档里也明确写着"case2/3/1.5 的真实任务覆盖仍未知"。

一个佐证：`grep -rl "case2\|case3\|case1.5" configs/` 在 `configs/` 下**没有任何匹配**。

---

## 8. 一句话概括

> **设计意图 → 人工翻译成几个数值区间 → 程序用这些数值给几何候选项打分排序
> → 挑最优的一个执行 → 用同样的数值做硬门槛验收 → 不过就换下一个候选。**

如果你想新增一个 case，**不需要改流程，只需要改三处**：

1. `construction/config.py:118` 加必需参数
2. `construction/candidates.py:52` 加 penalty
3. `construction/templates.py:10` 加验收判据

但**"什么是困难"这个语义判断，目前完全靠人写进参数区间里**——
这正是设计文档 1.2 节指出的核心问题，也是未来接入 Agent 想解决的那件事。

---

## 附：本文引用位置速查

| 概念 | 文件:行 |
|---|---|
| 唯一的转换点 | [`construction/candidates.py:10`](../src/lastmile_dataflow/construction/candidates.py) |
| 落点/朝向枚举 | [`candidates.py:15-23`](../src/lastmile_dataflow/construction/candidates.py) |
| case 语义打分 | [`candidates.py:52-71`](../src/lastmile_dataflow/construction/candidates.py) |
| 2D → 7 维位姿 | [`scenes/geometry.py:133`](../src/lastmile_dataflow/scenes/geometry.py) |
| 必需参数校验 | [`construction/config.py:118-121`](../src/lastmile_dataflow/construction/config.py) |
| 验收判据 | [`construction/templates.py:10-63`](../src/lastmile_dataflow/construction/templates.py) |
| 待验证假设 | [`construction/templates.py:67`](../src/lastmile_dataflow/construction/templates.py) |
| 主循环 | [`workflows/build.py:92-119`](../src/lastmile_dataflow/workflows/build.py) |
| 规则选候选 | [`workflows/build.py:109`](../src/lastmile_dataflow/workflows/build.py) |
| 编辑事务 | [`runtime/build_session.py:343`](../src/lastmile_dataflow/runtime/build_session.py) |
| 权限前置检查 | [`runtime/build_session.py:245`](../src/lastmile_dataflow/runtime/build_session.py) |
| 真实静置 | [`runtime/build_session.py:200`](../src/lastmile_dataflow/runtime/build_session.py) |
| 落位/稳定性验收 | [`validation/placement.py:31`](../src/lastmile_dataflow/validation/placement.py) |
