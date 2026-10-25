# 首阶段正式全量：命令与结果

- 状态：done
- 日期：2026-10-24
- 替代：无
- 关联：../plan/03-execution-schedule.md、2026-10-24-implementation.md

从本 worktree 根目录执行。下列为本轮实际 run-id；重新运行时换新 run-id。GPU 按用户指定使用 6、7。

```bash
export LD_LIBRARY_PATH=/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64:${LD_LIBRARY_PATH:-}
export CUDA_HOME=/data1/wenyifan/opt/cuda-12.8
export MUJOCO_GL=egl

# 完整构建 val103 / Cup_30 / Case2 / 1 变体
.venv/bin/lastmile-dataflow build-scenes \
  --config configs/scene_datasets/panda_omron_val103.json \
  --output-dir outputs --run-id panda-time-stage1-build-20261024-r03 \
  --build-workers 4 --gpu 6

# 构建完成后，对所有 accepted 场景的全部最终站位各做 3 次
.venv/bin/lastmile-dataflow evaluate-stations \
  --dataset outputs/scene_datasets/panda-time-stage1-build-20261024-r03 \
  --assets-dir /data1/wenyifan/molmospaces_data/assets \
  --config configs/execution/panda_omron_curobo.json \
  --output-dir outputs --run-id panda-time-stage1-calibration-20261024-r03 \
  --gpu 6 7 --cpu-workers 60 --preparation-workers 12 --recording-mode numerical
```

## 实际进度

- P11：274 项，272 通过、2 条件跳过。日志：`outputs/diagnostics/curobo-time/p11-software-regression-02.log`。
- 首次构建 `panda-time-stage1-build-20261024-2100`：在局部模型 XML 重读时失败，MuJoCo 3.5 将 2×4 skybox 的 gridlayout 导出为 12 字符；未生成场景，未启动标定。原 run 和日志保留。
- 修复：仅在导出派生 XML 时按 gridsize 截取有效布局，原 XML、贴图和 MJB 不变；简化 skybox 的重读/贴图检查及相关回归通过。日志：`outputs/diagnostics/curobo-time/p12-skybox-fix-tests.log`。

- 第二次构建 `panda-time-stage1-build-20261024-r02`：在增强重读局部 XML 时发现资源相对目录丢失，未生成场景。修复加载器的场景/机器人各自资源引用为绝对路径，统一派生 XML 导出；14 项相关检查通过。日志：`outputs/diagnostics/curobo-time/p12-asset-path-fix-tests-03.log`。

- 第三次构建 `panda-time-stage1-build-20261024-r03` 已完成：accepted 1、rejected 0，233 个候选、20 个最终站位，墙钟 54.315 s（发现 1.904 s、资格 5.094 s、构建/预览 47.296 s）。主记录为该 run 的 scenes.jsonl/summary.json。
- 标定 `panda-time-stage1-calibration-20261024-r03` 完成：60/60，20 成功、38 执行前规划无解、2 执行后续规划无解；22 条发生真实物理执行。基础设施错误 0，全部 recording/replay_complete，全部 observation_complete=false。成功率按原始 trial 为 20/60=33.33%；规划无解的任务判定仍为 unknown。
- 全部 20 点各 3 次：S0016/S0057/S0059/S0092/S0143/S0226 为 3/3，S0204 为 2/3，其余 13 点 0/3。低率 13 点、中间 1 点、高率 6 点；按标定率筛得候选有向配对 78，未验证配对导航路径。
- 标定总墙钟 225.458 s，调度阶段 224.658 s；GPU 活跃累计 374.379 卡秒，实际同时查询峰值 2、CPU trial 峰值 8、准备峰值 12（容量分别为 2/60/12）。CPU 物理累计 570.717 进程秒、准备累计 11.039 进程秒、recorder 收尾累计 3.439 进程秒。它们已并行，不可相加为墙钟；batch 归属的逐 trial 规划成本也不能当 GPU 活跃总时长。
- 单场景 20×3 数值标定和回放资料就绪用时 3.76 min，达到该任务 ≤30 min 目标。无匹配旧基线，不报告加速倍数。

## 离线回放

```bash
.venv/bin/lastmile-dataflow evaluate-stations \
  --replay-from outputs/panda-time-stage1-calibration-20261024-r03 \
  --rollouts r000004 r000026 --replay-camera front_camera \
  --output-dir outputs --run-id panda-time-stage1-replay-20261024-r03 --gpu 6
```

沿用前述环境变量。输出为 `outputs/replays/panda-time-stage1-replay-20261024-r03/{rollout}/front_camera.mp4`。成功 r000004 为 206 帧/10.248 s 实测，失败 r000026 为 302 帧/15.048 s 实测；均解码通过，320×240、20 fps，逐帧来源索引与实测时刻写在同目录 replay.json。渲染分别 3.460/5.866 s，不重新积分或规划。无实际 timeout，因此不制造超时视频。结论仅限本任务、position-v1 与当前资产路径，不代表完整视觉训练数据或百来源全 Case 成功。
