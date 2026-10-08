"""Antipodal point-pair proposals; geometric candidates are NOT verified grasps."""
import math
import numpy as np


def antipodal_grasps(points, normals, *, max_opening_m, margin_m=.005, friction=.5,
                      seed=0, max_candidates=64, pair_budget=10000, angles=8, depths_m=(-.01, 0., .01), support_up=None):
    p, n = np.asarray(points,float), np.asarray(normals,float)
    if p.ndim != 2 or p.shape[1:] != (3,) or p.shape != n.shape or not np.isfinite(p).all() or not np.isfinite(n).all():
        raise ValueError('finite surface points and normals required')
    if any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in (max_opening_m,margin_m,friction)):
        raise ValueError('invalid gripper/friction dimensions')
    if any(type(x) is not int or x <= 0 for x in (max_candidates,pair_budget,angles)) or type(seed) is not int or seed < 0:
        raise ValueError('invalid sampling budget/seed')
    if not depths_m or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>.04 for v in depths_m):
        raise ValueError('bounded approach depths required')
    if not len(p): return []
    norms=np.linalg.norm(n,axis=1)
    if np.any(norms < 1e-10): raise ValueError('zero surface normal')
    up=None
    if support_up is not None:
        up=np.asarray(support_up,float)
        if up.shape!=(3,) or not np.isfinite(up).all() or np.linalg.norm(up)<1e-8: raise ValueError('finite support-up direction required')
        up=up/np.linalg.norm(up)
    n=n/norms[:,None]; rng=np.random.default_rng(seed); result=[]; seen=set(); pairs=[]
    minimum_cos=1/math.sqrt(1+friction*friction)
    for _ in range(pair_budget):
        i,j=map(int,rng.integers(0,len(p),2)); key=tuple(sorted((i,j)))
        if i==j or key in seen: continue
        seen.add(key); delta=p[j]-p[i]; width=float(np.linalg.norm(delta))
        if not .002 < width < max_opening_m-margin_m: continue
        # TCP y is the measured closing axis in the RBY-1 site convention.
        y=delta/width
        if up is not None and (abs(np.dot(y,up))>.15 or np.dot((p[i]+p[j])/2,up)<float(np.min(p@up)+.5*np.ptp(p@up))): continue
        if np.dot(-n[i],y) < minimum_cos or np.dot(n[j],y) < minimum_cos: continue
        helper=np.eye(3)[np.argmin(np.abs(y))]
        z0=np.cross(y,helper) if up is None else -up+np.dot(up,y)*y
        z0/=np.linalg.norm(z0)
        pairs.append((i,j,width,y,z0))
        if len(pairs)>=max_candidates: break
    # Shuffle pair × orientation × depth, rather than exhausting all variants of
    # the first pair before considering another surface region.
    directions=np.linspace(0,2*np.pi,angles,endpoint=False) if up is None else (-.15,0.,.15)
    combos=[(pair,angle,depth) for pair in pairs for angle in directions for depth in depths_m]
    if not combos: return []
    for index in rng.permutation(len(combos))[:max_candidates]:
        (i,j,width,y,z0),angle,depth=combos[int(index)]
        z=z0*np.cos(angle)+np.cross(y,z0)*np.sin(angle); x=np.cross(y,z)
        pose=np.eye(4); pose[:3,:3]=np.column_stack((x,y,z)); pose[:3,3]=(p[i]+p[j])/2+depth*z
        result.append({'transform':pose.tolist(),'width_m':width,'score':float(min(np.dot(-n[i],y),np.dot(n[j],y))),
                       'region':'body','simulation_pass':False,'contacts':[p[i].tolist(),p[j].tolist()],
                       'approach_depth_m':float(depth)})
    return result


def finger_geometry(gripper):
    """Closed-pose finger/palm extents in the TCP frame (x finger width, y closing, z approach)."""
    fingers=np.concatenate([np.array(m['vertices']) for g in gripper['groups'] if 'axis' in g for m in g['meshes']])
    palm=np.concatenate([np.array(m['vertices']) for g in gripper['groups'] if 'axis' not in g for m in g['meshes']])
    thickness=max(float(np.ptp(np.concatenate([np.array(m['vertices']) for m in g['meshes']])[:,1]))
                  for g in gripper['groups'] if 'axis' in g)
    return {'half_width_m':float(np.abs(fingers[:,0]).max()),'thickness_m':thickness,
            'tip_z_m':float(fingers[:,2].max()),'back_z_m':float(fingers[:,2].min()),'palm_front_z_m':float(palm[:,2].max())}


def _directions(up, *, side_count, tilted_count):
    up=up/np.linalg.norm(up); helper=np.eye(3)[np.argmin(np.abs(up))]
    e1=np.cross(up,helper); e1/=np.linalg.norm(e1); e2=np.cross(up,e1)
    rows=[('top',-up)]
    for kind,count,elevation in (('side',side_count,0.),('tilted',tilted_count,np.pi/4)):
        for k in range(count):
            phi=2*np.pi*k/count; horizontal=np.cos(phi)*e1+np.sin(phi)*e2
            rows.append((kind,np.cos(elevation)*horizontal-np.sin(elevation)*up))
    return rows


def pinch_grasps(points, normals, *, support_up, max_opening_m, fingers, friction=.5, contact_band_m=.003, margin_m=.004, depths_m=(.012,.022,.035,.045,.052),
                 rolls=6, side_count=12, tilted_count=8, lateral_step_m=.012, floor_clearance_m=.005,
                 min_material_points=6, max_candidates=64, seed=0):
    """Approach-aware pinch proposals; geometric candidates are NOT verified grasps.

    For each approach direction, roll and fingertip depth, take the object material inside the finger band
    (finger width × fingertip depth), split it along the closing axis into pieces, and centre the open fingers
    on a piece only if both open finger volumes are empty. Fingertip depth is measured from the object's
    nearest extent along the approach, so the palm (behind the finger roots) stays outside the object.
    Both pinched faces must lie inside the friction cone of the closing axis, otherwise the piece squeezes out.
    Deep options matter for round objects: only near the equator are the pinched normals horizontal.
    """
    p=np.asarray(points,float); n=np.asarray(normals,float); up=np.asarray(support_up,float); up=up/np.linalg.norm(up)
    if n.shape!=p.shape or not np.isfinite(n).all(): raise ValueError('one finite normal per point required')
    n=n/np.maximum(np.linalg.norm(n,axis=1,keepdims=True),1e-12); cone=1/math.sqrt(1+friction*friction)
    if p.ndim!=2 or p.shape[1]!=3 or not np.isfinite(p).all() or len(p)<min_material_points: raise ValueError('finite object points required')
    hw,th,tip,back,palm=(fingers[k] for k in ('half_width_m','thickness_m','tip_z_m','back_z_m','palm_front_z_m'))
    margin=margin_m
    inner=max_opening_m/2; outer=inner+th
    if max(depths_m) > (tip-palm)-margin: raise ValueError('fingertip depth would bring the palm into the object')
    bottom=float((p@up).min()); centre=p.mean(axis=0); size=float(np.ptp(p,axis=0).max())
    rows=[]
    for kind,a in _directions(up,side_count=side_count,tilted_count=tilted_count):
        helper=np.eye(3)[np.argmin(np.abs(a))]; y0=np.cross(a,helper); y0/=np.linalg.norm(y0)
        s=p@a; s_min=float(s.min())
        for roll in np.linspace(0,np.pi,rolls,endpoint=False):  # the two-finger gripper is symmetric under π
            y=np.cos(roll)*y0+np.sin(roll)*np.cross(a,y0); x=np.cross(y,a); R=np.column_stack((x,y,a))
            px,py,ny=p@x,p@y,np.abs(n@y)  # |.|: mesh winding (outward sign) is not guaranteed
            for depth in depths_m:
                band=s<=s_min+depth
                for u in np.arange(px[band].min(),px[band].max()+1e-9,lateral_step_m):
                    sel=band&(np.abs(px-u)<=hw)
                    if sel.sum()<min_material_points: continue
                    ys=np.sort(py[sel]); breaks=np.where(np.diff(ys)>th+2*margin)[0]
                    pieces=np.split(ys,breaks+1)
                    for i in range(len(pieces)):
                        for j in range(i,len(pieces)):
                            lo,hi=float(pieces[i][0]),float(pieces[j][-1])
                            if hi-lo>2*(inner-margin): break
                            c=(lo+hi)/2; d=np.abs(py[sel]-c)
                            if np.any((d>inner-margin)&(d<outer+margin)): continue  # open finger volumes must be empty
                            if np.count_nonzero(d<=inner-margin)<min_material_points: continue
                            yy,nn=py[sel],ny[sel]
                            if not (np.any((yy>=hi-contact_band_m)&(nn>=cone)) and np.any((yy<=lo+contact_band_m)&(nn>=cone))): continue
                            origin=u*x+c*y+(s_min+depth-tip)*a
                            pose=np.eye(4); pose[:3,:3]=R; pose[:3,3]=origin
                            corners=np.array([[sx*hw,sy*sw,z] for sx in (-1,1) for sy in (-1,1) for sw in (inner,outer) for z in (back,tip)])
                            if float(((corners@R.T+origin)@up).min())<bottom+floor_clearance_m: continue
                            width=hi-lo
                            score=float(depth/max(depths_m)-.5*np.linalg.norm(np.cross(origin-centre,a))/max(size,1e-6))
                            rows.append({'transform':pose.tolist(),'width_m':float(max(width,1e-4)),'score':score,
                                         'region':'body','simulation_pass':False,'contacts':[],
                                         'approach_depth_m':float(depth),'approach_kind':kind,'roll_rad':float(roll)})
    if not rows: return []
    # Diversity before score: round-robin over approach kinds/directions, best-scored first within each.
    rng=np.random.default_rng(seed); groups={}
    for r in sorted(rows,key=lambda r:-r['score']):
        key=(r['approach_kind'],tuple(np.round(np.array(r['transform'])[:3,2],3)))
        groups.setdefault(key,[]).append(r)
    keys=list(groups); keys=[keys[int(i)] for i in rng.permutation(len(keys))]
    result=[]
    while len(result)<max_candidates and any(groups[k] for k in keys):
        for k in keys:
            if groups[k] and len(result)<max_candidates: result.append(groups[k].pop(0))
    return result
