"""Fixed head/top/four support-local edge views; no dynamic supplementary views."""
from pathlib import Path
import math
import imageio.v2 as imageio
import mujoco
import numpy as np
from ..runtime.preparation import preparation_guard
from ..scenes.geometry import descendants
from ..io import write_json,file_digest


def capture_review_views(sim,target,region,path,*,width=640,height=480):
    preparation_guard(sim); path=Path(path); path.mkdir(parents=True,exist_ok=False)
    renderer=mujoco.Renderer(sim.model,height=height,width=width); images=[]
    body=sim.model.body(target).id; target_geoms=np.flatnonzero(np.isin(sim.model.geom_bodyid,list(descendants(sim.model,body))))
    corners=np.array([[x,y,0] for x in region.bounds[:2] for y in region.bounds[2:]])@np.array(region.axes).T+region.origin
    center=corners.mean(axis=0); distance=max(1.5,float(np.linalg.norm(corners.max(axis=0)-corners.min(axis=0)))*1.4)
    views=[('head',sim.robot.camera_names['head_camera'])]
    yaw=math.degrees(math.atan2(region.axes[1][0],region.axes[0][0]))
    for name,azimuth,elevation in [('top',yaw,-89.),('u-',yaw+180,-35.),('u+',yaw,-35.),('v-',yaw-90,-35.),('v+',yaw+90,-35.)]:
        cam=mujoco.MjvCamera(); cam.type=mujoco.mjtCamera.mjCAMERA_FREE; cam.lookat=center; cam.distance=distance
        cam.azimuth=azimuth; cam.elevation=elevation; views.append((name,cam))
    try:
        from PIL import Image,ImageDraw
        for view,camera in views:
            renderer.disable_segmentation_rendering(); renderer.update_scene(sim.data,camera=camera); rgb=renderer.render().copy()
            renderer.enable_segmentation_rendering(); seg=renderer.render().copy(); renderer.disable_segmentation_rendering()
            mask=(seg[:,:,1]==int(mujoco.mjtObj.mjOBJ_GEOM)) & np.isin(seg[:,:,0],target_geoms)
            ys,xs=np.nonzero(mask); box=None
            if len(xs):
                box=[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())]
                image=Image.fromarray(rgb); draw=ImageDraw.Draw(image); draw.rectangle(box,outline=(255,64,0),width=3); draw.text((box[0],max(0,box[1]-12)),'1',fill=(255,64,0)); rgb=np.array(image)
            file=path/(view+'.png'); imageio.imwrite(file,rgb)
            images.append({'view':view,'path':str(file.resolve()),'sha256':file_digest(file),'objects':{'1':{'instance':target,'bbox_xyxy':box}}})
    finally: renderer.close()
    write_json(path/'views.json',{'schema_version':'fixed-review-views-v1','images':images,'dynamic_views':False})
    return images
