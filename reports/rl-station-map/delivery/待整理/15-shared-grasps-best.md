# 共享抓取候选、全站位训练与 best 选择

- 状态：done
- 日期：2026-11-12
- 替代：无
- 关联：../requirement/01-fixed-base-sac.md
- 记录时间：2026-11-12T17:51:16+08:00

## 改动

共享 `operation/grasps.py` 加载与位置／姿态 FPS：最多 1024 条等间隔选行、最近 64 条、FPS 16 条，默认 10 cm 等价 90°。cuRobo 使用整个候选池；RL 按 Home 预计算每站位候选，GPU reset 均匀抽取且 Episode 内固定局部目标，记录 grasp 来源和原行号。最终位置偏移归零，roll／pregrasp 保留。

全部合法站位参与训练，删除空间分组计算与配置；输出中的 train 标签仅描述训练站位，不代表数据划分。保留单站位和调试限制。周期评估默认关闭，`training.periodic_evaluation: true` 可启用每 100000 条经验的监控。

每个日志保存点及末尾按最近最多 100 个健康、全程无随机预热动作回合的未折扣奖励均值选 best，严格提高才替换；普通失败参与。latest 保存最新状态、窗口和 best 元数据；恢复时保留窗口并复制此前 best，若元数据声明 best 而文件缺失则明确报错。无有效回合时末尾回退并记录原因。回报来自近期多个 Actor 版本，是近似选优。

末尾加载 best 的 Actor 与双 Q 后评估、录视频，并生成同模型 Q 图。每 grasp K=16 动作，再对全部候选等权平均，保存逐 grasp 分数与总体均值／标准差。独立评估从全部合法站位抽点，不宣称留出泛化。

## 软件验证

- 站位图测试 67 项通过；相关场景／操作／分层测试 23 项通过。覆盖姿态 FPS、64 初筛、加载上限、均匀采样与局部 reset、grasp Q 聚合、best 均值／平局／无样本回退。
- 恢复训练保留 best 和窗口、末尾重载 best 的相关测试 3 项通过。
- 全套回归 337 项，1 failure、9 errors、3 skipped。错误均指向缺少 cuRobo 或 rtree；failure 为测试期待 CUDA 报错，但先遇到 cuRobo 缺失。不宣称全套通过。
- `git diff --check` 通过。

## 真实检查

产物根目录：`outputs/experiments/scratch/diagnostics/grasp-fps-check/runs/`。运行前 GPU 0–5 忙，6／7 空闲；检查使用 6／7，未干预其他进程。

1. `rl-reset/`：4 环境、8 steps、2 次 SAC 更新，有限状态检查通过，局部 reset 保留其他 3 个环境的 qpos、目标身份及回合缓存。
2. `end-to-end/`：为验证输出链而使用 0.1 s Episode、32 条经验、warmup=2 等短程配置，配置嵌入 checkpoint 元数据。完成 11 次学习更新，best 选中 transition=24，10 个合格回合均值 0.036786102。末尾加载该 best，生成 16 grasp 的 Q 汇总和 2 个视频。仅证明链路运行，不证明抓取学习效果。
3. `short/`：4 条经验的初始检查已保存无合格回合时的 fallback best／latest，但末尾较长 rollout／视频在 90 s 限制内未完成，由上述缩短 Episode 的检查验证输出链。
4. `curobo/`：真实场景加载和 16 候选准备完成，但进入 V2Planner 时缺少 `curobo` 包，未完成真实 GPU 规划。

真实 reset 命令：

```bash
MUJOCO_GL=egl LD_LIBRARY_PATH="$PWD/.venv/lib/native" .venv/bin/python tests/smoke/rl.py \
  --config configs/station_map/rl/val103_cup30_s0246_y04.yaml --gpu 7 \
  --num-envs 4 --steps 8 --random-actions --learner-updates 2 --batch-size 4 \
  --output outputs/experiments/scratch/diagnostics/grasp-fps-check/runs/rl-reset/result.json
```

## 限制

未启动完整训练，未验证收敛、空间泛化或抓取成功率。cuRobo 真实规划因环境缺依赖仍待验证。新配置应使用新 run，旧配置 checkpoint 仍遵循有效配置必须一致的恢复约定。
