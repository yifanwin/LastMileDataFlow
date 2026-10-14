# Raw-first 采集与 MolmoBot Adapter 使用说明

**新采集默认实时保存 raw / canonical；Adapter 只实现 MolmoBot。**
旧 20D 控制接口、attempt 文件和 no-edit-dataset-v2 保留。已有输出不迁移、不覆盖，代码变化后必须使用新 run-id。
协议定义见 [统一采集协议](canonical-data-protocol.md)。

## 1. 默认采集什么

`CollectionConfig.raw_first=true`、`record_depth=true` 为默认值；现有 JSON 未写这两个字段也采用默认值。
`--no-video` 只关闭 MP4，不关闭新的无损 RGB/depth。no-edit 旧 `record_png=false` 不再导致完整 qpos/qvel 丢失。

实际保留旧 attempt 布局，并增设：

```text
attempts/.../<attempt_id>/
  config.json / trajectory.jsonl / observations.json / result.json / artifacts.json
  raw/
    model-map.json                  # joint/qpos/qvel/actuator/body/geom/sensor 映射
    scene/model.mjb / initial.npz / final.npz
    states/<frame>.npz              # 全 qpos/qvel/ctrl/act/qfrc_actuator/sensordata
    state-index.jsonl
    commands.jsonl / command-arrays/ # 原始请求、实际提交；异常原值也保留
    intent-events.jsonl / intent-arrays/ # 预处理前的 cuRobo/VLA 输入
    controls.jsonl / contacts.jsonl # 实际控制更新和逐 physics tick 接触
    observations.jsonl
    observations/<camera>/<frame>.png
    depth/<camera>/<frame>.npz      # Renderer 原 depth + 有效 mask
  canonical/
    manifest.json / checksums.json
    frames.jsonl / robot-state.jsonl / executed-actions.jsonl
    task.json / scene.json / scene-states.jsonl
    calibration.json / camera-poses.jsonl
    outcomes.json / events.jsonl / expert.json
```

- 记录在 `Simulation.begin/step/step_policy` 中发生，不靠 trajectory 转换或 qpos 回放补造。
- 每个状态边界采集全部**启用**相机；第三人称未启用时明示 disabled，不生成虚假观察。
- pose 来自实际 GL 视图；固定机器人相机另存原始 `cam_xpos/cam_xmat`，并审计光学轴转换。跟随 observer 每帧更新外参。
- 每次控制更新先落盘，再 stepping；每物理 tick 保存真实接触。相机异常保留已执行状态/控制，包标 recording_incomplete，Adapter 拒绝使用。
- 20 Hz 区间实际为 12/13 physics ticks；native profile 保存 policy 10 Hz、control 50 Hz、physics_dt 0.004 s 三层计数。
- 原始数组无损保存非有限值；JSON 仅提供 null 投影。请求拒绝不生成 executed_action，提前停止不补足 nominal 时间。
- 每条日志/数组分块 fsync；硬崩溃的在途尾部仍可能缺失，不宣称断电零丢失。未封存包不进入 Adapter。
- 当前 live-v1 **冻结频率、physics_dt 和 controller profile**；执行中更改会拒绝下发，动态配置流尚未实现。
- MP4/分析画面及 policy processed input 显式标 derived；原始 RGB 不被拼接、覆盖或鱼眼图替代。

## 2. 当前重构边界

独立审计和索引 CLI 已删除。collector、adapter 内部仍执行现有协议检查，尚未缩到最小主记录。
本轮不改变 raw/canonical 数据格式；内部检查与重复索引在步骤三处理。

## 3. 只导出 MolmoBot，且不改原包

输出路径必须在源 episode 外且不存在。没有实现 π0 Adapter，也没有相关占位入口。

```bash
# native profile：22D 状态 + native 20D action + head fisheye + 腕图 + 16步 chunk/mask
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow export-molmobot \
  --attempt <sealed-attempt> --output outputs/derived_packages/<new-export> --seed 42

# cuRobo position profile：没有 motor 标定时，只导出状态与图像
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow export-molmobot \
  --attempt <sealed-attempt> --output outputs/derived_packages/<new-export> --state-images-only

# position → native action：必须由使用者提供显式标定映射
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow export-molmobot \
  --attempt <sealed-attempt> --output outputs/derived_packages/<new-export> --gripper-map <calibrated-map.json>
```

映射 JSON 必须包含 `source_controller_profile="position-v1"`、`target_controller_profile="molmobot-native-v1"`、
`calibration_source`，以及 left/right 各自的递增 `position_targets_m` 与同长度 `motor_commands`。
motor 范围为 [-100,100]；不外推、不默认按符号转换。代码只能校验格式/范围，**不能代替物理标定验证**。
该转换声明“不等价控制器重放”，不保证 force/motor controller 能复现 position controller 的运动。

导出包含 `data.npz / samples.jsonl / images/ / manifest.json / checksums.json`。
状态顺序为 base3、left7、left finger1、right7、right finger1、torso[1,2,3]；头部及全部原状态仍留在 source。
相机顺序为 right wrist、head、left wrist，observer/expert/场景真值不进入策略输入。
100 ms 窗口只使用真实状态边界；12/13 ticks 可合并，不插值图像，不静默裁剪动作。
不完整尾窗和重采样后超过冻结安全增量的动作标 mask=false；chunk padding 也标 false。
完整动作监督为末次提交的绝对目标相对窗口初始实测状态，**不是 measured_motion 冒充命令**。
raw 与完整 source 摘要绑定并在导出前后校验；源包不改动，失败导出不发布半包。

head warp 使用局部随机种子，保留实际系数、crop、采样 map 和有效 mask。
NumPy 实现与本地只读 upstream 对照测试通过（同种子、测试图最大 uint8 差值 ≤1）；最终 resize/归一化交给 checkpoint preprocessor。
这只是模型格式/augmentation 转换；**不保证源相机 FOV、外参和所选 checkpoint 的物理 rig 匹配**，需按 source calibration 另验收。
open 必须加 `--conditioning-point X Y`（首帧原 head 图中 [0,1] 归一化点）；点和首帧 conditioning image 使用同一实际 warp。
点出 crop 会拒绝；不从 scene 真值自动制造提示。语言使用已记录指令，不自动翻译或猜目标。

## 4. 验证与当前边界

```bash
MPLCONFIGDIR=/tmp/lastmile-mpl-cache PYTHONPATH=src:tests \
  ../molmospaces/.venv/bin/python -m unittest discover -s tests -v

```

本次真实 ProcTHOR train_2/RBY-1 验证使用 Mesa 软件 EGL，证明短 stepping、RGB/depth/标定采集和审计链路。
CUDA driver 当前不可用；**cuRobo CUDA、MolmoBot 推理、抓取/导航成败及真实四相机 no-edit rollout 未验收**。
实机时钟同步、动态参数流、通用支撑关系/夹持真值推断仍不属于本实现；未知值保持 unknown/null。
验收记录见 [实施报告](../reports/no_edit/raw-first-implementation-20261011.md)。
