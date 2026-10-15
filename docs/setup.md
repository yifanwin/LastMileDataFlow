# 安装与数据准备

## 1. 环境依赖

需要 Python 3.11、Git 和 uv。uv 用于创建环境，依赖安装使用环境自身的 pip：

```bash
scripts/install.sh

.venv/bin/lastmile-dataflow --help
```


## 2. 数据目录与路径规则

- 场景、资产、快照、权重、tokenizer 等数据路径相对于 `data_root`。
- Python 解释器、其他配置文件等代码/配置路径相对于 repo root，也可直接写绝对路径；与当前 JSON 所在目录无关。

```json
{
  "data_root": "data",
  "checkpoint": "models/MolmoBot-RBY1Multitask",
  "tokenizer_path": "models/Qwen3-tokenizer",
  "python": ".venv/bin/python"
}
```

加载后的运行配置保存实际解析出的路径，不再自动改写成相对于 run 目录的引用。数据整体搬迁后更新用户配置的 `data_root`；已有运行配置中的绝对输入路径需要显式更新。CLI 文件系统参数仍按调用者给出的路径使用，跨目录调用时用绝对路径。

预期布局：

```text
data/
  assets/
    scenes/procthor-10k-val/val_103.xml, val_103_metadata.json, ...
    objects/thor/...
    robots/rby1m/robot.xml, meshes, curobo_config/...
    grasps/droid/<asset>/<asset>_grasps_filtered.npz
  models/MolmoBot-RBY1Multitask/config.yaml, model.pt
  models/Qwen3-tokenizer/...
```

该树展示本项目需要的文件，不表示官方压缩包解压后自动得到所有这些名字。修改 `configs/robots/rby1.json` 和 `configs/scene_datasets/rby1_position_head320.json` 中的 `model_path` 以匹配实际模型。

## 3. 场景、对象、机器人与抓取候选

公开来源为 [Ai2 MolmoSpaces 数据](https://huggingface.co/datasets/allenai/molmospaces)，包含场景、对象、机器人和 grasps。其数据卡说明了不同子集的授权；下载前核对具体子集的许可与访问条件。不要默认下载全库。[数据卡与下载说明](https://huggingface.co/datasets/allenai/molmospaces/blob/main/README.md)。

使用上游独立下载脚本，不需要把 MolmoSpaces Python 仿真包装进基础环境：

```bash
uv venv --python 3.11 --seed .venv-download
.venv-download/bin/python -m pip install zstandard datasets huggingface-hub tqdm
mkdir -p data
curl -L -o data/download_assets.py \
  https://huggingface.co/datasets/allenai/molmospaces/resolve/main/download.py
.venv-download/bin/python data/download_assets.py data/downloaded --source mujoco --list
```

根据列出的实际版本选单个 source：

```bash
.venv-download/bin/python data/download_assets.py data/downloaded \
  --data_source_dir <listed-source> --no-versioned
```

脚本会显示容量并请求确认。不要省略 source 后直接下载全部。解压后按实际目录调整 `dataset_dir/asset_library/model_path`，保留 XML 的相对 mesh/texture/include 引用。[上游脚本参数](https://huggingface.co/datasets/allenai/molmospaces/blob/main/download.py)。

本仓库读取 MuJoCo XML 和匹配的 `val_<id>_metadata.json`，不是原始 ProcTHOR JSON；没有实现原始 ProcTHOR 导出器。新下载的资产版本是否提供这一 metadata 命名需实际核对，不能用 `val_<id>.json` 冒充。

**尚未公开确认的配套输入：** 当前适配器使用的 `rby1_v1.2_site_control.xml`、`curobo_config/rby1m_holobase*.yml` 与 `urdf/model_holobase.urdf` 是否随公开机器人包提供，尚未验证。

抓取候选读取 `grasps/droid` 或 `droid_objaverse` 下的 `transforms` 数组。缺少候选时现有实现会生成包围盒 pinch 候选；这不是实测成功保证。首轮应选已有匹配候选的目标。

## 4. MolmoBot multitask 后端

推理代码已在 `policies/multitask/`

单独准备本地完整权重和 tokenizer。安装脚本不自动下载大型模型：

```bash
.venv/bin/hf download allenai/MolmoBot-RBY1Multitask \
  --local-dir data/models/MolmoBot-RBY1Multitask
.venv/bin/hf download Qwen/Qwen3-4B-Instruct-2507 \
  --include 'tokenizer*' vocab.json merges.txt config.json \
  --local-dir data/models/Qwen3-tokenizer
```

调整 `configs/policies/molmobot.json` 的 checkpoint/tokenizer_path/python；数据路径相对 data_root，解释器路径相对 repo root。模型是 [RBY1Multitask](https://huggingface.co/allenai/MolmoBot-RBY1Multitask)，不是任意 MolmoBot 权重；当前后端只支持其单卡 SDPA/SigLIP、单次观测推理。

只做模型 smoke，不执行机器人：

```bash
.venv/bin/python tests/smoke/multitask.py \
  --checkpoint data/models/MolmoBot-RBY1Multitask \
  --tokenizer data/models/Qwen3-tokenizer --gpu "$GPU" \
  --output outputs/diagnostics/multitask-smoke
```

它检查本地权重严格加载、pick/open 真实动作输出及现有 service/client 通信；合成观测不代表真实任务成功。

## 5. 单卡小样本入口

从仓库根目录运行：

```bash
GPU=<chosen-index>
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/val103_cup30_minimal_v3.json --run-id small --gpu "$GPU"
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/small --assets-dir data/assets \
  --run-id stations --gpu "$GPU"
.venv/bin/lastmile-dataflow collect-lastmile \
  --dataset outputs/scene_datasets/small --assets-dir data/assets \
  --calibration outputs/stations --run-id lastmile --gpu "$GPU"
```

benchmark 的两条命令见 `lastmile-dataflow build-bench --help` 和 `evaluate-bench --help`。MolmoBot 站位标定必须使用 native robot 控制场景和对应相机，不能复用不兼容的 position 标定。

移动数据目录后显式更新 data_root；已有运行配置不自动重定位。MJB 可保存已编译场景和初态，但重建/native 模型转换仍需机器人资产。