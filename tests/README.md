# 验证分工

- `test_foundation.py`：配置、20D、原子控制、快照、防重置、格式桥、记录与连续性。
- `test_failures_and_legacy.py`：错误分类、严重穿透、非有限状态诊断、渲染故障现场保留和旧格式转换。
- `helpers.py`：简化协议模型生成器，不代表真实 RBY-1。
- `real_smoke.py`：显式真实模型验证，不随 unittest 自动运行；详见项目 README。
- `fixtures/legacy_case1_episode.json`：从旧已知样例复制的必要输入子集，只用于初态转换回归；
  不含人工审批、旧运行代码、成功标签或密钥，运行不访问 `lastmile_pipeline`。

单元测试可离线且无需 GPU；真实验收依赖外部资产和 GPU 渲染，结果分开报告。

## 阶段二

`test_construction.py` 增加四类规则模板、事务/跨模型回滚、落位/稳定性/邻居保护、过期或非法决策、
资产资格与资源异常、冻结身份、离线恢复、硬期限监督进程和累计预算测试。
所有简化 fixture 只证明程序接口，不是实机 RBY-1 或真实抓取证据。

`phase2_real_smoke.py` 显式使用阶段一已保存的真实 ProcTHOR/RBY-1 编译模型，重新执行
零编辑/移动、静置、冻结独立恢复及三相机短动作/解码。默认唯一 ID，不覆盖已有输出。
见 [阶段二交付报告](../reports/phase2/phase2-delivery.md)。

## 阶段三

`test_stations.py` 验证采样、控制配置边界、固定底盘、严格抓取协议、预算、未知分类、
证据篡改、空轨迹与视觉 Agent 协议。fixture 不替代真实 cuRobo/MuJoCo 结果。
`phase3_planner_smoke.py` 是可选原生 GPU 规划诊断，不是成功标签。

`verify_phase3_delivery.py` 对指定浏览包的全部 attempt 独立审计，并用 ffmpeg 完整解码所有相机和交付视频，
核对帧数、SHA256、实际回放时间与阶段。只读原始数据，检查结果另存：

```bash
PYTHONPATH=src ../molmospaces/.venv/bin/python tests/verify_phase3_delivery.py \
  --collection outputs/collections/case1-phase3-v2 \
  --derived outputs/delivery_views/case1-failure-v2 \
  --output reports/checks/phase3-artifacts.json
```

真实成败、视频与未测范围见[阶段三交付报告](../reports/phase3/phase3-delivery.md)。

## 自动 Case → 场景编辑

离线测试包含契约、图/支撑、相对旋转、采样、事务、资产、四角色协议、head 配对、预算与硬期限。
`test_local_construction.py` 覆盖精确场景身份、局部图、派生资产和实际分割可见性。
`test_case_edit_workflow.py` 使用 Mock Agent，不能证明真实 case 机制或任务成功。

```bash
MUJOCO_GL=egl CASE_EDIT_RENDER_TESTS=1 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python -m unittest discover -s tests -v
# 不调用外部服务：真实房屋、自动选目标和可观察初态
MUJOCO_GL=egl PYTHONPATH=src ../molmospaces/.venv/bin/python tests/case_edit_real_smoke.py \
  --request configs/case_edits/case1-val103.json --prepare-only
# 已获数据发送授权后：真实服务的三类构造烟测，不是移动任务验收
MUJOCO_GL=egl PYTHONPATH=src ../molmospaces/.venv/bin/python tests/case_edit_real_smoke.py \
  --request configs/case_edits/case1-val103.json configs/case_edits/case1-5-val103.json configs/case_edits/case3-val103.json \
  --api-settings configs/agent_api.json --provider xera
```

`case_edit_asset_smoke.py` 继续覆盖资产增删、事务和 RGB。Provider 回归继续覆盖独立密钥、auto 切换计费和显式不切换。
旧三角色集成脚本、旧批量 gate 和旧请求配置已替换。旧输出不覆盖，也不作为当前三类 case 成立证据。

新增 `test_agent_contracts.py` 覆盖同一 Schema 的本地校验、错误路径/上一响应反馈、能力降级计费、截断拒绝及显式模型覆盖。工作流回归覆盖实际多初态放置、提议补图、检查补图，以及格式失败不重新编辑。

同输入模型对照（只检查协议和图像判断，不接受场景、不升级任务结论）：

```bash
MUJOCO_GL=egl PYTHONPATH=src ../molmospaces/.venv/bin/python tests/case_edit_model_compare.py \
  --call outputs/case_construction/<run_id>/agents/<编号>-reviewer.json \
  --api-settings configs/agent_api.json --provider xera --models gpt-5.6-sol glm-5.3
```

该脚本要求新契约的真实 reviewer 输入和现存 RGB；不支持图片的模型会明确报错，不静默丢弃图片。

当前正式配置仅 Xera + Chat Completions + JSON Schema（服务端 strict=false，本地严格检查）。
回归还覆盖禁用 DMX 后 auto 无法访问 DMX、显式 Schema 不降级、非有限响应修复及错误字段路径。

`test_mjcf_loading.py` 对比原生与字符串/VFS 加载后的名称、网格、质量和几何数组，覆盖相对资源、同名 include、循环拒绝和目录覆盖回退；不改原始 XML。

`test_agent_retries.py` 离线覆盖临时 HTTP/超时/连接异常、2/4/8/16秒退避、最多5次总尝试、格式与网络共用上限、图片保持、永久错误和余额不足不重试，以及共享调用/时间预算提前终止。

局部候选回归覆盖拥挤台面不能占满所有槽位：先按支撑面分层、再随机选择各支撑目标；固定种子可复现，完整物理图不变。wire Schema 回归确保布尔支撑条件必须提供 value，非法条件不依赖模型自觉补齐。

## Case 工厂首批实现

- `test_case_factory.py`：五份规格、配对公平性、整边/yaw/路径覆盖、L1/L2 分离、
  case2/3 反事实、case1.5 三侧角色、宽松复核、一次调用、冻结、硬期限/不覆盖。
- `test_factory_foundations.py`：原生抓取缓存门禁、点对/深度采样、局部边、失败起点、
  资产身份、孤立筛选状态隔离、完整四段规划与基础设施异常、XML 符号链接资源根。
- `factory_reach_real_smoke.py`：显式原生 RBY-1/cuRobo IK smoke，不随离线回归执行。

这些 fixture 不代替可达范围表标定、真实资产抓取、金标准校准或 L2/导航任务验收。
新增真实运行结果与局限见 [首批实施报告](../reports/phase2/case-factory-initial-implementation-20261008.md)。
