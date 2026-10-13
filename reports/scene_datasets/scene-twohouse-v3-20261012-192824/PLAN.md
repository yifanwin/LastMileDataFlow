# 两场景 v3 构建

配置：val103_val104_countertop_targets_v3.json。目标62样本：每house 3个分散pick×2case×5变体 + 1个open。严格目标与物理资格，不自动partial。
GPU3/6/7；发现/资格检查用GPU3，构建最多3单元并行。未满足配额则停止并保留证据。
时间按编排阶段/单元/变体记录。完成标准为manifest sealed及物理审计通过。仅目录交付，带README，不压缩、不运行策略。旧样本只读保留。
基础commit：8dfc84200cebdf31778687d67a3831c1ca571884；不提交、不创建分支。

启动说明：首次run scene-twohouse-v3-20261012-192738 因解释器symlink解析丢失venv而在发现前退出；失败证据保留。当前run修复解释器定位，并已通过MuJoCo3.5.0依赖预检。
状态入口：outputs/diagnostics/scene-twohouse-v3-20261012-192824-orchestration/status.json；日志在outputs/logs/同run前缀。
