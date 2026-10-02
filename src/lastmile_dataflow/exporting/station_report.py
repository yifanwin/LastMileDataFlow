"""Figures and videos from actual recorded states; no generated robot motion."""
import csv
import json
import html
from pathlib import Path
import numpy as np
import mujoco
import imageio.v2 as imageio
from ..io import read_json,write_json,file_digest
from ..runtime.simulation import Simulation


def render_attempt(recorder,snapshot,robot,config,status,reason):
    from PIL import Image,ImageDraw,ImageFont
    with np.load(recorder.path/'replay.npz',allow_pickle=False) as z:
        times=z['times']; qpos=z['qpos']; phases=z['phases']
    sim=Simulation.from_snapshot(snapshot,robot,target=config.target)
    renderer=mujoco.Renderer(sim.model,height=config.height,width=config.width)
    scene_renderer=mujoco.Renderer(sim.model,height=600,width=960)
    composite=None; metadata=[]
    physics=[json.loads(line) for line in (recorder.path/'physics.jsonl').read_text().splitlines()]
    physics_times=np.array([row['time_s'] for row in physics])
    initial=read_json(recorder.path/'pick_initial.json')
    phase_names={'initial':'独立站位初始化','torso_adjust':'真实躯干调整','pregrasp':'cuRobo 预抓取',
                 'approach':'接近目标','close':'双指闭合','lift':'真实抬升','hold':'无支撑保持'}
    # Visualization only: transparent walls do not alter collision or stored model.
    walls=[g for g in range(sim.model.ngeom) if 'wall' in sim.model.geom(g).name.lower()]
    for g in walls: sim.model.geom_rgba[g,3]=.12
    try:
        vdir=recorder.path/'videos'; vdir.mkdir(exist_ok=True)
        composite=imageio.get_writer(vdir/'delivery.mp4',fps=robot.control_hz,codec='libx264',macro_block_size=2,ffmpeg_log_level='error')
        last_phase=None
        for i,(t,q,phase) in enumerate(zip(times,qpos,phases)):
            sim.data.qpos[:]=q; sim.data.time=t; mujoco.mj_forward(sim.model,sim.data)
            frames={}
            for alias,camera in sim.robot.camera_names.items():
                renderer.update_scene(sim.data,camera=camera); frames[alias]=renderer.render().copy()
            recorder.video_frame(frames,float(t))
            if i in (0,len(times)-1) or phase!=last_phase:
                recorder.observation(frames,step=i,time_s=float(t))
            last_phase=phase
            speed=4 if phase=='torso_adjust' else 1
            if i% speed and i!=len(times)-1: continue
            base=sim.robot.group('base'); target=sim.data.xpos[sim.target_id]
            camera=mujoco.MjvCamera(); camera.lookat=np.r_[(base[:2]+target[:2])/2,.8]; camera.distance=2.7
            camera.azimuth=135; camera.elevation=-25
            scene_renderer.update_scene(sim.data,camera=camera); scene=scene_renderer.render().copy()
            camera.lookat=target; camera.distance=.65; camera.azimuth=120; camera.elevation=-25
            renderer.update_scene(sim.data,camera=camera); local=renderer.render().copy()
            canvas=Image.new('RGB',(1280,720),'#101827')
            canvas.paste(Image.fromarray(scene),(0,70)); canvas.paste(Image.fromarray(local).resize((320,240)),(960,70))
            canvas.paste(Image.fromarray(frames['head_camera']).resize((320,240)),(960,310))
            draw=ImageDraw.Draw(canvas)
            try: font=ImageFont.truetype('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',18)
            except OSError: font=ImageFont.load_default()
            index=max(0,int(np.searchsorted(physics_times,t,side='right'))-1)
            evidence=physics[index]
            lift_cm=100*(evidence['height_m']-initial['height_m'])
            fingers=len(evidence['finger_forces_n'])
            support='有' if evidence['support_contact'] else '无'
            if t < physics_times[0]: lift_cm=0.; fingers='未知'; support='未采样'
            color='#63d0e0'
            draw.text((14,7),'Case1 | 固定底盘站位 → cuRobo → 真实抓取验收',font=font,fill='white')
            draw.text((14,37),f"{phase_names.get(str(phase),str(phase))} {speed}× | {recorder.attempt_id}",font=font,fill=color)
            draw.text((972,75),'目标局部 · 同一物理时刻',font=font,fill='white')
            draw.text((972,315),'机器人头相机 RGB',font=font,fill='white')
            draw.text((972,556),f'目标抬升 {lift_cm:.1f} cm',font=font,fill=color)
            draw.text((972,583),f'承力手指 {fingers}/2 · 桌面支撑 {support}',font=font,fill='white')
            draw.text((972,610),'本次结果 '+status.removeprefix('executed_'),font=font,fill='white')
            draw.text((972,637),'非导航 · 不重置 · 无运动插值',font=font,fill='white')
            draw.text((14,680),f'物理时间 {t:.3f}s | 当前段 {speed}× | 墙体仅渲染透明，物理碰撞保留 | '+reason,font=font,fill='white')
            composite.append_data(np.asarray(canvas))
            metadata.append({'frame':len(metadata),'source_control_index':i,'actual_time_s':float(t),'phase':str(phase),'speed':speed})
            if i==len(times)-1: canvas.save(recorder.path/'delivery_final.png')
        write_json(recorder.path/'delivery_video.json',{'path':'videos/delivery.mp4','kind':'measured_qpos_replay',
                   'no_interpolation':True,'not_navigation':True,'wall_transparency':'rendering_only','frames':metadata})
    finally:
        if composite is not None: composite.close()
        scene_renderer.close(); renderer.close(); sim.close()


def plot_map(run):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    run=Path(run); data=read_json(run/'stations.json'); geometry=read_json(run/'map_geometry.json')
    fig,ax=plt.subplots(figsize=(10,7),layout='constrained')
    for obj in geometry['objects']:
        lo,hi=np.array(obj['min']),np.array(obj['max'])
        if obj['body']==geometry.get('support'):
            ax.add_patch(plt.Rectangle(lo[:2],*(hi-lo)[:2],fill=True,color='#d8cfb9',alpha=.8,label='support surface envelope'))
        elif hi[2]>.25:
            ax.add_patch(plt.Rectangle(lo[:2],*(hi-lo)[:2],fill=False,color='#b9bec5',alpha=.7,lw=.7))
    groups={}
    for row in data:
        key=tuple(round(float(v),6) for v in row['base'][:2])
        groups.setdefault(key,[]).append(row)
    legend=set()
    def point(x,y,color,marker,label,size=70):
        ax.scatter(x,y,c=color,marker=marker,s=size,label=label if label not in legend else None,alpha=.9,zorder=5)
        legend.add(label)
    labels=[]
    for (x,y),rows in groups.items():
        success=any(r['execution']=='success' for r in rows)
        failure=any(r['execution']=='failure' for r in rows)
        infra=any(r['execution'] in ('infrastructure_error','incomplete') or r['status']=='infrastructure_error' for r in rows)
        no_solution=any(r['planning']=='no_solution' for r in rows)
        filtered=all(r['geometry']=='geometry_filtered' for r in rows)
        if success:
            point(x,y,'#159570','o','At least one physical strict success',95)
            color='#159570'
            if failure: point(x,y,'#ca4e4e','s','Also has physical failure (different config)',32)
        elif failure: point(x,y,'#ca4e4e','s','Physical execution failure',75);color='#ca4e4e'
        elif infra: point(x,y,'#944cac','x','Infrastructure / incomplete');color='#944cac'
        elif filtered: point(x,y,'#84909c','x','Geometry filtered (not executed)');color='#84909c'
        elif no_solution: point(x,y,'#d78b20','D','Finite-budget plan no solution');color='#d78b20'
        else: point(x,y,'#9ba3ad','o','Not tested / budget exhausted');color='#9ba3ad'
        for yaw in {round(r['base'][2],6) for r in rows}:
            ax.plot([x,x+.10*np.cos(yaw)],[y,y+.10*np.sin(yaw)],color=color,lw=1,zorder=4)
        label='/'.join(dict.fromkeys(r['station_id'] for r in rows))
        labels.append((x,y,label))
    # Keep dense local refinements legible without moving their measured coordinates.
    used=[]
    for x,y,label in sorted(labels):
        offset=(4,5)
        if any(abs(x-a)<.25 and abs(y-b)<.16 for a,b,_ in labels if (a,b)!=(x,y)):
            for candidate in ((-30,22),(8,-24),(12,22),(-30,-24),(12,40)):
                location=np.array([x,y])+np.array(candidate)/150.
                if all(np.linalg.norm(location-old)>.16 for old in used):
                    offset=candidate;break
        used.append(np.array([x,y])+np.array(offset)/150.)
        ax.annotate(label,(x,y),xytext=offset,textcoords='offset points',fontsize=8,zorder=6,
                    arrowprops={'arrowstyle':'-','color':'#697480','lw':.5} if offset!=(4,5) else None)
    xy=geometry['target_xyz']; ax.scatter(*xy[:2],c='#101827',marker='*',s=180,label='target')
    bases=np.array([r['base'][:2] for r in data]+[xy[:2]])
    ax.set(xlim=(bases[:,0].min()-.35,bases[:,0].max()+.35),ylim=(bases[:,1].min()-.35,bases[:,1].max()+.35),
           xlabel='World x (m)',ylabel='World y (m)',title='Fixed-base operation stations | discrete evidence, no unknown interpolation')
    ax.set_aspect('equal'); ax.grid(alpha=.15); ax.legend(loc='upper left',fontsize=8)
    fig.savefig(run/'station_map.svg'); fig.savefig(run/'station_map.png',dpi=150); plt.close(fig)


def export_report(run):
    run=Path(run); rows=read_json(run/'stations.json'); summary=read_json(run/'summary.json')
    fields=['run_id','station_id','base','arm','torso_h','grasp_row','approach_offset_m','geometry','planning','execution','status','reason','attempt']
    with (run/'station_table.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        for r in rows: writer.writerow({k:r.get(k) for k in fields})
    plot_map(run)
    cards=[]; links=[]
    for r in rows:
        path=Path(r['attempt']) if r.get('attempt') else None
        if not path: continue
        video=path/'videos/delivery.mp4'
        if video.is_file():
            import os
            relative=os.path.relpath(video,run)
            label=f"{r.get('run_id',run.name)} / {r['station_id']} / {r['arm']} / h={r['torso_h']} / grasp={r['grasp_row']} / offset={r.get('approach_offset_m',0.)*1000:g}mm / {r['execution']}"
            cards.append(f'<section><h3>{html.escape(label)}</h3><video controls preload="metadata" src="{html.escape(relative)}"></video><p>{html.escape(r["reason"])}</p></section>')
            links.append(f'- [{label}]({relative})：{r["reason"]}')
    report=f'''# {run.name} 阶段三数据浏览报告

## 执行与结果

同一冻结场景独立初始化站位，几何过滤 → cuRobo 有界规划 → 真实躯干调整与抓取 → 严格力接触/抬升/保持验收 → 成败数据与视频。

- 真实成功 **{summary['successes']}**，真实执行失败 **{summary['failures']}**，执行设施异常 **{summary['infrastructure_errors']}**。
- case 条件：`{summary['case_condition']['status']}`（{summary['case_condition']['reason']}）。
- 所有点是固定底盘独立操作试验，不是导航；单次配置标签不是成功率。有限规划无解不是物理不可解。

![离散站位图](station_map.png)

图：每个站位含朝向，颜色区分物理结果与非执行诊断；XY 点优先显示已测物理结果（未测配置不覆盖成功）；同点若有成败则叠加红色方块。不同臂/高度/grasp 的完整记录见 [CSV](station_table.csv) 与 [JSON](stations.json)。未知区域不插值、不填失败。

## 本次真实执行视频

'''+('\n'.join(links) or '没有发生可交付的真实执行。')+'''

## 追溯与限制

[冻结输入](frozen_inputs.json) · [汇总与预算](summary.json) · [证据审计](audit.json)。
每次独立 attempt 包含原始 20 维动作、实际状态、每个物理 tick 的力接触、规划诊断、初末快照与视频；未发生执行的规划失败动作轨迹为空、没有执行视频。
未测试/预算耗尽保留 unknown。当前只支持 case1 普通抓取；case2 把手、case3 障碍归因与 case1.5 三侧导航尚未验收。
'''
    (run/'REPORT.md').write_text(report,encoding='utf-8')
    import base64
    encoded=base64.b64encode((run/'station_map.png').read_bytes()).decode()
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in fields[:-1])+'</tr>' for r in rows)
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{html.escape(run.name)}</title><style>body{{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#182535}}img,video{{max-width:100%}}section{{padding:20px 0;border-top:1px solid #ddd}}table{{font-size:12px;border-collapse:collapse}}td,th{{padding:5px;border:1px solid #ddd}}</style><h1>{html.escape(run.name)} 数据浏览</h1><p>真实成功 {summary['successes']} / 执行失败 {summary['failures']}；case {summary['case_condition']['status']}。固定底盘标注不是导航。未知区域不填失败，有限预算无解不等于不可解。同一站位不同接近深度、手臂与高度是不同控制配置，不把控制成败归因于站位。</p><img src="data:image/png;base64,{encoded}" alt="离散站位图">{''.join(cards)}<h2>逐配置标签</h2><table><tr>{''.join('<th>'+html.escape(k)+'</th>' for k in fields[:-1])}</tr>{table}</table><p>视频为实际 qpos 回放，无运动插值；躯干 4×、抓取 1×，墙体仅渲染透明。详见 REPORT.md 与 audit.json。</p></html>'''
    (run/'REPORT.html').write_text(page,encoding='utf-8')
