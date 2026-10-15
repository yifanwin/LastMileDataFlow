> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../../README.md)。

# E-final-station-integration：执行前计划

- 问题：增强已生成最多20个覆盖点，但 no_edit 原先只复制场景又重新生成161点，没有接入最终点。
- 干预：新增可选 augmented_variant 输入，将原生快照、station_selection、stations_final、原始 task 绑定进入正式 collector。
- 不变项：物体/位姿、机器人、采样半径/spacing、目标、5 trials/点、原有 cuRobo 配置。复用已经通过物理构建/全量初始化的冻结变体，不重新随机构建障碍物。
- 验收预期：Case1 v000 / Case2 v001 各20点，与来源JSON逐项相等；各100次计划 trials；实际 trials/attempt IDs 全部属于最终点集。不得静默回退、不得[:20]、不得污染旧run。
- 覆盖方法：目标距离近远极值、8方位×2距离层代表、归一化位置/方位/距离上的最远点填充。非严格等间距；不能在没有有效点的障碍区强求均匀。
- 回归：旧raw采集配置不指定增强输入时兼容。损坏/错机器人/错场景/错目标/错selection/旧cache必须报错。
- 证据边界：单元fixture仅验证输入契约；真实原生运行另记录。正在运行不得写作导航/抓取成功。旧运行/源变体保留、不改写，不停止无关用户进程。
- 保留：源诊断run只读；新outputs/no_edit run、outputs/logs日志/PID、配置、输入hash/紧凑验收表。无Git提交/合并。
