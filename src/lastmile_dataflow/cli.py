"""单一命令入口。默认只执行有界短动作，不调用 Agent 或 GPU 规划器。

【设计原则：薄命令入口】这个文件只做三件事：
  1. parser() 声明所有子命令及其参数；
  2. main() 按 args.command 分发到对应模块（真正逻辑都在各领域模块里）；
  3. 把结果状态映射成进程退出码（0=符合预期，1=不符合）。

所以读这个文件时不要期待算法——它是一张“命令 → 哪个模块负责”的地图。
子命令一览：
  run           阶段一 有界短动作 attempt
  build         阶段二 规则式场景构建
  index/search  阶段二 静态检索
  feedback      阶段二 追加下游反馈（不改原结果）
  station-map   阶段三 固定底盘站位图采集
  vision-review 阶段三 可选只读视觉复查
  export-stations / render-delivery / audit-station  阶段三 导出与审计
  import-legacy / audit  兼容与阶段一审计
"""
import argparse
from dataclasses import replace
from pathlib import Path

from .config import CollectionConfig, RobotConfig, TaskConfig, load_config
from .io import read_json

ROOT = Path(__file__).resolve().parents[2]


def parser():
    """声明全部子命令与参数。"""
    p = argparse.ArgumentParser(prog="lastmile-dataflow")
    sub = p.add_subparsers(dest="command", required=True)
    raw = sub.add_parser('collect-no-edit', help='raw val scenes: station trials, Gaussian maps and continuous 1–3 successful rollouts')
    raw.add_argument('--assets-dir', required=True, type=Path)
    raw.add_argument('--dataset-dir', type=Path)
    raw.add_argument('--robot-config', type=Path, help='optional existing RBY1M configuration')
    raw.add_argument('--config', type=Path, default=ROOT/'configs/no_edit/curobo_v080.json')
    raw.add_argument('--output-dir', type=Path, default=ROOT/'outputs')
    raw.add_argument('--run-id', required=True)
    raw.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    raw.add_argument('--workers', type=int, default=2)
    raw.add_argument('--houses', nargs='+', type=int)
    raw.add_argument('--targets', nargs='+', help='explicit subset: instance IDs, asset IDs or task IDs')
    raw.add_argument('--spacing-m', type=float)
    raw.add_argument('--trials-per-station', type=int)
    raw.add_argument('--max-tasks', type=int, help='explicit per-house smoke subset, not full collection')
    raw.add_argument('--max-trials', type=int, help='explicit smoke budget, leaves unfinished tasks incomplete')
    raw.add_argument('--resume', action='store_true')
    raw.add_argument('--index-only', action='store_true')
    raw.add_argument('--no-video', action='store_true', help='retain RGB but disable MP4')
    status = sub.add_parser('no-edit-status', help='read no-edit batch progress without starting physics')
    status.add_argument('run', type=Path)
    case_edit = sub.add_parser('case-edit', help='four-agent local single-scene construction; NOT mobile task verification')
    case_edit.add_argument('--request', required=True, type=Path, help='v0.2 single-scene request JSON')
    case_edit.add_argument('--api-settings', required=True, type=Path)
    case_edit.add_argument('--provider', help='auto or configured provider name')
    case_edit.add_argument('--model', help='explicit model override, e.g. glm-5.3; does not change local API keys')
    case_edit.add_argument('--robot-config', type=Path, default=ROOT/'configs/robots/rby1.json')
    case_edit.add_argument('--collection-config', type=Path, default=ROOT/'configs/collection/smoke.json')
    case_edit.add_argument('--settle-config', type=Path, default=ROOT/'configs/case_edits/settling.json')
    case_edit.add_argument('--view-config', type=Path, default=ROOT/'configs/case_edits/views.json')
    case_edit.add_argument('--output-dir', type=Path)
    case_edit.add_argument('--run-id')
    # ---- 阶段一：run。四种互斥来源：--house / --scene-xml / --imported-scene / --snapshot ----
    run = sub.add_parser("run", help="load raw scene and execute bounded short actions")
    sources = run.add_mutually_exclusive_group(required=True)
    sources.add_argument("--house", type=int)
    sources.add_argument("--scene-xml", type=Path)
    sources.add_argument("--imported-scene", type=Path)
    sources.add_argument("--snapshot", type=Path, help="prior attempt scene/ directory; new independent attempt")
    run.add_argument("--dataset-dir", type=Path,
                     default=ROOT.parent / "molmospaces_data/assets/scenes/procthor-10k-train")
    run.add_argument("--metadata", type=Path)
    run.add_argument("--robot-config", type=Path, default=ROOT / "configs/robots/rby1.json")
    run.add_argument("--task-config", type=Path, default=ROOT / "configs/tasks/foundation.json")
    run.add_argument("--collection-config", type=Path, default=ROOT / "configs/collection/smoke.json")
    run.add_argument("--base", nargs=3, type=float, metavar=("X", "Y", "YAW"))
    run.add_argument("--target", help="instance ID, unique asset ID or explicit MJCF body")
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--steps", type=int)
    run.add_argument("--seed", type=int)
    run.add_argument("--attempt-id")
    run.add_argument("--actions", type=Path, help="JSON list of 20D actions")
    run.add_argument("--no-video", action="store_true")
    conv = sub.add_parser("import-legacy", help="one-time explicit episode conversion; no approval import")
    conv.add_argument("--episode", required=True, type=Path)
    conv.add_argument("--assets-dir", required=True, type=Path)
    conv.add_argument("--output", required=True, type=Path)
    audit = sub.add_parser("audit", help="check evidence consistency")
    audit.add_argument("attempt", type=Path)
    # ---- 阶段二：build / index / search / feedback ----
    build = sub.add_parser("build", help="rule-based v2 scene construction and independent handoff")
    bs = build.add_mutually_exclusive_group(required=True)
    bs.add_argument("--house", type=int)
    bs.add_argument("--scene-xml", type=Path)
    bs.add_argument("--index", type=Path, help="retrieve a bounded list of indexed scenes")
    build.add_argument("--category")
    build.add_argument("--metadata", type=Path)
    build.add_argument("--dataset-dir", type=Path, default=ROOT.parent / "molmospaces_data/assets/scenes/procthor-10k-train")
    build.add_argument("--build-config", type=Path, required=True)
    build.add_argument("--robot-config", type=Path, default=ROOT / "configs/robots/rby1.json")
    build.add_argument("--collection-config", type=Path, default=ROOT / "configs/collection/smoke.json")
    build.add_argument("--output-dir", type=Path)
    build.add_argument("--build-id")
    build.add_argument("--initial-snapshot", type=Path, help="verified unedited v1 compiled foundation; avoid remote mesh recompilation")
    build.add_argument("--no-images", action="store_true", help="explicit geometry-only mode, no visual review")
    build.add_argument("--no-regression", action="store_true", help="leave short-action handoff pending")
    index = sub.add_parser("index", help="static discovery, not simulation qualification")
    index.add_argument("--dataset-dir", type=Path, required=True)
    index.add_argument("--houses", nargs="+", type=int, required=True)
    index.add_argument("--output", type=Path, required=True)
    query = sub.add_parser("search", help="query parsed static index")
    query.add_argument("index", type=Path)
    query.add_argument("--category")
    query.add_argument("--asset-id")
    query.add_argument("--dynamic", action="store_true", default=None)
    query.add_argument("--limit", type=int, default=20)
    feedback = sub.add_parser("feedback", help="append phase-three evidence without modifying frozen scene")
    feedback.add_argument("build", type=Path)
    feedback.add_argument("--classification", required=True, choices=["scene_invalid", "success_without_expected_difficulty", "valid_unsolved", "infrastructure_failure", "case_verified"])
    feedback.add_argument("--evidence", required=True, type=Path)
    feedback.add_argument("--new-build-id")
    # ---- 阶段三：station-map / audit-station / vision-review / export-stations / render-delivery ----
    stations = sub.add_parser("station-map", help="v3 fixed-base cuRobo success/failure collection")
    origin = stations.add_mutually_exclusive_group(required=True)
    origin.add_argument("--build", type=Path, help="ready phase-two build directory")
    origin.add_argument("--snapshot", type=Path, help="explicit frozen scene, no inherited success labels")
    stations.add_argument("--station-config", type=Path, required=True)
    stations.add_argument("--robot-config", type=Path, default=ROOT / "configs/robots/rby1.json")
    stations.add_argument("--collection-config", type=Path, default=ROOT / "configs/collection/smoke.json")
    stations.add_argument("--output-dir", type=Path)
    stations.add_argument("--run-id", required=True)
    audit3 = sub.add_parser("audit-station", help="verify phase-three attempt and physical pick evidence")
    audit3.add_argument("attempt", type=Path)
    review = sub.add_parser("vision-review", help="read-only settled-image API review; explicit external image authorization")
    review.add_argument("--build", required=True, type=Path)
    review.add_argument("--env", type=Path, default=ROOT.parent / ".env")
    review.add_argument("--timeout", type=float, default=60.)
    export = sub.add_parser("export-stations", help="combine audited v3 runs without mixing control settings")
    export.add_argument("--runs", type=Path, nargs="+", required=True)
    export.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    export.add_argument("--collection-id", required=True)
    replay = sub.add_parser("render-delivery", help="derive a new video from an audited actual trajectory, never rerun physics")
    replay.add_argument("--attempt", required=True, type=Path)
    replay.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    replay.add_argument("--view-id", required=True)
    return p


def main(argv=None):
    """按子命令分发。注意每个分支最后都返回退出码，供 CI/脚本判断。"""
    args = parser().parse_args(argv)
    if args.command == 'no-edit-status':
        import json
        print(json.dumps(read_json(args.run/'summary.json'),ensure_ascii=False,indent=2))
        return 0
    if args.command == 'collect-no-edit':
        from .stations.no_edit_config import load_no_edit_config
        from .workflows.no_edit import collect_batch
        config=load_no_edit_config(args.config)
        if args.spacing_m is not None: config=replace(config,spacing_m=args.spacing_m)
        if args.trials_per_station is not None: config=replace(config,trials_per_station=args.trials_per_station)
        for key in ('max_tasks','max_trials'):
            if getattr(args,key) is not None and getattr(args,key)<=0: raise ValueError('invalid '+key)
        if any(g<0 for g in args.gpu_ids) or len(set(args.gpu_ids))!=len(args.gpu_ids): raise ValueError('invalid GPU list')
        assets=args.assets_dir.resolve()
        robot=load_config(RobotConfig,args.robot_config) if args.robot_config else RobotConfig(
            str(assets/'robots/rby1m/rby1_v1.2_site_control.xml'))
        collection=CollectionConfig(output_dir=str(args.output_dir.resolve()),seed=config.seed,
            width=config.width,height=config.height,record_video=not args.no_video)
        path=collect_batch(args.dataset_dir or assets/'scenes/procthor-10k-val',assets,robot,config,collection,
            run_id=args.run_id,gpu_ids=args.gpu_ids,max_workers=args.workers,houses=args.houses,resume=args.resume,
            index_only=args.index_only,targets=args.targets,max_tasks=args.max_tasks,max_trials=args.max_trials)
        print(path)
        return 0 if read_json(path/'summary.json')['status'] in ('completed','indexed_only') else 1
    if args.command == 'case-edit':
        from .construction.scene_request import load_scene_request
        from .runtime.preparation import SettleConfig
        from .recording.edit_views import ViewConfig
        from .workflows.case_edit_supervisor import supervised_case_edit
        request = load_scene_request(args.request)
        robot = load_config(RobotConfig, args.robot_config)
        collection = load_config(CollectionConfig, args.collection_config)
        if args.output_dir:
            collection = replace(collection, output_dir=str(args.output_dir.resolve()))
        path = supervised_case_edit(request, robot, collection, api_settings=args.api_settings,
            provider=args.provider, model=args.model, run_id=args.run_id,
            settle_config=SettleConfig(**read_json(args.settle_config)) if args.settle_config else None,
            view_config=ViewConfig(**read_json(args.view_config)) if args.view_config else None)
        print(path)
        return 0 if read_json(path/'result.json')['status'] == 'completed' else 1
    if args.command == "render-delivery":
        # 从已审计的真实轨迹派生视频；绝不重跑物理。
        from .exporting.replay import render_delivery
        print(render_delivery(args.attempt, args.output_dir, view_id=args.view_id))
        return 0
    if args.command == "export-stations":
        from .exporting.collection import export_collection
        path = export_collection(args.runs, args.output_dir, collection_id=args.collection_id)
        print(path)
        return 0 if read_json(path / "summary.json")['data_collection_complete'] else 1
    if args.command == "audit-station":
        from .validation.stations import audit_station_attempt
        result = audit_station_attempt(args.attempt)
        print(result)
        return 0 if result['valid'] else 1
    if args.command == "vision-review":
        from .agents.http_vision import review_build
        path = review_build(args.build, args.env, timeout_s=args.timeout)
        result = read_json(path / "response.json")
        print(f"{result['status']}: {path}")
        return 0 if result['status'] == 'accepted' else 1
    if args.command == "station-map":
        # 阶段三：验证 build 交接 → 隔离进程跑站位图 → 用 summary 判定完成度。
        from .stations.config import load_station_config
        from .workflows.stations import supervised_station_map
        robot = load_config(RobotConfig, args.robot_config)
        config = load_station_config(args.station_config)
        collection = load_config(CollectionConfig, args.collection_config)
        if args.output_dir: collection = replace(collection, output_dir=str(args.output_dir.resolve()))
        # build 模式下，冻结场景路径从 task_candidate.json 里读；否则显式 --snapshot。
        snapshot = Path(read_json(args.build / "task_candidate.json")['scene_dir']) if args.build else args.snapshot
        path = supervised_station_map(snapshot, robot, config, collection, run_id=args.run_id, build=args.build)
        print(path)
        if not (path / 'summary.json').exists(): return 1
        return 0 if read_json(path / 'summary.json')['data_collection_complete'] else 1
    if args.command == "import-legacy":
        from .integrations.legacy import convert_episode
        convert_episode(args.episode, args.assets_dir, args.output)
        print(args.output.resolve())
        return 0
    if args.command == "audit":
        from .runtime.runner import audit_attempt
        result = audit_attempt(args.attempt)
        print(result)
        return 0 if result["valid"] else 1
    if args.command == "index":
        from .catalog.index import create_index
        from .scenes.source import SceneSource
        records = create_index([SceneSource.procthor(args.dataset_dir, h) for h in args.houses], args.output)
        print({"index": str(args.output), "statuses": [r["status"] for r in records]})
        return 0 if all(r["status"] == "parsed" for r in records) else 1
    if args.command == "search":
        from .catalog.index import search_index
        print(search_index(args.index, category=args.category, asset_id=args.asset_id, dynamic=args.dynamic, limit=args.limit))
        return 0
    if args.command == "feedback":
        from .workflows.build import record_feedback
        print(record_feedback(args.build, args.classification, read_json(args.evidence), new_build_id=args.new_build_id))
        return 0
    if args.command == "build":
        # 阶段二：加载 v2 构建配置，按来源（房屋/显式 XML/检索结果）组织 source 列表。
        from .construction.config import load_build_config
        from .scenes.source import SceneSource
        from .workflows.build import build_request
        config = load_build_config(args.build_config)
        robot = load_config(RobotConfig, args.robot_config)
        collection = load_config(CollectionConfig, args.collection_config)
        if args.output_dir: collection = replace(collection, output_dir=str(args.output_dir.resolve()))
        if args.house is not None:
            sources = [SceneSource.procthor(args.dataset_dir, args.house)]
        elif args.scene_xml:
            sources = [SceneSource(args.scene_xml.stem, str(args.scene_xml.resolve()), str(args.metadata.resolve()) if args.metadata else None)]
        else:
            # 从索引检索；@retrieved / @parent 引用允许把配置里的目标/支撑写成占位符。
            from .catalog.index import search_index
            records = search_index(args.index, category=args.category, dynamic=True, limit=config.budget.candidates)
            if config.target == "@retrieved":
                sources = []
                for r in records:
                    if not r["parent_id"]: continue
                    refs = {"@retrieved": r["instance_id"], "@parent": r["parent_id"]}
                    selected = replace(config, target=r["instance_id"], support=refs.get(config.support, config.support),
                                       editable=[refs.get(n,n) for n in config.editable], protected=[refs.get(n,n) for n in config.protected])
                    sources.append((SceneSource(r["scene_id"], r["xml_path"], r["metadata_path"]), selected))
            else:
                unique = {r["scene_id"]: r for r in records}
                sources = [SceneSource(r["scene_id"], r["xml_path"], r["metadata_path"]) for r in unique.values()]
            if args.build_id and len(sources) > 1: raise ValueError("explicit build-id requires a single retrieved candidate")
        paths = build_request(sources, robot, config, collection, build_id=args.build_id, images=not args.no_images, regression=not args.no_regression, initial_frozen_dir=args.initial_snapshot)
        for path in paths: print(f"{read_json(path / 'result.json')['status']}: {path}")
        return 0 if paths and read_json(paths[-1] / "result.json")["status"] == "candidate_ready" else 1
    # ---- 下面是 run 分支 ----
    robot = load_config(RobotConfig, args.robot_config)
    task = load_config(TaskConfig, args.task_config)
    collection = load_config(CollectionConfig, args.collection_config)
    # 命令行显式参数覆盖配置文件（只覆盖这几项，其余保持冻结值）。
    overrides = {}
    if args.output_dir is not None:
        overrides["output_dir"] = str(args.output_dir.resolve())
    if args.steps is not None:
        overrides["max_steps"] = args.steps
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.no_video:
        overrides["record_video"] = False
    collection = replace(collection, **overrides)
    if args.target is not None:
        task = replace(task, target=args.target)
    from .scenes.source import SceneSource
    from .runtime.runner import run_attempt
    restoration = None
    source = None
    if args.house is not None:
        source = SceneSource.procthor(args.dataset_dir, args.house)
    elif args.scene_xml:
        source = SceneSource(args.scene_xml.stem, str(args.scene_xml.resolve()),
                             str(args.metadata.resolve()) if args.metadata else None)
    elif args.imported_scene:
        # 导入来源自带 schema 版本与 restoration 初态；忽略未知 schema。
        imported = read_json(args.imported_scene)
        if imported["schema_version"] != "1.0":
            raise ValueError("unknown imported schema")
        source = SceneSource(**imported["source"])
        restoration = imported["restoration"]
        if task.target is None:
            task = replace(task, target=imported["target"])
    if args.base and (args.snapshot or args.imported_scene):
        # 快照/导入的初态是冻结的，不允许再叠加 --base，否则就不是“独立恢复”了。
        raise ValueError("cannot alter initial base of a restored snapshot/import")
    actions = read_json(args.actions) if args.actions else None
    if actions is not None and not isinstance(actions, list):
        raise ValueError("actions file must contain JSON list")
    output = run_attempt(source, robot, task, collection, actions=actions, base=args.base,
                         restoration=restoration, frozen_dir=args.snapshot, attempt_id=args.attempt_id)
    result = read_json(output / "result.json")
    print(f"{result['status']}: {output}")
    return 0 if result["status"] == "execution_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
