# 障碍物配方

`procedural_obstacles_*.json` 用于独立构建实验，规则/数量/静置参数由 `scenes.obstacle_config` 读取。
基线几何参数引用 `configs/execution/curobo_v2.json`（相对 repo root）；场景输入统一按当前 scene-dataset 和快照格式读取。
旧工作树快照默认引用已移除；默认从 data_root 下的原始场景构建。当前专用机器人/metadata 输入可用性见 docs/setup.md，旧产物不当作可直接重载的示例。
