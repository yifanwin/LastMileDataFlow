> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# 显式 data_root 与路径简化

已删除 `src/lastmile_dataflow/paths.py`，不另建同类路径模块。

## 实现

- `data_root`：含数据输入的配置必须显式提供；绝对路径或相对 repo root。
- 数据字段：相对 data_root，包括场景、资产、快照、权重与 tokenizer。已解析的绝对数据路径可直接使用。
- 代码/配置字段：相对 repo root，或直接写绝对路径；不再相对当前 JSON 目录。
- 各配置加载器在现有 `config.py` 的小型读取函数中明确列出自身字段；删除全局字段表、递归字段扫描、模板替换与环境变量覆盖。
- 删除保存时按 run 目录自动改写路径的行为。运行配置保存实际使用的路径；已有运行产物不会自动重定位，不增加旧协议兼容层。

```json
{
  "data_root": "data",
  "checkpoint": "models/MolmoBot-RBY1Multitask",
  "tokenizer_path": "models/Qwen3-tokenizer",
  "python": ".venv/bin/python"
}
```

示例配置、调用方、模型 smoke 和安装/数据说明已同步。Python 路径保留 `.venv/bin/python`，不会把符号链接展开为基础解释器。CLI 文件系统参数没有新增解析协议；跨目录调用时使用绝对路径。

## 验证

配置示例逐类加载通过；新增/更新测试覆盖显式 data_root、配置目录无关、repo root 与解释器符号链接、保存配置不按字段名猜测路径。

全套回归 **180 项：179 通过，1 项可选真实渲染跳过**。日志：`outputs/logs/explicit-data-root-tests.log`；Python 语法与 `git diff --check` 通过。

本轮不重新加载大型权重，不执行真实机器人任务。原工作树修改保留，未提交。
