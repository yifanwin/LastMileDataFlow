# 阶段 3 场景编辑替换

- 状态：active
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

已用 edits/placement.py、checks.py、search.py 替换并删除 modes/、build/、augmentation/。不再生成八类意图、代理判定、修复候选和模式提示站位。每任务生成原场景及一个编辑场景，桌面默认 1–3 个（目标 0.3 m 内同一支撑面），地面默认 0–2 个（目标和站位圈之间）；尝试/超时预算、拒绝原因计数、真实物理检查、房屋并行和失败隔离保留。

TaskModeRecipe 替换为 SceneRecipe 和 SceneEditConfig；新增当前示例 configs/scene_datasets/panda_omron.json，删除原八模式构建示例。覆盖选点并入 stations/sampling.py。删除只验证模式构造器/修复流程的测试，保留并迁移物理检查、采样、配置、房屋编排和原场景测试。

45 项相关测试通过（test_augmentation、test_mode_config、test_sampling、test_construction、test_scene_datasets、test_no_edit），未执行真实编辑场景接受率和预览对照，阶段 3 的真实验证尚未完成。资产加载三模块合并、scene_datasets 中废弃 recipe 构建支路清理随后继续。RL 专属文件未改。
