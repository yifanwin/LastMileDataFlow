> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../README.md)。

# MolmoBot 工作空间操作评测

新增独立 VLA 后端，不调用 cuRobo/IK，不进入 A* 示教收集。
共享原始 val 任务枚举、半径 **2 m** 站位、初始化过滤、每点默认 **5 次**与 Gaussian 热图。
接口回归不代表真实任务通过；实际状态看对应新 run。

## 配置与协议

入口配置是 `configs/no_edit/molmobot.json`，模型配置是 `configs/policies/molmobot.json`。
参考 `MolmoBot/.worktrees/upstream-main/MolmoBot` 的 SIM 配置，不混用 standalone
robot server 默认值。冻结 upstream commit、源码摘要、config.yaml/model.pt SHA256。

`gripper_mode` 可选 `model_default`、`binary`、`continuous`：
默认 pick 连续、open 按阈值5二值化为 ±100；另外两种模式显式覆盖全部任务。
改变模式须新 run，不混合分母。默认不会自动把三种模式全部跑一遍。

VLA 使用 `rby1-multitask-molmobot-native-v1`。20D布局不变，但夹爪为原生 motor
控制，**不是旧位置协议的米值**。不修改旧后端的位置伺服。状态输入仍取实测夹爪位置。
控制器内置执行器/躯干限位处理显式记录，工程单步安全上限仍保留，不额外缩放越界动作。
躯干 h → `[0,h,-2h,h,0,0]`，h 为关节参数；Head 不进入策略动作。

真实原生夹爪空夹测试已确认：**−100 打开，+100 闭合，0 不主动闭合**。
闭合瞬态出现双指碰撞；接口 smoke 的 pick/open 都被原有碰撞阈值停止。
这是旧规则接口记录，不是“接入即抓取成功”。现改为 `runtime-record-only-v1`：
**删除执行期穿透深度/接触终止条件**，仍保留真实碰撞，不添加 contact exclude，
不改变模型动作、原生 motor、求解器或转换为位置伺服。旧 run 保留，新规则单独新 run。
详细证据见接入记录。

状态为22D：base3 + left7 + gripper1 + right7 + gripper1 + torso3；
torso取实测六关节的 `[1,2,3]`。预测16步、执行8步后重新推理；默认400策略动作。
policy/control/physics为100/20/4 ms，增量只在每个策略动作开始解析一次。

## 相机与语言

真实渲染1024×576，不由低清录像放大；输入顺序right wrist / head / left wrist。
相机使用RBY1GoProD455名义视场；当前版本不额外随机化外参，实际参数记录到attempt。
头部图在模型进程调用指定upstream鱼眼函数一次：4:3中心裁剪、魚眼、边缘裁剪，
再交给检查点预处理器。上游函数自带微小畸变随机性按attempt种子管理。

pick使用英文类别指令，无额外点提示。open使用最多一个首帧可见目标部件点和处理后的
首帧条件图；图/点变换沿用upstream。真正首批模型输入PNG保存在attempt。
不向策略提供grasp、目标3D位姿或规划结果；同类物体的语言消歧限制不作隐藏。

## 运行

从此worktree根目录运行，使用已有环境。模型进程PYTHONPATH优先指向指定upstream源码，
不修改外部工程，也不调用其场景执行器。

```bash
export DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export MUJOCO_GL=egl MPLCONFIGDIR=/tmp/molmobot-mpl

# smoke：每任务最多一次，不是完整热图
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/molmobot.json --run-id <unique-smoke-id> \
  --houses 103 --targets Cup_30 open-f348afd24bb7 --max-trials 1 \
  --workers 1 --gpu-ids 0 1 2 3 4 5 6 7

# 单场景全部任务、每个有效点5次
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/molmobot.json --run-id <unique-val103-id> \
  --houses 103 --workers 1 --gpu-ids 0 1 2 3 4 5 6 7
```

GPU候选须连续3次、间隔5s都满足利用率≤20%、空闲显存≥24576MiB。
按窗口最大利用率升序、最小空闲显存降序选择；没有满足条件的卡则等待。
模型在每个scene worker内常驻，私有本机IPC，一次只处理一个活动attempt；退出时清理子进程。
模型解释器需具备所选 upstream 的推理依赖；采集解释器需安装 `.[vla-client]`。
离线运行需提前提供 Qwen tokenizer 缓存到 `outputs/dependencies/molmobot-cache/hf/`；
配置模型权重只读，模型服务不把缓存写入 upstream。smoke 实测 CUDA allocated 峰值约10.2GiB，
选卡仍保守要求24GiB空闲（不等于把峰值固定写死为24GiB）。

## 验收、未知与输出

双臂均由策略控制，不套用idle-arm漂移或规划TCP tracking判断。Head固定、2m边界、
仿真有限性仍检查；接触/穿透、躯干跟踪误差只记录。pick仍要求同一手双指承力、抬升≥5cm、
脱离支撑稳定≥2s及原滑移阈值；open仍要求真实开启行程和接触保持。
每物理步采样，左右手独立累计，不拼接手指证据。

正常策略预算耗尽/非法动作/工作空间协议停止计失败；仿真异常为 `simulation_failure`，不计失败分母。加载、OOM、通信、视频设施错误不计零。
墙钟超时和中断为incomplete。未知trial后续用新attempt补齐，不覆盖旧证据。
`station_evaluation_complete`表示采样完成，不表示任务成功或可发布导航示教。
低成功率也保留热图；不自动调用A*或导出导航示教数据集。

新产物在`outputs/no_edit/<run>/`，全部运行日志在`outputs/logs/`。
实际执行保留四视频、原始/后处理动作、实际ctrl、逐物理步证据和qpos replay。
`policy_requests.jsonl`在校验和下发前保存预测（包含被拒绝动作）；
`policy_actions.jsonl`只记录已实际 stepping 的指令。`model_chunk_state` 是该批预测实际使用的22D状态，
不是每个buffer动作重用时的最新状态。open 的实际点提示字符串也单独保存。
机器人原视频为鱼眼前RGB，第三人称为分析合成画面，不作为模型输入。
离线推理等待不推进仿真，模型加载/推理时间与仿真时间分开，不能宣称实时部署通过。

热图输出普通版与站位编号版PNG/SVG、NPZ、heatmap.json、统计与完成度。
Gaussian使用绕障碍栅格距离，σ=采样间距、截断min(3σ,0.35m)。未知不计零，
完整点为k/5；0/5不证明不可达，区域插值不是新增试验。
未完成点按已观测终止trial作临时插值，须结合 `station_statistics.json` 的 `complete` 和
`terminal_trials` 阅读；临时热图不得冒充每点5次完成图。

## 回归

```bash
PYTHONPATH=src "$DATAFLOW_PYTHON" -m unittest discover -s tests -p test_molmobot.py -v
PYTHONPATH=src "$DATAFLOW_PYTHON" -m unittest discover -s tests -v
```

简化模型仅证明控制/时序/分类逻辑。真实模型、视频、预处理和完整采样须分别检查。

新 VLA 验收协议为 `mobile-vla-physical-v2`，冻结接触策略及
`trajectory_metrics={task_success, forbidden_contact_count, simulation_valid, ...}`。
计数只用于后处理分级，不自动把有接触的任务失败归因为 Collision。
初始化过滤仍在 begin 前执行；场景构建检查最终非法重叠，不保留旧运行期毫米阈值。
