# 配置

`scene_datasets/` 为场景库配方，`case_edits/` 为障碍物参数，`execution/` 为逐点执行，`lastmile_datasets/` 为连续采集，`robots/`、`policies/` 为机器人和策略。
cuRobo 默认配置为 `execution/curobo_v2.json`，仅 V2；旧版配置和 pin 清单已删除。
每次运行保存有效配置和种子，不保存审计身份。配置相对路径按配置文件位置解析；示例仍依赖用户已有的数据/模型位置，路径清理属于后续步骤。
