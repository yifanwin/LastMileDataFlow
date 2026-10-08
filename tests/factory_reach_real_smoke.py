"""Explicit native RBY/cuRobo smoke. Not a reach-table calibration or task success."""
import argparse
from pathlib import Path
from types import SimpleNamespace
import tempfile

from lastmile_dataflow.config import RobotConfig,load_config
from lastmile_dataflow.io import write_json
from lastmile_dataflow.scenes.source import SceneSource
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.planning.reach import NativeReach
from lastmile_dataflow.planning.curobo import tcp_pose


def main():
    p=argparse.ArgumentParser(); p.add_argument('--robot-config',type=Path,default=Path('configs/robots/rby1.json'))
    p.add_argument('--planner-dir',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False); sim=None
    try:
        with tempfile.TemporaryDirectory() as d:
            scene=Path(d)/'fixture.xml'; scene.write_text('<mujoco><worldbody><geom type="plane" size="1 1 .01" name="floor"/></worldbody></mujoco>')
            robot=load_config(RobotConfig,args.robot_config); sim=Simulation.from_source(SceneSource('native-robot-IK-only',str(scene)),robot)
            config=SimpleNamespace(robot_planner_dir=str(args.planner_dir.resolve()),num_ik_seeds=32,seed=0)
            ik=NativeReach(sim,config,'left'); near=tcp_pose(sim,'left'); far=near.copy(); far[0,3]+=10.
            results={'measured_fk_pose':ik.solve(near),'10m_offset_pose':ik.solve(far)}
            packet={'status':'pass' if results=={'measured_fk_pose':'success','10m_offset_pose':'no_solution'} else 'fail',
                    'results':results,'scope':'native RBY-1 IK smoke only; not table calibration or execution',
                    'num_ik_seeds':32,'seed':0,'real_task_success':'unknown','finite_budget_not_impossibility':True}
    except Exception as exc:
        packet={'status':'infrastructure_error','error_type':type(exc).__name__,'reason':str(exc)[:1000]}
    finally:
        if sim is not None: sim.close()
    write_json(args.output/'result.json',packet); print(packet)
    return 0 if packet['status']=='pass' else 1


if __name__=='__main__': raise SystemExit(main())
