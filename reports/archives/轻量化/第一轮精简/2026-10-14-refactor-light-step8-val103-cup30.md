> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 步骤八：val103 / Cup_30 最小真实复现

**已生成并重载真实增强场景，跑过 cuRobo V2 逐点规划和真实连续导航；尚未得到成功操作示教或真实困难 S0 评测。** MolmoBot 逐点测试和单例 bench smoke 已结束。步骤八的有限复现执行完成，但主链成功闭环仍未验证；以下不把产物存在当作任务成功。

实验始于 2026-10-14，持续到 2026-10-15。使用 `refactor-light` 自身的 `.venv`，不借用其他仓库环境，不改原始资产，不提交代码或合并分支。

## 1. 输入与有限实验范围

- 场景：`procthor-10k-val/val_103.xml`；目标：`Cup_30`，任务 `pick-0086`。
- 数据路径由 `data_root` 显式配置；机器人为 RBY1。权重使用已有 `MolmoBot-RBY1Multitask`，未重新下载。
- 种子：`20261014`；场景派生种子：`1765307115`。实际配置、种子、Git commit 保存在实验根目录的 `config.json`；逐次种子保存在试验记录。
- 场景仅保留 Case1、一个增强版本、三个采样点；每种方法每点两次。规划抓取候选最多两次，IK seeds 8、trajopt seeds 2，其余采用 V2 配置，包括其要求的 `max_attempts=5`。
- 单次墙钟预算 300 秒；MolmoBot 最大 400 控制步，RPC 超时 120 秒。不录制全视频，保留实际状态轨迹与必要视图。
- 场景构建与 cuRobo 使用 GPU 3；连续补测使用空闲 GPU 2；最终策略测试选择空闲且剩余显存充足的 GPU 6。

实验根目录：`outputs/diagnostics/step8-val103-cup30/`。这是一个小规模工程复现，不是策略排行榜；每点两次不能支持稳定成功率或方法优劣结论。

## 2. 已确认的真实执行结果

| 链路 | 实际结果 | 能说明什么 |
|---|---|---|
| 场景增强 | 1 个可重载场景，6 个新增物体，3 个保留点 | 原始场景、增强与载荷加载确实运行 |
| cuRobo V2 逐点 | 3 点 × 2 次；6 次有效规划结果均无解，执行动作 0 步 | 真实规划约束下的有界负结果，不是成功 rollout |
| 自动 S0→S1 采集 | 匹配标定完整，合格配对 0，示教 0 | 未伪造困难/容易点，也未重复标定 |
| 单独几何连通补测 | A* 路径 0.25 m；导航 73 步成功；整次 93 步，随后抓取规划失败 | 连续导航真实执行；不是自动困难样本，也不是成功示教 |
| MolmoBot 逐点 | 6 次，0 次成功；4 次墙钟超时、2 次达到 400 步上限；共 1538 动作步 | native profile 独立标定；预算内成功 0/6 |
| 困难 S0 bench | 匹配标定中合格配对 0，困难 episode 0；另跑单例未标定 smoke，306 步后超时，成功 0/1、覆盖 1/1 | 真实困难评测未执行 |
| 路径移动 | 移动 MJB 与状态，显式更新路径后加载，状态相等，nq=327 | 同版本编译场景快照可搬移；不证明独立 XML/mesh 重建 |
| 核心单测 | 35 项通过；另有障碍物用例 11 项通过 | 覆盖保留功能及这次遇到的具体回归，不替代真实任务结果 |

### 场景确实被增强

原场景 Cup_30 附近找到 60 个物理初始化及可见性筛选通过的点；增强版本最终保留 S0048、S0010、S0046。新增一把地面椅子和五个桌面物体：两类碗共三个、一个杯子、一个马克杯。

实际增强场景的头部相机视图

图 1：从持久化场景重载后渲染的实际画面。此图为 position profile，不是 MolmoBot 的 warped 输入图。

### cuRobo 没有产生可用 S1

cuRobo 三个采样点的观测成功率

图 2：每点有效规划次数为 2，观测成功次数均为 0；白色区域未观测。平滑场仅作可视化，不用于补造试验或选点，也不代表导航可达性。原始记录在 `v2-compatible/curobo/trials.jsonl`。

约束规划确实调用了 V2 goalset/IK；结果为 `constrained_planning_failure` 或 `planning_budget_no_solution`。因此 `collect_lastmile()` 复用完整匹配标定后返回 0 对，没有成功操作可以转成示教。

### 连续补测到达 S1，但操作仍失败

三个保留点之间没有找到路径。为验证连续执行，另在**同一个增强场景**进行有限几何重采样：29 个物理有效点，其中 4 个通过导航网格筛选；选出 S0032→S0059 的几何连通配对，不把它纳入困难点标定。

实际记录的连续导航轨迹

图 3：来自 `trajectory.npz` 的实测底盘轨迹，不是仅画出的规划线。导航到达误差约 **1.52 mm**；接着抓取规划失败。轨迹共 94 个状态、93 个动作；执行开始后不重置或瞬移。

### MolmoBot 真正执行了动作，但没有成功样本

| 采样点 | 两次实际控制步数 | 结束原因 | 预算内成功 / 试验数 |
|---|---|---|---|
| S0048 | 25 / 312 | 均为 300 秒墙钟超时 | 0 / 2 |
| S0010 | 229 / 172 | 均为 300 秒墙钟超时 | 0 / 2 |
| S0046 | 400 / 400 | 均达到控制步数上限 | 0 / 2 |

MolmoBot 原生相机配置下的逐点结果

图 4：仅来自最终 `policy-fixed/molmobot/trials.jsonl` 的六次试验，没有把早期通信异常算成失败。每点预算内观测成功为 0/2；其中四次超时的物理任务判定仍为 unknown，不等同于证明不可完成。标定按既有预算规则计数，原始状态完整保留。

### bench 只完成未标定 smoke

最终匹配的 native 标定没有高/低成功率配对，因此正式困难 bench 的 episode 为 0。另建一个明确标注 `uncalibrated_smoke` 的 S0048 episode，实际执行 **306 个控制步**后达到墙钟预算，记录完整，覆盖率 **1/1**，预算内观测成功 **0/1**。

`bench-smoke-eval/summary.json` 按现有规则将 timeout 计为预算内失败；`results.jsonl` 保留物理任务判定 unknown。二者分别表示预算评价和未完成的物理判定，不能据此声称困难 S0 成功率为 0，或已经证明任务不可完成。

## 3. 本次发现并修复的问题

只修复实际遇到的阻塞，不增加审计协议或通用恢复框架。

1. **资产检索扫描过重。** 注册 THOR 类别直接利用已有类别表取候选，非注册目录用 `os.scandir`；不先对 NAS 整个 Prefabs 目录逐个远程检查，再采样。
2. **Warp 与 V2 不兼容。** 已安装的 1.18.0 缺少当前 V2 使用的 `warp.torch` 接口；planning 依赖固定为实际验证过的 **1.11.1**，沿用同一 `.venv`。`pip check` 无冲突。
3. **EGL 设置太晚。** CLI 在导入工作流前设置 GPU/EGL；真实测试脚本也在导入 MuJoCo 前设置 EGL。
4. **策略超时后复用旧 RPC 导致阻塞。** 超时关闭客户端，不继续消费上一请求的迟到响应；关闭时不再向可能塞满的连接发送关闭消息。共享基础设施异常写入实际记录后结束这一批，避免反复生成无效试验。

最初 Warp 异常、EGL 初始化错误及 RPC 超时的记录都保留在旧实验目录/日志中，**不计入最终任务失败分母**。最终重测与它们分开保存。

## 4. 复现入口与实际产物

从本工作树根目录运行。先按 `docs/setup.md` 准备资产、模型及 `.venv`，在实验配置里填写自己的 `data_root`、checkpoint 和 tokenizer 位置。

```bash
# 场景配置应显式指向 procthor-10k-val / val_103 / Cup_30
MUJOCO_GL=egl .venv/bin/lastmile-dataflow build-scenes \
  --config <scene-config.json> --output-dir <new-output-dir> \
  --run-id scenes --gpu <idle-gpu> --allow-partial-targets

# 接收上一步已接受场景；root/configs 中放实际机器人、策略配置；root/config.json 保存实验配置、seed 和 git_commit
MUJOCO_GL=egl .venv/bin/python tests/refactor_step8_real.py \
  --root <new-experiment-root> --dataset <accepted-scene-dataset> \
  --assets-dir <data-root/assets> --gpu <idle-gpu>
```

本次接受的场景为 `scene_datasets/scenes-bounded/`；最终 cuRobo 与自动采集结果为 `v2-compatible/{curobo,lastmile}/`；native 策略场景为 `v2-compatible/native-scenes/`；最终策略与 bench 为 `policy-fixed/`。

额外连续轨迹位于 `continuity-extra-sampling/`；搬移测试位于 `moved-data/`。实际执行的局部补测脚本保存在 `run-scripts/`，其中路径记录本机实验位置，换机器需显式修改；它们不是新增生产入口。日志位于 `outputs/logs/step8-*.log` 和策略实验目录的 `logs/molmobot-policy.log`。

## 5. 限制与下一步

- **未获得成功操作示教。** 连续底盘导航成功不能替代 Cup_30 拾取成功。
- **未验证真实困难 S0 评测。** 无匹配困难样本时，bench smoke 只能验证入口和结果记录。
- position 与 native 的控制器、相机不同：重新生成 native 场景和采样，分别标定，不混用分母。
- MolmoBot 墙钟预算包含运行开销。首次模型加载约 424 秒；首轮渲染等开销显著减少可执行动作数。超时表示预算内未观测到成功，不代表已经证明物理任务失败。
- 搬移测试仅加载同版本 MJB 与状态，没有进行全新机器的资产下载及 XML/mesh 独立重建。

下一步应先诊断当前 Cup_30 的规划可达性、采样点和策略运行开销；取得少量可信高/低成功点后再做困难 S0 评测。不扩大为全场景验证或补造困难标签。
