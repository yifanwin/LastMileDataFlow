> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../../README.md)。

# 已合格子集42样本续构造

用户明确授权继续42样本，并将目标数量上下限取消（pick/open字段均-1）。本次复用旧有界筛选的4个pick、2个open，不声称穷举两场景所有目标。只改变数量限制，全部语义与物理资格不变；派生qualification记录原文件与recipe哈希，原JSON只读保留。
42 = 4pick×2case×5 + 2open。GPU3/1低占用，2单元并行；不占用当前高利用率GPU6/7。
期望：42个唯一且物理合格样本，各至少5个实际可见站位；可能搜索预算不足出现partial，不降低规则。最终manifest sealed且物理审计通过才算完成。仅目录交付且带README，不压缩、不执行策略。时间记录编排/构建/单元/变体，已完成发现与资格检查耗时不重跑。

## worktree迁移后的重跑

固定运行与输出根：scenes-datasets-design；新run：scene-twohouse-v3-qualified42-relocated-20261012-230210。旧run已因旧路径失效audit_failed，6个样本与失败日志全部保留只读；此次重新构造42个样本，不将旧6个算作新批次完成。
资格输入：outputs/diagnostics/scene-twohouse-v3-qualified42-relocated-20261012.json；只替换原worktree路径前缀，并更新recipe摘要，source文件SHA256与路径映射记录在relocation字段；6个任务原基线source/robot绑定核对通过。新物理规则、种子、目标、站位门槛均未改变。
旧分支/目录不存在时不建立别名，不覆盖旧manifest/checksums。运行期间不要移动worktree或outputs。
