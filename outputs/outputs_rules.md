# outputs 目录规则

- 状态：active
- 日期：2026-10-28
- 适用：本仓库及各 worktree 的 `outputs/`。只有本文件受 git 跟踪，其余运行产物都不入 git。硬规则摘要见仓库根目录的 `AGENTS.md`，本文是细则。

本文只规定运行产物放在哪、怎么命名、怎么搬迁和删除。实验结论写在 `reports/`。

## 1. 顶层

```text
outputs/
├── README.md            本地索引（不入 git）：每个 campaign、实验主题各一行；末尾记录搬迁与删除
├── outputs_rules.md     本规则（入 git）
├── campaigns/<名称>/    按配方批量运行（全量、批次标定、采集、评测）
└── experiments/
    ├── <主题>/          少数「场景 + 目标物体」上的复现、定位、A/B 对照，以及读 campaign 数据做的分析
    └── scratch/<YYYYMMDD>-<说明>/   临时试跑、冒烟测试、调参
```

顶层不放其他目录或文件。campaign 和实验主题统称「单元」：每个单元回答一个问题，自带说明，能单独看懂。

## 2. 单元内部

campaign 和实验主题用同一套子目录名，用到才建：

```text
<单元>/
├── README.md     用途、状态（见第 6 节）、代码提交号、启动命令、运行中改动、结论所在的报告
├── src/          运行用的代码（可选，见第 3 节）
├── state.json    调度状态（仅 campaign）
├── inputs/       冻结输入
├── runs/         原始结果。campaign 每 10 个 run 一组（c000-c009/）；实验按对照组分（a/、b51/），只有一组时省掉这一层
├── replays/      回放视频（每个 run 一个子目录）及视频清单
├── views/        按任务分类的符号链接视图，可随时重建，不算数据
├── analysis/     派生统计与分析结果（JSON、表、图）
└── logs/         日志、启动脚本、搬迁映射表
```

- 一个子目录只放一类东西，不交叉存放。
- 读某个 campaign 的数据去回答新问题时，结果放进提出该问题的实验主题的 `analysis/`，不写进那个 campaign。

## 3. 代码版本

单元往往要跑很久，每个 chunk 都会新起子进程，并从磁盘重新导入代码；运行期间有人改代码，后面的 run 就会悄悄换成新版本。因此：

1. README 记录代码对应的提交号，以及开跑时工作区是否干净。以提交号为准，不以拷贝的文件为准。
2. 运行期间代码不变，按以下优先顺序选一种：
   - 先提交，再用 `git archive <commit>` 把 `src`、`scripts`、`configs` 导出到 `<单元>/src/`，从这里启动；
   - 从一个固定在该提交上的 worktree 启动，README 写明路径；
   - 来不及提交时，拷贝当前工作区到 `src/`，同时把 `git diff` 存为 `src/working-tree.diff`，README 注明「未提交」。
3. 运行中替换了任何文件，都要在 README 的「运行中改动」一节记下：时间、文件、原因、提交号、从哪个 run 开始生效。改动涉及规划、执行或判定代码时，开新单元，不在原单元里续跑。

## 4. 命名

| 对象 | 格式 | 例 |
|---|---|---|
| campaign | `<主题>-<范围>-<YYYYMMDD>` | `no-edit-full-20261028` |
| campaign 内的 run | `<campaign>-c<NNN>` | `no-edit-full-20261028-c002` |
| 实验主题 | 描述问题的名词短语 | `column-spheres` |
| 实验 run | `<编号><对照组>-<对象>-<条件>-<YYYYMMDD>` | `n5h-val100-s0000-iso-20261028` |
| 分析结果 | `<对象>-<YYYYMMDD>` | `near-stations-val1-bowl-20261028` |
| 临时运行 | `<YYYYMMDD>-<说明>` | `20261028-egl-smoke` |

只用小写字母、数字和连字符；同一天有多次时追加 `-HHMM`。不用 `_` 前缀；名称不复用。

## 5. 数量

每个目录的直接子目录不超过 25 个。超过时，按序号或对照组分组，不靠改名缩短来凑数。文件数量不限。

## 6. 状态：只改 README，不搬目录

| 状态 | 含义 |
|---|---|
| `active` | 正在运行 |
| `done` | 已完成 |
| `baseline` | 已被新版本替代，但保留作对照 |
| `superseded` | 已被替代、不再引用，可作为删除候选 |

状态写在单元的 README 和 `outputs/README.md` 索引里。没有 `archive/` 目录，单元结束后原地保留。

## 7. 路径与搬迁

记录里的绝对路径包含 worktree 目录名，所以移动单元、改名 worktree 都会让这些路径失效。原则是**尽量不搬**。

- 有进程在读写时，不移动、改名或删除该单元、它所在的 worktree，以及 `outputs/`。
- 删除 worktree 前，先把它的 `outputs/` 迁走。
- 确需搬迁时（整理旧结构、迁出 worktree），按以下步骤：
  1. 停止相关进程，确认没有其他会话在操作同一个 worktree；
  2. 先演练：列出全部「旧 → 新」路径，检查目标不存在、每层不超过 25 个子目录；
  3. 执行 `mv`，把映射表存为 `<单元>/logs/reorg-<YYYYMMDD>.json`；
  4. 按映射表改写 JSON、JSONL、YAML、sh 里的路径，只替换完整的路径段；报告只改本主题的；
  5. 抽查：`config.json` 里的 `dataset`、`video.json` 里的 `rollout`、分析结果里的绝对路径都能解析到真实文件；
  6. 重建 `views/`，并在 `outputs/README.md` 记录这次搬迁。

## 8. 删除

须经用户确认，并在 `outputs/README.md` 记录删了什么、为什么删。

- **可删**：中止的、不计入任何统计的 run 及其专用输入；状态为 `superseded` 的单元；超过 7 天的 `scratch/`；可以重新生成的东西（`views/`、缓存、`__pycache__`）。
- **不删**：被报告引用的结果；`baseline` 单元；已有 run 依赖的冻结输入（`config.json` 里 `dataset` 指向的目录）。
