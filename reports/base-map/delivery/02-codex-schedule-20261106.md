# 轨迹级质量评分单次定时任务部署

- 状态：done（定时进程已结束；方案整体未完成，见执行结果）
- 日期：2026-11-06
- 替代：无
- 关联：[轨迹评分需求](../requirement/02-feasible-trajectory-quality.md)、[实施方案](../plan/02-feasible-trajectory-quality.md)

## 部署结果

在服务器 `phyagentos-dev-pro5000` 上，以用户 `wenyifan` 的 crontab 部署一次开发任务。按用户最新要求，计划启动时间改为 **2026-11-06 02:57:00 Asia/Shanghai（UTC+08:00）**，对应 UTC 2026-11-05 18:57。初次部署约为北京时间 00:54，约 01:04 曾改为 03:30，约 01:34 曾改为 02:55，约 01:38 改为当前的 02:57；未提前开发或运行项目测试。

工作目录：`/home/wenyifan/wenyifan/lmdf/LastMileDataFlow/.worktrees/base-map`，实际解析为 `/data1/wenyifan/lmdf/LastMileDataFlow/.worktrees/base-map`。

任务启动后读取工作区最新有效需求、方案和接口核对，完成轨迹级质量评分实现、相关测试及方案要求的验证，并输出交付报告。保留工作区现有修改和未跟踪方案，不修改 main 工作区源码。

北京时间约 01:32，按用户要求将提示词明确为一次 Codex 会话完成最新方案全部必需实施与验收，当前为 P0–P6：接口 GPU 实测、算法与记录、多候选与热启动、独立验证与评分、主链图表与全套回归、小规模真实规划及对照，最后完成 val103/Cup_30 目标场景全图和交付报告。阶段通过后自主继续；遇到实际错误先修复再验证，不因已完成部分阶段或耗时较长而停止，也不能仅启动后台实验就宣告完成。只有无法自行解决的外部阻塞才如实报告未完成项。本次只更新部署指令，未执行开发；最新回读确认 02:57 调度在队列，模型为 gpt-6.1-sol、思考强度 high，启动器未设置执行时长上限。

未安装 `at`，因此采用用户级 cron。服务器系统时区为 Asia/Shanghai，cron 服务为 active/running。已安装并回读确认恰好一条对应调度：

```cron
57 2 6 11 * /bin/bash /home/wenyifan/.local/state/codex-jobs/trajectory-quality-20261106-0300/run.sh >> /home/wenyifan/.local/state/codex-jobs/trajectory-quality-20261106-0300/run.log 2>&1 # codex-trajectory-quality-20261106-0300
```

cron 没有年份字段，启动器额外限定北京时间 `202611060257`，以启动标记阻止重复执行，启动时删除自身条目并保留其他用户调度。开发失败也不会自动重试。改期后保留原任务目录与标识（名称中的 `0300` 不再表示当前启动时间）；日志路径和状态检查命令不变。

## 认证与无人值守检查

- Codex 路径：`/home/wenyifan/.local/bin/codex`；版本：`codex-cli 0.164.0`。
- 按用户补充要求，启动器显式传入 `--model gpt-6.1-sol -c 'model_reasoning_effort="high"'`，固定模型及高思考强度，不依赖届时用户配置的默认值。改期至 02:57 后，启动器语法检查通过，回读确认队列中恰好一条对应调度，状态仍为 scheduled。
- `codex login status` 返回 `Logged in using ChatGPT`；未读取或复制认证令牌。
- 最小 cron 环境未加载代理时，探测超时退出（124）；加载当前代理变量后，无终端、标准输入关闭的只读探测成功返回 `CODEX_NONINTERACTIVE_OK`，退出码为 0。
- 使用正式任务同样的 `workspace-write` 和 `approval_policy="never"` 设置，独立 shell 探测成功执行临时目录内的 `touch` 并返回 `CODEX_SHELL_OK`，退出码为 0。探测没有读取项目或开展开发。
- 代理变量保存在任务目录的 `runtime.env`，权限为 0600；任务目录权限为 0700。正式任务使用已有用户认证及配置、新建独立 Codex CLI 进程，显式设置无人值守审批策略；保留工作区写入沙箱。
- 已通过启动器 Bash 语法检查。未启动正式开发任务。

## 路径与检查命令

全部调度文件位于 `/home/wenyifan/.local/state/codex-jobs/trajectory-quality-20261106-0300/`：

| 文件 | 用途 |
|---|---|
| `run.sh`、`prompt.txt` | 启动器及开发指令 |
| `status.txt` | 当前为 scheduled；启动后记录 running、PID、实际开始时间；结束后记录 finished/failed、结束时间及退出码 |
| `run.log` | 正式运行的 stdout/stderr 日志，目前为空 |
| `final.md` | Codex 任务结束时生成的最终答复 |
| `probe-network.log`、`probe-shell.log` | 成功的认证及 shell 执行探测记录 |
| `probe.log` | 未加载代理时的失败探测记录 |

```bash
# 启动前查看队列；启动后该条目会自动移除
crontab -l | grep -F codex-trajectory-quality-20261106-0300

# 任务状态与实际运行日志
job_dir=/home/wenyifan/.local/state/codex-jobs/trajectory-quality-20261106-0300
sed -n '1,12p' "$job_dir/status.txt"
tail -n 80 "$job_dir/run.log"

# 跟踪日志
tail -f "$job_dir/run.log"
```

## 实际执行结果（10:05 检查）

任务已于北京时间 **2026-11-06 02:57:01** 启动，**03:24:13** 结束，运行 27 分 12 秒。包装进程 PID 为 1358834，退出码为 0；该退出码仅表示 Codex CLI 正常结束，不代表方案全部完成。调度条目已自动移除，检查时没有该任务的运行进程。

任务报告已实现轨迹评分链路并更新文档，最终离线回归 428 项：426 通过、2 跳过。**方案整体未完成**：运行环境未暴露可用 CUDA 设备，真实入口报 `V2 requires CUDA`，P0 GPU 验收、P5 对照及 P6 全图未完成；Git 元数据只读，修改未提交。实际改动、测试记录及外部阻塞见[实施交付报告](03-trajectory-quality-implementation.md)。

运行状态与结果分别保存在任务目录的 `status.txt`、`run.log` 和 `final.md`。前文“未启动”“日志为空”等描述为部署时的历史检查结果，以本节及实施交付报告为当前状态。
