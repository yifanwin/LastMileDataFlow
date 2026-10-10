"""Reusable render-only observer: no MJCF changes, no physics/state writes."""
from dataclasses import dataclass,asdict
import math
import itertools
import mujoco
import numpy as np

from .edit_views import CameraRig,ViewConfig
from ..scenes.geometry import descendants


@dataclass(frozen=True)
class ThirdPersonConfig:
    context_margin_m: float = .35
    elevations_deg: tuple = (-40.,-70.)
    azimuths_deg: tuple = (45.,135.,225.,315.,0.,90.,180.,270.)

    def __post_init__(self):
        if not math.isfinite(self.context_margin_m) or self.context_margin_m < 0:
            raise ValueError('invalid context margin')
        if not self.elevations_deg or not self.azimuths_deg or not all(
                math.isfinite(v) for v in (*self.elevations_deg,*self.azimuths_deg)):
            raise ValueError('invalid observer angles')


class ThirdPersonCamera:
    """Fit robot+target with context; choose visible initial angle, then track union.

    Angles remain fixed per attempt, avoiding arbitrary camera jumps. Occlusion
    is real: never hide walls/objects to manufacture visibility.
    """
    name='third_person_camera'

    def __init__(self,config=None):
        self.config=config or ThirdPersonConfig(); self.angle=None; self.rig=None
        self.initial_visibility=None

    def framing(self,sim,width,height):
        m,d=sim.model,sim.data
        robot={b for b in range(m.nbody) if m.body(b).name.startswith(sim.robot.config.namespace)}
        target=descendants(m,sim.target_id) if sim.target_id is not None else set()
        robot_geoms=[];target_geoms=[];points=[]
        corners=np.array(list(itertools.product((-1.,1.),repeat=3)))
        for g in range(m.ngeom):
            b=int(m.geom_bodyid[g])
            if b not in robot and b not in target: continue
            if b in robot: robot_geoms.append(g)
            if b in target: target_geoms.append(g)
            radius=float(m.geom_rbound[g])+self.config.context_margin_m
            points.extend(d.geom_xpos[g]+corners*radius)
        if not points: raise ValueError('no robot/target geometry to frame')
        config=ViewConfig(width=width,height=height,fovy_rad=math.radians(float(m.vis.global_.fovy)))
        fitted=CameraRig.fit(np.asarray(points),config)
        return fitted,robot_geoms,target_geoms

    def render(self,sim,renderer,width,height):
        fit,robot_geoms,target_geoms=self.framing(sim,width,height)
        if self.angle is None:
            best=None
            renderer.enable_segmentation_rendering()
            try:
                for elevation in self.config.elevations_deg:
                    for azimuth in self.config.azimuths_deg:
                        angle=(math.radians(azimuth),math.radians(elevation))
                        rig=CameraRig(fit.lookat,fit.distance_m,fit.fovy_rad,width,height,(angle,))
                        renderer.update_scene(sim.data,camera=rig.camera(0));seg=renderer.render()
                        geoms=seg[:,:,1]==int(mujoco.mjtObj.mjOBJ_GEOM)
                        rp=int(np.count_nonzero(geoms&np.isin(seg[:,:,0],robot_geoms)))
                        tp=int(np.count_nonzero(geoms&np.isin(seg[:,:,0],target_geoms)))
                        score=(min(rp/300.,1.)*min(tp/24.,1.),min(rp,tp),rp+tp)
                        if best is None or score>best[0]: best=(score,angle,rp,tp)
                self.angle=best[1]
                self.initial_visibility={'robot_pixels':best[2],'target_pixels':best[3],
                                         'both_visible':best[2]>0 and best[3]>0}
            finally: renderer.disable_segmentation_rendering()
        self.rig=CameraRig(fit.lookat,fit.distance_m,fit.fovy_rad,width,height,(self.angle,))
        renderer.update_scene(sim.data,camera=self.rig.camera(0))
        return renderer.render().copy()

    def metadata(self):
        return {'name':self.name,'kind':'render_only_free_camera','mode':'follow_robot_target_union',
                'angle_policy':'initial segmentation selection; fixed angles within attempt',
                'config':asdict(self.config),'rig':self.rig.to_dict() if self.rig else None,
                'initial_visibility':self.initial_visibility,'scene_geometry_hidden':False}
