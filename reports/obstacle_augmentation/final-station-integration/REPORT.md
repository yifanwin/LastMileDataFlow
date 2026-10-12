# 最终覆盖站位正式接入 no_edit：执行报告

## 结论

**正式接入已实现，两组新原生 cuRobo 运行均已启动，落盘点集与增强来源完全一致，接入审计通过。**
采集仍在运行，尚未完成100次 station trials 或导航/抓取成功验收。下表是审计时的快照，非最终任务结果。

| 新运行 | 原增强后真实有效点 | 实际采用点 | 计划 station trials | 已记录 trials | 状态 |
|---|---:|---:|---:|---:|---|
| Case1 v000 | 24 | 20 | 100 | 5 | running |
| Case2 v001 | 58 | 20 | 100 | 0 | running |

## 修复与执行过程

1. 旧 collector 只预置增强快照，之后又重新采样161点，导致 Case1 v000 运行24点、Case2 v001 运行58点；旧数据不能视为最终20点运行。
2. 新增可选 `NoEditConfig.augmented_variant`。正式入口自动导入对应原生快照、最终点集、覆盖记录与构建 task；无需手工复制后伪续跑。
3. 原生模型/状态/实例/版本校验和、机器人配置、radius/spacing/可见性配置、场景 metadata 与目标身份全部校验。重算覆盖选择，必须与来源逐项一致，数量上限20。
4. 执行端直接使用已通过独立仿真初始化的冻结 station rows/initial；禁止 lattice 重新生成，不补点、不截前20点、不回退到原场景。旧缓存或点集外的 trials 明确报错。
5. 采用新 run-id，在 GPU0/7 启动两组，每点5次。旧 run/原始增强变体保留；未停止其它用户运行，未修改原始资产。

“分散”沿用目标距离近远极值、8方位×2距离层代表，以及位置/方位/距离联合最远点填充；
是在实际合法区域内做覆盖，不保证严格等间距，更不能在障碍占用区域强行补齐。
后续导航 rollout 复用这些站位，不新增采样点。

## 可追溯证据

- Case1：[新运行目录](../../../outputs/no_edit/no-edit-final20-case1-v000-20261011-225943)；[日志](../../../outputs/logs/no-edit-final20-case1-v000-20261011-225943.log)；[只读接入审计](case1-audit.json)。
- Case2：[新运行目录](../../../outputs/no_edit/no-edit-final20-case2-v001-20261011-225943)；[日志](../../../outputs/logs/no-edit-final20-case2-v001-20261011-225943.log)；[只读接入审计](case2-audit.json)。
- [运行命令、PID、GPU和预期](runs.json)、[执行前计划](PLAN.md)。
- [正式接入指南](../../../docs/no-edit-augmented-stations.md)。
- 配置：`configs/case_edits/no_edit_curobo_case*_v*_final_stations.json`，两种Case各5份都有对应配置。本次真实启动的是Case1 v000 / Case2 v001；不能宣称全部10份已跑完。
- 新run `frozen_config.json.augmented_input` 冻结所有来源hash、selected IDs；task `station_input.json` 记录 `lattice_regenerated=false`，`stations.json` 内容与来源完全相等，`progress.json` 记录20点/100次。
- 审计脚本 `scripts/audit_no_edit_station_input.py` 只读核对全部复制输入hash、覆盖算法、最终站位内容、进度计划和现有 trials/attempt station IDs；属于同一上下文审计，不是独立agent审查。

## 验证与限制

- 新增8项输入契约测试全部通过；原有 `test_no_edit.py` 39项全部通过。fixture测试只证明软件契约。
- 全套311项：**301通过、1失败、9跳过**。失败是原有 `test_molmobot.ConfigTests.test_model_defaults_are_from_selected_sim_configs`：当前用户MolmoBot配置半径0.792805608与测试期待2.0不一致；未修改该配置或为了通过测试恢复用户改动。
- [全套日志](../../../outputs/logs/final-station-input-all-tests.log)、[专项8项](../../../outputs/logs/final-station-input-unit.log)、[no_edit39项](../../../outputs/logs/final-station-input-no-edit-tests.log)。`compileall` 与 `git diff --check` 通过。
- 两组真实 collector 已创建原生 attempt。当前 planning_no_solution 不等于实际运动失败；没有 stepping 的 attempt 不宣称发生真实运动。接入通过不等于抓取或导航成功。
- 复用现有已验收的冻结物理增强变体，未重新随机采样障碍物、未重录旧RGB。主要受控变量只有执行端采用的站位集合。
- 本报告用精确计数、hash及ID一致性表，而非新图表达接入是否正确；没有新增视觉质量结论。
- 当前工作区基础commit `43565387f36ff8aaa4da591c7ba74b0d91eb3331`；代码仍是工作区改动，各run冻结代码摘要。未Git提交或合并。
