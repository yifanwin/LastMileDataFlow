# 安装与数据准备

- 状态：active
- 日期：2026-10-22
- 关联：[运行手册](运行手册.md)、[π0.5 运行指南](../reports/pi05/docs/01-running.md)、[architecture.md](architecture.md)

从空白服务器配置可运行环境。数据统一放在大数据盘，不放进仓库。需要整体搬迁旧机器时见[附录 A](#附录-a从既有环境迁移)。

## 0. 路径约定

```bash
export REPO=/path/to/LastMileDataFlow
export DATA_ROOT=/data1/molmospaces_data   # 按实际数据盘调整
export GPU=<空闲卡号>                      # 按 nvidia-smi 选择
```

```text
$DATA_ROOT/
  assets/     # assets_dir：场景 XML/metadata、机器人配置、软链视图
  mujoco/     # 下载解压的资源实体
  models/     # MolmoBot、Qwen tokenizer、π0.5 权重
```

运行产物保持在仓库内 `$REPO/outputs/`，默认不放到数据盘。

## 1. 系统前置

- NVIDIA GPU，驱动支持 CUDA 12（验证版本 580.178.04）
- CUDA toolkit 12.x（cuRobo 编译）、`gcc`/`g++`/`make`
- EGL/OpenGL 运行库（`ldconfig -p | grep libEGL` 非空）
- Python 3.11、[uv](https://docs.astral.sh/uv/)、git
- 磁盘：环境约 7 GB；MolmoBot 19 GB；π0.5 约 54 GB；场景与资产按需

## 2. 安装环境

```bash
git clone https://github.com/yifanwin/LastMileDataFlow.git "$REPO"
cd "$REPO"
scripts/install.sh
.venv/bin/lastmile-dataflow --help

scripts/install_pi05.sh        # 需要 π0.5 时
scripts/install_pyrua.sh       # 需要 PyRUA Agent + RLDX-1 时
```

`install_pyrua.sh` 把 RPent（`886b3b2`）和 RLDX-1（`ebcfd13`）源码、两个纯 Python 依赖层、RLDX-1 权重（13.8 GB）与 RLDX-1-VLM 支持文件、干净的 Codex home 都放到 `data/`。PyRUA-Lean 源码按 `configs/policies/pyrua_panda_omron.json` 的 `pyrua_root` 读取，不安装。RLDX-1 服务使用项目解释器的 torch 2.7.1，只在服务进程前置 `data/rldx_site`（transformers 4.57 等），仿真进程不受影响；RLDX 上游声明 Python 3.10，这里在 3.11 上加载与推理已实测。HF 直连慢时可设 `HF_ENDPOINT=https://hf-mirror.com`，并去掉代理变量。Agent 的模型访问走 Codex 登录；`data/codex_home/auth.json` 链接到用户自己的 `~/.codex/auth.json`。

已验证版本：Python 3.11、mujoco 3.5.0、torch 2.7.1、torchvision 0.22.1、transformers 5.4.0、numpy 2.4.6、warp-lang 1.11.1、nvidia-curobo 0.8.0。

当前 Blackwell 服务器需选择 CUDA 12.8 构建；`pyproject.toml` 已将 torch/torchvision 固定到 `+cu128`，安装脚本使用官方 cu128 index。修复已有 cu126 环境可执行：

```bash
.venv/bin/python -m pip install 'torch==2.7.1+cu128' 'torchvision==0.22.1+cu128' \
  --index-url https://download.pytorch.org/whl/cu128
```

版本命令见 [PyTorch 官方安装说明](https://pytorch.org/get-started/previous-versions/)。2026-10-26 的真实 cuRobo smoke 已确认 cu126 报 no kernel image；cu128 安装、真实 CUDA 运算与完整规划验证已完成，见[标定交付](../reports/task-modes/delivery/02-calibration-delivery.md)。

`pyproject.toml` 未锁定全部依赖。严格复现时从已知可用环境导出 `pip freeze --exclude-editable`，在新环境 `install.sh` 后安装该清单。

## 3. 准备数据

默认准备以下版本：

| 类型 | source | 版本 |
|---|---|---|
| scenes | `procthor-10k-val` | `20251217_with_occupancy` |
| objects | `thor` | `20251117` |
| robots | `rby1m` | `20251224` |
| grasps | `droid` | `20251116` |

train/test 与 objaverse 场景按需追加，见各小节。

### 3.1 下载资源

```bash
uv venv --python 3.11 --seed .venv-download
.venv-download/bin/python -m pip install zstandard datasets huggingface-hub tqdm
curl -L -o download_assets.py \
  https://huggingface.co/datasets/allenai/molmospaces/resolve/main/download.py

for src in \
  mujoco/scenes/procthor-10k-val/20251217_with_occupancy \
  mujoco/objects/thor/20251117 \
  mujoco/robots/rby1m/20251224 \
  mujoco/grasps/droid/20251116
do
  .venv-download/bin/python download_assets.py "$DATA_ROOT" \
    --data_source_dir "$src" --yes
done
```

不要加 `--no-versioned`。按需追加（把完整 source 路径加入上面的 `for` 列表）：

- `mujoco/scenes/procthor-10k-train/20251122_with_occupancy`
- `mujoco/scenes/procthor-10k-test/20251121_with_occupancy`
- `mujoco/grasps/droid_objaverse/20251218`（`*objaverse*` 场景使用；`droid` 对应 Thor 资产名，二者按场景二选一）

建立 `assets/` 链接视图。代码按固定路径查找 `assets/scenes`、`assets/objects`、`assets/robots`、`assets/grasps`（例如 `assets_dir/robots/rby1m/curobo_config` 写在代码里），而下载结果是带 `mujoco/` 前缀和版本号的目录；链接视图负责稳定映射，不复制数据。注意场景目录必须是真实目录、内部文件用软链：`SceneSource.procthor()` 会解析真实路径并要求目录名以 `-train/-val/-test` 结尾，目录级软链会被解析成带版本号的名称。`objects`、`robots`、`grasps` 可以直接用目录软链。

```bash
CACHE=$DATA_ROOT/mujoco
ASSETS=$DATA_ROOT/assets

mkdir -p "$ASSETS"/{scenes,objects,robots,grasps}
mkdir -p "$ASSETS/scenes/procthor-10k-val"
cp -rs "$CACHE/scenes/procthor-10k-val/20251217_with_occupancy/." "$ASSETS/scenes/procthor-10k-val/"
ln -sfnT "$CACHE/objects/thor/20251117" "$ASSETS/objects/thor"
ln -sfnT "$CACHE/robots/rby1m/20251224" "$ASSETS/robots/rby1m"
ln -sfnT "$CACHE/grasps/droid/20251116" "$ASSETS/grasps/droid"
```

需要 train/test 时追加：

```bash
mkdir -p "$ASSETS/scenes/procthor-10k-train" "$ASSETS/scenes/procthor-10k-test"
cp -rs "$CACHE/scenes/procthor-10k-train/20251122_with_occupancy/." "$ASSETS/scenes/procthor-10k-train/"
cp -rs "$CACHE/scenes/procthor-10k-test/20251121_with_occupancy/." "$ASSETS/scenes/procthor-10k-test/"
```

校验：

```bash
CACHE=$DATA_ROOT/mujoco
ASSETS=$DATA_ROOT/assets
V=20251217_with_occupancy
find "$CACHE/scenes/procthor-10k-val/$V" -maxdepth 1 -name 'val_*.xml' | wc -l            # 1000
find "$CACHE/scenes/procthor-10k-val/$V" -maxdepth 1 -name 'val_*_metadata.json' | wc -l   # 1000
test -f "$CACHE/robots/rby1m/20251224/rby1_v1.2_site_control.xml"
find "$ASSETS" -xtype l | wc -l   # 0
```

批量模式中单个 source 失败也会以退出码 0 结束，必须检查输出里的 `Completed: N/N` 和上面的计数。

### 3.2 PandaOmron 整机资产

公开包不包含，需要从 robosuite factory 生成（约 52 MB，position 与 robocasa 共用）：

```bash
git clone https://github.com/ARISE-Initiative/robosuite.git "$DATA_ROOT/robosuite"
git -C "$DATA_ROOT/robosuite" checkout 5ce6643f

.venv/bin/python tests/smoke/panda_omron.py --stage factory \
  --factory-root "$DATA_ROOT/robosuite" \
  --asset-output "$DATA_ROOT/assets/robots/panda_omron" --gpu "$GPU"
```

输出目录必须不存在。校验 `model.xml` 存在、`meshes/` 有 71 个文件。生成后先跑 `--stage control`、`--stage align`，再标定；详见 [PandaOmron 运行指南](../reports/panda-omron/docs/panda-omron.md)。

### 3.3 模型权重

MolmoBot：

```bash
.venv/bin/hf download allenai/MolmoBot-RBY1Multitask \
  --local-dir "$DATA_ROOT/models/MolmoBot-RBY1Multitask"
.venv/bin/hf download Qwen/Qwen3-4B-Instruct-2507 \
  --include 'tokenizer*' vocab.json merges.txt config.json \
  --local-dir "$DATA_ROOT/models/Qwen3-tokenizer"
```

π0.5（`pi05_base` 需要 gcloud CLI 或 gsutil）：

```bash
gcloud storage cp -r gs://openpi-assets/checkpoints/pi05_base "$DATA_ROOT/models/"
curl -L https://storage.googleapis.com/big_vision/paligemma_tokenizer.model \
  -o "$DATA_ROOT/models/paligemma_tokenizer.model"
.venv/bin/hf download robocasa/robocasa365_checkpoints \
  --include 'pi05_pretrain_human300/**' \
  --local-dir "$DATA_ROOT/models"
```

RBY-1 的 π0.5 本体统计需由项目数据生成，见 [π0.5 运行指南](../reports/pi05/docs/01-running.md)。

## 4. 配置路径

```bash
cd "$REPO"
cp configs/local.example.json configs/local.json
```

```json
{
  "assets_dir": "/data1/molmospaces_data/assets",
  "output_dir": "outputs"
}
```

`configs/local.json` 不入库。再把各配置中的 `data_root` 指向数据盘：

```bash
grep -rl '"data_root": "data"' configs
```

逐个改为 `"$DATA_ROOT"` 的绝对路径（路径相对仓库根目录解析，与 JSON 所在位置无关）。π0.5 配置中的 checkpoint、tokenizer、norm_stats 也按实际位置填写。

## 5. 验证

```bash
MUJOCO_GL=egl MPLCONFIGDIR=/tmp/lastmile-mpl \
  .venv/bin/python -m unittest discover -s tests -t . -v

.venv/bin/python tests/smoke/multitask.py \
  --checkpoint "$DATA_ROOT/models/MolmoBot-RBY1Multitask" \
  --tokenizer "$DATA_ROOT/models/Qwen3-tokenizer" --gpu "$GPU" \
  --output "outputs/diagnostics/multitask-smoke"
```

小样本主链：

```bash
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/val103_cup30_minimal_v3.json --run-id small --gpu "$GPU"
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset "outputs/scene_datasets/small" --run-id stations --gpu "$GPU"
.venv/bin/lastmile-dataflow collect-lastmile \
  --dataset "outputs/scene_datasets/small" \
  --calibration "outputs/stations" --run-id lastmile --gpu "$GPU"
```

benchmark 见 `build-bench --help`、`evaluate-bench --help`。

## 6. 复现边界

- 固定版本时，场景包包含 XML、metadata、map、ceiling 和场景网格；`rby1m` 包含 native XML、cuRobo 配置与 URDF。
- 公开包不包含 PandaOmron 资产；已有 `outputs/`、标定与 benchmark 结果不会自动重建。
- MJB 恢复需要匹配生成时的 MuJoCo 版本。

## 附录 A：从既有环境迁移

迁移能保留已有结果，但要求旧环境路径可达。只迁 `procthor-10k-val` 时，可省略 train/test 与 objaverse 相关目录。

### A.1 迁移清单

| 旧路径 | 新位置 |
|---|---|
| 旧仓库旁 `molmospaces_data/assets` | `$DATA_ROOT/assets` |
| `/nas/wenyifan/molmospaces_data/cache`、`mujoco` | `$DATA_ROOT/cache`、`$DATA_ROOT/mujoco` |
| `/nas/wenyifan/models/*` | `$DATA_ROOT/models/` |
| 旧环境的 `data/assets/robots/panda_omron` | `$DATA_ROOT/assets/robots/panda_omron` |
| 旧仓库 `outputs/` | `$REPO/outputs` |

`assets/` 在旧机器上是链接视图，实体在 `/nas/.../cache` 与 `mujoco`。

### A.2 同步

```bash
mkdir -p "$DATA_ROOT"
rsync -aH --info=progress2 /data0/wenyifan/MoMaTrajGen/molmospaces_data/assets "$DATA_ROOT"/
rsync -aH --info=progress2 \
  /nas/wenyifan/molmospaces_data/cache \
  /nas/wenyifan/molmospaces_data/mujoco \
  "$DATA_ROOT"/
rsync -aH --info=progress2 <旧环境>/data/assets/robots/panda_omron/ \
  "$DATA_ROOT/assets/robots/panda_omron/"
```

NAS 小文件较慢，完成后用 `rsync --dry-run` 复核。

### A.3 重写软链

旧 `assets/` 下大量软链使用绝对路径指向 `/nas/wenyifan/molmospaces_data`。若新数据盘不保持该挂载点，重写：

```bash
OLD=/nas/wenyifan/molmospaces_data
NEW="$DATA_ROOT"
find "$NEW" -type l -lname "$OLD/*" -print0 |
while IFS= read -r -d '' link; do
  target=$(readlink "$link")
  ln -sfn "$NEW${target#"$OLD"}" "$link"
done
find "$NEW" -xtype l | wc -l   # 0
```

### A.4 迁移运行结果

```bash
rsync -aH --info=progress2 <旧环境>/LastMileDataFlow/outputs/ "$REPO/outputs/"
```

已有 run 配置保存了旧绝对路径，不会自动重定位。需要续跑时优先从干净的 dataset/calibration 重新发起；必须复用旧 run 时，按旧路径批量替换 `outputs/**/*.json` 中的输入路径。
