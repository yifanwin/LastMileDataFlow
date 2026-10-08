"""cuRobo IK without environment collision; torso is sampled identically for all conditions."""
from pathlib import Path
import numpy as np
from .curobo import locked_arm_config,body_pose,pose7


class NativeReach:
    def __init__(self,sim,config,side):
        import torch
        import yaml
        from curobo.types.base import TensorDeviceType
        from curobo.wrap.reacher.ik_solver import IKSolver,IKSolverConfig
        if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; reach remains unknown')
        root=Path(config.robot_planner_dir); cfg=locked_arm_config(yaml.safe_load((root/'rby1m_holobase.yml').read_text()),sim,side,root)
        self.args=TensorDeviceType(); self.base=body_pose(sim,sim.model.body('robot_0/base').id)
        self.solver=IKSolver(IKSolverConfig.load_from_robot_config(cfg['robot_cfg'],None,self.args,
            num_seeds=config.num_ik_seeds,use_cuda_graph=False,self_collision_check=False,
            self_collision_opt=False,seed=config.seed))

    def solve(self,goal):
        from curobo.types.math import Pose
        result=self.solver.solve_single(Pose.from_list(pose7(np.linalg.solve(self.base,goal)),self.args))
        return 'success' if result.success is not None and bool(result.success.any().item()) else 'no_solution'
