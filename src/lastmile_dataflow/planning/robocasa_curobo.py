"""cuRobo side of the RoboCasa evaluator (main .venv): plan approach -> grasp -> lift from exported problems.

Run as a worker: one JSON line per request on stdin ({"problem": path, "plan": path, "seed": int}),
one JSON line per result on stdout. The robot config is built once; the world is reloaded per problem.
"""
import json
import sys
import time
import types
from dataclasses import dataclass

import numpy as np

from lastmile_dataflow.planning.result import GRASP_APPROACH_DISTANCE_M
from lastmile_dataflow.robots.panda_omron import planning as PM
from lastmile_dataflow.robots.planning_common import chain_state, retime_points
from lastmile_dataflow.sim.model.geometry import pose7

ARM_JOINTS = [f"panda_joint{k}" for k in range(1, 8)]
LIFT_M = 0.10
GRASP_ROUNDS = 3  # goalset picks a grasp; a later-stage failure masks it and replans
FAR_M = 10.0  # masked grasps are moved out of reach, keeping the goalset size fixed
CONTROL_HZ = 20
TIME_DILATION = 0.5
STAGES = ("approach", "grasp", "lift")


@dataclass
class PlanOutcome:
    success: bool
    status: str
    failed_stage: str  # "" on success; goalset / approach / grasp / lift
    grasp_index: int
    rounds: int
    planning_s: float


def _planner_class():
    from curobo.motion_planner import MotionPlanner
    from curobo.types import JointState

    class Planner(MotionPlanner):
        def plan_pose(self, goal, state, **kwargs):
            kwargs.setdefault("max_attempts", 5)
            # v0.8.0: trajectory-end JointStates (plan_grasp chains them) poison IK buffers for every later
            # query; a fresh position-only state avoids it. chain_state fixes the knot joint names.
            state = JointState.from_position(state.position.clone(), joint_names=list(state.joint_names))
            result = super().plan_pose(goal, state, **kwargs)
            if result is not None and result.js_solution is not None:
                chain_state(result.js_solution)
            return result

    return Planner


class RoboCasaGraspPlanner:
    def __init__(self, pool_size, *, ik_seeds=64, trajopt_seeds=12):
        PM.ASSETS = PM.ASSETS.parent / "assets_robocasa"
        PM.robot_description.cache_clear()
        from curobo._src.geom.types import Mesh, SceneCfg
        from curobo.motion_planner import MotionPlannerCfg
        from curobo.types import DeviceCfg

        self.dev = DeviceCfg()
        self.pool_size = pool_size
        self.worlds = 0
        robot = types.SimpleNamespace(arm_positions=lambda s: np.zeros(7), group=lambda n: np.array([0.04, -0.04]), lift_target=0.0)
        placeholder = SceneCfg(mesh=[Mesh(name="env", pose=[0, 0, 0, 1, 0, 0, 0], vertices=[[9, 9, 9], [9.1, 9, 9], [9, 9.1, 9]], faces=[[0, 1, 2]])])
        cfg = MotionPlannerCfg.create(
            robot=PM.configuration(robot), scene_model=placeholder, device_cfg=self.dev, max_batch_size=1, multi_env=False,
            num_ik_seeds=ik_seeds, num_trajopt_seeds=trajopt_seeds, max_goalset=pool_size, self_collision_check=True,
            optimizer_collision_activation_distance=0.01, use_cuda_graph=False,
        )
        self.mp = _planner_class()(cfg)
        self.mp.warmup(enable_graph=False, num_warmup_iterations=1)

    def _load_world(self, problem):
        from curobo._src.geom.types import Mesh, SceneCfg

        # cuRobo caches warp meshes by name: a reused name keeps the previous geometry
        self.worlds += 1
        mesh = Mesh(name=f"env{self.worlds}", pose=[0, 0, 0, 1, 0, 0, 0], vertices=problem["vertices"].tolist(), faces=problem["faces"].tolist())
        self.mp.update_world(SceneCfg(mesh=[mesh]))

    def _goals(self, matrices):
        from curobo.types import GoalToolPose

        v = np.array([pose7(T) for T in matrices], dtype=float)
        n = len(v)
        return GoalToolPose(
            tool_frames=self.mp.tool_frames,
            position=self.dev.to_device(v[:, :3]).view(1, 1, 1, n, 3),
            quaternion=self.dev.to_device(v[:, 3:]).view(1, 1, 1, n, 4),
        )

    def _points(self, trajectory, last):
        raw = trajectory.reorder(ARM_JOINTS).position.detach().cpu().numpy().reshape(-1, 7)
        if last is not None:
            raw = raw[: int(last.reshape(-1)[0].item())]
        return retime_points(raw, float(trajectory.dt.reshape(-1)[0].item()), CONTROL_HZ, TIME_DILATION)

    def plan(self, problem, seed):
        from curobo.types import JointState

        started = time.monotonic()
        self._load_world(problem)
        self.mp.reset_seed()
        np.random.seed(seed)
        goals = np.array(problem["goals_base"], dtype=float)
        if len(goals) != self.pool_size:
            raise ValueError(f"problem has {len(goals)} grasps, planner built for {self.pool_size}")
        start = JointState.from_position(self.dev.to_device(problem["arm"].astype(np.float32)).view(1, -1), joint_names=ARM_JOINTS)
        failed, status, index = "goalset", "", -1
        round_limit = 1 if self.pool_size == 1 else GRASP_ROUNDS
        for rounds in range(1, round_limit + 1):
            r = self.mp.plan_grasp(
                self._goals(goals), start, grasp_approach_offset=-GRASP_APPROACH_DISTANCE_M, grasp_lift_offset=LIFT_M,
                grasp_lift_in_tool_frame=False, plan_approach_to_grasp=True, plan_grasp_to_lift=True, disable_collision_links=[],
            )
            status = str(r.status)
            if r.goalset_index is None:
                failed = "goalset"
                break
            index = int(r.goalset_index.reshape(-1)[0].item())
            if bool(r.success.any().item()):
                stages = {s: self._points(getattr(r, s + "_interpolated_trajectory"), getattr(r, s + "_interpolated_last_tstep")) for s in STAGES}
                return PlanOutcome(True, status, "", index, rounds, time.monotonic() - started), stages
            failed = "approach" if "approach" in status else "grasp" if "grasp pose" in status else "lift"
            if rounds < round_limit:
                goals[index, :3, 3] += FAR_M
        return PlanOutcome(False, status, failed, index, rounds, time.monotonic() - started), {}


def serve():
    planner = None
    for line in sys.stdin:
        request = json.loads(line)
        with np.load(request["problem"]) as problem:
            problem = dict(problem)
        if planner is None:
            planner = RoboCasaGraspPlanner(len(problem["goals_base"]))
        outcome, stages = planner.plan(problem, int(request["seed"]))
        np.savez(request["plan"], **{s: stages[s] for s in stages}, outcome=json.dumps(outcome.__dict__))
        print("RESULT " + json.dumps(outcome.__dict__), flush=True)


if __name__ == "__main__":
    serve()
