> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../README.md)。

# MolmoBot VLA 正式接入增强后的最终站位

这是最终点集入口；旧的“native重建快照 + resume + lattice采样”不满足最多20点要求。

## 现有两组：直接运行

```bash
export DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export MUJOCO_GL=egl
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
# 检查GPU空闲情况后指定编号；本例的1不是固定要求。
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/case_edits/no_edit_molmobot_case1_v000_final_stations.json \
  --run-id "no-edit-molmobot-final20-case1-v000-$(date +%Y%m%d-%H%M%S)" \
  --houses 103 --targets Cup_30 --gpu-ids 1 --workers 1
```

Case2 用 `no_edit_molmobot_case2_v001_final_stations.json`。无需手工拷贝或第一次 `--resume`。
当前配置绑定新 `outputs/diagnostics/molmobot-final-stations-case*-v*-20261011-r1/variants/vNNN`，
不是原cuRobo包；每组20点×5次试验。续跑才使用相同配置/run-id/GPU参数加 `--resume`，代码与输入摘要必须一致。

## 其它变体的构建链路

1. 继续复用已提交的 `scripts/rebuild_augmented_native.py`，得到native配置的物理初始快照。
2. 用新增 `scripts/revalidate_native_stations.py` **重新验证全部原始稠密候选点**：

```bash
MUJOCO_EGL_DEVICE_ID=6 "$DATAFLOW_PYTHON" scripts/revalidate_native_stations.py \
  --variant outputs/diagnostics/<原增强run>/variants/vNNN \
  --native-scene outputs/no_edit/<native准备run>/scenes/val_103/scene \
  --config configs/no_edit/molmobot.json \
  --output outputs/diagnostics/<唯一native重验run>
```

3. 为新配置设置 `augmented_variant` 指向上一步的 `variants/vNNN`，通过正式collector启动**新**run。

输入需native移植的 `rebuild_provenance.json`，源变体、asset/pose、scene source和配置全部匹配。
重验做原生快照有限值、完整高度碰撞、支撑和case检查；每个候选点创建独立Simulation/MjData，
执行native机器人初始化及1024×576真实segmentation可见性检查，再按距离/方位覆盖压缩到最多20。
没有额外settle/physics stepping，不改写旧native快照，不把原cuRobo站位有效性当作native有效性。

## 输出与核验

- native包 `navigation/comparison.json.stations_after`：全部候选点，独立仿真标记、实际初始化位姿、可见性和失败原因。
- `stations_final.json` / `station_selection.json`：只选native通过点；距离极值、8方位×2距离层、联合最远点填充，不是取前20点。
- `native_revalidation.json`：机器人/配置、原native文件hash、来源与点数；`checks.json`记录重验范围。
- formal run `frozen_config.json.augmented_input`、scene `augmented_input/`：快照和最终点集一起绑定，校验来源及副本hash。
- 在VLA模型加载**前**写出task `stations.json`、`station_input.json`、`progress.json`，明确点数/计划trial；model加载状态在scene `model_service_status.json`。
- `scripts/audit_no_edit_station_input.py <formal-run> --output <audit.json>` 只读检查源hash、点集逐项相等、计划次数及已发生的trial/attempt IDs。

VLA模型加载、输入接入验收、实际动作执行和任务成功是不同状态。
旧run中的24/58点结果保留，不原地缩表或混入新试验；旧runtime超时也不因改点集而自动消失。

## Episode超时继续策略

单个episode墙钟超时现在独立记录为timeout并继续后续episode；见
超时隔离与统计定义。历史已停止run不会自动续跑。
