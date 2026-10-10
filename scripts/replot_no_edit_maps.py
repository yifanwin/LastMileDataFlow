#!/usr/bin/env python3
"""Re-render measured no-edit results into a NEW directory; never run robot motion."""
import argparse
from dataclasses import asdict
from pathlib import Path
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.io import read_json,write_json,file_digest
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.navigation.astar import scene_grid,task_support_obstacles
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.exporting.no_edit_heatmap import export_heatmap
from lastmile_dataflow.exporting.navigation_map import export_navigation_map


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-dir',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args()
    config=load_no_edit_config(args.config)
    tp=args.task_dir.resolve();scene=tp.parents[1]
    task=read_json(tp/'task.json');rows=read_json(tp/'station_statistics.json')
    version=read_json(scene/'scene/version.json')
    footprint=read_json(scene/'robot_geometry.json')
    args.output_dir.mkdir(parents=True,exist_ok=False)
    sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**version['robot']),target=task['target_body'])
    try:
        obstacles=task_support_obstacles(sim,task)
        options={'support_obstacles':obstacles}
        heat=scene_grid(sim,task['anchor_world'],config.radius_m,config.map_resolution_m,0.,0.,**options)
        nav=scene_grid(sim,task['anchor_world'],config.radius_m,config.map_resolution_m,
                       footprint['radius_m'],config.navigation_margin_m,**options)
    finally:sim.close()
    sigma=config.smoothing_sigma_m or (config.spacing_m or footprint['radius_m'])
    export_navigation_map(args.output_dir,nav,task['anchor_world'],display_grid=heat)
    export_heatmap(args.output_dir,heat,rows,task['anchor_world'],sigma,
                   max_support_distance_m=config.smoothing_support_distance_m,
                   title=task['task_id']+' | existing trials; display-only replot')
    write_json(args.output_dir/'replot_source.json',{'task_dir':str(tp),'config':asdict(config),
               'statistics_sha256':file_digest(tp/'station_statistics.json'),
               'scope':'visualization of existing trials only; no new navigation or physics execution'})
    print(args.output_dir.resolve())


if __name__ == '__main__':main()
