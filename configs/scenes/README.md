# 场景构建配方

当前入口使用 scene-recipe-v1，示例为 panda_omron.json。

```bash
PYTHONPATH=src .venv/bin/python scripts/build_scenes.py --config configs/scenes/panda_omron.json --output <新目录> --gpu 0
```

配置显式填写 data_root、dataset_dir、assets_dir、asset_index、robot_config 和 houses。路径规则见 configs/README.md。
每个任务生成一份原场景和一份编辑场景。edits.tabletop_count 默认 [1, 3]，floor_count 默认 [0, 2]，tabletop_radius_m 默认 0.3；桌面障碍放在目标所在支撑面，地面障碍放在目标与站位圈之间的可支撑地面。
edits.enabled=false 仅生成原场景。max_proposals 和 variant_timeout_s 控制编辑候选预算；task_timeout_s 控制单任务总预算。build_workers 控制房屋并行数。
其余旧 Case JSON 仅供历史结果查阅，不能作为当前构建输入。
