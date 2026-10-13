# Research 精简执行报告（2026-10-13）

## 结果

scene/lastmile 默认入口已切换为 Research 协议。构造期物理/case/站位检查保留；
完成后全量物理复审、自动 canonical 审计、多层 seal/递归输出摘要及重复 episode 索引删除。
每个 attempt 原子提交 episode.json；episodes.jsonl 为唯一全局视图；summary 可重建。
重型工具移至 scripts/debug。原始资产、旧采集结果及历史报告未删除或改写。

## 执行过程

先检查工作区及相关实现/测试；冻结新 schema 与身份/完成/统计契约；修改保存、记录、
加载、resume、导出及 CLI；同步协议文档与测试。修正了根目录场景配置的 worktree 路径。
真实构造首轮暴露 worker 初始化变量缺失，已修复并增加直接 worker/resume 回归，失败现场保留。
新数据使用新 ID；不执行旧数据兼容迁移，不提交或修改 Git 历史。

## 验证

| 项目 | 结果与边界 |
|---|---|
| 完整单元回归 | 457 项：446 通过、10 跳过、1 项既有失败 |
| 既有失败 | test_reach_sampling_config.test_only_requested_sampling_fields_differ_from_v080；改动前已失败，未改采样参数 |
| 真实 Case1 构造 | 1 个样本，新增表面障碍 5，合法 station 20 |
| 真实 Case2 构造 | 1 个样本，新增表面障碍 10，合法 station 20 |
| 真实采集/导出 | 两个 Case 各 2 个连续控制步、3 个状态帧、1 个 state/images 导出样本；RGB/depth、记录完整性及仿真健康通过 |
| 主 collector 诊断 | 每个 Case 最多 1 个 trial、45 秒 attempt 预算；两个 trial 均预算 timeout、0 执行步，完成记录及索引正常；未伪造执行视频或任务失败标签 |
| 输入/resume/重建 | 真实数据 preflight、改变导出开关后的 preflight resume、全局索引/summary 重建及完成场景 resume 通过 |
| 静态检查 | compileall、git diff --check 通过 |

构造沿用已有 Cup_30 发现结果，两个 Case 均重新构造；不把旧 accepted 样本直接当新构造。
采集/导出 smoke 不调用 planner，不赋予任务成功标签。主 collector 的 2/200 diagnostic
trial 不构成完整标定；正式 calibration_success_rate=null，未进入 S0/S1 recovery。
因此导航、抓取成功闭环及训练效果尚未验收。未做受控前后性能比较，不宣称具体提速或减量比例。

## 入口与证据

- [当前协议](../docs/datas/research-data-protocol.md)
- [新场景数据](../outputs/diagnostics/research-simplification-20261013-115653/scene_datasets/research-simplification-20261013-115653/manifest.json)
- [Case1 真实采集/导出](../outputs/diagnostics/research-simplification-20261013-115653/capture-case1/smoke.json)
- [Case2 真实采集/导出](../outputs/diagnostics/research-simplification-20261013-115653/capture-case2/smoke.json)
- [主 collector 诊断统计](../outputs/diagnostics/research-simplification-20261013-115653/lastmile_datasets/research-collector-one-trial/summary.json)

实际轨迹仍复用 no_edit 子 run 的路径，不复制第二套载荷。四个固定快照文件的身份摘要
仍保留，用于模型/初态/配置配对；它们不是递归目录 seal。独立 benchmark 的既有协议不在本次清理范围。
