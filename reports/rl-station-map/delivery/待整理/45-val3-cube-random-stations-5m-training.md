# val_3 / Cup_12 原位置方块：500万条随机站位训练

- 状态：done
- 日期：2026-11-18
- 时间戳：2026-11-18T11:26:31.899824+08:00
- 替代：无
- 关联：[目标与初态检查](../analyze/44-cube-scene-target-selection.md)、[原800万条实验](37-random-stations-8m-training-launch.md)

## 配置与启动

用户指定 `val_3 / Cup_12` 任务、CUDA 0–3，并将预算明确为 5,000,000 条健康经验。原杯位于餐桌 `Dining_Table_203_1` 上，训练仍替换成 4 cm、0.1 kg 的 `RL_Cube`，保持原 XY 和物理支撑面；16 个有效正对目标站位，每回合随机选择，物理固定底盘，保留升降控制。

2026-11-18T11:26:31.899824+08:00：从零 SAC，seed 42，CUDA 0–3 每卡 128 个环境，总 512；Learner 与 Sampler0 共卡。预热 20,000 条、batch 512、CPU replay 100,000，每新增 128 条健康经验更新一次，完整预算预计 38,906 次更新。输入 69 维，动作 7 维；TensorBoard 每 30 秒汇总，每 400,000 条保存独立 checkpoint；不启用周期评估。当前 contact 协议训练入口不会自动进行结束后的完整独立抓取评估。

以原运行实际保存的 `input.yaml` 为基准，仅改 `scene.scene_xml`、`scene.scene_metadata`、`scene.target` 和 `training.max_transitions`。不复制当前 val103 离线评分 YAML 的缓存物体坐标。运行输入保存到新 run 的 `input.yaml`，未改源码或公开默认配置。

源码固定为当前提交 `d681bfa3541d5b6129cab36ad4f504a0d20c18e7`，`src/`、`scripts/`、`configs/` 和 `pyproject.toml` 均与提交一致；已有报告修改保留。该提交不同于原运行的 `3e3b4ec`：包含共享观测构建、可选移动底盘及监控更新；本次移动底盘关闭。运行期间不修改源码。

启动时 CUDA 0–3 利用率均为 0%、显存占用均为 0 MiB，每卡空闲 72,831 MiB。以原运行实测 8,000,000 条 / 7,531.6 s 粗估，本次约 78.5 分钟，场景吞吐可能不同，以启动后的实测为准。

- Run：`outputs/experiments/rl-cube-random-stations/runs/20261118-112631-val3-cup12-random-facing-5m-g0123`
- 主进程 / 进程组：`2139505`
- 命令：`.venv/bin/python -u scripts/station_map/rl/train.py --config outputs/experiments/rl-cube-random-stations/runs/20261118-112631-val3-cup12-random-facing-5m-g0123/input.yaml --gpu 0 1 2 3 --seed 42 --output outputs/experiments/rl-cube-random-stations/runs/20261118-112631-val3-cup12-random-facing-5m-g0123`
- 环境、绝对路径、原始工作区状态和进程信息：[launch.json](../../../outputs/experiments/rl-cube-random-stations/runs/20261118-112631-val3-cup12-random-facing-5m-g0123/launch.json)。

## 启动验证

2026-11-18T11:29:00.548696+08:00：主进程 2139505 与四个 Sampler（2141906–2141909）均存活，`nvidia-smi` 实测对应物理 GPU 0–3，各 Sampler 约 5,348 MiB，主进程另占 GPU0 约 1,172 MiB；四个 worker 均已贡献健康经验。实测 300,023 条健康经验、2,187 次 SAC 更新，已结束随机动作预热。checkpoint 确认输入 69 维、动作 7 维、预算 5,000,000、val_3/Cup_12、233 个候选 / 16 个有效站位；TensorBoard 文件已生成。核查保存在 run 内 `startup-verification.json`。

该时刻吞吐约 2,813.7 条/s，若保持此速度，剩余约 28 分钟；这只是启动期估计。已记录的 10 个早期回合均包含预热，其中 9 个标记为 infrastructure failure；日志存在 `LS_ITERATIONS` 达到默认 50 次的警告，代码把任何 overflow 或非有限物理状态标为 unhealthy。本次未细分每个异常回合的具体位，不能将所有异常逐个归因于同一条警告。异常经验不进入健康预算，需结合首批完整回合继续核查；此早期结束样本不能用作整批异常率。训练启动不代表 Actor 已学会抓取。

2026-11-18T11:30:27.829322+08:00：首批完整回合核查通过，累计 590,062 条健康经验、4,453 次更新，吞吐约 3,022.9 条/s；四个 worker 都有回合结束记录。已记录 517 个结束回合，其中 499 个健康完成，18 个 unhealthy 已排除；健康完成中仅 2 个不含预热，成功 0 次，样本尚不足以评价 Actor。已保存 `ckpts/step-000400116.pt`。异常回合未持续阻断采样与训练，保留原参数继续运行；按当前速度估计剩余约 24 分钟。详情见 run 内 `first-episodes-verification.json`。

## 完成

2026-11-18T12:06:58+08:00 回查：已于约 11:54 完成，进程退出；`sampling_complete` 记录 5,000,000 条健康经验、38,906 次更新、1,607.9 s（26.8 min）。`latest.pt` 确认 500 万 / 38,906 更新。末尾控制台 JSON 的 `updates=12884` 来自结束时重新加载的 `best.pt`，不能作为最终更新数；`best.pt` 为 1,669,212 条 / 12,884 更新，选中时最近 100 个健康非预热回合成功 97 次。

结果与 TensorBoard 命令见[训练结果汇总](46-val3-cube-5m-results.md)。中期学会较多抓取，后期成功率及无碰撞成功率回落；尚无独立固定 checkpoint 抓取评估或视频验证。
