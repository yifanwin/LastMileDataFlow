from pathlib import Path
import json,html
import numpy as np
import imageio.v2 as imageio
from PIL import Image,ImageDraw,ImageFont
from lastmile_dataflow.io import read_json,write_json,file_digest
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.navigation.astar import scene_grid
import argparse
parser=argparse.ArgumentParser(description='Re-render existing navigation states with uninflated geometry, no new physics')
parser.add_argument('--source-dir',required=True,type=Path)
parser.add_argument('--output-dir',required=True,type=Path)
args=parser.parse_args()
source_root=args.source_dir.resolve();root=args.output_dir.resolve();root.mkdir(parents=True,exist_ok=False)
attempt=source_root/'attempts/navigation-only';request=read_json(source_root/'navigation_request.json');test=read_json(source_root/'test_plan.json')
source=Path(test['source_scene']);robot=RobotConfig(**read_json(source/'version.json')['robot'])
sim=Simulation.from_snapshot(source,robot,target=request['task']['target_body'])
try:
 foot=read_json(source.parent/'robot_geometry.json')
 cfg=test['config'];grid=scene_grid(sim,request['task']['anchor_world'],cfg['radius_m'],cfg['map_resolution_m'],0.,0.,support_obstacles=request['task']['support_obstacles'])
finally:sim.close()
rows=[json.loads(line) for line in (attempt/'trajectory.jsonl').read_text().splitlines()]
states=[rows[0]['state_before']]+[row['state_after'] for row in rows]
font=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',18)
title=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',24)
left,top,size=80,84,600
h,w=grid.free.shape;lo=grid.origin-.5*grid.resolution;span=np.array([w,h])*grid.resolution
def pixel(xy):
 v=(np.asarray(xy)-lo)/span
 return left+round(v[0]*size),top+size-round(v[1]*size)
base=Image.new('RGB',(800,780),'#101820')
raster=Image.fromarray(np.uint8(np.where(grid.free[::-1,:,None],[240,244,246],[30,41,47]))).resize((size,size),Image.Resampling.NEAREST)
base.paste(raster,(left,top));draw=ImageDraw.Draw(base)
for obs in request['task']['support_obstacles']:
 x0,y1=pixel(obs['min'][:2]);x1,y0=pixel(obs['max'][:2]);draw.rectangle((x0,y0,x1,y1),fill='#493d2d',outline='#b99a62',width=2)
planned=[pixel(p) for p in request['path']['xy']];draw.line(planned,fill='#8998a3',width=3)
draw.text((30,12),'A* 导航图 · 实测轨迹回放 · 不执行抓取',font=title,fill='white')
draw.text((30,718),'蓝：实走轨迹   灰：A* 规划   棕：家具轮廓',font=font,fill='white')
draw.text((30,746),'实际几何轮廓，未显示规划膨胀 | 实测状态，无插值',font=font,fill='#a7bbc4')
for xy,color,label in ((request['start']['xy'],'#ed6b64','S0'),(request['goal']['xy'],'#66d68c','S1'),(request['task']['anchor_world'][:2],'#ffc55b','目标')):
 x,y=pixel(xy)
 if label in ('S0','S1'):
  rx,ry=foot['radius_m']*size/span
  draw.ellipse((x-rx,y-ry,x+rx,y+ry),outline=color,width=3)
  draw.ellipse((x-4,y-4,x+4,y+4),fill=color)
  draw.text((x+rx+8,y-12),label,font=font,fill=color)
 else:
  draw.ellipse((x-6,y-6,x+6,y+6),fill=color)
  draw.text((x+8,y-12),label,font=font,fill=color)
trail=[];fps=robot.control_hz
with imageio.get_writer(root/'astar_navigation_map.mp4',fps=fps,codec='libx264',macro_block_size=2,ffmpeg_log_level='error') as writer:
 for index,state in enumerate(states):
  frame=base.copy();d=ImageDraw.Draw(frame);point=pixel(state['robot']['base'][:2]);trail.append(point)
  if len(trail)>1:d.line(trail,fill='#72d8ea',width=4)
  d.ellipse((point[0]-5,point[1]-5,point[0]+5,point[1]+5),fill='white')
  d.text((30,48),f"真实仿真时间 {state['time_s']:.2f} s | 帧 {index+1}/{len(states)} | 碰撞记录 0",font=font,fill='#72d8ea')
  writer.append_data(np.asarray(frame))
  if index==0:frame.save(root/'map_initial.png')
  if index==len(states)-1:frame.save(root/'map_final.png')
checks={}
for name,path in (('map',root/'astar_navigation_map.mp4'),):
 reader=imageio.get_reader(path);meta=reader.get_meta_data();count=reader.count_frames()
 for label,index in (('initial',0),('middle',count//2),('final',count-1)):
  imageio.imwrite(root/f'{name}_{label}_decoded.png',reader.get_data(index))
 reader.close();checks[name]={'path':str(path),'decoded_frames':count,'fps':meta['fps'],'duration_s':meta['duration'],'sha256':file_digest(path)}
 if count!=len(states):raise RuntimeError('video frame count differs from recorded physical states')
summary=read_json(source_root/'summary.json');goal_error=float(np.linalg.norm(np.asarray(summary['navigation']['actual_base'][:2])-request['goal']['xy']))
write_json(root/'video_checks.json',{'videos':checks,'measured_states':len(states),'actual_physics_duration_s':states[-1]['time_s']-states[0]['time_s'],
 'station_footprint':foot,'station_circle_inflation_m':0.,'station_radius_pixels':(foot['radius_m']*size/span).tolist(),
 'goal_xy_error_m':goal_error,'source_dir':str(source_root),'display':'physical_footprints_no_inflation_no_labels','planner_and_trajectory_unchanged':True,
 'scope':'existing actual navigation states replay only; no new physics or interpolation'})
page='<html lang="zh-CN"><meta charset="utf-8"><title>A* 导航图：实际轮廓</title><style>body{max-width:1000px;margin:30px auto;font:17px system-ui;background:#101820;color:white}video,img{max-width:100%}</style><h1>A* 导航图 · 实际几何轮廓</h1><p>显示实际轮廓，不显示底盘安全膨胀；原 A* 规划和实测轨迹未改动。没有新增物理执行。S0/S1 圆圈按底盘和车轮实际外接圆半径绘制，不含导航安全余量。</p><video controls preload="metadata" poster="map_initial.png" src="astar_navigation_map.mp4"></video><h2>末帧</h2><img src="map_final.png"></html>'
(root/'index.html').write_text(page)
print(json.dumps(checks,ensure_ascii=False));print('goal_error_m',goal_error)
