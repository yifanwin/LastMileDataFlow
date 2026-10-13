# 当前版本样例：固定Chair的三个Case1 v3 sample

**一个scenes-datasets中的3个新sample已构造、封存并通过真实物理审计；同一组sample的LastMile采集已启动。**
这是采集启动快照，尚未完成全部标定/连续轨迹，不将开始运行写成成功数据交付。
专用外层名称：`outputs/case1-v3-fixed-chair-examples-20261013/`。没有发布包、benchmark或MolmoBot派生。

## 入口

- [样例目录](../../../outputs/case1-v3-fixed-chair-examples-20261013/README.md)
- [scenes-datasets manifest](../../../outputs/case1-v3-fixed-chair-examples-20261013/scenes-datasets/manifest.json)
- [三个sample索引](../../../outputs/case1-v3-fixed-chair-examples-20261013/scenes-datasets/samples.jsonl)
- [LastMile运行身份](../../../outputs/case1-v3-fixed-chair-examples-20261013/lastmile-datasets/run.json)
- [LastMile实时进度](../../../outputs/case1-v3-fixed-chair-examples-20261013/lastmile-datasets/progress.json)
- [使用与目录说明](../../../docs/datas/fixed-chair-case1-examples.md)、[执行前计划](PLAN.md)

## 构造结果

val_103 / Cup_30 / pick，仅Case1。Chair_007_2的实际源模型根XY为
`[6.899078400567273, 2.1097407870111944]`，来自用户满意的原Case1 v000冻结模型。
三份摆放根XY误差均为**0**；允许正常静置后的6mm内物理漂移，不焊死、不平移避碰、不更换资产。

| sample后缀 | 新增Chair | 新增桌面物品 | Chair yaw（rad） | 静置漂移 | 最终站位 | 计划标定 |
|---|---:|---:|---:|---:|---:|---:|
| `af075f2e118c5cdd` | 1 | 5 | 0.012327 | 1.391mm | 20 | 100 |
| `ec79c0d2fe4e6add` | 1 | 5 | 0.705053 | 1.391mm | 19 | 95 |
| `d05b614b021b2cbf` | 1 | 6 | -0.186602 | 1.391mm | 20 | 100 |

完整sample ID为`val_103-case1-<后缀>`；三个独立变体种子分别为1178404618、289243955、1758674230。
桌面物品是**新增数量**，不包含原桌面物体。原场景已有家具/物品保留。
第二份真实合格站位只有19个，符合>=5、最多20规则，不强行补点；因此总计59站位、**295次**标定，不冒称300次。
三份均满足正常数量要求，无容量短缺例外。真实物理审计3/3通过，包内身份/选点/摘要校验通过。
来源：[构造数值记录](../../../outputs/case1-v3-fixed-chair-examples-20261013/scene-results.json)、[物理审计](../../../outputs/diagnostics/fixedchair-v3-examples-20261013-audit.json)。

### 实际RGB

![sample 1](../../../outputs/case1-v3-fixed-chair-examples-20261013/scenes-datasets/samples/val_103-case1-af075f2e118c5cdd/media/after_oblique_a.png)

图1：真实渲染，新增1把固定根位置Chair和5件桌面物品；不作抓取成功结论。

![sample 2](../../../outputs/case1-v3-fixed-chair-examples-20261013/scenes-datasets/samples/val_103-case1-ec79c0d2fe4e6add/media/after_oblique_a.png)

图2：同一固定Chair根位置，允许旋转；新增5件桌面物品，最终19个站位。

![sample 3](../../../outputs/case1-v3-fixed-chair-examples-20261013/scenes-datasets/samples/val_103-case1-d05b614b021b2cbf/media/after_oblique_a.png)

图3：同一固定Chair根位置，另一次随机旋转与6件桌面物品布局。

三幅原PNG均已实际打开检查，未编辑/重绘物体。各sample保存6张增强前后真实RGB，总计18张；数量结论来自edits/checks，不靠图片计数。

## 执行与采集状态

1. 开始时主工作区干净；读取现有规则/实现，保持旧场景、源XML和历史run只读。
2. 从真实冻结MJB提取锚点，补充固定Chair资产/根XY入口、随机yaw及静置漂移检查。
3. 对偏心资产根补偿旋转后的footprint中心；约束同时进入构造拒绝与物理审计，不事后搬回Chair。
4. 真实目标资格检查通过：Cup_30基线60个有效站位；按现有Case1 v3构造3变体。
5. 三份封存并通过物理审计后，脚本自动开始同一个LastMile父dataset中的3个采集单元。

构造实际GPU6，3变体构造run墙钟约279.91s；此数不含目标发现、资格检查、最终包审计或后续cuRobo采集。
分变体约79.57s、66.50s、70.19s；[构造summary](../../../outputs/diagnostics/fixedchair-v3-examples-20261013-u000-r00/summary.json)保留准确计时，不将各项和当成整体墙钟。
采集在0–7中按实时利用率/空闲显存选卡，单worker依次处理三个sample；启动时实际选GPU6。

当前正在第一份的独立cuRobo标定，已有真实attempt与规划无解记录；规划无解不等于物理不可能，没有运动时不捏造运动视频。
每站5次，采集从初始化开始绑定当前raw-first/depth记录；真正执行时保存RGB/depth/状态/控制/接触和canonical对齐数据。
完整标定后才按S0<=0.2、S1>=0.8、距离<=1m选择并执行连续导航抓取；无路径/无合格点对保留负结果。
当前**尚无三份已完成采集或成功训练episode声明**；最终以各sample task结果、父summary和train_index为准。

实际文件位于`lastmile-datasets/samples/<sample-id>/scenes/val_103/attempts/pick/<station>/<attempt>/raw/、canonical/`，
与动作、结果在同一attempt内，不在外部no_edit目录找RGB。两个dataset通过同一sample ID关联。

启动和代码摘要：[launch.json](../../../outputs/logs/fixedchair-v3-launch-20261013.json)。
监控：[主日志](../../../outputs/logs/fixedchair-v3-examples-20261013.log)、[第一份worker日志](../../../outputs/logs/val_103-case1-af075f2e118c5cdd-val-103-worker.log)。
脚本：`scripts/run_fixedchair_v3_examples.sh`。不要重复启动；若中断，须核对相同代码/配置/输入再决定恢复，不覆盖旧attempt。

## 软件验证与限制

- [相关回归](../../../outputs/logs/fixedchair-v3-related-final-20261013.log)：124项，123通过、1跳过；新增4项锚点旋转/漂移/配置/目录测试。
- [全仓回归](../../../outputs/logs/fixedchair-v3-all-tests-20261013.log)：452项，440通过、2失败、10跳过。
- 两项失败为recorder异常分类和reach sampling配置断言，在未修改HEAD独立解包中同样复现：[基线证据](../../../outputs/logs/fixedchair-v3-baseline-failures-20261013.log)，未修无关模块。
- fixture验证不当作真实GPU验收；真实场景、渲染和包物理审计单列。`git diff --check`通过。
- 基础commit `33a45849bfa12e27f1efae7dfb004d6de79a7e07`；未创建分支、提交或合并。
- 输出位置为显式可选参数，现有默认布局保持兼容。实际执行文件位于父dataset内部，不新增发布包、镜像或MolmoBot格式派生。
- 三份是同一house/目标的随机变体，不代表三所独立房屋；未估计泛化或障碍物的因果难度提升。
