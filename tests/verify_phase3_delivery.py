"""Offline full video decoding and exact source-time verification (not a physics test)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import numpy as np
from lastmile_dataflow.io import read_json,write_json,file_digest
from lastmile_dataflow.exporting.collection import verify_manifest
from lastmile_dataflow.validation.stations import audit_station_attempt


def decode(job):
    path,expected=job
    start=time.monotonic()
    info=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                         'stream=width,height,r_frame_rate','-of','json',str(path)],capture_output=True,text=True,check=True)
    # Decode EVERY frame; -xerror makes decoding errors fail instead of hiding them.
    proc=subprocess.run(['ffmpeg','-v','error','-xerror','-threads','2','-i',str(path),
                         '-an','-progress','pipe:1','-f','null','-'],capture_output=True,text=True)
    frames=[int(line.split('=',1)[1]) for line in proc.stdout.splitlines() if line.startswith('frame=')]
    count=frames[-1] if frames else 0
    return {'path':str(path.resolve()),'sha256':file_digest(path),'full_decode':True,
            'frames':count,'expected_frames':expected,'stream':json.loads(info.stdout)['streams'][0],
            'valid':proc.returncode==0 and count==expected and not proc.stderr.strip(),
            'decoder_errors':proc.stderr,'wall_time_s':time.monotonic()-start}


def mapping(path):
    video=read_json(path/'delivery_video.json')
    with np.load(path/'replay.npz',allow_pickle=False) as z:
        times=z['times'];phases=z['phases']
    previous=-1
    for i,frame in enumerate(video['frames']):
        index=frame['source_control_index']
        assert index>previous and frame['frame']==i
        assert float(times[index])==frame['actual_time_s'] and str(phases[index])==frame['phase']
        assert frame['speed']==(4 if frame['phase']=='torso_adjust' else 1)
        previous=index
    assert previous==len(times)-1 and video['no_interpolation'] and video['not_navigation']
    return len(video['frames'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--collection',type=Path,required=True)
    parser.add_argument('--derived',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();start=time.monotonic();verify_manifest(args.collection)
    rows=read_json(args.collection/'stations.json');jobs=[];audits={};mappings=[]
    for name in dict.fromkeys(row['attempt'] for row in rows if row.get('attempt')):
        path=Path(name);audit=audit_station_attempt(path);assert audit['valid'],audit;audits[name]=audit
        if not audit['executed_steps']:continue
        for camera,video in read_json(path/'videos.json')['cameras'].items():
            jobs.append((path/'videos'/f'{camera}.mp4',video['frame_count']))
        frames=mapping(path);jobs.append((path/'videos/delivery.mp4',frames));mappings.append(name)
    if args.derived:
        path=args.derived;verify_manifest(path);source=read_json(path/'source.json');attempt=Path(source['attempt'])
        assert file_digest(attempt/'artifacts.json')==source['artifact_manifest_sha256']
        for name in ('replay.npz','physics.jsonl','pick_initial.json'):
            assert file_digest(path/name)==file_digest(attempt/name)
        jobs.append((path/'videos/delivery.mp4',mapping(path)));mappings.append(str(path))
    with ThreadPoolExecutor(max_workers=3) as pool:videos=list(pool.map(decode,jobs))
    result={'valid':all(v['valid'] for v in videos),'independent_attempt_audits':audits,
            'exact_replay_time_phase_mappings':mappings,'videos':videos,'wall_time_s':time.monotonic()-start,
            'method':'ffmpeg full decoding with -xerror, frame count, SHA256, source time/phase mapping; not visual grasp inference'}
    write_json(args.output,result);print(json.dumps({'valid':result['valid'],'attempts':len(audits),'videos':len(videos),'frames':sum(v['frames'] for v in videos)}))
    if not result['valid']:raise SystemExit(1)


if __name__=='__main__':main()
