# 验证分工

- `test_foundation.py`：配置、20D、原子控制、快照、防重置、格式桥、记录与连续性。
- `test_failures_and_legacy.py`：错误分类、严重穿透、非有限状态诊断、渲染故障现场保留和旧格式转换。
- `helpers.py`：简化协议模型生成器，不代表真实 RBY-1。
- `real_smoke.py`：显式真实模型验证，不随 unittest 自动运行；详见项目 README。
- `fixtures/legacy_case1_episode.json`：从旧已知样例复制的必要输入子集，只用于初态转换回归；
  不含人工审批、旧运行代码、成功标签或密钥，运行不访问 `lastmile_pipeline`。

单元测试可离线且无需 GPU；真实验收依赖外部资产和 GPU 渲染，结果分开报告。

## 阶段二

`test_construction.py` 增加四类规则模板、事务/跨模型回滚、落位/稳定性/邻居保护、过期或非法决策、
资产资格与资源异常、冻结身份、离线恢复、硬期限监督进程和累计预算测试。
所有简化 fixture 只证明程序接口，不是实机 RBY-1 或真实抓取证据。

`phase2_real_smoke.py` 显式使用阶段一已保存的真实 ProcTHOR/RBY-1 编译模型，重新执行
零编辑/移动、静置、冻结独立恢复及三相机短动作/解码。默认唯一 ID，不覆盖已有输出。
见 [阶段二交付报告](../reports/phase2-delivery.md)。
