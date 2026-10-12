"""Build a native MolmoBot final-point input package from a transplanted snapshot."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.workflows.native_station_input import revalidate_native_stations

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--variant',required=True,type=Path)
    p.add_argument('--native-scene',required=True,type=Path)
    p.add_argument('--config',default=ROOT/'configs/no_edit/molmobot.json',type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    print(revalidate_native_stations(a.variant,a.native_scene,load_no_edit_config(a.config),a.output))
