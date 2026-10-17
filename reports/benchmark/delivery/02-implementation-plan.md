# lastmile-bench 实施方案整理记录

状态：done
日期：2026-10-17
替代：无
关联：[需求](../requirement/01-lastmile-bench-v0.1.md)、[实施方案](../plan/01-lastmile-bench-v0.1.md)

## 执行过程与结果

核对现有 benchmark workflow、CLI、逐站位标定、A*、native 场景转换、VLA 请求、动作桥、recorder 和 PandaOmron 实际交付边界，形成分阶段实施方案。

方案确定复用五个命令，以 RBY-1/MolmoBot 作为首版默认组合；先解决 native 物理配置上的 cuRobo 原地标定，再实现恢复区域与路径、困难 episode、Full/fixed 闭环和分组统计，最后接入 Open 并扩量。给出了目标文件布局、配置、命令、最小测试和真实验收顺序。

确认需处理的实际障碍包括：native 场景与后端绑定、native 固定底盘当前被拒绝、position/native 夹爪控制含义不同、现有路径长度不含端点连接段，以及缺少恢复诊断和 Macro。方案未将这些能力描述为已实现。

## 验证与限制

检查文档元信息、关联链接和需求覆盖；本次只新增方案与整理记录并更新文档入口，未修改源码、生成可运行配置或启动 GPU/模型实验。方案状态为 draft，示例命令标为实施后的目标接口。保留此前需求文档及工作区已有迁移。
