# 配置职责

- `robots/rby1.json`：显式模型路径；其他控制参数和初态默认值由 `RobotConfig` 集中定义，运行时完整冻结。
- `tasks/foundation.json`：第一阶段仅执行 short_action，不声明抓取成功或 case 实证。
- `collection/smoke.json`：最多 6 步、80 次初始化候选、三相机 320×240、轻量协议与零 Agent 调用。

路径以 JSON 所在目录为基准。默认资产路径只是本机示例，部署到其他机器必须显式配置。
配置、动作布局、限制和数据格式见 [data-format.md](../docs/data-format.md)。

- `builds/phase3-case1-cup.json`：本次原始 Cup 构建，目标/支撑与起点明确；不导入旧成功状态。
- `builds/case1-train169-distance.json`：case1 距离困难（train_169，冻结底盘在西侧，距离区间 [1.05,1.25]）。
- `builds/case1_5-val103-sides.json`：case1.5 侧向差异（val_103，家具局部系三侧向量与角色标注）。
- `stations/case1-cup-probe.json` / `case1-cup-contact.json`：同站位 0 / 10 mm 接近深度对照。
- `stations/case1-train169.json`：case1 阶段三站位（含冻结初态与就近细化探针）。
- `stations/case1_5-val103-sides.json`：case1.5 分侧站位配置，`side_points`/`side_roles` 与构建证据一致，
  `max_side_assignment_m` 决定站位归属哪一侧。
- `stations/case1-cup-coarse.json`：26 个粗位姿、最多 2 个局部细化位姿；两臂/三高度是候选配置，
  32 次规划/4 次真实执行/1200 秒限制意味着配置可能未测，不能当成双臂实证。

站位配置 v3 与物理协议见 [phase3.md](../docs/phase3.md)。配置冻结后不可覆盖既有 run；使用新的唯一 ID。

`--scene-xml` 会先解析（`resolve()`）路径：若场景 XML 本身是指向 NAS 缓存的软链，相对 mesh 引用
（`../../objects/thor/...`）会落到缓存目录而失败。此时改用 `--house <n> --dataset-dir <本地场景目录>`，
或传入本地树内的真实路径。构建/站位失败只写 `result.json`，不会覆盖既有产物。
