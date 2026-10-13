# 当前版本样例：固定Chair的三个Case1 sample

入口：`outputs/case1-v3-fixed-chair-examples-20261013/`。
这是一个带版本说明的外层目录，里面只有一份scenes-datasets、一份lastmile-datasets，以及简短入口/锚点/构造结果说明。
两份dataset包含对应的三个sample，ID一致；不是三套dataset。

## 固定与随机条件

val_103 / Cup_30 / pick，scene-cases-v3。Chair_007_2沿用用户满意的实际冻结模型根XY：
`[6.899078400567273, 2.1097407870111944]`。不随机平移、不换Chair；绕自身根位置随机yaw。
原资产根可能偏离footprint中心，因此采样先补偿旋转后的局部中心，使**根XY**精确不变。
Chair正常参与物理仿真，不焊死；静置漂移限6mm，超过则拒绝，不搬回去伪造通过。
每份桌面新增5–8件真实注册物品，原物体不删除；现行容量不足证书规则保持不变。

原case和源资产只读。新场景各自完整检查候选站位，coverage最多20个，至少5个；无强行补点。
本次实际selected数为20、19、20，总计59站位，逐站5次，共295次标定。

## 目录与RGB

```text
outputs/case1-v3-fixed-chair-examples-20261013/
  README.md / chair-anchor.json / scene-results.json
  scenes-datasets/
    manifest.json / samples.jsonl / packages.jsonl
    samples/<sample-id>/
      construction/ / validation/ / media/          # 真实增强前后RGB
      robot_sensor_configs/<rs-id>/scene/、stations/
  lastmile-datasets/
    run.json / progress.json                        # 运行过程中已有
    manifest.json / summary.json / *index.jsonl      # 最终汇总逐步生成
    calibration/ / selections/ / audits/
    samples/<相同sample-id>/
      frozen_config.json / scenes/val_103/
        tasks/cup2/                                 # 逐点进度、率表和选择
        attempts/pick/<station-id>/<attempt-id>/
          raw/                                      # RGB/depth/状态/控制/接触
          canonical/                                # 帧、动作、状态对齐
          outcome.json / videos/ / trajectory.jsonl
```

原始采集实体直接落在lastmile-datasets的sample内，不放外部no_edit目录再跨根引用。
source scene package按执行协议复制到当前sample用于冻结输入核对；不自动导出MolmoBot/其他训练格式。
规划无解没有实际运动时不捏造运动轨迹或失败视频；RGB/depth只记录真实传感器输出。

## 运行与监控

已执行：[运行报告](../../reports/scene_datasets/2026-10-13-fixed-chair-v3/REPORT.md)。
复现参数见`configs/scene_datasets/val103_cup30_fixedchair_v3_examples.json`和
`configs/lastmile_datasets/fixedchair_v3_examples.json`；robot配置单独保存，不改用户原配置。
脚本`scripts/run_fixedchair_v3_examples.sh`先构造、断言3样本封存，再启动完整采集。
**不要重复启动**：输出目录拒绝覆盖。中断后的恢复须明确核对同输入/代码/配置，不用改动后的代码强行resume。

```bash
cat outputs/case1-v3-fixed-chair-examples-20261013/lastmile-datasets/progress.json
tail -f outputs/logs/fixedchair-v3-examples-20261013.log
tail -f outputs/logs/val_103-case1-af075f2e118c5cdd-val-103-worker.log
```

GPU构造前读取状态选择空闲GPU6；采集在0–7中实时选空闲设备，单worker依次处理三个sample。
标定后按S0<=0.2、S1>=0.8、距离<=1m选择，执行真实连续导航抓取，开始后不重置/传送。
无合格点对或全部失败也是完整负结果，不修改阈值换取成功。训练索引只收审计通过的真实成功连续轨迹。
当前是场景完成、采集进行中；不把启动/退出码/目录存在写成任务成功。不生成发布包或benchmark。
