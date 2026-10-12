# MolmoBot VLA 最终站位正式接入：执行报告

## 结果

**代码已接入，两组MolmoBot原生全量站位重验完成，正式VLA运行的最终点集落盘及输入审计通过。**
截至审计时，两组模型服务正在加载，尚无新VLA trial完成；不宣称机器人动作已执行或抓取成功。

| 输入 | 原始候选数 | native实际通过 | 最终点数 | 计划trial数 | 模型状态 |
|---|---:|---:|---:|---:|---|
| Case1 v000 | 161 | 24 | 20 | 100 | loading |
| Case2 v001 | 161 | 58 | 20 | 100 | loading |

两组各161行均有独立Simulation/MjData与真实初始化/可见性记录，共322次原生站位检查。
点集并非从cuRobo复制其有效性结论：native motor、10Hz/4ms、1024×576配置下重新验证，
然后采用距离近远极值、8方位×2距离层代表和位置/方位/距离联合最远点覆盖压缩。
最多20点；是合法区域内的覆盖分散，不保证严格等间距。

## 与之前提交、运行的关系

`4356538`中的 `rebuild_augmented_native.py` 继续作为上游profile移植工具。
本次使用之前两组MolmoBot run中**不可变的native初始scene**，不是运行中某一帧，也不是旧stations/trials缓存。
原始native快照文件hash、源变体、asset/pose、scene source都绑定入新包。
旧run保留；原本24/58点的试验不会改写成20点，也不会并入新结果。

## 代码与执行链路

1. 新增 `workflows/native_station_input.py` 与 `scripts/revalidate_native_stations.py`：验证native移植来源及全量lattice身份，检查原生快照有限值、完整高度碰撞、支撑和Case条件，对全部原始点重新验证，再覆盖压缩20点。
2. 两个新包：`outputs/diagnostics/molmobot-final-stations-case1-v000-20261011-r1` 与Case2 v001。包含全部161行的通过/失败原因、最终点、selection及native来源hash。
3. 沿用 `augmented_variant` 正式入口，扩展native重验来源/hash/config/count检查；模型及初始物理场景与点集共同绑定，错profile/错配置/错来源/错selection拒绝。
4. 将点集正式绑定提前到VLA模型加载之前：task `stations.json`、`station_input.json` 和20点/100次 `progress.json` 已落盘。模型状态单独记录 `model_service_status.json`，避免把模型尚未就绪误报为实际执行完成。
5. VLA `run_vla_attempt` 保持原来的原生动作/控制/模型接口，接收冻结的已通过station initial；不重新生成lattice，不补点、不前20截断。所有trial/attempt必须在final IDs内。
6. 新配置：`configs/case_edits/no_edit_molmobot_case1_v000_final_stations.json` / Case2 v001。新run-id，各20点×5次，GPU1/4；保留模型与400步horizon及300秒attempt预算，不调整阈值。

## 证据与重现

- Case1：[新VLA运行](../../../outputs/no_edit/no-edit-molmobot-final20-case1-v000-20261011-232238)；[模型日志](../../../outputs/logs/no-edit-molmobot-final20-case1-v000-20261011-232238-val-103-model.log)；[接入审计](case1-audit.json)。
- Case2：[新VLA运行](../../../outputs/no_edit/no-edit-molmobot-final20-case2-v001-20261011-232238)；[模型日志](../../../outputs/logs/no-edit-molmobot-final20-case2-v001-20261011-232238-val-103-model.log)；[接入审计](case2-audit.json)。
- [原生322次重验汇总及来源hash](native-results.json)、[启动命令/PID/GPU](runs.json)、[执行前计划](PLAN.md)。
- [正式运行及其它变体重验指南](../../../docs/molmobot-final-stations.md)。
- 原生重验日志：[Case1](../../../outputs/logs/molmobot-final-stations-case1-v000-20261011-r1.log)、[Case2](../../../outputs/logs/molmobot-final-stations-case2-v001-20261011-r1.log)。
- 原生包 `navigation/comparison.json.stations_after` 保留161行，`stations_final.json` / `station_selection.json`保留选点及压缩丢弃原因，`native_revalidation.json`绑定机器人/验证配置和native快照hash。
- 正式run `frozen_config.json.augmented_input` 与scene `augmented_input/` 冻结来源hash和ID；执行task点集与来源逐项相等，`lattice_regenerated=false`。两组只读接入审计valid=true。

## 验证与边界

- 输入契约8项、native重验契约及VLA加载前绑定顺序5项、原有no_edit39项：共52项专项测试通过。软件fixture不冒充真实机器人结果。
- 全套316项：306通过、9跳过、1失败。失败仍是既有MolmoBot默认配置半径0.792805608与测试预期2.0不一致；没有改写用户配置来消除此失败。
- [全套日志](../../../outputs/logs/molmobot-final-station-all-tests-final.log)、[native5项](../../../outputs/logs/molmobot-native-revalidation-unit-final.log)、[输入8项](../../../outputs/logs/molmobot-formal-input-tests.log)、[no_edit39项](../../../outputs/logs/molmobot-final-no-edit-unit.log)。compileall与git diff --check通过。
- 原生重验没有额外physics stepping/settle，不改变原native源积分状态；本次重查碰撞/支撑/Case，但未重做matched-control source effects或完整动态稳定性实验，不宣称新障碍搜索/完整物理增强验收。
- 输入接入与模型加载、实际动作、任务成功分别记录；截至审计0个新VLA trials，不因审计通过推断任务成功。
- 旧Case2曾因300秒attempt墙钟预算在381步时incomplete；仅修复点集不保证该预算问题消失，本次未擅自改变预算。
- 当前报告用计数、ID及hash一致性表表达接入正确性，不新增RGB/视觉质量结论。
- 同一上下文只读审计，不是独立agent复核；只在指定worktree修改，未修改原始资产、旧run或其它工作区，未Git提交/合并。
