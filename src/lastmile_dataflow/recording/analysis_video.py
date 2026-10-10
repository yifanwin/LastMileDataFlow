"""Reference-style fourth video: real observer + synchronized wrist + measured facts."""
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageOps


class AnalysisVideo:
    width,height=1280,720

    def __init__(self,task,station, *, path=None,goal=None,radius_m=2.,footprint_radius_m=None):
        self.task,self.station,self.path,self.goal=task,station,path,goal
        self.radius=radius_m;self.history=[]
        if footprint_radius_m is not None and (not np.isfinite(footprint_radius_m) or footprint_radius_m <= 0):
            raise ValueError('footprint_radius_m must be finite and positive')
        self.footprint_radius_m=footprint_radius_m
        fonts=('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
               '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        font=next((p for p in fonts if Path(p).is_file()),None)
        self.font=ImageFont.truetype(font,20) if font else ImageFont.load_default()
        self.title_font=ImageFont.truetype(font,26) if font else ImageFont.load_default()

    def frame(self,frames, *, phase,time_s,base_xy,target_height,initial_height,facts=None,side=None,collisions=0):
        self.history.append(list(base_xy))
        canvas=Image.new('RGB',(self.width,self.height),'#101820');draw=ImageDraw.Draw(canvas)
        scene=ImageOps.contain(Image.fromarray(frames['third_person_camera']),(960,594))
        canvas.paste(scene,((960-scene.width)//2,74+(594-scene.height)//2))
        heading='S0 → A* → S1 → cuRobo 操作' if self.path else 'S0 独立操作评测'
        draw.text((18,8),heading,font=self.title_font,fill='white')
        instruction=self.task.get('instruction','')
        draw.text((18,43),instruction[:60],font=self.font,fill='#72d8ea')
        draw.text((973,10),'夹爪近景 · 同一物理时刻',font=self.font,fill='white')
        wrist='wrist_camera_r' if side=='right' else 'wrist_camera_l'
        if wrist in frames:
            inset=ImageOps.contain(Image.fromarray(frames[wrist]),(308,230))
            canvas.paste(inset,(968+(308-inset.width)//2,76))
        labels={'navigation':'A* 路线 · 底盘闭环','arrival':'到站稳定 · 无重置',
                'dry_pregrasp':'候选抓取规划','torso_adjust':'躯干调整','pregrasp':'预抓取',
                'approach':'接近目标','close':'闭合夹爪','lift':'真实抬升',
                'lift_attached':'持物抬升','hold':'稳定保持','open':'沿真实关节开启',
                'retry_release':'操作重试 · 松爪','initialize':'初始化'}
        draw.text((973,315),'阶段  '+labels.get(phase,phase),font=self.font,fill='#72d8ea')
        draw.text((973,347),f'目标抬升 {(target_height-initial_height)*100:.1f} cm',font=self.font,fill='white')
        contacts='--' if facts is None else str(len(facts.get('finger_forces_n',{})))+' / 2'
        support='--' if facts is None else '有' if facts.get('support_contact') else '无'
        draw.text((973,379),f'承力手指 {contacts}    支撑 {support}',font=self.font,fill='white')
        draw.text((973,411),f'碰撞记录 {collisions}',font=self.font,fill='white')
        left,top,size=980,452,206
        center=np.asarray(self.task.get('anchor_world',base_xy)[:2],float)
        lo=center-self.radius;span=2*self.radius
        def pixel(xy):
            v=(np.asarray(xy)-lo)/span
            return (left+int(v[0]*size),top+size-int(v[1]*size))
        draw.rectangle((left,top,left+size,top+size),outline='#506575')
        draw.ellipse((left,top,left+size,top+size),outline='#506575')
        for obstacle in self.task.get('support_obstacles',[]):
            x0,y1=pixel(obstacle['min'][:2]);x1,y0=pixel(obstacle['max'][:2])
            x0,x1=max(left,x0),min(left+size,x1)
            y0,y1=max(top,y0),min(top+size,y1)
            if x0 <= x1 and y0 <= y1:
                draw.rectangle((x0,y0,x1,y1),fill='#493d2d',outline='#b99a62')
        if self.path:
            planned=[pixel(p) for p in self.path['xy']]
            if len(planned)>1:draw.line(planned,fill='#687785',width=2)
        actual=[pixel(p) for p in self.history]
        if len(actual)>1:draw.line(actual,fill='#72d8ea',width=3)
        def dot(xy,color,label):
            x,y=pixel(xy);draw.ellipse((x-4,y-4,x+4,y+4),fill=color)
            draw.text((x+6,y-10),label,font=self.font,fill=color)
        def station_marker(xy,color,label):
            if self.footprint_radius_m is None:
                dot(xy,color,label)
                return
            x,y=pixel(xy)
            r=self.footprint_radius_m*size/span
            draw.ellipse((x-r,y-r,x+r,y+r),outline=color,width=2)
            draw.ellipse((x-3,y-3,x+3,y+3),fill=color)
            draw.text((x+r+6,y-10),label,font=self.font,fill=color)
        station_marker(self.station['xy'],'#ed6b64','S0')
        if self.goal:station_marker(self.goal['xy'],'#66d68c','S1')
        dot(center,'#ffc55b','T');dot(base_xy,'white','')
        draw.text((18,678),f'仿真时间 {time_s:.3f} s | 1× | 连续物理执行，无重置 | 原场景遮挡保留',font=self.font,fill='white')
        draw.text((973,678),'蓝:实走 灰:规划',font=self.font,fill='#72d8ea')
        return np.asarray(canvas)
