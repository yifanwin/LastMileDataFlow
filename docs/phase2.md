# 阶段二：规则式场景构建

实现桌面等**固定、明确水平碰撞面**上的有限编辑、真实静置检查和冻结交接。
输出 `TaskCandidate`，不是机器人成功标签。v1 `TaskConfig` 与 attempt 连续执行限制不变。

## 使用

以下命令从项目根目录执行。与阶段一一样，先安装依赖或指定 `DATAFLOW_PYTHON`。

```bash
# 静态索引：只扫描明确选择的房屋，不为全库运行仿真
bin/lastmile-dataflow index --dataset-dir ../molmospaces_data/assets/scenes/procthor-10k-train \
  --houses 0 2 --output outputs/indexes/train-0-2.sqlite
bin/lastmile-dataflow search outputs/indexes/train-0-2.sqlite --category SaltShaker --dynamic

# 真实 GPU 可用时：构建后独立恢复，执行至多 6 步并记录三相机
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 bin/lastmile-dataflow build --house 0 \
  --build-config configs/builds/phase2-cached-train0-move.json \
  --initial-snapshot outputs/attempts/phase1-final-train0/scene

# 从静态索引选择目标；parent 仅作为检索线索，加载后仍需验证真实支撑面
bin/lastmile-dataflow build --index outputs/indexes/train-0-2.sqlite --category SaltShaker \
  --build-config configs/builds/case1-retrieval.json

# 独立尝试，不改写原构建版本
bin/lastmile-dataflow run --snapshot outputs/scene_versions/<directory-id> --target <instance-id>
```

`--no-images` 显式关闭构建诊断图，不等于视觉模型复查；`--no-regression` 留下交接待验状态，CLI 不返回正式完成。
输出 ID 不允许覆盖。CLI 构建在隔离进程运行，整个 request 的墙钟期限也覆盖原生模型加载与 NAS 等待；
超时后终止子进程，并保留部分证据。进程结束有最多 4 s 的清理宽限。
Python `run_build()` 是用于联调的进程内接口（协作式检查期限）；需要硬截止时使用 `supervised_build()` 或默认 `build_request()`。
`--initial-snapshot` 可以只读使用 **未编辑、无 legacy restoration 的 v1 冻结模型**。检查包摘要、机器人配置与来源描述；
状态编辑不读取在线原始资产，不宣称重新核验当前 NAS 文件内容。模型结构编辑仍须核验在线源码摘要后重新编译。
冻结模型被视为输入，不被修改。`--index` 使用 `@retrieved`/`@parent` 引用检索实例及其 metadata 父对象；
无法确认实际水平面、部件语义或机器人合法初态时拒绝，不猜测。

## v2 构建契约

核心字段：`schema_version="2.0"`、`task_id`、`case_type`、明确的现有实例 `target`/`support`、
`editable`、`allowed_operations`、`protected`、`parameters`、`protocol`、`budget`。
`region_geom` 可限制到某个实际碰撞几何；`robot_base` 是 `[x,y,yaw]`。

| 模板 | 必需参数 | 检查含义 |
|---|---|---|
| case1 | `distance_range_m` | 目标 body 原点到机器人底盘的水平距离 |
| case2 | `handle`、`desired_direction_world` | 经声明验证且有来源的部件局部轴，转换为世界方向 |
| case3 | `obstacle`、`approach_offset_m`、`obstacle_distance_range_m` | 障碍在世界坐标接近走廊内且稳定；不是实际规划阻断证据 |
| case1.5 | `frame_body`、`side_a`、`side_b`、`min_distance_difference_m`、`clearance_radius_m`、`min_clearance_difference_m` | 家具局部坐标系中两侧的距离与保守几何净空差异；不是导航验证 |

`height_range_m` 可选，指**目标 body 原点**高度。`yaw_candidates_rad` 可声明有限朝向。
case2 的 `handle` 必须有 `body`、非零 `axis_local`、`source` 与 `verified=true`；
标注 body 必须属于目标。系统不默认 +X 是把手，配置中的验证声明需要调用者提供可信来源。

`initial_operations` 是需要整体提交的一组操作。每项有 `op`、`instance`，移动/旋转/新增还需
`pose=[x,y,z,w,x,y,z]`，新增需 `asset_id`。关节编辑、缩放与任意模型导入明确不支持。
固定家具只支持保持高度的水平平移/世界 yaw 旋转，并重新编译模型；有依附物时必须在同一事务中处理，复杂支撑链会拒绝。
所有被移动的动态对象都会重新验证指定平面上的支撑和稳定性，不仅检查任务目标。

### 资产池

`asset_pool` 指向版本化 JSON，路径相对配置文件。每项需 `asset_id`、`xml_path`、`root_body`、
`sha256`、`qualification`、`parts`。首版新增只允许一个 free root joint 的单根动态资产；加载/摆放分别有 `status=verified` 与 `evidence`。
资产会重新解析、编译并检查摘要。普通抓取、把手抓取资格独立记录；未知资格不被提升。
首版任务目标必须已存在；新增池当前用于辅助资产/障碍，不支持在同一初始化步骤创建缺失的任务目标。
没有随工程发布未经真实校准的通用资产池；单元测试资产只是测试 fixture。

## 事务与验收

1. 核对操作权限、保护对象、版本、实例和支撑依赖。
2. 保存模型工作副本与完整积分状态。
3. 状态编辑或模型重编译；跨模型按稳定关节/执行器名称迁移状态。
4. 保持机器人 ctrl，真实 `mj_step` 静置；不逐帧重写位姿。
5. 检查指定支撑接触、足迹与边距、支撑射线、末尾运动窗口、全过程严重穿透/弹飞、邻居与机器人初态。
6. 重新检查全部构建要求；提交，否则恢复原模型与状态。失败证据仍保留。

平面区域来自实际水平 box 面（不限局部 Z 轴）或水平 mesh 三角形。
mesh 上 5×5 支撑射线是保守采样，**不是任意洞口的数学证明**；柜层、容器及动态支撑链不纳入首版覆盖。
默认协议固定为 `placement-v2`：静置 1–2 s，尾窗 0.25 s，严重穿透 10 mm，
边距 10 mm，支撑高度容差 8 mm，速度阈值 0.025 m/s。这是有限试跑协议，不宣称全资产校准。

规则优先零编辑，再按要求和改动幅度排序候选。失败候选摘要不重复，换方向/位置有候选上限；
编辑、修复、Agent 与墙钟预算贯穿整个 request，不因换房屋重置。未找到合格候选时正常退出，不保证产出。

## 产物和独立恢复

- `outputs/indexes/`：SQLite 检索、JSON 记录；`parsed` 只表示 XML/metadata 可解析。外部 mesh/texture 默认记录引用，`exists=null`、`resolution=not_checked`，加载资格仍为 unknown。
- `outputs/builds/<id>/`：配置、初始化、事务、检查、观察、决策、成本、任务候选及交接证据。
- `outputs/scene_versions/<directory-id>/`：MJB、完整积分状态、配置/来源/依赖与实例映射。
- `outputs/attempts/<id>-handoff/`：阶段一短动作、三相机与审计结果。
- `outputs/feedback/`：追加下游反馈，不改写原 build 或冻结场景。

`model_id` 覆盖编译模型、实例与兼容信息；`checkpoint_id` 覆盖完整动态状态及控制恢复信息。
`scene_version_id` 组合模型、状态、映射与构建信息；目录 ID 是独立存储键，不等同版本摘要。
压缩 NPZ 文件的字节不用于定义动态身份。依赖清单保存来源/引用；源码和 include 有摘要，外部资源默认不逐文件哈希。
编译模型的内容摘要覆盖已经加载的资产；MJB 包含恢复所需资源，独立恢复不要求源资产在线。
需要逐资源检查时，Python `dependencies(..., verify_resources=True)` 提供显式慢路径。

`candidate_ready` 表示物理/构建要求与独立恢复、短动作交接通过；
`candidate_ready_regression_pending` 表示没有运行短动作交接；`handoff_failed` 表示交接异常。
case 条件与任务完成始终为 `unknown`，直到阶段三提供真实执行证据。

## 按需视觉接口与反馈

`agents/protocol.py` 提供俯视、局部、侧视和机器人头相机观察，所有图明确标记时刻与诊断用途。
调用者可通过 Python `backend(observation)` 接入模型；默认路径没有外部模型调用，也没有虚构模型审查。
输出只能选择/修复已给出的候选、补图、查询区域或放弃。非法 JSON、过期版本、未知 ID、
重复失败建议、越权字段、超时与超预算都会拒绝。后端没有仿真对象和编辑权限，不能覆盖硬检查。
调用记录的 `source` 区分规则与模型；测试后端不算真实视觉联调。

```bash
bin/lastmile-dataflow feedback outputs/builds/<id> --classification valid_unsolved \
  --evidence /path/to/phase3-evidence.json
```

分类还包括 `scene_invalid`、`success_without_expected_difficulty`、`infrastructure_failure`。
有效但未解的难例保留，不自动删除必要障碍；需要修复时新建构建，不覆盖既有采集版本。

## 验证

```bash
PYTHONPATH=src ../molmospaces/.venv/bin/python -m unittest discover -s tests -v
MUJOCO_GL=egl MUJOCO_EGL_DEVICE_ID=1 PYTHONPATH=src \
  ../molmospaces/.venv/bin/python tests/phase2_real_smoke.py --prefix phase2-new
```

真实 smoke 使用阶段一校验过的真实编译模型，避免重编译远程网格；不重新核验在线源文件摘要，也不将既有阶段一结果当作新编辑结果。
新编辑、物理静置、冻结恢复及短动作均在本次重新执行。结果、限制和日志见[阶段二交付报告](../reports/phase2-delivery.md)。
