"""Before-begin diagnostic pose sweep; no real task motion or scene edits."""
import argparse,copy,json
from pathlib import Path
import numpy as np
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station,robot_contacts

p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();f=read_json(a.run/'frozen_config.json');rows=[]
for scene in sorted((a.run/'scenes').glob('val_*')):
    for taskdir in sorted((scene/'tasks').glob('*')):
        if not (taskdir/'stations.json').exists():continue
        task=read_json(taskdir/'task.json');sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**f['robot']),target=task['target_body'])
        try:
            for station in read_json(taskdir/'stations.json'):
                if station['geometry']!='valid':continue
                for h in f['config']['torso_heights']:
                    place_frozen_station(sim,station);initial=copy.deepcopy(station['initial']);initial['torso']=[h];sim.robot.initialize(initial);contacts=robot_contacts(sim)
                    rows.append({'scene':scene.name,'task_id':task['task_id'],'category':task['category'],'anchor_height':task['anchor_world'][2],'station':station['station_id'],'h':h,'collisions':contacts})
        finally:sim.close()
write_json(a.out,{'scope':'diagnostic unstarted pose sweep of valid initial stations, not actual execution','rows':rows})
