# data

`recorder.py` 保存一份实际轨迹和观测；渲染/视频工具不改物理状态。
`statistics.py` 汇总逐点试验，`selection.py` 从完整标定选择 S0/S1；`benchmark.py` 提供困难起点选择及覆盖率统计。
`heatmap.py` 绘制成败图，`navigation_map.py` 保存可读导航图；不建立第二套协议索引。
`molmobot_dataset.py` 为按需转换，图像处理在 `molmobot_images.py`；非原生夹爪动作需要明确标定。
