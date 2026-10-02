"""Revision-bound, minimal-edit planar candidate generation."""
from types import SimpleNamespace
import itertools
import mujoco
import numpy as np
from ..io import digest
from ..scenes.geometry import placement_pose, body_points, quat_angle


def candidates(session):
    session.active(); config=session.config; sim=session.sim
    region=session.placements[config.target]; target=sim.model.body(config.target).id
    if config.target not in config.editable and config.case_type!='case3': return []
    a,b,c,d=region.bounds
    current=region.local(sim.data.xpos[target])
    points=[current[:2],np.array([(a+b)/2,(c+d)/2])]
    points.extend(np.array([x,y]) for x,y in itertools.product(np.linspace(a,b,7)[1:-1],np.linspace(c,d,7)[1:-1]))
    results=[]; seen=set()
    rotation_current=sim.data.xmat[target].reshape(3,3)
    current_yaw=float(np.arctan2(rotation_current[1,0],rotation_current[0,0]))
    yaws=config.parameters.get('yaw_candidates_rad',[current_yaw,0,np.pi/2,np.pi,-np.pi/2])
    for xy in points:
        for yaw in yaws:
            pose=placement_pose(sim,target,region,xy,yaw)
            if pose is None: continue
            op='rotate' if np.linalg.norm(np.array(pose[:3])-sim.data.xpos[target])<.008 else 'move'
            if op=='rotate': pose[:3]=sim.data.xpos[target].tolist()
            no_target_edit=config.case_type=='case3' and np.linalg.norm(np.array(pose[:3])-sim.data.xpos[target])<.008 and quat_angle(np.array(pose[3:]),sim.data.xquat[target])<.04
            if not no_target_edit and (op not in config.allowed_operations or config.target not in config.editable): continue
            operations=[] if no_target_edit else [{'op':op,'instance':config.target,'pose':pose,'region_id':region.region_id}]
            if no_target_edit: pose=np.r_[sim.data.xpos[target],sim.data.xquat[target]].tolist()
            if config.case_type=='case3':
                p=config.parameters; name=p['obstacle']; offset=np.asarray(p['approach_offset_m'])
                world=np.array(pose[:3])+offset
                try: body=sim.model.body(name).id
                except KeyError:
                    if 'add' not in config.allowed_operations or name not in config.editable: continue
                    asset,record=session.pool.load(p.get('obstacle_asset')); model=asset.compile(); data=mujoco.MjData(model); mujoco.mj_forward(model,data)
                    points_asset=body_points(SimpleNamespace(model=model,data=data),model.body(record['root_body']).id)
                    world[2]=region.height-(points_asset[:,2]-data.xpos[model.body(record['root_body']).id,2]).min()+.003
                    operations.append({'op':'add','instance':name,'asset_id':p['obstacle_asset'],'pose':[*world,1,0,0,0],'region_id':region.region_id})
                else:
                    localxy=region.local(world)[:2]; obstacle_pose=placement_pose(sim,body,region,localxy,0)
                    if obstacle_pose is None or name not in config.editable or 'move' not in config.allowed_operations: continue
                    operations.append({'op':'move','instance':name,'pose':obstacle_pose,'region_id':region.region_id})
            # Deduplicate sub-mm numerical drift; precise execution poses remain unchanged.
            identity=[{**o,'pose':[round(x,3) for x in o['pose'][:3]]+[round(x,5) for x in o['pose'][3:]]} for o in operations]
            key=digest(identity)
            if key in seen: continue
            seen.add(key)
            # Rank minimal edits first; geometry requirements are still re-evaluated after settling.
            penalty=0.
            parameters=config.parameters
            if 'distance_range_m' in parameters:
                distance=np.linalg.norm(np.array(pose[:2])-sim.robot.group('base')[:2]); lo,hi=parameters['distance_range_m']
                penalty += max(lo-distance,0,distance-hi)*100
            if 'height_range_m' in parameters:
                lo,hi=parameters['height_range_m']; penalty += max(lo-pose[2],0,pose[2]-hi)*100
            if config.case_type=='case2':
                handle=parameters['handle']; hb=sim.model.body(handle['body']).id
                local_rotation=sim.data.xmat[target].reshape(3,3).T @ sim.data.xmat[hb].reshape(3,3)
                cy,sy=np.cos(yaw),np.sin(yaw); rotation=np.array([[cy,-sy,0],[sy,cy,0],[0,0,1]])
                direction=rotation @ local_rotation @ np.array(handle['axis_local']); desired=np.array(parameters['desired_direction_world'])
                angle=np.arccos(np.clip(np.dot(direction,desired)/np.linalg.norm(direction)/np.linalg.norm(desired),-1,1))
                penalty += max(0,angle-parameters.get('direction_tolerance_rad',.15))*100
            if config.case_type=='case1.5':
                fb=sim.model.body(parameters['frame_body']).id;rot=sim.data.xmat[fb].reshape(3,3)
                side_points=[sim.data.xpos[fb]+rot @ np.asarray(parameters[key]) for key in ('side_a','side_b')]
                distances=[np.linalg.norm(x[:2]-np.array(pose[:2])) for x in side_points]
                penalty += max(0,parameters['min_distance_difference_m']-distances[1]+distances[0])*100
            cost=penalty+float(np.linalg.norm(np.array(pose[:3])-sim.data.xpos[target]))+.02*abs(yaw)
            results.append({'candidate_id':key,'revision':session.revision,'operations':operations,'cost':cost})
    return sorted(results,key=lambda x:(x['cost'],x['candidate_id']))
