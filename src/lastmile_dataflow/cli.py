"""单一命令入口。默认只执行有界短动作，不调用 Agent 或 GPU 规划器。"""
import argparse
from dataclasses import replace
from pathlib import Path

from .config import CollectionConfig, RobotConfig, TaskConfig, load_config
from .io import read_json

ROOT = Path(__file__).resolve().parents[2]


def parser():
    p = argparse.ArgumentParser(prog="lastmile-dataflow")
    sub = p.add_subparsers(dest="command", required=True)
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
    feedback.add_argument("--classification", required=True, choices=["scene_invalid", "success_without_expected_difficulty", "valid_unsolved", "infrastructure_failure"])
    feedback.add_argument("--evidence", required=True, type=Path)
    feedback.add_argument("--new-build-id")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
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
    robot = load_config(RobotConfig, args.robot_config)
    task = load_config(TaskConfig, args.task_config)
    collection = load_config(CollectionConfig, args.collection_config)
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
        imported = read_json(args.imported_scene)
        if imported["schema_version"] != "1.0":
            raise ValueError("unknown imported schema")
        source = SceneSource(**imported["source"])
        restoration = imported["restoration"]
        if task.target is None:
            task = replace(task, target=imported["target"])
    if args.base and (args.snapshot or args.imported_scene):
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
