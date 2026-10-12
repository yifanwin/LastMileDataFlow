# 阶段三交付：case1 站位图与真实成败采集

日期：2026-10-02。**case1 的“构建 → 采样 → cuRobo → 真实执行 → 审计反馈 → 导出”最小闭环已完成。**
本次 Cup 数据包含 **3 次严格成功、3 次真实执行失败**。这不是成功率统计，也不是连续导航演示。
case2、case3、case1.5 仍未完成真实任务覆盖。

## 先看交付结果

- [数据浏览 HTML](../outputs/collections/case1-phase3-v2/REPORT.html) · [逐配置 CSV](../outputs/collections/case1-phase3-v2/station_table.csv) · [全部轨迹索引](../outputs/collections/case1-phase3-v2/stations.json)。
- [成功交付视频](../outputs/attempts/case1-contact-v1-S000-left-h0.7380-g794/videos/delivery.mp4)：杯子抬起 **11.54 cm**，双指承力、无桌面支撑持续 **3.52 s**。
- [抓持失败交付视频](../outputs/delivery_views/case1-failure-v2/videos/delivery.mp4)：双指短暂接触后未带起杯子。来自原始失败轨迹的新版展示，**没有重跑物理或改写原始 attempt**。
- [运行与配置说明](../docs/phase3.md) · [审计结果](checks/phase3-artifacts.json) · [测试日志](logs/phase3-unit-tests.log)。

![固定底盘离散站位图](../outputs/collections/case1-phase3-v2/station_map.png)

图 1：线段表示初始化朝向；绿色为至少一个配置真实成功，红方块表示同点还有其他配置的执行失败。
橙色只有有限预算规划无解，灰色叉为初始化过滤，灰色圆为未测。**未知不插值，也不填成失败。**
S000 与局部加密点 S026 有成功见证；源起点 S001 只在已测左臂、高躯干、指定 grasp 的预算内无解。
图是独立站位操作结果，不证明机器人导航到过这些点。家具矩形只是显示轮廓，不是规划碰撞模型。

## 执行过程与实现结果

1. 从原始 **ProcTHOR val_103** 新建 Cup 候选，使用原始家具与物体，没有恢复旧管线成功场景。
   目标 Cup_30 位于餐桌；新源起点 `[6.3, 2.7, -0.5354]`，与目标水平距离 **1.178 m**。
   零资产编辑，真实静置 1 s，落位、支撑、稳定性、穿透检查通过；冻结独立恢复完全一致，阶段一六步交接审计通过。
2. 实现分层站位采样：真实地面/关节限位/初始碰撞过滤，再运行原生 GPU cuRobo。
   单臂规划锁定非活动关节，用实测躯干和底盘坐标；规划 FK 与 MuJoCo 核对。失败规划不产生可执行路径。
3. 每个实际试验重新加载同一冻结初态，只在开始前设置底盘站位。
   开始后通过原 **20D 动作桥**真实调整躯干、接近、闭合、抬升、保持；不传送、不回滚、不固定目标。
4. 每个物理 tick（.004 s）记录力接触和协议约束，另存 20 Hz 动作/完整状态、初末快照、计划、三相机视频及实际 qpos 回放。
5. 先测 0 mm 接近深度，保存真实抓持失败；按[运行前对照计划](phase3/phase3-contact-plan.md)只把接近深度改为 10 mm，获得严格成功。
   没有加大夹爪力、关闭碰撞或放宽验收阈值。
6. 再做粗采样与实测边界局部加密，自动产生分层站位图、逐配置结果、失败阶段和追加式 `case_verified` 反馈。
   合并前独立审计全部原始证据，不改阶段二的历史 `unknown` 字段。

源码新增 `stations/`、`planning/`、`validation/pick.py`、`workflows/stations.py` 与 `exporting/`；
CLI 新增 `station-map`、`audit-station`、`export-stations`、`render-delivery`、`vision-review`。
工程不导入、不调用 `lastmile_pipeline` 或 MolmoSpaces Python 包；本机只借用已安装第三方依赖的解释器和只读资产。

## 真实成功和失败分开解释

| 冻结 run | 规划调用 | 真实执行 | 严格成功 | 执行失败 | 未执行规划无解 | 墙钟耗时 |
|---|---:|---:|---:|---:|---:|---:|
| [0 mm 对照](../outputs/station_maps/case1-probe-v2/REPORT.html) | 5 | 1 | 0 | 1 | 1 | 163.9 s |
| [10 mm 对照](../outputs/station_maps/case1-contact-v1/REPORT.html) | 5 | 1 | 1 | 0 | 1 | 159.2 s |
| [粗采样与局部加密](../outputs/station_maps/case1-coarse-v3/REPORT.html) | 28 | 4 | 2 | 2 | 14 | 659.3 s |
| 合计 | **38** | **6** | **3** | **3** | **16** | **982.4 s** |

各行是独立 attempt，其中包含重复近点验证；不是独立场景样本，也不估计总体成功率。
耗时为 run 记录值，另有构建 192.6 s、语义复查 7.35 s；不含调试、派生渲染与最终审计耗时。

![严格成功终态](../outputs/attempts/case1-contact-v1-S000-left-h0.7380-g794/delivery_final.png)

图 2：成功终态来自真实状态记录，显示目标离桌、双指承力。验收还使用全过程物理记录，不仅看这一帧。
固定协议要求抬升 ≥5 cm、双指真实力接触、无外部支撑连续 ≥2 s、相对滑移 ≤1 cm/5°，
且底盘漂移 ≤2 mm/.2°，头/闲置臂/躯干误差 ≤.002 rad，无禁止碰撞。
S000 的两次 10 mm 独立执行均达到 **11.54 cm / 3.52 s**；S026 达到 **11.52 cm / 3.508 s**。

![抓持失败终态](../outputs/delivery_views/case1-failure-v2/delivery_final.png)

图 3：0 mm 对照虽然所有操作计划成功，闭合时也曾有双指受力，但随后失去抓持。
最大抬升仅 **0.016 cm**，无支撑保持 0 s；800 个控制步骤和 10000 个物理 tick 全部保留。
“浅接触”是根据对照和力记录提出的解释，尚未做系统接近深度扫描。
**相同站位成败不同来自控制配置差别，不能归因于站位本身。**

另两次失败为 S000 的 h=0、S026 的 h=.369：分别实际执行 64、361 步躯干调整后，
实测状态重新规划无解，未进入有效抓持。它们是“已开始操作但未完成任务”的真实执行失败，
**不是两次抓持滑落**。完整视频与阶段记录位于浏览包对应行。

## 站位覆盖和 case 结论的范围

粗采样有 **26 个底盘位置/朝向**，14 个初始化有效，12 个被真实地面、碰撞或关节限位过滤。
产生 2 个局部加密候选，总共 28 个位姿。左右臂 × 三躯干高度 × 单一 grasp 是候选配置，共 168 行。

最终粗采样 run：2 成功、2 执行失败、14 未执行规划无解、72 几何过滤配置、**78 未测配置**；
在 4 次执行预算处停止（规划 28/32、墙钟 659.3/1200 s）。
右臂尚无真实执行，连续躯干、其他 grasp、更多 yaw 也未穷举；S027 仍未测。
合并包有 172 行、22 个实际创建的 attempt，包括 16 个没有动作/视频的规划失败。

本次 `case_condition=pass` 的准确含义：**同一有效冻结场景，源起点已测配置在有限规划预算内困难，其他站位有严格物理成功见证。**
没有证明源起点对全部抓法都不可解，更没有全局物理不可达结论。
反馈是新增记录：[粗采样反馈入口](../outputs/station_maps/case1-coarse-v3/build_feedback.json)。

## 阶段二补齐了什么

![静置后的俯视图](../outputs/builds/phase3-case1-cup-raw-v2/observations/0001/diagnostic_top.png)

图 4：本次新构建的静置场景，零资产编辑。
[目标局部图](../outputs/builds/phase3-case1-cup-raw-v2/observations/0001/diagnostic_target.png) ·
[机器人视图](../outputs/builds/phase3-case1-cup-raw-v2/observations/0001/robot_head.png) ·
[程序检查](../outputs/builds/phase3-case1-cup-raw-v2/checks/initial.json)。

经用户明确授权，向 `https://www.dmxapi.cn/v1` 的 `gpt-5.6-terra` 实际发送这三张图和最小几何摘要。
**1 次调用、1036 tokens、7.35 s**，严格版本绑定 JSON 返回可见布局 `pass`，理由为目标杯子可见并在餐桌台面上。
[请求摘要](../outputs/agent_reviews/review-2ef4b01bce9b/request.json) · [响应及用量](../outputs/agent_reviews/review-2ef4b01bce9b/response.json)。
密钥未写入产物；拒绝 API 重定向，不把语义建议当成抓取/导航结论。

这补齐了 **Cup 静置图的真实视觉模型语义复查**，不是多轮视觉 Agent 选候选/执行编辑的完整联调。
原始起点曾有严重家具碰撞，失败构建保留；程序现会直接拒绝无效机器人初态，不尝试通过移动目标修好机器人碰撞。
同时修正真实地面射线被 visual geom 遮挡的问题，底盘固定伺服不再跟随漂移，±π 超出实际 ±3.14 限位时过滤而不裁剪。

## 验证与剩余限制

- **75/75 单元回归通过**。涵盖防重置、固定底盘、预算、配置边界加密、严格力抓取、空轨迹、设施错误、证据篡改、反馈证据与 API 重定向拒绝。
  简化 fixture 只验证软件逻辑，不替代上述真实 GPU/模型证据。
- **22/22 attempt 独立审计通过**：输入/输出摘要、冻结版本、轨迹连续性、20D 底盘零命令、回放 qpos 及物理成功证据。
- 相机及交付视频完整解码：最终检查 **25 段、12778 帧**，逐段核对帧数、摘要及展示帧到实际时间/阶段的对应。
  [检查脚本](../tests/verify_phase3_delivery.py) · [机器可读结果](checks/phase3-artifacts.json)。
- 站位图、成功/失败终态均已打开检查；本地无头 Chromium 的 [HTML 截图](checks/phase3-browser.png) 已检查。
  Markdown 交付入口与本地视频链接另做存在性检查，见 [文档检查](checks/phase3-documents.json)。

视频参照用户给定交付形式：**1280×720、多视角、阶段与实时物理事实**；原三相机保留 20 Hz。
展示视频取实际 qpos，无运动插值，躯干 4×、抓取 1×；墙体仅渲染透明，原物理模型不改。
0 mm 原始 attempt 的旧展示也保留，本文链接是最新的只读派生版本。
浏览 HTML 内嵌图和样式，视频/原始轨迹使用相对链接；转移交付包时需保留相关 `outputs/attempts/` 与 `outputs/delivery_views/`，不是一个脱离数据目录即可播放的 HTML。

未纳入最终样本的调试失败仍保留于原 outputs/logs：无效起点构建、原生规划器 head 锁关节错误、
π 限位错误，以及修正前的采样调度 run。没有把基础设施崩溃算作任务失败。

尚未完成：case2 把手使用归因、case3 障碍因果、case1.5 三侧导航；全量视觉 Agent 编辑闭环；
阶段四连续导航/VLA/困难恢复；阶段五批量续跑发布。
**本次验收来自 val_103，不是 train 场景覆盖**；多房屋、多资产、双臂成功率、train-10k 大规模采集仍为 `unknown`。
lift 规划尚未附着目标模型，实际接触仍逐 tick 验证；曲面碰撞 mesh 是三角化近似，不能据无解推导物理不可能。

本次没有可用 Git 仓库状态，未创建或提交版本；原场景资产、旧管线与既有采集记录未覆盖。
