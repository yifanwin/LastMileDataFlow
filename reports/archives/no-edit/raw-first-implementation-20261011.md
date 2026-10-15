> 历史归档：不作为当前运行说明或能力承诺。旧生成附件已清理，失效的本地链接已转为纯文本。当前入口见 [仓库 README](../../../README.md)。

# Raw-first 采集协议与 MolmoBot Adapter 实施报告

日期：2026-10-11。范围：LastMileDataFlow 的 MuJoCo 采集链路及 **MolmoBot-only Adapter**。

## 1. 执行过程与结果

1. 核对 Simulation、attempt recorder、cuRobo/no-edit/station 和 MolmoBot 执行入口，保留原有 20D 控制接口及历史输出。
2. 在真实 stepping 边界接入 raw/canonical sidecar，不用规划轨迹或事后状态回放代替执行证据。
3. 增加独立审计、全结果索引与不可变源包的 MolmoBot 导出；没有实现 π0 Adapter。
4. 补充异常、时序、控制器语义、图像变换及导出回归，再运行真实冻结场景短程采集。

| 已实现 | 证据内容 |
|---|---|
| raw | 无损 RGB/depth、完整 qpos/qvel/ctrl/act、原始指令、实际控制更新、逐 tick 接触、编译模型与初末状态 |
| canonical | 实测状态/运动、提交目标、严格 frame/interval 引用、实际时钟、相机内外参、任务/场景/结果/版本身份 |
| MolmoBot derived | 22D 状态、native 20D 动作、100 ms 窗口、16 步 chunk/mask、确定性 head warp、提示点同变换 |
| 工具 | `audit-canonical`、`index-canonical`、`export-molmobot` |

完整模型映射包含 joint/actuator/body/geom/sensor。固定相机保存原始 simulator 矩阵和光学坐标转换；跟随 observer 的外参按帧保存。
失败、不完整和拒绝输入保留证据；未知 task success 不推断为成功。旧 attempt 文件及成功筛选 `dataset_index.jsonl` 不替换。

## 2. 验证

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 全量 unittest | **288 项，9 项跳过，无失败** | 日志 |
| 真实 ProcTHOR train_2 / RBY-1 短 stepping | 2 个执行区间、3 个状态边界，12/13 physics ticks | smoke.json |
| 真实相机采集 | 3 相机 × 3 帧：9 RGB、9 depth、9 份 simulator 相机矩阵 | 原始包 |
| canonical 审计 | valid=true、issues=[] | 同上 smoke.json；独立审计可重复执行 |
| Adapter 图像变换 | 固定种子与本地只读 upstream 对照，测试图最大 uint8 差值 ≤1 | `tests/test_canonical_data.py` |

单元测试中的简化模型和 mock 图像只验证逻辑；没有把它们算作真实渲染或任务成功。
真实采集使用 Mesa 软件 EGL，源场景为已有冻结资产快照，输出使用独立新目录，未修改资产或旧采集结果。
最后的 Adapter 安全 mask 与 derived 元数据补充由最终全量回归覆盖；真实 smoke 不执行 Adapter 或模型推理。

测试覆盖完整状态、12/13 与 native 10/50 Hz 时序、partial interval、异常/NaN 保留、传感器故障、校验和/路径篡改、无执行结果索引、源包不变、position 标定约束、open 提示及超过安全范围时保留动作但 mask=false。

## 3. 使用入口

详见 实施说明 与 协议定义。

```bash
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow audit-canonical <attempt>
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow index-canonical <run>
DATAFLOW_PYTHON=../molmospaces/.venv/bin/python bin/lastmile-dataflow export-molmobot \
  --attempt <sealed-attempt> --output <new-output> --state-images-only
```

native profile 可以导出动作；cuRobo position profile **不能猜测米制 position 到 motor 的映射**。
没有真实标定时只导出状态/图像；动作导出必须提供显式 `--gripper-map`，代码不能代替物理标定验收。

## 4. 剩余限制

- CUDA driver 当前不可用，未验收 cuRobo CUDA、MolmoBot checkpoint 推理、抓取/导航成功或真实四相机 no-edit rollout。
- 软件 EGL 短程通过仅证明执行/渲染/记录/审计链路，`execution_complete` 不代表 task success。
- live-v1 冻结控制频率、physics_dt 和 controller profile；动态配置流、实机异步时钟同步未实现。
- 通用支撑关系和夹持真值无法可靠确定时保持 unknown/null；准备阶段缺少模型的 attempt 不伪造 raw。
- head warp 是模型 augmentation，不保证物理相机 rig/FOV 与 checkpoint 匹配；position→motor 不保证控制器重放等价。
- fsync 降低丢失风险但不保证硬崩溃零丢失；未封存或不完整源包禁止 Adapter 导出。

实现已落盘，未提交 Git；历史数据未自动迁移。
