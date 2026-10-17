# 报告组织与当前状态

**当前五命令实现已整理，软件回归可用；真实成功操作示教与困难 S0 评测闭环仍未验证。**
运行方式见 [运行手册](../docs/运行手册.md)，不要从历史实施步骤寻找当前命令。

## 验证边界

- 第一轮有限真实实验已生成并重载增强场景，执行过 cuRobo V2 逐点规划和连续导航；没有得到成功操作示教。MolmoBot 逐点与单例 bench smoke 也未成功，不能视为困难评测完成。见 [真实实验记录](archives/第一轮精简/2026-10-14-refactor-light-step8-val103-cup30.md)。
- 第二轮在迁移后的源码上完成软件回归与真实快照 CPU stepping；没有重新验收 GPU rollout、模型推理和五命令端到端。见 [第二轮结果](archives/第二轮精简/实施结果.md)。
- 本轮整理与实际验证结果见 [整理报告](REPORT.md)。测试入口与验证类型见 [tests](../tests/README.md)。
- lastmile-datasets 的全组合、两类示教索引和独立物化已完成软件实现，验证见[交付报告](build-lastmile-datasets/delivery/01-implementation.md)；合成数据搬迁读取不代表真实机器人成功采集。

历史事实只描述对应版本，不能作为当前代码已通过真实验收的依据。公开机器人配套资产是否齐全仍是复现限制。

## 目录职责

`reports/` 按主题组织，每个主题内部按文档用途分类。例如：

```text
reports/
├── README.md
├── REPORT.md                      # 保留的最近一次整理结果
├── build-lastmile-datasets/        # 示例主题，按实际需要创建
│   ├── requirement/               # 需求文档
│   ├── plan/                      # 方案、计划文档
│   ├── delivery/                  # 结果、交付文档
│   ├── analyze/                   # 分析文档
│   └── 其他文档.md                 # 暂未明确分类的文档
└── archives/                      # 已有历史归档
```

当前主题：`build-lastmile-datasets/`、`benchmark/`（[lastmile-bench v0.1 需求](benchmark/requirement/01-lastmile-bench-v0.1.md)、[实施方案](benchmark/plan/01-lastmile-bench-v0.1.md)、[困难 S0 benchmark 现状](benchmark/info/01-current-status.md)）、`panda-omron/`。

- 新文档先确定所属主题，再放入对应分类目录；分类目录按需创建。
- 尚未明确分类的文档先放在所属主题根目录，例如 `reports/build-lastmile-datasets/`，不另建 `other/`。
- 已有 `REPORT.md` 和 [archives](archives/README.md) 暂时保留，不因本次分类规则批量搬迁历史文档。
- 报告不作为运行输入；不新增报告附件索引，不在这里堆积视频、日志等运行产物。
