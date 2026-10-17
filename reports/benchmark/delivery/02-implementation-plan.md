# lastmile-bench 实施方案整理记录

状态：done
日期：2026-10-17
替代：无
关联：[需求](../requirement/01-lastmile-bench-v0.1.md)、[实施方案](../plan/01-lastmile-bench-v0.1.md)、[标定口径修订](03-calibration-scope-correction.md)

## 执行过程与结果

核对现有 benchmark workflow、CLI、逐站位标定、A*、native 场景转换、VLA 请求、动作桥、recorder 和 PandaOmron 实际交付边界，形成分阶段实施方案。

修订后方案复用五个命令，先直接读取已有 scene-datasets 和 cuRobo 标定，包括允许底盘联合移动的结果；随后实现恢复区域与路径、困难 episode、Full policy 闭环和分组统计，最后扩展 Open 与规模。构建沿用来源机器人，策略接口适配与构建分开。给出了目标文件布局、配置、命令、最小测试和真实验收顺序。

需处理的构建差异为：既有标定被待测策略 profile 绑定阻断、现有路径长度不含端点连接段、缺少恢复诊断和 Macro。native 等控制适配按实际策略另行处理，不再作为重新标定前置。原方案提出的 native 固定底盘标定桥、准备场景模式和必做 Full/fixed 双模式已从主流程移除。

## 验证与限制

检查文档元信息、关联链接和需求覆盖；本次只新增方案与整理记录并更新文档入口，未修改源码、生成可运行配置或启动 GPU/模型实验。方案状态为 draft，示例命令标为实施后的目标接口。保留此前需求文档及工作区已有迁移。
