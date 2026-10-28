"""PyRUA host for a running project episode, in place of RPent's RoboCasa boot.

`pyrualean.play.play_episode` drives the agent unchanged (prompt, MCP tools,
Codex/Claude/OpenAI runtime, budgets, ledger, result.json). Its host contract
is `boot` -> (toolkit, daemons) and `dump_card`; here `boot` builds RPent's
own `RoboCasaToolkit` and primitives over `ProjectEnvClient` instead of a
robosuite env server, so every primitive runs its upstream algorithm against
the project Simulation. Upstream text that describes the RoboCasa kitchen or
its success predicate is edited explicitly; each edit must match once.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import inspect
import math
import os
from pathlib import Path
import shutil
import time
from unittest import mock

import numpy as np

from pyrualean import play as pyrua_play
from pyrualean.hosts.common import RpentBackend
from pyrualean.prompt import TaskCard, knowledge_text
from pyrualean.robocasa import ADAPTER as ROBOCASA_ADAPTER, RobocasaRobot
from robots.robocasa.env_client import CAM_ALIAS, RoboCasaEnvClient
from robots.robocasa.toolkit import RoboCasaToolkit
from rpent.dashboard.events import NullDashboardEventSink
from rpent.memory import MemoryManager
from rpent.utils.logging import init_output_dir

ROBOT = 'lastmile_panda'
SCENE_EDITS = (
    ("in a RoboCasa365 kitchen.", "in a LastMileDataFlow house scene."),
    ("x/y span the kitchen", "x/y span the house"),
    ("Counters are at about z = 0.9 m; read every height from the\nworld maps, never assume one.",
     "Support heights differ between furniture; read every height from\nthe world maps, never assume one."),
    ("Success is the benchmark's own predicate", "Success is the project's physical predicate"),
    ("``state().task_progress`` shows the values that predicate\ncomputes and ``success_criteria()`` its source.",
     "``state().task_progress`` shows the values that predicate\ncomputes and ``success_criteria()`` states it."),
)
KNOWLEDGE_EDITS = (
    ("# Operating knowledge for RoboCasa365:", "# Operating knowledge for the RoboCasa robot API on LastMileDataFlow scenes:"),
    ("  mobile base, in a kitchen with counters, cabinets, drawers, a stove, a sink,\n"
     "  a fridge and a microwave.  World frame: x/y span the kitchen (coordinates\n"
     "  of several metres), z up; metres and radians; quaternions are\n"
     "  `[x, y, z, w]`.  Counters are at about z = 0.9 m; objects sit on counters,\n"
     "  inside cabinets or in drawers.",
     "  mobile base, in a furnished house scene (tables, counters, shelves,\n"
     "  dressers, chairs; obstacles may stand on the floor).  World frame: x/y span\n"
     "  the house (coordinates of several metres), z up; metres and radians;\n"
     "  quaternions are `[x, y, z, w]`.  Support heights differ between furniture\n"
     "  (a table is often near 0.75 m, a counter near 0.9 m): measure them from\n"
     "  the world maps."),
    ("  change with the seed.", "  change with the scene."),
    ("  environment's own predicate (`state().success`, `robo.done`);\n"
     "  `state().task_progress` shows the live values that predicate computes and\n"
     "  `success_criteria()` its source.",
     "  project's physical predicate (`state().success`, `robo.done`);\n"
     "  `state().task_progress` shows its live values and `success_criteria()`\n"
     "  states it with its thresholds."),
    ("(0.85-0.95 for objects on a counter)", "(about 0.03-0.12 m above the measured support surface)"),
)


def edit(text, edits):
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f'upstream text changed; edit does not match once: {old[:60]!r}')
        text = text.replace(old, new)
    return text


class LastMilePandaRobot(RobocasaRobot):
    __doc__ = edit(inspect.getdoc(RobocasaRobot), SCENE_EDITS)
    VLA_REFERENCE_EDITS = tuple((old.replace("benchmark's own predicate", "project's physical predicate"),
                                 new.replace("benchmark's own predicate", "project's physical predicate"))
                                for old, new in RobocasaRobot.VLA_REFERENCE_EDITS)


ADAPTER = replace(ROBOCASA_ADAPTER, name=ROBOT, robot_cls=LastMilePandaRobot, host=__name__,
                  blurb='a mobile-base PandaOmron robot in a LastMileDataFlow house scene')


def lastmile_knowledge(name='robocasa', primitives='full'):
    return edit(knowledge_text('robocasa', primitives), KNOWLEDGE_EDITS)


def unmap_action(input_action):
    """robocasa (RLinf rpent branch) `PandaOmronKeyConverter.unmap_action`, copied verbatim.

    RLDX-1 actions go through the evaluation's own conversion; the robocasa
    package itself (and its kitchen assets) is not installed here.
    """
    output_action = {
        "robot0_right_gripper": (-1.0 if input_action["action.gripper_close"] < 0.5 else 1.0),
        "robot0_right": np.concatenate((input_action["action.end_effector_position"],
                                        input_action["action.end_effector_rotation"]), axis=-1),
        "robot0_base": input_action["action.base_motion"][..., 0:3],
        "robot0_torso": input_action["action.base_motion"][..., 3:4],
        "robot0_base_mode": (-1.0 if input_action["action.control_mode"] < 0.5 else 1.0),
    }
    return output_action


# PandaOmron HYBRID_MOBILE_BASE `_action_split_indexes` order, then base mode.
ACTION_PARTS = (('robot0_right', 6), ('robot0_right_gripper', 1), ('robot0_base', 3), ('robot0_torso', 1))


class ProjectEnvClient(RoboCasaEnvClient):
    """RPent's RoboCasa env client contract, served by the project episode in-process.

    Images are already top-down (MuJoCo renderer), so no flip is applied.
    `reset` returns the current observation: the project placed the robot
    before `Simulation.begin()` and physics is never reset afterwards.
    """

    def __init__(self, environment, *, size=256):
        self._client = None
        self.environment, self.owner = environment, environment.owner
        self.camera_h = self.camera_w = size
        self.last_obs = self._call(environment.observation)

    def _call(self, fn, *args, **kwargs):
        return self.owner.call(fn, *args, **kwargs)

    def reset(self):
        self.last_obs = self._call(self.environment.observation)
        return self.last_obs

    def step(self, flat_action):
        self.last_obs = self._call(self.environment.step, np.asarray(flat_action, float))
        return self.last_obs, 0., False, {}

    def chunk_step(self, flat_actions, *, return_all_frames=False):
        raise NotImplementedError('RoboCasa primitives step one action at a time')

    def check_success(self):
        return bool(self._call(self.environment.success))

    def get_task_language(self):
        return self.environment.language

    def get_success_criteria_text(self):
        return self._call(self.environment.success_criteria)

    def get_task_progress(self):
        return self._call(self.environment.progress)

    def render_camera(self, camera_name, height=None, width=None, depth=False):
        return self._call(self.environment.render, CAM_ALIAS.get(camera_name, camera_name),
                          height or self.camera_h, width or self.camera_w, depth)

    def get_camera_meta(self, camera_name, height=None, width=None):
        return self._call(self.environment.camera_meta, CAM_ALIAS.get(camera_name, camera_name),
                          height or self.camera_h, width or self.camera_w)

    def world_map(self, camera_name, height=None, width=None):
        return self._call(self.environment.world_map, CAM_ALIAS.get(camera_name, camera_name),
                          height or self.camera_h, width or self.camera_w)

    def grasp_contact(self):
        return self._call(self.environment.grasp_contact)

    def reassemble_env_action(self, unmap_result):
        parts = [np.asarray(unmap_result.pop(name), float).reshape(size) for name, size in ACTION_PARTS]
        return np.concatenate([*parts, [float(unmap_result.pop('robot0_base_mode'))]])


class ProjectBackend(RpentBackend):
    """Counts project control steps; RPent's own frame recording stays off (one trajectory)."""

    def __init__(self, toolkit):
        super().__init__(toolkit)
        self.environment = toolkit.lastmile_environment

    def env_steps(self):
        return self.environment.steps


def make_backend(toolkit):
    return ProjectBackend(toolkit)


@dataclass(frozen=True)
class Cell:
    task: str
    seed: int
    max_episode_steps: int
    suite: str = 'lastmile'
    cuda_device: int | None = None

    @property
    def tag(self):
        return f'{self.task}_s{self.seed}'


def boot(cell, out_dir, *, rpent_root=None, env_only=False, environment, vla_client):
    """Build RPent's RoboCasa toolkit over the running project episode."""
    memory = out_dir/'_memory'
    memory.mkdir(parents=True, exist_ok=True)
    (memory/'MEMORY.md').touch()
    client = ProjectEnvClient(environment)
    os.environ.pop('RLDX_ALLOW_RESET', None)   # evaluation: the primitives' reset stays refused
    # RoboCasaToolkit roots its EnvState at RPent's process-global output dir.
    init_output_dir(out_dir/'rpent', verbose=False)
    toolkit = RoboCasaToolkit(
        runtime_kwargs={'env_client': client, 'workdir': str(out_dir/'rpent'), 'hi_res': None,
                        'vla_client': vla_client},
        dashboard_events=NullDashboardEventSink(), memory=MemoryManager(memory))
    primitives = toolkit._primitives
    primitives.stop_recording()        # the project recorder owns the only trajectory and video
    primitives._rldx._unmap = unmap_action
    toolkit.lastmile_environment = environment
    return toolkit, []


def _yaw(quat_xyzw):
    x, y, z, w = quat_xyzw
    return math.atan2(2.*(w*z+x*y), 1.-2.*(y*y+z*z))


def _fmt(values):
    return '[' + ', '.join(f'{float(v):.3f}' for v in values) + ']'


def dump_card(toolkit, cell, out_dir):
    """Task card from the step-0 envelope: instruction, robot pose, images; no object poses."""
    view = toolkit.execute_tool('view_env_state', {'step': 0}).result
    raw = view.get('state') or {}
    card_dir = out_dir/'card'
    card_dir.mkdir(exist_ok=True)
    images = {}
    for camera in ('agentview', 'navview', 'wrist'):
        source = toolkit.state.artifact_path(f'{camera}.png', step=0)
        target = card_dir/f'{camera}.png'
        shutil.copyfile(source, target)
        images[camera] = str(target)
    qpos = raw['robot0_gripper_qpos']
    yaw = _yaw(raw['robot0_base_quat'])
    facts = {
        'initial base position [x, y, z]': f"{_fmt(raw['robot0_base_pos'])} m",
        'initial base heading (yaw about world z)': f'{yaw:.3f} rad ({math.degrees(yaw):.1f} deg)',
        'initial gripper (EEF) position [x, y, z]': f"{_fmt(raw['robot0_eef_pos'])} m",
        'initial gripper orientation [x, y, z, w]': _fmt(raw['robot0_eef_quat']),
        'initial gripper opening (|q0| + |q1|)': f'{abs(qpos[0])+abs(qpos[1]):.3f} (about 0.08 fully open)',
        'navview': 'render-only camera at the RoboCasa navview pose on the base (no MJCF camera)',
    }
    if view.get('task_progress'):
        facts['task_progress at step 0'] = str(view['task_progress'])
    card = TaskCard(suite=cell.suite, task=cell.task, seed=cell.seed,
                    task_language=str(view.get('task_language') or ''), object_names=[], images=images,
                    max_env_steps=cell.max_episode_steps, robot=ROBOT, facts=facts)
    (out_dir/'card.json').write_text(card.to_json()+'\n', encoding='utf-8')
    return card


@contextmanager
def _working_directory():
    previous = os.getcwd()   # play_episode chdirs into its workspace
    try:
        yield
    finally:
        os.chdir(previous)


def play(policy, environment, out_dir, *, seed):
    """One `play_episode` against the project episode; returns PyRUA's result.json."""
    config = policy.config
    task = environment.task
    cell = Cell(task=f"{task['operation']}:{task.get('category') or 'object'}", seed=seed,
                max_episode_steps=config.control_step_budget)
    budget_s = max(1., environment.deadline - time.monotonic())
    options = {'environment': environment, 'vla_client': None if policy.vla is None else policy.vla.client}
    with mock.patch.multiple(pyrua_play, get_adapter=lambda name: ADAPTER, knowledge_text=lastmile_knowledge), \
            _working_directory():
        return pyrua_play.play_episode(
            cell, out_dir, arm=config.arm, model=config.model, reasoning_effort=config.reasoning_effort,
            budget_s=budget_s, codex_bin=config.codex_bin, rpent_root=config.rpent_root,
            images=config.images, feedback=config.feedback, codex_home=config.codex_home,
            max_turns=config.max_turns, robot=ROBOT, boot_options=options, runtime=config.runtime,
            primitives=config.primitives)
