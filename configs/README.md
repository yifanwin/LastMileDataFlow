# 配置

`scene_datasets/` 为场景库配方，`case_edits/` 为障碍物参数，`execution/` 为逐点执行，`lastmile_datasets/` 为连续采集，`robots/`、`policies/` 为机器人和策略。
cuRobo 默认配置为 `execution/curobo_v2.json`，仅 V2；旧版配置和 pin 清单已删除。
每次运行保存有效配置和种子，不保存审计身份。data_root 由用户显式填写；数据字段相对 data_root，配置/代码字段相对 repo root 或直接写绝对路径。各配置加载器明确列出自己需要解析的字段，不扫描嵌套产物。资产与模型仍需按 docs/setup.md 准备。
