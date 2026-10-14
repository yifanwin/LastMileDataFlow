# 机器人配置

`rby1.json` 保存 RBY-1 模型路径、实际控制频率、物理步长和相机选择。
data_root 必须显式设置，model_path 相对它；需要按真实资产位置设置，见 [setup](../../docs/setup.md)。
位置控制和 native motor 是不同语义：机器人动作适配位于 `runtime/action.py`、`native_actions.py` 与 `robot.py`。
改变控制模式、步长或相机后，不复用不匹配的站位标定。所有物理资产只读，控制适配在内存中进行。
