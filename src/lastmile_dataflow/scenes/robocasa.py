"""RoboCasa365 native scene import: freeze official kitchen instances and their pick subtask.

No ProcTHOR house parsing, robot assembly, grasp-asset filtering or scene editing happens here; the
official environment places the robot and objects. Each instance is frozen once (arguments, episode
metadata, native XML, initial state) and every later load restores exactly that state.
"""

from dataclasses import asdict, dataclass, field
import logging
from pathlib import Path

from PIL import Image

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL
from lastmile_dataflow.robots.registry import load_robot_config
from lastmile_dataflow.sim.robocasa_runtime import RoboCasaRuntime, freeze_env, make_env, native_env_kwargs, object_record
from lastmile_dataflow.utils.io import append_row, create_run, read_config, write_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoboCasaInstance:
    layout_id: int
    style_id: int
    seed: int


@dataclass(frozen=True)
class RoboCasaSceneRecipe:
    robot_config: str
    instances: list
    scene_source: str = "robocasa365"
    runtime: str = "robocasa"
    env_name: str = "PickPlaceCounterToCabinet"
    obj_groups: str = "cup"
    target: str = "obj"
    criterion: str = PICK_PROTOCOL["version"]
    previews: list = field(default_factory=lambda: ["robot0_agentview_left", "robot0_eye_in_hand"])

    def __post_init__(self):
        if (self.scene_source, self.runtime) != ("robocasa365", "robocasa"):
            raise ValueError("RoboCasa recipes use scene_source robocasa365 with the robocasa runtime")
        if self.env_name != "PickPlaceCounterToCabinet" or self.target != "obj":
            raise ValueError("first RoboCasa batch is the counter pick of PickPlaceCounterToCabinet")
        if self.criterion != PICK_PROTOCOL["version"]:
            raise ValueError(f"criterion must be {PICK_PROTOCOL['version']}")
        instances = [i if isinstance(i, RoboCasaInstance) else RoboCasaInstance(**i) for i in self.instances]
        if not instances or len(set(instances)) != len(instances):
            raise ValueError("explicit unique RoboCasa instances required")
        object.__setattr__(self, "instances", instances)


def load_robocasa_recipe(path):
    return RoboCasaSceneRecipe(**read_config(path, repo_fields=("robot_config",)))


def scene_id(recipe, instance):
    return f"robocasa365/{recipe.env_name}/layout{instance.layout_id}-style{instance.style_id}-seed{instance.seed}"


def _pick_lang(env, target):
    return f"Pick up the {env.get_obj_lang(target)} from the counter."


def _freeze_instance(recipe, robot, instance, root):
    """Create the official environment once, check the target, and freeze the resulting instance."""
    kwargs = native_env_kwargs(
        robot,
        env_name=recipe.env_name,
        layout_id=instance.layout_id,
        style_id=instance.style_id,
        seed=instance.seed,
        obj_groups=recipe.obj_groups,
    )
    env = make_env(kwargs)
    try:
        env.reset()
        meta = env.get_ep_meta()
        target = object_record(env, recipe.target)
        if recipe.obj_groups not in (target["groups"] or []) and target["category"] != recipe.obj_groups:
            raise ValueError(f"target category {target['category']} outside {recipe.obj_groups}")
        if not target["graspable"]:
            raise ValueError("target is not marked graspable by the native task")
        relative = Path("scenes") / scene_id(recipe, instance).replace("/", "__")
        freeze_env(env, kwargs, root / relative)
        return {
            "scene_id": scene_id(recipe, instance),
            "status": "accepted",
            "scene_source": recipe.scene_source,
            "runtime": recipe.runtime,
            "robot_config": recipe.robot_config,
            "scene": str(relative),
            "env_name": recipe.env_name,
            "env_class": f"{type(env).__module__}.{type(env).__name__}",
            "layout_id": int(meta["layout_id"]),
            "style_id": int(meta["style_id"]),
            "seed": instance.seed,
            "target": target,
            "objects": {name: object_record(env, name) for name in env.objects},
            "lang": meta["lang"],
            "pick_lang": _pick_lang(env, recipe.target),
            "criterion": recipe.criterion,
            "native_success": f"{recipe.env_name}._check_success (object inside cabinet and gripper far)",
            "robot_scale": robot.scale,
        }
    finally:
        env.close()


def _write_previews(record, root, recipe):
    """Reload through the runtime (never re-sampling) and save one RGB per preview camera."""
    runtime = RoboCasaRuntime(root / record["scene"], target=recipe.target)
    try:
        previews = {}
        for camera in recipe.previews:
            path = root / record["scene"] / f"preview_{camera}.png"
            Image.fromarray(runtime.render(camera)).save(path)
            previews[camera] = str(path.relative_to(root))
        state = runtime.get_state()
        write_json(root / record["scene"] / "initial_facts.json", state)
        return previews
    finally:
        runtime.close()


def build_robocasa_scenes(config, output):
    """Render on the EGL device bound by the entry script (``select_devices``)."""
    recipe = load_robocasa_recipe(config)
    robot = load_robot_config(recipe.robot_config)
    root = create_run(Path(output), {"recipe": asdict(recipe), "robot": asdict(robot)})
    (root / "scenes.jsonl").touch()
    summary = {"accepted": 0, "rejected": 0, "scene_source": recipe.scene_source, "runtime": recipe.runtime}
    for instance in recipe.instances:
        logger.info("导入 RoboCasa 实例 %s", scene_id(recipe, instance))
        try:
            record = _freeze_instance(recipe, robot, instance, root)
        except ValueError as exc:
            record = {"scene_id": scene_id(recipe, instance), "status": "rejected", "reason": str(exc)}
        else:
            record["previews"] = _write_previews(record, root, recipe)
        append_row(root / "scenes.jsonl", record)
        summary[record["status"]] += 1
    write_json(root / "summary.json", {**summary, "status": "completed"})
    return root
