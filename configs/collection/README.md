# 记录配置

通过 `config.load_config(CollectionConfig, path)` 加载；`collect-no-edit --collection-config` 可显式传入。

- `smoke.json`：保留的小步数记录配置，不再对应历史 `run/build/station-map/case-edit` 命令。
- `no_raw_first.json`：现有诊断配置，不代表完整训练观测。

字段默认值见 `src/lastmile_dataflow/config.py`；必要时显式配置分辨率、seed、视频/depth 和记录目录。
现有 raw/canonical 载荷与协议字段待步骤三缩小；不能把关闭必要观测的数据当完整示教。
说明见 [Raw-first 使用说明](../../docs/datas/raw-first-implementation.md)。
