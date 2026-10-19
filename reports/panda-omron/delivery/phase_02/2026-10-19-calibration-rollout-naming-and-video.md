状态：done
日期：2026-10-19
替代：无
关联：[PandaOmron 运行指南](../docs/panda-omron.md)、[全流程实施记录](phase_02/2026-10-17-panda-omron-full-flow.md)、[无障碍物标定](2026-10-19-val103-cup30-no-obstacle-calibration.md)、[数据格式](../../../../docs/data-format.md)

# 标定 rollout 命名与视频记录

## 需求

1. 标定 rollout 目录名改为按点位序号，形如 `S00xx/N`（站位号/第 N 次试验）。
2. 标定 rollout 应能可选保留视频。

## 改动

- `src/lastmile_dataflow/workflows/collection.py`
  - 新增 `calibration_attempt_id(scene, station, trial)`：单场景返回 `S0021/0`；多场景加场景前缀 `panda-val103-cup-S0021/0`，避免各场景站位号都从 S0000 起导致撞名。
  - `_prepare_run` 按 `len(scenes) > 1` 给每个场景写入 `attempt_prefix`。
  - `_execute` 改为接收现成的 `attempt_id`（不再内部生成编号），目录名语义由调用方决定。
  - `_station_trials` 不再接收 `attempt_ids` 计数器；标定路径走 `calibration_attempt_id`。
  - `_recovery_trials`（连续采集）保持原运行内递增编号 `rNNNNNN`。
- 视频不需要新逻辑：`--record-video` 开关已存在（`CollectionConfig.record_video`，`runtime/execution/context.py` 逐控制步调用 `recorder.video_frame`），默认关闭，本次显式开启。
- 测试与文档同步：`tests/workflows/test_collection.py` 改为校验新命名；`docs/data-format.md` 记录标定与连续采集两种 rollout 目录约定。

## 实际执行

```bash
# 标定：新命名 + 视频
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/diagnostics/panda-omron/flow-scene-008 \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json --gpu 3 \
  --run-id flow-calibration-004 --output-dir outputs/diagnostics/panda-omron \
  --record-physics --record-video

# 复用新标定的连续采集
.venv/bin/lastmile-dataflow collect-lastmile \
  --dataset outputs/diagnostics/panda-omron/flow-scene-008 \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json --gpu 3 \
  --calibration outputs/diagnostics/panda-omron/flow-calibration-004 \
  --run-id flow-collect-002 --output-dir outputs/diagnostics/panda-omron --record-physics

# 物化（CPU）
.venv/bin/lastmile-dataflow collect-lastmile \
  --materialize-from outputs/diagnostics/panda-omron/flow-collect-002 \
  --run-id flow-materialized-002 --output-dir outputs/diagnostics/panda-omron
```

## 结果

- `flow-calibration-004`：`rollouts/S0021/0`、`S0021/1`、`S0020/0`、`S0020/1`、`S0013/0`、`S0013/1`，每目录含 `videos/front_camera.mp4` 与 `videos/robot0_eye_in_hand.mp4`（h264、320×240；成功 trial 275 帧左右，S0020 规划无解较短）。标定结果与 `flow-calibration-003` 一致：S0021 2/2、S0013 2/2 成功，S0020 0/2 规划无解，完整标定、2 个合格配对。
- `flow-collect-002`：复用该标定，2 个配对均成功；恢复采集目录仍为 `r000000`、`r000001`。
- `flow-materialized-002`：6 条示教全部物化，`source.rollout_id` 保留 `S0021/0` 等原值；`load_training_batch` 读取连续示例返回 `state/actions` 各 `(2,16,12)`。
- 软件回归：225 项通过，1 项显式 OpenGL 测试跳过。

## 限制

- 视频显著增大输出体积（本次每成功 rollout 约 176 KB/相机），默认仍关闭，按需用 `--record-video`。
- 多场景标定的前缀命名已实现但本机无多场景数据集实例，未做真实多场景运行验证。
- 每站 2 次仍仅为接入规模验证，不据此估计跨场景成功率。
