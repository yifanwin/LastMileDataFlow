# 配置

所有阶段参数使用显式 JSON。从仓库根目录运行，先修改示例的数据和模型路径。

| 目录 | 用途 |
|---|---|
| scene_datasets | SceneRecipe：房屋、目标筛选、障碍数量、站位与构建进程 |
| collection | StationRun：执行配置路径、资产目录、进程数、记录模式与预算 |
| execution | ExecutionConfig：物理执行、规划、导航和成功率阈值 |
| benchmark | BenchmarkConfig：困难起点、恢复区域与评测预算 |
| policies | MolmoBot/π0.5 权重、tokenizer、解释器与服务参数 |
| robots | Panda/RBY-1 模型、控制profile、相机与初始姿态 |
| rl | 独立 RL 研究配置，本次逻辑不改 |

数据输入配置显式填写 data_root；数据/权重/tokenizer 路径相对 data_root，配置/解释器路径相对仓库根目录，也可绝对指定。不是相对 JSON 所在目录。CLI 路径按当前调用目录解释。

local.example.json 可复制为 local.json，提供 assets_dir/output_dir；阶段脚本的 --output 始终显式填写。
已删除旧 Case、八模式、scene_eval 和重复 lastmile_datasets 配置，不保留旧路径兼容层。既有运行配置保存在原 run 中，不改写历史结果。

运行步骤见 [运行手册](../docs/运行手册.md)，运行参数见 [collection](collection/README.md)。
