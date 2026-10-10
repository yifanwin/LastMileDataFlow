# yaw / 全命令限位与单次警告修复

## 执行过程与结果

对旧run `no-edit-v080-val103-cup30-20261010-165638` 中 S0044/t4 的 `base_theta=-3.1404502842168096` 越过模型下限-3.14，完成以下增量修复。未修改旧结果、原始资产或模型限位；未提交/合并或自动重启全量。

1. `planning/curobo_v2.py` 将真实 yaw 关节/执行器范围平移为局部范围；当前状态和世界目标使用同一个未折返的标量。原实现局部±pi加基准旋转、执行又用最短角差，会产生跨硬限位的路径。
2. `runtime/no_edit_execution.py` 新版路径与跟踪使用有界标量差，不跨±pi偷跳。允许较长旋转路径，保留速度/增量限制。初始采样 heading 只在小容差内裁剪并记录。
3. `robots/action_limits.py` 统一检查底盘、双臂、两夹爪、联动h目标的模型关节、执行器及协议交集。角度容差0.003rad，平移/夹爪容差0.001m；仅小幅越界投影，较大越界仍拒绝。原adapter原子严格校验保留。
4. 小幅投影事件追加 `command_limit_adjustments.jsonl`；原动作和提交动作分别记录。没有修改qpos、重置、传送或删除避碰。
5. `InvalidAction` 归为当前attempt的 `failure / control_limit:*`，TrackingFailure；记录warning事件及控制台警告，结束该attempt，其他独立试验继续。不是忽略非法命令、不是成功，也不将真实基础设施异常吞掉。

## 验证证据

- 回归 **211项，8项跳过，其余通过**；22.235秒。日志：`outputs/logs/all-limits-regression-20261010.log`。
- 原越界值的投影、arm/gripper/平移限位、执行器范围交集、较大越界拒绝和yaw不跨限位均有单元回归。
- 故障注入验证警告/失败分类；两个点位的10次独立试验全部继续。这些fixture不是物理抓取证据。
- **CUDA1原S0044、种子432661044真实重测 success / mobile_pick：390步、92.818秒。** 目标yaw范围[-3.1211603572950186,-2.3561944901923444]，未越界；本次未触发小幅裁剪。真实结果：`outputs/diagnostics/v080-yaw-fix-S0044-seed432661044/summary.json`，时间见同目录timing.json。
- 四视频保存于该目录 `attempts/v080-S0044-432661044/videos/`；第三人称ffprobe核验1280×720、391帧。

[真实第三人称视频](../../outputs/diagnostics/v080-yaw-fix-S0044-seed432661044/attempts/v080-S0044-432661044/videos/third_person_camera.mp4)

## 范围与限制

这是针对yaw及命令限位的修复与单次点位真实验收，不是全部物体/整个val完成证明。较大控制越界仍使当前attempt失败，只警告并继续剩余采样。尚未启动全量重跑；源码摘要改变，后续采集须新run-id。
