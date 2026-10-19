# π0.5 双本体构建方案编写总结

状态：done  
日期：2026-10-18  
替代：无  
关联：[构建方案](../plan/01-pi05-integration-build.md)、[接入需求](../requirement/01-pi05-integration.md)、[既有梳理总结](01-requirements-review.md)

## 执行与结果

阅读 `reports/pi05` 全部既有需求和评估，复核当前后端分派、控制、22D 状态导出、记录布局、benchmark 场景适配、依赖与安装入口，编写一份完整实施方案。

- 明确单仓库 `.venv` / `.venv-pi05`、pyproject 独立依赖组、JAX 本地权重恢复与一套推理服务。
- 将 Panda 路线 B 落到 FK、反馈跟踪、共用 controller、物理时序、导航与记录的具体职责。
- 提出 RBY state22 / position action20 适配及本体统计准备路径，区分模型 smoke、真实闭环与任务能力。
- 列明后端/profile 支持矩阵、拟改模块、P0–P6 实施依赖、最小真实评测与阶段验收。
- 同步需求关联与报告入口，保留既有评估文档，不把方案写成已实现能力。

本轮只读比对的两个 openpi 仓库共用 JAX 核心文件内容一致，为一套核心提供代码依据；未据此宣称两份权重已经加载成功。本机 uv 帮助确认支持从 pyproject 安装独立依赖组，安装步骤仍属拟实施内容。

## 检查与限制

核对文档本地链接、JSON 示例语法和差异格式。本次只修改文档，未安装环境、修改源码或配置、恢复权重、运行测试或 rollout。RBY 统计与 Panda 默认 profile 迁移仍需按方案调查和验证。
