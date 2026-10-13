# 当前版本Case1固定椅子样例：执行计划

用户要求：一个scenes-datasets和一个lastmile-datasets，包含对应的3个sample；椅子同资产/同位置、允许旋转，桌面随机，满足scene-cases-v3与当前raw-first协议。
外层专用名称：`outputs/case1-v3-fixed-chair-examples-20261013/`。不做benchmark、发布包或训练格式派生。

## 冻结条件

- val_103 / Cup_30 / pick，仅Case1，3变体，构造种子20261013。
- 从原cuRobo Case1 v000真实冻结模型提取Chair_007_2根坐标：XY=[6.899078400567273,2.1097407870111944]。
- 随机yaw绕根坐标旋转；摆放根XY精确一致，静置漂移限6mm（沿用源影响物理容差），无平移避碰/换椅子/焊死。
- 正常每份1把Chair＋5–8件新增目标支撑面物品；容量不足只接受现行穷举证书。
- 原场景/目标保留；候选域完整检查，coverage最多20点、至少5点，不继承原点有效性/成功率。
- 每点5次独立cuRobo试验；各20点时共300次。S0<=0.2、S1>=0.8、距离<=1m，优先3个S0、每个最多5个S1；无路径/无合格点对正常保留负结果。
- 当前evidence-v2、原300秒attempt预算；真实连续执行，不拼接标定失败轨迹，不重置/传送。
- RGB/depth/状态/控制/接触与时钟从第一帧记录到raw/canonical，实际采集文件位于lastmile-datasets/samples内，不跨外部run找RGB。

## 验收

构造：3个accepted样本，chair约束、Case1、物理/可见性/包校验通过。
采集：请求的3份完整标定及连续尝试有完整统计；成功训练episode仅在真实任务/导航成功且canonical审计通过后入集。
完成不等于成功；零训练样本不能写成成功数据交付。失败/基础设施异常/未执行分开保存。

## 运行范围与资源

主LastMileDataFlow工作区，基础commit33a45849bfa12e27f1efae7dfb004d6de79a7e07，不建分支/提交/合并。
日志唯一入口outputs/logs；构造过程与失败保留outputs/diagnostics。实际GPU运行前读取状态选空闲设备，不干扰其他任务。
场景构造与cuRobo采集耗时分别记录；不预设成功率或墙钟完成时间。
