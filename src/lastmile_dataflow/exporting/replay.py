"""Derived delivery rendering: original attempt stays immutable and auditable."""
from pathlib import Path
import re
import shutil
import imageio.v2 as imageio
from ..config import construct,RobotConfig
from ..stations.config import StationConfig
from ..io import read_json,write_json,file_digest
from ..validation.stations import audit_station_attempt
from .station_report import render_attempt


def render_delivery(attempt,output_dir,*,view_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',view_id): raise ValueError('unsafe delivery view id')
    attempt=Path(attempt).resolve(); audit=audit_station_attempt(attempt)
    if not audit['valid']: raise ValueError('source attempt audit failed')
    result=read_json(attempt/'result.json')
    if not result['executed_steps']: raise ValueError('no physical execution to render')
    source=read_json(attempt/'source.json'); cfg=read_json(attempt/'config.json')
    robot=construct(RobotConfig,cfg['robot']); config=construct(StationConfig,cfg['station_protocol'])
    path=Path(output_dir)/'delivery_views'/view_id;path.mkdir(parents=True,exist_ok=False)
    for name in ('replay.npz','physics.jsonl','pick_initial.json'):shutil.copyfile(attempt/name,path/name)
    write_json(path/'source.json',{'attempt':str(attempt),'artifact_manifest_sha256':file_digest(attempt/'artifacts.json'),
               'kind':'derived_measured_qpos_replay','audit':audit,'original_not_modified':True})
    class ViewRecorder:
        def __init__(self): self.path=path; self.attempt_id=attempt.name
        def video_frame(self,frames,time_s): pass  # Original 20 Hz RGB videos already exist in the source attempt.
        def observation(self,frames,*,step,time_s):
            d=path/'keyframes'/f'{step:06d}';d.mkdir(parents=True,exist_ok=False)
            for camera,pixels in frames.items(): imageio.imwrite(d/(camera+'.png'),pixels)
    render_attempt(ViewRecorder(),source['snapshot'],robot,config,result['status'],result['termination_reason'])
    write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
    return path
