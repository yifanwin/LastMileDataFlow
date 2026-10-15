> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../../README.md)。

# 单episode超时隔离：执行总结（2026-10-12）

## 结论

已修复共享采集逻辑：**单个episode超时记录为独立的timeout，结束当前独立仿真并继续下一episode，不再使整个任务提前退出。** 适用于MolmoBot与cuRobo。此次修改代码及验证，没有重启或改写旧run。

## 已核实的两个历史run

| run尾部 | 采用点数 | 计划次数 | 旧已完成次数 | 停止episode |
|---|---:|---:|---:|---|
| final20-case2-v001-232238 | 20 | 100 | 11 | S0131 / trial=1，397步 |
| case2-v001-230626 | 58 | 290 | 14 | S0061 / trial=4，396步 |

两者最后一条均为`incomplete / attempt_wall_clock_budget`，旧循环直接break。只读历史证据。230626仍是58点旧版，不能当作最终20点运行。

## 修改

- 两个runtime将episode `TimeoutError`独立记为`timeout`，保存原reason、episode_timeout事件、实际步数和既有轨迹/媒体，物理任务结果仍为unknown。
- 共享collector不因该episode超时break，继续同站位下一trial和下一站位；每次尝试仍是独立Simulation，不在已begin仿真内恢复或传送。
- 调度和统计共用终结episode判定，续跑跳过已经记录的timeout；兼容旧`incomplete + attempt_wall_clock_budget`，不改写旧证据、不将interrupted认作超时。
- 超时单列timeout_trials / EpisodeTimeout；terminal_trials按已结束episode计数，同一station/trial终结记录去重。成功率明确为预算内成功数/已结束episode数，含timeout，并非把timeout改名为普通抓取失败。
- 新增独立timeout状态目录。复制返回row后添加trial序号，避免共享返回对象污染历史序号。
- 基础设施异常、用户中断、全局max_trials和scene预算仍保留整体停止语义。模型RPC超时不能在通信不同步时强行继续用可能错序的动作。
- 保持原300秒单次预算，不放宽物理或时间阈值。

## 验证

- 新增6项软件回归通过：两个后端的同点下一episode/下一站位继续、旧/新timeout续跑不重复、基础设施/中断仍停止、全局预算仍停止、超时计数及trial去重、独立状态目录。
- 全套322项：312通过、9跳过、1项既有失败。失败仍是MolmoBot用户配置半径0.792805608与测试预期2.0不一致，未修改用户配置。
- 专项日志、全套最终日志。compileall和git diff --check通过。
- 回归使用合成episode返回值，仅验证调度/统计契约；没有宣称新版真实VLA已经续跑或完成100/290次。
- 精确计数/原因表更适合这次逻辑修复，无新增RGB或视觉结论。

## 历史运行与续跑限制

两个历史run均已停止，代码更新不会自动恢复它们。它们冻结了旧代码摘要，直接以新代码`--resume`会被拒绝；需要新run或显式、可追溯的迁移续跑，不能绕过摘要校验或改写旧result/frozen_config。旧证据及其它工作区未改写，未Git提交/合并。

超时策略与统计口径
