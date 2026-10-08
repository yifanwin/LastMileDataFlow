# 阶段三：站位图与真实成败采集

实现 **case1 普通抓取**的“构建 → 分层站位 → 原生 cuRobo → 严格物理执行 → 自动审计/反馈 → 浏览导出”。
不依赖旧管线或 MolmoSpaces Python 包，也不需要人工逐例批准。连续导航示教仍属于阶段四。

## 运行

在 `LastMileDataFlow/` 执行。GPU 必须在运行环境可见；不要用 CPU/假规划替代 cuRobo。

```bash
export DATAFLOW_PYTHON=../molmospaces/.venv/bin/python
export CUDA_VISIBLE_DEVICES=1
export MUJOCO_GL=egl
export MUJOCO_EGL_DEVICE_ID=0

# 原始 ProcTHOR 场景的新构建，不导入旧成功标签
bin/lastmile-dataflow build --house 103 \
  --dataset-dir ../molmospaces_data/assets/scenes/procthor-10k-val \
  --build-config configs/builds/phase3-case1-cup.json --build-id my-case1-build

# 输入必须是 candidate_ready；新 run-id，不覆盖旧记录
bin/lastmile-dataflow station-map --build outputs/builds/my-case1-build \
  --station-config configs/stations/case1-cup-coarse.json --run-id my-case1-map

# 审计阶段三抓取 attempt；不要用阶段一 audit 判断任务成功
bin/lastmile-dataflow audit-station outputs/attempts/<attempt-id>

# 合并同一冻结场景/目标的不同控制配置；保留全部原始成败
bin/lastmile-dataflow export-stations --runs outputs/station_maps/<run-a> \
  outputs/station_maps/<run-b> --collection-id my-case1-data

# 更新展示格式时只生成派生视频；不修改原始 attempt，不重跑物理
bin/lastmile-dataflow render-delivery --attempt outputs/attempts/<attempt-id> --view-id my-view

# 可选、需明确外发授权：仅场景语义复查，不覆盖物理结论
bin/lastmile-dataflow vision-review --build outputs/builds/my-case1-build --env ../.env
```

也接受 `--snapshot <frozen-dir>` 做独立操作试验，但它不是阶段二构建验收。
cuRobo 是可选 GPU 依赖，应单独安装与 CUDA/PyTorch 匹配的版本；本机使用现有原生 cuRobo checkout。
`pip install -e '.[phase3]'` 安装其余可视化/规划辅助依赖，不自动安装或下载 cuRobo。

## 分层结果

| 层 | 字段与意义 |
|---|---|
| 初始化 | `geometry=valid/geometry_filtered/not_tested`；真实地面覆盖、实际关节限位和机器人碰撞，不代表导航到过该点 |
| 规划 | `planning=success/no_solution/infrastructure_error/not_tested`；显式检查 cuRobo success，无解不会返回可执行路径 |
| 执行 | `execution=success/failure/not_executed/incomplete/infrastructure_error`；只有实际运动能产生物理任务成败 |
| case | 源起点已测配置困难 + 其他点有严格成功见证才 pass；有限预算无解不代表所有抓法、连续躯干或全 yaw 不可解 |

规划失败的轨迹为空、没有执行视频。发生躯干调整后再规划失败则保留调整动作/视频，首个失败阶段独立记录。
基础设施失败停止当前队列，不换场景制造任务失败；未知和预算耗尽不填零。

每个站位记录 XYZ 平面位姿 `[x,y,yaw]`、手臂、h、grasp row、接近深度、预算、冻结初态与所有计划。
默认提供显式 probe + 目标周围多半径/朝向粗采样，再在已测成败边界附近做有界局部加密；
先检查全部粗采样几何，只对预算内配置运行规划与真实执行。未执行区域仍保留。
`max_candidates` 截断范围在冻结配置中明确，不宣称穷举整张地面。

## 动作与物理验收

- 初态只修改机器人 base；非 base 的 qpos 完全相同。此操作不是导航。
- dry planner 的 h 只用于诊断，不进入真实执行；实际 attempt 独立重载后动态调整躯干。
- 连续执行开始后禁止恢复/放置/回滚。所有动作走原 20 维桥；闲置臂、头和底盘目标保持固定。
- 原生 cuRobo 锁定非活动关节，使用实测底盘坐标系；FK 位置/朝向与 MuJoCo 核对。
- 世界碰撞由实际各 geom 生成 mesh，保留桌腿间隙；曲面基元使用三角化近似。家具 AABB 只用于图的背景。
- 目标从操作规划障碍中排除以允许指接触；当前 lift 未在规划器中附着目标模型，真实物理全程检查目标支撑与机器人碰撞。

固定 `strict-pick-v3` 协议不可通过配置放宽：双指真实法向力 >1e-6 N、抬升 ≥5 cm、
无环境支撑连续保持 ≥2 s、相对滑移 ≤1 cm / 5°；逐 .004 s 检查底盘漂移 ≤2 mm / .2°、
头/闲置臂/躯干误差 ≤.002 rad 及禁止碰撞。躯干调整按实际 h 检查联动，稳态按目标 h 检查。
所有物理 tick 落盘，20 Hz 动作状态另存。不得固定杯子、关闭碰撞或逐帧写回目标位姿。
修订 2（2026-10-08，`PROTOCOL['revision']`）：手指与目标的接触不再被归为 `nonfinger_target`（修订 1 会把穿透 >1 mm 但当步不承力的手指接触误判为非手指）；
手指穿入目标 >1 cm 判为 `finger_target_penetration`。掌部/手腕/手臂碰目标的判据和其余阈值不变；已有执行按其记录的逐步事实审计，结论不变。

`approach_offset_m` 是沿已验证格式的 grasp 接近轴的有限程序参数，范围 [0,.02] m。
不同深度是不同控制配置，不能把同站位的成败差别归因于站位。所有配置分别冻结；失败不会被成功覆盖。

## 预算与产物

`max_plans` 统计每个 dry/实际阶段的规划调用；`max_executions` 统计真正开始独立物理试验的次数。
每次 attempt 的超时是协作式检查，整个 CLI run 使用隔离进程硬期限，也覆盖 GPU 原生调用。
硬超时保留 `interruption.json` 与部分动作，但不为部分 attempt 伪造终态或任务失败标签。
此阶段不实现续跑：中断后使用新 run-id。阶段五再接统一断点调度。

- `outputs/station_maps/<run>/`：冻结输入、候选、几何过滤、逐配置 JSON/CSV、进度、case/预算汇总、审计、PNG/SVG、HTML/Markdown。
- `outputs/attempts/<id>/`：规划与 FK/资源摘要、站位初态、原始动作/状态、逐 tick 力接触、真实终态、可复核 verdict、`replay.npz`、三相机及交付 MP4。
- `outputs/collections/<id>/`：同场景各 run 的审计索引和浏览包；大文件保留在原始 attempt，不重复拷贝。
- `outputs/feedback/`：追加 `case_verified` 或保留难例/重新归类/设施失败反馈，不改原 build 的 unknown 历史字段。
- `outputs/agent_reviews/`：外部模型的最小图像摘要、版本绑定的严格响应、token/耗时。无密钥。

交付视频为实际 qpos 的无插值回放，**1280×720**，主视角 + 目标近景 + 机器人 RGB + 同时刻接触/抬升事实。
躯干段 4×、抓取段 1×，显示真实时间；墙体仅在渲染时透明，原始 MJB 和物理碰撞不变。
三相机视频保留原 20 Hz 时间序列。没有规划执行的点没有执行视频。
浏览 HTML 的图和样式自包含，MP4/原始证据用本地相对链接；分享时保留关联 attempt 目录。

## 验证与边界

```bash
PYTHONPATH=src ../molmospaces/.venv/bin/python -m unittest discover -s tests -v
```

程序测试用 synthetic fixture，不代表真实物理成功。真实结果见[交付报告](../reports/phase3-delivery.md)。
当前不支持 case2 把手归因、case3 障碍因果和 case1.5 三侧导航验收；不借普通抓取通过宣称这些模板成立。
没有 VLA、连续导航或批量发布结论。普通资产/房屋多样性与大规模成功率仍未知。
