# 执行前计划：MolmoBot最终点集接入

目标：已提交的native重建脚本只移植障碍布局，不绑定最终20点；本次复用旧run中不可变的native初始快照，对原始161点逐点重新做native初始化/可见性检查，再覆盖压缩20点，接入既有augmented_variant正式采集入口。
预期：两组原生包所有161行有独立仿真记录，selected仅包含native实际通过点；最多20点、距离与方位覆盖；formal collector点集逐项等于包，无重新lattice，所有VLA试验属于final IDs。
不变项：原始障碍布局、native模型/初始物理状态、VLA模型、5 trials/点、400动作步horizon；不改旧run/原始资产、不Git提交。
区别：重验使用native motor/10Hz/4ms及1024x576，绝不沿用position-v1/20Hz/320x240的验证结论。
验收：新软件契约测试 + 两组真实GPU全量重验 + formal输入审计；VLA采集运行单列状态，不把输入通过宣称任务成功。失败不放宽几何/支撑阈值。
保留：outputs/diagnostics新native点集包；outputs/no_edit新采集run；全部日志/PID在outputs/logs；报告紧凑记录、源hash。旧native快照来源两个2240xx运行，源增强r5冻结结果只读。
