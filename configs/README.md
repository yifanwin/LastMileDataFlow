# 配置使用说明

先配置资产和模型路径，再选择实验配方。**JSON 不支持注释，字段说明集中在各子目录 README。** 本目录说明覆盖现有 JSON 的全部字段；额外列出的可选字段会标注默认值。

## 首次运行需要手动配置

1. 在场景、机器人和策略配置中填写 `data_root`，确认场景、对象、机器人 XML、权重和 tokenizer 已准备好。
2. 在场景配方中修改 `houses`、`targets`、`cases` 和目标数/变体数，避免直接启动大规模构造。
3. 选择 cuRobo 或 MolmoBot 执行配置；MolmoBot 还要配置权重、tokenizer 和本仓库解释器。
4. 运行时显式传数据集、资产目录、输出目录、唯一 `--run-id` 和 `--gpu`。先查看 GPU 0–7 的利用率和空闲显存，不固定卡号。

## 配置入口

| 目录 | 用途与入口 | 说明 |
|---|---|---|
| [scene_datasets](scene_datasets/README.md) | `build-scenes --config` | 场景库配方；内含共用机器人配置 |
| [execution](execution/README.md) | `evaluate-stations / collect-lastmile --config` | 逐站位执行和标定 |
| [lastmile_datasets](lastmile_datasets/README.md) | `collect-lastmile --config` | S0→S1→操作的连续采集参数 |
| [policies](policies/README.md) | execution 的 `policy_config`；benchmark 的 `--policy-config` | MolmoBot 模型服务 |
| [robots](robots/README.md) | 机器人加载器；构造时由场景配方引用具体文件 | 机器人模型与控制；评测读取快照中的机器人配置 |
| [scene_eval](scene_eval/README.md) | `evaluate-stations --config` | 与 execution/MolmoBot 同参数的预设 |
| [collection](collection/README.md) | Python `load_config(CollectionConfig, ...)` | 记录配置示例；当前 CLI 不直接加载此 JSON |
| [case_edits](case_edits/README.md) | Python `load_obstacle_config` → `augment_obstacles` | 独立障碍物实验；不是额外 CLI 命令 |

## 路径规则

- `data_root` 相对**当前 worktree 仓库根目录**，也可用绝对路径。
- 数据字段相对 `data_root`：如 `dataset_dir`、`asset_library`、`model_path`、`checkpoint`、`tokenizer_path`、快照路径；collection 的 `output_dir` 也属于这一类。
- 配置/解释器字段相对**仓库根目录**：`robot_config`、`baseline_config`、`policy_config`、`python`。
- 不是相对当前 JSON 所在目录。各加载器只解析自己声明的字段，不会递归转换所有嵌套路径。
- CLI 的文件系统参数相对调用时工作目录；建议从仓库根目录运行。JSON 中的路径与 CLI 路径不要混淆。

## 最小构造示例

从仓库根目录执行，先按 [setup](../docs/setup.md) 准备环境和资产：

```bash
nvidia-smi
# 将 <空闲GPU> 换成实际编号，run-id 每次使用新名字
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/val103_cup30_minimal_v3.json \
  --output-dir outputs --run-id cup30-demo --gpu <空闲GPU>
```

评测会优先使用数据集保存的部分几何参数；覆盖规则见 [execution](execution/README.md#数据集参数优先级)。改变机器人控制、相机分辨率、策略或输入场景后，应重新生成匹配的标定。场景接受、执行结束、导航成功、操作成功分别判断，不把文件存在当任务成功。完整命令见 [运行手册](../docs/运行手册.md)。
