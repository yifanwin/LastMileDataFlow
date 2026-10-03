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

`test_case_shared_layer.py` 覆盖 case 共用层：逐条要求的 `strength` / `layer` 标注完整性、
零编辑显式标记、同量纲候选排序、`rank`/`shortlist` 决策语法与按用途计数的网关，
以及设计 5.B 的两条必测故障——case1 冻结初态被绕过（`measurement_source_inconsistent`）与
case1.5 侧向向量指向家具内部（前置检查拒绝且**不生成候选**）。合成 fixture 不是真实 RBY-1 证据。

`phase2_real_smoke.py` 显式使用阶段一已保存的真实 ProcTHOR/RBY-1 编译模型，重新执行
零编辑/移动、静置、冻结独立恢复及三相机短动作/解码。默认唯一 ID，不覆盖已有输出。
见 [阶段二交付报告](../reports/phase2/phase2-delivery.md)；
case 共用层与 case1/case1.5 的真实运行见
[共用层交付报告](../reports/phase2/phase2-case-shared-layer-delivery.md)。

## 阶段三

`test_stations.py` 验证采样、控制配置边界、固定底盘、严格抓取协议、预算、未知分类、
证据篡改、空轨迹与视觉 Agent 协议。fixture 不替代真实 cuRobo/MuJoCo 结果。
`phase3_planner_smoke.py` 是可选原生 GPU 规划诊断，不是成功标签。

`verify_phase3_delivery.py` 对指定浏览包的全部 attempt 独立审计，并用 ffmpeg 完整解码所有相机和交付视频，
核对帧数、SHA256、实际回放时间与阶段。只读原始数据，检查结果另存：

```bash
PYTHONPATH=src ../molmospaces/.venv/bin/python tests/verify_phase3_delivery.py \
  --collection outputs/collections/case1-phase3-v2 \
  --derived outputs/delivery_views/case1-failure-v2 \
  --output reports/checks/phase3-artifacts.json
```

真实成败、视频与未测范围见[阶段三交付报告](../reports/phase3-delivery.md)。
