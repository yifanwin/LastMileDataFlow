> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../README.md)。

## 运行指南

### 1. 进入目录、设置环境

```bash
cd /home/wenyifan/wenyifan/MoMaTrajGen/LastMileDataFlow/.worktrees/no-edit-lastmile-val

export DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python
export MUJOCO_GL=egl
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

### 2. 运行 val_103 的 Cup_30，使用 CUDA 5

```bash
RUN_ID="molmobot-val103-cup30-$(date +%Y%m%d-%H%M%S)"
mkdir -p outputs/logs

bin/lastmile-dataflow collect-no-edit \
  --assets-dir /home/wenyifan/wenyifan/MoMaTrajGen/molmospaces_data/assets \
  --config configs/no_edit/molmobot.json \
  --houses 103 \
  --targets Cup_30 \
  --gpu-ids 5 \
  --workers 1 \
  --run-id "$RUN_ID" \
  > "outputs/logs/$RUN_ID.log" 2>&1
```

建议在 `tmux` 中运行。移除 `--targets Cup_30` 即运行该场景全部支持任务。

### 3. 查看进度

```bash
tail -f "outputs/logs/$RUN_ID-val-103-worker.log"

bin/lastmile-dataflow no-edit-status "outputs/no_edit/$RUN_ID"
```

### 常用配置

修改 `configs/no_edit/molmobot.json`：

| 参数 | 当前值 |
|---|---:|
| `radius_m`：采样半径 | 约 0.793 m |
| `spacing_m`：采样间距 | 约 0.111 m |
| `trials_per_station`：每位置次数 | 5 |
| `attempt_timeout_s`：单次超时 | 300 秒 |
| `width` / `height`：图像尺寸 | 1024 × 576 |

要跑 **2 m**，将 `radius_m` 改为 `2.0`。

### 输出与续跑

- 数据、视频、热图：`outputs/no_edit/$RUN_ID/`
- 日志：`outputs/logs/$RUN_ID*.log`
- 续跑：原命令使用同一 `RUN_ID`，追加 `--resume`。
- 修改配置后使用新的 `RUN_ID`，不要覆盖旧结果。
### 连续导航 + VLA 试验的错误处理

`scripts/run_molmobot_station_transfer.py` 保留每轮原始结果：局部初始化、执行、录制错误或超时可继续下一轮；
共享基础设施错误（`infrastructure_error` / `failure_scope=shared`）保存当前结果和摘要后停止，不复用失效模型连接。
缺失导航证据时，摘要显示 `unknown`，不计为导航成功；提前停止由 `stopped_early` 和 `stop_reason` 标记。
