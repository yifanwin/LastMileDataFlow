> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../README.md)。

# 增强场景的最终站位接入 no_edit

`NoEditConfig.augmented_variant` 指向增强 run 的 `variants/vNNN`，路径相对配置目录。
配置为空时，原始场景采集保持原有行为；指定后，执行端必须使用该变体的
`stations_final.json`，不重新生成 lattice、不用全部有效点，也不做 `[:20]`。

## 输入检查

- 完成的增强 run、通过的物理 checks、原生快照四项校验和。
- 原生机器人配置一致，radius/spacing/图像大小/可见像素阈值一致；换机器人必须重建并重新验证。
- 重新核算原始全量增强后站位的距离/方位覆盖压缩，与点集及 selection 逐项一致，数量为 1–20。
- 每个点必须已有独立仿真初始化通过记录、无新 solver warning，目标和冻结位姿一致。
- 精确匹配场景、metadata、目标 task/body；保留构建阶段 anchor。

收集器自动将原生 scene 与输入记录复制到**新** run，写入 hash 和来源；不再需要手工
`cp scene` + `--resume`。禁止给旧 run 后补点集或混入旧 trials。
续跑时来源、拷贝输入、stations、trials 必须一致，不符合就报错，不回退重新采样。

## 示例：当前两组重建

```bash
export DATAFLOW_PYTHON="$PWD/../no-edit-lastmile-val/.venv-curobo-v080/bin/python"
export MUJOCO_GL=egl
export MPLCONFIGDIR="$PWD/outputs/dependencies/mpl"
export WARP_CACHE_PATH="$PWD/outputs/dependencies/warp-cache"
# 先检查 GPU；以下 0 仅为示例。
bin/lastmile-dataflow collect-no-edit \
  --assets-dir /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/case_edits/no_edit_curobo_case1_v000_final_stations.json \
  --run-id "no-edit-final20-case1-v000-$(date +%Y%m%d-%H%M%S)" \
  --houses 103 --targets Cup_30 --gpu-ids 0 --workers 1
```

Case2 使用 `no_edit_curobo_case2_v001_final_stations.json`。Case1/Case2 各5份变体
均有对应配置，Case1 v004 是19点，其余为20点。每点5次试验，所以本次两组各100次
station trials；后续导航 rollout 可重复使用这些点，不会增加新的采样站位。

## 核验落盘

- run `frozen_config.json.augmented_input`：来源/hash/覆盖算法/selected IDs。
- scene `augmented_input/`：不可变输入副本；`scene/`：对应物理快照。
- task `station_input.json`：`lattice_regenerated=false`、`execution_point_count`。
- task `stations.json`：必须与源 `stations_final.json` 内容完全相同。
- task `progress.json`：点数、计划 trial 数；`trials.json` 中不得出现点集之外的 station ID。

“接入验证通过”不等于导航/抓取成功；正在运行的采集不能写成已完成。

## MolmoBot原生站位包

MolmoBot不能沿用cuRobo点集的初始化有效性结论，需对native快照全部原始候选点重新验证。
已提供 [MolmoBot专用重验与正式接入](molmobot-final-stations.md)，其结果复用同一 `augmented_variant`入口。
