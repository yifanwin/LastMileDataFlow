"""RBY-1 cache orchestration: measured geometry → proposals → native isolated physics."""
from pathlib import Path
import numpy as np
from ..io import digest,write_json
from ..grasping.geometry import object_meshes,sample_surface,asset_collision_digest,density_count
from ..grasping.sampling import pinch_grasps,finger_geometry
from ..grasping.cache import save_grasp_cache
from ..runtime.grasp_filter import extract_gripper,filter_grasps,subtree_inertial,FILTER_VERSION


def generate_asset_grasps(sim,target,capability,path,*,seed=0,max_candidates=64):
    if capability.get('measurement_status')!='measured': raise ValueError('measured capability required')
    if sim.started or sim.closed: raise RuntimeError('cache generation needs preparation scene')
    body=sim.model.body(target).id
    entry=next((e for e in sim.catalog if e['body_id']==body),None)
    if not entry or not entry.get('asset_id'): raise ValueError('explicit target asset identity required')
    meshes=object_meshes(sim,body); asset_digest=asset_collision_digest(meshes)
    gripper=extract_gripper(sim)
    points,normals=sample_surface(meshes,count=density_count(meshes),seed=seed)
    fingers=finger_geometry(gripper)
    candidates=pinch_grasps(points,normals,support_up=sim.data.xmat[body].reshape(3,3).T@np.array([0.,0.,1.]),
                            max_opening_m=capability['gripper']['max_opening_m'],fingers=fingers,
                            seed=seed,max_candidates=max_candidates)
    if not candidates:
        raise ValueError('no_pinch_candidates_within_RBY_opening')
    object_inertial=subtree_inertial(sim.model,body)
    filtered=filter_grasps(gripper,meshes,candidates,mass=object_inertial['mass'],
                           object_rotation=sim.data.xmat[body].reshape(3,3),object_inertial=object_inertial)
    path=save_grasp_cache(path,asset_id=entry['asset_id'],asset_digest=asset_digest,
                         gripper_digest=capability['gripper']['digest'],candidates=filtered)
    write_json(path/'generation.json',{'target':target,'seed':seed,'candidate_count':len(candidates),
        'isolated_verified_count':sum(r['simulation_pass'] for r in filtered),'gripper_geometry_digest':digest(gripper),
        'finger_geometry_tcp':fingers,'sampler_version':'pinch-slice-v2','filter_version':FILTER_VERSION,
        'task_strict_pick_status':'unknown','scope':'isolated candidate filter, not real-scene task validation'})
    return path


def run_grasp_generation(scene_xml,metadata,target,robot,capability,output,*,seed=0,max_candidates=64):
    """Persist each new run, including loading/geometry/filter failure evidence."""
    from ..scenes.source import SceneSource
    from ..runtime.simulation import Simulation
    import os
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    # Preserve the resource root of symlinked scene XMLs, like load_spec().
    source=SceneSource(Path(scene_xml).stem,os.path.abspath(scene_xml),os.path.abspath(metadata))
    write_json(output/'inputs.json',{'source':source.__dict__,'target':target,'seed':seed,
        'max_candidates':max_candidates,'capability':capability,'source_policy':'pure_scene_geometry_only'})
    sim=None
    try:
        sim=Simulation.from_source(source,robot,target=target)
        cache=generate_asset_grasps(sim,target,capability,output/'cache',seed=seed,max_candidates=max_candidates)
        from ..io import read_json
        count=read_json(cache/'generation.json')['isolated_verified_count']
        result={'status':'isolated_candidates_verified' if count else 'no_verified_candidates',
                'cache':str(cache.resolve()),'isolated_verified_count':count,'strict_scene_pick':'unknown'}
    except Exception as exc:
        result={'status':'infrastructure_or_generation_error','error_type':type(exc).__name__,
                'reason':str(exc)[:500],'strict_scene_pick':'unknown'}
    finally:
        if sim is not None: sim.close()
    write_json(output/'result.json',result)
    return output
