# Case 工厂：当前实现与运行边界

新入口是 `case-factory`，旧 `case-edit` 保留但冻结 Agent 层功能开发。
设计依据为[重构提议](../reports/phase2/pipeline-restructure-proposal.md)、
[具体定义](../reports/phase2/case-definition-proposal.md)和[实施计划](../reports/phase2/case-factory-implementation-plan.md)。

**当前是首批实现，不是第 0–8 阶段全部交付。** 五份 CaseSpec、共用配对判定、
原生 case1 编排和宽松复核已接入。抓取筛选器已用阶段三的真实成功/失败抓取校准，
采样器已改为按手指截面切片；生成的候选尚未在真实场景中用 strict-pick-v3 验收，L2 任务仍为 0。
完整证据和缺口见[实施报告](../reports/phase2/case-factory-initial-implementation-20261008.md)和
[抓取与静置修正报告](../reports/phase2/case-factory-grasp-settle-fix-20261008.md)。

## 已实现的路径

1. 读取人写的 CaseSpec；不运行旧 normalizer、strategist 或 proposer。
2. 检查机器人能力配置与**仿真筛选通过的原生 RBY-1 抓取缓存**；缺少前提时直接退出，不能回退到 DROID。
3. 读取纯场景，初始化、静置，按几何与受力接触确认支撑关系；不使用 benchmark 的任务、起点或成功标签。
   源场景整体只观察：全场景严重穿透与求解器告警仍是硬检查；稳定性只要求每个候选的任务范围
   （目标及同一支撑面 0.5 m 内的物体），按 1 s 窗口的位移与转角判定，瞬时速度超标只记为抖动告警。
4. 按支撑面局部 `u-/u+/v-/v+` 四边采样；检查初态、头部可见性、双臂及躯干采样下的 IK。
5. 规划完整的预抓取、接近、抬升、撤离四段；规划状态不冒充执行轨迹。
6. 反推 S0，计算 C0 / C-yaw / C1、整条起始边的有限采样证据及保守地面网格路径。
7. L1 后可调用现有 strict-pick-v3 独立执行与审计。只有两侧证据完整、审计通过，才可能升级 L2。
8. 可选一次视觉复核，冻结任务记录、漏斗与失败原因。

`same_edge` 的距离困难单独输出 `case1-S`，不计入 case1 配额。
规划无解一律表示**冻结预算内无解**。网格路径不是连续导航成功。

原生后端目前仅覆盖 case1 主链路。case1.5 的站位归因和 case2/3 的反事实判定已有纯逻辑检查，
但它们的原生编辑事务、把手标注、去杂物对照尚未接通；不会伪造通过。
编辑路由保留 `editing_pending.json`。导航控制、断点续跑/发布和 Agent A/B 尚未实现。

## 第 5 阶段的临时宽松策略

配置：`configs/case_factory/plausibility.json`。

- 金标准样本、误杀率/漏检率校准暂缓，不作为启动依赖。
- 仅 P1（渲染/资产异常）阻断，而且要求模型置信度 ≥ 0.98，并引用实际视图。
- P2–P6 的问题先标记，不阻断；原始场景不应用 P6。
- 阻断项不确定、低置信度失败、服务/格式/渲染异常进入人工抽检队列。
- 每个任务最多一次 HTTP 调用，不补图、不格式修复重试、不供应规划或成败标签。
- 该策略标为 `relaxed_uncalibrated`。**0.98 是宽松启用策略，不是经校准的错误率保证。**

不编辑分支支持固定头部、俯视和四边斜视图，并通过实际分割给目标画框编号。
编辑前后固定配对图尚待编辑分支接入。
此宽松策略不修改稳定性、穿透、可达、换边、反事实或 strict-pick-v3 的硬条件。

## 运行

从仓库根目录运行。解释器需具备项目依赖和 `phase3` 可选依赖；cuRobo 使用已有原生 GPU 安装。
GPU 在启动前用 `nvidia-smi` 检查，设备通过环境变量传入，代码不硬编码设备号。

```bash
export DATAFLOW_PYTHON=../molmospaces/.venv/bin/python
export CUDA_VISIBLE_DEVICES=4 MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=4

# 已提供本机模型的几何测量值；更换模型时重新测量到新文件，不能覆盖旧结果
bin/lastmile-dataflow robot-capability --robot-config configs/robots/rby1.json \
  --output outputs/case_factory_assets/<unique-id>/capability.json

# 纯场景中的资产实例；不读取任何旧抓取文件
bin/lastmile-dataflow grasp-generate \
  --scene-xml ../molmospaces_data/assets/scenes/procthor-10k-val/val_103.xml \
  --metadata ../molmospaces_data/assets/scenes/procthor-10k-val/val_103_metadata.json \
  --target <instance-id> --capability configs/robots/rby1_capability.json \
  --output outputs/case_factory_assets/<unique-cache-root>/<asset-id>
```

抓取命令将结果写在指定目录的 `result.json`，缓存写在 `cache/`。
即使加载或筛选失败也保留证据；目录存在即拒绝重跑。
孤立夹爪筛选通过不等于真实场景 strict-pick-v3 成功。

- 采样器 `pinch-slice-v2`：只从上方、侧面和斜上方接近；指尖深入物体最外缘 1.2–5.2 cm（掌部始终在物体外）；
  在手指截面内找连续材料段居中夹持，张开的两指所在空间必须为空，两侧被夹面须在摩擦锥内；
  表面采样点间距约 3 mm。
- 筛选器 `isolated-close-lift-shake-v3`（v3 在 v2 基础上并入与掌部刚性相连的手腕几何，闭合/抬升/保持中非手指部位碰物体即失败）：夹爪的手指惯量、阻尼、armature、单执行器与等式耦合、
  接触参数、求解器选项均取自真实模型；夹爪为自由刚体，经 weld 约束跟随 mocap 目标（mocap 本身无速度，
  无法靠摩擦带起物体）；掌部与手指之间按真实模型的父子关系排除碰撞。
  已用阶段三 Cup_30 抓取校准：+10 mm 接近（真实严格成功）通过，0 mm 接近（真实执行失败）不通过。

将工厂配置中的 `grasp_root` 指向选定的新缓存根目录；其他相对路径按配置文件位置解析。
`capability_path` 必须指向当前模型的测量文件。
`grasp_root` 中没有仿真通过的缓存时，命令输出 `dependencies_missing`，而非产生任务。

```bash
# 调试仅允许 val-103；--planning-only 只能产生 L1，退出码不代表正式交付完成
bin/lastmile-dataflow case-factory --factory-config /path/to/frozen-factory.json \
  --dataset-dir ../molmospaces_data/assets/scenes/procthor-10k-val --houses 103 \
  --planning-only --run-id <unique-run-id>

# 正式来源为 train；不传 --houses 时枚举该数据目录全部 train 原始场景
bin/lastmile-dataflow case-factory --factory-config /path/to/frozen-factory.json \
  --houses 0 1 2 --quota 5 --run-id <new-run-id>
```

只有明确传入 `--api-settings` 才发送固定图像到外部视觉服务；默认关闭全部 Agent。
可用 `--provider`、`--model` 指定服务；不读取或打印密钥到证据中。
单次站位软预算与全运行硬期限写在工厂配置里；超时会终止并回收子进程，不生成虚假失败轨迹。

## 验证

```bash
PYTHONPATH=src ../molmospaces/.venv/bin/python -m unittest discover -s tests -v

# 显式真实 GPU IK smoke，不是可达范围表标定或抓取任务验收
CUDA_VISIBLE_DEVICES=4 MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=4 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python tests/factory_reach_real_smoke.py \
  --planner-dir ../molmospaces_data/assets/robots/rby1m/curobo_config \
  --output outputs/planner_checks/<unique-id>
```
