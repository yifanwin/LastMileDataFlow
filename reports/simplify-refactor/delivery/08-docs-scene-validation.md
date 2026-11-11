# 文档配置清理与真实场景验证

- 状态：done（本轮增量，完整目标继续 active）
- 日期：2026-11-11
- 替代：无
- 关联：../plan/01-refactor-plan.md、01-handover.md

## 实际改动

- 清理旧 Case/no-edit 配方、case_edits、scene_eval 和重复 lastmile_datasets 预设。保留当前 SceneRecipe、机器人、执行、collection、策略、benchmark 和 RL 配置；原 reports 与真实运行结果不删除。
- fixed_pick 后端已删除，仍用于独立策略/本体 smoke 的输入场景定义改名为 panda_omron_scene.json，不将它误称为保留了固定抓取执行器。
- README、运行手册、架构、数据格式、配置索引与测试说明按五阶段脚本、统一编辑、cuRobo标定、直接自包含导出和当前文件结构更新。setup 安装说明保留，移除旧CLI示例。
- 场景预览原来只保留函数，未从构建入口调用。本轮补上 export_scene_previews，并更新构建编排测试的隔离边界。
- smoke GPU入口同步使用生产 select_devices，避免直接将物理卡号写入EGL序号。

## 真实验证

GPU检查：0–3空闲，4–7存在其他用户负载；仅使用GPU0。两个运行均使用固定已提交源码，运行期间未修改源码：

1. 71668c4，scene-check：house103/Cup_30、seed42，5个站位，桌面1个障碍/地面0个，最多8个候选；原场景和编辑场景均接受，无候选拒绝。
2. 6de1c22，scene-preview-check：同一输入，原场景和编辑场景均接受；自动导出两张PNG和index.html，预览失败0。

原场景与阶段0 noedit 对照：stations逐项一致、scene_id一致、model.mjb字节一致、initial.npz所有数组一致。未生成指纹或哈希。

编辑场景真实通过物理校验，放置1个桌面障碍、保留5个站位。已查看预览图，原场景/编辑后并列展示目标、增加的障碍及站位。结果与图像位于 outputs/experiments/simplify-refactor/runs/，不重复复制到reports。

23项相关局部测试通过（场景编排、配置、脚本与自包含/Panda导出）。此前全套回归中的新增 station_preparation import 错误已修复并通过局部测试。

## 剩余项与限制

仍需最后核对长函数/闭包及生命周期分类，核对全部保留/删除清单，并完成重构后的必要标定/采集/benchmark读取验证。当前真实场景检查不证明完整规划、策略或训练成功；不将计划里的历史验收项写成已完成。
