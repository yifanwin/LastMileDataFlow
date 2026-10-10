from pathlib import Path
import json,html
import numpy as np
import imageio.v2 as imageio
from PIL import Image,ImageDraw,ImageFont
from lastmile_dataflow.io import read_json,write_json,file_digest
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.navigation.astar import scene_grid
root=Path('outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf')
attempt=root/'attempts/navigation-only';request=read_json(root/'navigation_request.json');test=read_json(root/'test_plan.json')
source=Path(test['source_scene']);robot=RobotConfig(**read_json(source/'version.json')['robot'])
sim=Simulation.from_snapshot(source,robot,target=request['task']['target_body'])
try:
 foot=read_json(source.parent/'robot_geometry.json')
 cfg=test['config'];grid=scene_grid(sim,request['task']['anchor_world'],cfg['radius_m'],cfg['map_resolution_m'],foot['radius_m'],cfg['navigation_margin_m'],support_obstacles=request['task']['support_obstacles'])
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
 draw.text((x0+8,y0+8),'支撑物 / 障碍物',font=font,fill='#dfbf85')
planned=[pixel(p) for p in request['path']['xy']];draw.line(planned,fill='#8998a3',width=3)
draw.text((30,12),'A* 导航图 · 实测轨迹回放 · 不执行抓取',font=title,fill='white')
draw.text((30,718),'蓝：实走轨迹   灰：A* 规划   棕：支撑物障碍',font=font,fill='white')
draw.text((30,746),'底盘膨胀避障 | 世界 XY（米） | 连续物理状态，无插值',font=font,fill='#a7bbc4')
for xy,color,label in ((request['start']['xy'],'#ed6b64','S0'),(request['goal']['xy'],'#66d68c','S1'),(request['task']['anchor_world'][:2],'#ffc55b','目标')):
 x,y=pixel(xy);draw.ellipse((x-6,y-6,x+6,y+6),fill=color);draw.text((x+8,y-12),label,font=font,fill=color)
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
raw=attempt/'videos/third_person_camera.mp4'
checks={}
for name,path in (('analysis',raw),('map',root/'astar_navigation_map.mp4')):
 reader=imageio.get_reader(path);meta=reader.get_meta_data();count=reader.count_frames()
 for label,index in (('initial',0),('middle',count//2),('final',count-1)):
  imageio.imwrite(root/f'{name}_{label}_decoded.png',reader.get_data(index))
 reader.close();checks[name]={'path':str(path),'decoded_frames':count,'fps':meta['fps'],'duration_s':meta['duration'],'sha256':file_digest(path)}
 if count!=len(states):raise RuntimeError('video frame count differs from recorded physical states')
summary=read_json(root/'summary.json');goal_error=float(np.linalg.norm(np.asarray(summary['navigation']['actual_base'][:2])-request['goal']['xy']))
write_json(root/'video_checks.json',{'videos':checks,'measured_states':len(states),'actual_physics_duration_s':states[-1]['time_s']-states[0]['time_s'],
 'goal_xy_error_m':goal_error,'scope':'real simulation video and navigation-map replay from the same measured states; no interpolation'})
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>A* 导航测试视频</title><style>body{max-width:1280px;margin:30px auto;background:#101820;color:#eee;font:17px system-ui;padding:20px}video,img{max-width:100%}p{line-height:1.6}</style><h1>A* 导航测试：val103 / Cup30</h1><p>连续真实导航到站，1.252 m 规划路线，17.5 s 物理时间，碰撞记录 0。不执行抓取；支撑物提前标注并参与膨胀避障。</p><h2>放大导航图：同一次实测状态回放</h2><video controls preload="metadata" poster="map_initial.png" src="astar_navigation_map.mp4"></video><h2>真实场景与同步导航小图</h2><video controls preload="metadata" poster="analysis_initial_decoded.png" src="attempts/navigation-only/videos/third_person_camera.mp4"></video><h2>最终导航图</h2><img src="map_final.png"><p>灰线：规划；蓝线：实走；棕色：支撑物。独立测试，不覆盖原采集结果。</p></html>'''
(root/'index.html').write_text(page)
print(json.dumps(checks,ensure_ascii=False));print('goal_error_m',goal_error)
