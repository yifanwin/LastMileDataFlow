> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../README.md)。

# 场景库

发现任务 → 筛选有效采样点 → 构建障碍物或直接保存 open 场景。
统一输出 `config.json`、`scenes.jsonl` 和 `scene-files/`；augmenter 直接写入父场景库，不发布 child run。
Case1/Case2 的数量范围、碰撞、静置、支撑、源场景影响和最终可见站位检查保留；没有 capacity 例外或封存复查。
详见 [最小数据格式](../data-format.md) 和 [运行手册](../运行手册.md)。旧格式不再兼容。
