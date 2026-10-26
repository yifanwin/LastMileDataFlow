# 任务模式配置

1. 生成离线索引，每次使用新输出目录：

   ```bash
   LD_LIBRARY_PATH=/data1/wenyifan/opt/lib:${LD_LIBRARY_PATH:-} \
   .venv/bin/python scripts/build_asset_index.py \
     --source-root /data1/wenyifan/lmdf/molmospaces_data/assets/objects/thor \
     --output outputs/asset_index/my-thor-index
   ```

2. 修改 `panda_omron_task_modes.json` 的 `data_root`、`assets_dir`、`asset_index`、`houses`。assets_dir 显式指向含 grasps 的资产根目录。数据路径相对 data_root，配置路径相对仓库根目录；索引也可填绝对路径。当前文件引用已生成的 r02 索引。
3. `mode_defaults.json` 统一保存构造预算、诊断预算和几何阈值。本批次固定沿用初版阈值，实际对照与未验证边界见标定交付。
4. `panda_omron_calibration.json` 使用相同 mode_config、三次 trial、可移动底盘和 Pick/Open，操作半径为 2 m，局部底盘移动范围为 0.35 m。

新配方由 `load_task_mode_recipe()` 解析，schema 为 `scene-dataset-recipe-v2`，不接受旧 Case 配置。来源 house 的 train/val 划分优先读取 `house_splits`；未填写时在完整 houses 计划上按 split_seed 一次生成。保存的有效配方包含完整 house_splits；子集运行必须继承该映射。

5. 从仓库根目录执行构建：

   ```bash
   .venv/bin/lastmile-dataflow build-scenes \
     --config configs/task_modes/panda_omron_task_modes.json \
     --output-dir outputs --run-id modes-001 --gpu <空闲GPU>
   ```

公开入口已接入任务共享准备、父进程统一写记录和八类候选构造器。`execution_config` 可显式填写，默认使用 `configs/task_modes/panda_omron_calibration.json`；其中的移动底盘与 grasp 参数供名义通道代理使用。每变体最多 32 提案、3 个物理原候选、每个一次 repair；未进入物理候选前不加载真实资产。未知或失败保留真实原因。

`evaluate-stations` 已接入八类诊断、原 trial 引用和有界 IK，输出 diagnosis.jsonl；多组真实完整标定已完成，六类有最终标签正例，Orientation/Visibility 无正例。用户要求本轮止于标定，后续任务暂不推进。当前 build accepted 及 proposal_intent 均不表示已取得困难起点或模式标签。执行进度见[计划表](../../reports/task-modes/plan/02-build-schedule.md)。

索引唯一查询文件为 `assets.jsonl`，`geometry/` 保存适配后的派生 XML，源文件只读。loadable 表示能进入当前 rigid/free-root 障碍物编辑链；不支持的资产保留原因。完整索引不代表所有资产已有物理放置正例。
