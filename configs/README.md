# 配置使用说明

先配置资产和模型路径，再选择实验配方。**JSON 不支持注释，字段说明集中在各子目录 README。** 本目录说明覆盖现有 JSON 的全部字段；额外列出的可选字段会标注默认值。

## 首次运行需要手动配置

1. 复制 `configs/local.example.json` 为 `configs/local.json`，填写本机 `assets_dir` 和 `output_dir`。该文件已被 Git 忽略；CLI 显式参数优先。
2. 在场景、机器人和策略配置中填写 `data_root`，确认场景、对象、机器人 XML、权重和 tokenizer 已准备好。
3. 在场景配方中修改 `houses`、`targets`、`modes` 和目标数/变体数，避免直接启动大规模构造。
4. 选择 cuRobo、MolmoBot 或 π0.5 执行配置；VLA 还要配置权重、tokenizer 和本仓库解释器。
5. 运行时显式传数据集、唯一 `--run-id` 和 `--gpu`。先查看 GPU 0–7 的利用率和空闲显存，不固定卡号。

`local.json` 只支持 `assets_dir` 和 `output_dir`，路径相对仓库根目录，也可用绝对路径；不改变其他配置的 `data_root`。
文件不存在时默认输出到 `outputs`，标定和连续采集需显式传 `--assets-dir`。
`output_dir` 设置构建、标定、连续采集的默认输出根目录及 benchmark 评测日志根目录；benchmark 产物仍显式传 `--output`。
详见 [运行手册](../docs/运行手册.md#0-配置本机公共路径)。

当前配置与代码的默认基础 `seed` 统一为 `42`；trial、变体和重试仍按现有逻辑派生不同 seed。显式传入其他 seed 时仍使用指定值；已有采集结果和标定不改写。

## 配置入口

| 目录 | 用途与入口 | 说明 |
|---|---|---|
| [scene_datasets](scene_datasets/README.md) | 历史 Case JSON | 保留实验输入引用，已退出公开构建 |
| [task_modes](task_modes/README.md) | 新模式配方、共享阈值与三次标定配置 | 公开 build-scenes v2 八类候选；统一诊断及 train/val 下游消费 |
| [execution](execution/README.md) | `evaluate-stations / collect-lastmile --config` | 逐站位执行和标定 |
| [lastmile_datasets](lastmile_datasets/README.md) | `collect-lastmile --config` | S0→S1→操作的连续采集参数 |
| [policies](policies/README.md) | execution 的 `policy_config`；`evaluate-bench --policy-config` | MolmoBot / π0.5 模型服务 |
| [benchmark](benchmark/README.md) | `build-bench --config` | 恢复区域、路径、≤20% / ≥60% 阈值及评测预算 |
| [robots](robots/README.md) | 机器人加载器；构造时由场景配方引用具体文件 | 机器人模型与控制；评测读取快照中的机器人配置 |
| [scene_eval](scene_eval/README.md) | `evaluate-stations --config` | 与 execution/MolmoBot 同参数的预设 |
| [collection](collection/README.md) | Python `load_config(CollectionConfig, ...)` | 记录配置示例；当前 CLI 不直接加载此 JSON |
| [case_edits](case_edits/README.md) | 历史增强 JSON | 旧增强入口已退出 |

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
  --config configs/task_modes/panda_omron_task_modes.json \
  --output-dir outputs --run-id cup30-demo --gpu <空闲GPU>
```

评测会优先使用数据集保存的部分几何参数；覆盖规则见 [execution](execution/README.md#数据集参数优先级)。改变机器人控制、相机分辨率、策略或输入场景后，应重新生成匹配的标定。场景接受、执行结束、导航成功、操作成功分别判断，不把文件存在当任务成功。完整命令见 [运行手册](../docs/运行手册.md)。

## Panda 三次协议

新候选使用 `task_modes/panda_omron_task_modes.json` 构建，标定配置为 `task_modes/panda_omron_calibration.json`：每点三次、局部底盘范围 0.35 m、操作半径 2 m。几何阈值尚未经过新模式标定。

`execution/`、`lastmile_datasets/`、`benchmark/` 中的已有协议用于对应历史场景；新模式诊断与下游已通过软件回归，完整真实标定与闭环尚未验证。资源使用 CLI 的 `--gpu`、`--cpu-workers`、`--preparation-workers`、`--recording-mode`，不混入执行配置。构建使用 `--build-workers`。
