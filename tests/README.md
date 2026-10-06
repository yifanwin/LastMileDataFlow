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
见 [阶段二交付报告](../reports/phase2/phase2-delivery.md)。

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

真实成败、视频与未测范围见[阶段三交付报告](../reports/phase3/phase3-delivery.md)。

## 自动 Case → 场景编辑

新增 `test_case_edit_*`、`test_scene_graph`、`test_edit_{sampling,compiler,views,review,topology}`：契约、实测支撑、相对旋转、联合采样、事务、四操作、Agent 协议、共享预算、去重及硬期限。
默认两个渲染测试需显式启用；fixture / mock 验证不代替真实房屋或真实模型验收。

```bash
MUJOCO_GL=egl CASE_EDIT_RENDER_TESTS=1 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python -m unittest discover -s tests -v
# 获得数据发送授权后才运行：真实服务 + 两房屋 + 两种抽象现象，每组目标 3
MUJOCO_GL=egl PYTHONPATH=src ../molmospaces/.venv/bin/python tests/case_edit_real_smoke.py \
  --api-settings configs/agent_api.json --settle-config configs/case_edits/settling.json \
  --snapshot-root outputs/attempts --timeout 600
```

`case_edit_agent_smoke.py` 验证真实 Agent1/2；`case_edit_three_agent_smoke.py` 为真实三个 Agent + 合成场景真实 RGB。
`case_edit_asset_smoke.py` 验证真实 THOR 资产 + 合成场景的 add/remove/rollback，**不含视觉模型最终接受**。
`case_edit_real_smoke.py --prepare-only` 仅验证真实房屋无人工目标的准备与 RGB，不产生 accepted。
诊断重跑可用 `--cases distance --skip-expected-failures`，不能据此声称完整 S7 gate。
实际结果及失败目录见[交付报告](../reports/case-edit-delivery.md)。

Provider 回归覆盖独立密钥、auto 故障切换计费、下一调用恢复第一优先级、显式 provider 不切换及 CLI 覆盖。
真实烟测也支持 `--provider auto|dmx|xera`；只指定名字，不在命令行放密钥。
