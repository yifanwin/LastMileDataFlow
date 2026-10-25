# lastmile-datasets 实现与验证

- 状态：done
- 日期：2026-10-16
- 替代：无
- 关联：[需求定义](../requirement/01-lastmile-datasets.md)、[当前状况](../../curobo-time-optimization/info/01-current-status.md)

本文保留当轮数据集软件交付事实；2026-10-24 的 PandaOmron 主体与每点 3 次需求见当前状况，不据此改写下文历史验证结果。

**已实现全组合采集、两类成功示教索引及独立物化。离线回归通过，合成数据已验证搬迁后脱离源目录读取 batch；未运行真实 RBY-1/GPU 成功采集或模型训练。**

## 实际改动

- `data/evaluation/selection.py` 将资格选择与组合分开：低率含等号、高率含等号，配对为完整 S0×S1；距离、起点数量和候选数量不截断集合。benchmark 的选择定义未改。
- `workflows/collection.py` 在连续执行前保存全部场景的 `pair_index.jsonl`，无路径、局部失败、未知、共享故障及尚未执行的配对均保留。无外部标定时，先在同一 run 完成标定，再生成计划。
- `train_index.jsonl` 纳入所有站位中成功、完整且有真实控制步的标定操作，以及导航和操作均成功的连续 attempt。两类分别标注 `source_type`；直接操作不附加导航字段，也不重新执行。
- 导航难度包含平面距离、实际交给跟踪器的 A* 折线长度和真实 `navigation` 控制间隔数；后者在失败时也从实际记录获取，不含 arrival/操作阶段。
- 标定复用继续匹配实际执行配置与 profile，但不把选点和热图参数作为执行条件。
- 新增 `data/export/lastmile_dataset.py`：独立物化、按原始来源去重、内部相对路径、来源元数据，以及原始 20D 动作的 NumPy batch 读取。
- 物化复制所选轨迹、RGB/已启用深度、录制与必要配置元数据，保留配对全集；失败轨迹、非必需诊断视频、仿真资产和模型权重不打包。缺必需数据时明确未完成，不静默删样本。
- CLI 仍只有五个命令；`collect-lastmile --materialize-from` 是无需 GPU/资产的独立模式。新输出禁止覆盖输入或已有 run。
- 更新 README、运行手册、数据格式、架构和配置说明；运行手册第 2 节为标定，第 3 节为生成、物化和 batch 读取，第 4、5 节为 benchmark 与结果解释。

## 验证结果

| 验证 | 实际结果 | 能证明的范围 |
|---|---|---|
| 相关领域回归 | 58 项通过 | 标定复用、全组合、两类来源、故障停批、物化和执行判定软件边界 |
| 全套离线回归 | 217 项：216 通过、1 跳过 | 当前仓库的软件回归；跳过项为已有显式可选渲染测试 |
| 全组合 fixture | 2 个 S0 × 3 个 S1 = 6 个 pair；无路径仍保留 | 含阈值边界、不受距离/数量限制，低率和中间率站位的单次成功也可纳入 |
| 跨场景共享故障 fixture | 全量 12 个 pair 保留，仅执行第 1 个，剩余保持未执行 | 不继续消费共享故障服务，不把未执行冒充任务失败 |
| 物化与搬迁读取 | 3 条引用去重为 2 条不同来源轨迹；同编号跨 run 不误合并 | 两类来源的内部副本、来源保留与重复引用去重 |
| 阻断原目录访问后读取 | 原引用、标定及物化原位置均禁止读取；搬迁副本仍可组成 batch | NumPy state `[2,2,22]`、action `[2,2,20]`、RGB `[2,2,8,8,3]` 和深度 `[2,2,8,8]` 的独立读取 |
| 简化 MuJoCo 实际 stepping | 3 个 navigation 间隔 + 1 个 arrival 间隔后失败，导航步数为 3 | 计数来自真实执行回调；不是 RBY-1 导航成功验证 |
| CLI 实际执行 / help / 分发、格式检查 | 通过 | 仓库外启动已安装入口、禁用 CUDA、无 GPU/资产参数，物化后可读取 batch；五个命令保持不变 |

执行的主要命令：

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest \
  tests.workflows.test_collection tests.workflows.test_cli \
  tests.data.test_dataset tests.data.test_lastmile_dataset \
  tests.runtime.test_outcomes tests.runtime.test_lifecycle -q

MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v

.venv/bin/lastmile-dataflow collect-lastmile --help
git diff --check
```

## 使用与剩余边界

按[运行手册第 3 节](../../../docs/运行手册.md#3-lastmile-datasets-生成)先生成引用型结果，再调用独立物化。`--calibration` 必须指向具体标定 run，不是父目录。

- 测试使用简化仿真、mock 结果和合成轨迹/观测；没有生成或宣称真实成功机器人示教。本轮未修改、删除或搬迁既有采集数据和外部资产。
- 引用型结果仍依赖来源目录；只有物化完成后的训练读取路径自包含。来源元数据中的旧路径仅供追溯，训练读取器不访问它们。
- 物化读取器对应本仓库原始 `trajectory.npz` 控制格式，不自动适配任意外部派生格式。非原生夹爪的 MolmoBot 动作映射仍需显式标定，不宣称完整上游训练兼容性。
- 没有验证真实 RBY-1/GPU rollout 成功、真实规模复制吞吐、模型训练效果或可搬迁仿真重放。
- 旧连续采集的 `episodes.jsonl` 不作兼容读取；benchmark 使用自己的 `episodes.jsonl`，未更改。
