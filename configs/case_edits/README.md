# 程序化障碍物与现有增强输入配置

四 Agent 的 `case-edit` 请求、API、独立 settling/views 配置已删除。目录名在后续目录整理时调整。

- `procedural_obstacles_*.json`：通过 `augment-obstacles` 运行的独立程序化增强配方；具体规则以文件中的版本和字段为准，不自动解释成最新 scene-datasets 规则。
- `no_edit_curobo_case*_final_stations.json`：使用现有增强快照与最终点集的 cuRobo 配置。
- `no_edit_molmobot_case*_final_stations.json`：现有 native MolmoBot 输入配置。

后二类配置暂时保留，因为当前核心 collector 仍支持 `augmented_variant`；输入统一在步骤三完成。
最新场景库入口见 [增强场景构造指南](../../docs/增强场景构造运行指南.md)。
