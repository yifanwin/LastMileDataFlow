# Research 数据协议

当前 `build-scene-datasets`、`collect-lastmile-datasets` 使用本协议。
目标是正确构造、采集、统计和恢复，不负责数据集发布、不可篡改封存或取证。
旧数据不原地迁移；新版本使用新 dataset/run ID。

## 场景：构造检查保留，不再完成后复审

构造、事务回滚、静置、collision/support/case/source-effect、visibility 和合法初始化仍保留。
针对最终状态完成必要检查；之后不能修改影响检查结论的模型、初态或配置。
站位使用覆盖式选择，最终数量不超过 20；构建不会重新加载全部场景做物理复审。

```text
scene_datasets/<dataset>/
  manifest.json                  # scene-datasets-research-v2; building/complete/failed
  recipe.json / build.json       # 实际参数、输入快照身份、构建单元与 resume 配置
  discovery.json / exclusions.jsonl
  samples.jsonl                  # 从已提交 sample.json 汇总的唯一样本索引
  samples/<sample>/
    sample.json                  # 最后原子写入的样本完成记录
    task.json / construction.json / validation.json
    robot_sensor_configs/<config>/
      manifest.json              # scene-station-package-research-v2; 身份与科学配置绑定
      task.json / initialization.json
      stations.json              # candidates、validated、selected_ids、selection
      scene/                     # model.mjb、initial.npz、version.json、instances.json
```

只保留四个固定快照文件的身份摘要及 station/config 摘要，不递归 hash 输出目录。
现有 `scene/checksums.json` 仅绑定这四个固定快照文件，不是 sample/package/dataset seal。
模型与初态配对、仿真版本、机器人配置、实例映射仍由快照加载器验证。
构造期详细诊断留在独立 diagnostics run；样本不再复制完整对照快照、媒体和取证目录。
`complete` 表示满足计划配额，不表示导航或操作成功；`failed` 仍可包含合法已完成样本。

## Episode：一次实际 attempt，一份完成事实

实际轨迹继续复用已有 `no_edit/<child-run>/scenes/val_<house>/attempts/...` 路径，
不搬迁已有执行器，也不再为 research run 复制一套 rollout 数据。
每次实际 attempt 都使用唯一 ID；禁止覆盖。初始化前异常也在全局视图中保留。

```text
lastmile_datasets/<run>/
  manifest.json                  # lastmile-datasets-research-v1; building/complete/failed
  run.json / plan.json           # 关键配置、计划量、trial 预算与种子规则
  selections.json               # calibration/selection 来源指针与 S0/S1 组
  episodes.jsonl                 # 唯一全局 episode 视图，可重建
  summary.json                   # 派生统计，可重建
  exports/molmobot/<episode>/    # 可选格式导出
```

每个实际 attempt 内：`episode.json` 是原子完成记录，raw/canonical 是轨迹载荷。
轨迹 schema 为 `research-canonical/2.0.0`；finalize 关闭并 fsync 流后提交 episode。
没有 episode 完成记录的目录不能当成已完成 trial，也不能进入训练。
完成记录可以表达失败或记录不完整；`complete` 不等于 `recording_complete=true`。
索引不能独立改写任务结果；重建不会再次执行仿真。

完成记录包含 episode/trial/retry/role、task/station/S0/S1、seed、代码与配置身份，
以及执行状态、终止原因、任务结果、simulation_valid、recording_complete、data_valid、
连续仿真标志、轨迹和必需文件引用。未知结果为 null，不自动转换为失败。
calibration 的 S0/S1 可为空；重试沿用逻辑 trial，实际 episode ID 不同。

## 必要数据检查在采集期完成

保留实际提交动作/控制与测量状态的区分、20D/控制语义、有限值、相机标定，
以及状态/动作/帧计数、仿真时间和必需观测对齐。边界记录使用 N 动作、N+1 状态帧。
任务成功和记录质量分别表达；成功但丢失观测的轨迹不进入训练。
默认不写 per-tick contacts 或 physics trace，在线物理监测和碰撞汇总不关闭。
需要深查时显式设置 `CollectionConfig.record_physics_trace=true`，不能事后补造 trace。
research 场景的 MJB 引用运行快照，不逐 episode 再复制整份模型。

## 统计与筛选

成功率按逻辑 calibration trial 计算，每个有效 trial 最多接受一个结果。
真实失败、规划无解和 episode 预算 timeout 进入有效分母；基础设施/记录异常排除并单独计数。
无有效 trial 时成功率为 null。attempt 数、有效 trial 数、不同 S1 数必须分别统计。
计划量来自 plan.json，不能只用已经产出的成功轨迹反推覆盖率。
不完整或 diagnostic 子集只报告 observed 成功率；正式 calibration_success_rate 保持 null。

成功训练轨迹要求 recovery、实际执行、导航成功、任务成功、仿真有效、记录及数据语义有效，
并保持连续仿真；还必须满足对应 task 的 eligibility。
不再要求 seal 或事后 canonical audit。失败/异常轨迹保留，不混入成功示教。
训练/评测应按原始 scene_id 分组；export_config.json 提供 split_group，不自动随机切轨迹。

## Resume 和导出

Resume 验证模型/初态、样本、station、关键机器人/传感器/动作及实验配置。
不因代码 commit 或导出设置变化一律拒绝；实际执行版本逐 episode 记录。
执行语义变化必须更新 execution_contract/protocol 版本并使用新 run，不混合实验条件。
已提交但未入索引的 episode 自动补入，不重跑；缺失必需数据拒绝复用。
运行锁与原子 JSON/JSONL 替换仍保留，不依赖递归 checksum。

MolmoBot 导出只检查自身需要的状态/动作/图像输入，写 export_config.json，不全量复审。
position 夹爪没有真实映射时只能 state/images-only，不能伪造 native 动作监督。
普通转换失败单独写导出错误，不改写采集结果；磁盘等共享资源错误仍安全停止。

```bash
# 需选择具备依赖的解释器和可用 GPU；配置中的资产路径必须适合当前部署。
PYTHONPATH=src MUJOCO_GL=egl <python> -m lastmile_dataflow.cli build-scene-datasets \
  --config <recipe.json> --discovery <discovery.json> --output-dir outputs \
  --dataset-id <new-id> --gpu-ids <gpu> --workers 1
PYTHONPATH=src <python> -m lastmile_dataflow.cli index-lastmile-datasets \
  outputs/lastmile_datasets/<run>
# 显式真实采集/导出 smoke：不调用 CUDA planner，不验证任务成功。
PYTHONPATH=src MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=<gpu> <python> tests/research_real_smoke.py \
  --package <new-scene-config-package> --output outputs/diagnostics/<new-smoke>
```

## 深查与旧数据

重型工具位于 scripts/debug/，正常 collector/exporter 不调用：
`audit_canonical.py`、`audit_legacy_scene.py`、`review_timeout.py`。
直接运行这些工具时使用 `PYTHONPATH=src:.`，输出必须在源数据之外。
历史报告、原始资产、旧采集结果和独立 benchmark 的既有协议不在本次清理范围。
