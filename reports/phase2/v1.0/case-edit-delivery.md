# Case → 场景编辑实施与验证报告

> 历史报告：以下 S0–S7 指旧版距离/朝向编辑验收，不覆盖当前 case1/case1.5/case3 的移动操作目标。当前进度见[构造进度报告](case-edit-progress.md)；历史输出保留，不作为新版本成功证据。

日期：2026-10-06。**S0–S7 已实现并完成对应验证；完整真实批量 gate 通过。**
当前任务细目见[阶段计划](../../docs/case-to-edit-pipeline-plan.md)，运行接口见[自动编辑说明](../../docs/case-edit-pipeline.md)。

## 执行过程与交付

按 S0→S7 实现严格契约、无人工目标场景准备、实测图/谓词、按序联合采样、独立事务、Agent1/2、固定成对六图/Agent3、add/remove/carry_supported、批量 CLI 与共享预算。
新入口不复用旧 build 对象白名单，不做最小编辑；每个请求重新规范化，前后端点检查 invariant，SI/相对旋转。
用户已有中文方案、API 配置和 AGENTS.md 未覆盖；源场景/资产只读。

## 已实际完成的验证

| 范围 | 结果 | 证据 |
|---|---|---|
| 代码回归 | 124 项通过，含实际 EGL 渲染与旧入口回归 | `unittest discover`，详见下方命令 |
| 真实 Agent1/2 + 合成场景图 | 规范化成功、2 个合法提议 | `outputs/case_edits/s4-agent-smoke-1791292853815806259/` |
| 真实三个 Agent + 合成场景真实 RGB | 1 次尝试、1 个接受样本、6 图与冻结场景 | `outputs/case_edits/case-edit-0a7f6bf34ee7/` |
| 两个真实 ProcTHOR/RBY 基线 | train_0 / train_2 均无人工目标准备成功 | [首次准备](checks/case-edit-s1-real-1791291604.json)、[train_0 延时重试](checks/case-edit-s1-retry-1791292499.json) |
| 真实 THOR 闹钟 + 合成场景 | 添加/删除/回滚、6 图、源文件不变；**无 Agent3 接受** | `outputs/case_edits/real-asset-smoke-1791292548784905858/` |
| train_0 方向编辑，真实模型/场景 | 5 次尝试，3 个不同通过样本；18 图；独立恢复通过 | `outputs/case_edits/case-edit-e2e-1791293533-train0-direction/` |

距离组合：train_0 / train_2 均各 3 次尝试、3 个通过样本（共 36 图），见 [真实距离重跑](checks/case-edit-geometry-1791295215.json)。
train_0 方向样本的独立恢复见 [恢复检查](checks/case-edit-train0-direction-restore.json)。

真实 Agent 请求使用用户明确指定的服务；原 DMX 频道后续返回 `pre_consume_token_quota_failed`（额度不足），证据见[服务诊断](checks/case-edit-service-probe.json)。已加入用户指定的频道二，独立密钥、可指定 provider，auto 每次先第一个、失败再第二个；每次切换计入共享预算。未在报告或运行产物中保存密钥。最新文件由用户指定 `provider:xera`；代码支持 CLI 覆盖为 auto/dmx/xera，并保留用户当前选择。
合成 fixture、mock、规则 pending_review、基线 RGB 均不冒充真实场景最终接受。
机器人 `case_condition` / `task_completion` 全部保持 unknown。

## 暴露的问题与修正

- train_0 默认 3 s 静置不足：显式延长上限到 10 s，真实 7.408 s 稳定；稳定/穿透阈值未放宽。
- 更改相机 near clip 曾误改变视场：按比例更新整个 frustum，断言实际 FOV；保留失败图，不用替代图。
- 模型把“全空间覆盖”放进单样本视觉检查：禁止将生成流程要求混入用户场景语义。
- 编辑前物体藏在冰箱、补拍仍未知：有限成对换角度后更换绑定，不反复消耗同一隐蔽物体的参数预算。
- 模型擅加绝对距离范围 / 误读区域坐标：相对距离交给前后比较，不凭空硬限；提供程序测得的对象距离和区域角点提示。
- 两个相对角度用相同分位层会相关：采用按维度独立排列的 Latin 分层，避免同步旋转只探索对角线，保留种子重放。
- 无候选、碰撞不断消耗第一轮：连续拒绝有界换策略，反馈包含实际测量和视觉原因，共享预算不清零。
- 单次服务读取上限 60 s 曾超时：提高至 120 s 或剩余共享预算（取较短），仍有硬墙钟截止；设施错误不伪装编辑失败。
- 已静置缓存再次演进也可能失稳：真实检查拒绝 train_0 的部分缓存重跑，未当作必然合法基线。
- 子进程崩溃时留下 partial：监督入口将其归为 infrastructure_error，不当成已结束结果。

早期失败运行和预算耗尽样本保留在 `outputs/case_edits/`；未把它们改写为成功。
完整 S7 门槛已达到，见[机器验证报告](checks/case-edit-acceptance.json)：

| 真实房屋 | 现象 | 尝试 / 接受 | 六图 / 独立恢复 | 完成运行 |
|---|---|---|---|---|
| train_0 | 距离增加 | 3 / 3 | 18 图 / 通过 | `case-edit-geometry-1791295215-train0-distance` |
| train_0 | 可见朝向差异 | 5 / 3 | 18 图 / 通过 | `case-edit-e2e-1791293533-train0-direction` |
| train_2 | 距离增加 | 3 / 3 | 18 图 / 通过 | `case-edit-geometry-1791295215-train2-distance` |
| train_2 | 可见朝向差异 | 9 / 3 | 18 图 / 通过 | `case-edit-dual-channel-1791297540-train2-direction` |

| 场景 / 类型 | 通过样本编号 | RGB 子目录 | 示例图 |
|---|---|---|---|
| train_0 / direction | `000000、000002、000004` | `rgb_retry_1/` | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/before_top.png) · [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/after_top.png) |
| train_0 / distance | `000000、000001、000002` | `rgb/` | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_top.png) · [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_top.png) |
| train_2 / direction | `000002、000003、000008` | `rgb/` | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/before_top.png) · [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/after_top.png) |
| train_2 / distance | `000000、000001、000002` | `rgb/` | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/before_top.png) · [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/after_top.png) |

共 12 个不同接受样本、72 张接受样本 RGB，全部独立恢复与实际位姿核对通过。
缺角色、百万米无候选、零调用预算的预期失败输入均如期未接受样本：缺角色/百万米描述返回无提议，零预算在调用前结束，见[失败验收](checks/case-edit-failures-1791295900.json)。
三个早期组合在一次正常有界运行中达到目标；最后一个组合由频道二完成的一次独立运行达到目标，**没有拼接多个 partial 的计数**。

## Provider 验证

- 单元测试验证：独立密钥不串用，auto 每个逻辑调用重回第一优先级，显式指定不切换，CLI 覆盖、错误配置，以及调用预算不足时不发起隐藏的第二次请求。
- 真实 train_2 方向运行记录第一个服务失败、第二个服务三角色响应并完成 3 个接受样本。
- 最新 `providers` 格式的真实 S4 调用按用户指定 xera 成功：`outputs/case_edits/s4-agent-smoke-1791296857436080679/`，2 次调用、2 个提议；这项使用合成场景图，不代替真实矩阵。

## 可复现检查

```bash
MUJOCO_GL=egl CASE_EDIT_RENDER_TESTS=1 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python -m unittest discover -s tests -v
# 获得服务数据发送授权后：
MUJOCO_GL=egl PYTHONPATH=src ../molmospaces/.venv/bin/python tests/case_edit_real_smoke.py \
  --api-settings configs/agent_api.json --settle-config configs/case_edits/settling.json \
  --snapshot-root outputs/attempts --timeout 600
```

EGL 真正生成了 RGB，但 NVIDIA 驱动不可用，不声称 NVIDIA GPU/机器人规划验收。
关节化/嵌套根移动与复杂支撑仍不支持；启发式范围和几何代理不证明真实任务困难。

## 12 个通过样本的完整输出链路

以下链接相对于本报告，直接打开本地实际产物。每个样本均从同一运行的冻结基线独立编辑；`sample.json` 同时保存 Symbolic DSL、采样后的 executable、规则检查及最终状态。Agent 日志保存结构化请求与响应，不包含模型内部思考。

最终验收图片按 `sample.json.view_pair_id` 与 `pair.json` 匹配；不把初次拍摄或失败尝试混入交付。

### train_0 / direction

**共同输入与基线：** [请求](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/request.json) → [运行配置](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/configuration.json) → [Agent1 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/template.json) → [稳定化记录](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/graph.json) → [编辑前场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/baseline/scene)

**运行汇总：** [所有 Agent 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents) · [最终通过清单](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/result.json) · [进度](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/progress.json)

#### sample_000000

[Agent2 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0003-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/review_1.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000000/rgb_retry_1/after_oblique_b.png) |

#### sample_000002

[Agent2 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0005-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/review_1.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000002/rgb_retry_1/after_oblique_b.png) |

#### sample_000004

[Agent2 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/agents/0007-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/review_1.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-e2e-1791293533-train0-direction/samples/sample_000004/rgb_retry_1/after_oblique_b.png) |

### train_0 / distance

**共同输入与基线：** [请求](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/request.json) → [运行配置](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/configuration.json) → [Agent1 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/template.json) → [稳定化记录](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/graph.json) → [编辑前场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/baseline/scene)

**运行汇总：** [所有 Agent 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents) · [最终通过清单](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/result.json) · [进度](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/progress.json)

#### sample_000000

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0002-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000000/rgb/after_oblique_b.png) |

#### sample_000001

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0003-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000001/rgb/after_oblique_b.png) |

#### sample_000002

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0001-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/agents/0004-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train0-distance/samples/sample_000002/rgb/after_oblique_b.png) |

### train_2 / direction

**共同输入与基线：** [请求](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/request.json) → [运行配置](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/configuration.json) → [Agent1 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/template.json) → [稳定化记录](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/baseline/graph.json) → [编辑前场景](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/baseline/scene)

**运行汇总：** [所有 Agent 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents) · [最终通过清单](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/result.json) · [进度](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/progress.json)

#### sample_000002

[Agent2 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0003-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000002/rgb/after_oblique_b.png) |

#### sample_000003

[Agent2 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0004-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000003/rgb/after_oblique_b.png) |

#### sample_000008

[Agent2 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/agents/0006-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-dual-channel-1791297540-train2-direction/samples/sample_000008/rgb/after_oblique_b.png) |

### train_2 / distance

**共同输入与基线：** [请求](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/request.json) → [运行配置](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/configuration.json) → [Agent1 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0000-normalizer.json) → [Case Template](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/template.json) → [稳定化记录](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/baseline/preparation.json) → [编辑前 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/baseline/graph.json) → [编辑前场景](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/baseline/scene)

**运行汇总：** [所有 Agent 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents) · [最终通过清单](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/result.json) · [进度](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/progress.json)

#### sample_000000

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0003-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000000/rgb/after_oblique_b.png) |

#### sample_000001

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0004-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000001/rgb/after_oblique_b.png) |

#### sample_000002

[Agent2 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0002-proposer.json) → [第 0 轮 Symbolic DSL](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/proposals/round_000.json) → [DSL、采样与执行检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/sample.json) → [编辑后 Scene Graph](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/graph_after.json) → [配对 RGB 信息](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/pair.json) → [Agent3 对话](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/agents/0005-reviewer.json) → [最终视觉检查](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/review_0.json) → [编辑后冻结场景](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/scene)

| 角度 | 编辑前 RGB | 编辑后 RGB |
|---|---|---|
| 俯视 | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/before_top.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/after_top.png) |
| 斜视 A | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/before_oblique_a.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/after_oblique_a.png) |
| 斜视 B | [编辑前](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/before_oblique_b.png) | [编辑后](../../outputs/case_edits/case-edit-geometry-1791295215-train2-distance/samples/sample_000002/rgb/after_oblique_b.png) |
